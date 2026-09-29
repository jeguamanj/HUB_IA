import contextlib
from fastapi import FastAPI

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

# Configuración centralizada (FASE 0 · A0.1)
try:
    from API.config import get_settings, validate_credentials
except ImportError:
    # pyrefly: ignore [missing-import]
    from config import get_settings, validate_credentials

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

    # Reporte de credenciales obligatorias al arrancar (no bloquea el boot).
    missing = validate_credentials()
    if missing:
        logger.warning("Configuración incompleta — credenciales faltantes:")
        for msg in missing:
            logger.warning("    - %s", msg)
        logger.warning("Revisa '.env' (usa '.env.example' como referencia).")
    else:
        logger.info("Configuración de credenciales correcta.")

    # Persistencia durable del ContextStore (SQLite/Postgres/memoria según DB_DSN)
    try:
        store = init_durable_store(settings.db_dsn)
        logger.info("ContextStore durable: backend=%s", getattr(store.backend, "name", "memoria"))
    except Exception as exc:  # nunca impedir el arranque por persistencia
        logger.warning("No se pudo inicializar la persistencia del contexto: %s", exc)

    # Control de envíos evaluables: mismo DSN + límite de intentos.
    try:
        tracker = init_submission_tracker(settings.db_dsn, max_attempts=settings.max_submission_attempts)
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
        init_async_evaluator()   # asocia `_evaluar_async_pipeline` al worker
        manager.start_worker()
        logger.info(
            "Cola de evaluaciones asíncrona activa: backend=%s | worker=1x1",
            getattr(manager.backend, "name", "memoria"),
        )
    except Exception as exc:
        logger.warning("No se pudo inicializar la cola de evaluaciones asíncrona: %s", exc)

    # Iniciar consumidor de cola Artemis si está configurado
    artemis_service.start()
    yield
    logger.info("Cerrando el servidor MCP...")
    if manager is not None:
        try:
            await manager.stop_worker()
        except Exception as exc:
            logger.warning("Error al detener worker de evaluaciones: %s", exc)
    artemis_service.stop()

app = FastAPI(
    title="MCP HUB Server",
    description="Servidor centralizado para gestionar múltiples servicios MCP y Core de IA Institucional.",
    version="1.0.0",
    lifespan=lifespan
)

# Middleware de propagación transparente de contexto institucional
app.add_middleware(InstitutionalContextMiddleware)

# Montar los routers de los servicios
app.include_router(get_context_router(), prefix="/context", tags=["Contexto Institucional"])
app.include_router(get_echo_router(), prefix="/echo", tags=["Echo"])
app.include_router(get_llm_chat_router(), prefix="/llm", tags=["LLM Chat"])
app.include_router(get_zoom_reports_router(), prefix="/api", tags=["Zoom Reports"])
app.include_router(get_zoom_management_router(), prefix="/zoom/manage", tags=["Zoom Management"])
app.include_router(get_zoom_license_router(), prefix="/zoom/licenses", tags=["Zoom License Management"])
app.include_router(get_essay_evaluator_router(), prefix="/ensayos", tags=["Evaluador Ensayos"])


PORT = settings.port

if __name__ == "__main__":
    import uvicorn
    logger.info("Iniciando servidor en http://0.0.0.0:%s", PORT)
    uvicorn.run(app, host="0.0.0.0", port=PORT)