"""Deterministic offline provider.

It lets the whole system (API, UI, agents, evaluation, CI) run without any API key or network.
It is NOT a model: extraction is a keyword + number heuristic and embeddings are hashed bags of words.
Use it to test the plumbing, then switch ESG_PROVIDER to azure, gemini or anthropic for real results.
"""

from __future__ import annotations

import hashlib
import json
import re

import numpy as np

from ..numbers import NUMBER_WITH_UNIT
from .base import LLMProvider

_DIM = 256
_TOKEN = re.compile(r"[a-z0-9]+")
_YEAR = re.compile(r"\b(20[0-4]\d)\b")


class FakeProvider(LLMProvider):
    name = "fake"

    def _embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            v = np.zeros(_DIM, dtype=np.float32)
            for tok in _TOKEN.findall(text.lower()):
                v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % _DIM] += 1.0
            out.append(v.tolist())
        return out

    def _complete(self, system: str, user: str) -> str:
        try:
            payload = json.loads(user)
        except json.JSONDecodeError:
            return json.dumps({})
        task = payload.get("task")
        if task == "extract_kpi":
            return json.dumps(self._extract(payload))
        if task == "expand_queries":
            return json.dumps({"queries": []})
        if task == "answer":
            return json.dumps(self._answer(payload))
        return json.dumps({})

    @staticmethod
    def _extract(p: dict) -> dict:
        keywords = [k.lower() for k in p.get("keywords", [])]
        # The heuristic ignores avoid_terms on a first attempt (it is not a model), but applies them once the
        # verification agent sends feedback: this exercises the self-correction loop end to end.
        avoid = [t.lower() for t in p.get("avoid_terms", [])] if p.get("feedback") else []
        for passage in p.get("passages", []):
            text = passage["text"]
            for sentence in re.split(r"(?<=[.;])\s+|\n+", text):
                sentence = sentence.strip()
                low = sentence.lower()
                if not sentence or any(t in low for t in avoid):
                    continue
                kw = next((k for k in keywords if k in low), None)
                if kw is None:
                    continue
                after = sentence[low.index(kw) + len(kw) :]
                m = NUMBER_WITH_UNIT.search(after)
                if not m:
                    continue
                year = _YEAR.search(sentence)
                return {
                    "value_text": m.group(0).strip(),
                    "year": int(year.group(1)) if year else None,
                    "doc_id": passage["doc_id"],
                    "page": passage["page"],
                    "quote": sentence.strip(),
                    "confidence": 0.6,
                }
        return {"value_text": None, "quote": None, "confidence": 0.0}

    @staticmethod
    def _answer(p: dict) -> dict:
        passages = p.get("passages", [])
        if not passages:
            return {"answer": "The report does not contain this information.", "sources": []}
        top = passages[0]
        first = re.split(r"(?<=[.])\s+|\n+", top["text"].strip())[0]
        return {"answer": first, "sources": [{"doc_id": top["doc_id"], "page": top["page"], "quote": first}]}
