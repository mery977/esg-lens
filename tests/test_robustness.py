"""What breaks on real reports and real cloud models: units, number formats, messy PDF text,
off-by-one pages, rate limits, reasoning models that refuse a temperature, parallel usage counting."""

import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from esg_lens.agents import VerificationAgent
from esg_lens.kpis import KPI_BY_KEY
from esg_lens.llm.base import LLMProvider, is_transient
from esg_lens.numbers import parse_value, to_canonical
from esg_lens.schemas import Citation, KpiValue


@pytest.mark.parametrize(
    "text, value, unit",
    [
        ("48.2 ktCO2e", 48.2, "ktCO2e"),
        ("1.5 MtCO2e", 1.5, "MtCO2e"),
        ("5 kt CO₂e", 5, "ktCO2e"),
        ("12,345 tonnes of CO2 equivalent", 12345, "tCO2e"),
        ("1,500 TJ", 1500, "TJ"),
        ("2.3 TWh", 2.3, "TWh"),
        ("125 megalitres", 125, "ML"),
        ("3 400 m³", 3400, "m3"),
        ("1.234.567 m3", 1234567, "m3"),
        ("48 250,5 tCO2e", 48250.5, "tCO2e"),
        ("48\u202f250 tCO2e", 48250, "tCO2e"),  # narrow no-break space (French PDFs)
        ("1,234,567.5 MWh", 1234567.5, "MWh"),
    ],
)
def test_real_world_formats(text, value, unit):
    assert parse_value(text) == (value, unit)


@pytest.mark.parametrize(
    "value, unit, canonical, expected",
    [
        (48.2, "ktCO2e", "tCO2e", 48200),
        (1.5, "MtCO2e", "tCO2e", 1_500_000),
        (1500, "TJ", "MWh", 416_666.667),
        (2.3, "TWh", "MWh", 2_300_000),
        (125, "ML", "m3", 125_000),
    ],
)
def test_conversion_to_one_unit_per_indicator(value, unit, canonical, expected):
    converted, out_unit = to_canonical(value, unit, canonical)
    assert out_unit == canonical and converted == pytest.approx(expected, rel=1e-6)


MESSY_PAGES = [
    "Introduction.",
    "Climate. In 2025 our Scope 1 emis-\nsions were 48.2 kt CO\u2082e, down 7\u00a0% on 2024.",
    "Our efﬁciency programme cut energy consumption to 1,500 TJ.",
]


def test_verifier_handles_hyphenation_subscripts_and_ligatures():
    v = VerificationAgent({"d": MESSY_PAGES})
    ok = KpiValue(
        kpi="scope1_emissions",
        value=48.2,
        unit="ktCO2e",
        citation=Citation(doc_id="d", page=2, quote="our Scope 1 emissions were 48.2 kt CO2e"),
    )
    assert v.verify(KPI_BY_KEY["scope1_emissions"], ok) == []
    energy = KpiValue(
        kpi="energy_consumption",
        value=1500,
        unit="TJ",
        citation=Citation(
            doc_id="d", page=3, quote="Our efficiency programme cut energy consumption to 1,500 TJ."
        ),
    )
    assert v.verify(KPI_BY_KEY["energy_consumption"], energy) == []


def test_verifier_fixes_an_off_by_one_page_but_not_an_invented_quote():
    v = VerificationAgent({"d": MESSY_PAGES})
    shifted = KpiValue(
        kpi="energy_consumption",
        value=1500,
        unit="TJ",
        citation=Citation(doc_id="d", page=2, quote="cut energy consumption to 1,500 TJ"),
    )
    assert v.verify(KPI_BY_KEY["energy_consumption"], shifted) == [] and shifted.citation.page == 3
    invented = shifted.model_copy(deep=True)
    invented.citation.quote = "energy consumption reached 1,500 TJ"
    assert any("not found verbatim" in p for p in v.verify(KPI_BY_KEY["energy_consumption"], invented))


def test_verifier_rejects_a_unit_from_another_family():
    v = VerificationAgent({"d": MESSY_PAGES})
    wrong = KpiValue(
        kpi="energy_consumption",
        value=1500,
        unit="tCO2e",
        citation=Citation(doc_id="d", page=3, quote="cut energy consumption to 1,500 TJ"),
    )
    assert any("unexpected" in p for p in v.verify(KPI_BY_KEY["energy_consumption"], wrong))


