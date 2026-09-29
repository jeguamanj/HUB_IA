# =============================================================================
# tests/test_submission_tracker.py — control de envíos LTI (FASE 3)
# -----------------------------------------------------------------------------
# Valida el límite de envíos por tarea-estudiante, la crónica/historial para
# métricas y la fábrica con degradación a memoria.
# =============================================================================

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from API.core.submission_tracker import (
    SubmissionTracker,
    InMemorySubmissionBackend,
    make_submission_backend,
)


async def _scenario_max_2():
    tr = SubmissionTracker(backend=InMemorySubmissionBackend(), max_attempts=2)
    ok1, _, m1 = await tr.check_and_register(submission_id="S1", user_id="U1", course_id="C1", assignment_id="A1", nota=8.0)
    ok2, _, m2 = await tr.check_and_register(submission_id="S2", user_id="U1", course_id="C1", assignment_id="A1", nota=9.0)
    assert ok1 is True and ok2 is True
    ok3, ultimo, m3 = await tr.check_and_register(submission_id="S3", user_id="U1", course_id="C1", assignment_id="A1", nota=7.0)
    assert ok3 is False
    assert "alcanzado" in m3
    assert ultimo is not None and ultimo["nota"] == 9.0


async def _scenario_grupos_independientes():
    tr = SubmissionTracker(backend=InMemorySubmissionBackend(), max_attempts=1)
    okA, _, _ = await tr.check_and_register(submission_id="A1", user_id="X", course_id="C1", assignment_id="T1")
    okB, _, _ = await tr.check_and_register(submission_id="A2", user_id="X", course_id="C1", assignment_id="T2")
    assert okA is True and okB is True


async def _scenario_historial():
    tr = SubmissionTracker(backend=InMemorySubmissionBackend(), max_attempts=5)
    await tr.register(submission_id="S1", user_id="U1", course_id="C1", assignment_id="A1", nota=7.5, modelo="x")
    await tr.register(submission_id="S2", user_id="U1", course_id="C1", assignment_id="A1", nota=8.5, modelo="y")
    # Nueva semántica por GRUPO: dos envíos de la misma tarea → un registro con
    # intentos acumulados y la última nota.
    hist = await tr.history("U1")
    assert len(hist) == 1
    assert hist[0]["intentos"] == 2
    assert hist[0]["nota"] == 8.5
    assert hist[0]["submission_id"] == "S2"


async def _scenario_reenvio_mismo_submission():
    """Caso REAL Canvas: el estudiante reenvía el MISMO submission_id.
    El límite debe contar reenvíos aunque la PK no cambie."""
    tr = SubmissionTracker(backend=InMemorySubmissionBackend(), max_attempts=2)
    ok1, _, _ = await tr.check_and_register(submission_id="57423885", user_id="U", course_id="C", assignment_id="A")
    ok2, _, _ = await tr.check_and_register(submission_id="57423885", user_id="U", course_id="C", assignment_id="A")
    ok3, _, m3 = await tr.check_and_register(submission_id="57423885", user_id="U", course_id="C", assignment_id="A")
    assert ok1 is True and ok2 is True
    assert ok3 is False and "alcanzado" in m3


async def _scenario_check_allowed():
    tr = SubmissionTracker(backend=InMemorySubmissionBackend(), max_attempts=1)
    ok1, _ = await tr.check_allowed(user_id="U", course_id="C", assignment_id="A")
    assert ok1 is True
    await tr.register(submission_id="S", user_id="U", course_id="C", assignment_id="A")
    ok2, motivo = await tr.check_allowed(user_id="U", course_id="C", assignment_id="A")
    assert ok2 is False and "alcanzado" in motivo


async def _scenario_max_attempts_override():
    """El límite es configurable por llamada (per-payload / per-admin)."""
    tr = SubmissionTracker(backend=InMemorySubmissionBackend(), max_attempts=2)
    # Registrar 3 envíos del mismo grupo.
    for i in range(3):
        await tr.register(submission_id=f"S{i}", user_id="U", course_id="C", assignment_id="A", nota=8.0)
    # Con el límite global (2) y 3 envíos → bloqueado.
    ok_global, _ = await tr.check_allowed(user_id="U", course_id="C", assignment_id="A")
    assert ok_global is False
    # Con override max_attempts=5 → permitido.
    ok_override, _ = await tr.check_allowed(user_id="U", course_id="C", assignment_id="A", max_attempts=5)
    assert ok_override is True


class TestLimiteEnvios:
    def test_max_2_envios_por_grupo(self):
        asyncio.run(_scenario_max_2())

    def test_grupos_independientes(self):
        asyncio.run(_scenario_grupos_independientes())

    def test_historial_para_metricas(self):
        asyncio.run(_scenario_historial())

    def test_reenvio_mismo_submission(self):
        # Caso real Canvas: mismo submission_id reenviado.
        asyncio.run(_scenario_reenvio_mismo_submission())

    def test_check_allowed_sin_consumir(self):
        asyncio.run(_scenario_check_allowed())

    def test_max_attempts_override(self):
        asyncio.run(_scenario_max_attempts_override())


class TestFactory:
    def test_fallback_memory_empty(self):
        assert make_submission_backend("").name == "memory"

    def test_fallback_memory_unsupported(self):
        assert make_submission_backend("mongodb://h/db").name == "memory"

    def test_sqlalchemy_dsn_no_raise(self):
        b = make_submission_backend("sqlite+aiosqlite:///./storage/sub.db")
        assert b.name in ("sqlalchemy", "memory")
        assert hasattr(b, "register") and hasattr(b, "count_group")