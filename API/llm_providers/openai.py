"""OpenAI-compatible chat completions provider."""

import os
from typing import Any, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

DEFAULT_ENDPOINT = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT = 60.0


def _chat_completions_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip() or DEFAULT_ENDPOINT
    if endpoint.rstrip("/").endswith("/v1"):
        return f"{endpoint.rstrip('/')}/chat/completions"
    return endpoint


async def chat(
    prompt: str | list[dict[str, Any]],
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Call OpenAI Chat Completions and return the shape used by HUB providers."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {"error": "OPENAI_API_KEY no configurada en el entorno"}

    if isinstance(prompt, str):
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
    else:
        messages = list(prompt)

    request_body = {
        "model": model or os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
        "messages": messages,
    }
    endpoint = _chat_completions_endpoint(os.getenv("OPENAI_ENDPOINT", ""))

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
                json=request_body,
            )
        if response.is_error:
            return {
                "error": f"OpenAI API respondió HTTP {response.status_code}: {response.text[:500]}"
            }

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            return {"error": "OpenAI API devolvió una respuesta sin choices"}

        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, str):
            return {"error": "OpenAI API devolvió contenido de respuesta inválido"}

        result = {
            "provider": "openai",
            "model": data.get("model") or request_body["model"],
            "response": content,
        }
        if isinstance(data.get("usage"), dict):
            result["usage"] = data["usage"]
        return result
    except httpx.TimeoutException:
        return {"error": f"OpenAI API excedió el timeout de {timeout:g} segundos"}
    except httpx.HTTPError as exc:
        return {"error": f"Error de conexión con OpenAI API: {type(exc).__name__}"}
    except (ValueError, TypeError, KeyError):
        return {"error": "OpenAI API devolvió una respuesta inválida"}