"""Verification agent: deterministic checks that catch hallucinations before a value is accepted.

A value is accepted only if its quote really exists in the document, the figure really appears in
the quote, and it is physically plausible. Failures are turned into feedback for a retry.

Text from real PDFs is messy (ligatures, CO₂ subscripts, words hyphenated across lines, curly quotes,
non-breaking spaces), so the quote and the page are compared after the same normalisation. A quote
found on a neighbouring page (a common off-by-one from page breaks) is accepted and the page fixed.
"""

from __future__ import annotations

import re
import unicodedata

from ..numbers import digits_signature, normalise_unit, same_family
from ..schemas import KpiSpec, KpiValue

_TRANSLATE = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u00ad": "",  # soft hyphen
    }
)
_HYPHEN_BREAK = re.compile(r"(\w)-\s*\n\s*(\w)")
_SPACE = re.compile(r"\s+")


def normalise_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_TRANSLATE)
    text = _HYPHEN_BREAK.sub(r"\1\2", text)  # "emis-\nsions" -> "emissions"
    return _SPACE.sub(" ", text).strip().lower()


class VerificationAgent:
    def __init__(self, pages_by_doc: dict[str, list[str]]):
        self.pages_by_doc = pages_by_doc
        self._norm_pages = {d: [normalise_text(p) for p in pages] for d, pages in pages_by_doc.items()}

    def _locate(self, doc_id: str, page: int, quote: str) -> int | None:
        """Page where the quote really is: the cited one first, then its neighbours, then anywhere."""
        pages = self._norm_pages.get(doc_id) or []
        q = normalise_text(quote)
        if not q:
            return None
        order = [page, page - 1, page + 1] + [p for p in range(1, len(pages) + 1) if abs(p - page) > 1]
        for p in order:
            if 1 <= p <= len(pages) and q in pages[p - 1]:
                return p
        return None

    def _lead_in(self, doc_id: str, page: int, quote: str, max_chars: int = 400) -> str:
        """The sentences just before the quote. Reports often name the indicator in the previous sentence
        ("...the proportion of women in management... In 2025, the ratio was 19%/81%.")."""
        pages = self._norm_pages.get(doc_id) or []
        if not 1 <= page <= len(pages):
            return ""
        text, q = pages[page - 1], normalise_text(quote)
        at = text.find(q)
        if at <= 0:
            return ""
        sentences = re.split(r"(?<=[.!?;])\s+", text[max(0, at - max_chars) : at].strip())
        return " ".join(sentences[-2:])  # previous sentence (+ start of the current one if cut mid-way)

    def verify(self, kpi: KpiSpec, result: KpiValue) -> list[str]:
        """Return the list of problems found (empty list = verified). May fix an off-by-one page."""
        if result.value is None:
            return []  # "not disclosed" is a legitimate answer
        problems: list[str] = []
        cit = result.citation
        if cit is None:
            return ["the value has no citation; quote the sentence that contains it"]

        if cit.doc_id not in self.pages_by_doc:
            problems.append(f"unknown document {cit.doc_id}")
        else:
            found = self._locate(cit.doc_id, cit.page, cit.quote)
            if found is None:
                problems.append(f"the quote is not found verbatim on page {cit.page}; copy it exactly")
            elif found != cit.page:
                cit.page = found  # same sentence, wrong page number: fix the citation, not the value

        quote = normalise_text(cit.quote)
        if digits_signature(result.value) not in re.sub(r"[^\d]", "", quote):
            problems.append("the extracted figure does not appear in the quote")

        wrong_basis = [t for t in kpi.avoid_terms if t in quote]
        if wrong_basis:
            problems.append(
                f"the quote reports a {wrong_basis[0]} figure, which is not {kpi.name}; find the right one"
            )

        # The indicator must be named in the quote, or in the sentence right before it (never further away).
        lead_in = self._lead_in(cit.doc_id, cit.page, cit.quote) if cit.doc_id in self.pages_by_doc else ""
        if not any(k in quote or k in lead_in for k in kpi.keywords):
            problems.append(f"the quote does not mention {kpi.name}; it may be another indicator")

        expected_unit = normalise_unit(kpi.unit_hint)
        if result.unit and expected_unit and not same_family(result.unit, expected_unit):
            problems.append(f"unit {result.unit} is unexpected for this indicator (expected {expected_unit})")
        if result.value < 0:
            problems.append("negative value")
        if expected_unit == "%" and result.value > 100:
            problems.append("a percentage cannot exceed 100")
        return problems
