"""PDF ingestion: extract text page by page and split it into overlapping, page-aware chunks."""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from pypdf import PdfReader

from .schemas import Chunk

# pypdf logs a warning per odd font: noise for a user, the text is extracted anyway
logging.getLogger("pypdf").setLevel(logging.ERROR)

_WS = re.compile(r"[ \t]+")


def doc_id_for(path: str | Path) -> str:
    """Stable id from the file content, so re-ingesting the same report is idempotent."""
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
    return f"{Path(path).stem}-{digest}"


def read_pages(path: str | Path) -> list[str]:
    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        text = "\n".join(_WS.sub(" ", line).strip() for line in text.splitlines())
        pages.append(text.strip())
    return pages


def chunk_pages(doc_id: str, pages: list[str], size: int = 900, overlap: int = 150) -> list[Chunk]:
    """Split each page separately so every chunk keeps an exact page number for citations."""
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")
    chunks: list[Chunk] = []
    for page_no, text in enumerate(pages, start=1):
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(len(text), start + size)
            # Prefer to cut on a sentence or line boundary.
            if end < len(text):
                cut = max(text.rfind(". ", start, end), text.rfind("\n", start, end))
                if cut > start + size // 2:
                    end = cut + 1
            piece = text[start:end].strip()
            if piece:
                chunks.append(
                    Chunk(
                        chunk_id=f"{doc_id}:p{page_no}:{len(chunks)}", doc_id=doc_id, page=page_no, text=piece
                    )
                )
            if end >= len(text):
                break
            start = end - overlap
    return chunks


def ingest_pdf(path: str | Path, size: int = 900, overlap: int = 150) -> tuple[str, list[str], list[Chunk]]:
    doc_id = doc_id_for(path)
    pages = read_pages(path)
    return doc_id, pages, chunk_pages(doc_id, pages, size, overlap)
