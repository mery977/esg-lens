"""Gemini provider, through Vertex AI (Application Default Credentials) or the Gemini API.

Set ESG_GCP_PROJECT to use Vertex AI (recommended on Cloud Run: the service account authenticates,
no key needed). Otherwise the SDK reads GOOGLE_API_KEY / GEMINI_API_KEY.
"""

from __future__ import annotations

from ..config import Settings
from .base import LLMProvider


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, settings: Settings):
        super().__init__()
        try:
            from google import genai
            from google.genai import types
        except ImportError as e:  # pragma: no cover
            raise ImportError("pip install 'esg-lens[gemini]'") from e
        self._types = types
        if settings.gcp_project:
            self.client = genai.Client(
                vertexai=True, project=settings.gcp_project, location=settings.gcp_location
            )
            self.embed_client = (
                self.client
                if settings.gcp_embedding_location == settings.gcp_location
                else genai.Client(
                    vertexai=True, project=settings.gcp_project, location=settings.gcp_embedding_location
                )
            )
        else:
            self.client = genai.Client()
            self.embed_client = self.client
        self.model = settings.gemini_model
        self.embedding_model = settings.gemini_embedding_model

    def _complete(self, system: str, user: str) -> str:
        resp = self.client.models.generate_content(
            model=self.model,
            contents=user,
            config=self._types.GenerateContentConfig(
                # Gemini 3 models are tuned for their default temperature; JSON mode keeps output strict.
                system_instruction=system,
                response_mime_type="application/json",
            ),
        )
        return resp.text or ""

    def _embed(self, texts: list[str]) -> list[list[float]]:
        resp = self.embed_client.models.embed_content(model=self.embedding_model, contents=texts)
        return [e.values for e in resp.embeddings]
