"""Evidence: the cited sentence located in its page, with a little context around it.

Used by the UI to show *why* a value can be trusted, and saved next to the results of real reports
so that they can be reviewed without the PDF. Only a short excerpt is kept (never the full page):
the reports belong to the companies.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"\w+", re.UNICODE)


def _quote_pattern(quote: str) -> re.Pattern | None:
    """Tolerant regex for the quote: any whitespace, hyphenation or punctuation run between its words."""
    words = _WORD.findall(quote)
    if not words:
        return None
    sep = r"[\W_]*?(?:-\s*)?\s*"
    return re.compile(sep.join(re.escape(w) for w in words), re.IGNORECASE | re.UNICODE)


def locate(page_text: str, quote: str, width: int = 280) -> dict:
    """Return {"found", "before", "match", "after"}: the quote as it is printed on the page, with context."""
    pattern = _quote_pattern(quote)
    m = pattern.search(page_text) if pattern else None
    if not m:
        return {"found": False, "before": "", "match": quote, "after": ""}
    start, end = m.span()
    before = page_text[max(0, start - width) : start]
    after = page_text[end : end + width]
    clean = lambda s: re.sub(r"\s+", " ", s)  # noqa: E731
    return {
        "found": True,
        "before": ("…" if start > width else "") + clean(before).lstrip(),
        "match": clean(m.group(0)),
        "after": clean(after).rstrip() + ("…" if end + width < len(page_text) else ""),
    }


def evidence_for(report, pages_by_doc: dict[str, list[str]], width: int = 280) -> dict[str, dict]:
    """Evidence for every cited value of an ExtractionReport, keyed by indicator."""
    out = {}
    for k in report.kpis:
        cit = k.citation
        pages = pages_by_doc.get(cit.doc_id) if cit else None
        if cit and pages and 1 <= cit.page <= len(pages):
            out[k.kpi] = {"page": cit.page, **locate(pages[cit.page - 1], cit.quote, width)}
    return out
