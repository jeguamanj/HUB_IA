# API/config.py
import os
from dataclasses import dataclass, field
from typing import Optional

# 1) Carga .env local (no pisa variables de entorno reales)
from dotenv import load_dotenv
load_dotenv(override=False)


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("Invalid boolean value")


# =============================================================================
# Esquema de configuración tipado
# =============================================================================
@dataclass
class Settings:
    """Configuración tipada y validable de la aplicación."""

    # ============================================================
    # SERVIDOR
    # ============================================================
    port: int = 10000
    log_level: str = "INFO"
    hub_api_token: str = field(default_factory=lambda: os.getenv("HUB_API_TOKEN", ""))
    hub_docs_username: str = field(default_factory=lambda: os.getenv("HUB_DOCS_USERNAME", ""))
    hub_docs_password: str = field(default_factory=lambda: os.getenv("HUB_DOCS_PASSWORD", ""))

    # ============================================================
    # PERSISTENCIA / BASE DE DATOS
    # ============================================================
    # DSN SQLAlchemy async. DB_DSN es canónica; DATABASE_URL es alias.
    # Ejemplos: SQLite local o PostgreSQL async con asyncpg.
    #   sqlite+aiosqlite:///./storage/hub.db
    #   postgresql+asyncpg://user:pass@host:5432/db?sslmode=require
    # Vacío → backend en memoria (transitorio).
    db_dsn: str = ""

    # Ruta del archivo SQLite cuando se usa DSN SQLite relativo.
    db_sqlite_path: str = "storage/hub.db"

    # Máximo de envíos evaluables por tarea-estudiante (control de crónica).
    max_submission_attempts: int = 2
    # Días que un contexto LMS permanece disponible después del cierre de la tarea.
    lms_context_grace_days: int = 3

    # ============================================================
    # PROVEEDORES LLM / SERVICIOS EXTERNOS
    # ============================================================
    openrouter_api_key: str = ""
    zoom_client_id: str = ""
    zoom_client_secret: str = ""
    zoom_account_id: str = ""
    canvas_api_url: str = ""
    canvas_api_token: str = ""
    ollama_endpoint: str = "http://localhost:11434"
    ollama_model: str = "llama3.3:latest"
    ollama_timeout: float = 60.0
    llm_soft_timeout: float = 3.5
    llm_hard_timeout: float = 12.0
    llm_max_concurrent: int = 2
    openrouter_models_cache_ttl: int = 1800

    # ============================================================
    # ACTIVEMQ ARTEMIS
    # ============================================================
    artemis_enabled: bool = False
    artemis_host: str = "localhost"
    artemis_port: int = 61613
    artemis_user: str = "artemis"
    artemis_password: str = "artemis"
    artemis_in_queue: str = "/queue/lms.evaluaciones.in"
    artemis_out_queue: str = "/queue/lms.calificaciones.out"
    artemis_selector: str = "tipo = 'calificar ensayos IA'"

    # ============================================================
    # LOGGING
    # ============================================================
    log_level: str = "INFO"

    # ============================================================
    # CAMPOS INTERNOS / COMPATIBILIDAD
    # ============================================================
    # Compatibilidad hacia atrás (código antiguo usa estos nombres)
    db_sqlite_path: str = "storage/hub.db"
    max_submission_attempts: int = 2

    # Alias de compatibilidad (código antiguo usa estos nombres)
    openrouter_api_key: str = ""
    zoom_client_id: str = ""
    zoom_client_secret: str = ""
    zoom_account_id: str = ""
    canvas_api_url: str = ""
    canvas_api_token: str = ""
    ollama_endpoint: str = "http://localhost:11434"
    ollama_model: str = "llama3.3:latest"
    ollama_timeout: float = 60.0

    # Artemis
    artemis_enabled: bool = field(default=False, init=False)
    artemis_host: str = field(default="localhost", init=False)
    artemis_port: int = field(default=61613, init=False)
    artemis_user: str = field(default="artemis", init=False)
    artemis_password: str = field(default="artemis", init=False)
    artemis_in_queue: str = field(default="/queue/lms.evaluaciones.in", init=False)
    artemis_out_queue: str = field(default="/queue/lms.calificaciones.out", init=False)
    artemis_selector: str = field(default="tipo = 'calificar ensayos IA'", init=False)

    # Logging
    log_level: str = field(default="INFO", init=False)

    # ============================================================
    # POST-INIT: Carga variables de entorno (12-factor) SOBRE los defaults
    # ============================================================================
    def __post_init__(self):
        # DB_DSN es canónica; DATABASE_URL se conserva como alias.
        db_dsn = os.getenv("DB_DSN")
        if db_dsn is None:
            db_dsn = os.getenv("DATABASE_URL")
        if db_dsn is not None:
            self.db_dsn = db_dsn

        # Puerto (Azure inyecta PORT=8000)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Log level
        log_level = os.getenv("LOG_LEVEL")
        if log_level:
            self.log_level = log_level.upper()

        # Máximo de intentos (sobrescribe global si viene en env)
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        grace_days = os.getenv("LMS_CONTEXT_GRACE_DAYS")
        if grace_days is not None:
            try:
                parsed_grace_days = int(grace_days)
            except ValueError as exc:
                raise ValueError("LMS_CONTEXT_GRACE_DAYS debe ser un entero entre 0 y 365.") from exc
            if not 0 <= parsed_grace_days <= 365:
                raise ValueError("LMS_CONTEXT_GRACE_DAYS debe estar entre 0 y 365.")
            self.lms_context_grace_days = parsed_grace_days

        for env_name, field_name, parser in (
            ("LLM_SOFT_TIMEOUT", "llm_soft_timeout", float),
            ("LLM_HARD_TIMEOUT", "llm_hard_timeout", float),
            ("LLM_MAX_CONCURRENT", "llm_max_concurrent", int),
            ("OPENROUTER_MODELS_CACHE_TTL", "openrouter_models_cache_ttl", int),
        ):
            value = os.getenv(env_name)
            if value is not None:
                try:
                    setattr(self, field_name, parser(value))
                except ValueError:
                    pass

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)


