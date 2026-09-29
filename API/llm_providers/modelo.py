# API/llm_providers/modelo.py

import os
import time
import asyncio
from typing import Optional, Union, List, Dict, Any
import httpx
from dotenv import load_dotenv

try:
    from API.core.logging import get_logger
except ImportError:  # pragma: no cover
    from core.logging import get_logger

try:
    from API.llm_providers.openrouter_catalog import (
        get_prioritized_free_model_ids,
        get_cached_prioritized_models_sync,
        STATIC_FALLBACK_MODELS
    )
except ImportError:
    # pyrefly: ignore [missing-import]
    from llm_providers.openrouter_catalog import (
        get_prioritized_free_model_ids,
        get_cached_prioritized_models_sync,
        STATIC_FALLBACK_MODELS
    )

load_dotenv()

logger = get_logger("llm_providers.modelo")

# Endpoint base de OpenRouter asegurando la ruta completa
_raw_endpoint = os.getenv("OPENROUTER_ENDPOINT", "https://openrouter.ai/api/v1/chat/completions")
if not _raw_endpoint.endswith("/chat/completions"):
    OPENROUTER_ENDPOINT = _raw_endpoint.rstrip("/") + "/api/v1/chat/completions"
else:
    OPENROUTER_ENDPOINT = _raw_endpoint

# Parámetros de rendimiento y conmutación agresiva
SOFT_TIMEOUT_SECONDS = float(os.getenv("LLM_SOFT_TIMEOUT", "3.5"))   # Si tarda más de 3.5s, dispara el siguiente en paralelo
HARD_TIMEOUT_SECONDS = float(os.getenv("LLM_HARD_TIMEOUT", "12.0"))  # Máximo tiempo que un modelo puede retener conexión
MAX_CONCURRENT_RACE = int(os.getenv("LLM_MAX_CONCURRENT", "2"))      # Máximo de modelos en carrera simultánea

# Alias genéricos que no representan un modelo de OpenRouter específico
ALIAS_GENERICOS = {"modelo", "free", "openrouter-free", "default", "none", "null", "auto", ""}


# ============================================================
# CIRCUIT BREAKER & HEALTH TRACKER EN MEMORIA
# ============================================================
class ModelHealthTracker:
    """
    Rastrea en tiempo real la salud y latencia de cada modelo.
    Pone modelos saturados en enfriamiento (cooldown) y prioriza los que responden más rápido.
    """
    def __init__(self, base_cooldown_seconds: int = 180):
        self.base_cooldown_seconds = base_cooldown_seconds
        self.failures: Dict[str, int] = {}
        self.cooldown_until: Dict[str, float] = {}
        self.avg_latencies: Dict[str, float] = {}

    def is_available(self, model: str) -> bool:
        return time.time() > self.cooldown_until.get(model, 0.0)

    def record_success(self, model: str, latency: float):
        self.failures[model] = 0
        self.cooldown_until.pop(model, None)
        prev = self.avg_latencies.get(model, latency)
        # Promedio móvil exponencial ponderado
        self.avg_latencies[model] = round(0.6 * prev + 0.4 * latency, 2)

    def record_failure(self, model: str, reason: str = ""):
        fails = self.failures.get(model, 0) + 1
        self.failures[model] = fails
        cooldown = self.base_cooldown_seconds * min(fails, 4)
        self.cooldown_until[model] = time.time() + cooldown
        logger.warning("Circuit Breaker: modelo '%s' en enfriamiento (%ss) por: %s", model, cooldown, reason)


# Singleton global de salud de modelos
health_tracker = ModelHealthTracker()


