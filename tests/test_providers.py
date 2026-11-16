"""Each cloud adapter is exercised against a mock HTTP server with the real vendor SDK:
right endpoint, right model, right auth, request shape accepted by the current SDK version.
Skipped when the SDK is not installed (pip install -e ".[all]")."""

import json

import pytest

from esg_lens.config import Settings

AZURE_TEMPERATURE_ERROR = (
    "Unsupported value: 'temperature' does not support 0 with this model. "
    "Only the default (1) value is supported."
)


def test_azure_v1_api_keyless_ready_and_reasoning_models():
    openai = pytest.importorskip("openai")
    pytest.importorskip("azure.identity")
    import httpx

    from esg_lens.llm.azure_openai import AzureOpenAIProvider

    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append((str(req.url), req.headers.get("authorization"), "temperature" in body))
        if req.url.path.endswith("/embeddings"):
            data = [
                {"object": "embedding", "index": i, "embedding": [0.1, 0.2]}
                for i in range(len(body["input"]))
            ]
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": data,
                    "model": "m",
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                },
            )
        if "temperature" in body:  # what a GPT-5 family deployment answers
            err = {
                "message": AZURE_TEMPERATURE_ERROR,
                "type": "invalid_request_error",
                "param": "temperature",
            }
            return httpx.Response(400, json={"error": err})
        msg = {"role": "assistant", "content": '{"ok": true}'}
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [{"index": 0, "finish_reason": "stop", "message": msg}],
            },
        )

    provider = AzureOpenAIProvider(
        Settings(provider="azure", azure_endpoint="https://res.openai.azure.com/", azure_api_key="k")
    )
    provider.client = openai.OpenAI(
        base_url=str(provider.client.base_url),
        api_key="k",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert provider.complete_json("s", "u") == {"ok": True}
    assert provider.complete_json("s", "u") == {"ok": True}
    assert len(provider.embed(["a", "b"])) == 2
    urls = [u for u, _, _ in seen]
    assert urls[0] == "https://res.openai.azure.com/openai/v1/chat/completions"
    assert urls[-1] == "https://res.openai.azure.com/openai/v1/embeddings"
    assert [t for _, _, t in seen[:3]] == [True, False, False]  # temperature dropped after one refusal


def test_anthropic_request_matches_current_sdk(monkeypatch):
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    from esg_lens.llm.anthropic import AnthropicProvider

    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append((req.url.path, body["model"]))
        return httpx2.Response(
            200,
            json={
                "id": "m",
                "type": "message",
                "role": "assistant",
                "model": body["model"],
                "content": [{"type": "text", "text": '{"a": 1}'}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    provider = AnthropicProvider(Settings(provider="anthropic"))
    provider.client = anthropic.Anthropic(
        api_key="x", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler))
    )
    assert provider.complete_json("s", "u") == {"a": 1}
    assert seen == [("/v1/messages", Settings().anthropic_model)]


def test_gemini_generate_and_batch_embed(monkeypatch):
    genai = pytest.importorskip("google.genai")
    import httpx

    monkeypatch.setenv("GEMINI_API_KEY", "x")
    from esg_lens.llm.gemini import GeminiProvider

    paths = []

    def handler(req):
        paths.append(req.url.path)
        body = json.loads(req.content)
        if "embed" in req.url.path.lower():
            return httpx.Response(200, json={"embeddings": [{"values": [0.1, 0.2]}] * len(body["requests"])})
        parts = [{"text": '{"b": 2}'}]
        return httpx.Response(
            200, json={"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}]}
        )

    provider = GeminiProvider(Settings(provider="gemini"))
    provider.client = provider.embed_client = genai.Client(
        api_key="x",
        http_options=genai.types.HttpOptions(
            httpx_client=httpx.Client(transport=httpx.MockTransport(handler))
        ),
    )
    assert provider.complete_json("s", "u") == {"b": 2}
    assert len(provider.embed(["a", "b", "c"])) == 3
    assert paths[0].endswith(f"{Settings().gemini_model}:generateContent")
    assert paths[1].endswith(f"{Settings().gemini_embedding_model}:batchEmbedContents")


def test_vertex_generation_and_embeddings_use_their_own_locations():
    pytest.importorskip("google.genai")
    from esg_lens.llm.gemini import GeminiProvider

    p = GeminiProvider(Settings(provider="gemini", gcp_project="demo-project"))
    assert p.client._api_client.location == "global"
    assert p.embed_client._api_client.location == "europe-west1"


def test_openai_api_endpoint_auth_and_reasoning_models(monkeypatch):
    openai = pytest.importorskip("openai")
    import httpx

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from esg_lens.llm.openai_api import OpenAIProvider

    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(
            (str(req.url), req.headers.get("authorization"), body.get("model"), "temperature" in body)
        )
        if req.url.path.endswith("/embeddings"):
            data = [
                {"object": "embedding", "index": i, "embedding": [0.1]} for i in range(len(body["input"]))
            ]
            usage = {"prompt_tokens": 1, "total_tokens": 1}
            return httpx.Response(200, json={"object": "list", "data": data, "model": "m", "usage": usage})
        if "temperature" in body:
            err = {
                "message": AZURE_TEMPERATURE_ERROR,
                "type": "invalid_request_error",
                "param": "temperature",
            }
            return httpx.Response(400, json={"error": err})
        msg = {"role": "assistant", "content": '{"ok": true}'}
        choice = {"index": 0, "finish_reason": "stop", "message": msg}
        return httpx.Response(
            200,
            json={"id": "x", "object": "chat.completion", "created": 0, "model": "m", "choices": [choice]},
        )

    provider = OpenAIProvider(Settings(provider="openai"))
    provider.client = openai.OpenAI(
        api_key="sk-test", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert provider.complete_json("s", "u") == {"ok": True}
    assert len(provider.embed(["a", "b"])) == 2
    assert seen[1][0] == "https://api.openai.com/v1/chat/completions"
    assert seen[1][1:] == ("Bearer sk-test", "gpt-5.4-mini", False)
    assert seen[-1][2] == "text-embedding-3-small"
