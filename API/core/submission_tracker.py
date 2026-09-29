# =============================================================================
# API/core/submission_tracker.py
# -----------------------------------------------------------------------------
# Control de envíos y crónica de evaluaciones (FASE 3 · afinar MCP de ensayos).
#
# Propósito:
#   - Llevar un registro durable de cuántas veces un estudiante (user_id) ha
#     enviado una misma tarea (course_id + assignment_id) para ser evaluada.
#   - Limitar el número de envíos evaluables por tarea (default 2).
#   - Guardar la crónica (submission_id, nota, modelo, timestamps) en SQLite o
#     PostgreSQL para métricas posteriores.
#
# Almacenamiento:
#   - SQLAlchemy async (mismo patrón que `context_backend`): SQLite+Postgres.
#   - Si la BD no está disponible, degrada a memoria transitoria con aviso
#     (nunca bloquea el pipeline).
#
# Clave de agrupación de intentos: (course_id, assignment_id, user_id).
# Un mismo estudiante puede entregar varias veces (submission_id distinto en
# Canvas); aquí contamos las entregas EVALUADAS de ese grupo hasta `max_attempts`.
# =============================================================================

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from API.core.logging import get_logger
except ImportError:  # pragma: no cover
    from core.logging import get_logger

logger = get_logger("core.submission_tracker")

DEFAULT_MAX_ATTEMPTS = 2


# =============================================================================
# Backends (memoria transitoria + SQLAlchemy async)
# =============================================================================