# =============================================================================
# CAMPOS DE COMPATIBILIDAD (código antiguo usa estos nombres)
# =============================================================================
    # --- Compatibilidad: db_dsn como campo público ---
    # Compatibilidad hacia atrás (código antiguo usa estos nombres)
    db_sqlite_path: str = "storage/hub.db"
    max_submission_attempts: int = 2

    # Alias de compatibilidad (código antiguo usa estos nombres)
    openrouter_api_key: str = ""
    zoom_client_id: str = ""
    zoom_client_secret: str = ""
    zoom_account_id: str = ""
    canvas_api_url: str = ""
    canvas_api_token: str = ""
    ollama_endpoint: str = "http://localhost:11434"
    ollama_model: str = "llama3.3:latest"
    ollama_timeout: float = 60.0

    # Artemis
    artemis_enabled: bool = field(default=False, init=False)
    artemis_host: str = field(default="localhost", init=False)
    artemis_port: int = field(default=61613, init=False)
    artemis_user: str = field(default="artemis", init=False)
    artemis_password: str = field(default="artemis", init=False)
    artemis_in_queue: str = field(default="/queue/lms.evaluaciones.in", init=False)
    artemis_out_queue: str = field(default="/queue/lms.calificaciones.out", init=False)
    artemis_selector: str = field(default="tipo = 'calificar ensayos IA'", init=False)

    # Logging
    log_level: str = field(default="INFO", init=False)

    # ============================================================
    # POST-INIT: Carga variables de entorno (12-factor) SOBRE los defaults
    # ============================================================================
    def __post_init__(self):
        # DB_DSN es canónica; DATABASE_URL se conserva como alias.
        db_dsn = os.getenv("DB_DSN")
        if db_dsn is None:
            db_dsn = os.getenv("DATABASE_URL")
        if db_dsn is not None:
            self.db_dsn = db_dsn

        # Puerto (Azure inyecta PORT=8000)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Log level
        log_level = os.getenv("LOG_LEVEL")
        if log_level:
            self.log_level = log_level.upper()

        # Máximo de intentos (sobrescribe global si viene en env)
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)


