# =============================================================================
# API/config.py
# -----------------------------------------------------------------------------
# Configuración centralizada del MCP HUB Server IA (FASE 0 · Agente A0.1).
#
# Objetivo:
#   - Único punto de verdad para la configuración (credenciales, endpoints,
#     timeouts, flags de servicio) del proyecto.
#   - Validación de credenciales obligatorias al arrancar con mensajes claros.
#   - Referencia versionable mediante `.env.example`.
#
# Compatibilidad:
#   Se conservan los identificadores de módulo preexistentes que otros archivos
#   importan (`ACTIVE_SERVICES`, `ARTEMIS_CONFIG`, `LLM_PROVIDERS`, `ZOOM_CONFIG`)
#   para no romper importaciones. El resto del código debe migrar a `settings`
#   (vía el objeto singleton `settings` o la función `get_settings()`).
# =============================================================================

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from typing import Any, Callable, Dict, List, Optional

from dotenv import load_dotenv

# -----------------------------------------------------------------------------
# Carga de variables de entorno
# -----------------------------------------------------------------------------
# Busca `.env` en la raíz del proyecto y en el directorio actual. `override=False`
# evita pisar variables ya presentes en el entorno del proceso (12-factor).
load_dotenv(verbose=False)
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"), override=False)

_TRUE_VALUES = {"true", "1", "yes", "on", "y"}


def _as_bool(v: Optional[str], default: bool = False) -> bool:
    """Parses a string env value into a boolean."""
    if v is None or v == "":
        return default
    return str(v).strip().lower() in _TRUE_VALUES


def _as_float(v: Optional[str], default: float) -> float:
    """Parses a string env value into a float with a safe fallback."""
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_int(v: Optional[str], default: int) -> int:
    """Parses a string env value into an int with a safe fallback."""
    if v is None or v == "":
        return default
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _as_list(v: Optional[str], default: Optional[List[str]] = None) -> List[str]:
    """Parses a comma-separated env value into a list of stripped items."""
    if not v:
        return list(default) if default is not None else []
    return [item.strip() for item in v.split(",") if item.strip()]


# =============================================================================
# Esquema de configuración tipado
# =============================================================================

