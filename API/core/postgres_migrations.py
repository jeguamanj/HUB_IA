"""Apply the versioned PostgreSQL schema used by durable evaluations."""

import argparse
import asyncio
import os
from pathlib import Path

from API.core.logging import get_logger

logger = get_logger("core.postgres_migrations")
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def is_postgres_dsn(dsn: str) -> bool:
    return (dsn or "").strip().lower().startswith(("postgresql+", "postgres+"))


async def apply_pending_migrations(dsn: str, *, engine_factory=None) -> None:
    """Apply unapplied SQL migrations in order; PostgreSQL scripts own transactions."""
    if not is_postgres_dsn(dsn):
        raise ValueError("PostgreSQL migrations require a PostgreSQL DSN.")

    if engine_factory is None:
        from sqlalchemy.ext.asyncio import create_async_engine

        engine_factory = create_async_engine

    engine = engine_factory(dsn)
    try:
        async with engine.connect() as connection:
            raw_connection = await connection.get_raw_connection()
            postgres = raw_connection.driver_connection
            ledger_exists = await postgres.fetchval(
                "SELECT to_regclass('hub_schema_migrations')"
            )
            applied_versions = set()
            if ledger_exists:
                rows = await postgres.fetch(
                    "SELECT version FROM hub_schema_migrations"
                )
                applied_versions = {row["version"] for row in rows}

            for path in sorted(MIGRATIONS_DIR.glob("[0-9]*_*.sql")):
                version = path.name.split("_", 1)[0]
                if version in applied_versions:
                    continue
                await postgres.execute(path.read_text(encoding="utf-8"))
                applied_versions.add(version)
                logger.info("Migración PostgreSQL aplicada: %s", path.name)
    finally:
        await engine.dispose()


async def verify_schema_current(dsn: str, *, engine_factory=None) -> None:
    """Fail if the PostgreSQL schema is missing migrations; perform no DDL."""
    if not is_postgres_dsn(dsn):
        raise ValueError("Production schema verification requires PostgreSQL.")

    if engine_factory is None:
        from sqlalchemy.ext.asyncio import create_async_engine

        engine_factory = create_async_engine

    engine = engine_factory(dsn)
    try:
        async with engine.connect() as connection:
            raw_connection = await connection.get_raw_connection()
            postgres = raw_connection.driver_connection
            ledger_exists = await postgres.fetchval(
                "SELECT to_regclass('hub_schema_migrations')"
            )
            if not ledger_exists:
                raise RuntimeError(
                    "PostgreSQL schema is not initialized; run the migration command."
                )
            rows = await postgres.fetch(
                "SELECT version FROM hub_schema_migrations"
            )
            applied_versions = {row["version"] for row in rows}
            pending = [
                path.name
                for path in sorted(MIGRATIONS_DIR.glob("[0-9]*_*.sql"))
                if path.name.split("_", 1)[0] not in applied_versions
            ]
            if pending:
                raise RuntimeError(
                    "PostgreSQL schema has pending migrations: "
                    + ", ".join(pending)
                    + ". Run the migration command before starting production."
                )
    finally:
        await engine.dispose()


async def initialize_evaluation_schema(dsn: str, *, engine_factory=None) -> None:
    """Backward-compatible name for applying pending migrations."""
    if not is_postgres_dsn(dsn):
        return
    await apply_pending_migrations(dsn, engine_factory=engine_factory)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply pending PostgreSQL migrations outside the API runtime."
    )
    parser.add_argument(
        "command",
        choices=("apply",),
        help="Apply pending migrations using MIGRATION_DB_DSN.",
    )
    parser.parse_args()
    dsn = os.getenv("MIGRATION_DB_DSN", "").strip()
    if not dsn:
        parser.error("Set MIGRATION_DB_DSN in the protected migration environment.")
    try:
        asyncio.run(apply_pending_migrations(dsn))
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()