# =============================================================================
# CAMPOS DE COMPATIBILIDAD (código antiguo usa estos nombres)
# =============================================================================
    # --- Compatibilidad: db_dsn como campo público ---
    # Compatibilidad hacia atrás (código antiguo usa estos nombres)
    db_sqlite_path: str = "storage/hub.db"
    max_submission_attempts: int = 2

    # Alias de compatibilidad (código antiguo usa estos nombres)
    openrouter_api_key: str = ""
    zoom_client_id: str = ""
    zoom_client_secret: str = ""
    zoom_account_id: str = ""
    canvas_api_url: str = ""
    canvas_api_token: str = ""
    ollama_endpoint: str = "http://localhost:11434"
    ollama_model: str = "llama3.3:latest"
    ollama_timeout: float = 60.0

    # Artemis
    artemis_enabled: bool = field(default=False, init=False)
    artemis_host: str = field(default="localhost", init=False)
    artemis_port: int = field(default=61613, init=False)
    artemis_user: str = field(default="artemis", init=False)
    artemis_password: str = field(default="artemis", init=False)
    artemis_in_queue: str = field(default="/queue/lms.evaluaciones.in", init=False)
    artemis_out_queue: str = field(default="/queue/lms.calificaciones.out", init=False)
    artemis_selector: str = field(default="tipo = 'calificar ensayos IA'", init=False)

    # Logging
    log_level: str = field(default="INFO", init=False)

    # ============================================================
    # POST-INIT: Carga variables de entorno (12-factor) SOBRE los defaults
    # ============================================================================
    def __post_init__(self):
        # DB_DSN es canónica; DATABASE_URL se conserva como alias.
        db_dsn = os.getenv("DB_DSN")
        if db_dsn is None:
            db_dsn = os.getenv("DATABASE_URL")
        if db_dsn is not None:
            self.db_dsn = db_dsn

        # Puerto (Azure inyecta PORT=8000)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Log level
        log_level = os.getenv("LOG_LEVEL")
        if log_level:
            self.log_level = log_level.upper()

        # Máximo de intentos (sobrescribe global si viene en env)
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        # Sobrescribir max_submission_attempts si viene en env
        max_attempts = os.getenv("MAX_SUBMISSION_ATTEMPTS")
        if max_attempts and max_attempts.isdigit():
            self.max_submission_attempts = int(max_attempts)

        # Log level (alias)
        log_level_env = os.getenv("LOG_LEVEL")
        if log_level_env:
            self.log_level = log_level_env.upper()

        # Puerto (alias PORT)
        port_env = os.getenv("PORT")
        if port_env and port_env.isdigit():
            self.port = int(port_env)

        # Variables específicas de proveedores (opcional, para comodidad)
        for key in (
            "openrouter_api_key", "zoom_client_id", "zoom_client_secret",
            "zoom_account_id", "canvas_api_url", "canvas_api_token",
            "ollama_endpoint", "ollama_model", "ollama_timeout",
            "artemis_enabled", "artemis_host", "artemis_port",
            "artemis_user", "artemis_password", "artemis_in_queue",
            "artemis_out_queue", "artemis_selector",
        ):
            val = os.getenv(key.upper())
            if val is not None:
                object.__setattr__(self, key, val)

        for env_name, field_name, parser in (
            ("LLM_SOFT_TIMEOUT", "llm_soft_timeout", float),
            ("LLM_HARD_TIMEOUT", "llm_hard_timeout", float),
            ("LLM_MAX_CONCURRENT", "llm_max_concurrent", int),
            ("OPENROUTER_MODELS_CACHE_TTL", "openrouter_models_cache_ttl", int),
        ):
            value = os.getenv(env_name)
            if value is not None:
                try:
                    setattr(self, field_name, parser(value))
                except ValueError:
                    pass

        for env_name, field_name, parser in (
            ("OLLAMA_TIMEOUT", "ollama_timeout", float),
            ("ARTEMIS_ENABLED", "artemis_enabled", _parse_bool),
            ("ARTEMIS_PORT", "artemis_port", int),
        ):
            value = os.getenv(env_name)
            if value is not None:
                try:
                    setattr(self, field_name, parser(value))
                except ValueError:
                    pass



# =============================================================================
# INSTANCIA GLOBAL (singleton)
# =============================================================================
settings = Settings()


ZOOM_CONFIG = {
    "base_url": "https://api.zoom.us/v2",
    "account_id": settings.zoom_account_id,
    "client_id": settings.zoom_client_id,
    "client_secret": settings.zoom_client_secret,
}
LLM_PROVIDERS = {
    "openrouter": settings.openrouter_api_key,
    "openai": os.getenv("OPENAI_API_KEY", ""),
    "ollama": settings.ollama_endpoint,
}
ACTIVE_SERVICES = {"context": True, "llm": True, "zoom": True, "essays": True}


