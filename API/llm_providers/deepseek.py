# API/llm_providers/deepseek.py

import os
import asyncio
import requests
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_ENDPOINT = os.getenv("OPENROUTER_ENDPOINT", "https://openrouter.ai/api/v1/chat/completions")
MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat-v3-0324")


def _sync_chat(prompt: str, api_key: str, model: str, endpoint: str):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost",
        "X-Title": "MCP-HUB-Server"
    }
    data = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    try:
        response = requests.post(endpoint, headers=headers, json=data, timeout=60)
        if response.status_code == 200:
            result = response.json()
            if "error" in result:
                err_obj = result.get("error", {})
                err_msg = err_obj.get("message", str(err_obj)) if isinstance(err_obj, dict) else str(err_obj)
                return {"error": f"Error OpenRouter (200 con error upstream): {err_msg}"}
            choices = result.get("choices", [])
            if not choices:
                return {"error": "Error OpenRouter: respuesta 200 sin 'choices'"}
            content = choices[0].get("message", {}).get("content") or ""
            return {
                "provider": "deepseek",
                "model": model,
                "prompt": prompt,
                "response": content
            }
        else:
            return {
                "error": f"Error {response.status_code}: {response.text}"
            }
    except Exception as e:
        return {"error": f"Excepción de conexión LLM: {str(e)}"}


async def chat(prompt: str):
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"error": "OPENROUTER_API_KEY no configurada en el entorno"}

    return await asyncio.to_thread(_sync_chat, prompt, api_key, MODEL, OPENROUTER_ENDPOINT)