@dataclass
class Settings:
    """Configuración tipada y validable de la aplicación."""

    # --- Servidor ---------------------------------------------------------
    port: int = 10000
    # Nivel de logging (CRITICAL|ERROR|WARNING|INFO|DEBUG). Default INFO.
    log_level: str = "INFO"

    # --- Persistencia / Base de datos (FASE 1 · B1.2) ---------------------
    # DSN SQLAlchemy async. SQLite y PostgreSQL comparten interfaz:
    #   SQLite:    sqlite+aiosqlite:///./storage/hub.db
    #   PostgreSQL: postgresql+asyncpg://user:pass@host:5432/db
    # Valores vacíos o "memory" → backend en memoria transitorio (dev/fallback).
    db_dsn: str = ""
    # Ruta del archivo SQLite cuando se usa DSN SQLite relativo.
    db_sqlite_path: str = "storage/hub.db"
    # Máximo de envíos evaluables por tarea-estudiante (control de crónica).
    max_submission_attempts: int = 2

    # --- Servicios activos ------------------------------------------------
    # Nombres de servicio que el HUB expone. Usado para validación selectiva.
    active_services: List[str] = field(
        default_factory=lambda: [
            "echo_service",
            "llm_chat_service",
            "zoom_reports",
            "zoom_management_service",
            "zoom_license_management",
            "essay_evaluator_mcp",
        ]
    )

    # --- OpenRouter / LLM -------------------------------------------------
    openrouter_api_key: str = ""
    openrouter_endpoint: str = "https://openrouter.ai/api/v1/chat/completions"
    openrouter_models_endpoint: str = "https://openrouter.ai/api/v1/models"
    preferred_free_model: str = ""
    llm_soft_timeout: float = 3.5
    llm_hard_timeout: float = 12.0
    llm_max_concurrent: int = 2
    openrouter_models_cache_ttl: int = 1800

    # --- Proveedores LLM específicos --------------------------------------
    deepseek_api_key: str = ""
    deepseek_endpoint: str = ""
    openrouter_model: str = "deepseek/deepseek-chat-v3-0324"

    gemini_api_key: str = ""
    gemini_endpoint: str = ""
    gemma_model: str = "google/gemma-4-31b-it:free"

    openai_api_key: str = ""
    openai_endpoint: str = ""

    # --- Ollama (On-Premise / local) --------------------------------------
    ollama_endpoint: str = "http://localhost:11434"
    ollama_model: str = "llama3.3:latest"
    ollama_timeout: float = 60.0

    # --- Zoom ---------------------------------------------------------------
    zoom_account_id: str = ""
    zoom_client_id: str = ""
    zoom_client_secret: str = ""
    zoom_base_url: str = "https://api.zoom.us/v2"
    zoom_header_image: str = ""

    # --- Canvas LMS ---------------------------------------------------------
    canvas_api_url: str = ""
    canvas_api_token: str = ""

    # --- ActiveMQ Artemis (STOMP) -----------------------------------------
    artemis_enabled: bool = False
    artemis_host: str = "localhost"
    artemis_port: int = 61613
    artemis_user: str = "artemis"
    artemis_password: str = "artemis"
    artemis_in_queue: str = "/queue/lms.evaluaciones.in"
    artemis_out_queue: str = "/queue/lms.calificaciones.out"
    artemis_selector: str = "tipo = 'calificar ensayos IA'"

    # --- Otros ---------------------------------------------------------------
    lms_webhook_url: str = ""

    # =========================================================================
    # Métodos de conveniencia
    # =========================================================================

    def is_artemis_enabled(self) -> bool:
        return self.artemis_enabled


def _resolve_db_dsn() -> str:
    """
    Resuelve la DSN de la base de datos con precedencia:
      1) DATABASE_URL  (estándar 12-factor, típico en Azure/Cloud)
      2) DB_DSN        (legacy del proyecto)
      3) ''            → SQLite local (storage/hub.db) vía los backends por defecto

    Esto permite usar SQLite en el sandbox local y PostgreSQL en la nube sin
    tocar código: solo se define la variable correspondiente en el entorno.
    """
    g = os.getenv
    return (g("DATABASE_URL") or g("DB_DSN") or "").strip()