def _obtener_lista_modelos(
    modelo_preferido: Optional[str] = None,
    lista_base: Optional[List[str]] = None
) -> List[str]:
    """
    Construye la lista ordenada de modelos a intentar:
    1. Si hay un modelo preferido válido, se coloca al inicio.
    2. Los modelos activos se ordenan por su latencia histórica más rápida.
    3. Los modelos en enfriamiento se colocan al final como último recurso.
    """
    if lista_base:
        modelos = list(lista_base)
    else:
        modelos = get_cached_prioritized_models_sync()

    # Separar disponibles de los que están en cooldown
    disponibles = [m for m in modelos if health_tracker.is_available(m)]
    en_cooldown = [m for m in modelos if not health_tracker.is_available(m)]

    # Ordenar disponibles dando prioridad a los que históricamente respondieron más rápido
    disponibles.sort(key=lambda m: health_tracker.avg_latencies.get(m, 5.0))

    ordenados = disponibles + en_cooldown

    # Verificar si el usuario pidió un modelo preferido explícito
    env_pref = os.getenv("PREFERRED_FREE_MODEL", "").strip()
    preferido = (modelo_preferido or env_pref).strip()

    if preferido and preferido.lower() not in ALIAS_GENERICOS:
        if preferido in ordenados:
            ordenados.remove(preferido)
        ordenados.insert(0, preferido)

    return ordenados


# ============================================================
# PETICIÓN ATÓMICA ASÍNCRONA POR MODELO
# ============================================================
async def _query_single_model_async(
    client: httpx.AsyncClient,
    model: str,
    messages: List[Dict[str, Any]],
    endpoint: str,
    headers: Dict[str, str],
    enable_reasoning: bool = False,
    response_format: Optional[dict] = None
) -> Dict[str, Any]:
    """
    Ejecuta una petición individual no bloqueante con timeout estricto.
    """
    t0 = time.time()
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages
    }
    if enable_reasoning:
        payload["reasoning"] = {"enabled": True}
    if response_format:
        payload["response_format"] = response_format

    try:
        response = await client.post(endpoint, headers=headers, json=payload, timeout=HARD_TIMEOUT_SECONDS)
        elapsed = round(time.time() - t0, 2)

        # Caso HTTP 200 OK
        if response.status_code == 200:
            result = response.json()

            # Detectar si OpenRouter devolvió un error upstream encapsulado en 200
            if "error" in result:
                err_obj = result.get("error", {})
                err_msg = err_obj.get("message", "Error upstream en 200") if isinstance(err_obj, dict) else str(err_obj)
                return {
                    "model": model,
                    "elapsed": elapsed,
                    "success": False,
                    "status_code": 200,
                    "error": f"Upstream error (200): {err_msg}"
                }

            choices = result.get("choices", [])
            if not choices:
                return {
                    "model": model,
                    "elapsed": elapsed,
                    "success": False,
                    "status_code": 200,
                    "error": "Choices vacío en respuesta 200"
                }

            choice = choices[0]
            message = choice.get("message", {})
            content = message.get("content") or ""
            reasoning_details = message.get("reasoning_details") or message.get("reasoning")

            if not content.strip() and reasoning_details and isinstance(reasoning_details, str):
                content = reasoning_details

            if not content.strip():
                return {
                    "model": model,
                    "elapsed": elapsed,
                    "success": False,
                    "status_code": 200,
                    "error": "Contenido de texto vacío"
                }

            return {
                "model": model,
                "elapsed": elapsed,
                "success": True,
                "content": content,
                "reasoning_details": reasoning_details
            }

        # Caso saturación (429) o fallos de servidor (500/502/503/504)
        elif response.status_code in [429, 500, 502, 503, 504]:
            try:
                err_detail = response.json().get("error", {}).get("message", "")
            except Exception:
                err_detail = response.text[:120]
            motivo = "Rate Limit (429)" if response.status_code == 429 else f"Error servidor ({response.status_code})"
            return {
                "model": model,
                "elapsed": elapsed,
                "success": False,
                "status_code": response.status_code,
                "error": f"{motivo}: {err_detail}"
            }

        # Otros errores (ej. 400 bad request)
        else:
            return {
                "model": model,
                "elapsed": elapsed,
                "success": False,
                "status_code": response.status_code,
                "error": response.text[:120]
            }

    except httpx.TimeoutException:
        elapsed = round(time.time() - t0, 2)
        return {
            "model": model,
            "elapsed": elapsed,
            "success": False,
            "status_code": "timeout",
            "error": f"Timeout tras {elapsed}s (límite {HARD_TIMEOUT_SECONDS}s)"
        }
    except Exception as e:
        elapsed = round(time.time() - t0, 2)
        return {
            "model": model,
            "elapsed": elapsed,
            "success": False,
            "status_code": "exception",
            "error": str(e)
        }


