# =============================================================================
# API/core/logging.py
# -----------------------------------------------------------------------------
# Logging centralizado del MCP HUB Server IA (FASE 0 · Agente A0.3 `log-config`).
#
# Objetivo:
#   - Único punto de configuración del logging (formato, nivel, handlers).
#   - Helper `get_logger(name)` para que cada módulo use `logger = get_logger(__name__)`.
#   - Nivel configurable por entorno (LOG_LEVEL, default INFO) e inicializable desde
#     el boot de la aplicación sin duplicar config en cada módulo.
#
# Uso en cada módulo:
#     from API.core.logging import get_logger
#     logger = get_logger(__name__)
#     logger.info("mensaje")
#     logger.warning("algo")
# =============================================================================

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

# Nombre raíz bajo el que se agrupan todos los loggers del proyecto.
LOG_ROOT = "mcp_hub"

_DEFAULT_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
)
_VALID_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}


def _parse_level(value: Optional[str], default: int = logging.INFO) -> int:
    """Convierte el nombre de nivel (str) del entorno a constante `logging`."""
    if not value:
        return default
    return _VALID_LEVELS.get(str(value).strip().upper(), default)


def configure_logging(
    level: Optional[str] = None,
    log_root: str = LOG_ROOT,
    fmt: str = _DEFAULT_FORMAT,
    force: bool = False,
) -> None:
    """
    Configura el logging global de la aplicación.

    Args:
        level:    Nombre del nivel (INFO, DEBUG, WARNING...). Si es None, lee `LOG_LEVEL`.
        log_root: Prefijo sobre el que se fija el nivel.
        fmt:      Formato de los registros.
        force:    Re-aplica la configuración aunque ya esté inicializada (útil en tests).
    """
    root = logging.getLogger()
    if root.handlers and not force:
        return

    level_value = _parse_level(level or os.getenv("LOG_LEVEL"), logging.INFO)

    # Handler de consola con soporte de colores básico en TTY.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt))

    root.setLevel(level_value)
    # Limpieza de handlers duplicados si `force=True`.
    if force:
        for h in list(root.handlers):
            root.removeHandler(h)
        for name in list(logging.Logger.manager.loggerDict):
            if str(name).startswith(log_root):
                logging.getLogger(name).handlers.clear()
    root.addHandler(handler)

    # Silenciar librerías de terceros muy ruidosas en INFO.
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str, level: Optional[str] = None) -> logging.Logger:
    """
    Devuelve (creando si es necesario) un logger bajo el prefijo `mcp_hub`.

    Uso posterior recomendado (migración A0.3):
        logger = get_logger(__name__)   # name será 'API.services.xyz'
    """
    qualified = f"{LOG_ROOT}.{name}" if not name.startswith(LOG_ROOT) else name
    logger = logging.getLogger(qualified)
    if level is not None:
        logger.setLevel(_parse_level(level))
    return logger


def setup_logging() -> None:
    """
    Inicializa el logging al arrancar la aplicación.
    Llámese desde `server.py` antes de arrancar uvicorn si se desea logging con nivel.
    (Reentrante: no duplica handlers entre imports/uso en módulos.)
    """
    configure_logging(force=False)


__all__ = [
    "LOG_ROOT",
    "configure_logging",
    "get_logger",
    "setup_logging",
]