def get_settings() -> Settings:
    """Return the process-wide application settings."""
    return settings


def reload_settings() -> Settings:
    global settings, ZOOM_CONFIG, LLM_PROVIDERS
    settings = Settings()
    ZOOM_CONFIG = {
        "base_url": "https://api.zoom.us/v2",
        "account_id": settings.zoom_account_id,
        "client_id": settings.zoom_client_id,
        "client_secret": settings.zoom_client_secret,
    }
    LLM_PROVIDERS = {
        "openrouter": settings.openrouter_api_key,
        "openai": os.getenv("OPENAI_API_KEY", ""),
        "ollama": settings.ollama_endpoint,
    }
    return settings


def validate_credentials(cfg: Optional[Settings] = None, strict: bool = False) -> list[str]:
    """Report required credentials while allowing the server to start without them."""
    cfg = cfg or settings
    missing = []
    if not cfg.openrouter_api_key:
        missing.append("OPENROUTER_API_KEY requerida para el servicio 'llm'.")
    if strict:
        for name, label in (
            ("zoom_account_id", "ZOOM_ACCOUNT_ID"),
            ("zoom_client_id", "ZOOM_CLIENT_ID"),
            ("zoom_client_secret", "ZOOM_CLIENT_SECRET"),
            ("canvas_api_url", "CANVAS_API_URL"),
            ("canvas_api_token", "CANVAS_API_TOKEN"),
        ):
            if not getattr(cfg, name, ""):
                missing.append(f"{label} requerida para la integración correspondiente.")
    return missing


def validate_zoom(cfg: Optional[Settings] = None) -> list[str]:
    cfg = cfg or settings
    return [
        f"{name} requerida para Zoom."
        for name, value in (
            ("ZOOM_ACCOUNT_ID", cfg.zoom_account_id),
            ("ZOOM_CLIENT_ID", cfg.zoom_client_id),
            ("ZOOM_CLIENT_SECRET", cfg.zoom_client_secret),
        )
        if not value
    ]


def validate_production_configuration(cfg: Optional[Settings] = None) -> list[str]:
    """Return production-only security/configuration errors without exposing secrets."""
    cfg = cfg or settings
    errors = []
    client_auth_enabled = os.getenv("API_CLIENT_AUTH_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not client_auth_enabled:
        errors.append("API_CLIENT_AUTH_ENABLED debe estar habilitado en producción.")
    if not cfg.hub_docs_username or len(cfg.hub_docs_password) < 16:
        errors.append(
            "HUB_DOCS_USERNAME y una contraseña HUB_DOCS_PASSWORD de al menos 16 caracteres son obligatorios."
        )
    if not cfg.db_dsn.strip().lower().startswith("postgresql+asyncpg://"):
        errors.append("DB_DSN debe usar PostgreSQL async para producción.")
    if not os.getenv("ATTACHMENT_ALLOWED_HOSTS", "").strip():
        errors.append("ATTACHMENT_ALLOWED_HOSTS debe declarar los dominios de LMS/CDN permitidos.")
    return errors


# =============================================================================
# COMPATIBILIDAD HACIA ATRÁS (código antiguo usa estos nombres)
# =============================================================================
# Estos campos se mantienen como propiedades de compatibilidad
# para que `settings.db_dsn`, `settings.max_submission_attempts`, etc.
# sigan funcionando sin cambios en el código existente.
# =============================================================================

# --- Compatibilidad: db_dsn como campo público ---

# --- Compatibilidad: campos planos ---
# (los campos planos ya están definidos arriba con field(default=..., init=False))
# max_submission_attempts, db_sqlite_path, etc.

# --- Alias de compatibilidad (código antiguo usa estos nombres) ---
# (los campos planos ya están definidos arriba con field(default=..., init=False))
# max_submission_attempts, db_sqlite_path, etc.

# =============================================================================
# EXPORT
# =============================================================================
__all__ = [
    "Settings",
    "settings",
    "ZOOM_CONFIG",
    "LLM_PROVIDERS",
    "ACTIVE_SERVICES",
    "get_settings",
    "reload_settings",
    "validate_credentials",
    "validate_zoom",
    "validate_production_configuration",
]