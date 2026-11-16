"""Hybrid retrieval: BM25 (exact terms such as "Scope 1") fused with dense vectors (meaning).

Kept local and dependency-light on purpose. In production the same interface maps to
Azure AI Search (hybrid + semantic ranker) or Vertex AI Search; see docs/architecture.md.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from .llm.base import LLMProvider
from .schemas import Chunk

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _minmax(x: np.ndarray) -> np.ndarray:
    if x.size == 0:
        return x
    lo, hi = float(x.min()), float(x.max())
    return np.zeros_like(x) if hi - lo < 1e-12 else (x - lo) / (hi - lo)


class HybridIndex:
    def __init__(self, chunks: list[Chunk], embeddings: np.ndarray):
        if len(chunks) != len(embeddings):
            raise ValueError("one embedding per chunk is required")
        self.chunks = chunks
        self.embeddings = embeddings / (np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-12)
        self._bm25 = BM25Okapi([tokenize(c.text) for c in chunks]) if chunks else None

    @classmethod
    def build(
        cls, chunks: list[Chunk], llm: LLMProvider, batch: int = 64, progress: Path | None = None
    ) -> HybridIndex:
        """Embed every chunk. With `progress`, vectors are saved after each batch, so a run stopped by a
        quota error resumes where it stopped instead of paying for the same embeddings again."""
        if llm.embed_limiter.per_minute > 0:  # one full-quota batch per minute, at most 100 (API maximum)
            batch = min(100, llm.embed_limiter.per_minute)
        vectors: list[list[float]] = []
        if progress and progress.exists():
            vectors = np.load(progress).tolist()[: len(chunks)]
        for i in range(len(vectors), len(chunks), batch):
            vectors.extend(llm.embed([c.text for c in chunks[i : i + batch]]))
            if progress:
                progress.parent.mkdir(parents=True, exist_ok=True)
                np.save(progress, np.asarray(vectors, dtype=np.float32))
            if len(chunks) > batch:
                print(f"  embedded {len(vectors)}/{len(chunks)} passages", file=sys.stderr, flush=True)
        if progress and progress.exists():
            progress.unlink()
        return cls(chunks, np.asarray(vectors, dtype=np.float32).reshape(len(chunks), -1))

    def search(
        self, query: str, llm: LLMProvider, k: int = 6, alpha: float = 0.5
    ) -> list[tuple[Chunk, float]]:
        if not self.chunks:
            return []
        bm25 = _minmax(np.asarray(self._bm25.get_scores(tokenize(query)), dtype=np.float32))
        q = np.asarray(llm.embed([query])[0], dtype=np.float32)
        q = q / (np.linalg.norm(q) + 1e-12)
        dense = _minmax(self.embeddings @ q)
        score = alpha * dense + (1 - alpha) * bm25
        order = np.argsort(-score)[:k]
        return [(self.chunks[i], float(score[i])) for i in order]

    # Persistence -------------------------------------------------------------
    def save(self, folder: str | Path) -> None:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "chunks.json").write_text(
            encoding="utf-8", data=json.dumps([c.model_dump() for c in self.chunks])
        )
        np.save(folder / "embeddings.npy", self.embeddings)

    @classmethod
    def load(cls, folder: str | Path) -> HybridIndex:
        folder = Path(folder)
        chunks = [Chunk(**c) for c in json.loads((folder / "chunks.json").read_text(encoding="utf-8"))]
        return cls(chunks, np.load(folder / "embeddings.npy"))
