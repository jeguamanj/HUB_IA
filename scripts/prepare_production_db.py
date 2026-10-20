"""Provision a new PostgreSQL database and apply the repository migrations."""

import asyncio
import getpass
import re
import ssl
from urllib.parse import parse_qs, unquote, urlsplit

import asyncpg

from API.core.postgres_migrations import MIGRATIONS_DIR

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_MIN_PASSWORD_LENGTH = 24


def _identifier(value: str, label: str) -> str:
    value = value.strip()
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(
            f"{label} debe ser identificador PostgreSQL simple en minúsculas "
            "(letras, números y guion bajo; máximo 63 caracteres)."
        )
    return value


def _connection_args(dsn: str, database: str | None = None) -> dict:
    parsed = urlsplit(dsn.strip())
    if parsed.scheme not in {"postgresql", "postgres"} or not parsed.hostname:
        raise ValueError(
            "El DSN administrativo debe usar postgresql:// o postgres:// y un host."
        )
    if not parsed.username:
        raise ValueError("El DSN administrativo debe especificar el usuario DBA.")
    if not database and not parsed.path.strip("/"):
        raise ValueError("El DSN administrativo debe especificar la base de mantenimiento.")

    hostname = parsed.hostname
    query = parse_qs(parsed.query)
    sslmode = query.get("sslmode", [""])[0].lower()
    is_loopback = hostname.lower() in {"localhost", "127.0.0.1", "::1"}
    if sslmode in {"verify-full", "verify-ca"}:
        ssl_context = ssl.create_default_context()
        if sslmode == "verify-ca":
            ssl_context.check_hostname = False
    elif sslmode == "require":
        ssl_context = True
    elif is_loopback and sslmode in {"", "disable"}:
        ssl_context = False
    else:
        raise ValueError(
            "Para un host no local, el DSN requiere sslmode=require, verify-ca o verify-full."
        )

    return {
        "host": hostname,
        "port": parsed.port or 5432,
        "user": unquote(parsed.username),
        "password": unquote(parsed.password or ""),
        "database": database or parsed.path.lstrip("/"),
        "ssl": ssl_context,
        "timeout": 15,
    }


def _migration_connection_args(
    admin_args: dict, database: str, username: str, password: str
) -> dict:
    return {
        **admin_args,
        "database": database,
        "user": username,
        "password": password,
    }


_CREATE_ROLE_SQL = "SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', $1, $2)"


async def _apply_migrations(connection: asyncpg.Connection) -> None:
    for migration_path in sorted(MIGRATIONS_DIR.glob("[0-9]*_*.sql")):
        await connection.execute(migration_path.read_text(encoding="utf-8"))

    versions = await connection.fetch(
        "SELECT version FROM hub_schema_migrations ORDER BY version"
    )
    applied = {row["version"] for row in versions}
    expected = {
        path.name.split("_", 1)[0]
        for path in MIGRATIONS_DIR.glob("[0-9]*_*.sql")
    }
    if applied != expected:
        raise RuntimeError("El ledger de migraciones no coincide con los archivos locales.")


async def provision_database(
    admin_dsn: str,
    database: str,
    migration_role: str,
    migration_password: str,
    runtime_role: str,
    runtime_password: str,
) -> None:
    database = _identifier(database, "El nombre de la base")
    migration_role = _identifier(migration_role, "El rol de migraciones")
    runtime_role = _identifier(runtime_role, "El rol runtime")
    if migration_role == runtime_role:
        raise ValueError("Los roles DDL y runtime deben ser diferentes.")
    if min(len(migration_password), len(runtime_password)) < _MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Las contraseñas de base deben tener al menos {_MIN_PASSWORD_LENGTH} caracteres."
        )

    admin_args = _connection_args(admin_dsn)
    admin = await asyncpg.connect(**admin_args)
    try:
        version = int(await admin.fetchval("SHOW server_version_num"))
        if version < 150000:
            raise RuntimeError("La base requiere PostgreSQL 15 o superior.")
        if await admin.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", database
        ):
            raise RuntimeError(
                "La base ya existe. Este script solo admite una base nueva y vacía."
            )
        existing_roles = await admin.fetch(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY($1::text[])",
            [migration_role, runtime_role],
        )
        if existing_roles:
            raise RuntimeError(
                "Uno o más roles ya existen. No se modificaron roles ni bases."
            )

        for role, password in (
            (migration_role, migration_password),
            (runtime_role, runtime_password),
        ):
            statement = await admin.fetchval(_CREATE_ROLE_SQL, role, password)
            await admin.execute(statement)

        await admin.execute(
            f'CREATE DATABASE "{database}" OWNER "{migration_role}" '
            "ENCODING 'UTF8' TEMPLATE template0"
        )
    finally:
        await admin.close()

    migration_args = _migration_connection_args(
        admin_args, database, migration_role, migration_password
    )
    migration_connection = await asyncpg.connect(**migration_args)
    try:
        await migration_connection.execute(
            f'GRANT CONNECT ON DATABASE "{database}" TO "{runtime_role}"'
        )
        await migration_connection.execute(
            f'GRANT USAGE ON SCHEMA public TO "{runtime_role}"'
        )
        await migration_connection.execute(
            f'REVOKE CREATE ON SCHEMA public FROM "{runtime_role}"'
        )
        await migration_connection.execute(
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{runtime_role}"'
        )
        await migration_connection.execute(
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
            f'GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO "{runtime_role}"'
        )
        await _apply_migrations(migration_connection)
        await migration_connection.execute(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public '
            f'TO "{runtime_role}"'
        )
        await migration_connection.execute(
            f'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public '
            f'TO "{runtime_role}"'
        )
    finally:
        await migration_connection.close()


def _secret(prompt: str) -> str:
    value = getpass.getpass(prompt)
    confirmation = getpass.getpass("Confirma la contraseña: ")
    if value != confirmation:
        raise ValueError("Las contraseñas no coinciden.")
    if len(value) < _MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Las contraseñas de base deben tener al menos {_MIN_PASSWORD_LENGTH} caracteres."
        )
    return value


def main() -> None:
    print("Preparación inicial de una base PostgreSQL de producción.")
    print("No se muestran ni se guardan DSN o contraseñas.")
    admin_dsn = getpass.getpass(
        "DSN DBA a la base administrativa (TLS obligatorio si es remoto): "
    )
    database = input("Nombre de la nueva base: ").strip()
    migration_role = input("Rol DDL [hub_migrator]: ").strip() or "hub_migrator"
    runtime_role = input("Rol runtime [hub_runtime]: ").strip() or "hub_runtime"
    migration_password = _secret("Contraseña nueva del rol DDL: ")
    runtime_password = _secret("Contraseña nueva del rol runtime: ")

    confirmation = input(
        f"Escribe CREAR {database} para crear la base e instalar el esquema: "
    ).strip()
    if confirmation != f"CREAR {database}":
        raise SystemExit("Cancelado; no se creó la base.")

    asyncio.run(
        provision_database(
            admin_dsn,
            database,
            migration_role,
            migration_password,
            runtime_role,
            runtime_password,
        )
    )
    print(
        "Base y migraciones preparadas. Configura DB_DSN con el rol runtime; "
        "reserva MIGRATION_DB_DSN con el rol DDL solo para tareas administrativas."
    )


if __name__ == "__main__":
    main()
