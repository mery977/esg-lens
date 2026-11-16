"""Evaluation harness.

Gold file = JSON Lines, one expected indicator per line:
  {"pdf": "data/samples/sample_esg_report.pdf", "kpi": "scope1_emissions", "value": 48250,
   "unit": "tCO2e", "page": 2, "tolerance_pct": 1}
Use "value": null for an indicator the report does not disclose (the system must NOT invent one).
Lines with "reviewed": false (drafts written by `esg-lens annotate`) are skipped until a human checks them.

Metrics
- accuracy: value within tolerance (or correctly "not disclosed")
- citation_accuracy: cited page = expected page, among correct values
- verified_rate: share of returned values that passed the verification agent
- hallucination_rate: share of undisclosed indicators for which a value was returned
- latency and number of LLM calls per document
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .agents import Orchestrator
from .config import Settings
from .kpis import KPI_BY_KEY
from .llm.base import LLMProvider
from .store import DocumentStore


def _close(pred: float | None, gold: float | None, tol_pct: float) -> bool:
    if gold is None:
        return pred is None
    if pred is None:
        return False
    return abs(pred - gold) <= abs(gold) * tol_pct / 100 + 1e-9


def run_eval(gold_path: str | Path, store: DocumentStore, llm: LLMProvider, settings: Settings) -> dict:
    all_rows = [
        json.loads(line) for line in Path(gold_path).read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    rows = [r for r in all_rows if r.get("reviewed", True)]
    skipped = len(all_rows) - len(rows)
    usage_before = (llm.usage.input_chars, llm.usage.output_chars)
    by_pdf: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_pdf[r["pdf"]].append(r)

    details, latencies, calls = [], [], []
    for pdf, items in by_pdf.items():
        if not Path(pdf).exists() and Path(pdf).name == "sample_esg_report.pdf":
            from .sample import build_sample_report

            build_sample_report(pdf)
        doc_id = store.add_pdf(pdf)
        index, pages = store.load(doc_id)
        orch = Orchestrator(llm, index, {doc_id: pages}, settings.top_k, settings.hybrid_alpha)
        report = orch.run(doc_id, [KPI_BY_KEY[i["kpi"]] for i in items])
        latencies.append(report.latency_s)
        calls.append(report.llm_calls)
        preds = {k.kpi: k for k in report.kpis}
        for gold in items:
            pred = preds[gold["kpi"]]
            correct = _close(pred.value, gold.get("value"), gold.get("tolerance_pct", 1.0))
            page_ok = None
            if correct and gold.get("value") is not None and gold.get("page") is not None:
                page_ok = pred.citation is not None and pred.citation.page == gold["page"]
            details.append(
                {
                    "pdf": Path(pdf).name,
                    "kpi": gold["kpi"],
                    "expected": gold.get("value"),
                    "predicted": pred.value,
                    "unit": pred.unit,
                    "correct": correct,
                    "page_correct": page_ok,
                    "verified": pred.verified,
                    "notes": pred.verification_notes,
                }
            )

    n = len(details)
    disclosed = [d for d in details if d["expected"] is not None]
    undisclosed = [d for d in details if d["expected"] is None]
    returned = [d for d in details if d["predicted"] is not None]
    pages = [d for d in details if d["page_correct"] is not None]
    in_chars = llm.usage.input_chars - usage_before[0]
    out_chars = llm.usage.output_chars - usage_before[1]
    return {
        "provider": llm.name,
        "items": n,
        "skipped_unreviewed": skipped,
        "documents": len(by_pdf),
        # ~4 characters per token: a planning estimate, not a bill
        "est_input_tokens": in_chars // 4,
        "est_output_tokens": out_chars // 4,
        "accuracy": round(sum(d["correct"] for d in details) / n, 3) if n else None,
        "accuracy_disclosed": round(sum(d["correct"] for d in disclosed) / len(disclosed), 3)
        if disclosed
        else None,
        "citation_accuracy": round(sum(d["page_correct"] for d in pages) / len(pages), 3) if pages else None,
        "verified_rate": round(sum(d["verified"] for d in returned) / len(returned), 3) if returned else None,
        "hallucination_rate": round(
            sum(d["predicted"] is not None for d in undisclosed) / len(undisclosed), 3
        )
        if undisclosed
        else None,
        "avg_latency_s": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "avg_llm_calls_per_doc": round(sum(calls) / len(calls), 1) if calls else None,
        "details": details,
    }
