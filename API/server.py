import contextlib
import os
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

import sys
from pathlib import Path

# Asegurar que el directorio raíz y el directorio API estén en sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
API_DIR = Path(__file__).resolve().parent
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

# Importar routers de servicios
try:
    from API.services.echo_service import get_router as get_echo_router
    from API.services.llm_chat_service import get_router as get_llm_chat_router
    from API.services.zoom_reports import get_router as get_zoom_reports_router
    from API.services.zoom_management_service import get_router as get_zoom_management_router
    from API.services.zoom_license_management import get_router as get_zoom_license_router
    from API.services.essay_evaluator_mcp import get_router as get_essay_evaluator_router
    from API.services.context_service import get_router as get_context_router
    from API.services.artemis_adapter import artemis_service
    from API.core.context_engine import InstitutionalContextMiddleware
    from API.core.api_auth import (
        BearerTokenMiddleware,
        DocsBasicAuthMiddleware,
        get_api_token,
        client_token_auth_enabled,
        get_docs_credentials,
        require_api_token,
    )
    from API.core.client_tokens import ClientTokenRepository
except ImportError:
    # pyrefly: ignore [missing-import]
    from services.echo_service import get_router as get_echo_router
    # pyrefly: ignore [missing-import]
    from services.llm_chat_service import get_router as get_llm_chat_router
    # pyrefly: ignore [missing-import]
    from services.zoom_reports import get_router as get_zoom_reports_router
    # pyrefly: ignore [missing-import]
    from services.zoom_management_service import get_router as get_zoom_management_router
    # pyrefly: ignore [missing-import]
    from services.zoom_license_management import get_router as get_zoom_license_router
    # pyrefly: ignore [missing-import]
    from services.essay_evaluator_mcp import get_router as get_essay_evaluator_router
    # pyrefly: ignore [missing-import]
    from services.context_service import get_router as get_context_router
    # pyrefly: ignore [missing-import]
    from services.artemis_adapter import artemis_service
    # pyrefly: ignore [missing-import]
    from core.context_engine import InstitutionalContextMiddleware
    # pyrefly: ignore [missing-import]
    from core.api_auth import (
        BearerTokenMiddleware,
        DocsBasicAuthMiddleware,
        get_api_token,
        client_token_auth_enabled,
        get_docs_credentials,
        require_api_token,
    )
    # pyrefly: ignore [missing-import]
    from core.client_tokens import ClientTokenRepository

# Configuración centralizada (FASE 0 · A0.1)
try:
    from API.config import (
        get_settings,
        validate_credentials,
        validate_production_configuration,
    )
except ImportError:
    # pyrefly: ignore [missing-import]
    from config import (
        get_settings,
        validate_credentials,
        validate_production_configuration,
    )

# Logging centralizado (FASE 0 · A0.3)
try:
    from API.core.logging import setup_logging, get_logger
except ImportError:
    # pyrefly: ignore [missing-import]
    from core.logging import setup_logging, get_logger

# Persistencia durable del contexto (FASE 1 · B1.2)
try:
    from API.core.context_engine import init_durable_store
except ImportError:
    # pyrefly: ignore [missing-import]
    from core.context_engine import init_durable_store

# Control de envíos y crónica de evaluaciones (FASE 3 · afinar MCP ensayos)
try:
    from API.core.submission_tracker import init_submission_tracker
except ImportError:
    # pyrefly: ignore [missing-import]
    from core.submission_tracker import init_submission_tracker

# Cola de evaluaciones asíncronas + worker (FASE 3)
try:
    from API.core.evaluation_queue import init_evaluation_queue
except ImportError:
    # pyrefly: ignore [missing-import]
    from core.evaluation_queue import init_evaluation_queue

# Inicializar logging ANTES de cualquier registro (reentrante, no duplica handlers).
setup_logging()
logger = get_logger("server")