class _Flaky(LLMProvider):
    name = "flaky"
    base_delay_s = 0.0

    def __init__(self, errors):
        super().__init__()
        self.errors = list(errors)

    def _complete(self, system, user):
        if self.errors:
            raise self.errors.pop(0)
        return "{}"

    def _embed(self, texts):
        return [[1.0] for _ in texts]


class _Status(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def test_rate_limits_and_server_errors_are_retried():
    llm = _Flaky([_Status(429), _Status(503), TimeoutError("read timeout")])
    assert llm.complete("s", "u") == "{}" and llm.usage.calls == 1


def test_client_errors_are_not_retried():
    llm = _Flaky([_Status(400)])
    with pytest.raises(_Status):
        llm.complete("s", "u")


def test_usage_counter_is_thread_safe():
    llm = _Flaky([])
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: llm.complete("s", "u"), range(400)))
    assert llm.usage.calls == 400


def _response(status):
    return httpx.Response(status, request=httpx.Request("POST", "https://example.invalid"))


def test_real_sdk_errors_are_classified():
    openai = pytest.importorskip("openai")
    assert is_transient(openai.RateLimitError("slow down", response=_response(429), body=None))
    assert is_transient(openai.InternalServerError("oops", response=_response(500), body=None))
    assert not is_transient(openai.BadRequestError("bad", response=_response(400), body=None))
    anthropic = pytest.importorskip("anthropic")
    assert is_transient(anthropic.RateLimitError("slow down", response=_response(429), body=None))


class _Scripted(LLMProvider):
    """Returns canned model answers, to test how the agents cope with sloppy or failing output."""

    name = "scripted"
    base_delay_s = 0.0

    def __init__(self, answer):
        super().__init__()
        self.answer = answer

    def _complete(self, system, user):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def _embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


def test_sloppy_model_output_is_tolerated(indexed, llm):
    from esg_lens.agents.extraction import ExtractionAgent

    _, doc_id, index, _ = indexed
    passages = [c for c, _ in index.search("Scope 1", llm, k=2)]
    answer = (
        '{"value_text": "48,250 tCO2e", "year": "FY2025", "page": "p. 2", "doc_id": "made-up",'
        ' "quote": "Scope 1 emissions totalled 48,250 tCO2e", "confidence": "high"}'
    )
    kpi = ExtractionAgent(_Scripted(answer)).extract(KPI_BY_KEY["scope1_emissions"], passages)
    assert (kpi.value, kpi.year, kpi.citation.page, kpi.confidence) == (48250, 2025, 2, 0.9)
    assert kpi.citation.doc_id == doc_id  # an unknown doc_id from the model is replaced


def test_one_failing_indicator_does_not_break_the_report(indexed):
    from esg_lens.agents import Orchestrator

    _, doc_id, index, pages = indexed
    report = Orchestrator(_Scripted(ValueError("model exploded")), index, {doc_id: pages}).run(doc_id)
    assert len(report.kpis) == 7
    assert all(k.value is None and "extraction failed" in k.verification_notes[0] for k in report.kpis)


def test_fetch_downloads_checks_pdf_and_survives_a_bad_link(tmp_path, sample_pdf):
    from esg_lens.reports import fetch

    not_pdf = tmp_path / "page.html"
    not_pdf.write_text("<html>moved</html>", encoding="utf-8")
    catalog = tmp_path / "catalog.json"
    reports = [
        {"file": "good.pdf", "url": sample_pdf.resolve().as_uri()},
        {"file": "moved.pdf", "url": not_pdf.resolve().as_uri()},
        {"file": "dead.pdf", "url": (tmp_path / "missing.pdf").resolve().as_uri()},
    ]
    catalog.write_text(json.dumps({"reports": reports}), encoding="utf-8")
    statuses = dict(fetch(catalog, tmp_path / "reports"))
    assert statuses["good.pdf"].startswith("downloaded")
    assert "did not return a PDF" in statuses["moved.pdf"] and statuses["dead.pdf"].startswith("FAILED")
    assert dict(fetch(catalog, tmp_path / "reports"))["good.pdf"] == "already downloaded"


