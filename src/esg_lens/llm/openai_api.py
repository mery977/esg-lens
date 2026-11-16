"""OpenAI API provider (platform.openai.com). Reads OPENAI_API_KEY from the environment.

The chat and embedding logic is shared with Azure OpenAI, which serves the same models through the
same client: only the endpoint and the authentication differ (see azure_openai.py).
"""

from __future__ import annotations

from ..config import Settings
from .base import LLMProvider, rejects_temperature


class OpenAICompatibleProvider(LLMProvider):
    """Chat completions in JSON mode + embeddings, for any OpenAI-compatible endpoint."""

    client: object
    chat_model: str
    embedding_model: str
    _send_temperature: bool = True  # switched off automatically for reasoning models (GPT-5 family)

    def _complete(self, system: str, user: str) -> str:
        kwargs = {
            "model": self.chat_model,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self._send_temperature:
            try:
                resp = self.client.chat.completions.create(temperature=0, **kwargs)
                return resp.choices[0].message.content or ""
            except Exception as e:
                if not rejects_temperature(e):
                    raise
                self._send_temperature = False
        resp = self.client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    def _embed(self, texts: list[str]) -> list[list[float]]:
        resp = self.client.embeddings.create(model=self.embedding_model, input=texts)
        return [d.embedding for d in resp.data]


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"

    def __init__(self, settings: Settings):
        super().__init__()
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover
            raise ImportError("pip install 'esg-lens[openai]'") from e
        self.client = OpenAI(max_retries=0)  # OPENAI_API_KEY; retries are handled in LLMProvider
        self.chat_model = settings.openai_model
        self.embedding_model = settings.openai_embedding_model