# ============================================================
# MOTOR DE CONMUTACIÓN RÁPIDA HEDGED / RACING
# ============================================================
async def _async_hedged_chat_fallback(
    prompt: Union[str, List[Dict[str, Any]]],
    api_key: str,
    endpoint: str,
    modelos_a_probar: List[str],
    enable_reasoning: bool = False,
    response_format: Optional[dict] = None
) -> Dict[str, Any]:
    """
    Ejecuta el algoritmo de conmutación rápida (Hedged Requests Racing):
    - Dispara el primer modelo candidato.
    - Si no responde en SOFT_TIMEOUT_SECONDS (ej. 3.5s), inicia de inmediato el siguiente
      modelo en paralelo sin abortar el primero.
    - El primer modelo que devuelva una respuesta válida GANA inmediatamente.
    - Las tareas pendientes se cancelan, logrando respuestas en 2-4 segundos.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost",
        "X-Title": "MCP-HUB-Server IA Fast"
    }

    messages = [{"role": "user", "content": prompt}] if isinstance(prompt, str) else prompt
    prompt_str = prompt if isinstance(prompt, str) else messages[-1].get("content", "")

    t_global_start = time.time()
    intentos_registro: List[Dict[str, Any]] = []

    logger.info("Fast LLM Engine: iniciando conmutación rápida con %s candidatos", len(modelos_a_probar))

    async with httpx.AsyncClient() as client:
        active_tasks: Dict[asyncio.Task, str] = {}
        next_model_idx = 0

        while active_tasks or next_model_idx < len(modelos_a_probar):
            # 1. Lanzar tareas en paralelo si hay cupo concurrente
            while len(active_tasks) < MAX_CONCURRENT_RACE and next_model_idx < len(modelos_a_probar):
                model_name = modelos_a_probar[next_model_idx]
                next_model_idx += 1
                t_launch = round(time.time() - t_global_start, 2)
                logger.info("Fast LLM [%ss]: disparando modelo #%s: '%s'", t_launch, next_model_idx, model_name)

                task = asyncio.create_task(
                    _query_single_model_async(
                        client=client,
                        model=model_name,
                        messages=messages,
                        endpoint=endpoint,
                        headers=headers,
                        enable_reasoning=enable_reasoning,
                        response_format=response_format
                    )
                )
                active_tasks[task] = model_name

            # 2. Esperar por el próximo evento: soft timeout (para escalar) o la primera completada
            wait_timeout = SOFT_TIMEOUT_SECONDS if next_model_idx < len(modelos_a_probar) else HARD_TIMEOUT_SECONDS

            done, pending = await asyncio.wait(
                set(active_tasks.keys()),
                timeout=wait_timeout,
                return_when=asyncio.FIRST_COMPLETED
            )

            # 3. Procesar las tareas que hayan terminado
            for finished_task in done:
                res = finished_task.result()
                model_name = res.get("model", "unknown")
                active_tasks.pop(finished_task, None)

                # ¡Éxito! Tenemos respuesta válida
                if res.get("success"):
                    t_win = round(time.time() - t_global_start, 2)
                    logger.info("Fast LLM [OK] [%ss]: éxito con '%s' (latencia individual: %ss)", t_win, model_name, res['elapsed'])
                    health_tracker.record_success(model_name, res["elapsed"])

                    # Cancelar inmediatamente las demás tareas en carrera
                    for pending_task in active_tasks.keys():
                        pending_task.cancel()

                    return {
                        "provider": "openrouter-free",
                        "model_used": model_name,
                        "model": model_name,
                        "prompt": prompt_str,
                        "response": res["content"],
                        "text": res["content"],  # Compatibilidad
                        "reasoning_details": res.get("reasoning_details"),
                        "total_elapsed_seconds": t_win,
                        "fallback_attempts": intentos_registro
                    }
                else:
                    # Falló este modelo: registrar y continuar con la carrera
                    t_fail = round(time.time() - t_global_start, 2)
                    logger.warning("Fast LLM [%ss]: modelo '%s' falló: %s. Conmutando...", t_fail, model_name, res.get('error'))
                    health_tracker.record_failure(model_name, res.get("error", ""))
                    intentos_registro.append({
                        "model": model_name,
                        "status_code": res.get("status_code"),
                        "motivo": res.get("error"),
                        "elapsed": res.get("elapsed")
                    })

    # Si se agotaron todos los modelos
    total_time = round(time.time() - t_global_start, 2)
    return {
        "error": f"Todos los modelos gratuitos fallaron o están temporalmente saturados ({total_time}s).",
        "intentos": intentos_registro
    }


# ============================================================
# COMPATIBILIDAD SÍNCRONA
# ============================================================
def _sync_chat_con_fallback(
    prompt: Union[str, List[Dict[str, Any]]],
    api_key: str,
    endpoint: str,
    modelo_preferido: Optional[str] = None,
    enable_reasoning: bool = False,
    response_format: Optional[dict] = None,
    modelos_base: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Envoltura síncrona que ejecuta el motor de conmutación rápida en un event loop.
    """
    modelos_a_probar = _obtener_lista_modelos(modelo_preferido, lista_base=modelos_base)
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        # Si ya corre un loop, correr en un hilo aislado
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                asyncio.run,
                _async_hedged_chat_fallback(
                    prompt=prompt,
                    api_key=api_key,
                    endpoint=endpoint,
                    modelos_a_probar=modelos_a_probar,
                    enable_reasoning=enable_reasoning,
                    response_format=response_format
                )
            ).result()
    else:
        return loop.run_until_complete(
            _async_hedged_chat_fallback(
                prompt=prompt,
                api_key=api_key,
                endpoint=endpoint,
                modelos_a_probar=modelos_a_probar,
                enable_reasoning=enable_reasoning,
                response_format=response_format
            )
        )