class SubmissionBackend:
    """Interfaz de almacenamiento de envíos."""

    name = "abstract"

    async def register(
        self,
        *,
        submission_id: str,
        user_id: str,
        course_id: str,
        assignment_id: str,
        nota: Optional[float] = None,
        escala_maxima: Optional[float] = None,
        modelo: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Registra (o actualiza) una entrega evaluada y devuelve la fila."""
        raise NotImplementedError

    async def count_group(
        self,
        *,
        user_id: str,
        course_id: str,
        assignment_id: str,
    ) -> int:
        """Número de entregas evaluadas para la tarea de ese estudiante."""
        raise NotImplementedError

    async def get(self, submission_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    async def history(self, user_id: str) -> List[Dict[str, Any]]:
        """Crónica de envíos de un estudiante (para métricas)."""
        raise NotImplementedError

    async def stats(self) -> Dict[str, Any]:
        raise NotImplementedError

    async def close(self) -> None:
        pass


class InMemorySubmissionBackend(SubmissionBackend):
    """Backend transitorio (dev / fallback). Se indexa por GRUPO
    (course_id, assignment_id, user_id) y acumula `intentos` por grupo."""

    name = "memory"

    def __init__(self) -> None:
        # Clave compuesta -> registro del grupo (con contador de intentos).
        self._groups: Dict[tuple, Dict[str, Any]] = {}
        # submission_id -> registro (para get(h) y métricas).
        self._subs: Dict[str, Dict[str, Any]] = {}
        self._seq = 0

    @staticmethod
    def _grupo(user_id, course_id, assignment_id) -> tuple:
        return (course_id or "", assignment_id or "", user_id or "")

    async def register(self, **kw) -> Dict[str, Any]:
        user_id = kw.get("user_id") or ""
        course_id = kw.get("course_id") or ""
        assignment_id = kw.get("assignment_id") or ""
        sid = kw.get("submission_id") or f"sub_{int(time.time() * 1000)}"
        grupo = self._grupo(user_id, course_id, assignment_id)
        self._seq += 1
        now = time.time()

        prev = self._groups.get(grupo)
        intentos = (prev.get("intentos", 0) if prev else 0) + 1
        row = {
            "submission_id": sid,
            "user_id": user_id,
            "course_id": course_id,
            "assignment_id": assignment_id,
            "nota": kw.get("nota"),
            "escala_maxima": kw.get("escala_maxima"),
            "modelo": kw.get("modelo"),
            "intentos": intentos,
            "created_at": prev.get("created_at", now) if prev else now,
            "last_evaluated_at": now,
        }
        self._groups[grupo] = row
        self._subs[sid] = row
        return dict(row)

    async def count_group(self, *, user_id, course_id, assignment_id) -> int:
        row = self._groups.get(self._grupo(user_id, course_id, assignment_id))
        return row.get("intentos", 0) if row else 0

    async def get(self, submission_id):
        return dict(self._subs[submission_id]) if submission_id in self._subs else None

    async def history(self, user_id):
        # Único registro por grupo (el más reciente de cada curso/tarea).
        out = []
        for grupo, r in self._groups.items():
            if r.get("user_id") == user_id:
                out.append(dict(r))
        # Ordenar por created_at ascendente.
        out.sort(key=lambda x: x.get("created_at", 0))
        return out

    async def stats(self):
        total = sum(r.get("intentos", 1) for r in self._groups.values())
        return {"total_envios": total, "grupos": len(self._groups), "backend": self.name}


class SQLAlchemySubmissionBackend(SubmissionBackend):
    """
    Backend durable sobre SQLAlchemy async (SQLite + PostgreSQL),
    tabla `submissions`.
    """

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
            from sqlalchemy import Text, Integer, Float, PrimaryKeyConstraint
            from sqlalchemy.ext.asyncio import (
                AsyncSession,
                async_sessionmaker,
                create_async_engine,
            )
            from pathlib import Path

            # Crear dir para SQLite relativo.
            if self.dsn.startswith("sqlite+aiosqlite:///./"):
                rel = self.dsn.split("///./", 1)[1]
                Path(rel).parent.mkdir(parents=True, exist_ok=True)

            class Base(DeclarativeBase):
                pass

            class SubmissionRow(Base):
                """Una fila por GRUPO (course+assignment+user) con contador de intentos."""
                __tablename__ = "submission_groups"
                __table_args__ = (
                    PrimaryKeyConstraint("course_id", "assignment_id", "user_id"),
                )
                course_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
                assignment_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
                user_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
                # Último submission_id y resultado evaluado (historia del grupo).
                submission_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
                nota: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
                escala_maxima: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
                modelo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
                intentos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
                created_at: Mapped[float] = mapped_column(Float, nullable=False)
                last_evaluated_at: Mapped[float] = mapped_column(Float, nullable=False)

            self._engine = create_async_engine(self.dsn)
            self._SessionLocal = async_sessionmaker(self._engine, class_=AsyncSession, expire_on_commit=False)
            self._Row = SubmissionRow
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            self._ready = True
        except Exception as exc:
            raise RuntimeError(f"No se pudo inicializar SubmissionTracker: {exc}") from exc

    async def _get_session(self):
        await self._ensure_ready()
        if self._session is None or self._session.closed:
            self._session = self._SessionLocal()
        return self._session

    async def register(self, **kw) -> Dict[str, Any]:
        session = await self._get_session()
        import sqlalchemy as sa

        Row = self._Row
        user_id = kw.get("user_id") or ""
        course_id = kw.get("course_id") or ""
        assignment_id = kw.get("assignment_id") or ""
        sid = kw.get("submission_id") or ""
        now = time.time()
        try:
            result = await session.execute(
                sa.select(Row).where(
                    Row.course_id == course_id,
                    Row.assignment_id == assignment_id,
                    Row.user_id == user_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = Row(
                    course_id=course_id,
                    assignment_id=assignment_id,
                    user_id=user_id,
                    submission_id=sid,
                    nota=kw.get("nota"),
                    escala_maxima=kw.get("escala_maxima"),
                    modelo=kw.get("modelo"),
                    intentos=1,
                    created_at=now,
                    last_evaluated_at=now,
                )
                session.add(row)
            else:
                row.submission_id = sid
                row.nota = kw.get("nota", row.nota)
                row.escala_maxima = kw.get("escala_maxima", row.escala_maxima)
                row.modelo = kw.get("modelo", row.modelo)
                row.intentos = row.intentos + 1
                row.last_evaluated_at = now
            await session.commit()
            return self._row_to_dict(row)
        except Exception as exc:
            await session.rollback()
            raise exc

    async def count_group(self, *, user_id, course_id, assignment_id) -> int:
        try:
            session = await self._get_session()
            import sqlalchemy as sa

            Row = self._Row
            result = await session.execute(
                sa.select(Row.intentos).where(
                    Row.user_id == user_id,
                    Row.course_id == course_id,
                    Row.assignment_id == assignment_id,
                )
            )
            row = result.scalar_one_or_none()
            return int(row) if row else 0
        except Exception as exc:
            logger.warning("SubmissionTracker: fallo count_group (%s).", exc)
            raise

    async def get(self, submission_id):
        # Ya no es clave primaria; devolvemos la fila del grupo que lo usó como
        # último envío (o ninguna).
        session = await self._get_session()
        import sqlalchemy as sa

        Row = self._Row
        result = await session.execute(sa.select(Row).where(Row.submission_id == submission_id))
        row = result.scalar_one_or_none()
        return self._row_to_dict(row) if row else None

    async def history(self, user_id):
        session = await self._get_session()
        import sqlalchemy as sa

        Row = self._Row
        result = await session.execute(sa.select(Row).where(Row.user_id == user_id).order_by(Row.created_at))
        rows = result.scalars().all()
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(r) -> Dict[str, Any]:
        return {
            "submission_id": r.submission_id,
            "course_id": r.course_id,
            "assignment_id": r.assignment_id,
            "user_id": r.user_id,
            "nota": r.nota,
            "escala_maxima": r.escala_maxima,
            "modelo": r.modelo,
            "intentos": r.intentos,
            "created_at": r.created_at,
            "last_evaluated_at": r.last_evaluated_at,
        }

    async def stats(self):
        session = await self._get_session()
        import sqlalchemy as sa

        Row = self._Row
        result = await session.execute(sa.select(sa.func.count(Row.course_id), sa.func.coalesce(sa.func.sum(Row.intentos), 0)))
        grupos, total = result.first()
        return {"total_envios": int(total or 0), "grupos": int(grupos or 0), "backend": self.name}

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
# Fábrica y controlador
# =============================================================================

def _is_memory(dsn: str) -> bool:
    dsn = (dsn or "").strip()
    if not dsn or dsn.lower() in ("memory", "mem"):
        return True
    supported = ("sqlite", "postgresql", "postgres")
    return not any(dsn.split("://", 1)[0].lower().startswith(d) for d in supported)


def _make_memory_backend() -> InMemorySubmissionBackend:
    return InMemorySubmissionBackend()


def make_submission_backend(dsn: Optional[str] = None, *, logger_override=None) -> SubmissionBackend:
    """
    Resuelve el backend de envíos. Degrada a memoria si la BD no está disponible.
    """
    dsn = (dsn or "").strip()
    log = logger_override or logger

    if _is_memory(dsn):
        log.info("SubmissionTracker: DSN vacío/'memory' → backend en memoria (transitorio).")
        return _make_memory_backend()

    try:
        from sqlalchemy import __version__ as _sq  # noqa: F401  (driver disponible)
        _driver_ok = True
    except ImportError:
        _driver_ok = False

    if not _driver_ok:
        log.warning("SubmissionTracker: sqlalchemy no instalado → memoria.")
        return _make_memory_backend()

    try:
        backend = SQLAlchemySubmissionBackend(dsn)
        import asyncio

        async def _probe():
            await backend._ensure_ready()
            await backend.close()

        asyncio.run(_probe())
        return backend
    except Exception as exc:
        log.warning("SubmissionTracker: no se pudo usar '%s' (%s) → memoria.", dsn, exc)
        return _make_memory_backend()


class SubmissionTracker:
    """
    Controlador de envíos evaluables con límite de intentos por tarea-estudiante.
    """

    def __init__(self, backend: Optional[SubmissionBackend] = None, max_attempts: int = DEFAULT_MAX_ATTEMPTS):
        self.backend = backend or _make_memory_backend()
        self.max_attempts = max_attempts

    async def check_and_register(
        self,
        *,
        submission_id: str,
        user_id: str,
        course_id: str,
        assignment_id: str,
        nota: Optional[float] = None,
        escala_maxima: Optional[float] = None,
        modelo: Optional[str] = None,
        max_attempts: Optional[int] = None,
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        Verifica si el estudiante puede ser evaluado de nuevo y registra el envío.

        Regla: número de entregas evaluadas del grupo (course+assignment+user)
        < `max_attempts` (o el del constructor si no se indica) → se permite.
        En caso contrario devuelve (False, última crónica, motivo de bloqueo).

        Returns:
            (permitido, entry/último registro, motivo)
        """
        limite = self.max_attempts if max_attempts is None else max_attempts
        try:
            attempts = await self.backend.count_group(user_id=user_id, course_id=course_id, assignment_id=assignment_id)
        except Exception as exc:
            logger.warning("SubmissionTracker: fallo al contar intentos (%s). Se permite con aviso.", exc)
            attempts = 0

        if attempts >= limite:
            ultimo = None
            try:
                hist = await self.backend.history(user_id)
                ultimo = hist[-1] if hist else None
            except Exception:
                pass
            return False, ultimo, "Máximo de envíos evaluables alcanzado"

        try:
            entry = await self.backend.register(
                submission_id=submission_id,
                user_id=user_id,
                course_id=course_id,
                assignment_id=assignment_id,
                nota=nota,
                escala_maxima=escala_maxima,
                modelo=modelo,
            )
        except Exception as exc:
            logger.warning("SubmissionTracker: fallo al registrar envío (%s); se permite evaluar igualmente.", exc)
            return True, {
                "submission_id": submission_id,
                "user_id": user_id,
                "course_id": course_id,
                "assignment_id": assignment_id,
                "nota": nota,
                "escala_maxima": escala_maxima,
                "modelo": modelo,
                "intentos": 1,
            }, "registrado_con_aviso"

        return True, entry, "ok"

    async def check_allowed(
        self,
        *,
        user_id: str,
        course_id: str,
        assignment_id: str,
        max_attempts: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """
        Verifica (sin registrar) si el estudiante puede ser evaluado de nuevo.
        No consume el intento; útil para rechazar temprano y no gastar tokens.

        Args:
            max_attempts: límite puntual que sobrescribe el del constructor.

        Returns:
            (permitido: bool, motivo: str)
        """
        limite = self.max_attempts if max_attempts is None else max_attempts
        try:
            attempts = await self.backend.count_group(
                user_id=user_id, course_id=course_id, assignment_id=assignment_id
            )
        except Exception as exc:
            logger.warning("SubmissionTracker: fallo al contar intentos (%s); se permite.", exc)
            attempts = 0
        if attempts >= limite:
            return False, "Máximo de envíos evaluables alcanzado (%s)" % limite
        return True, "ok"

    async def register(
        self,
        *,
        submission_id: str,
        user_id: str,
        course_id: str,
        assignment_id: str,
        nota: Optional[float] = None,
        escala_maxima: Optional[float] = None,
        modelo: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        Registra una entrega evaluada (incrementa la crónica del grupo).
        Se invoca tras una evaluación exitosa.

        Returns:
            (registrado: bool, entry: dict, motivo: str)
        """
        try:
            entry = await self.backend.register(
                submission_id=submission_id,
                user_id=user_id,
                course_id=course_id,
                assignment_id=assignment_id,
                nota=nota,
                escala_maxima=escala_maxima,
                modelo=modelo,
            )
            return True, entry, "ok"
        except Exception as exc:
            logger.warning("SubmissionTracker: fallo al registrar envío (%s).", exc)
            return False, {}, str(exc)

    async def history(self, user_id: str) -> List[Dict[str, Any]]:
        try:
            return await self.backend.history(user_id)
        except Exception as exc:
            logger.warning("SubmissionTracker: no se pudo leer historial (%s).", exc)
            return []

    async def stats(self) -> Dict[str, Any]:
        try:
            return await self.backend.stats()
        except Exception as exc:
            logger.warning("SubmissionTracker: no se pudo leer métricas (%s).", exc)
            return {"total_envios": 0, "error": str(exc)}


# Singleton global controlado por init (como ContextStore).
_submission_tracker: Optional[SubmissionTracker] = None


def get_tracker() -> SubmissionTracker:
    """Devuelve el tracker singleton (crea uno en memoria si aún no se inició)."""
    global _submission_tracker
    if _submission_tracker is None:
        _submission_tracker = SubmissionTracker()
    return _submission_tracker


def init_submission_tracker(dsn: str = "", max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> SubmissionTracker:
    """Inicializa el tracker singleton con backend durable y límite de intentos."""
    global _submission_tracker
    backend = make_submission_backend(dsn)
    _submission_tracker = SubmissionTracker(backend=backend, max_attempts=max_attempts)
    return _submission_tracker


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "SubmissionBackend",
    "InMemorySubmissionBackend",
    "SQLAlchemySubmissionBackend",
    "make_submission_backend",
    "SubmissionTracker",
    "get_tracker",
    "init_submission_tracker",
]