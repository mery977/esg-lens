"""Claude provider (Anthropic API). Reads ANTHROPIC_API_KEY from the environment.

Anthropic does not offer an embeddings endpoint, so retrieval uses the local hashed embeddings
combined with BM25 (the hybrid index still works well on exact ESG terms). To use dense embeddings,
plug another embedding model into `embed`.
"""

from __future__ import annotations

from ..config import Settings
from .base import LLMProvider
from .fake import FakeProvider


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, settings: Settings):
        super().__init__()
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise ImportError("pip install 'esg-lens[anthropic]'") from e
        self.client = anthropic.Anthropic()
        self.model = settings.anthropic_model
        self._local_embedder = FakeProvider()

    def _complete(self, system: str, user: str) -> str:
        # Current Claude models take no sampling parameters (no temperature): the SDK 2.x rejects them.
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")

    def _embed(self, texts: list[str]) -> list[list[float]]:
        return self._local_embedder._embed(texts)
