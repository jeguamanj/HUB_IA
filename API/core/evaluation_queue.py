# =============================================================================
# API/core/evaluation_queue.py
# -----------------------------------------------------------------------------
# Cola de evaluaciones asíncronas + consulta de estado (FASE 3 · afinar MCP).
#
# Problema: el flujo LMS→MCP síncrono bloquea la petición HTTP mientras el LLM
# evalúa (100-300s), generando timeouts/cargas bajo volumen.
#
# Solución (asíncrono con persistencia):
#   - `EvaluationManager.enqueue(payload)` guarda la solicitud en BD
#     (SQLite/Postgres) con estado PENDING y devuelve `evaluation_id` de inmediato
#     (el LMS vuelve 202 sin esperar).
#   - Un worker (1 evaluación a la vez) procesa en background: PENDING -> EVALUATING
#     -> EVALUATED (con resultado consolidado) o ERROR.
#   - `get_status(evaluation_id)` permite al LMS consultar el estado y, al estar
#     EVALUATED, obtener la calificación + rubric_assessment_canvas.
#
# Persistencia: patrón SQLAlchemy async (igual que `submission_tracker`); degrada
# a memoria si no hay driver/BD (dev). Estados: PENDING, EVALUATING, EVALUATED, ERROR.
# =============================================================================

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from API.core.logging import get_logger
except ImportError:  # pragma: no cover
    from core.logging import get_logger

logger = get_logger("core.evaluation_queue")

STATUS_PENDING = "PENDING"
STATUS_EVALUATING = "EVALUATING"
STATUS_EVALUATED = "EVALUATED"
STATUS_ERROR = "ERROR"
ALL_STATUS = {STATUS_PENDING, STATUS_EVALUATING, STATUS_EVALUATED, STATUS_ERROR}
_CURRENT_EVALUATION_CONTEXT = contextvars.ContextVar(
    "current_evaluation_context", default=None
)


class AttemptLimitExceeded(Exception):
    """Raised when no academic attempt can be reserved for a job."""


# =============================================================================
# Backends (memoria + SQLAlchemy async)
# =============================================================================

