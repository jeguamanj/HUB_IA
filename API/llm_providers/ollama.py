# API/llm_providers/ollama.py

import os
import time
import logging
from typing import Optional, Union, List, Dict, Any
import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("OllamaProvider")

OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", "http://localhost:11434").rstrip("/")
OLLAMA_DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "llama3.3:latest")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "60.0"))


async def is_available(endpoint: Optional[str] = None) -> bool:
    """
    Verifica rápidamente si el servidor de Ollama está encendido y accesible.
    Timeout muy corto (1.5s) para no demorar la toma de decisiones.
    """
    url = (endpoint or OLLAMA_ENDPOINT).rstrip("/") + "/api/tags"
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(url)
            return resp.status_code == 200
    except Exception:
        return False


async def list_models(endpoint: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Lista los modelos instalados localmente en la instancia de Ollama.
    """
    url = (endpoint or OLLAMA_ENDPOINT).rstrip("/") + "/api/tags"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json().get("models", [])
            return []
    except Exception as e:
        logger.warning(f"No se pudieron listar los modelos de Ollama: {e}")
        return []


async def chat(
    prompt: Union[str, List[Dict[str, Any]]],
    model: Optional[str] = None,
    endpoint: Optional[str] = None,
    system_prompt: Optional[str] = None,
    response_format: Optional[str] = None,  # "json" para forzar structured output en Ollama
    options: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Ejecuta una inferencia en el servidor On-Premise de Ollama mediante su API nativa /api/chat.
    """
    target_endpoint = (endpoint or OLLAMA_ENDPOINT).rstrip("/")
    target_model = model or OLLAMA_DEFAULT_MODEL
    url = f"{target_endpoint}/api/chat"

    # Construir lista de mensajes
    messages: List[Dict[str, Any]] = []

    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    if isinstance(prompt, str):
        messages.append({"role": "user", "content": prompt})
    elif isinstance(prompt, list):
        messages.extend(prompt)

    payload: Dict[str, Any] = {
        "model": target_model,
        "messages": messages,
        "stream": False
    }

    if response_format:
        payload["format"] = response_format

    if options:
        payload["options"] = options

    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
            resp = await client.post(url, json=payload)

            elapsed = round(time.time() - t0, 2)

            if resp.status_code == 200:
                data = resp.json()
                msg = data.get("message", {})
                content = msg.get("content", "").strip()

                return {
                    "provider": "ollama",
                    "model_used": target_model,
                    "model": target_model,
                    "prompt": prompt if isinstance(prompt, str) else messages[-1].get("content", ""),
                    "response": content,
                    "text": content,
                    "total_elapsed_seconds": elapsed,
                    "eval_duration_ms": round(data.get("eval_duration", 0) / 1e6, 2),
                    "prompt_eval_count": data.get("prompt_eval_count", 0),
                    "eval_count": data.get("eval_count", 0)
                }
            else:
                return {
                    "provider": "ollama",
                    "error": f"Error Ollama ({resp.status_code}): {resp.text}",
                    "status_code": resp.status_code,
                    "total_elapsed_seconds": elapsed
                }

    except httpx.TimeoutException:
        return {
            "provider": "ollama",
            "error": f"Timeout esperando respuesta de Ollama tras {OLLAMA_TIMEOUT}s.",
            "status_code": "timeout"
        }
    except Exception as e:
        return {
            "provider": "ollama",
            "error": f"Excepción conectando a Ollama ({target_endpoint}): {str(e)}",
            "status_code": "connection_error"
        }
