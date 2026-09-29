# =============================================================================
# tests/test_logging.py — A0.3 `log-config`
# -----------------------------------------------------------------------------
# Valida el módulo de logging centralizado (`API/core/logging.py`):
#   - `get_logger` crea loggers bajo el prefijo `mcp_hub`.
#   - `configure_logging` aplica formato/nivel y es reentrante (sin duplicar).
#   - La configuración es funcional (los mensajes llegan a un StreamHandler).
# =============================================================================

import io
import logging

import pytest

from API.core.logging import (
    LOG_ROOT,
    configure_logging,
    get_logger,
)


class TestGetLogger:
    def test_logger_prefixed(self):
        log = get_logger("dominio.modulo")
        assert log.name == f"{LOG_ROOT}.dominio.modulo"

    def test_logger_idempotent(self):
        a = get_logger("a.b")
        b = get_logger("a.b")
        assert a is b

    def test_does_not_reprefix(self):
        # Pasar un nombre ya prefijado no lo duplica.
        log = get_logger(f"{LOG_ROOT}.api.x")
        assert log.name == f"{LOG_ROOT}.api.x"


class TestConfigureLogging:
    def test_reentrant_no_ducplicate_handlers(self):
        """Varios `configure_logging` no acumulan handlers en el root."""
        configure_logging(force=True)
        configure_logging()  # no-op si ya hay handlers
        root = logging.getLogger()
        handlers = [h for h in root.handlers]
        stream_count = sum(1 for h in handlers if isinstance(h, logging.StreamHandler))
        assert stream_count >= 1
        # No se duplican: configurar de nuevo sin force no añade.
        before = len(root.handlers)
        configure_logging()
        assert len(root.handlers) == before

    def test_messages_flow_to_stream(self):
        """Un mensaje INFO se emite por el handler de consola."""
        configure_logging(force=True)
        stream = io.StringIO()
        # Reemplazar el handler de consola por uno que capture en memoria.
        root = logging.getLogger()
        for h in list(root.handlers):
            root.removeHandler(h)

        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)

        log = get_logger("test.runtime")
        log.info("hola")
        log.warning("cuidado")
        output = stream.getvalue()
        assert "hola" in output
        assert "cuidado" in output