import httpx
import pytest
from geoassist.llm.base import LLMError
from geoassist.llm.openai_compatible import OpenAICompatibleClient


def client(handler, sleeper=lambda seconds: None):
    return OpenAICompatibleClient(
        "https://llm.example/v1",
        "secret-key",
        "model-x",
        transport=httpx.MockTransport(handler),
        sleeper=sleeper,
    )


def test_success_returns_content_and_sends_auth_header():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "respuesta"}}]})

    result = client(handler).complete([{"role": "user", "content": "hola"}], timeout_s=2)
    assert result == "respuesta"
    assert seen["auth"] == "Bearer secret-key"
    assert "secret-key" not in "https://llm.example/v1"


def test_429_retries_once():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "rate"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    assert client(handler).complete([], timeout_s=2) == "ok"
    assert calls["n"] == 2


def test_timeout_raises_without_retry():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ReadTimeout("timeout")

    with pytest.raises(LLMError) as error:
        client(handler).complete([], timeout_s=0.1)
    assert error.value.kind == "timeout"
    assert calls["n"] == 1


def test_500_after_retry_raises():
    def handler(request):
        return httpx.Response(500, json={"error": "server"})

    with pytest.raises(LLMError) as error:
        client(handler).complete([], timeout_s=1)
    assert error.value.kind == "http"
