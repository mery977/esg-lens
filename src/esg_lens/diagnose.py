"""`esg-lens check`: one tiny generation call and one embedding call, before indexing whole reports,
with a plain-language diagnosis of the usual setup mistakes (bad key, wrong model name, quota)."""

from __future__ import annotations

from .config import Settings
from .llm import get_provider


def explain(provider: str, exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status == 400 and "<html" in low:
        hint = "the API key is probably malformed (incomplete paste?): copy it again and retry"
    elif "api key" in low or "api_key" in low or status in (401, 403) or "permission" in low:
        hint = "the key is invalid, revoked, or not allowed for this API: create a new one"
    elif status == 404 or "not found" in low:
        hint = "this model name is not available to your key: set another one (see README, default models)"
    elif status == 429 or "quota" in low or "resource_exhausted" in low:
        hint = "quota reached: wait a minute, or use a cheaper model / enable billing"
    elif "endpoint" in low or "connection" in low:
        hint = "the service cannot be reached: check the endpoint and your network"
    else:
        hint = "see the error above"
    return f"{provider}: FAILED -> {hint}\n  {text[:400]}"


def check(settings: Settings) -> tuple[bool, str]:
    try:
        llm = get_provider(settings)
        llm.complete_json("You check a connection.", 'Return {"ok": true}.')
        dim = len(llm.embed(["Scope 1 emissions"])[0])
    except Exception as e:  # noqa: BLE001 - turned into a diagnosis
        return False, explain(settings.provider, e)
    model = {
        "gemini": settings.gemini_model,
        "anthropic": settings.anthropic_model,
        "openai": settings.openai_model,
        "azure": settings.azure_chat_deployment,
    }.get(settings.provider, settings.provider)
    return True, f"{settings.provider}: OK (model {model}, embeddings of size {dim})"