def _load_settings() -> Settings:
    """Construye un Settings leyendo las variables de entorno (con defaults)."""
    g = os.getenv

    s = Settings(
        port=_as_int(g("PORT"), 10000),
        log_level=(g("LOG_LEVEL") or "INFO").strip().upper(),
        db_dsn=_resolve_db_dsn(),
        db_sqlite_path=(g("DB_SQLITE_PATH") or "storage/hub.db").strip(),
        max_submission_attempts=_as_int(g("MAX_SUBMISSION_ATTEMPTS"), 2),
        active_services=_as_list(g("ACTIVE_SERVICES")) or [
            "echo_service",
            "llm_chat_service",
            "zoom_reports",
            "zoom_management_service",
            "zoom_license_management",
            "essay_evaluator_mcp",
        ],
        # OpenRouter / LLM
        openrouter_api_key=g("OPENROUTER_API_KEY", ""),
        openrouter_endpoint=g("OPENROUTER_ENDPOINT", "https://openrouter.ai/api/v1/chat/completions"),
        openrouter_models_endpoint=g("OPENROUTER_MODELS_ENDPOINT", "https://openrouter.ai/api/v1/models"),
        preferred_free_model=g("PREFERRED_FREE_MODEL", ""),
        llm_soft_timeout=_as_float(g("LLM_SOFT_TIMEOUT"), 3.5),
        llm_hard_timeout=_as_float(g("LLM_HARD_TIMEOUT"), 12.0),
        llm_max_concurrent=_as_int(g("LLM_MAX_CONCURRENT"), 2),
        openrouter_models_cache_ttl=_as_int(g("OPENROUTER_MODELS_CACHE_TTL"), 1800),
        # Proveedores LLM específicos
        deepseek_api_key=g("DEEPSEEK_API_KEY", ""),
        deepseek_endpoint=g("DEEPSEEK_ENDPOINT", ""),
        openrouter_model=g("OPENROUTER_MODEL", "deepseek/deepseek-chat-v3-0324"),
        gemini_api_key=g("GEMINI_API_KEY", ""),
        gemini_endpoint=g("GEMINI_ENDPOINT", ""),
        gemma_model=g("GEMMA_MODEL", g("GEMINI_MODEL", "google/gemma-4-31b-it:free")),
        openai_api_key=g("OPENAI_API_KEY", ""),
        openai_endpoint=g("OPENAI_ENDPOINT", ""),
        # Ollama
        ollama_endpoint=g("OLLAMA_ENDPOINT", "http://localhost:11434").rstrip("/"),
        ollama_model=g("OLLAMA_MODEL", "llama3.3:latest"),
        ollama_timeout=_as_float(g("OLLAMA_TIMEOUT"), 60.0),
        # Zoom
        zoom_account_id=g("ZOOM_ACCOUNT_ID", ""),
        zoom_client_id=g("ZOOM_CLIENT_ID", ""),
        zoom_client_secret=g("ZOOM_CLIENT_SECRET", ""),
        zoom_base_url=g("ZOOM_BASE_URL", "https://api.zoom.us/v2"),
        zoom_header_image=g("ZOOM_HEADER_IMAGE", ""),
        # Canvas
        canvas_api_url=g("CANVAS_API_URL", ""),
        canvas_api_token=g("CANVAS_API_TOKEN", ""),
        # Artemis
        artemis_enabled=_as_bool(g("ARTEMIS_ENABLED"), False),
        artemis_host=g("ARTEMIS_HOST", "localhost"),
        artemis_port=_as_int(g("ARTEMIS_PORT"), 61613),
        artemis_user=g("ARTEMIS_USER", "artemis"),
        artemis_password=g("ARTEMIS_PASSWORD", "artemis"),
        artemis_in_queue=g("ARTEMIS_IN_QUEUE", "/queue/lms.evaluaciones.in"),
        artemis_out_queue=g("ARTEMIS_OUT_QUEUE", "/queue/lms.calificaciones.out"),
        artemis_selector="tipo = 'calificar ensayos IA'",
        lms_webhook_url=g("LMS_WEBHOOK_URL", ""),
    )
    return s


# =============================================================================
# Singleton de configuración
# =============================================================================

settings = _load_settings()


def get_settings() -> Settings:
    """
    Devuelve la configuración compartida de la aplicación.

    Uso recomendado:
        from API.config import get_settings
        cfg = get_settings()
        cfg.openrouter_api_key
    """
    return settings


def reload_settings() -> Settings:
    """
    Reconstruye `settings` desde el entorno. Útil en tests o tras editar `.env`.
    Devuelve la nueva instancia y actualiza el singleton global.
    """
    global settings
    settings = _load_settings()
    return settings


# =============================================================================
# Definción de credenciales obligatorias por servicio
# =============================================================================

_SERVICE_REQUIRED: Dict[str, List[str]] = {
    # Servicio → lista de atributos de `Settings` (claves de credencial) exigidos.
    "llm": ["openrouter_api_key"],
    "zoom": ["zoom_account_id", "zoom_client_id", "zoom_client_secret"],
    "canvas": ["canvas_api_url", "canvas_api_token"],
    # "artemis" se valida solo si está habilitado (usa user/password con defaults).
}

