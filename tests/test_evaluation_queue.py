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
    SQLAlchemyEvaluationBackend,
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
            assert info["evaluation_id"].startswith("eval_")

            await mgr.process_one()
            code, body = await mgr.get_status(info["evaluation_id"])
            assert code == 200 and body["status"] == "EVALUADO"  # canon directo
            assert body["nota_final"] == 9.0
            assert body["submission_id"] == "S1"
            assert body["rubric_assessment_canvas"]["_2541"]["rating_id"] == "blank"

        asyncio.run(scenario())


class TestEstadoColaAsincrona:
    def test_status_is_not_disclosed_across_institutions(self):
        async def scenario():
            mgr = _manager()
            info = await mgr.enqueue({"institution_id": "institution-a"})

            allowed_code, _ = await mgr.get_status(
                info["evaluation_id"],
                institution_id="institution-a",
            )
            hidden_code, hidden_body = await mgr.get_status(
                info["evaluation_id"],
                institution_id="institution-b",
            )

            assert allowed_code == 200
            assert hidden_code == 404
            assert hidden_body["estado"] == "NOT_FOUND"

        asyncio.run(scenario())

    def test_salida_consolidada_sin_duplicados(self):
        async def scenario():
            mgr = _manager()
            info = await mgr.enqueue({"submission_id": "S1"})
            await mgr.process_one()
            _, body = await mgr.get_status(info["evaluation_id"])
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

    def test_same_submission_can_create_new_evaluation(self):
        async def scenario():
            mgr = _manager()
            first = await mgr.enqueue({"submission_id": "S1"})
            await mgr.process_one()
            second = await mgr.enqueue({"submission_id": "S1"})
            assert second["estado"] == STATUS_PENDING
            assert second["evaluation_id"] != first["evaluation_id"]
            st = await mgr.stats()
            assert st["total"] == 2

        asyncio.run(scenario())

    def test_idempotency_key_reuses_same_evaluation(self):
        async def scenario():
            mgr = _manager()
            payload = {"submission_id": "S1"}
            first = await mgr.enqueue(
                payload, idempotency_client_id="client", idempotency_key="request-1"
            )
            retry = await mgr.enqueue(
                payload, idempotency_client_id="client", idempotency_key="request-1"
            )
            assert retry["evaluation_id"] == first["evaluation_id"]
            assert (await mgr.stats())["total"] == 1

            with pytest.raises(ValueError, match="otro payload"):
                await mgr.enqueue(
                    {"submission_id": "S2"},
                    idempotency_client_id="client",
                    idempotency_key="request-1",
                )

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
            info = await mgr.enqueue({"submission_id": "S2"})
            processed = await mgr.process_one()
            assert processed is True
            _, body = await mgr.get_status(info["evaluation_id"])
            assert body["estado"] == STATUS_ERROR
            assert "LLM caído" in body.get("error", "")

        asyncio.run(scenario())


def test_postgres_expired_lease_recovery_releases_attempt_before_error_status():
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import CheckConstraint, DateTime, Integer, JSON, String, Text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.orm import DeclarativeBase, mapped_column

    class Base(DeclarativeBase):
        pass

    class JobRow(Base):
        __tablename__ = "evaluation_jobs"
        __table_args__ = (
            CheckConstraint(
                "status <> 'ERROR' OR attempt_tracking IN "
                "('RELEASED', 'NOT_APPLICABLE', 'SKIPPED_MISSING_IDENTITY')",
                name="ck_failed_job_attempt_released",
            ),
        )
        evaluation_id = mapped_column(String, primary_key=True)
        status = mapped_column(String, nullable=False)
        worker_attempts = mapped_column(Integer, nullable=False)
        max_processing_attempts = mapped_column(Integer, nullable=False)
        attempt_tracking = mapped_column(String, nullable=False)
        submission_group_id = mapped_column(Integer, nullable=True)
        result = mapped_column(JSON, nullable=True)
        error = mapped_column(Text, nullable=True)
        updated_at = mapped_column(DateTime(timezone=True), nullable=False)
        finished_at = mapped_column(DateTime(timezone=True), nullable=True)
        lease_owner = mapped_column(String, nullable=True)
        lease_expires_at = mapped_column(DateTime(timezone=True), nullable=True)
        processing_cycles = mapped_column(Integer, nullable=False)
        created_at = mapped_column(DateTime(timezone=True), nullable=False)
        available_at = mapped_column(DateTime(timezone=True), nullable=False)

    class GroupRow(Base):
        __tablename__ = "evaluation_submission_groups"
        submission_group_id = mapped_column(Integer, primary_key=True)
        reserved_attempts = mapped_column(Integer, nullable=False)
        updated_at = mapped_column(DateTime(timezone=True), nullable=False)

    class AttemptRow(Base):
        __tablename__ = "evaluation_attempts"
        evaluation_attempt_id = mapped_column(Integer, primary_key=True)
        evaluation_id = mapped_column(String, nullable=False)
        status = mapped_column(String, nullable=False)
        finished_at = mapped_column(DateTime(timezone=True), nullable=True)
        error_code = mapped_column(String, nullable=True)
        error_message = mapped_column(Text, nullable=True)

    class EventRow(Base):
        __tablename__ = "evaluation_status_events"
        evaluation_status_event_id = mapped_column(Integer, primary_key=True)
        evaluation_id = mapped_column(String, nullable=False)
        event_number = mapped_column(Integer, nullable=False)
        event_type = mapped_column(String, nullable=False)
        from_status = mapped_column(String, nullable=True)
        to_status = mapped_column(String, nullable=True)
        actor_type = mapped_column(String, nullable=False)
        actor_id = mapped_column(String, nullable=True)
        processing_cycle = mapped_column(Integer, nullable=True)
        attempt_number = mapped_column(Integer, nullable=True)
        occurred_at = mapped_column(DateTime(timezone=True), nullable=False)
        metadata_json = mapped_column("metadata", JSON, nullable=False)

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        async with session_factory() as session:
            session.add_all(
                [
                    JobRow(
                        evaluation_id="eval-final-failure",
                        status="EVALUATING",
                        worker_attempts=3,
                        max_processing_attempts=3,
                        attempt_tracking="RESERVED",
                        submission_group_id=7,
                        updated_at=now,
                        processing_cycles=3,
                        lease_expires_at=now - timedelta(seconds=1),
                        created_at=now - timedelta(minutes=10),
                        available_at=now,
                    ),
                    GroupRow(
                        submission_group_id=7,
                        reserved_attempts=1,
                        updated_at=now,
                    ),
                    AttemptRow(
                        evaluation_attempt_id=1,
                        evaluation_id="eval-final-failure",
                        status="RUNNING",
                    ),
                ]
            )
            await session.commit()

        backend = SQLAlchemyEvaluationBackend(
            "postgresql+asyncpg://localhost/test"
        )

        async def initialized():
            return None

        backend._ensure_ready = initialized
        backend._SessionLocal = session_factory
        backend._Row = JobRow
        backend._GroupRow = GroupRow
        backend._AttemptRow = AttemptRow
        backend._EventRow = EventRow
        assert await backend.find_next_pending() is None

        async with session_factory() as session:
            job = await session.get(JobRow, "eval-final-failure")
            group = await session.get(GroupRow, 7)
            attempt = await session.get(AttemptRow, 1)
            assert job.status == STATUS_ERROR
            assert job.attempt_tracking == "RELEASED"
            assert group.reserved_attempts == 0
            assert attempt.status == "ABANDONED"
        await engine.dispose()

    asyncio.run(scenario())