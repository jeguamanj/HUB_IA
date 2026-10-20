from API.config import Settings


def test_db_dsn_takes_precedence_over_database_url(monkeypatch):
    monkeypatch.setenv("DB_DSN", "postgresql+asyncpg://canonical/test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://legacy/test")

    assert Settings().db_dsn == "postgresql+asyncpg://canonical/test"


def test_database_url_is_used_when_db_dsn_is_not_defined(monkeypatch):
    monkeypatch.delenv("DB_DSN", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://legacy/test")

    assert Settings().db_dsn == "postgresql+asyncpg://legacy/test"


def test_empty_db_dsn_does_not_fall_back_to_database_url(monkeypatch):
    monkeypatch.setenv("DB_DSN", "")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://legacy/test")

    assert Settings().db_dsn == ""


def test_db_dsn_defaults_to_empty_string(monkeypatch):
    monkeypatch.delenv("DB_DSN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert Settings().db_dsn == ""
