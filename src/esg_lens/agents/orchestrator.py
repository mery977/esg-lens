"""Orchestrator: coordinates the retrieval, extraction and verification agents for every indicator.

For each indicator:  plan queries -> retrieve passages -> extract -> verify
                     -> if verification fails, retry extraction once with the problems as feedback
                     -> accept (verified) or keep the value flagged as unverified.
Indicators are processed in parallel threads; each one is independent.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from ..index import HybridIndex
from ..kpis import KPI_CATALOGUE
from ..llm.base import LLMProvider
from ..numbers import normalise_unit, to_canonical
from ..schemas import ExtractionReport, KpiSpec, KpiValue
from .extraction import ExtractionAgent
from .retrieval import RetrievalAgent
from .verification import VerificationAgent

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        llm: LLMProvider,
        index: HybridIndex,
        pages_by_doc: dict[str, list[str]],
        top_k: int = 6,
        alpha: float = 0.5,
        max_retries: int = 1,
        workers: int = 4,
    ):
        self.llm = llm
        self.retriever = RetrievalAgent(llm, index, top_k=top_k, alpha=alpha)
        self.extractor = ExtractionAgent(llm)
        self.verifier = VerificationAgent(pages_by_doc)
        self.max_retries = max_retries
        self.workers = workers

    def run_one(self, kpi: KpiSpec) -> KpiValue:
        """One indicator. A provider failure on one indicator is reported on it, not fatal to the report."""
        try:
            return self._run_one(kpi)
        except Exception as e:  # noqa: BLE001 - surfaced to the user in verification_notes
            logger.warning("indicator %s failed: %s", kpi.key, e)
            return KpiValue(
                kpi=kpi.key, verification_notes=[f"extraction failed: {type(e).__name__}: {e}"[:300]]
            )

    def _run_one(self, kpi: KpiSpec) -> KpiValue:
        passages = self.retriever.retrieve(kpi)
        if not passages:
            return KpiValue(kpi=kpi.key, verification_notes=["no relevant passage found"])

        result = self.extractor.extract(kpi, passages)
        problems = self.verifier.verify(kpi, result)
        first_problems = list(problems)
        attempts = 1
        while problems and attempts <= self.max_retries:
            attempts += 1
            retry = self.extractor.extract(kpi, passages, feedback=problems)
            retry_problems = self.verifier.verify(kpi, retry)
            if len(retry_problems) <= len(problems):
                result, problems = retry, retry_problems

        result.attempts = attempts
        result.corrected_issues = [p for p in first_problems if p not in problems]
        result.verified = result.value is not None and not problems
        result.verification_notes = problems
        if not result.verified:
            result.confidence = min(result.confidence, 0.3)
        if result.value is not None:  # one unit per indicator: ktCO2e -> tCO2e, GWh/TJ -> MWh, ML -> m3
            value, unit = to_canonical(result.value, result.unit, normalise_unit(kpi.unit_hint))
            result.value, result.unit = round(value, 6), unit
        return result

    def run(self, doc_id: str, kpis: list[KpiSpec] | None = None) -> ExtractionReport:
        kpis = kpis or KPI_CATALOGUE
        calls_before = self.llm.usage.calls
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            results = list(pool.map(self.run_one, kpis))
        return ExtractionReport(
            doc_id=doc_id,
            provider=self.llm.name,
            kpis=results,
            latency_s=round(time.perf_counter() - start, 3),
            llm_calls=self.llm.usage.calls - calls_before,
        )
