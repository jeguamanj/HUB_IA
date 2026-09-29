# API/llm_providers/openrouter_catalog.py

import os
import time
import logging
import asyncio
from typing import List, Dict, Any, Optional
import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("OpenRouterCatalog")

OPENROUTER_MODELS_ENDPOINT = os.getenv("OPENROUTER_MODELS_ENDPOINT", "https://openrouter.ai/api/v1/models")
CACHE_TTL_SECONDS = int(os.getenv("OPENROUTER_MODELS_CACHE_TTL", "1800"))  # 30 minutos

# Modelos estáticos de emergencia si OpenRouter no responde
STATIC_FALLBACK_MODELS: List[str] = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "qwen/qwen3.8-27b:free",
    "liquid/lfm-2.5-2.6b:free",
    "cohere/north-mini-code:free",
    "dots-studio/dots-3-note-preview:free",
    "poolside/laguna-s-2.1:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free"
]

# Modelos excluidos explícitamente (restringidos a IDEs o exclusivos de moderación)
EXCLUDED_MODEL_IDS = {
    "thinkingmachines/inkling:free",
    "thinkingmachines/inkling-small:free",
    "nvidia/nemotron-3.5-content-safety:free"
}

# Estado de caché en memoria
_cached_catalog: List[Dict[str, Any]] = []
_cached_prioritized_ids: List[str] = list(STATIC_FALLBACK_MODELS)
_last_fetch_time: float = 0.0
_cache_lock = asyncio.Lock()


def _score_model_for_prioritization(model_data: Dict[str, Any]) -> float:
    """
    Calcula una puntuación de idoneidad para ordenar la cola de modelos gratuitos.
    Prioriza estabilidad, velocidad comprobada (< 2s), ventana de contexto y soporte de razonamiento.
    """
    mid = model_data.get("id", "").lower()
    score = 0.0

    # 1. Ponderación por proveedor y familia (modelos comprobados < 2s en la cima)
    if "nvidia/nemotron-3-ultra" in mid:
        score += 125.0  # Ultra rápido (< 0.8s) y alta calidad 550B
    elif "nvidia/nemotron-3-super" in mid:
        score += 120.0  # Ultra rápido (< 1.7s) y 120B
    elif "nvidia/nemotron-3.5-lightning" in mid:
        score += 105.0  # Rápido pero ocasionalmente encolado upstream
    elif "qwen/qwen" in mid:
        score += 100.0  # Gran capacidad de razonamiento e instrucción
    elif "nvidia/" in mid:
        score += 90.0
    elif "google/gemma" in mid:
        score += 80.0
    elif "cohere/" in mid or "liquid/" in mid:
        score += 75.0
    elif "nex-agi/" in mid:
        score += 70.0
    else:
        score += 50.0

    # 2. Bono por ventana de contexto (hasta +15 puntos)
    ctx = model_data.get("context_length") or 0
    score += min(ctx / 20000.0, 15.0)

    # 3. Bono si soporta razonamiento estructurado (+10 puntos)
    supported_params = model_data.get("supported_parameters", []) or []
    if "reasoning" in supported_params or "include_reasoning" in supported_params:
        score += 10.0

    # 4. Bono si es multimodal / visión (+5 puntos)
    arch = model_data.get("architecture", {}) or {}
    modality = str(arch.get("modality", "")).lower()
    if "image" in modality:
        score += 5.0

    return score


