# =============================================================================
# tests/test_context_backend.py — B1.2 `durable-store`
# -----------------------------------------------------------------------------
# Valida:
#   1. CRUD del backend en memoria (dict_to_record / record_to_dict / TTL).
#   2. Write-through del ContextStore hacia el backend durable.
#   3. Restauración de contextos desde un backend durable (simula restart).
#   4. Conmutación SQLite/PostgreSQL con degradación controlada a memoria.
# =============================================================================

import asyncio
import time

from API.core.context_backend import (
    ContextRecord,
    ContextBackend,
    InMemoryContextBackend,
    make_context_backend,
)
from API.core.context_engine import ContextStore, InstitutionalContext


def run(coro):
    """Ejecuta una coroutine en su propio event-loop (tests síncronos)."""
    return asyncio.run(coro)


class TestContextRecord:
    def test_roundtrip(self):
        rec = ContextRecord("c1", 100.0, 3600, {"inst": "X", "slots": {}})
        d = ContextBackend.record_to_dict(rec)
        r2 = ContextBackend.dict_to_record(d, now=200.0)
        assert r2.context_id == "c1"
        assert r2.created_at == 100.0
        assert r2.ttl_seconds == 3600
        assert r2.data == {"inst": "X", "slots": {}}

    def test_expiry(self):
        now = time.time()
        rec = ContextRecord("c1", now - 5000, 3600, {})
        assert rec.is_expired(now) is True
        rec2 = ContextRecord("c1", now, 3600, {})
        assert rec2.is_expired(now) is False

    def test_dict_to_record_handles_json_string(self):
        # data puede venir serializada como JSON string.
        rec = ContextBackend.dict_to_record(
            {"context_id": "c", "created_at": 1, "ttl_seconds": 3600, "data": '{"a":1}'},
            now=2,
        )
        assert rec.data == {"a": 1}


class TestInMemoryBackend:
    def test_crud(self):
        async def scenario():
            b = InMemoryContextBackend()
            assert await b.list_ids() == []
            rec = ContextRecord("ctx_a", time.time(), 3600, {"institution_id": "UTPL"})
            await b.create(rec)
            assert await b.list_ids() == ["ctx_a"]
            got = await b.get("ctx_a")
            assert got is not None and got.data["institution_id"] == "UTPL"
            assert await b.delete("ctx_a") is True
            assert await b.get("ctx_a") is None

        run(scenario())

    def test_get_drops_expired(self):
        async def scenario():
            b = InMemoryContextBackend()
            stale = ContextRecord("stale", time.time() - 99999, 10, {})
            await b.create(stale)
            assert await b.get("stale") is None  # expirado -> no disponible y purga

        run(scenario())


class TestContextStoreDurable:
    def test_write_through(self):
        async def scenario():
            backend = InMemoryContextBackend()
            store = ContextStore(backend=backend)
            ctx = store.create(institution_id="UTPL", user_role="DOCENTE")
            await asyncio.sleep(0.05)
            ids = await backend.list_ids()
            assert ctx.context_id in ids
            # update -> persiste el cambio
            ctx.evaluation_scale = 20.0
            store.save(ctx)
            await asyncio.sleep(0.05)
            rec = await backend.get(ctx.context_id)
            assert rec.data["evaluation_scale"] == 20.0
            # delete -> borra en durable
            assert store.delete(ctx.context_id) is True
            await asyncio.sleep(0.05)
            assert await backend.get(ctx.context_id) is None

        run(scenario())

    def test_restore_after_restart(self):
        async def scenario():
            backend = InMemoryContextBackend()
            s1 = ContextStore(backend=backend)
            c1 = s1.create(institution_id="UTPL", custom_instructions="severa")
            c2 = s1.create(institution_id="UTPL", slots={"zoom": {"account_id": "X"}})
            await asyncio.sleep(0.05)

            # Reinicio: nueva instancia que carga desde el mismo backend.
            s2 = ContextStore(backend=backend)
            n = await s2.load_from_backend()
            assert n == 2
            assert s2.get(c1.context_id).custom_instructions == "severa"
            assert s2.get(c2.context_id).slots["zoom"]["account_id"] == "X"

        run(scenario())

    def test_persist_failure_does_not_break(self):
        """Si el backend falla, create/save no lanzan (best-effort)."""

        class BrokenBackend(InMemoryContextBackend):
            async def save(self, record):
                raise RuntimeError("disk full")

        async def scenario():
            store = ContextStore(backend=BrokenBackend())
            ctx = store.create(institution_id="T")  # no debe lanzar
            assert ctx.context_id

        run(scenario())


class TestFactoryConmutacion:
    def test_empty_and_memory_dsn(self):
        assert make_context_backend("").name == "memory"
        assert make_context_backend("memory").name == "memory"

    def test_unsupported_dialect_falls_back(self):
        assert make_context_backend("mongodb://h/db").name == "memory"

    def test_sqlalchemy_dsn_degrades_when_driver_absent(self):
        """SQLite/PostgreSQL DSN no lanzan y devuelven un backend utilizable
        (sqlalchemy si el driver/BD están, memoria si no)."""
        for dsn in (
            "sqlite+aiosqlite:///./storage/hub.db",
            "postgresql+asyncpg://u:p@localhost:5432/db",
        ):
            backend = make_context_backend(dsn)
            assert backend.name in ("sqlalchemy", "memory")
            assert hasattr(backend, "create")
            assert hasattr(backend, "get")