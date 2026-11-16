"""Benchmark tooling: build a gold set with a human in the loop, then compare providers on it.

1. `esg-lens annotate report.pdf --out data/gold/real.jsonl`
   Runs the pipeline and writes a DRAFT gold line per indicator ("reviewed": false) with the page and
   quote it found. Open the file, check every value against the PDF, fix it, set "reviewed": true.
   The model proposes, a human decides: drafts never count in the evaluation.

2. `esg-lens bench data/gold/real.jsonl --providers azure,gemini,anthropic --out bench`
   Runs the same evaluation for each provider (each with its own index, since embeddings differ) and
   writes bench/results.json and bench/LEADERBOARD.md.

Costs are estimated from bench/pricing.json (USD per million tokens), which YOU fill from the providers'
current price pages: prices change too often to be hard-coded.
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path

from .agents import Orchestrator
from .config import Settings
from .evaluation import run_eval
from .llm import get_provider
from .store import DocumentStore


def annotate(pdf: str | Path, out: str | Path, settings: Settings) -> int:
    llm = get_provider(settings)
    store = DocumentStore(settings, llm)
    doc_id = store.add_pdf(pdf)
    index, pages = store.load(doc_id)
    report = Orchestrator(llm, index, {doc_id: pages}, settings.top_k, settings.hybrid_alpha).run(doc_id)
    lines = []
    for k in report.kpis:
        lines.append(
            {
                "pdf": str(pdf),
                "kpi": k.kpi,
                "value": k.value,
                "unit": k.unit,
                "page": k.citation.page if k.citation else None,
                "tolerance_pct": 1,
                "reviewed": False,
                "draft_quote": k.citation.quote if k.citation else None,
                "draft_verified": k.verified,
            }
        )
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return len(lines)


def _cost(result: dict, pricing: dict) -> float | None:
    price = pricing.get(result["provider"])
    if not price:
        return None
    return round(
        result["est_input_tokens"] / 1e6 * price["input_per_million"]
        + result["est_output_tokens"] / 1e6 * price["output_per_million"],
        4,
    )


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f} %"


def leaderboard_markdown(results: list[dict]) -> str:
    ok = [r for r in results if "error" not in r]
    ok.sort(key=lambda r: (-(r["accuracy"] or 0), r["hallucination_rate"] or 0))
    lines = [
        "# ESG Lens benchmark",
        "",
        f"{ok[0]['items'] if ok else 0} indicators across {ok[0]['documents'] if ok else 0} report(s).",
        "",
        "| Provider | Accuracy | Accuracy (disclosed) | Citation accuracy | Hallucination rate "
        "| Verified rate | Latency / report | Model calls / report | Est. cost (USD) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in ok:
        cost = r.get("est_cost_usd")
        lines.append(
            f"| {r['provider']} | {_pct(r['accuracy'])} | {_pct(r['accuracy_disclosed'])} "
            f"| {_pct(r['citation_accuracy'])} | {_pct(r['hallucination_rate'])} "
            f"| {_pct(r['verified_rate'])} "
            f"| {r['avg_latency_s']} s | {r['avg_llm_calls_per_doc']} | {'n/a' if cost is None else cost} |"
        )
    for r in results:
        if "error" in r:
            lines.append(f"| {r['provider']} | failed: {r['error'][:80]} | | | | | | | |")

    lines += [
        "",
        "## Errors by indicator",
        "",
        "| Indicator | " + " | ".join(r["provider"] for r in ok) + " |",
    ]
    lines.append("|---|" + "---|" * len(ok))
    kpis = sorted({d["kpi"] for r in ok for d in r["details"]})
    for kpi in kpis:
        cells = []
        for r in ok:
            rows = [d for d in r["details"] if d["kpi"] == kpi]
            wrong = sum(not d["correct"] for d in rows)
            cells.append(f"{wrong}/{len(rows)}")
        lines.append(f"| {kpi} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "Accuracy = value within tolerance or correctly reported as not disclosed. "
        "Costs are estimates (~4 characters per token) from bench/pricing.json.",
    ]
    return "\n".join(lines) + "\n"


def bench(gold: str | Path, providers: list[str], out: str | Path, base: Settings) -> list[dict]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    pricing_file = out / "pricing.json"
    pricing = json.loads(pricing_file.read_text(encoding="utf-8")) if pricing_file.exists() else {}
    results = []
    for provider in providers:
        settings = base.model_copy(update={"provider": provider, "data_dir": f"{base.data_dir}/{provider}"})
        try:
            llm = get_provider(settings)
            result = run_eval(gold, DocumentStore(settings, llm), llm, settings)
            result["est_cost_usd"] = _cost(result, pricing)
        except Exception as e:  # one failing provider must not stop the benchmark
            result = {
                "provider": provider,
                "error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc(limit=3),
            }
        results.append(result)
    (out / "results.json").write_text(encoding="utf-8", data=json.dumps(results, indent=2))
    (out / "LEADERBOARD.md").write_text(encoding="utf-8", data=leaderboard_markdown(results))
    return results