# ============================================================
# FUNCIONES PRINCIPALES EXPORTADAS (ASÍNCRONAS NATIVAS)
# ============================================================
async def chat(
    prompt: Union[str, List[Dict[str, Any]]],
    modelo_preferido: Optional[str] = None,
    enable_reasoning: bool = True,
    response_format: Optional[dict] = None
) -> Dict[str, Any]:
    """
    Función asíncrona principal de chat con conmutación rápida inteligente.
    Aprovecha el catálogo dinámico y la carrera especulativa (hedged racing)
    para garantizar respuestas ultrarrápidas (< 3-5 segundos) sin bloqueos.
    """
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"error": "OPENROUTER_API_KEY no configurada en el entorno"}

    try:
        modelos_dinamicos = await get_prioritized_free_model_ids()
    except Exception as e:
        logger.warning(f"No se pudo consultar el catálogo dinámico ({e}). Usando lista en caché.")
        modelos_dinamicos = get_cached_prioritized_models_sync()

    modelos_a_probar = _obtener_lista_modelos(modelo_preferido, lista_base=modelos_dinamicos)

    return await _async_hedged_chat_fallback(
        prompt=prompt,
        api_key=api_key,
        endpoint=OPENROUTER_ENDPOINT,
        modelos_a_probar=modelos_a_probar,
        enable_reasoning=enable_reasoning,
        response_format=response_format
    )


async def generate(prompt: str, response_format: Optional[dict] = None) -> Dict[str, Any]:
    """
    Alias de compatibilidad para generación de texto con conmutación rápida.
    """
    return await chat(prompt, enable_reasoning=True, response_format=response_format)
