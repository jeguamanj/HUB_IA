from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
try:
    from API.llm_providers import deepseek, gemini, openai, modelo, ollama
    from API.llm_providers import openrouter_catalog
    from API.core.context_engine import get_current_context
except ImportError:
    # pyrefly: ignore [missing-import]
    from llm_providers import deepseek, gemini, openai, modelo, ollama
    # pyrefly: ignore [missing-import]
    from llm_providers import openrouter_catalog
    # pyrefly: ignore [missing-import]
    from core.context_engine import get_current_context


router = APIRouter()

class LLMChatRequest(BaseModel):
    prompt: str
    provider: str = "modelo"
    model: Optional[str] = None
    system_prompt: Optional[str] = None

@router.post("/chat")
async def chat_llm(request: LLMChatRequest):
    """
    Servicio de chat con LLM. Permite seleccionar el proveedor:
    - 'modelo' (o 'free', 'openrouter-free'): Con fallback inteligente ultrarrápido (Hedged Racing)
    - 'ollama' (o 'onpremise', 'local'): Inferencia en servidor Ollama local/institucional
    - 'deepseek': DeepSeek Chat vía OpenRouter
    - 'gemini' o 'gemma': Google Gemma vía OpenRouter
    - 'openai': OpenAI
    Inyecta automáticamente el contexto institucional activo si existe.
    """
    provider = request.provider.strip().lower()

    # Resolver contexto institucional si está activo en la sesión/solicitud
    ctx = get_current_context()
    institutional_sys_prompt = ctx.to_system_instruction() if ctx else ""
    user_sys_prompt = request.system_prompt or ""
    combined_sys_prompt = f"{institutional_sys_prompt}\n{user_sys_prompt}".strip() if (institutional_sys_prompt or user_sys_prompt) else None

    # Si hay system prompt, estructurar como lista de mensajes
    if combined_sys_prompt:
        messages = [
            {"role": "system", "content": combined_sys_prompt},
            {"role": "user", "content": request.prompt}
        ]
        prompt_payload = messages
    else:
        prompt_payload = request.prompt

    if provider in ["modelo", "free", "openrouter-free"]:
        res = await modelo.chat(prompt_payload, modelo_preferido=request.model)
        if ctx:
            res["context_id"] = ctx.context_id
            res["tenant_id"] = ctx.tenant_id
        return res
    elif provider in ["ollama", "onpremise", "local"]:
        res = await ollama.chat(request.prompt, model=request.model, system_prompt=combined_sys_prompt)
        if ctx:
            res["context_id"] = ctx.context_id
            res["tenant_id"] = ctx.tenant_id
        return res
    elif provider == "deepseek":
        return await deepseek.chat(request.prompt)
    elif provider in ["gemini", "gemma"]:
        return await gemini.chat(request.prompt)
    elif provider == "openai":
        return await openai.chat(request.prompt)
    else:
        raise HTTPException(status_code=400, detail=f"Proveedor LLM '{request.provider}' no soportado")


@router.get("/models/free")
async def get_free_models(refresh: bool = False):
    """
    Consulta en tiempo real la lista dinámica de modelos gratuitos disponibles en OpenRouter,
    filtrados y priorizados por estabilidad, contexto y capacidades de razonamiento.
    """
    return await openrouter_catalog.get_free_models_catalog(force_refresh=refresh)


@router.post("/models/refresh")
async def refresh_free_models():
    """
    Fuerza la actualización inmediata de la caché de modelos gratuitos desde OpenRouter.
    """
    return await openrouter_catalog.get_free_models_catalog(force_refresh=True)


@router.get("/ollama/status")
async def get_ollama_status():
    """
    Verifica el estado del servicio local/on-premise de Ollama.
    """
    available = await ollama.is_available()
    return {
        "provider": "ollama",
        "endpoint": ollama.OLLAMA_ENDPOINT,
        "available": available,
        "default_model": ollama.OLLAMA_DEFAULT_MODEL
    }


@router.get("/ollama/models")
async def get_ollama_models():
    """
    Lista los modelos descargados y disponibles en el servidor On-Premise de Ollama.
    """
    models = await ollama.list_models()
    return {
        "provider": "ollama",
        "count": len(models),
        "models": models
    }


def get_router():
    return router 