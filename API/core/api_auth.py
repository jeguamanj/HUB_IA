import base64
import binascii
import hmac
import os
from collections.abc import Callable

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from API.config import get_settings
from API.core.client_tokens import ApiClientPrincipal, ClientTokenRepositoryError
from API.core.logging import get_logger


_bearer_scheme = HTTPBearer(auto_error=False)
logger = get_logger("core.api_auth")
_DOCS_PATHS = frozenset(
    {"/docs", "/docs/", "/docs/oauth2-redirect", "/redoc", "/redoc/", "/openapi.json"}
)


def _authentication_error(expected_token: str, supplied_token: str | None) -> tuple[int, str] | None:
    if not expected_token:
        return 503, "API authentication is not configured"
    if supplied_token is None or not hmac.compare_digest(
        supplied_token.encode("utf-8"), expected_token.encode("utf-8")
    ):
        return 401, "Invalid or missing bearer token"
    return None


def client_token_auth_enabled() -> bool:
    return os.getenv("API_CLIENT_AUTH_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def required_scope(request: Request) -> str | None:
    method = request.method.upper()
    path = request.url.path

    if path == "/echo/echo" and method == "POST":
        return "echo:use"
    if path == "/context/init" and method == "POST":
        return "context:write"
    if path == "/context/active/current" and method == "GET":
        return "context:read"
    if path in {"/context/list", "/context/stats"}:
        return None
    if path.startswith("/context/"):
        if method == "POST" and path.endswith("/close"):
            return "context:close"
        if method == "GET":
            return "context:read"
        if method == "POST":
            return "context:write"
        if method == "DELETE":
            return "context:delete"
        return None
    if path in {
        "/ensayos/enqueue",
        "/ensayos/webhook-canvas",
        "/ensayos/evaluar-flujo-completo",
    } and method == "POST":
        return "essays:submit"
    if path.startswith("/ensayos/estado/") and method == "GET":
        return "essays:read"
    if path == "/llm/chat" and method == "POST":
        return "llm:chat"
    if (
        path in {"/llm/models/free", "/llm/ollama/status", "/llm/ollama/models"}
        and method == "GET"
    ):
        return "llm:read"
    return None


def _scope_error(scope: str | None, principal: ApiClientPrincipal) -> str | None:
    if scope is None or scope not in principal.scopes:
        return "Client is not authorized for this operation"
    return None


def _tenant_error(request: Request, principal: ApiClientPrincipal) -> str | None:
    institution_id = request.headers.get("X-Institution-ID")
    if institution_id != principal.institution_id:
        return "Client is not authorized for this institution"
    if (
        principal.tenant_id is not None
        and request.headers.get("X-Tenant-ID") != principal.tenant_id
    ):
        return "Client is not authorized for this tenant"
    return None


def assert_client_institution(request: Request, institution_id: str) -> None:
    principal = getattr(request.state, "api_client", None)
    if principal is not None and (
        principal.tenant_id is not None
        or principal.institution_id != institution_id
    ):
        raise HTTPException(status_code=404, detail="Resource not found")


async def require_api_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    if getattr(request.state, "api_client", None) is not None:
        return
    error = _authentication_error(
        get_settings().hub_api_token,
        credentials.credentials if credentials is not None else None,
    )
    if error is not None:
        status_code, detail = error
        headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
        raise HTTPException(status_code=status_code, detail=detail, headers=headers)


class BearerTokenMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        token_provider: Callable[[], str],
        public_paths: frozenset[tuple[str, str]] | None = None,
        client_auth_enabled: Callable[[], bool] = client_token_auth_enabled,
        scope_resolver: Callable[[Request], str | None] = required_scope,
    ) -> None:
        super().__init__(app)
        self.token_provider = token_provider
        self.public_paths = public_paths
        self.client_auth_enabled = client_auth_enabled
        self.scope_resolver = scope_resolver

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if self.public_paths is not None and (request.method, request.url.path) in self.public_paths:
            return await call_next(request)

        authorization = request.headers.get("Authorization", "")
        scheme, separator, token = authorization.partition(" ")
        supplied_token = token.strip() if separator and scheme.lower() == "bearer" else None

        if self.client_auth_enabled():
            if supplied_token is None:
                return self._error_response(401, "Invalid or missing bearer token")
            repository = getattr(request.app.state, "api_client_repository", None)
            if repository is None:
                return self._error_response(503, "API client authentication is unavailable")
            try:
                principal = await repository.authenticate(supplied_token)
            except ClientTokenRepositoryError:
                logger.error("API client token validation failed because the repository is unavailable.")
                return self._error_response(503, "API client authentication is unavailable")
            if principal is None:
                return self._error_response(401, "Invalid or expired bearer token")

            permission_error = _scope_error(self.scope_resolver(request), principal)
            if permission_error is not None:
                return self._error_response(403, permission_error)
            tenant_error = _tenant_error(request, principal)
            if tenant_error is not None:
                return self._error_response(403, tenant_error)
            request.state.api_client = principal
            context_error = await self._context_access_error(request, principal)
            if context_error is not None:
                return self._error_response(404, context_error)
            return await call_next(request)

        error = _authentication_error(self.token_provider(), supplied_token)
        if error is not None:
            status_code, detail = error
            headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
            return JSONResponse(status_code=status_code, content={"detail": detail}, headers=headers)

        return await call_next(request)

    @staticmethod
    def _error_response(status_code: int, detail: str) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
        return JSONResponse(
            status_code=status_code,
            content={"detail": detail},
            headers=headers,
        )

    @staticmethod
    async def _context_access_error(
        request: Request,
        principal: ApiClientPrincipal,
    ) -> str | None:
        context_id = request.headers.get("X-Context-ID") or request.query_params.get("context_id")
        if not context_id:
            return None
        from API.core.context_engine import context_store

        context = await context_store.get_or_restore(context_id)
        if context is None:
            return "Context not found"
        if (
            context.institution_id != principal.institution_id
            or (
                principal.tenant_id is not None
                and context.tenant_id != principal.tenant_id
            )
        ):
            return "Context not found"
        return None


class DocsBasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        credentials_provider: Callable[[], tuple[str, str]],
        protected_paths: frozenset[str] = _DOCS_PATHS,
    ) -> None:
        super().__init__(app)
        self.credentials_provider = credentials_provider
        self.protected_paths = protected_paths

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path not in self.protected_paths:
            return await call_next(request)

        expected_username, expected_password = self.credentials_provider()
        if not expected_username or not expected_password:
            return JSONResponse(
                status_code=503,
                content={"detail": "API documentation authentication is not configured"},
            )

        authorization = request.headers.get("Authorization", "")
        scheme, separator, encoded_credentials = authorization.partition(" ")
        supplied_credentials: tuple[str, str] | None = None
        if separator and scheme.lower() == "basic":
            try:
                decoded = base64.b64decode(encoded_credentials, validate=True).decode("utf-8")
                supplied_credentials = tuple(decoded.split(":", 1)) if ":" in decoded else None
            except (binascii.Error, UnicodeDecodeError, ValueError):
                supplied_credentials = None

        if supplied_credentials is None or not (
            hmac.compare_digest(
                supplied_credentials[0].encode("utf-8"), expected_username.encode("utf-8")
            )
            and hmac.compare_digest(
                supplied_credentials[1].encode("utf-8"), expected_password.encode("utf-8")
            )
        ):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing documentation credentials"},
                headers={"WWW-Authenticate": 'Basic realm="MCP HUB Documentation"'},
            )

        return await call_next(request)


def get_api_token() -> str:
    return get_settings().hub_api_token


def get_docs_credentials() -> tuple[str, str]:
    settings = get_settings()
    return settings.hub_docs_username, settings.hub_docs_password


__all__ = [
    "BearerTokenMiddleware",
    "DocsBasicAuthMiddleware",
    "client_token_auth_enabled",
    "assert_client_institution",
    "get_api_token",
    "get_docs_credentials",
    "required_scope",
    "require_api_token",
]
