"""Provider factory."""

from __future__ import annotations

from ..config import Settings, get_settings
from .base import LLMProvider


def get_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    return _make(settings).set_quotas(settings.requests_per_minute, settings.embed_per_minute)


def _make(settings: Settings) -> LLMProvider:
    if settings.provider == "fake":
        from .fake import FakeProvider

        return FakeProvider()
    if settings.provider == "openai":
        from .openai_api import OpenAIProvider

        return OpenAIProvider(settings)
    if settings.provider == "azure":
        from .azure_openai import AzureOpenAIProvider

        return AzureOpenAIProvider(settings)
    if settings.provider == "gemini":
        from .gemini import GeminiProvider

        return GeminiProvider(settings)
    if settings.provider == "anthropic":
        from .anthropic import AnthropicProvider

        return AnthropicProvider(settings)
    raise ValueError(f"unknown provider {settings.provider!r}")


__all__ = ["LLMProvider", "get_provider"]
