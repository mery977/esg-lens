"""MCP server: exposes ESG Lens as tools for any MCP client (Claude Desktop, Claude Code, Copilot, ADK...).

Run locally over stdio:   esg-lens-mcp
Over HTTP:                esg-lens-mcp --http --port 8001

Claude Desktop config (claude_desktop_config.json):
  {"mcpServers": {"esg-lens": {"command": "esg-lens-mcp", "env": {"ESG_PROVIDER": "anthropic"}}}}

The tools return verified, cited data, so the calling agent never has to trust its own reading of a PDF.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .agents import Orchestrator, QAAgent
from .config import get_settings
from .kpis import KPI_BY_KEY, KPI_CATALOGUE
from .llm import get_provider
from .store import DocumentStore

settings = get_settings()
llm = get_provider(settings)
store = DocumentStore(settings, llm)

server = MCPServer(
    name="esg-lens",
    title="ESG Lens",
    instructions=(
        "Tools to extract ESG indicators (CSRD/ESRS, GRI) from sustainability reports. Every value comes "
        "with a page and a verbatim quote, and values that fail verification are flagged. Index a report "
        "with ingest_report before extracting or asking. Report 'not disclosed' when a value is null."
    ),
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


@server.tool(annotations=READ_ONLY)
def list_indicators() -> list[dict]:
    """List the ESG indicators ESG Lens can extract, with their keys and expected units."""
    return [{"key": k.key, "name": k.name, "unit": k.unit_hint} for k in KPI_CATALOGUE]


@server.tool(annotations=READ_ONLY)
def list_reports() -> list[dict]:
    """List the sustainability reports already indexed, with their doc_id."""
    return store.list()


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True, openWorldHint=False))
def ingest_report(pdf_path: str) -> dict:
    """Index a local PDF sustainability report. Returns its doc_id. Re-indexing the same file is a no-op."""
    path = Path(pdf_path).expanduser()
    if path.suffix.lower() != ".pdf" or not path.is_file():
        raise ValueError(f"not a PDF file: {pdf_path}")
    return {"doc_id": store.add_pdf(path)}


@server.tool(annotations=READ_ONLY)
def extract_indicators(doc_id: str, indicators: list[str] | None = None) -> dict:
    """Extract ESG indicators from an indexed report with the multi-agent pipeline.

    Args:
        doc_id: id returned by ingest_report or list_reports.
        indicators: optional subset of indicator keys (see list_indicators); all when omitted.
    """
    unknown = [k for k in indicators or [] if k not in KPI_BY_KEY]
    if unknown:
        raise ValueError(f"unknown indicator keys: {unknown}")
    index, pages = store.load(doc_id)
    selected = [KPI_BY_KEY[k] for k in indicators] if indicators else None
    orch = Orchestrator(llm, index, {doc_id: pages}, settings.top_k, settings.hybrid_alpha)
    return orch.run(doc_id, selected).model_dump()


@server.tool(annotations=READ_ONLY)
def ask_report(doc_id: str, question: str) -> dict:
    """Answer a question about an indexed report, using only its content, with page citations."""
    index, _ = store.load(doc_id)
    return QAAgent(llm, index, settings.top_k, settings.hybrid_alpha).ask(question).model_dump()


def main() -> None:
    parser = argparse.ArgumentParser(prog="esg-lens-mcp")
    parser.add_argument("--http", action="store_true", help="serve over streamable HTTP instead of stdio")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    if args.http:
        server.run(transport="streamable-http", port=args.port)
    else:
        server.run()


if __name__ == "__main__":
    main()
