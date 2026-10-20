import asyncio
from pathlib import Path

from API.core import postgres_migrations


class FakePostgresConnection:
    def __init__(self):
        self.applied = []

    async def fetchval(self, query):
        return "hub_schema_migrations" if self.applied else None

    async def fetch(self, query):
        return [{"version": version} for version, _ in self.applied]

    async def execute(self, script):
        version = script.splitlines()[0].split(": ", 1)[1]
        self.applied.append((version, script))


class FakeConnection:
    def __init__(self, postgres):
        self.driver_connection = postgres

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    async def get_raw_connection(self):
        return self


class FakeEngine:
    def __init__(self, postgres):
        self.postgres = postgres
        self.disposed = False

    def connect(self):
        return FakeConnection(self.postgres)

    async def dispose(self):
        self.disposed = True


def test_postgres_migrations_apply_in_order_and_only_once(tmp_path: Path, monkeypatch):
    (tmp_path / "002_second.sql").write_text(
        "-- version: 002\nSELECT 2;", encoding="utf-8"
    )
    (tmp_path / "001_first.sql").write_text(
        "-- version: 001\nSELECT 1;", encoding="utf-8"
    )
    monkeypatch.setattr(postgres_migrations, "MIGRATIONS_DIR", tmp_path)
    postgres = FakePostgresConnection()
    engines = []

    def engine_factory(dsn):
        engine = FakeEngine(postgres)
        engines.append(engine)
        return engine

    async def scenario():
        await postgres_migrations.initialize_evaluation_schema(
            "postgresql+asyncpg://localhost/test", engine_factory=engine_factory
        )
        await postgres_migrations.initialize_evaluation_schema(
            "postgresql+asyncpg://localhost/test", engine_factory=engine_factory
        )

    asyncio.run(scenario())

    assert [version for version, _ in postgres.applied] == ["001", "002"]
    assert all(engine.disposed for engine in engines)


def test_non_postgres_dsn_skips_migration_engine():
    def unexpected_engine_factory(dsn):
        raise AssertionError("No debe crear engine para SQLite")

    asyncio.run(
        postgres_migrations.initialize_evaluation_schema(
            "sqlite+aiosqlite:///./storage/test.db",
            engine_factory=unexpected_engine_factory,
        )
    )


def test_production_schema_verification_fails_when_migrations_are_pending(
    tmp_path: Path,
    monkeypatch,
):
    (tmp_path / "001_first.sql").write_text(
        "-- version: 001\nSELECT 1;", encoding="utf-8"
    )
    monkeypatch.setattr(postgres_migrations, "MIGRATIONS_DIR", tmp_path)
    postgres = FakePostgresConnection()
    engine = FakeEngine(postgres)

    async def scenario():
        try:
            await postgres_migrations.verify_schema_current(
                "postgresql+asyncpg://localhost/test",
                engine_factory=lambda dsn: engine,
            )
        except RuntimeError as exc:
            assert "not initialized" in str(exc)
        else:
            raise AssertionError("Missing migration ledger should block startup")

    asyncio.run(scenario())
    assert engine.disposed is True


def test_production_schema_verification_rejects_unapplied_version(
    tmp_path: Path,
    monkeypatch,
):
    (tmp_path / "001_first.sql").write_text(
        "-- version: 001\nSELECT 1;", encoding="utf-8"
    )
    monkeypatch.setattr(postgres_migrations, "MIGRATIONS_DIR", tmp_path)
    postgres = FakePostgresConnection()

    async def ledger_exists(query):
        return "hub_schema_migrations"

    postgres.fetchval = ledger_exists
    engine = FakeEngine(postgres)

    async def scenario():
        try:
            await postgres_migrations.verify_schema_current(
                "postgresql+asyncpg://localhost/test",
                engine_factory=lambda dsn: engine,
            )
        except RuntimeError as exc:
            assert "001_first.sql" in str(exc)
        else:
            raise AssertionError("Pending migration should block startup")

    asyncio.run(scenario())
    assert engine.disposed is True


def test_production_schema_verification_accepts_applied_migrations(
    tmp_path: Path,
    monkeypatch,
):
    (tmp_path / "001_first.sql").write_text(
        "-- version: 001\nSELECT 1;", encoding="utf-8"
    )
    monkeypatch.setattr(postgres_migrations, "MIGRATIONS_DIR", tmp_path)
    postgres = FakePostgresConnection()
    postgres.applied.append(("001", ""))
    engine = FakeEngine(postgres)

    asyncio.run(
        postgres_migrations.verify_schema_current(
            "postgresql+asyncpg://localhost/test",
            engine_factory=lambda dsn: engine,
        )
    )

    assert engine.disposed is True
