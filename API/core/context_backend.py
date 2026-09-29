# =============================================================================
# API/core/context_backend.py
# -----------------------------------------------------------------------------
# Persistencia durable del ContextStore (FASE 1 · Agente B1.2 `durable-store`).
#
# Capa de almacenamiento con interfaz única intercambiable:
#   - InMemoryContextBackend   : respaldo/dev y fallback automático (sin drivers).
#   - SQLAlchemyContextBackend : SQLite (aiosqlite) y PostgreSQL (asyncpg) a
#                                través de un único DSN SQLAlchemy async.
#
# Conmutación: solo cambia `DB_DSN`. Si el driver/base configurada NO está
# disponible, se degrada a memoria con un aviso por log (nunca se cae).
#
# La capa de negocio vive en `context_engine.py` (ContextStore). Este módulo
# define el contrato de backend y su fábrica.
# =============================================================================

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Contrato de backend
# ---------------------------------------------------------------------------

class ContextRecord:
    """Fila serializada de un contexto institucional (prescinde de pydantic)."""

    __slots__ = ("context_id", "created_at", "ttl_seconds", "data")

    def __init__(
        self,
        context_id: str,
        created_at: float,
        ttl_seconds: int,
        data: Dict[str, Any],
    ):
        self.context_id = context_id
        self.created_at = created_at
        self.ttl_seconds = ttl_seconds
        self.data = data  # dict JSON-serializable con el estado completo

    def is_expired(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        return now > (self.created_at + self.ttl_seconds)


class ContextBackend:
    """Interfaz abstracta de almacenamiento durable."""

    name: str = "abstract"

    async def create(self, record: ContextRecord) -> None:
        raise NotImplementedError

    async def get(self, context_id: str) -> Optional[ContextRecord]:
        raise NotImplementedError

    async def save(self, record: ContextRecord) -> None:
        raise NotImplementedError

    async def delete(self, context_id: str) -> bool:
        raise NotImplementedError

    async def list_ids(self) -> List[str]:
        raise NotImplementedError

    async def close(self) -> None:
        """Libera recursos (pool de conexiones, engine). No-op por defecto."""

    # -- utilidades --------------------------------------------------------
    @classmethod
    def record_to_dict(cls, record: ContextRecord) -> Dict[str, Any]:
        return {
            "context_id": record.context_id,
            "created_at": record.created_at,
            "ttl_seconds": record.ttl_seconds,
            "data": record.data,
        }

    @classmethod
    def dict_to_record(cls, raw: Dict[str, Any], now: Optional[float] = None) -> ContextRecord:
        now = now if now is not None else time.time()
        data = raw.get("data", {})
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (TypeError, ValueError):
                data = {}
        return ContextRecord(
            context_id=str(raw["context_id"]),
            created_at=float(raw.get("created_at", now)),
            ttl_seconds=int(raw.get("ttl_seconds", 3600)),
            data=data if isinstance(data, dict) else {},
        )


# ---------------------------------------------------------------------------
# Backend en memoria (respaldo / dev / fallback)
# ---------------------------------------------------------------------------

class InMemoryContextBackend(ContextBackend):
    """Backend transitorio en memoria. Sin durabilidad real (sobrevive a crashes no)."""

    name = "memory"

    def __init__(self) -> None:
        self._records: Dict[str, ContextRecord] = {}

    async def create(self, record: ContextRecord) -> None:
        self._records[record.context_id] = record

    async def get(self, context_id: str) -> Optional[ContextRecord]:
        rec = self._records.get(context_id)
        if rec is None:
            return None
        if rec.is_expired():
            self._records.pop(context_id, None)
            return None
        return rec

    async def save(self, record: ContextRecord) -> None:
        self._records[record.context_id] = record

    async def delete(self, context_id: str) -> bool:
        return self._records.pop(context_id, None) is not None

    async def list_ids(self) -> List[str]:
        now = time.time()
        # Limpieza perezosa de expirados al listar.
        expired = [cid for cid, r in self._records.items() if r.is_expired(now)]
        for cid in expired:
            self._records.pop(cid, None)
        return list(self._records.keys())


# ---------------------------------------------------------------------------
# Backend SQLAlchemy async (SQLite + PostgreSQL) — import diferido
# ---------------------------------------------------------------------------

class SQLAlchemyContextBackend(ContextBackend):
    """
    Backend durable sobre SQLAlchemy 2.x async.

    - SQLite:      `sqlite+aiosqlite:///./storage/hub.db`
    - PostgreSQL:  `postgresql+asyncpg://user:pass@host:5432/db`

    Se usa una única tabla JSON (`contexts`) con `context_id` como PK y el
    estado serializado en JSON. La instanciación es *perezosa*: el engine se
    construye al conectar, permitiendo un fallback limpio si falla.
    """

    name = "sqlalchemy"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._engine: Optional[Any] = None
        self._SessionLocal: Optional[Any] = None
        self._session: Optional[Any] = None
        self._ready = False

    # -- helpers de infraestructura (import diferido) ----------------------
    @staticmethod
    def _dialect_for(dsn: str) -> str:
        return dsn.split("://", 1)[0].lower()

    async def _ensure_ready(self) -> None:
        """Importa SQLAlchemy, construye engine y crea tablas (perezoso)."""
        if self._ready:
            return
        try:
            from sqlalchemy import JSON, Text
            from sqlalchemy.ext.asyncio import (
                AsyncSession,
                async_sessionmaker,
                create_async_engine,
            )
            from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

            # Mapear relative sqlite DSN a ruta dentro del proyecto.
            dsn = self.dsn
            if dsn.startswith("sqlite+aiosqlite:///./"):
                rel = dsn.split("///./", 1)[1]
                from pathlib import Path

                Path(rel).parent.mkdir(parents=True, exist_ok=True)

            class Base(DeclarativeBase):
                pass

            class ContextRow(Base):
                __tablename__ = "contexts"
                context_id: Mapped[str] = mapped_column(Text, primary_key=True)
                created_at: Mapped[int] = mapped_column(nullable=False, default=lambda: int(time.time()))
                ttl_seconds: Mapped[int] = mapped_column(nullable=False, default=3600)
                data: Mapped[str] = mapped_column(JSON, nullable=False, default=dict)

            self._engine = create_async_engine(dsn)
            self._SessionLocal = async_sessionmaker(self._engine, class_=AsyncSession, expire_on_commit=False)
            self._row_model = ContextRow

            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            self._ready = True
        except Exception as exc:  # driver ausente, servicio caído, etc.
            raise RuntimeError(f"No se pudo inicializar el backend SQLAlchemy: {exc}") from exc

    async def _get_session(self):
        await self._ensure_ready()
        if self._session is None or self._session.closed:
            self._session = self._SessionLocal()
        return self._session

    # -- CRUD --------------------------------------------------------------
    async def create(self, record: ContextRecord) -> None:
        await self.save(record)

    async def save(self, record: ContextRecord) -> None:
        session = await self._get_session()
        Row = self._row_model
        try:
            import sqlalchemy as sa

            stmt = sa.select(Row).where(Row.context_id == record.context_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            payload = json.dumps(record.data, ensure_ascii=False)
            if row is None:
                session.add(Row(
                    context_id=record.context_id,
                    created_at=int(record.created_at),
                    ttl_seconds=record.ttl_seconds,
                    data=payload,
                ))
            else:
                row.created_at = int(record.created_at)
                row.ttl_seconds = record.ttl_seconds
                row.data = payload
            await session.commit()
        except Exception as exc:
            await session.rollback()
            raise exc

    async def get(self, context_id: str) -> Optional[ContextRecord]:
        session = await self._get_session()
        import sqlalchemy as sa

        Row = self._row_model
        result = await session.execute(sa.select(Row).where(Row.context_id == context_id))
        row = result.scalar_one_or_none()
        if row is None:
            return None
        rec = self.dict_to_record(
            {
                "context_id": row.context_id,
                "created_at": float(row.created_at),
                "ttl_seconds": int(row.ttl_seconds),
                "data": row.data if isinstance(row.data, str) else json.dumps(row.data, ensure_ascii=False),
            }
        )
        if rec.is_expired():
            await self.delete(context_id)
            return None
        return rec

    async def delete(self, context_id: str) -> bool:
        session = await self._get_session()
        import sqlalchemy as sa

        Row = self._row_model
        result = await session.execute(sa.delete(Row).where(Row.context_id == context_id))
        await session.commit()
        return result.rowcount > 0

    async def list_ids(self) -> List[str]:
        session = await self._get_session()
        import sqlalchemy as sa

        Row = self._row_model
        result = await session.execute(sa.select(Row.context_id))
        ids = [row[0] for row in result.all()]
        return ids

    async def close(self) -> None:
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None
        if self._engine is not None:
            try:
                await self._engine.dispose()
            except Exception:
                pass
            self._engine = None


# ---------------------------------------------------------------------------
# Fábrica con degradación a memoria
# ---------------------------------------------------------------------------

def _make_memory_backend() -> InMemoryContextBackend:
    return InMemoryContextBackend()


def _sqlalchemy_driver_available(dsn: str) -> bool:
    """Comprueba (sin event-loops) si el driver del dialecto está instalado."""
    dialect = dsn.split("://", 1)[0].lower()
    require = []
    if dialect.startswith("sqlite"):
        require = ["sqlalchemy", "aiosqlite"]
    elif dialect.startswith("postgresql") or dialect.startswith("postgres"):
        require = ["sqlalchemy", "asyncpg"]
    try:
        for mod in require:
            __import__(mod)
        return True
    except ImportError:
        return False


def make_context_backend(
    dsn: Optional[str] = None,
    *,
    logger: Any = None,
) -> ContextBackend:
    """
    Resuelve el backend durable acorde al DSN.

    - DSN vacío / "memory"      → InMemoryContextBackend.
    - DSN SQLAlchemy            → intenta SQLAlchemyContextBackend.
         Si el driver o la base NO están disponibles → degrada a memoria con aviso.

    Args:
        dsn:    DSN SQLAlchemy async. None → memoria.
        logger: Logger opcional para emitir el aviso de degradación.
    """
    dsn = (dsn or "").strip()
    log = logger or (lambda *a, **k: None)

    if not dsn or dsn.lower() in ("memory", "mem"):
        warn = getattr(log, "info", None) or (lambda *a, **k: None)
        warn("ContextBackend: DSN vacío o 'memory' → backend en memoria (transitorio).")
        return _make_memory_backend()

    dialect = dsn.split("://", 1)[0].lower()
    # Reconocer prefijos de dialecto SQLAlchemy: sqlite / sqlite+aiosqlite,
    # postgresql / postgresql+asyncpg.
    supported = ("sqlite", "postgresql", "postgres")
    if not any(dialect.startswith(d) for d in supported):
        warn = getattr(log, "warning", None) or (lambda *a, **k: None)
        warn("ContextBackend: dialecto '%s' no soportado → memoria.", dialect)
        return _make_memory_backend()

    try:
        backend = SQLAlchemyContextBackend(dsn)

        # Verificación síncrona (sin event-loops): si el driver del dialecto no
        # está instalado, degrada a memoria de inmediato. La conexión a la base
        # en sí (SQLite file/Postgres host) es *perezosa*: ocurre en el primer
        # `save/get` y cualquier fallo allí ya se captura en `ContextStore`.
        if not _sqlalchemy_driver_available(dsn):
            raise RuntimeError(
                "driver del dialecto no instalado (sqlalchemy/aiosqlite/asyncpg)"
            )

        return backend
    except Exception as exc:
        warn = getattr(log, "warning", None) or (lambda *a, **k: None)
        warn("ContextBackend: no se pudo usar '%s' (%s) → degradando a memoria.", dsn, exc)
        return _make_memory_backend()


# Backends de respaldo exportados por conveniencia (tests dev).
__all__ = [
    "ContextBackend",
    "ContextRecord",
    "InMemoryContextBackend",
    "SQLAlchemyContextBackend",
    "make_context_backend",
]