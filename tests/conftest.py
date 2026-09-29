# =============================================================================
# tests/conftest.py
# -----------------------------------------------------------------------------
# Fixtures y configuración global de la suite de tests.
#
# Aislamiento:
#   - Se evita cargar el `.env` real para que los tests no dependan de
#     credenciales de producción ni disparen llamadas de red.
#   - `clear_settings_env` se auto-aplica y neutraliza los defaults de red.
# =============================================================================

import os
import sys
from pathlib import Path

import pytest

# Garantizar que la raíz del proyecto esté en sys.path para imports `API.*`.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Variables de entorno con valores de red: los tests no deben tocar servicios reales.
_NEUTRALIZED_NET = {
    # Todo lo que genere I/O de red se desactiva por defecto.
    "ARTEMIS_ENABLED": "false",  # nunca conectar en tests
}


@pytest.fixture(autouse=True)
def clear_network_env(monkeypatch):
    """Neutraliza variables de red antes de cada test."""
    for key, val in _NEUTRALIZED_NET.items():
        monkeypatch.setenv(key, val, prepend=False)
    # No se permiten llamadas externas accidentales a OpenRouter/Zoom/Ollama.
    for key in (
        "OPENROUTER_API_KEY",
        "ZOOM_ACCOUNT_ID",
        "ZOOM_CLIENT_ID",
        "ZOOM_CLIENT_SECRET",
        "CANVAS_API_URL",
        "CANVAS_API_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    yield