def test_shipped_catalog_is_valid():
    from esg_lens.reports import load_catalog

    reports = load_catalog("data/reports/catalog.json")
    assert len(reports) >= 3
    assert all(r["url"].startswith("https://") and r["file"].endswith(".pdf") for r in reports)


def test_run_all_writes_json_csv_and_summary(tmp_path, monkeypatch, sample_pdf):
    import shutil

    from esg_lens import cli
    from esg_lens.config import get_settings

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "reports").mkdir(parents=True)
    shutil.copy(sample_pdf, tmp_path / "data" / "reports" / "northwind-2025.pdf")
    monkeypatch.setenv("ESG_PROVIDER", "fake")
    monkeypatch.setenv("ESG_DATA_DIR", str(tmp_path / ".esg_fake"))
    get_settings.cache_clear()
    try:
        assert cli.main(["run", "--all"]) == 0
        first = (tmp_path / "results" / "fake" / "northwind-2025.json").stat().st_mtime_ns
        assert cli.main(["run", "--all"]) == 0  # second run: nothing left to do, results kept
        assert (tmp_path / "results" / "fake" / "northwind-2025.json").stat().st_mtime_ns == first
    finally:
        get_settings.cache_clear()
    out = tmp_path / "results" / "fake"
    csv_text = (out / "northwind-2025.csv").read_text(encoding="utf-8-sig")
    assert csv_text.startswith("indicator;value;unit") and "scope2_emissions;12900.0;tCO2e" in csv_text
    assert "| northwind-2025 | 6/7 | 6 | 1 | 0 |" in (out / "SUMMARY.md").read_text(encoding="utf-8")


def test_check_command_and_diagnosis(monkeypatch, capsys):
    from esg_lens import cli
    from esg_lens.config import get_settings
    from esg_lens.diagnose import explain

    monkeypatch.setenv("ESG_PROVIDER", "fake")
    get_settings.cache_clear()
    try:
        assert cli.main(["check"]) == 0 and "fake: OK" in capsys.readouterr().out
    finally:
        get_settings.cache_clear()

    class _ClientError(Exception):
        def __init__(self, code, msg):
            super().__init__(msg)
            self.code = code

    html = _ClientError(400, "{'message': '<html><title>Error 400 (Bad Request)!!1</title></html>'}")
    assert "malformed" in explain("gemini", html)
    assert "invalid" in explain(
        "gemini", _ClientError(400, "API key not valid. Please pass a valid API key.")
    )
    assert "model name" in explain("gemini", _ClientError(404, "models/gemini-x is not found"))
    assert "quota" in explain("gemini", _ClientError(429, "RESOURCE_EXHAUSTED"))


GOOGLE_429 = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota. "
    "Please retry in 35.45177743s.', 'details': [{'@type': 'type.googleapis.com/google.rpc.RetryInfo', "
    "'retryDelay': '35s'}]}}"
)


def test_server_retry_delay_is_read():
    from esg_lens.llm.base import retry_after

    assert retry_after(Exception(GOOGLE_429)) == pytest.approx(35.45177743)
    assert retry_after(Exception("{'retryDelay': '57s'}")) == 57
    assert retry_after(Exception("plain error")) is None


def test_retry_waits_what_the_server_asks(monkeypatch):
    from esg_lens.llm import base

    waits = []
    monkeypatch.setattr(base.time, "sleep", waits.append)
    err = _Status(429)
    err.args = (GOOGLE_429,)
    llm = _Flaky([err])
    assert llm.complete("s", "u") == "{}" and waits and waits[0] >= 36


def test_daily_quota_fails_fast_with_a_clear_message():
    err = _Status(429)
    err.args = ("Quota exceeded per day. Please retry in 7200s.",)
    with pytest.raises(RuntimeError, match="run the same command again later"):
        _Flaky([err]).complete("s", "u")


def test_rate_limiter_spreads_load_over_the_minute():
    from esg_lens.llm.base import RateLimiter

    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    limiter = RateLimiter(100, clock=lambda: now[0], sleep=sleep)
    for _ in range(4):
        limiter.acquire(64)  # four batches of 64 texts with a 100 / minute quota
    assert now[0] >= 120 and len(slept) == 3


