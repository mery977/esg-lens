"""Question-answering agent (RAG): answers free questions about a report, always with sources."""

from __future__ import annotations

import json

from ..index import HybridIndex
from ..llm.base import LLMProvider
from ..schemas import Answer, Citation
from . import prompts


class QAAgent:
    def __init__(self, llm: LLMProvider, index: HybridIndex, top_k: int = 6, alpha: float = 0.5):
        self.llm, self.index, self.top_k, self.alpha = llm, index, top_k, alpha

    def ask(self, question: str) -> Answer:
        hits = self.index.search(question, self.llm, k=self.top_k, alpha=self.alpha)
        payload = {
            "task": "answer",
            "question": question,
            "passages": [{"doc_id": c.doc_id, "page": c.page, "text": c.text} for c, _ in hits],
        }
        try:
            out = self.llm.complete_json(prompts.ANSWER, json.dumps(payload))
        except (ValueError, json.JSONDecodeError):
            return Answer(question=question, answer="The model returned an invalid answer.", citations=[])
        citations = [
            Citation(
                doc_id=str(s.get("doc_id", "")), page=int(s.get("page", 0)), quote=str(s.get("quote", ""))
            )
            for s in out.get("sources", [])
            if isinstance(s, dict)
        ]
        return Answer(question=question, answer=str(out.get("answer", "")), citations=citations)
