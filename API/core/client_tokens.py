"""Database-backed opaque credentials for institutional API integrations."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from API.core.postgres_migrations import is_postgres_dsn


API_CLIENT_SCOPES = frozenset(
    {
        "context:read",
        "context:write",
        "context:close",
        "context:delete",
        "echo:use",
        "essays:read",
        "essays:submit",
        "llm:chat",
        "llm:read",
    }
)


@dataclass(frozen=True)
class ApiClientPrincipal:
    client_id: str
    client_name: str
    institution_id: str
    tenant_id: str | None
    scopes: frozenset[str]


class ClientTokenRepositoryError(RuntimeError):
    """The credential store could not complete an authentication query."""


def validate_client_scopes(scopes: Iterable[str], tenant_id: str | None) -> tuple[str, ...]:
    normalized = tuple(sorted(set(scopes)))
    if not normalized:
        raise ValueError("At least one API client scope is required.")
    invalid = set(normalized) - API_CLIENT_SCOPES
    if invalid:
        raise ValueError(f"Unsupported API client scopes: {', '.join(sorted(invalid))}")
    if tenant_id and ({"essays:read", "essays:submit"} & set(normalized)):
        raise ValueError(
            "Essay scopes require institution-level clients until evaluation records "
            "persist and enforce tenant ownership."
        )
    return normalized


class ClientTokenRepository:
    def __init__(self, dsn: str) -> None:
        if not is_postgres_dsn(dsn):
            raise ValueError("Opaque API client credentials require PostgreSQL.")
        self.dsn = dsn
        self._engine = None

    async def open(self) -> None:
        if self._engine is None:
            from sqlalchemy.ext.asyncio import create_async_engine

            self._engine = create_async_engine(self.dsn, pool_pre_ping=True)

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    async def authenticate(self, token: str) -> ApiClientPrincipal | None:
        if self._engine is None:
            raise RuntimeError("API client token repository is not initialized.")
        from sqlalchemy import text
        from sqlalchemy.exc import SQLAlchemyError

        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(
                    text(
                        """
                        SELECT client_id, client_name, institution_id, tenant_id, scopes
                        FROM hub_api_clients
                        WHERE token_hash = :token_hash
                          AND revoked_at IS NULL
                          AND expires_at > now()
                        """
                    ),
                    {"token_hash": token_hash},
                )
                row = result.mappings().one_or_none()
                if row is None:
                    return None
                await connection.execute(
                    text(
                        "UPDATE hub_api_clients SET last_used_at = now() "
                        "WHERE client_id = :client_id"
                    ),
                    {"client_id": row["client_id"]},
                )
        except SQLAlchemyError as exc:
            raise ClientTokenRepositoryError(
                "API client credential database is unavailable."
            ) from exc
        scopes = row["scopes"]
        if isinstance(scopes, str):
            scopes = json.loads(scopes)
        return ApiClientPrincipal(
            client_id=str(row["client_id"]),
            client_name=row["client_name"],
            institution_id=row["institution_id"],
            tenant_id=row["tenant_id"],
            scopes=frozenset(scopes),
        )

    async def issue(
        self,
        *,
        client_name: str,
        institution_id: str,
        tenant_id: str | None,
        scopes: Iterable[str],
        actor: str,
        expires_at: datetime,
    ) -> tuple[str, str]:
        if self._engine is None:
            raise RuntimeError("API client token repository is not initialized.")
        if not client_name.strip() or not institution_id.strip() or not actor.strip():
            raise ValueError("Client name, institution ID, and actor are required.")
        if expires_at.tzinfo is None or expires_at <= datetime.now(expires_at.tzinfo):
            raise ValueError("Client token expiry must be a future timezone-aware timestamp.")
        from sqlalchemy import text

        normalized_scopes = validate_client_scopes(scopes, tenant_id)
        client_id = str(uuid.uuid4())
        token = f"hub_{secrets.token_urlsafe(32)}"
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO hub_api_clients
                        (client_id, client_name, institution_id, tenant_id,
                         token_hash, scopes, created_by, expires_at)
                    VALUES
                        (:client_id, :client_name, :institution_id, :tenant_id,
                         :token_hash, CAST(:scopes AS JSONB), :created_by, :expires_at)
                    """
                ),
                {
                    "client_id": client_id,
                    "client_name": client_name,
                    "institution_id": institution_id,
                    "tenant_id": tenant_id,
                    "token_hash": token_hash,
                    "scopes": json.dumps(normalized_scopes),
                    "created_by": actor,
                    "expires_at": expires_at,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO hub_api_client_events (client_id, actor, action)
                    VALUES (:client_id, :actor, 'created')
                    """
                ),
                {"client_id": client_id, "actor": actor},
            )
        return client_id, token

    async def rotate(
        self,
        *,
        client_id: str,
        actor: str,
        expires_at: datetime,
    ) -> str:
        if self._engine is None:
            raise RuntimeError("API client token repository is not initialized.")
        if not actor.strip():
            raise ValueError("Actor is required.")
        if expires_at.tzinfo is None or expires_at <= datetime.now(expires_at.tzinfo):
            raise ValueError("Client token expiry must be a future timezone-aware timestamp.")
        from sqlalchemy import text

        token = f"hub_{secrets.token_urlsafe(32)}"
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE hub_api_clients
                    SET token_hash = :token_hash,
                        expires_at = :expires_at,
                        last_used_at = NULL
                    WHERE client_id = :client_id AND revoked_at IS NULL
                    RETURNING client_id
                    """
                ),
                {
                    "token_hash": token_hash,
                    "expires_at": expires_at,
                    "client_id": client_id,
                },
            )
            if result.scalar_one_or_none() is None:
                raise ValueError("Active API client not found.")
            await connection.execute(
                text(
                    """
                    INSERT INTO hub_api_client_events (client_id, actor, action)
                    VALUES (:client_id, :actor, 'rotated')
                    """
                ),
                {"client_id": client_id, "actor": actor},
            )
        return token

    async def revoke(self, *, client_id: str, actor: str) -> None:
        if self._engine is None:
            raise RuntimeError("API client token repository is not initialized.")
        from sqlalchemy import text

        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE hub_api_clients
                    SET revoked_at = now()
                    WHERE client_id = :client_id AND revoked_at IS NULL
                    RETURNING client_id
                    """
                ),
                {"client_id": client_id},
            )
            if result.scalar_one_or_none() is None:
                raise ValueError("Active API client not found.")
            await connection.execute(
                text(
                    """
                    INSERT INTO hub_api_client_events (client_id, actor, action)
                    VALUES (:client_id, :actor, 'revoked')
                    """
                ),
                {"client_id": client_id, "actor": actor},
            )

    async def list_clients(self, *, include_revoked: bool = False) -> list[dict[str, object]]:
        if self._engine is None:
            raise RuntimeError("API client token repository is not initialized.")
        from sqlalchemy import text

        query = """
            SELECT client_id, client_name, institution_id, tenant_id, scopes,
                   created_at, created_by, expires_at, revoked_at, last_used_at
            FROM hub_api_clients
        """
        if not include_revoked:
            query += " WHERE revoked_at IS NULL"
        query += " ORDER BY created_at DESC"
        async with self._engine.connect() as connection:
            result = await connection.execute(text(query))
            return [dict(row) for row in result.mappings().all()]
