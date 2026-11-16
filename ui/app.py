"""Streamlit front-end. Talks to the API (ESG_API_URL, default http://localhost:8000).

Run:  uvicorn esg_lens.api:app   then   streamlit run ui/app.py

Tabs
- Real reports:   saved runs on public CSRD reports (results/), viewable without any API key
- Analyse:        upload a PDF and run the agents live
- Ask:            questions answered with citations
- Compare:        the same report across model providers
Every figure comes with its evidence: the cited sentence highlighted on its page.
"""

from __future__ import annotations

import csv
import html
import io
import json
import os

import requests
import streamlit as st

API = os.environ.get("ESG_API_URL", "http://localhost:8000")
TOKEN = os.environ.get("ESG_API_TOKEN")
HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

LABELS = {
    "scope1_emissions": "Scope 1 emissions",
    "scope2_emissions": "Scope 2 emissions (market-based)",
    "scope3_emissions": "Scope 3 emissions",
    "energy_consumption": "Energy consumption",
    "renewable_share": "Renewable energy share",
    "water_withdrawal": "Water withdrawal",
    "women_in_management": "Women in management",
}
STATUS = {  # label, badge style, expander icon
    "passed": ("Verified", "ok", ":material/verified:"),
    "not disclosed": ("Not disclosed", "muted", ":material/remove_circle_outline:"),
    "FAILED": ("Rejected by checks", "bad", ":material/error:"),
    "ERROR": ("Error", "bad", ":material/warning:"),
}

st.set_page_config(page_title="ESG Lens", page_icon="🌿", layout="wide")
st.markdown(
    """
<style>
.block-container {padding-top: 2rem; max-width: 1200px;}
.badge {display:inline-block; padding:2px 10px; border-radius:999px; font-size:0.8rem; font-weight:600;}
.badge.ok {background:#e6f4ea; color:#137333;}
.badge.bad {background:#fce8e6; color:#c5221f;}
.badge.muted {background:#eef0f3; color:#5f6368;}
.badge.fix {background:#fef7e0; color:#8a5a00;}
.evidence {font-family: Georgia, serif; font-size: 0.95rem; line-height: 1.6; padding: 12px 16px;
           border-left: 3px solid #137333; background: rgba(127,127,127,0.06); border-radius: 4px;}
.evidence mark {background: #fff2a8; color: #202124; padding: 1px 2px; border-radius: 2px;}
.kpi-value {font-size: 1.6rem; font-weight: 700; line-height: 1.2;}
.kpi-meta {color: #6b7280; font-size: 0.85rem;}
@media (prefers-color-scheme: dark) {
  .badge.ok {background:#1e3a2a; color:#81c995;} .badge.bad {background:#3c1f1d; color:#f28b82;}
  .badge.muted {background:#2d2f33; color:#bdc1c6;} .badge.fix {background:#3a3112; color:#fdd663;}
  .evidence mark {background:#5c4d00; color:#fff;}
}
</style>
""",
    unsafe_allow_html=True,
)


# --- API ------------------------------------------------------------------------------------------


