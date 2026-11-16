"""Typed data contracts shared by every layer (ingestion, agents, API, evaluation)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    """A passage of a document, always traceable to its page."""

    chunk_id: str
    doc_id: str
    page: int  # 1-based
    text: str


class KpiSpec(BaseModel):
    """Definition of an ESG indicator the system knows how to look for."""

    key: str
    name: str
    unit_hint: str
    queries: list[str]  # search queries the retrieval agent starts from
    keywords: list[str]  # words that must appear near the value
    # A quote containing one of these reports the wrong figure (e.g. location-based Scope 2).
    avoid_terms: list[str] = []


class Citation(BaseModel):
    doc_id: str
    page: int
    quote: str


class KpiValue(BaseModel):
    """One extracted indicator. `value` is None when the report does not disclose it."""

    kpi: str
    value: float | None = None
    unit: str | None = None
    year: int | None = None
    citation: Citation | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    verified: bool = False
    verification_notes: list[str] = Field(default_factory=list)
    attempts: int = 1  # extraction attempts, including retries after failed verification
    corrected_issues: list[str] = Field(default_factory=list)  # problems found, then fixed by a retry


class ExtractionReport(BaseModel):
    doc_id: str
    provider: str
    kpis: list[KpiValue]
    latency_s: float
    llm_calls: int


class Answer(BaseModel):
    question: str
    answer: str
    citations: list[Citation]
