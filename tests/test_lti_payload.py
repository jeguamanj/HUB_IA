# =============================================================================
# tests/test_lti_payload.py — afinado MCP de ensayos (FASE 3)
# -----------------------------------------------------------------------------
# Valida el manejo de los payloads reales emitidos desde el LTI del LMS:
#   - Caso 1: submission online_upload + attachment.url (archivo descargable).
#   - Caso 2: submission online_text_entry + attachment.txt (texto en línea).
#   - Extracción de assignment.description y depuración de la consigna.
#   - Trazabilidad LMS (course/assignment/submission/user).
# =============================================================================

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from API.services.essay_evaluator_mcp import (
    SolicitudEvaluacionDirecta,
    _extraer_consigna_evaluable,
)
from API.utils import document_parser
from API.utils.document_parser import _validate_download_url

DESC_REAL = (
    "Descripción de la actividad\n\nComponente de aprendizaje:\n\n"
    "Aprendizaje práctico - experimental (APE)\n\nActividad de aprendizaje:\n\n"
    "Analice la situación actual de una empresa utilizando la matriz FODA "
    "y proponga un plan de mejora en base a los resultados del análisis\n\n"
    "Tipo de recurso:\n\nInvestigación y propuesta\n\nTema de la unidad:\n\n"
    "UNIDAD 3: Planeación\n\nResultados de aprendizaje que se espera lograr:\n\n"
    "Analiza las variables del entorno interno y externo que influyen en las organizaciones.\n\n"
    "Estrategias de trabajo:\n\nRevise los contenidos relacionados con la unidad 3.\n\n"
    "Unifique todo el desarrollo en un documento Word y proceda con la entrega.\n\n"
    "Instrumento de evaluación:\n\nRúbrica"
)

RUBRICA_MIN = [
    {"id": "_2541", "points": 2.5, "description": "Contextualización",
     "ratings": [{"id": "blank", "points": 2.5, "description": "Excelente"}]}
]


def _assignment(**over):
    a = {
        "id": "992586",
        "courseId": "79871",
        "name": "[APEB1-30%] Investigación y propuesta: Analice la situación actual",
        "description": DESC_REAL,
        "rubric": RUBRICA_MIN,
    }
    a.update(over)
    return a


class TestCaso1ArchivoURL:
    def test_extrae_url_y_descripcion(self):
        payload = {
            "success": True,
            "assignment": _assignment(),
            "submission": {"id": "57423885", "type": "online_upload", "userId": "182199", "score": "7.75"},
            "attachment": {"name": "TAREA_FODA.docx", "url": "https://utpl.test/files/24498662/download?verifier=H4d", "txt": None},
            "ai": {"status": "PENDING"},
        }
        req = SolicitudEvaluacionDirecta.model_validate(payload)
        assert req.origen_ensayo == "https://utpl.test/files/24498662/download?verifier=H4d"
        assert req.assignment_description and "Analice la situación actual" in req.assignment_description
        assert req.course_id == "79871"
        assert req.assignment_id == "992586"
        assert req.submission_id == "57423885"
        assert req.user_id_canvas == "182199"
        assert req.id_lms == "CANVAS"


class TestCaso2TextoOnline:
    def test_extrae_texto_y_descripcion(self):
        payload = {
            "success": True,
            "assignment": _assignment(),
            "submission": {"id": "57423860", "type": "online_text_entry", "userId": "181093", "score": "8.00"},
            "attachment": {"url": None, "txt": "MOLINOS MIRAFLORES\nMISION: ...\nFORTALEZAS\n- Mejor tecnología del país"},
            "ai": {"status": "PENDING"},
        }
        req = SolicitudEvaluacionDirecta.model_validate(payload)
        assert req.origen_ensayo and "MOLINOS" in req.origen_ensayo
        assert req.assignment_description and "Analice la situación actual" in req.assignment_description
        assert req.submission_type == "online_text_entry"