def test_embedding_resumes_after_an_interruption(tmp_path):
    from esg_lens.index import HybridIndex
    from esg_lens.schemas import Chunk

    chunks = [Chunk(chunk_id=str(i), doc_id="d", page=1, text=f"text {i}") for i in range(10)]

    class _Counting(_Flaky):
        def __init__(self, fail_after=None):
            super().__init__([])
            self.embedded, self.fail_after = 0, fail_after

        def _embed(self, texts):
            if self.fail_after is not None and self.embedded >= self.fail_after:
                raise ValueError("quota")
            self.embedded += len(texts)
            return [[1.0, float(i)] for i in range(len(texts))]

    progress = tmp_path / "partial.npy"
    with pytest.raises(ValueError):
        HybridIndex.build(chunks, _Counting(fail_after=6), batch=3, progress=progress)
    assert progress.exists()
    second = _Counting()
    index = HybridIndex.build(chunks, second, batch=3, progress=progress)
    assert len(index.chunks) == 10 and second.embedded == 4 and not progress.exists()


def test_evidence_locates_quotes_despite_pdf_line_breaks():
    from esg_lens.evidence import locate

    page = "Intro text. Gross Scope 1 GHG\nemissions tCO2eq 36,442 33,245* 37,870 10 %\nNext line."
    ev = locate(page, "Gross Scope 1 GHG emissions tCO2eq 36,442", width=12)
    assert ev["found"] and ev["match"] == "Gross Scope 1 GHG emissions tCO2eq 36,442"
    assert ev["before"].endswith("Intro text. ") and ev["after"].startswith(" 33,245")
    assert not locate(page, "invented sentence")["found"]


METSO_LIKE = [
    "Diversity. In 2023, the company set a new long-term target to increase the proportion of women in "
    "middle and senior management positions. The target is to achieve a ratio of 30% female/70% male for "
    "middle and senior management positions by the end of 2030. In 2025, the ratio was 19%/81%. "
    "Board members: 9. Our new plant opened in 2019, a ratio of 19%/81% was achieved in productivity."
]


def test_indicator_named_in_the_previous_sentence_is_accepted():
    v = VerificationAgent({"d": METSO_LIKE})
    ok = KpiValue(
        kpi="women_in_management",
        value=19,
        unit="%",
        citation=Citation(doc_id="d", page=1, quote="In 2025, the ratio was 19%/81%"),
    )
    assert v.verify(KPI_BY_KEY["women_in_management"], ok) == []


def test_indicator_named_only_far_away_is_still_rejected():
    v = VerificationAgent({"d": METSO_LIKE})
    far = KpiValue(
        kpi="women_in_management",
        value=19,
        unit="%",
        citation=Citation(doc_id="d", page=1, quote="a ratio of 19%/81% was achieved in productivity"),
    )
    assert any("does not mention" in p for p in v.verify(KPI_BY_KEY["women_in_management"], far))


def test_reverify_updates_saved_results_without_any_model(tmp_path, monkeypatch):
    from esg_lens import cli
    from esg_lens.schemas import ExtractionReport

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".esg_gemini" / "doc").mkdir(parents=True)
    (tmp_path / ".esg_gemini" / "doc" / "pages.json").write_text(json.dumps(METSO_LIKE), encoding="utf-8")
    saved = ExtractionReport(
        doc_id="doc",
        provider="gemini",
        latency_s=1.0,
        llm_calls=2,
        kpis=[
            KpiValue(
                kpi="women_in_management",
                value=19,
                unit="%",
                verified=False,
                verification_notes=["the quote does not mention Women in management positions"],
                citation=Citation(doc_id="doc", page=1, quote="In 2025, the ratio was 19%/81"),
            )
        ],
    )
    out = tmp_path / "results" / "gemini"
    out.mkdir(parents=True)
    (out / "metso-2025.json").write_text(saved.model_dump_json(), encoding="utf-8")

    assert cli.main(["reverify", "--provider", "gemini"]) == 0
    after = ExtractionReport.model_validate_json((out / "metso-2025.json").read_text(encoding="utf-8"))
    assert after.kpis[0].verified and after.kpis[0].verification_notes == []
    ev = json.loads((out / "metso-2025.evidence.json").read_text(encoding="utf-8"))
    assert ev["women_in_management"]["found"]
    assert "| metso-2025 | 1/1 | 1 |" in (out / "SUMMARY.md").read_text(encoding="utf-8")