settings = get_settings()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Contexto de vida útil de la aplicación.
    Inicializa servicios y workers (ej. ActiveMQ Artemis si está activo).
    """
    logger.info("Iniciando el servidor MCP...")
    app.state.ready = False
    if os.getenv("APP_ENV", "").strip().lower() in {"prod", "production"}:
        production_errors = validate_production_configuration(settings)
        if production_errors:
            raise RuntimeError(
                "Configuración de producción incompleta: "
                + "; ".join(production_errors)
            )
    if not settings.hub_api_token and not client_token_auth_enabled():
        logger.error(
            "HUB_API_TOKEN no configurado; las rutas protegidas responderán HTTP 503."
        )

    # Reporte de credenciales obligatorias al arrancar (no bloquea el boot).
    missing = validate_credentials()
    if missing:
        logger.warning("Configuración incompleta — credenciales faltantes:")
        for msg in missing:
            logger.warning("    - %s", msg)
        logger.warning("Revisa '.env' (usa '.env.example' como referencia).")
    else:
        logger.info("Configuración de credenciales correcta.")

    store = None
    tracker = None

    # El esquema de evaluaciones se administra con migraciones SQL versionadas.
    # Un fallo de PostgreSQL detiene el arranque para no aceptar trabajos en memoria.
    from API.core.postgres_migrations import (
        initialize_evaluation_schema,
        is_postgres_dsn,
        verify_schema_current,
    )
    if os.getenv("APP_ENV", "").strip().lower() in {"prod", "production"}:
        await verify_schema_current(settings.db_dsn)
    else:
        await initialize_evaluation_schema(settings.db_dsn)

    api_client_repository = None
    if client_token_auth_enabled():
        api_client_repository = ClientTokenRepository(settings.db_dsn)
        await api_client_repository.open()
        app.state.api_client_repository = api_client_repository

    # Persistencia durable del ContextStore (SQLite/Postgres/memoria según DB_DSN)
    try:
        store = init_durable_store(settings.db_dsn)
        logger.info("ContextStore durable: backend=%s", getattr(store.backend, "name", "memoria"))
    except Exception as exc:  # nunca impedir el arranque por persistencia
        logger.warning("No se pudo inicializar la persistencia del contexto: %s", exc)

    # Control de envíos evaluables: mismo DSN + límite de intentos.
    try:
        tracker = init_submission_tracker(settings.db_dsn, max_attempts=settings.max_submission_attempts)
        await tracker.initialize()
        logger.info(
            "SubmissionTracker activo: backend=%s | max_intentos=%s",
            getattr(tracker.backend, "name", "memoria"),
            tracker.max_attempts,
        )
    except Exception as exc:
        logger.warning("No se pudo inicializar el control de envíos: %s", exc)

    # Cola de evaluaciones asíncronas: inicia backend (mismo DSN) + worker.
    manager = None
    try:
        # El evaluador (pipeline de ensayo) se registra desde el módulo de ensayos
        # para evitar ciclos de import.
        from API.services.essay_evaluator_mcp import init_async_evaluator
        manager = init_evaluation_queue(settings.db_dsn)
        await manager.initialize()
        init_async_evaluator()   # asocia `_evaluar_async_pipeline` al worker
        manager.start_worker()
        logger.info(
            "Cola de evaluaciones asíncrona activa: backend=%s | worker=1x1",
            getattr(manager.backend, "name", "memoria"),
        )
    except Exception as exc:
        if is_postgres_dsn(settings.db_dsn):
            raise RuntimeError(
                "PostgreSQL configurado, pero la cola durable no pudo iniciar"
            ) from exc
        logger.warning("No se pudo inicializar la cola de evaluaciones asíncrona: %s", exc)

    # Iniciar consumidor de cola Artemis si está configurado
    artemis_service.start()
    app.state.ready = True
    yield
    app.state.ready = False
    logger.info("Cerrando el servidor MCP...")
    from API.utils.zoom_api_utils import close_async_zoom_client

    await close_async_zoom_client()
    if manager is not None:
        try:
            await manager.stop_worker()
        except Exception as exc:
            logger.warning("Error al detener worker de evaluaciones: %s", exc)
    if tracker is not None:
        try:
            await tracker.close()
        except Exception as exc:
            logger.warning("Error al cerrar SubmissionTracker: %s", exc)
    if store is not None:
        try:
            await store.close()
        except Exception as exc:
            logger.warning("Error al cerrar ContextStore: %s", exc)
    if api_client_repository is not None:
        await api_client_repository.close()
        app.state.api_client_repository = None
    artemis_service.stop()

app = FastAPI(
    title="MCP HUB Server",
    description="Servidor centralizado para gestionar múltiples servicios MCP y Core de IA Institucional.",
    version="1.0.0",
    lifespan=lifespan
)

# Middleware de propagación transparente de contexto institucional
app.add_middleware(InstitutionalContextMiddleware)

# La dependencia en routers documenta Bearer en OpenAPI; el middleware exige
# autenticación global y deja público únicamente el healthcheck GET.
app.add_middleware(
    BearerTokenMiddleware,
    token_provider=get_api_token,
    client_auth_enabled=client_token_auth_enabled,
    public_paths=frozenset({
        ("GET", "/health"),
        ("GET", "/ready"),
        ("GET", "/docs"),
        ("GET", "/docs/"),
        ("GET", "/docs/oauth2-redirect"),
        ("GET", "/redoc"),
        ("GET", "/redoc/"),
        ("GET", "/openapi.json"),
    }),
)
app.add_middleware(
    DocsBasicAuthMiddleware,
    credentials_provider=get_docs_credentials,
)

# Healthcheck mínimo y público para balanceadores y monitoreo.
@app.get("/health", tags=["Health"])
async def healthcheck():
    return {"status": "ok"}


@app.get("/ready", tags=["Health"])
async def readinesscheck():
    if not getattr(app.state, "ready", False):
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return {"status": "ready"}

# Montar los routers de los servicios
protected = [Depends(require_api_token)]
app.include_router(get_context_router(), prefix="/context", tags=["Contexto Institucional"], dependencies=protected)
app.include_router(get_echo_router(), prefix="/echo", tags=["Echo"], dependencies=protected)
app.include_router(get_llm_chat_router(), prefix="/llm", tags=["LLM Chat"], dependencies=protected)
app.include_router(get_zoom_reports_router(), prefix="/api", tags=["Zoom Reports"], dependencies=protected)
app.include_router(get_zoom_management_router(), prefix="/zoom/manage", tags=["Zoom Management"], dependencies=protected)
app.include_router(get_zoom_license_router(), prefix="/zoom/licenses", tags=["Zoom License Management"], dependencies=protected)
app.include_router(get_essay_evaluator_router(), prefix="/ensayos", tags=["Evaluador Ensayos"], dependencies=protected)


PORT = settings.port

if __name__ == "__main__":
    import uvicorn
    logger.info("Iniciando servidor en http://0.0.0.0:%s", PORT)
    uvicorn.run(app, host="0.0.0.0", port=PORT)