class TestConsigna:
    def test_extrae_solo_lo_evaluable(self):
        cons = _extraer_consigna_evaluable(DESC_REAL)
        assert "Analice la situación actual" in cons
        assert "Rúbrica" not in cons
        assert "UNIDAD 3" not in cons
        assert "Revise los contenidos" not in cons  # estrategia operativa filtrada
        # Significativamente más corta que la descripción original.
        assert len(cons) < len(DESC_REAL)

    def test_vacio(self):
        assert _extraer_consigna_evaluable("") == ""
        assert _extraer_consigna_evaluable(None) == ""

    def test_descripcion_simple(self):
        assert _extraer_consigna_evaluable("Actividad de aprendizaje:\nResuelva los ejercicios planteados") == "Resuelva los ejercicios planteados"


class TestEnvelopeIntento:
    def test_contar_intento_default_true(self):
        payload = {
            "success": True,
            "assignment": _assignment(),
            "submission": {"id": "S1", "type": "online_upload", "userId": "U1"},
            "attachment": {"url": "https://x/desc.docx", "txt": None},
        }
        req = SolicitudEvaluacionDirecta.model_validate(payload)
        assert req.contar_intento is True

    def test_max_intentos_default_none(self):
        # Sin max_intentos en el payload → usa la config global.
        payload = {
            "success": True,
            "assignment": _assignment(),
            "submission": {"id": "S1", "type": "online_upload", "userId": "U1"},
            "attachment": {"url": "https://x/desc.docx", "txt": None},
        }
        req = SolicitudEvaluacionDirecta.model_validate(payload)
        assert req.max_intentos is None

    def test_max_intentos_configurable_por_tarea(self):
        # El LTI puede definir un límite propio por tarea.
        payload = {
            "success": True,
            "max_intentos": 3,
            "assignment": _assignment(),
            "submission": {"id": "S1", "type": "online_upload", "userId": "U1"},
            "attachment": {"url": "https://x/desc.docx", "txt": None},
        }
        req = SolicitudEvaluacionDirecta.model_validate(payload)
        assert req.max_intentos == 3


class TestAttachmentUrlSecurity:
    def test_blocks_loopback_addresses(self):
        with pytest.raises(ValueError, match="privados o reservados"):
            asyncio.run(_validate_download_url("http://127.0.0.1/internal"))

    def test_production_allowlist_rejects_unapproved_host(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("ATTACHMENT_ALLOWED_HOSTS", "lms.example.edu")

        with pytest.raises(ValueError, match="no está permitido"):
            asyncio.run(_validate_download_url("https://evil.example.net/file.docx"))

    def test_fetch_follows_validated_canvas_redirect(self, monkeypatch):
        calls = []

        class Response:
            def __init__(self, status_code, headers, body=b""):
                self.status_code = status_code
                self.headers = headers
                self.body = body

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_value, traceback):
                return False

            def raise_for_status(self):
                return None

            async def aiter_bytes(self):
                yield self.body

        class Client:
            def __init__(self, **kwargs):
                assert kwargs["follow_redirects"] is False

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_value, traceback):
                return False

            def stream(self, method, url):
                calls.append(url)
                if len(calls) == 1:
                    return Response(
                        302,
                        {"location": "https://files.canvas.example/download?sig=secret"},
                    )
                return Response(
                    200,
                    {"content-type": "application/pdf", "content-length": "4"},
                    b"%PDF",
                )

        validated = []

        async def validate(url):
            validated.append(url)
            return "canvas.example"

        monkeypatch.setattr(document_parser, "_validate_download_url", validate)
        monkeypatch.setattr(document_parser.httpx, "AsyncClient", Client)

        data, hint = asyncio.run(
            document_parser.fetch_url_bytes("https://canvas.example/files/1")
        )

        assert data == b"%PDF"
        assert hint == "application/pdf"
        assert validated == calls
        assert len(calls) == 2

    def test_fetch_rejects_redirect_to_http_from_https(self, monkeypatch):
        class Response:
            status_code = 302
            headers = {"location": "http://files.example/download"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_value, traceback):
                return False

        class Client:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_value, traceback):
                return False

            def stream(self, method, url):
                return Response()

        async def validate(url):
            return "example"

        monkeypatch.setattr(document_parser, "_validate_download_url", validate)
        monkeypatch.setattr(document_parser.httpx, "AsyncClient", Client)

        with pytest.raises(ValueError, match="HTTPS a HTTP"):
            asyncio.run(
                document_parser.fetch_url_bytes("https://canvas.example/files/1")
            )