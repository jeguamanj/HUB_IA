# API/llm_providers/gemini.py

import os
import asyncio
import json
from typing import Optional, Union, List, Dict, Any
import requests
from dotenv import load_dotenv

load_dotenv()

# Endpoint base de OpenRouter asegurando la ruta completa /api/v1/chat/completions
_raw_endpoint = os.getenv("OPENROUTER_ENDPOINT", "https://openrouter.ai/api/v1/chat/completions")
if not _raw_endpoint.endswith("/chat/completions"):
    OPENROUTER_ENDPOINT = _raw_endpoint.rstrip("/") + "/api/v1/chat/completions"
else:
    OPENROUTER_ENDPOINT = _raw_endpoint

# Modelo gratuito de Google Gemma en OpenRouter por defecto
MODEL = os.getenv("GEMMA_MODEL", os.getenv("GEMINI_MODEL", "google/gemma-4-31b-it:free"))


def _sync_chat(
    prompt: Union[str, List[Dict[str, Any]]],
    api_key: str,
    model: str,
    endpoint: str,
    enable_reasoning: bool = True,
    response_format: Optional[dict] = None
) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost",
        "X-Title": "MCP-HUB-Server IA"
    }

    # Soporte tanto para un string simple como para una lista estructurada de mensajes
    if isinstance(prompt, str):
        messages = [{"role": "user", "content": prompt}]
    else:
        messages = prompt

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages
    }

    # Habilitar el razonamiento estructurado según especificación de OpenRouter
    if enable_reasoning:
        payload["reasoning"] = {"enabled": True}

    if response_format:
        payload["response_format"] = response_format

    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=60)
        
        if response.status_code == 200:
            result = response.json()
            if "error" in result:
                err_obj = result.get("error", {})
                err_msg = err_obj.get("message", str(err_obj)) if isinstance(err_obj, dict) else str(err_obj)
                return {"error": f"Error OpenRouter (200 con error upstream): {err_msg}"}

            choices = result.get("choices", [])
            if not choices:
                return {"error": "Error OpenRouter: respuesta 200 sin 'choices'"}

            choice = choices[0]
            message = choice.get("message", {})
            content = message.get("content") or ""
            reasoning_details = message.get("reasoning_details") or message.get("reasoning")

            if not content.strip() and reasoning_details and isinstance(reasoning_details, str):
                content = reasoning_details

            return {
                "provider": "gemma",
                "model": model,
                "prompt": prompt if isinstance(prompt, str) else messages[-1].get("content", ""),
                "response": content,
                "text": content,  # Compatibilidad con accesos por .get("text")
                "reasoning_details": reasoning_details
            }
        else:
            try:
                err_json = response.json()
                err_obj = err_json.get("error", {})
                err_msg = err_obj.get("message") or response.text
                raw_detail = err_obj.get("metadata", {}).get("raw")
                if raw_detail:
                    err_msg = f"{err_msg} - {raw_detail}"
            except Exception:
                err_msg = response.text
            return {"error": f"Error OpenRouter ({response.status_code}): {err_msg}"}
            
    except Exception as e:
        return {"error": f"Excepción de conexión LLM (Gemma/OpenRouter): {str(e)}"}


async def chat(
    prompt: Union[str, List[Dict[str, Any]]],
    enable_reasoning: bool = True,
    response_format: Optional[dict] = None,
    model: Optional[str] = None
) -> Dict[str, Any]:
    """
    Función asíncrona principal de chat con Google Gemma vía OpenRouter.
    """
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"error": "OPENROUTER_API_KEY no configurada en el entorno"}

    target_model = model or MODEL
    return await asyncio.to_thread(
        _sync_chat,
        prompt,
        api_key,
        target_model,
        OPENROUTER_ENDPOINT,
        enable_reasoning,
        response_format
    )


async def generate(prompt: str, response_format: Optional[dict] = None) -> Dict[str, Any]:
    """
    Alias de compatibilidad con código que invoque generate().
    """
    return await chat(prompt, enable_reasoning=True, response_format=response_format)
