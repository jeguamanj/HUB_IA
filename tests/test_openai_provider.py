import asyncio
import json

import httpx

from API.llm_providers import openai
from API.services import llm_chat_service


def test_openai_chat_sends_model_messages_and_returns_usage(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_ENDPOINT", "https://example.test/v1")
    captured = {}
    real_async_client = httpx.AsyncClient

    async def handler(request):
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["Authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "gpt-test",
                "choices": [{"message": {"content": "respuesta"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )

    monkeypatch.setattr(
        openai.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    result = asyncio.run(
        openai.chat("pregunta", model="gpt-test", system_prompt="reglas")
    )

    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["body"] == {
        "model": "gpt-test",
        "messages": [
            {"role": "system", "content": "reglas"},
            {"role": "user", "content": "pregunta"},
        ],
    }
    assert result["response"] == "respuesta"
    assert result["usage"]["prompt_tokens"] == 5


def test_openai_chat_returns_configuration_error_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = asyncio.run(openai.chat("pregunta"))

    assert "OPENAI_API_KEY" in result["error"]


def test_chat_route_passes_model_and_combined_system_prompt(monkeypatch):
    captured = {}

    async def fake_chat(prompt, model=None):
        captured["prompt"] = prompt
        captured["model"] = model
        return {"provider": "openai", "response": "ok"}

    monkeypatch.setattr(llm_chat_service.openai, "chat", fake_chat)

    result = asyncio.run(
        llm_chat_service.chat_llm(
            llm_chat_service.LLMChatRequest(
                prompt="pregunta",
                provider="openai",
                model="gpt-test",
                system_prompt="instrucciones",
            )
        )
    )

    assert captured["model"] == "gpt-test"
    assert captured["prompt"] == [
        {"role": "system", "content": "instrucciones"},
        {"role": "user", "content": "pregunta"},
    ]
    assert result["response"] == "ok"