class EvaluationBackend:
    name = "abstract"

    async def initialize(self) -> None:
        """Prepare the backend before the worker starts."""

    async def create(
        self,
        evaluation_id: str,
        payload: Dict[str, Any],
        *,
        idempotency_client_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        request_sha256: Optional[str] = None,
    ) -> None:
        raise NotImplementedError

    async def find_by_idempotency(
        self, client_id: str, key: str
    ) -> Optional[Dict[str, Any]]:
        return None

    async def record_llm_invocation(self, **invocation) -> None:
        return None

    async def record_mcp_invocation(self, **invocation) -> None:
        return None

    async def find_next_pending(self) -> Optional[Dict[str, Any]]:
        """Devuelve la siguiente evaluación PENDING (la más antigua)."""
        raise NotImplementedError

    async def set_status(
        self,
        evaluation_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        raise NotImplementedError

    async def get(self, evaluation_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    async def stats(self) -> Dict[str, Any]:
        raise NotImplementedError

    async def close(self) -> None:
        pass


class InMemoryEvaluationBackend(EvaluationBackend):
    """Backend transitorio (dev / fallback)."""

    name = "memory"

    def __init__(self) -> None:
        self._rows: Dict[str, Dict[str, Any]] = {}

    async def create(
        self,
        evaluation_id: str,
        payload: Dict[str, Any],
        *,
        idempotency_client_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        request_sha256: Optional[str] = None,
    ) -> None:
        self._rows[evaluation_id] = {
            "evaluation_id": evaluation_id,
            "payload": payload,
            "status": STATUS_PENDING,
            "result": None,
            "error": None,
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "idempotency_client_id": idempotency_client_id,
            "idempotency_key": idempotency_key,
            "request_sha256": request_sha256,
        }

    async def find_by_idempotency(self, client_id: str, key: str):
        for row in self._rows.values():
            if (
                row.get("idempotency_client_id") == client_id
                and row.get("idempotency_key") == key
            ):
                return dict(row)
        return None

    async def find_next_pending(self) -> Optional[Dict[str, Any]]:
        candidates = [
            r for r in self._rows.values() if r.get("status") == STATUS_PENDING
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda r: r.get("created_at", 0))
        return dict(candidates[0])

    async def set_status(self, evaluation_id, status, result=None, error=None) -> None:
        if evaluation_id not in self._rows:
            return
        row = self._rows[evaluation_id]
        row["status"] = status
        if status == STATUS_EVALUATING:
            row["started_at"] = time.time()
        if status in (STATUS_EVALUATED, STATUS_ERROR):
            row["finished_at"] = time.time()
        if result is not None:
            row["result"] = result
        if error is not None:
            row["error"] = error

    async def get(self, evaluation_id):
        row = self._rows.get(evaluation_id)
        return dict(row) if row else None

    async def stats(self):
        counts = {s: 0 for s in ALL_STATUS}
        for r in self._rows.values():
            counts[r.get("status", STATUS_PENDING)] = counts.get(r.get("status"), 0) + 1
        return {"total": len(self._rows), **counts, "backend": self.name}


class SQLAlchemyEvaluationBackend(EvaluationBackend):
    """Backend durable (SQLite/Postgres), tabla `evaluation_jobs`."""

    name = "sqlalchemy"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._is_postgres = dsn.startswith(("postgresql+", "postgres+"))
        self._engine = None
        self._SessionLocal = None
        self._session = None
        self._ready = False
        self._Row = None
        self._AttemptRow = None
        self._EventRow = None
        self._GroupRow = None
        self._LLMInvocationRow = None
        self._MCPInvocationRow = None
        self._worker_id = f"worker-{uuid.uuid4().hex}"

    async def _ensure_ready(self) -> None:
        if self._ready:
            return
        try:
            from sqlalchemy.orm import DeclarativeBase, mapped_column
            from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, Numeric, SmallInteger, String, Text
            from sqlalchemy.ext.asyncio import (
                AsyncSession,
                async_sessionmaker,
                create_async_engine,
            )
            from pathlib import Path

            if self._is_postgres:
                from sqlalchemy.dialects.postgresql import JSONB

                payload_type = JSONB
                result_type = JSONB
                timestamp_type = DateTime(timezone=True)
                error_column_name = "error_message"
            else:
                payload_type = Text
                result_type = Text
                timestamp_type = Float
                error_column_name = "error"

            if self.dsn.startswith("sqlite+aiosqlite:///./"):
                rel = self.dsn.split("///./", 1)[1]
                Path(rel).parent.mkdir(parents=True, exist_ok=True)

            class Base(DeclarativeBase):
                pass

            class EvalRow(Base):
                __tablename__ = "evaluation_jobs"
                # Explicit types avoid resolving function-local Mapped names
                # under postponed annotations.
                evaluation_id = mapped_column(Text, primary_key=True)
                payload = mapped_column(payload_type, nullable=False)
                status = mapped_column(Text, nullable=False, default=STATUS_PENDING)
                result = mapped_column(result_type, nullable=True)
                error = mapped_column(error_column_name, Text, nullable=True)
                created_at = mapped_column(timestamp_type, nullable=False)
                started_at = mapped_column(timestamp_type, nullable=True)
                finished_at = mapped_column(timestamp_type, nullable=True)
                if self._is_postgres:
                    source = mapped_column(Text, nullable=False, default="unknown")
                    lms = mapped_column(Text, nullable=True)
                    institution_id = mapped_column(Text, nullable=True)
                    submission_group_id = mapped_column(BigInteger, nullable=True)
                    course_id = mapped_column(Text, nullable=True)
                    assignment_id = mapped_column(Text, nullable=True)
                    user_id = mapped_column(Text, nullable=True)
                    submission_id = mapped_column(Text, nullable=True)
                    context_id = mapped_column(Text, nullable=True)
                    count_attempt = mapped_column(Boolean, nullable=False, default=False)
                    attempt_tracking = mapped_column(
                        String(32), nullable=False, default="NOT_APPLICABLE"
                    )
                    attempt_number = mapped_column(Integer, nullable=True)
                    max_attempts_snapshot = mapped_column(Integer, nullable=True)
                    idempotency_client_id = mapped_column(Text, nullable=True)
                    idempotency_key = mapped_column(Text, nullable=True)
                    request_sha256 = mapped_column(String(64), nullable=True)
                    processing_cycles = mapped_column(Integer, nullable=False, default=0)
                    worker_attempts = mapped_column(Integer, nullable=False, default=0)
                    max_processing_attempts = mapped_column(Integer, nullable=False, default=3)
                    available_at = mapped_column(timestamp_type, nullable=False)
                    lease_owner = mapped_column(Text, nullable=True)
                    lease_expires_at = mapped_column(timestamp_type, nullable=True)
                    updated_at = mapped_column(timestamp_type, nullable=False)

            if self._is_postgres:
                class AttemptRow(Base):
                    __tablename__ = "evaluation_attempts"
                    evaluation_attempt_id = mapped_column(BigInteger, primary_key=True)
                    evaluation_id = mapped_column(String(128), nullable=False)
                    processing_cycle = mapped_column(Integer, nullable=False)
                    attempt_number = mapped_column(SmallInteger, nullable=False)
                    status = mapped_column(String(16), nullable=False)
                    started_at = mapped_column(timestamp_type, nullable=False)
                    finished_at = mapped_column(timestamp_type, nullable=True)
                    error_code = mapped_column(Text, nullable=True)
                    error_message = mapped_column(Text, nullable=True)

                class EventRow(Base):
                    __tablename__ = "evaluation_status_events"
                    evaluation_status_event_id = mapped_column(BigInteger, primary_key=True)
                    evaluation_id = mapped_column(String(128), nullable=False)
                    event_number = mapped_column(Integer, nullable=False)
                    event_type = mapped_column(Text, nullable=False)
                    from_status = mapped_column(String(16), nullable=True)
                    to_status = mapped_column(String(16), nullable=True)
                    actor_type = mapped_column(Text, nullable=False, default="worker")
                    actor_id = mapped_column(Text, nullable=True)
                    processing_cycle = mapped_column(Integer, nullable=True)
                    attempt_number = mapped_column(SmallInteger, nullable=True)
                    occurred_at = mapped_column(timestamp_type, nullable=False)
                    metadata_json = mapped_column("metadata", JSONB, nullable=False, default=dict)

                class GroupRow(Base):
                    __tablename__ = "evaluation_submission_groups"
                    submission_group_id = mapped_column(BigInteger, primary_key=True)
                    institution_id = mapped_column(Text, nullable=True)
                    lms = mapped_column(Text, nullable=False)
                    course_id = mapped_column(Text, nullable=False)
                    assignment_id = mapped_column(Text, nullable=False)
                    user_id = mapped_column(Text, nullable=False)
                    consumed_attempts = mapped_column(Integer, nullable=False)
                    reserved_attempts = mapped_column(Integer, nullable=False)
                    created_at = mapped_column(timestamp_type, nullable=False)
                    updated_at = mapped_column(timestamp_type, nullable=False)

                class LLMInvocationRow(Base):
                    __tablename__ = "llm_invocations"
                    llm_invocation_id = mapped_column(BigInteger, primary_key=True)
                    evaluation_id = mapped_column(String(128), nullable=False)
                    evaluation_attempt_id = mapped_column(BigInteger, nullable=False)
                    call_number = mapped_column(SmallInteger, nullable=False)
                    provider = mapped_column(Text, nullable=False)
                    model = mapped_column(Text, nullable=True)
                    is_fallback = mapped_column(Boolean, nullable=False, default=False)
                    status = mapped_column(String(16), nullable=False)
                    started_at = mapped_column(timestamp_type, nullable=False)
                    finished_at = mapped_column(timestamp_type, nullable=True)
                    duration_ms = mapped_column(BigInteger, nullable=True)
                    input_tokens = mapped_column(BigInteger, nullable=True)
                    output_tokens = mapped_column(BigInteger, nullable=True)
                    token_source = mapped_column(String(16), nullable=False, default="unknown")
                    cost_amount = mapped_column(Numeric(20, 10), nullable=True)
                    cost_currency = mapped_column(String(3), nullable=True)
                    error_code = mapped_column(Text, nullable=True)
                    error_message = mapped_column(Text, nullable=True)

                class MCPInvocationRow(Base):
                    __tablename__ = "mcp_invocations"
                    mcp_invocation_id = mapped_column(BigInteger, primary_key=True)
                    evaluation_id = mapped_column(String(128), nullable=True)
                    client_id = mapped_column(Text, nullable=True)
                    institution_id = mapped_column(Text, nullable=True)
                    tool_name = mapped_column(Text, nullable=False)
                    status = mapped_column(String(16), nullable=False)
                    started_at = mapped_column(timestamp_type, nullable=False)
                    finished_at = mapped_column(timestamp_type, nullable=True)
                    duration_ms = mapped_column(BigInteger, nullable=True)
                    correlation_id = mapped_column(Text, nullable=True)
                    error_code = mapped_column(Text, nullable=True)
                    error_message = mapped_column(Text, nullable=True)

                self._AttemptRow = AttemptRow
                self._EventRow = EventRow
                self._GroupRow = GroupRow
                self._LLMInvocationRow = LLMInvocationRow
                self._MCPInvocationRow = MCPInvocationRow

            self._engine = create_async_engine(self.dsn)
            self._SessionLocal = async_sessionmaker(self._engine, class_=AsyncSession, expire_on_commit=False)
            self._Row = EvalRow
            if not self._is_postgres:
                async with self._engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
            self._ready = True
        except Exception as exc:
            raise RuntimeError(f"No se pudo inicializar EvaluationBackend: {exc}") from exc

    async def initialize(self) -> None:
        await self._ensure_ready()

    async def _get_session(self):
        await self._ensure_ready()
        if self._session is None:
            self._session = self._SessionLocal()
        return self._session

    async def create(
        self,
        evaluation_id,
        payload,
        *,
        idempotency_client_id=None,
        idempotency_key=None,
        request_sha256=None,
    ) -> None:
        session = await self._get_session()
        import sqlalchemy as sa

        Row = self._Row
        now = datetime.now(timezone.utc) if self._is_postgres else time.time()
        try:
            values = dict(
                evaluation_id=evaluation_id,
                payload=payload if self._is_postgres else json.dumps(payload, ensure_ascii=False),
                status=STATUS_PENDING,
                created_at=now,
            )
            if self._is_postgres:
                correlation = _correlation_fields(payload)
                count_attempt = bool(payload.get("contar_intento", True))
                identity = (
                    payload.get("institution_id"),
                    payload.get("id_lms") or "unknown",
                    correlation.get("course_id"),
                    correlation.get("assignment_id"),
                    correlation.get("user_id_canvas"),
                )
                attempt_tracking = "NOT_APPLICABLE"
                submission_group_id = None
                attempt_number = None
                max_attempts = payload.get("max_intentos")
                if max_attempts is None:
                    max_attempts = payload.get("max_attempts_snapshot", 2)
                if count_attempt and all(identity[1:]):
                    if max_attempts <= 0:
                        raise AttemptLimitExceeded(
                            "Máximo de intentos académicos alcanzado"
                        )
                    from sqlalchemy.dialects.postgresql import insert

                    GroupRow = self._GroupRow
                    group = GroupRow.__table__
                    statement = insert(group).values(
                        institution_id=identity[0],
                        lms=identity[1],
                        course_id=identity[2],
                        assignment_id=identity[3],
                        user_id=identity[4],
                        consumed_attempts=0,
                        reserved_attempts=1,
                        created_at=now,
                        updated_at=now,
                    )
                    statement = statement.on_conflict_do_update(
                        constraint="uq_evaluation_submission_groups_identity",
                        set_={
                            "reserved_attempts": group.c.reserved_attempts + 1,
                            "updated_at": now,
                        },
                        where=(
                            group.c.consumed_attempts + group.c.reserved_attempts
                            < max_attempts
                        ),
                    ).returning(
                        group.c.submission_group_id,
                        group.c.consumed_attempts,
                        group.c.reserved_attempts,
                    )
                    reserved = (await session.execute(statement)).first()
                    if reserved is None:
                        raise AttemptLimitExceeded(
                            "Máximo de intentos académicos alcanzado"
                        )
                    submission_group_id = reserved.submission_group_id
                    attempt_number = reserved.consumed_attempts + reserved.reserved_attempts
                    attempt_tracking = "RESERVED"
                elif count_attempt:
                    attempt_tracking = "SKIPPED_MISSING_IDENTITY"
                values.update(
                    source=payload.get("source") or payload.get("id_lms") or "unknown",
                    lms=payload.get("lms") or payload.get("id_lms"),
                    institution_id=payload.get("institution_id"),
                    course_id=correlation.get("course_id"),
                    assignment_id=correlation.get("assignment_id"),
                    user_id=correlation.get("user_id_canvas"),
                    submission_id=correlation.get("submission_id"),
                    context_id=correlation.get("context_id"),
                    submission_group_id=submission_group_id,
                    count_attempt=count_attempt,
                    attempt_tracking=attempt_tracking,
                    attempt_number=attempt_number,
                    max_attempts_snapshot=max_attempts,
                    idempotency_client_id=idempotency_client_id,
                    idempotency_key=idempotency_key,
                    request_sha256=request_sha256,
                    available_at=now,
                    updated_at=now,
                )
            row = Row(**values)
            session.add(row)
            if self._is_postgres:
                session.add(
                    self._EventRow(
                        evaluation_id=evaluation_id,
                        event_number=1,
                        event_type="evaluation_enqueued",
                        to_status=STATUS_PENDING,
                        actor_type="api",
                        occurred_at=now,
                        metadata_json={},
                    )
                )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            raise exc

    async def find_next_pending(self):
        if self._is_postgres:
            await self._ensure_ready()
            import sqlalchemy as sa

            Row = self._Row
            AttemptRow = self._AttemptRow
            EventRow = self._EventRow
            GroupRow = self._GroupRow
            now = datetime.now(timezone.utc)
            async with self._SessionLocal() as session:
                exhausted_result = await session.execute(
                    sa.select(Row)
                    .where(
                        Row.status == STATUS_EVALUATING,
                        Row.lease_expires_at <= now,
                        Row.worker_attempts >= Row.max_processing_attempts,
                    )
                    .order_by(Row.lease_expires_at.asc())
                    .limit(20)
                    .with_for_update(skip_locked=True)
                )
                exhausted_rows = exhausted_result.scalars().all()
                for exhausted in exhausted_rows:
                    previous_status = exhausted.status
                    exhausted.error = "Lease expirado tras agotar reintentos técnicos"
                    exhausted.finished_at = now
                    exhausted.updated_at = now
                    exhausted.lease_owner = None
                    exhausted.lease_expires_at = None
                    attempt_result = await session.execute(
                        sa.select(AttemptRow)
                        .where(
                            AttemptRow.evaluation_id == exhausted.evaluation_id,
                            AttemptRow.status == "RUNNING",
                        )
                        .order_by(AttemptRow.evaluation_attempt_id.desc())
                        .limit(1)
                        .with_for_update()
                    )
                    abandoned_attempt = attempt_result.scalar_one_or_none()
                    if abandoned_attempt:
                        abandoned_attempt.status = "ABANDONED"
                        abandoned_attempt.finished_at = now
                        abandoned_attempt.error_code = "LEASE_EXPIRED"
                        abandoned_attempt.error_message = exhausted.error
                    if exhausted.attempt_tracking == "RESERVED":
                        exhausted.attempt_tracking = "RELEASED"
                        await session.execute(
                            sa.update(GroupRow)
                            .where(
                                GroupRow.submission_group_id == exhausted.submission_group_id,
                                GroupRow.reserved_attempts > 0,
                            )
                            .values(
                                reserved_attempts=GroupRow.reserved_attempts - 1,
                                updated_at=now,
                            )
                        )
                    exhausted.status = STATUS_ERROR
                    event_number = await session.scalar(
                        sa.select(sa.func.coalesce(sa.func.max(EventRow.event_number), 0) + 1)
                        .where(EventRow.evaluation_id == exhausted.evaluation_id)
                    )
                    session.add(
                        EventRow(
                            evaluation_id=exhausted.evaluation_id,
                            event_number=event_number,
                            event_type="processing_exhausted",
                            from_status=previous_status,
                            to_status=STATUS_ERROR,
                            actor_type="recovery",
                            actor_id=self._worker_id,
                            processing_cycle=exhausted.processing_cycles,
                            occurred_at=now,
                            metadata_json={"reason": "lease_expired"},
                        )
                    )
                result = await session.execute(
                    sa.select(Row)
                    .where(
                        sa.or_(
                            sa.and_(
                                Row.status == STATUS_PENDING,
                                Row.available_at <= now,
                            ),
                            sa.and_(
                                Row.status == STATUS_EVALUATING,
                                Row.lease_expires_at <= now,
                                Row.worker_attempts < Row.max_processing_attempts,
                            ),
                        )
                    )
                    .order_by(Row.available_at.asc(), Row.created_at.asc())
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                row = result.scalar_one_or_none()
                if row is None:
                    if exhausted_rows:
                        await session.commit()
                    return None

                previous_status = row.status
                if previous_status == STATUS_EVALUATING:
                    abandoned = await session.execute(
                        sa.select(AttemptRow)
                        .where(
                            AttemptRow.evaluation_id == row.evaluation_id,
                            AttemptRow.status == "RUNNING",
                        )
                        .order_by(AttemptRow.evaluation_attempt_id.desc())
                        .limit(1)
                        .with_for_update()
                    )
                    abandoned_attempt = abandoned.scalar_one_or_none()
                    if abandoned_attempt:
                        abandoned_attempt.status = "ABANDONED"
                        abandoned_attempt.finished_at = now
                        abandoned_attempt.error_code = "LEASE_EXPIRED"
                row.status = STATUS_EVALUATING
                row.started_at = row.started_at or now
                row.updated_at = now
                row.worker_attempts += 1
                row.processing_cycles += 1
                row.lease_owner = self._worker_id
                row.lease_expires_at = now + timedelta(minutes=5)
                attempt_number = 1
                attempt = AttemptRow(
                    evaluation_id=row.evaluation_id,
                    processing_cycle=row.processing_cycles,
                    attempt_number=attempt_number,
                    status="RUNNING",
                    started_at=now,
                )
                session.add(attempt)
                event_number = await session.scalar(
                    sa.select(sa.func.coalesce(sa.func.max(EventRow.event_number), 0) + 1)
                    .where(EventRow.evaluation_id == row.evaluation_id)
                )
                session.add(
                    EventRow(
                        evaluation_id=row.evaluation_id,
                        event_number=event_number,
                        event_type=(
                            "evaluation_recovered"
                            if previous_status == STATUS_EVALUATING
                            else "evaluation_claimed"
                        ),
                        from_status=previous_status,
                        to_status=STATUS_EVALUATING,
                        actor_type="worker",
                        actor_id=self._worker_id,
                        processing_cycle=row.processing_cycles,
                        attempt_number=attempt_number,
                        occurred_at=now,
                        metadata_json={},
                    )
                )
                await session.flush()
                attempt_id = attempt.evaluation_attempt_id
                job = self._row_to_dict(row)
                await session.commit()
                job.update(
                    _evaluation_attempt_id=attempt_id,
                    _processing_cycle=row.processing_cycles,
                    _attempt_number=attempt_number,
                )
                return job

        session = await self._get_session()
        import sqlalchemy as sa

        Row = self._Row
        result = await session.execute(
            sa.select(Row)
            .where(Row.status == STATUS_PENDING)
            .order_by(Row.created_at.asc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return self._row_to_dict(row) if row else None

    async def find_by_idempotency(self, client_id: str, key: str):
        if not self._is_postgres:
            return None
        session = await self._get_session()
        import sqlalchemy as sa

        Row = self._Row
        result = await session.execute(
            sa.select(Row).where(
                Row.idempotency_client_id == client_id,
                Row.idempotency_key == key,
            )
        )
        row = result.scalar_one_or_none()
        return self._row_to_dict(row) if row else None

    async def record_llm_invocation(self, **invocation) -> None:
        context = _CURRENT_EVALUATION_CONTEXT.get()
        if not self._is_postgres or not context:
            return
        await self._ensure_ready()
        context["llm_call_number"] += 1
        row = self._LLMInvocationRow(
            evaluation_id=context["evaluation_id"],
            evaluation_attempt_id=context["evaluation_attempt_id"],
            call_number=context["llm_call_number"],
            provider=invocation["provider"],
            model=invocation.get("model"),
            is_fallback=invocation.get("is_fallback", False),
            status=invocation["status"],
            started_at=invocation["started_at"],
            finished_at=invocation["finished_at"],
            duration_ms=invocation["duration_ms"],
            input_tokens=invocation.get("input_tokens"),
            output_tokens=invocation.get("output_tokens"),
            token_source=invocation.get("token_source", "unknown"),
            error_code=invocation.get("error_code"),
            error_message=invocation.get("error_message"),
        )
        async with self._SessionLocal() as session:
            session.add(row)
            await session.commit()

    async def record_mcp_invocation(self, **invocation) -> None:
        context = _CURRENT_EVALUATION_CONTEXT.get()
        if not self._is_postgres or not context:
            return
        await self._ensure_ready()
        row = self._MCPInvocationRow(
            evaluation_id=context["evaluation_id"],
            client_id=context.get("client_id"),
            institution_id=context.get("institution_id"),
            tool_name=invocation["tool_name"],
            status=invocation["status"],
            started_at=invocation["started_at"],
            finished_at=invocation["finished_at"],
            duration_ms=invocation["duration_ms"],
            correlation_id=context["evaluation_id"],
            error_code=invocation.get("error_code"),
            error_message=invocation.get("error_message"),
        )
        async with self._SessionLocal() as session:
            session.add(row)
            await session.commit()

    async def set_status(self, evaluation_id, status, result=None, error=None) -> None:
        if self._is_postgres:
            await self._set_postgres_status(evaluation_id, status, result, error)
            return

        session = await self._get_session()
        import sqlalchemy as sa

        Row = self._Row
        try:
            result_stmt = await session.execute(sa.select(Row).where(Row.evaluation_id == evaluation_id))
            row = result_stmt.scalar_one_or_none()
            if row is None:
                return
            row.status = status
            if status == STATUS_EVALUATING:
                row.started_at = datetime.now(timezone.utc) if self._is_postgres else time.time()
            if status in (STATUS_EVALUATED, STATUS_ERROR):
                row.finished_at = datetime.now(timezone.utc) if self._is_postgres else time.time()
            if result is not None:
                row.result = result if self._is_postgres else json.dumps(result, ensure_ascii=False)
            if error is not None:
                row.error = error
            if self._is_postgres:
                row.updated_at = datetime.now(timezone.utc)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            raise exc

    async def _set_postgres_status(self, evaluation_id, status, result, error) -> None:
        await self._ensure_ready()
        import sqlalchemy as sa

        Row = self._Row
        AttemptRow = self._AttemptRow
        EventRow = self._EventRow
        now = datetime.now(timezone.utc)
        async with self._SessionLocal() as session:
            try:
                query = await session.execute(
                    sa.select(Row).where(Row.evaluation_id == evaluation_id).with_for_update()
                )
                row = query.scalar_one_or_none()
                if row is None:
                    return

                previous_status = row.status
                if result is not None:
                    row.result = result
                if error is not None:
                    row.error = error
                row.updated_at = now

                if status == STATUS_EVALUATED and row.attempt_tracking == "RESERVED":
                    await session.execute(
                        sa.update(self._GroupRow)
                        .where(
                            self._GroupRow.submission_group_id == row.submission_group_id,
                            self._GroupRow.reserved_attempts > 0,
                        )
                        .values(
                            reserved_attempts=self._GroupRow.reserved_attempts - 1,
                            consumed_attempts=self._GroupRow.consumed_attempts + 1,
                            updated_at=now,
                        )
                    )
                    row.attempt_tracking = "CONSUMED"

                if status == STATUS_ERROR and row.worker_attempts < row.max_processing_attempts:
                    query = await session.execute(
                        sa.select(AttemptRow)
                        .where(
                            AttemptRow.evaluation_id == evaluation_id,
                            AttemptRow.status == "RUNNING",
                        )
                        .order_by(AttemptRow.evaluation_attempt_id.desc())
                        .limit(1)
                        .with_for_update()
                    )
                    attempt = query.scalar_one_or_none()
                    if attempt:
                        attempt.status = "FAILED"
                        attempt.finished_at = now
                        attempt.error_code = "PROCESSING_ERROR"
                        attempt.error_message = error
                    row.status = STATUS_PENDING
                    row.available_at = now + timedelta(seconds=min(2 ** row.worker_attempts, 30))
                    row.lease_owner = None
                    row.lease_expires_at = None
                    row.finished_at = None
                    event_type = "retry_scheduled"
                    target_status = STATUS_PENDING
                else:
                    event_type = "status_changed"
                    target_status = status
                    if status in (STATUS_EVALUATED, STATUS_ERROR):
                        if status == STATUS_ERROR and row.attempt_tracking == "RESERVED":
                            await session.execute(
                                sa.update(self._GroupRow)
                                .where(
                                    self._GroupRow.submission_group_id == row.submission_group_id,
                                    self._GroupRow.reserved_attempts > 0,
                                )
                                .values(
                                    reserved_attempts=self._GroupRow.reserved_attempts - 1,
                                    updated_at=now,
                                )
                            )
                            row.attempt_tracking = "RELEASED"
                        row.status = status
                        row.finished_at = now
                        row.lease_owner = None
                        row.lease_expires_at = None
                        query = await session.execute(
                            sa.select(AttemptRow)
                            .where(
                                AttemptRow.evaluation_id == evaluation_id,
                                AttemptRow.status == "RUNNING",
                            )
                            .order_by(AttemptRow.evaluation_attempt_id.desc())
                            .limit(1)
                            .with_for_update()
                        )
                        attempt = query.scalar_one_or_none()
                        if attempt:
                            attempt.status = "SUCCEEDED" if status == STATUS_EVALUATED else "FAILED"
                            attempt.finished_at = now
                            attempt.error_code = None if status == STATUS_EVALUATED else "PROCESSING_ERROR"
                            attempt.error_message = error
                    else:
                        row.status = status

                event_number = await session.scalar(
                    sa.select(sa.func.coalesce(sa.func.max(EventRow.event_number), 0) + 1)
                    .where(EventRow.evaluation_id == evaluation_id)
                )
                session.add(
                    EventRow(
                        evaluation_id=evaluation_id,
                        event_number=event_number,
                        event_type=event_type,
                        from_status=previous_status,
                        to_status=target_status,
                        actor_type="worker",
                        actor_id=self._worker_id,
                        processing_cycle=row.processing_cycles,
                        attempt_number=1,
                        occurred_at=now,
                        metadata_json={},
                    )
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def get(self, evaluation_id):
        session = await self._get_session()
        import sqlalchemy as sa

        Row = self._Row
        result = await session.execute(sa.select(Row).where(Row.evaluation_id == evaluation_id))
        row = result.scalar_one_or_none()
        return self._row_to_dict(row) if row else None

    async def stats(self):
        session = await self._get_session()
        import sqlalchemy as sa

        Row = self._Row
        result = await session.execute(
            sa.select(Row.status, sa.func.count(Row.evaluation_id)).group_by(Row.status)
        )
        counts = {s: 0 for s in ALL_STATUS}
        total = 0
        for status, n in result.all():
            counts[status] = int(n)
            total += int(n)
        return {"total": total, **counts, "backend": self.name}

    @staticmethod
    def _row_to_dict(r) -> Dict[str, Any]:
        from datetime import datetime

        def _load(value):
            if not isinstance(value, str):
                return value
            try:
                return json.loads(value) if value else None
            except Exception:
                return None

        def _timestamp(value):
            return value.timestamp() if isinstance(value, datetime) else value

        return {
            "evaluation_id": r.evaluation_id,
            "payload": _load(r.payload) or {},
            "institution_id": getattr(r, "institution_id", None),
            "status": r.status,
            "result": _load(r.result),
            "error": r.error,
            "created_at": _timestamp(r.created_at),
            "started_at": _timestamp(r.started_at),
            "finished_at": _timestamp(r.finished_at),
            "idempotency_client_id": getattr(r, "idempotency_client_id", None),
            "idempotency_key": getattr(r, "idempotency_key", None),
            "request_sha256": getattr(r, "request_sha256", None),
        }

    async def close(self):
        try:
            if self._session is not None:
                await self._session.close()
                self._session = None
        except Exception:
            pass
        if self._engine is not None:
            try:
                await self._engine.dispose()
            except Exception:
                pass
            self._engine = None


# =============================================================================
# Fábrica
# =============================================================================

def _is_memory(dsn: str) -> bool:
    dsn = (dsn or "").strip()
    if not dsn or dsn.lower() in ("memory", "mem"):
        return True
    supported = ("sqlite", "postgresql", "postgres")
    return not any(dsn.split("://", 1)[0].lower().startswith(d) for d in supported)


def make_evaluation_backend(dsn: Optional[str] = None, *, logger_override=None) -> EvaluationBackend:
    dsn = (dsn or "").strip()
    log = logger_override or logger
    if _is_memory(dsn):
        log.info("EvaluationQueue: DSN vacío/'memory' → backend en memoria (transitorio).")
        return InMemoryEvaluationBackend()
    try:
        import sqlalchemy  # noqa: F401

        return SQLAlchemyEvaluationBackend(dsn)
    except Exception as exc:
        log.warning("EvaluationQueue: no se pudo usar '%s' (%s) → memoria.", dsn, exc)
        return InMemoryEvaluationBackend()


# =============================================================================
# Manager de la cola + worker
# =============================================================================

def _gen_id(payload: Dict[str, Any]) -> str:
    import uuid

    return f"eval_{uuid.uuid4().hex}"


def _correlation_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Select per-job identifiers without copying submission data into context."""
    fields = {
        "context_id": payload.get("context_id"),
        "submission_id": payload.get("submission_id") or (payload.get("submission") or {}).get("id"),
        "course_id": payload.get("course_id") or payload.get("id_curso"),
        "assignment_id": payload.get("assignment_id"),
        "user_id_canvas": (
            payload.get("user_id_canvas")
            or payload.get("user_id")
            or payload.get("id_usuario")
            or (payload.get("submission") or {}).get("user_id")
            or (payload.get("submission") or {}).get("userId")
        ),
    }
    return {key: value for key, value in fields.items() if value is not None}


class EvaluationManager:
    """
    Encola solicitudes de evaluación, exponen estado y procesa en background
    (1 evaluación a la vez) mediante un worker asíncrono.
    """

    def __init__(
        self,
        backend: Optional[EvaluationBackend] = None,
        evaluator: Optional[Any] = None,
        poll_interval: float = 1.5,
    ):
        self.backend = backend or InMemoryEvaluationBackend()
        # `evaluator` es un callable async: await evaluator(payload) -> result dict
        self._evaluator = evaluator
        self._poll_interval = poll_interval
        self._worker_task: Optional[asyncio.Task] = None

    # -- public API ------------------------------------------------------
    async def initialize(self) -> None:
        """Initialize the configured backend, degrading explicitly to memory on failure."""
        try:
            await self.backend.initialize()
        except Exception as exc:
            if getattr(self.backend, "_is_postgres", False):
                raise RuntimeError(
                    "PostgreSQL configurado, pero no se pudo inicializar la cola durable"
                ) from exc
            logger.warning(
                "EvaluationQueue: no se pudo inicializar el backend durable (%s) → memoria.",
                exc,
            )
            await self.backend.close()
            self.backend = InMemoryEvaluationBackend()

    async def enqueue(
        self,
        payload: Dict[str, Any],
        *,
        idempotency_client_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        request_sha256 = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if idempotency_key:
            if not idempotency_client_id:
                raise ValueError("La clave de idempotencia requiere un cliente identificado")
            existing = await self.backend.find_by_idempotency(
                idempotency_client_id, idempotency_key
            )
            if existing:
                if existing.get("request_sha256") != request_sha256:
                    raise ValueError("Idempotency-Key ya fue usada con otro payload")
                return {
                    "evaluation_id": existing["evaluation_id"],
                    "estado": existing["status"],
                    **_correlation_fields(existing.get("payload") or {}),
                }

        eid = _gen_id(payload)
        correlation = _correlation_fields(payload)
        try:
            await self.backend.create(
                eid,
                payload,
                idempotency_client_id=idempotency_client_id,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256 if idempotency_key else None,
            )
        except Exception as exc:
            if idempotency_key and idempotency_client_id:
                existing = await self.backend.find_by_idempotency(
                    idempotency_client_id, idempotency_key
                )
                if existing:
                    if existing.get("request_sha256") != request_sha256:
                        raise ValueError(
                            "Idempotency-Key ya fue usada con otro payload"
                        ) from exc
                    return {
                        "evaluation_id": existing["evaluation_id"],
                        "estado": existing["status"],
                        **_correlation_fields(existing.get("payload") or {}),
                    }
            logger.warning("EvaluationQueue: fallo al encolar (%s).", exc)
            raise
        logger.info("EvaluationQueue: evaluación %s encolada (PENDING).", eid)
        return {"evaluation_id": eid, "estado": STATUS_PENDING, **correlation}

    async def get_status(
        self,
        evaluation_id: str,
        *,
        institution_id: Optional[str] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        row = await self.backend.get(evaluation_id)
        if row is None:
            return 404, {"estado": "NOT_FOUND", "evaluation_id": evaluation_id}
        if institution_id is not None:
            row_institution_id = row.get("institution_id") or (
                (row.get("payload") or {}).get("institution_id")
            )
            if row_institution_id != institution_id:
                return 404, {"estado": "NOT_FOUND", "evaluation_id": evaluation_id}

        # Mientras no esté terminado, devolvemos solo marcador de estado.
        if row.get("status") != STATUS_EVALUATED:
            body: Dict[str, Any] = {
                "evaluation_id": evaluation_id,
                "estado": row["status"],            # PENDING | EVALUATING
                **_correlation_fields(row.get("payload") or {}),
            }
            if row.get("status") == STATUS_ERROR:
                body["error"] = row.get("error")
            return 200, body

        # ESTADO EVALUADO: el `result` guardado ES el canon (status:"EVALUADO" + nota +
        # feedback + rubric_assessment_canvas + ...). Se devuelve directo, sin envolver
        # en capas de control que duplicarían. (Aditivo para futuro LMS: añadir aquí el
        # adaptador de formato — ver ARQUITECTURA_BASE.md, sección 7.6.4 "Opción C".)
        canon = row.get("result") or {}
        if not isinstance(canon, dict):
            canon = {"evaluation_id": evaluation_id, "estado": STATUS_EVALUATED}
        canon.setdefault("status", STATUS_EVALUATED)
        canon.setdefault("evaluation_id", evaluation_id)
        for key, value in _correlation_fields(row.get("payload") or {}).items():
            canon.setdefault(key, value)
        return 200, canon

    async def process_one(self) -> bool:
        """Procesa la próxima evaluación PENDING. Devuelve True si procesó algo."""
        row = await self.backend.find_next_pending()
        if row is None:
            return False
        eid = row["evaluation_id"]
        payload = row.get("payload") or {}
        if "_evaluation_attempt_id" not in row:
            await self.backend.set_status(eid, STATUS_EVALUATING)
        logger.info("EvaluationQueue: procesando %s (EVALUATING)...", eid)
        telemetry_token = None
        if row.get("_evaluation_attempt_id"):
            telemetry_token = _CURRENT_EVALUATION_CONTEXT.set(
                {
                    "evaluation_id": eid,
                    "evaluation_attempt_id": row["_evaluation_attempt_id"],
                    "institution_id": payload.get("institution_id"),
                    "client_id": row.get("idempotency_client_id"),
                    "llm_call_number": 0,
                }
            )
        try:
            if self._evaluator is None:
                raise RuntimeError("Evaluator no configurado (EvaluationManager._evaluator)")
            resultado = await self._evaluator(payload)
            if isinstance(resultado, dict):
                resultado = dict(resultado)
                resultado["evaluation_id"] = eid
                for key, value in _correlation_fields(payload).items():
                    resultado.setdefault(key, value)
            await self.backend.set_status(eid, STATUS_EVALUATED, result=resultado)
            logger.info("EvaluationQueue: %s EVALUATED.", eid)
        except Exception as exc:
            logger.error("EvaluationQueue: %s ERROR: %s", eid, exc)
            await self.backend.set_status(eid, STATUS_ERROR, error=str(exc))
        finally:
            if telemetry_token is not None:
                _CURRENT_EVALUATION_CONTEXT.reset(telemetry_token)
        return True

    async def stats(self) -> Dict[str, Any]:
        return await self.backend.stats()

    # -- worker -----------------------------------------------------------
    def start_worker(self) -> None:
        """Inicia el bucle worker en background (1 evaluación a la vez)."""
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._worker_task = asyncio.create_task(self._run_worker())  # type: ignore[assignment]

    async def _run_worker(self) -> None:
        logger.info("EvaluationQueue: worker iniciado (procesa 1 a la vez).")
        while True:
            try:
                await self.process_one()
            except Exception as exc:
                logger.error("EvaluationQueue: error en worker: %s", exc)
            await asyncio.sleep(self._poll_interval)

    async def stop_worker(self) -> None:
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        try:
            await self.backend.close()
        except Exception:
            pass


# Singleton global (inicializado por server.py)
_manager: Optional[EvaluationManager] = None


def get_manager() -> EvaluationManager:
    global _manager
    if _manager is None:
        _manager = EvaluationManager()
    return _manager


def init_evaluation_queue(
    dsn: str = "",
    evaluator: Optional[Any] = None,
    poll_interval: float = 1.5,
) -> EvaluationManager:
    global _manager
    backend = make_evaluation_backend(dsn)
    _manager = EvaluationManager(backend=backend, evaluator=evaluator, poll_interval=poll_interval)
    return _manager


__all__ = [
    "STATUS_PENDING",
    "STATUS_EVALUATING",
    "STATUS_EVALUATED",
    "STATUS_ERROR",
    "EvaluationBackend",
    "InMemoryEvaluationBackend",
    "SQLAlchemyEvaluationBackend",
    "make_evaluation_backend",
    "EvaluationManager",
    "get_manager",
    "init_evaluation_queue",
]