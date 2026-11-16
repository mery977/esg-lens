"""Retrieval agent: plans search queries for an indicator and gathers the best passages."""

from __future__ import annotations

import json

from ..index import HybridIndex
from ..llm.base import LLMProvider
from ..schemas import Chunk, KpiSpec
from . import prompts


class RetrievalAgent:
    def __init__(
        self, llm: LLMProvider, index: HybridIndex, top_k: int = 6, alpha: float = 0.5, expand: bool = True
    ):
        self.llm, self.index, self.top_k, self.alpha, self.expand = llm, index, top_k, alpha, expand

    def plan(self, kpi: KpiSpec) -> list[str]:
        queries = list(kpi.queries)
        if self.expand:
            payload = {"task": "expand_queries", "indicator": kpi.name, "known_queries": kpi.queries}
            try:
                extra = self.llm.complete_json(prompts.EXPAND_QUERIES, json.dumps(payload)).get("queries", [])
                queries += [q for q in extra if isinstance(q, str)][:3]
            except (ValueError, json.JSONDecodeError):
                pass  # planning is an optimisation, never a point of failure
        return queries

    def retrieve(self, kpi: KpiSpec) -> list[Chunk]:
        """Fuse results of all queries, boosting passages that contain the indicator keywords."""
        scores: dict[str, float] = {}
        by_id: dict[str, Chunk] = {}
        for query in self.plan(kpi):
            for chunk, score in self.index.search(query, self.llm, k=self.top_k, alpha=self.alpha):
                by_id[chunk.chunk_id] = chunk
                scores[chunk.chunk_id] = max(scores.get(chunk.chunk_id, 0.0), score)
        for cid, chunk in by_id.items():
            if any(k in chunk.text.lower() for k in kpi.keywords):
                scores[cid] += 0.5
        ranked = sorted(by_id, key=lambda cid: -scores[cid])[: self.top_k]
        return [by_id[cid] for cid in ranked]
