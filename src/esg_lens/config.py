"""Runtime configuration, read from environment variables (or a .env file).

No secret is ever hard-coded. On Azure the default authentication is keyless (managed identity /
`DefaultAzureCredential`); on Google Cloud it is Application Default Credentials.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["fake", "openai", "azure", "gemini", "anthropic"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ESG_", env_file=".env", extra="ignore")

    # Which model provider to use. "fake" runs fully offline (tests, demos, CI).
    provider: Provider = "fake"

    # Retrieval
    chunk_size: int = 900  # characters
    chunk_overlap: int = 150
    top_k: int = 6
    hybrid_alpha: float = 0.5  # weight of the vector score vs. BM25 (0 = BM25 only, 1 = vector only)

    # OpenAI API (platform.openai.com, key in OPENAI_API_KEY)
    openai_model: str = "gpt-5.4-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    # Azure OpenAI (Microsoft Foundry)
    azure_endpoint: str | None = None  # https://<resource>.openai.azure.com
    azure_chat_deployment: str = "gpt-5.4-mini"
    azure_embedding_deployment: str = "text-embedding-3-small"
    # Empty = the stable Azure OpenAI v1 API (no version to keep up to date). Set a version only if needed.
    azure_api_version: str | None = None
    azure_api_key: str | None = None  # leave empty to use keyless auth (managed identity)

    # Gemini (Vertex AI or Gemini API)
    gemini_model: str = "gemini-3.8-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    gcp_project: str | None = None  # set to use Vertex AI with Application Default Credentials
    # Recent Gemini models are served from "global" (or the "eu" / "us" multi-regions), not single regions;
    # embedding models are regional. Two settings, so each call goes where its model lives.
    gcp_location: str = "global"
    gcp_embedding_location: str = "europe-west1"

    # Anthropic
    anthropic_model: str = "claude-sonnet-5-5"

    # Client-side quotas (0 = no limit). Free tiers count every embedded text as one request: set
    # ESG_EMBED_PER_MINUTE / ESG_REQUESTS_PER_MINUTE just under your plan's limits to never hit a 429.
    requests_per_minute: int = 0
    embed_per_minute: int = 0

    # Storage
    data_dir: str = ".esg_lens"

    # API security. When set, every endpoint except /health requires
    # "Authorization: Bearer <token>". In Azure it is injected from a Container Apps secret,
    # on top of Entra ID authentication at the ingress (see deploy/azure).
    api_token: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
