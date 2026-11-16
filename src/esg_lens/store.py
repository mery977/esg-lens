"""Document store: keeps each ingested report's pages and hybrid index on disk.

Local folder by default. In the cloud, mount Azure Files / a Cloud Storage FUSE volume on ESG_DATA_DIR,
or swap this class for Blob Storage / Cloud Storage without touching the agents.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import Settings
from .index import HybridIndex
from .ingest import ingest_pdf
from .llm.base import LLMProvider


class DocumentStore:
    def __init__(self, settings: Settings, llm: LLMProvider):
        self.root = Path(settings.data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings, self.llm = settings, llm

    def _folder(self, doc_id: str) -> Path:
        return self.root / doc_id

    def add_pdf(self, path: str | Path) -> str:
        try:
            doc_id, pages, chunks = ingest_pdf(path, self.settings.chunk_size, self.settings.chunk_overlap)
        except Exception as e:
            raise ValueError(f"cannot read this PDF ({type(e).__name__}): {e}") from e
        if not chunks:
            raise ValueError("no extractable text in this PDF (scanned document? run OCR on it first)")
        folder = self._folder(doc_id)
        if (folder / "pages.json").exists():
            return doc_id  # already indexed (idempotent)
        HybridIndex.build(chunks, self.llm, progress=folder / "partial_embeddings.npy").save(folder)
        (folder / "pages.json").write_text(encoding="utf-8", data=json.dumps(pages))
        (folder / "meta.json").write_text(
            encoding="utf-8",
            data=json.dumps(
                {
                    "doc_id": doc_id,
                    "source": Path(path).name,
                    "pages": len(pages),
                    "chunks": len(chunks),
                    "embedded_with": self.llm.name,
                }
            ),
        )
        return doc_id

    def list(self) -> list[dict]:
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.root.glob("*/meta.json"))]

    def load(self, doc_id: str) -> tuple[HybridIndex, list[str]]:
        folder = self._folder(doc_id)
        if not (folder / "pages.json").exists():
            raise KeyError(doc_id)
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        if meta.get("embedded_with") != self.llm.name:
            raise RuntimeError(
                f"{doc_id} was indexed with the '{meta.get('embedded_with')}' provider; embeddings are not "
                f"comparable with '{self.llm.name}'. Re-ingest it, or use another ESG_DATA_DIR per provider."
            )
        return HybridIndex.load(folder), json.loads((folder / "pages.json").read_text(encoding="utf-8"))
