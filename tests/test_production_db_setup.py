import hashlib
import re
from pathlib import Path

import pytest

from scripts.prepare_production_db import _connection_args, _identifier


def test_database_identifier_is_restricted_to_safe_postgres_names():
    assert _identifier("hub_prod_1", "database") == "hub_prod_1"

    for value in ("HubProd", "db-name", 'db"; DROP DATABASE postgres;--', "a" * 64):
        with pytest.raises(ValueError):
            _identifier(value, "database")


def test_remote_admin_dsn_requires_tls():
    with pytest.raises(ValueError, match="sslmode"):
        _connection_args("postgresql://admin:secret@db.example.org:5432/postgres")

    args = _connection_args(
        "postgresql://admin:secret@db.example.org:5432/postgres?sslmode=require"
    )
    assert args["host"] == "db.example.org"
    assert args["database"] == "postgres"
    assert args["ssl"] is True


def test_production_runtime_schema_migration_has_matching_checksum():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "004_production_runtime_tables.sql"
    )
    migration = migration_path.read_text(encoding="utf-8")
    body = migration.split("INSERT INTO hub_schema_migrations", 1)[0]
    checksum = re.search(
        r"'004',\s*'production_runtime_tables',\s*'([0-9a-f]{64})'",
        migration,
        re.DOTALL,
    )

    assert checksum is not None
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == checksum.group(1)
    assert "CREATE TABLE contexts" in body
    assert "CREATE TABLE submission_groups" in body
