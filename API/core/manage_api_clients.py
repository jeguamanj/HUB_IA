"""Out-of-band CLI for managing API client credentials."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone

from API.config import get_settings
from API.core.client_tokens import (
    ClientTokenRepository,
    ClientTokenRepositoryError,
    validate_client_scopes,
)


def _expiry(value: str) -> datetime:
    try:
        expires_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use an ISO-8601 timestamp with timezone.") from exc
    if expires_at.tzinfo is None:
        raise argparse.ArgumentTypeError("Expiry must include a timezone.")
    if expires_at <= datetime.now(timezone.utc):
        raise argparse.ArgumentTypeError("Expiry must be in the future.")
    return expires_at


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    issue = commands.add_parser("issue", help="Create a new integration credential.")
    issue.add_argument("--name", required=True)
    issue.add_argument("--institution-id", required=True)
    issue.add_argument("--tenant-id")
    issue.add_argument("--scopes", required=True, help="Comma-separated allowlisted scopes.")
    issue.add_argument("--actor", required=True, help="Operator identity recorded in the audit log.")
    issue.add_argument("--expires-at", required=True, type=_expiry)

    rotate = commands.add_parser("rotate", help="Replace an active credential.")
    rotate.add_argument("--client-id", required=True)
    rotate.add_argument("--actor", required=True)
    rotate.add_argument("--expires-at", required=True, type=_expiry)

    revoke = commands.add_parser("revoke", help="Revoke an active credential.")
    revoke.add_argument("--client-id", required=True)
    revoke.add_argument("--actor", required=True)

    list_command = commands.add_parser("list", help="List credential metadata without secrets.")
    list_command.add_argument("--include-revoked", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> None:
    from sqlalchemy.exc import SQLAlchemyError

    settings = get_settings()
    repository = ClientTokenRepository(settings.db_dsn)
    try:
        await repository.open()
        if args.command == "issue":
            scopes = validate_client_scopes(
                (scope.strip() for scope in args.scopes.split(",") if scope.strip()),
                args.tenant_id,
            )
            client_id, token = await repository.issue(
                client_name=args.name,
                institution_id=args.institution_id,
                tenant_id=args.tenant_id,
                scopes=scopes,
                actor=args.actor,
                expires_at=args.expires_at,
            )
            print(f"client_id: {client_id}")
            print("token (shown once):")
            print(token)
        elif args.command == "rotate":
            token = await repository.rotate(
                client_id=args.client_id,
                actor=args.actor,
                expires_at=args.expires_at,
            )
            print("token (shown once):")
            print(token)
        elif args.command == "revoke":
            await repository.revoke(client_id=args.client_id, actor=args.actor)
            print(f"Revoked client {args.client_id}.")
        else:
            clients = await repository.list_clients(
                include_revoked=args.include_revoked,
            )
            print(json.dumps(clients, ensure_ascii=False, default=str, indent=2))
    except SQLAlchemyError as exc:
        raise ClientTokenRepositoryError(
            "API client database operation failed; inspect protected database logs."
        ) from exc
    finally:
        await repository.close()


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    try:
        asyncio.run(_run(args))
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
