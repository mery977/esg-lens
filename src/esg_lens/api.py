"""REST API (FastAPI).

POST /documents            upload a PDF report, returns its doc_id
GET  /documents            list indexed reports
POST /documents/{id}/extract   run the multi-agent KPI extraction
POST /documents/{id}/ask       ask a question, answered with citations
POST /documents/{id}/evidence  the cited sentence located on its page, with context
GET  /results                 saved extractions of real reports (results/<provider>/)
GET  /results/{provider}/{report}   one of them, with evidence and human-checked gold values
GET  /health                  (public)

If ESG_API_TOKEN is set, every other endpoint requires "Authorization: Bearer <token>".
"""

from __future__ import annotations

import secrets
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from . import __version__
from .agents import Orchestrator, QAAgent
from .config import get_settings
from .evidence import locate
from .kpis import KPI_BY_KEY, KPI_CATALOGUE
from .llm import get_provider
from .schemas import Answer, ExtractionReport
from .store import DocumentStore

MAX_UPLOAD_BYTES = 50 * 1024 * 1024

settings = get_settings()
llm = get_provider(settings)
store = DocumentStore(settings, llm)
app = FastAPI(
    title="ESG Lens",
    version=__version__,
    description="Multi-agent extraction of ESG KPIs from sustainability reports, with citations.",
)


def require_token(authorization: str | None = Header(default=None)) -> None:
    """Constant-time bearer-token check; a no-op when no token is configured (local development)."""
    if not settings.api_token:
        return
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, settings.api_token):
        raise HTTPException(401, "missing or invalid bearer token", headers={"WWW-Authenticate": "Bearer"})


protected = [Depends(require_token)]


class ExtractRequest(BaseModel):
    kpis: list[str] | None = None  # subset of KPI keys; all if empty


class AskRequest(BaseModel):
    question: str


class EvidenceRequest(BaseModel):
    page: int
    quote: str


def _load(doc_id: str):
    try:
        return store.load(doc_id)
    except KeyError as e:
        raise HTTPException(404, f"unknown document {doc_id}") from e


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/docs")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "provider": llm.name, "version": __version__}


@app.get("/kpis", dependencies=protected)
def kpis() -> list[dict]:
    return [k.model_dump() for k in KPI_CATALOGUE]


@app.get("/documents", dependencies=protected)
def list_documents() -> list[dict]:
    return store.list()


@app.post("/documents", status_code=201, dependencies=protected)
async def upload(file: UploadFile = File(...)) -> dict:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(415, "only PDF files are accepted")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file too large (50 MB max)")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / Path(file.filename).name
        path.write_bytes(data)
        try:
            doc_id = await run_in_threadpool(store.add_pdf, path)  # embedding can take a while
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
    return {"doc_id": doc_id}


@app.post("/documents/{doc_id}/extract", response_model=ExtractionReport, dependencies=protected)
def extract(doc_id: str, req: ExtractRequest | None = None) -> ExtractionReport:
    index, pages = _load(doc_id)
    keys = (req.kpis if req else None) or []
    unknown = [k for k in keys if k not in KPI_BY_KEY]
    if unknown:
        raise HTTPException(422, f"unknown KPI keys: {unknown}")
    selected = [KPI_BY_KEY[k] for k in keys] or None
    orch = Orchestrator(llm, index, {doc_id: pages}, top_k=settings.top_k, alpha=settings.hybrid_alpha)
    return orch.run(doc_id, selected)


@app.post("/documents/{doc_id}/ask", response_model=Answer, dependencies=protected)
def ask(doc_id: str, req: AskRequest) -> Answer:
    index, _ = _load(doc_id)
    return QAAgent(llm, index, top_k=settings.top_k, alpha=settings.hybrid_alpha).ask(req.question)


@app.post("/documents/{doc_id}/evidence", dependencies=protected)
def evidence(doc_id: str, req: EvidenceRequest) -> dict:
    _, pages = _load(doc_id)
    if not 1 <= req.page <= len(pages):
        raise HTTPException(404, f"page {req.page} does not exist")
    return {"page": req.page, **locate(pages[req.page - 1], req.quote)}


@app.get("/results", dependencies=protected)
def results() -> list[dict]:
    from .reports import list_results

    return list_results()


@app.get("/results/{provider}/{report}", dependencies=protected)
def result(provider: str, report: str) -> dict:
    from .reports import load_result

    try:
        return load_result(provider, report)
    except KeyError as e:
        raise HTTPException(404, f"no saved result {provider}/{report}") from e