# Descripciones legibles por variable, para reportes de error al operador.
_VAR_LABEL: Dict[str, str] = {
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "zoom_account_id": "ZOOM_ACCOUNT_ID",
    "zoom_client_id": "ZOOM_CLIENT_ID",
    "zoom_client_secret": "ZOOM_CLIENT_SECRET",
    "canvas_api_url": "CANVAS_API_URL",
    "canvas_api_token": "CANVAS_API_TOKEN",
}


def validate_credentials(cfg: Optional[Settings] = None, strict: bool = False) -> List[str]:
    """
    Valida credenciales obligatorias según los servicios activos.

    Args:
        cfg:   Instancia Settings; si es None usa el singleton `settings`.
        strict: Si True exige todas las credenciales definidas en `_SERVICE_REQUIRED`
                (llm, zoom, canvas), independientemente de qué servicios estén
                activos. Si False solo exige la clave de OpenRouter (necesaria para
                el motor LLM). Artemis nunca se exige aquí (usa su flag `enabled`).

    Returns:
        Lista de mensajes (str) con cada credencial ausente. Vacía si todo OK.
    """
    cfg = cfg or settings
    missing: List[str] = []

    required = _SERVICE_REQUIRED if strict else {"llm": _SERVICE_REQUIRED["llm"]}

    for svc, attrs in required.items():
        for attr in attrs:
            if not getattr(cfg, attr, ""):
                missing.append(
                    f"{_VAR_LABEL.get(attr, attr)} requerida para el servicio '{svc}'."
                )

    return missing


def validate_zoom(cfg: Optional[Settings] = None) -> List[str]:
    """Validación específica de credenciales Zoom (mensajes accionables)."""
    cfg = cfg or settings
    return [
        f"{_VAR_LABEL.get(a, a)} faltante para integración Zoom."
        for a in _SERVICE_REQUIRED["zoom"]
        if not getattr(cfg, a, "")
    ]


def validate_artemis(cfg: Optional[Settings] = None) -> List[str]:
    """Validación de config Artemis si está habilitado (usa credenciales con defaults)."""
    cfg = cfg or settings
    if not cfg.artemis_enabled:
        return []
    issues: List[str] = []
    if not cfg.artemis_host:
        issues.append("ARTEMIS_HOST vacío con ARTEMIS_ENABLED=true.")
    if not cfg.artemis_in_queue or not cfg.artemis_out_queue:
        issues.append("Colas de Artemis (in/out) no definidas con ARTEMIS_ENABLED=true.")
    return issues


# =============================================================================
# Compatibilidad con el esquema de configuración preexistente
# -----------------------------------------------------------------------------
# Estas constantes de módulo se conservan para NO romper imports actuales
# (p.ej. `from API.config import ZOOM_CONFIG` en zoom_license_management.py).
# Código nuevo: usar `settings` / `get_settings()`.
# =============================================================================

ACTIVE_SERVICES = list(settings.active_services)

ARTEMIS_CONFIG = {
    "enabled": settings.artemis_enabled,
    "host": settings.artemis_host,
    "port": settings.artemis_port,
    "user": settings.artemis_user,
    "password": settings.artemis_password,
    "in_queue": settings.artemis_in_queue,
    "out_queue": settings.artemis_out_queue,
    "selector": settings.artemis_selector,
}

LLM_PROVIDERS = {
    "deepseek": {
        "api_key": settings.deepseek_api_key,
        "endpoint": settings.deepseek_endpoint,
    },
    "gemini": {
        "api_key": settings.gemini_api_key,
        "endpoint": settings.gemini_endpoint,
    },
    "openai": {
        "api_key": settings.openai_api_key,
        "endpoint": settings.openai_endpoint,
    },
}

ZOOM_CONFIG = {
    "account_id": settings.zoom_account_id,
    "client_id": settings.zoom_client_id,
    "client_secret": settings.zoom_client_secret,
    "base_url": settings.zoom_base_url,
}