def api_get(path: str, timeout: int = 15):
    r = requests.get(f"{API}{path}", headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def api_post(path: str, timeout: int = 900, **kwargs):
    r = requests.post(f"{API}{path}", headers=HEADERS, timeout=timeout, **kwargs)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise RuntimeError(f"{r.status_code}: {detail}")
    return r.json()


# --- Rendering ------------------------------------------------------------------------------------


def status_of(k: dict) -> str:
    if k["verified"]:
        return "passed"
    if any(n.startswith(("extraction failed", "invalid model output")) for n in k["verification_notes"]):
        return "ERROR"
    return "not disclosed" if k["value"] is None else "FAILED"


def badge(text: str, kind: str) -> str:
    return f'<span class="badge {kind}">{html.escape(text)}</span>'


def fmt_value(k: dict) -> str:
    v = k["value"]
    if v is None:
        return "not disclosed"
    text = f"{v:,.0f}" if abs(v) >= 100 or float(v).is_integer() else f"{v:,.2f}"
    return f"{text} {k['unit'] or ''}".strip()


def gold_verdict(k: dict, gold: dict) -> str | None:
    g = gold.get(k["kpi"])
    if not g:
        return None
    if g.get("value") is None:
        return "correct" if k["value"] is None else "wrong"
    if k["value"] is None:
        return "missed"
    tol = g.get("tolerance_pct", 1.0) / 100
    return "correct" if abs(k["value"] - g["value"]) <= tol * abs(g["value"]) else "wrong"


def evidence_html(ev: dict | None, quote: str | None) -> str:
    if ev and ev.get("found"):
        return (
            f'<div class="evidence">{html.escape(ev["before"])}<mark>{html.escape(ev["match"])}</mark>'
            f"{html.escape(ev['after'])}</div>"
        )
    if quote:
        return f'<div class="evidence"><mark>{html.escape(quote)}</mark></div>'
    return ""


def to_csv(report: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["indicator", "value", "unit", "year", "status", "page", "quote", "self_corrected", "notes"])
    for k in report["kpis"]:
        c = k.get("citation") or {}
        w.writerow(
            [
                k["kpi"],
                k["value"],
                k["unit"],
                k["year"],
                status_of(k),
                c.get("page"),
                c.get("quote"),
                "; ".join(k.get("corrected_issues", [])),
                "; ".join(k["verification_notes"]),
            ]
        )
    return buf.getvalue()


def render_report(report: dict, evidence: dict, gold: dict | None = None, key: str = "r") -> None:
    kpis = report["kpis"]
    statuses = [status_of(k) for k in kpis]
    verdicts = [gold_verdict(k, gold or {}) for k in kpis]
    checked = [v for v in verdicts if v]

    cols = st.columns(5 if checked else 4)
    cols[0].metric("Indicators found", f"{sum(k['value'] is not None for k in kpis)} / {len(kpis)}")
    cols[1].metric("Verified", f"{statuses.count('passed')}")
    cols[2].metric("Self-corrected", f"{sum(bool(k.get('corrected_issues')) for k in kpis)}")
    secs = report["latency_s"]
    cols[3].metric("Model calls · time", f"{report['llm_calls']} · {'< 1' if secs < 1 else f'{secs:.0f}'} s")
    if checked:
        cols[4].metric(
            "Correct vs human check",
            f"{checked.count('correct')} / {len(checked)}",
            help="Compared with values read by hand in the report (data/gold).",
        )

    st.caption(
        f"Provider **{report['provider']}**. *Verified* = the quote exists verbatim on the cited page, the "
        "figure is in it, unit and range are plausible. Nothing is reported without a source."
    )

    for k, status, verdict in zip(kpis, statuses, verdicts, strict=True):
        label, kind, icon = STATUS[status]
        name = LABELS.get(k["kpi"], k["kpi"])
        cit = k.get("citation") or {}
        head = f"{name} — {fmt_value(k)}"
        with st.expander(head, expanded=False, icon=icon):
            left, right = st.columns([1, 2.4])
            with left:
                st.markdown(
                    f'<div class="kpi-value">{html.escape(fmt_value(k))}</div>', unsafe_allow_html=True
                )
                meta = []
                if k["year"]:
                    meta.append(f"year {k['year']}")
                if cit.get("page"):
                    meta.append(f"page {cit['page']}")
                st.markdown(f'<div class="kpi-meta">{" · ".join(meta)}</div>', unsafe_allow_html=True)
                tags = [badge(label, kind)]
                if k.get("corrected_issues"):
                    tags.append(badge(f"self-corrected ({k['attempts']} attempts)", "fix"))
                if verdict:
                    tags.append(badge(f"human check: {verdict}", "ok" if verdict == "correct" else "bad"))
                st.markdown(" ".join(tags), unsafe_allow_html=True)
            with right:
                if k["value"] is None:
                    st.markdown(
                        "The agents found no figure for this indicator that passes the checks: reported as "
                        "**not disclosed** rather than guessed."
                    )
                else:
                    st.markdown("**Evidence**")
                    st.markdown(
                        evidence_html(evidence.get(k["kpi"]), cit.get("quote")), unsafe_allow_html=True
                    )
                if k.get("corrected_issues"):
                    st.markdown("**First answer rejected by the verification agent, then fixed:**")
                    for issue in k["corrected_issues"]:
                        st.markdown(f"- {issue}")
                if k["verification_notes"]:
                    st.markdown("**Checks that still fail:**")
                    for note in k["verification_notes"]:
                        st.markdown(f"- {note}")

    c1, c2, _ = st.columns([1, 1, 4])
    c1.download_button("Download CSV", to_csv(report), f"{key}.csv", "text/csv", key=f"{key}-csv")
    c2.download_button(
        "Download JSON", json.dumps(report, indent=2), f"{key}.json", "application/json", key=f"{key}-json"
    )


# --- Page -----------------------------------------------------------------------------------------

st.title("ESG Lens")
st.markdown(
    "Multi-agent extraction of ESG indicators from sustainability reports. "
    "**Every figure is cited, checked, and traceable to its page; what cannot be verified is not reported.**"
)

try:
    health = requests.get(f"{API}/health", timeout=5).json()
except requests.RequestException:
    st.error(f"The API is not reachable at {API}. Start it with `uvicorn esg_lens.api:app`.")
    st.stop()

with st.sidebar:
    st.subheader("Model for live analysis")
    names = {"gemini": "Gemini", "anthropic": "Claude", "openai": "OpenAI", "azure": "Azure OpenAI"}
    if health["provider"] == "fake":
        st.markdown("**Offline demo** (no model, no API key)")
        st.caption(
            "Used only by *Analyse* and *Ask*. The *Real reports* tab shows runs made with real models. "
            "To analyse with a model: `scripts/ui.ps1 -Provider gemini` (or anthropic, openai)."
        )
    else:
        st.markdown(f"**{names.get(health['provider'], health['provider'])}**")
    st.caption(f"API v{health.get('version', '?')}")
    st.divider()
    st.subheader("Add a report")
    uploaded = st.file_uploader("Sustainability report (PDF)", type=["pdf"])
    if uploaded and st.button("Index report", type="primary"):
        with st.spinner("Reading, chunking and embedding the report..."):
            try:
                res = api_post(
                    "/documents", files={"file": (uploaded.name, uploaded.getvalue())}, timeout=1800
                )
                st.success("Indexed. Open the *Analyse* tab.")
                st.session_state["doc_id"] = res["doc_id"]
            except (RuntimeError, requests.RequestException) as e:
                st.error(str(e))
    st.divider()
    st.caption("Agents: retrieval (hybrid BM25 + vectors) → extraction → deterministic verification → retry.")

tab_real, tab_live, tab_ask, tab_compare = st.tabs(["Real reports", "Analyse", "Ask", "Compare providers"])

saved = api_get("/results")

with tab_real:
    if not saved:
        st.info(
            "No saved run yet. Run `esg-lens fetch` then `esg-lens run --all` with a provider "
            "(or `scripts/run-real.ps1`): results appear here."
        )
    else:
        reports = sorted({s["report"] for s in saved})
        c1, c2 = st.columns(2)
        report_name = c1.selectbox("Report", reports, key="real-report")
        providers = [s["provider"] for s in saved if s["report"] == report_name]
        provider = c2.selectbox("Provider", providers, key="real-provider")
        data = api_get(f"/results/{provider}/{report_name}")
        render_report(data["report"], data["evidence"], data["gold"], key=f"{provider}-{report_name}")

with tab_live:
    docs = api_get("/documents")
    if not docs:
        st.info("Upload a report in the sidebar, or run `esg-lens demo` to index the fictional sample.")
    else:
        labels = {f"{d['source']} ({d['pages']} pages)": d["doc_id"] for d in docs}
        ids = list(labels.values())
        default = ids.index(st.session_state["doc_id"]) if st.session_state.get("doc_id") in ids else 0
        doc_id = labels[st.selectbox("Report", list(labels), index=default, key="live-doc")]
        if st.button("Run the agents", type="primary"):
            with st.spinner("Planning queries, retrieving passages, extracting, verifying..."):
                try:
                    st.session_state["live"] = (doc_id, api_post(f"/documents/{doc_id}/extract", json={}))
                except (RuntimeError, requests.RequestException) as e:
                    st.error(str(e))
        if st.session_state.get("live", (None,))[0] == doc_id:
            report = st.session_state["live"][1]
            evidence = {}
            for k in report["kpis"]:
                c = k.get("citation")
                if c:
                    try:
                        evidence[k["kpi"]] = api_post(
                            f"/documents/{doc_id}/evidence",
                            json={"page": c["page"], "quote": c["quote"]},
                            timeout=30,
                        )
                    except (RuntimeError, requests.RequestException):
                        pass
            render_report(report, evidence, key=f"live-{doc_id}")

with tab_ask:
    docs = api_get("/documents")
    if not docs:
        st.info("Index a report first.")
    else:
        labels = {f"{d['source']} ({d['pages']} pages)": d["doc_id"] for d in docs}
        doc_id = labels[st.selectbox("Report", list(labels), key="ask-doc")]
        question = st.text_input("Question", placeholder="What are the company's 2030 climate targets?")
        if question:
            with st.spinner("Searching the report..."):
                try:
                    ans = api_post(f"/documents/{doc_id}/ask", json={"question": question}, timeout=300)
                except (RuntimeError, requests.RequestException) as e:
                    st.error(str(e))
                    ans = None
            if ans:
                st.markdown(ans["answer"])
                for c in ans["citations"]:
                    st.markdown(
                        f'<div class="kpi-meta">Page {c["page"]}</div>{evidence_html(None, c["quote"])}',
                        unsafe_allow_html=True,
                    )

with tab_compare:
    multi = {r for r in {s["report"] for s in saved} if sum(s["report"] == r for s in saved) > 1}
    if not saved:
        st.info("Saved runs appear here, side by side, once you have run real reports with providers.")
    else:
        report_name = st.selectbox("Report", sorted({s["report"] for s in saved}), key="cmp-report")
        runs = {
            s["provider"]: api_get(f"/results/{s['provider']}/{report_name}")
            for s in saved
            if s["report"] == report_name
        }
        gold = next(iter(runs.values()))["gold"] if runs else {}
        rows = []
        for key, name in LABELS.items():
            row = {"Indicator": name}
            if gold:
                g = gold.get(key)
                row["Human check"] = (
                    "—" if not g else ("not disclosed" if g["value"] is None else f"{g['value']:,}")
                )
            for prov, data in runs.items():
                k = next((x for x in data["report"]["kpis"] if x["kpi"] == key), None)
                if k:
                    mark = {"correct": " ✓", "wrong": " ✗", "missed": " ✗"}.get(
                        gold_verdict(k, gold) or "", ""
                    )
                    row[prov] = (fmt_value(k) if k["value"] is not None else "not disclosed") + mark
            rows.append(row)
        st.dataframe(rows, hide_index=True, width="stretch")
        summary = [
            {
                "Provider": s["provider"],
                "Found": f"{s['found']}/{s['indicators']}",
                "Verified": s["passed"],
                "Model calls": s["llm_calls"],
                "Time (s)": round(s["latency_s"]),
            }
            for s in saved
            if s["report"] == report_name
        ]
        st.dataframe(summary, hide_index=True, width="stretch")
        if report_name not in multi:
            st.caption("Only one provider has run this report so far: run it with another one to compare.")
