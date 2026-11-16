import json

from esg_lens.agents import Orchestrator, QAAgent, VerificationAgent
from esg_lens.evaluation import run_eval
from esg_lens.ingest import chunk_pages
from esg_lens.kpis import KPI_BY_KEY
from esg_lens.schemas import Citation, KpiValue


def test_chunks_keep_page_numbers():
    chunks = chunk_pages("d", ["a" * 2000, "", "short page"], size=900, overlap=100)
    assert {c.page for c in chunks} == {1, 3}
    assert all(len(c.text) <= 900 for c in chunks)


def test_hybrid_search_finds_the_right_page(indexed, llm):
    _, _, index, _ = indexed
    top_chunk, _ = index.search("Scope 1 emissions", llm, k=1)[0]
    assert top_chunk.page == 2


def test_extraction_is_cited_and_verified(indexed, llm):
    _, doc_id, index, pages = indexed
    report = Orchestrator(llm, index, {doc_id: pages}).run(doc_id, [KPI_BY_KEY["scope1_emissions"]])
    kpi = report.kpis[0]
    assert kpi.value == 48250 and kpi.unit == "tCO2e" and kpi.year == 2025
    assert kpi.verified and kpi.citation.page == 2


def test_energy_is_normalised_to_mwh(indexed, llm):
    _, doc_id, index, pages = indexed
    kpi = Orchestrator(llm, index, {doc_id: pages}).run(doc_id, [KPI_BY_KEY["energy_consumption"]]).kpis[0]
    assert kpi.value == 410_000 and kpi.unit == "MWh"


def test_undisclosed_indicator_is_not_invented(indexed, llm):
    _, doc_id, index, pages = indexed
    kpi = Orchestrator(llm, index, {doc_id: pages}).run(doc_id, [KPI_BY_KEY["water_withdrawal"]]).kpis[0]
    assert kpi.value is None


def test_verifier_rejects_a_hallucinated_quote(indexed):
    _, doc_id, _, pages = indexed
    fake = KpiValue(
        kpi="scope1_emissions",
        value=99999,
        unit="tCO2e",
        citation=Citation(doc_id=doc_id, page=2, quote="Scope 1 emissions were 99,999 tCO2e."),
    )
    problems = VerificationAgent({doc_id: pages}).verify(KPI_BY_KEY["scope1_emissions"], fake)
    assert any("not found verbatim" in p for p in problems)


def test_verifier_rejects_impossible_percentage(indexed):
    _, doc_id, _, pages = indexed
    bad = KpiValue(
        kpi="renewable_share",
        value=640,
        unit="%",
        citation=Citation(doc_id=doc_id, page=3, quote="renewable 640%"),
    )
    problems = VerificationAgent({doc_id: pages}).verify(KPI_BY_KEY["renewable_share"], bad)
    assert "a percentage cannot exceed 100" in problems


def test_qa_answers_with_citation(indexed, llm):
    _, _, index, _ = indexed
    ans = QAAgent(llm, index).ask("What share of renewable energy did the group reach?")
    assert ans.citations and ans.citations[0].page == 3


def test_evaluation_runs_end_to_end(settings, llm, indexed, sample_pdf, tmp_path):
    store = indexed[0]
    gold = tmp_path / "gold.jsonl"
    rows = [
        {"pdf": str(sample_pdf), "kpi": "scope1_emissions", "value": 48250, "page": 2},
        {"pdf": str(sample_pdf), "kpi": "water_withdrawal", "value": None},
    ]
    gold.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    result = run_eval(gold, store, llm, settings)
    assert result["accuracy"] == 1.0 and result["hallucination_rate"] == 0.0


def test_annotate_writes_unreviewed_drafts_skipped_by_eval(settings, llm, sample_pdf, tmp_path):
    from esg_lens.benchmark import annotate
    from esg_lens.store import DocumentStore

    gold = tmp_path / "draft.jsonl"
    n = annotate(sample_pdf, gold, settings)
    rows = [json.loads(line) for line in gold.read_text(encoding="utf-8").splitlines()]
    assert n == len(rows) == 7 and not any(r["reviewed"] for r in rows)
    result = run_eval(gold, DocumentStore(settings, llm), llm, settings)
    assert result["items"] == 0 and result["skipped_unreviewed"] == 7


def test_bench_writes_leaderboard_and_survives_a_failing_provider(settings, sample_pdf, tmp_path):
    from esg_lens.benchmark import bench

    gold = tmp_path / "gold.jsonl"
    row = {"pdf": str(sample_pdf), "kpi": "scope1_emissions", "value": 48250, "page": 2}
    gold.write_text(json.dumps(row) + "\n", encoding="utf-8")
    out = tmp_path / "bench"
    # "azure" fails here on purpose (no endpoint configured): the benchmark must still complete.
    results = bench(gold, ["fake", "azure"], out, settings)
    assert results[0]["accuracy"] == 1.0 and "error" in results[1]
    board = (out / "LEADERBOARD.md").read_text(encoding="utf-8")
    assert "| fake | 100.0 %" in board and "azure | failed" in board


def test_self_correction_fixes_the_scope2_trap(indexed, llm):
    _, doc_id, index, pages = indexed
    kpi = Orchestrator(llm, index, {doc_id: pages}).run(doc_id, [KPI_BY_KEY["scope2_emissions"]]).kpis[0]
    # First answer = location-based figure, rejected by verification; the retry returns the market-based one.
    assert kpi.value == 12900 and kpi.verified
    assert kpi.attempts == 2 and any("location-based" in issue for issue in kpi.corrected_issues)


def test_verifier_flags_wrong_basis(indexed):
    _, doc_id, _, pages = indexed
    wrong = KpiValue(
        kpi="scope2_emissions",
        value=31400,
        unit="tCO2e",
        citation=Citation(
            doc_id=doc_id, page=2, quote="Scope 2 location-based emissions reached 31,400 tCO2e in 2025."
        ),
    )
    problems = VerificationAgent({doc_id: pages}).verify(KPI_BY_KEY["scope2_emissions"], wrong)
    assert any("location-based" in p for p in problems)


def test_quotes_do_not_include_section_titles(indexed, llm):
    _, doc_id, index, pages = indexed
    kpi = Orchestrator(llm, index, {doc_id: pages}).run(doc_id, [KPI_BY_KEY["scope1_emissions"]]).kpis[0]
    assert kpi.citation.quote.startswith("In 2025, our Scope 1 emissions")
