"""Command line: `esg-lens ingest report.pdf`, `esg-lens extract <doc_id>`, `esg-lens ask <doc_id> "..."`,
`esg-lens eval data/gold/sample.jsonl`, `esg-lens annotate report.pdf --out gold.jsonl`,
`esg-lens bench gold.jsonl --providers azure,gemini,anthropic`, `esg-lens demo`,
`esg-lens fetch` (download the real reports), `esg-lens run --all` (run them, save the results)."""

from __future__ import annotations

import argparse
import json
import sys

from .agents import Orchestrator, QAAgent
from .config import get_settings
from .llm import get_provider
from .store import DocumentStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="esg-lens")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest", help="index a PDF report")
    p.add_argument("pdf")
    p = sub.add_parser("extract", help="extract all KPIs from an indexed report")
    p.add_argument("doc_id")
    p = sub.add_parser("ask", help="ask a question about an indexed report")
    p.add_argument("doc_id")
    p.add_argument("question")
    p = sub.add_parser("eval", help="run the evaluation on a gold file")
    p.add_argument("gold")
    p.add_argument("--out", default=None, help="write the JSON report here")
    p = sub.add_parser("annotate", help="write draft gold lines for a report, to be checked by a human")
    p.add_argument("pdf")
    p.add_argument("--out", required=True)
    p = sub.add_parser("bench", help="compare providers on a gold file")
    p.add_argument("gold")
    p.add_argument("--providers", default="fake", help="comma-separated: fake,azure,gemini,anthropic")
    p.add_argument("--out", default="bench")
    sub.add_parser("demo", help="generate the synthetic sample report and run the pipeline on it")
    sub.add_parser("check", help="verify the provider settings and credentials with one tiny call")
    p = sub.add_parser("reverify", help="re-run the verification on saved results (no model call)")
    p.add_argument("--provider", default=None, help="default: every provider in results/")
    p = sub.add_parser("fetch", help="download the public reports listed in data/reports/catalog.json")
    p.add_argument("--catalog", default="data/reports/catalog.json")
    p = sub.add_parser("run", help="index + extract real reports; results saved in results/<provider>/")
    p.add_argument("pdf", nargs="?", help="one PDF (or use --all)")
    p.add_argument("--all", action="store_true", help="every PDF in data/reports/ not processed yet")
    p.add_argument("--force", action="store_true", help="with --all: also redo reports that have results")
    args = parser.parse_args(argv)

    if args.cmd == "reverify":
        from .reports import RESULTS_DIR, reverify

        providers = (
            [args.provider] if args.provider else sorted(p.name for p in RESULTS_DIR.glob("*/") if p.is_dir())
        )
        for prov in providers:
            for message in reverify(prov):
                print(f"{prov}/{message}")
        return 0

    if args.cmd == "fetch":
        from .reports import fetch

        statuses = fetch(args.catalog)
        for name, status in statuses:
            print(f"{name}: {status}")
        return 0 if all(not s.startswith("FAILED") for _, s in statuses) else 1

    settings = get_settings()
    if args.cmd == "annotate":
        from .benchmark import annotate

        n = annotate(args.pdf, args.out, settings)
        print(
            f"{n} draft lines appended to {args.out}: check each one against the PDF, then set reviewed=true"
        )
        return 0
    if args.cmd == "bench":
        from .benchmark import bench

        results = bench(
            args.gold, [p.strip() for p in args.providers.split(",") if p.strip()], args.out, settings
        )
        print(open(f"{args.out}/LEADERBOARD.md", encoding="utf-8").read())
        return 0 if all("error" not in r for r in results) else 1
    if args.cmd == "check":
        from .diagnose import check

        ok, message = check(settings)
        print(message)
        return 0 if ok else 1
    llm = get_provider(settings)
    store = DocumentStore(settings, llm)

    if args.cmd == "ingest":
        print(store.add_pdf(args.pdf))
    elif args.cmd == "extract":
        index, pages = store.load(args.doc_id)
        report = Orchestrator(llm, index, {args.doc_id: pages}, settings.top_k, settings.hybrid_alpha).run(
            args.doc_id
        )
        print(report.model_dump_json(indent=2))
    elif args.cmd == "ask":
        index, _ = store.load(args.doc_id)
        print(
            QAAgent(llm, index, settings.top_k, settings.hybrid_alpha)
            .ask(args.question)
            .model_dump_json(indent=2)
        )
    elif args.cmd == "eval":
        from .evaluation import run_eval

        result = run_eval(args.gold, store, llm, settings)
        text = json.dumps(result, indent=2)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text)
        print(text)
    elif args.cmd == "run":
        from pathlib import Path

        from .reports import REPORTS_DIR, RESULTS_DIR, run_report, summary_markdown

        pdfs = (
            sorted(REPORTS_DIR.glob("*.pdf"), key=lambda p: p.stat().st_size)
            if args.all
            else [Path(args.pdf)]
            if args.pdf
            else []
        )
        if not pdfs:
            print("no report: give a PDF path, or use --all after `esg-lens fetch`")
            return 1
        failures = 0
        for pdf in pdfs:
            done = RESULTS_DIR / llm.name / f"{pdf.stem}.json"
            if args.all and not args.force and done.exists():
                # Results are kept: a rerun spends quota only on the reports still missing.
                print(f"{pdf.name}: already done ({done}), skipped. Use --force to redo it.")
                continue
            print(f"{pdf.name}: indexing and extracting with {llm.name}...", flush=True)
            try:
                print(f"  -> {run_report(pdf, llm, settings)}")
            except Exception as e:  # noqa: BLE001 - one bad report must not stop the others
                failures += 1
                print(f"  -> FAILED: {type(e).__name__}: {e}")
                if "quota exhausted" in str(e):
                    print("  Daily quota reached: stopping here. Run the same command tomorrow, it resumes.")
                    break
        out = RESULTS_DIR / llm.name
        if out.exists():
            summary = summary_markdown(out)
            (out / "SUMMARY.md").write_text(summary, encoding="utf-8")
            print("\n" + summary)
        return 1 if failures else 0
    elif args.cmd == "demo":
        from .sample import build_sample_report

        path = build_sample_report("data/samples/sample_esg_report.pdf")
        doc_id = store.add_pdf(path)
        index, pages = store.load(doc_id)
        report = Orchestrator(llm, index, {doc_id: pages}, settings.top_k, settings.hybrid_alpha).run(doc_id)
        print(report.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
