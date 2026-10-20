import asyncio
import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from API.core.client_tokens import ClientTokenRepository


class FakeResult:
    def scalar_one_or_none(self):
        return "client-id"

    def mappings(self):
        return self

    def all(self):
        return [{"client_id": "client-id", "scopes": ["echo:use"]}]


class FakeConnection:
    def __init__(self, statements):
        self.statements = statements

    async def execute(self, statement, parameters=None):
        self.statements.append((str(statement), parameters or {}))
        return FakeResult()


class FakeTransaction:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


class FakeEngine:
    def __init__(self):
        self.statements = []
        self.connection = FakeConnection(self.statements)

    def begin(self):
        return FakeTransaction(self.connection)

    def connect(self):
        return FakeTransaction(self.connection)


def test_issue_stores_only_a_hash_and_records_creation():
    async def scenario():
        repository = ClientTokenRepository("postgresql+asyncpg://localhost/test")
        engine = FakeEngine()
        repository._engine = engine
        client_id, token = await repository.issue(
            client_name="Canvas",
            institution_id="institution-a",
            tenant_id=None,
            scopes=["essays:submit", "essays:read"],
            actor="operator",
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        return engine, client_id, token

    engine, client_id, token = asyncio.run(scenario())

    insert_sql, insert_parameters = engine.statements[0]
    assert "INSERT INTO hub_api_clients" in insert_sql
    assert insert_parameters["client_id"] == client_id
    assert insert_parameters["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in repr(insert_parameters)
    assert insert_parameters["scopes"] == '["essays:read", "essays:submit"]'
    assert "INSERT INTO hub_api_client_events" in engine.statements[1][0]


def test_list_clients_returns_metadata_without_selecting_token_hash():
    async def scenario():
        repository = ClientTokenRepository("postgresql+asyncpg://localhost/test")
        engine = FakeEngine()
        repository._engine = engine
        clients = await repository.list_clients()
        return clients, engine.statements

    clients, statements = asyncio.run(scenario())

    assert clients == [{"client_id": "client-id", "scopes": ["echo:use"]}]
    query, parameters = statements[0]
    assert "WHERE revoked_at IS NULL" in query
    assert "token_hash" not in query
    assert parameters == {}


def test_api_client_migration_checksum_matches_migration_body():
    migration_path = Path(__file__).resolve().parents[1] / "migrations" / "003_api_client_tokens.sql"
    migration = migration_path.read_text(encoding="utf-8")
    body = migration.split("INSERT INTO hub_schema_migrations", 1)[0]
    checksum = re.search(
        r"'003',\s*'api_client_tokens',\s*'([0-9a-f]{64})'",
        migration,
        re.DOTALL,
    )

    assert checksum is not None
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == checksum.group(1)
