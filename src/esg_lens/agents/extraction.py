"""Extraction agent: turns retrieved passages into one structured, cited indicator value."""

from __future__ import annotations

import json
import re

from ..llm.base import LLMProvider
from ..numbers import normalise_unit, parse_value
from ..schemas import Chunk, Citation, KpiSpec, KpiValue
from . import prompts


def _int(raw: object, lo: int, hi: int) -> int | None:
    """First integer in lo..hi found in what the model returned ("FY2025" -> 2025, "p. 12" -> 12)."""
    for m in re.findall(r"\d+", str(raw if raw is not None else "")):
        if lo <= int(m) <= hi:
            return int(m)
    return None


def _confidence(raw: object) -> float:
    words = {"high": 0.9, "medium": 0.6, "low": 0.3}
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return words.get(str(raw).strip().lower(), 0.0)


class ExtractionAgent:
    def __init__(self, llm: LLMProvider):
        self.llm = llm

    def extract(self, kpi: KpiSpec, passages: list[Chunk], feedback: list[str] | None = None) -> KpiValue:
        payload = {
            "task": "extract_kpi",
            "kpi": kpi.key,
            "indicator": kpi.name,
            "unit_hint": kpi.unit_hint,
            "keywords": kpi.keywords,
            "avoid_terms": kpi.avoid_terms,
            "passages": [{"doc_id": c.doc_id, "page": c.page, "text": c.text} for c in passages],
        }
        if feedback:
            payload["feedback"] = feedback
        try:
            out = self.llm.complete_json(prompts.EXTRACT_KPI, json.dumps(payload))
        except (ValueError, json.JSONDecodeError) as e:
            return KpiValue(kpi=kpi.key, verification_notes=[f"invalid model output: {e}"])

        if not isinstance(out, dict):
            return KpiValue(kpi=kpi.key, verification_notes=["invalid model output: not a JSON object"])
        value_text = out.get("value_text")
        value, unit = parse_value(None if value_text is None else str(value_text))
        if value is None:
            return KpiValue(kpi=kpi.key, confidence=_confidence(out.get("confidence")))

        known_docs = {c.doc_id for c in passages}
        doc_id = str(out.get("doc_id") or "")
        page = _int(out.get("page"), 1, 100_000)
        citation = None
        if out.get("quote") and page is not None:
            citation = Citation(
                doc_id=doc_id if doc_id in known_docs else passages[0].doc_id,
                page=page,
                quote=str(out["quote"]),
            )
        return KpiValue(
            kpi=kpi.key,
            value=value,
            unit=unit or normalise_unit(kpi.unit_hint),
            year=_int(out.get("year"), 1990, 2100),
            citation=citation,
            confidence=_confidence(out.get("confidence")),
        )
