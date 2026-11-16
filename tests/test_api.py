import importlib

from fastapi.testclient import TestClient


def make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("ESG_PROVIDER", "fake")
    monkeypatch.setenv("ESG_DATA_DIR", str(tmp_path / "api_store"))
    import esg_lens.config as config

    config.get_settings.cache_clear()
    import esg_lens.api as api

    importlib.reload(api)
    return TestClient(api.app)


def test_full_api_flow(tmp_path, monkeypatch, sample_pdf):
    client = make_client(tmp_path, monkeypatch)
    assert client.get("/health").json()["provider"] == "fake"
    assert client.get("/", follow_redirects=False).headers["location"] == "/docs"

    with open(sample_pdf, "rb") as f:
        r = client.post("/documents", files={"file": ("report.pdf", f, "application/pdf")})
    assert r.status_code == 201
    doc_id = r.json()["doc_id"]
    assert client.get("/documents").json()[0]["doc_id"] == doc_id

    report = client.post(f"/documents/{doc_id}/extract", json={"kpis": ["scope1_emissions"]}).json()
    assert report["kpis"][0]["value"] == 48250

    ans = client.post(f"/documents/{doc_id}/ask", json={"question": "Scope 1 emissions?"}).json()
    assert ans["citations"]


def test_api_rejects_bad_input(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    assert client.post("/documents", files={"file": ("x.txt", b"hi", "text/plain")}).status_code == 415
    assert client.post("/documents/missing/extract", json={}).status_code == 404
    broken = client.post("/documents", files={"file": ("broken.pdf", b"not a pdf", "application/pdf")})
    assert broken.status_code == 422 and "cannot read this PDF" in broken.json()["detail"]


def test_scanned_pdf_without_text_gets_a_clear_message(tmp_path, monkeypatch):
    from reportlab.pdfgen import canvas

    scanned = tmp_path / "scanned.pdf"
    c = canvas.Canvas(str(scanned))
    c.rect(10, 10, 100, 100)  # a drawing, no text layer
    c.showPage()
    c.save()
    client = make_client(tmp_path, monkeypatch)
    r = client.post("/documents", files={"file": ("scanned.pdf", scanned.read_bytes(), "application/pdf")})
    assert r.status_code == 422 and "OCR" in r.json()["detail"]


def test_token_is_enforced_when_configured(tmp_path, monkeypatch, sample_pdf):
    monkeypatch.setenv("ESG_API_TOKEN", "s3cret-token")
    client = make_client(tmp_path, monkeypatch)
    assert client.get("/health").status_code == 200  # health stays public for probes
    assert client.get("/documents").status_code == 401
    assert client.get("/documents", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/documents", headers={"Authorization": "Bearer s3cret-token"})
    assert ok.status_code == 200


def test_evidence_and_saved_results(tmp_path, monkeypatch, sample_pdf):
    import json
    import shutil

    from esg_lens import cli
    from esg_lens.config import get_settings

    # A saved real-report run (fake provider), with its gold values, as the UI reads them.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "reports").mkdir(parents=True)
    (tmp_path / "data" / "gold").mkdir(parents=True)
    shutil.copy(sample_pdf, tmp_path / "data" / "reports" / "northwind-2025.pdf")
    gold = {"pdf": "data/reports/northwind-2025.pdf", "kpi": "scope1_emissions", "value": 48250, "page": 2}
    (tmp_path / "data" / "gold" / "real.jsonl").write_text(json.dumps(gold) + "\n", encoding="utf-8")
    monkeypatch.setenv("ESG_PROVIDER", "fake")
    monkeypatch.setenv("ESG_DATA_DIR", str(tmp_path / ".esg_fake"))
    get_settings.cache_clear()
    assert cli.main(["run", "--all"]) == 0

    client = make_client(tmp_path, monkeypatch)
    listed = client.get("/results").json()
    assert listed == [
        {
            "provider": "fake",
            "report": "northwind-2025",
            "found": 6,
            "passed": 6,
            "indicators": 7,
            "llm_calls": listed[0]["llm_calls"],
            "latency_s": listed[0]["latency_s"],
        }
    ]
    saved = client.get("/results/fake/northwind-2025").json()
    ev = saved["evidence"]["scope1_emissions"]
    assert ev["found"] and "48,250" in ev["match"] and ev["page"] == 2 and ev["before"]
    assert saved["gold"]["scope1_emissions"]["value"] == 48250
    assert client.get("/results/fake/missing").status_code == 404
    assert client.get("/results/../etc").status_code == 404

    with open(sample_pdf, "rb") as f:
        doc_id = client.post("/documents", files={"file": ("r.pdf", f, "application/pdf")}).json()["doc_id"]
    quote = "our Scope 1 emissions"
    live = client.post(f"/documents/{doc_id}/evidence", json={"page": 2, "quote": quote}).json()
    assert live["found"] and live["match"].lower() == quote.lower()
    assert client.post(f"/documents/{doc_id}/evidence", json={"page": 99, "quote": "x"}).status_code == 404
