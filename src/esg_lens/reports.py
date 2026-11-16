"""Real reports: download the public reports listed in data/reports/catalog.json, then run the full
pipeline on them and save the results where they can be read, reviewed and compared across providers.

    esg-lens fetch                       # download every report of the catalogue (skips existing files)
    esg-lens run data/reports/x.pdf      # index + extract, writes results/<provider>/x.json and .csv
    esg-lens run --all                   # every PDF of data/reports/, plus results/<provider>/SUMMARY.md
"""

from __future__ import annotations

import csv
import json
import urllib.request
from pathlib import Path

from .agents import Orchestrator
from .config import Settings
from .evidence import evidence_for
from .llm.base import LLMProvider
from .schemas import ExtractionReport
from .store import DocumentStore

CATALOG = Path("data/reports/catalog.json")
REPORTS_DIR = Path("data/reports")
RESULTS_DIR = Path("results")
# Some corporate sites refuse requests that do not look like a browser.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) esg-lens/0.1",
    "Accept": "application/pdf,*/*",
}


def load_catalog(path: str | Path = CATALOG) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["reports"]


def fetch(
    catalog: str | Path = CATALOG, dest: str | Path = REPORTS_DIR, timeout: int = 120
) -> list[tuple[str, str]]:
    """Download the catalogue's PDFs. Returns (file, status) per report; a failure does not stop the rest."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    statuses = []
    for r in load_catalog(catalog):
        target = dest / r["file"]
        if target.exists() and target.stat().st_size > 0:
            statuses.append((r["file"], "already downloaded"))
            continue
        try:
            req = urllib.request.Request(r["url"], headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
            if not data.startswith(b"%PDF"):
                raise ValueError("the link did not return a PDF (moved or blocked?): open it in a browser")
            target.write_bytes(data)
            statuses.append((r["file"], f"downloaded ({len(data) / 1e6:.1f} MB)"))
        except Exception as e:  # noqa: BLE001 - reported per file
            statuses.append((r["file"], f"FAILED: {e}. Download it from {r['url']} into {dest}"))
    return statuses


def _rows(report: ExtractionReport) -> list[dict]:
    rows = []
    for k in report.kpis:
        cit = k.citation
        if k.verified:
            status = "passed"
        elif any(n.startswith(("extraction failed", "invalid model output")) for n in k.verification_notes):
            status = "ERROR"
        else:
            status = "not disclosed" if k.value is None else "FAILED"
        rows.append(
            {
                "indicator": k.kpi,
                "value": k.value,
                "unit": k.unit,
                "year": k.year,
                "checks": status,
                "page": cit.page if cit else None,
                "quote": cit.quote if cit else None,
                "self_corrected": "; ".join(k.corrected_issues),
                "notes": "; ".join(k.verification_notes),
            }
        )
    return rows


def run_report(
    pdf: str | Path, llm: LLMProvider, settings: Settings, out_dir: str | Path | None = None
) -> Path:
    """Index and extract one report; write <out>/<name>.json (full) and <name>.csv (to review in Excel)."""
    pdf = Path(pdf)
    out = Path(out_dir or RESULTS_DIR / llm.name)
    out.mkdir(parents=True, exist_ok=True)
    store = DocumentStore(settings, llm)
    doc_id = store.add_pdf(pdf)
    index, pages = store.load(doc_id)
    report = Orchestrator(llm, index, {doc_id: pages}, settings.top_k, settings.hybrid_alpha).run(doc_id)

    (out / f"{pdf.stem}.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    # Short excerpts around each cited sentence, so the results can be reviewed without the PDF.
    evidence = evidence_for(report, {doc_id: pages})
    (out / f"{pdf.stem}.evidence.json").write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    rows = _rows(report)
    # utf-8-sig + ";" so that Excel (French locale included) opens it correctly with a double click
    with open(out / f"{pdf.stem}.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    return out / f"{pdf.stem}.json"


def summary_markdown(results_dir: str | Path) -> str:
    lines = [
        "| Report | Indicators found | Checks passed | Self-corrected | Errors | Model calls | Time (s) |",
        "|---|---|---|---|---|---|---|",
    ]
    for path in sorted(p for p in Path(results_dir).glob("*.json") if not p.name.endswith(".evidence.json")):
        r = ExtractionReport.model_validate_json(path.read_text(encoding="utf-8"))
        rows = _rows(r)
        found = sum(row["value"] is not None for row in rows)
        passed = sum(row["checks"] == "passed" for row in rows)
        fixed = sum(bool(row["self_corrected"]) for row in rows)
        errors = sum(row["checks"] == "ERROR" for row in rows)
        lines.append(
            f"| {path.stem} | {found}/{len(rows)} | {passed} | {fixed} | {errors} "
            f"| {r.llm_calls} | {r.latency_s} |"
        )
    return (
        "\n".join(lines)
        + "\n\nEvery value must still be checked against the cited page before it enters a gold set.\n"
    )


# --- Saved results, as served to the UI (works without any API key) --------------------------------

GOLD_FILES = (Path("data/gold/real.jsonl"), Path("data/gold/sample.jsonl"))


def list_results(results_dir: str | Path = RESULTS_DIR) -> list[dict]:
    """Every saved extraction: provider, report, headline numbers."""
    items = []
    for path in sorted(Path(results_dir).glob("*/*.json")):
        if path.name.endswith(".evidence.json"):
            continue
        try:
            r = ExtractionReport.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        rows = _rows(r)
        items.append(
            {
                "provider": path.parent.name,
                "report": path.stem,
                "found": sum(row["value"] is not None for row in rows),
                "passed": sum(row["checks"] == "passed" for row in rows),
                "indicators": len(rows),
                "llm_calls": r.llm_calls,
                "latency_s": r.latency_s,
            }
        )
    return items


def gold_for(report: str, gold_files=GOLD_FILES) -> dict[str, dict]:
    """Human-checked values for a report (matched on the PDF file name), keyed by indicator."""
    out = {}
    for gf in gold_files:
        if not Path(gf).exists():
            continue
        for line in Path(gf).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if Path(row.get("pdf", "")).stem == report and row.get("reviewed", True):
                out[row["kpi"]] = row
    return out


def load_result(provider: str, report: str, results_dir: str | Path = RESULTS_DIR) -> dict:
    """A saved extraction with its evidence (saved excerpts, else rebuilt from the local index) and gold."""
    base = Path(results_dir) / provider
    path = base / f"{report}.json"
    if not path.resolve().is_relative_to(Path(results_dir).resolve()) or not path.exists():
        raise KeyError(f"{provider}/{report}")
    r = ExtractionReport.model_validate_json(path.read_text(encoding="utf-8"))
    ev_path = base / f"{report}.evidence.json"
    if ev_path.exists():
        evidence = json.loads(ev_path.read_text(encoding="utf-8"))
    else:  # results produced before evidence files existed: rebuild from the provider's local store
        pages_file = Path(f".esg_{provider}") / r.doc_id / "pages.json"
        pages = json.loads(pages_file.read_text(encoding="utf-8")) if pages_file.exists() else None
        evidence = evidence_for(r, {r.doc_id: pages}) if pages else {}
    return {"report": json.loads(r.model_dump_json()), "evidence": evidence, "gold": gold_for(report)}


def reverify(
    provider: str, results_dir: str | Path = RESULTS_DIR, data_dir: str | Path | None = None
) -> list[str]:
    """Re-run the deterministic verification on saved results, without calling any model.

    Verification is pure code: when its rules improve, earlier runs can be re-checked for free. Needs the
    local index of the run (.esg_<provider>/) for the page texts. Also (re)writes the evidence excerpts.
    """
    from .agents import VerificationAgent
    from .kpis import KPI_BY_KEY

    base = Path(results_dir) / provider
    store_root = Path(data_dir or f".esg_{provider}")
    messages = []
    for path in sorted(p for p in base.glob("*.json") if not p.name.endswith(".evidence.json")):
        report = ExtractionReport.model_validate_json(path.read_text(encoding="utf-8"))
        pages_file = store_root / report.doc_id / "pages.json"
        if not pages_file.exists():
            messages.append(f"{path.stem}: skipped (no local index in {store_root})")
            continue
        pages = json.loads(pages_file.read_text(encoding="utf-8"))
        verifier = VerificationAgent({report.doc_id: pages})
        changed = []
        for k in report.kpis:
            spec = KPI_BY_KEY.get(k.kpi)
            if spec is None or k.value is None:
                continue
            problems = verifier.verify(spec, k)
            # The figure check ran before unit conversion (e.g. GWh -> MWh); its original verdict stands.
            figure_ok_before = not any("figure does not appear" in n for n in k.verification_notes)
            if figure_ok_before:
                problems = [p for p in problems if "figure does not appear" not in p]
            verified = not problems
            if verified != k.verified:
                changed.append(f"{k.kpi}: {'now verified' if verified else 'now rejected'}")
            k.verified, k.verification_notes = verified, problems
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        evidence = evidence_for(report, {report.doc_id: pages})
        (base / f"{path.stem}.evidence.json").write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        rows = _rows(report)
        with open(base / f"{path.stem}.csv", "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        messages.append(
            f"{path.stem}: " + ("; ".join(changed) if changed else "no change") + ", evidence saved"
        )
    if base.exists():
        (base / "SUMMARY.md").write_text(summary_markdown(base), encoding="utf-8")
    return messages
