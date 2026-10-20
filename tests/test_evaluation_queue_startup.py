import asyncio
from pathlib import Path

import pytest

from API.core.evaluation_queue import EvaluationManager, make_evaluation_backend


def test_sqlite_queue_backend_initializes_inside_running_event_loop(tmp_path: Path):
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("aiosqlite")

    async def scenario():
        dsn = f"sqlite+aiosqlite:///{tmp_path / 'evaluation_jobs.db'}"
        backend = make_evaluation_backend(dsn)
        assert backend.name == "sqlalchemy"

        manager = EvaluationManager(backend=backend)
        await manager.initialize()
        stats = await manager.stats()
        assert stats["backend"] == "sqlalchemy"
        assert stats["total"] == 0

        await backend.close()

    asyncio.run(scenario())


def test_postgres_queue_mapping_uses_schema_column_names(monkeypatch):
    pytest.importorskip("sqlalchemy")
    from sqlalchemy.dialects.postgresql import JSONB

    from API.core.evaluation_queue import SQLAlchemyEvaluationBackend

    class FakeEngine:
        pass

    monkeypatch.setattr(
        "sqlalchemy.ext.asyncio.create_async_engine",
        lambda dsn: FakeEngine(),
    )

    async def scenario():
        backend = SQLAlchemyEvaluationBackend(
            "postgresql+asyncpg://localhost/test"
        )
        await backend.initialize()
        return backend._Row.__table__

    table = asyncio.run(scenario())

    assert {"payload", "result", "error_message", "count_attempt", "attempt_tracking"}.issubset(
        table.c.keys()
    )
    assert isinstance(table.c.payload.type, JSONB)
    assert isinstance(table.c.result.type, JSONB)
