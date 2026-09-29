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
import json
import time
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


# =============================================================================
# Backends (memoria + SQLAlchemy async)
# =============================================================================

class EvaluationBackend:
    name = "abstract"

    async def create(self, evaluation_id: str, payload: Dict[str, Any]) -> None:
        raise NotImplementedError

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

    async def create(self, evaluation_id: str, payload: Dict[str, Any]) -> None:
        self._rows[evaluation_id] = {
            "evaluation_id": evaluation_id,
            "payload": payload,
            "status": STATUS_PENDING,
            "result": None,
            "error": None,
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
        }

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
        self._engine = None
        self._SessionLocal = None
        self._session = None
        self._ready = False
        self._Row = None

    async def _ensure_ready(self) -> None:
        if self._ready:
            return
        try:
            from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
            from sqlalchemy import Text, Integer, Float
            from sqlalchemy.ext.asyncio import (
                AsyncSession,
                async_sessionmaker,
                create_async_engine,
            )
            from pathlib import Path

            if self.dsn.startswith("sqlite+aiosqlite:///./"):
                rel = self.dsn.split("///./", 1)[1]
                Path(rel).parent.mkdir(parents=True, exist_ok=True)

            class Base(DeclarativeBase):
                pass

            class EvalRow(Base):
                __tablename__ = "evaluation_jobs"
                evaluation_id: Mapped[str] = mapped_column(Text, primary_key=True)
                payload: Mapped[str] = mapped_column(Text, nullable=False)      # JSON
                status: Mapped[str] = mapped_column(Text, nullable=False, default=STATUS_PENDING)
                result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
                error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
                created_at: Mapped[float] = mapped_column(Float, nullable=False)
                started_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
                finished_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

            self._engine = create_async_engine(self.dsn)
            self._SessionLocal = async_sessionmaker(self._engine, class_=AsyncSession, expire_on_commit=False)
            self._Row = EvalRow
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            self._ready = True
        except Exception as exc:
            raise RuntimeError(f"No se pudo inicializar EvaluationBackend: {exc}") from exc

    async def _get_session(self):
        await self._ensure_ready()
        if self._session is None or self._session.closed:
            self._session = self._SessionLocal()
        return self._session

    async def create(self, evaluation_id, payload) -> None:
        session = await self._get_session()
        import sqlalchemy as sa

        Row = self._Row
        now = time.time()
        try:
            row = Row(
                evaluation_id=evaluation_id,
                payload=json.dumps(payload, ensure_ascii=False),
                status=STATUS_PENDING,
                created_at=now,
            )
            session.add(row)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            raise exc

    async def find_next_pending(self):
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

    async def set_status(self, evaluation_id, status, result=None, error=None) -> None:
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
                row.started_at = time.time()
            if status in (STATUS_EVALUATED, STATUS_ERROR):
                row.finished_at = time.time()
            if result is not None:
                row.result = json.dumps(result, ensure_ascii=False)
            if error is not None:
                row.error = error
            await session.commit()
        except Exception as exc:
            await session.rollback()
            raise exc

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
        def _load(s):
            try:
                return json.loads(s) if s else None
            except Exception:
                return None

        return {
            "evaluation_id": r.evaluation_id,
            "payload": _load(r.payload) or {},
            "status": r.status,
            "result": _load(r.result),
            "error": r.error,
            "created_at": r.created_at,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
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

        backend = SQLAlchemyEvaluationBackend(dsn)
        import asyncio

        async def _probe():
            await backend._ensure_ready()
            await backend.close()

        asyncio.run(_probe())
        return backend
    except Exception as exc:
        log.warning("EvaluationQueue: no se pudo usar '%s' (%s) → memoria.", dsn, exc)
        return InMemoryEvaluationBackend()


# =============================================================================
# Manager de la cola + worker
# =============================================================================

def _gen_id(payload: Dict[str, Any]) -> str:
    import uuid

    # Preferir id estable basado en submission_id si existe.
    sid = payload.get("submission_id") or (
        payload.get("submission") or {}
    ).get("id")
    if sid:
        return f"eval_{str(sid)}"
    return f"eval_{uuid.uuid4().hex[:12]}"


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
    async def enqueue(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        eid = _gen_id(payload)
        existing = await self.backend.get(eid)
        if existing and existing.get("status") in (STATUS_EVALUATING, STATUS_PENDING):
            # Ya en cola: devolver el estado en curso (evita duplicar).
            return {"evaluation_id": eid, "estado": existing["status"]}
        if existing and existing.get("status") == STATUS_EVALUATED:
            # Reintento del mismo submission ya evaluado: NO se re-encola.
            # El LMS debe consultar GET /estado/{id} para obtener el canon.
            return {"evaluation_id": eid, "estado": STATUS_EVALUATED}
        try:
            await self.backend.create(eid, payload)
        except Exception as exc:
            logger.warning("EvaluationQueue: fallo al encolar (%s).", exc)
            raise
        logger.info("EvaluationQueue: evaluación %s encolada (PENDING).", eid)
        return {"evaluation_id": eid, "estado": STATUS_PENDING}

    async def get_status(self, evaluation_id: str) -> Tuple[int, Dict[str, Any]]:
        row = await self.backend.get(evaluation_id)
        if row is None:
            return 404, {"estado": "NOT_FOUND", "evaluation_id": evaluation_id}

        # Mientras no esté terminado, devolvemos solo marcador de estado.
        if row.get("status") != STATUS_EVALUATED:
            body: Dict[str, Any] = {
                "evaluation_id": evaluation_id,
                "estado": row["status"],            # PENDING | EVALUATING
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
        return 200, canon

    async def process_one(self) -> bool:
        """Procesa la próxima evaluación PENDING. Devuelve True si procesó algo."""
        row = await self.backend.find_next_pending()
        if row is None:
            return False
        eid = row["evaluation_id"]
        payload = row.get("payload") or {}
        await self.backend.set_status(eid, STATUS_EVALUATING)
        logger.info("EvaluationQueue: procesando %s (EVALUATING)...", eid)
        try:
            if self._evaluator is None:
                raise RuntimeError("Evaluator no configurado (EvaluationManager._evaluator)")
            resultado = await self._evaluator(payload)
            await self.backend.set_status(eid, STATUS_EVALUATED, result=resultado)
            logger.info("EvaluationQueue: %s EVALUATED.", eid)
        except Exception as exc:
            logger.error("EvaluationQueue: %s ERROR: %s", eid, exc)
            await self.backend.set_status(eid, STATUS_ERROR, error=str(exc))
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