def _parse_model_info(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Extrae y normaliza los metadatos relevantes de un modelo gratuito."""
    mid = raw.get("id", "")
    arch = raw.get("architecture", {}) or {}
    modality = str(arch.get("modality", "text->text"))
    params = raw.get("supported_parameters", []) or []
    pricing = raw.get("pricing", {}) or {}

    provider_brand = mid.split("/")[0] if "/" in mid else "unknown"

    return {
        "id": mid,
        "name": raw.get("name", mid),
        "description": raw.get("description", ""),
        "context_length": raw.get("context_length", 0),
        "provider_brand": provider_brand,
        "is_multimodal": "image" in modality,
        "modality": modality,
        "supports_reasoning": "reasoning" in params or "include_reasoning" in params,
        "supports_tools": "tools" in params,
        "supports_structured_outputs": "response_format" in params or "structured_outputs" in params,
        "pricing": {
            "prompt": pricing.get("prompt", "0"),
            "completion": pricing.get("completion", "0")
        },
        "expiration_date": raw.get("expiration_date")
    }


async def fetch_openrouter_free_models(timeout: float = 12.0) -> List[Dict[str, Any]]:
    """
    Consulta en tiempo real la API oficial de OpenRouter (/api/v1/models)
    y filtra únicamente los modelos gratuitos activos y aptos para chat.
    """
    headers = {
        "HTTP-Referer": "http://localhost",
        "X-Title": "MCP-HUB-Server IA Catalog"
    }
    api_key = os.getenv("OPENROUTER_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(OPENROUTER_MODELS_ENDPOINT, headers=headers)
        response.raise_for_status()
        data = response.json().get("data", [])

    free_parsed = []
    for item in data:
        mid = item.get("id", "")
        pricing = item.get("pricing", {}) or {}
        prompt_cost = str(pricing.get("prompt", "1")).strip()
        comp_cost = str(pricing.get("completion", "1")).strip()

        is_free_flag = mid.endswith(":free") or (prompt_cost == "0" and comp_cost == "0")

        if is_free_flag and mid not in EXCLUDED_MODEL_IDS:
            parsed = _parse_model_info(item)
            parsed["_score"] = _score_model_for_prioritization(item)
            free_parsed.append(parsed)

    # Ordenar por puntuación descendente
    free_parsed.sort(key=lambda m: m.get("_score", 0.0), reverse=True)

    # Limpiar clave interna _score
    for m in free_parsed:
        m.pop("_score", None)

    return free_parsed


async def get_free_models_catalog(force_refresh: bool = False) -> Dict[str, Any]:
    """
    Obtiene el catálogo completo de modelos gratuitos con información enriquecida.
    Aplica caché en memoria respetando el TTL configurado.
    """
    global _cached_catalog, _cached_prioritized_ids, _last_fetch_time

    now = time.time()
    cache_expired = (now - _last_fetch_time) > CACHE_TTL_SECONDS

    if force_refresh or cache_expired or not _cached_catalog:
        async with _cache_lock:
            # Doble comprobación bajo lock
            now = time.time()
            if force_refresh or (now - _last_fetch_time) > CACHE_TTL_SECONDS or not _cached_catalog:
                try:
                    logger.info("Actualizando catálogo dinámico de modelos free desde OpenRouter...")
                    models = await fetch_openrouter_free_models()
                    if models:
                        _cached_catalog = models
                        _cached_prioritized_ids = [m["id"] for m in models]
                        _last_fetch_time = now
                        logger.info(f"Catálogo actualizado con éxito: {len(models)} modelos gratuitos disponibles.")
                except Exception as e:
                    logger.warning(f"Error consultando OpenRouter API ({e}). Usando lista de respaldo.")
                    if not _cached_catalog:
                        _cached_prioritized_ids = list(STATIC_FALLBACK_MODELS)

    # Formatear fecha legible
    last_updated_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(_last_fetch_time)) if _last_fetch_time > 0 else "N/A"

    return {
        "status": "success",
        "total_free_models": len(_cached_catalog) if _cached_catalog else len(_cached_prioritized_ids),
        "last_updated": last_updated_iso,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "cached": not (force_refresh or cache_expired),
        "models": _cached_catalog if _cached_catalog else [{"id": mid, "name": mid} for mid in _cached_prioritized_ids]
    }


async def get_prioritized_free_model_ids(force_refresh: bool = False) -> List[str]:
    """
    Devuelve la lista ordenada de IDs de modelos gratuitos listos para usar en la cola de fallback.
    """
    await get_free_models_catalog(force_refresh=force_refresh)
    return list(_cached_prioritized_ids) if _cached_prioritized_ids else list(STATIC_FALLBACK_MODELS)


def get_cached_prioritized_models_sync() -> List[str]:
    """
    Devuelve la lista en memoria de modelos priorizados sin bloquear (acceso síncrono).
    """
    if _cached_prioritized_ids:
        return list(_cached_prioritized_ids)
    return list(STATIC_FALLBACK_MODELS)
