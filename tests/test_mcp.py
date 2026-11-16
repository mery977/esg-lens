import asyncio
import importlib
import json

import pytest

pytest.importorskip("mcp")


def load_server(tmp_path, monkeypatch):
    monkeypatch.setenv("ESG_PROVIDER", "fake")
    monkeypatch.setenv("ESG_DATA_DIR", str(tmp_path / "mcp_store"))
    import esg_lens.config as config

    config.get_settings.cache_clear()
    import esg_lens.mcp_server as mcp_server

    importlib.reload(mcp_server)
    return mcp_server.server


def _payload(result):
    if getattr(result, "structuredContent", None):
        data = result.structuredContent
        return data.get("result", data)
    return json.loads(result.content[0].text)


def test_mcp_tools_end_to_end(tmp_path, monkeypatch, sample_pdf):
    server = load_server(tmp_path, monkeypatch)

    async def scenario():
        names = {t.name for t in await server.list_tools()}
        assert {
            "list_indicators",
            "list_reports",
            "ingest_report",
            "extract_indicators",
            "ask_report",
        } <= names

        doc_id = _payload(await server.call_tool("ingest_report", {"pdf_path": str(sample_pdf)}))["doc_id"]
        report = _payload(
            await server.call_tool(
                "extract_indicators", {"doc_id": doc_id, "indicators": ["scope1_emissions"]}
            )
        )
        assert report["kpis"][0]["value"] == 48250 and report["kpis"][0]["verified"]

        answer = _payload(await server.call_tool("ask_report", {"doc_id": doc_id, "question": "Scope 1?"}))
        assert answer["citations"]

    asyncio.run(scenario())
