# =============================================================================
# tests/test_evaluation_queue.py — cola asíncrona + salida consolidada (FASE 3)
# -----------------------------------------------------------------------------
# Valida:
#   1. Encolar (PENDING) → procesar (EVALUATED) → consultar estado con resultado.
#   2. El resultado consolidado NO duplica criterios ni bloques inflados.
#   3. Re-encolar un submission ya evaluado no duplica (devuelve el resultado).
#   4. Estados/límites de la cola (memory backend) y stats.
# =============================================================================

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from API.core.evaluation_queue import (
    EvaluationManager,
    InMemoryEvaluationBackend,
    STATUS_PENDING,
    STATUS_EVALUATED,
    STATUS_ERROR,
)
from API.services.essay_evaluator_mcp import _consolidar_desde_datos_eval


async def _fake_evaluator(payload):
    await asyncio.sleep(0.01)
    canon = _consolidar_desde_datos_eval(
        evaluacion={
            "nota_final": 9.0,
            "escala_maxima": 10.0,
            "resumen_retroalimentacion": "Buen análisis FODA.",
            "fortalezas": ["Contexto"],
            "areas_mejora": ["Indicadores"],
            "recomendaciones": ["Revisar tabla"],
            "rubric_assessment_canvas": {"_2541": {"points": 2.5, "rating_id": "blank", "comments": "Bien"}},
        },
        evaluation_id=payload.get("submission_id", "x"),
        submission_id=payload.get("submission_id"),
        datos_lms={"course_id": payload.get("course_id")},
        modelo="fake",
    )
    canon["status"] = "EVALUADO"
    return canon


def _manager():
    return EvaluationManager(backend=InMemoryEvaluationBackend(), evaluator=_fake_evaluator, poll_interval=0.05)


class TestColaAsincrona:
    def test_enqueue_procesa_y_consulta_estado(self):
        async def scenario():
            mgr = _manager()
            info = await mgr.enqueue({"submission_id": "S1", "course_id": "C1", "assignment_id": "A1", "user_id": "U1"})
            assert info["estado"] == STATUS_PENDING
            assert info["evaluation_id"] == "eval_S1"

            await mgr.process_one()
            code, body = await mgr.get_status("eval_S1")
            assert code == 200 and body["status"] == "EVALUADO"  # canon directo
            assert body["nota_final"] == 9.0
            assert body["submission_id"] == "S1"
            assert body["rubric_assessment_canvas"]["_2541"]["rating_id"] == "blank"

        asyncio.run(scenario())

    def test_salida_consolidada_sin_duplicados(self):
        async def scenario():
            mgr = _manager()
            await mgr.enqueue({"submission_id": "S1"})
            await mgr.process_one()
            _, body = await mgr.get_status("eval_S1")
            # Estructura canónica: NO duplica criterios ni bloques inflados.
            assert "criterios" not in body
            assert "evaluation_id" in body and "feedback" in body
            assert isinstance(body["rubric_assessment_canvas"], dict)
            # Conjunto de claves canónico.
            esperado = {"status", "evaluation_id", "submission_id", "nota_final",
                        "escala_maxima", "feedback", "fortalezas", "areas_mejora",
                        "recomendaciones", "rubric_assessment_canvas", "modelo_utilizado", "datos_lms"}
            assert set(body.keys()) == esperado

        asyncio.run(scenario())

    def test_reenqueue_evaluado_no_duplica(self):
        async def scenario():
            mgr = _manager()
            await mgr.enqueue({"submission_id": "S1"})
            await mgr.process_one()
            info2 = await mgr.enqueue({"submission_id": "S1"})
            assert info2["estado"] == STATUS_EVALUATED
            # El canon se obtiene vía GET /estado/{id}, no se re-encola ni duplica.
            st = await mgr.stats()
            assert st["total"] == 1  # no se duplicó

        asyncio.run(scenario())

    def test_estado_inexistente_404(self):
        async def scenario():
            mgr = _manager()
            code, body = await mgr.get_status("eval_zzz")
            assert code == 404 and body["estado"] == "NOT_FOUND"

        asyncio.run(scenario())

    def test_error_worker(self):
        async def scenario():
            async def bad(payload):
                raise RuntimeError("LLM caído")
            mgr = EvaluationManager(backend=InMemoryEvaluationBackend(), evaluator=bad, poll_interval=0.05)
            await mgr.enqueue({"submission_id": "S2"})
            processed = await mgr.process_one()
            assert processed is True
            _, body = await mgr.get_status("eval_S2")
            assert body["estado"] == STATUS_ERROR
            assert "LLM caído" in body.get("error", "")

        asyncio.run(scenario())