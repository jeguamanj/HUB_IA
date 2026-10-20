import asyncio

from fastapi.testclient import TestClient
import pytest

from API.config import Settings, get_settings, validate_production_configuration
from API.core.api_auth import BearerTokenMiddleware, required_scope
from API.core.client_tokens import ApiClientPrincipal, validate_client_scopes
from API.server import app


def test_production_configuration_requires_strong_separate_credentials_and_postgres(monkeypatch):
    monkeypatch.setenv("API_CLIENT_AUTH_ENABLED", "true")
    monkeypatch.setenv("ATTACHMENT_ALLOWED_HOSTS", "lms.example.edu")
    config = Settings()
    config.hub_docs_username = "docs-admin"
    config.hub_docs_password = "p" * 16
    config.db_dsn = "postgresql+asyncpg://user:secret@db.example/hub"

    assert validate_production_configuration(config) == []

    monkeypatch.setenv("API_CLIENT_AUTH_ENABLED", "false")
    errors = validate_production_configuration(config)
    assert any("API_CLIENT_AUTH_ENABLED" in error for error in errors)


def test_client_scope_allowlist_restricts_tenant_unsupported_operations():
    assert validate_client_scopes(["essays:read", "essays:submit"], None) == (
        "essays:read",
        "essays:submit",
    )

    with pytest.raises(ValueError, match="institution-level"):
        validate_client_scopes(["essays:read"], "faculty-a")
    with pytest.raises(ValueError, match="Unsupported"):
        validate_client_scopes(["zoom:admin"], None)


def test_client_token_middleware_enforces_scope_and_tenant():
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    class FakeRepository:
        async def authenticate(self, token):
            if token != "valid-token":
                return None
            return ApiClientPrincipal(
                client_id="client-1",
                client_name="Canvas",
                institution_id="institution-a",
                tenant_id="faculty-a",
                scopes=frozenset({"echo:use"}),
            )

    async def echo_endpoint(request):
        return JSONResponse({"client_id": request.state.api_client.client_id})

    client_app = Starlette(routes=[Route("/echo/echo", echo_endpoint, methods=["POST"])])
    client_app.state.api_client_repository = FakeRepository()
    client_app.add_middleware(
        BearerTokenMiddleware,
        token_provider=lambda: "legacy-token",
        client_auth_enabled=lambda: True,
        scope_resolver=required_scope,
    )
    client = TestClient(client_app)
    headers = {
        "Authorization": "Bearer valid-token",
        "X-Institution-ID": "institution-a",
        "X-Tenant-ID": "faculty-a",
    }

    assert client.post("/echo/echo", headers=headers).json() == {"client_id": "client-1"}
    assert client.post(
        "/echo/echo",
        headers={**headers, "X-Tenant-ID": "faculty-b"},
    ).status_code == 403
    assert client.get(
        "/api/zoom/report/excel",
        headers=headers,
    ).status_code == 403


def test_client_token_middleware_fails_closed_when_repository_is_missing():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    async def endpoint(request):
        return PlainTextResponse("unexpected")

    client_app = Starlette(routes=[Route("/echo/echo", endpoint, methods=["POST"])])
    client_app.add_middleware(
        BearerTokenMiddleware,
        token_provider=lambda: "legacy-token",
        client_auth_enabled=lambda: True,
        scope_resolver=required_scope,
    )

    response = TestClient(client_app).post(
        "/echo/echo",
        headers={
            "Authorization": "Bearer valid-token",
            "X-Institution-ID": "institution-a",
        },
    )
    assert response.status_code == 503


def test_settings_reads_hub_api_token(monkeypatch):
    from API.config import Settings

    monkeypatch.setenv("HUB_API_TOKEN", "configured-secret")
    monkeypatch.setenv("HUB_DOCS_USERNAME", "docs-user")
    monkeypatch.setenv("HUB_DOCS_PASSWORD", "docs-password")

    settings = Settings()
    assert settings.hub_api_token == "configured-secret"
    assert settings.hub_docs_username == "docs-user"
    assert settings.hub_docs_password == "docs-password"


def test_protected_api_requires_bearer_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "hub_api_token", "test-secret")
    client = TestClient(app)

    response = client.post("/echo/echo", params={"message": "hola"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_protected_api_rejects_incorrect_bearer_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "hub_api_token", "test-secret")
    client = TestClient(app)

    response = client.post(
        "/echo/echo",
        params={"message": "hola"},
        headers={"Authorization": "Bearer wrong-secret"},
    )

    assert response.status_code == 401


def test_protected_api_accepts_valid_bearer_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "hub_api_token", "test-secret")
    client = TestClient(app)

    response = client.post(
        "/echo/echo",
        params={"message": "hola"},
        headers={"Authorization": "Bearer test-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"echo": "hola"}


def test_fastapi_accepts_scoped_client_token(monkeypatch):
    class FakeRepository:
        async def authenticate(self, token):
            if token != "client-token":
                return None
            return ApiClientPrincipal(
                client_id="client-1",
                client_name="Echo client",
                institution_id="institution-a",
                tenant_id=None,
                scopes=frozenset({"echo:use"}),
            )

    monkeypatch.setenv("API_CLIENT_AUTH_ENABLED", "true")
    previous_repository = getattr(app.state, "api_client_repository", None)
    app.state.api_client_repository = FakeRepository()
    try:
        response = TestClient(app).post(
            "/echo/echo",
            params={"message": "hola"},
            headers={
                "Authorization": "Bearer client-token",
                "X-Institution-ID": "institution-a",
            },
        )
    finally:
        app.state.api_client_repository = previous_repository

    assert response.status_code == 200
    assert response.json() == {"echo": "hola"}


def test_protected_api_fails_closed_when_token_is_unconfigured(monkeypatch):
    monkeypatch.setattr(get_settings(), "hub_api_token", "")
    client = TestClient(app)

    response = client.post("/echo/echo", params={"message": "hola"})

    assert response.status_code == 503


def test_healthcheck_is_public(monkeypatch):
    monkeypatch.setattr(get_settings(), "hub_api_token", "")
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_returns_unavailable_until_startup_completes():
    from fastapi.responses import JSONResponse
    from API.server import readinesscheck

    app.state.ready = False
    response = asyncio.run(readinesscheck())
    assert isinstance(response, JSONResponse)
    assert response.status_code == 503

    app.state.ready = True
    assert asyncio.run(readinesscheck()) == {"status": "ready"}


def test_other_methods_on_health_path_are_not_public(monkeypatch):
    monkeypatch.setattr(get_settings(), "hub_api_token", "")
    client = TestClient(app)

    response = client.post("/health")

    assert response.status_code == 503


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_documentation_requires_basic_credentials(monkeypatch, path):
    monkeypatch.setattr(get_settings(), "hub_docs_username", "docs-user")
    monkeypatch.setattr(get_settings(), "hub_docs_password", "docs-password")
    client = TestClient(app)

    response = client.get(path)

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_documentation_accepts_basic_credentials(monkeypatch, path):
    monkeypatch.setattr(get_settings(), "hub_docs_username", "docs-user")
    monkeypatch.setattr(get_settings(), "hub_docs_password", "docs-password")
    client = TestClient(app)

    response = client.get(path, auth=("docs-user", "docs-password"))

    assert response.status_code == 200


def test_api_documentation_rejects_incorrect_basic_credentials(monkeypatch):
    monkeypatch.setattr(get_settings(), "hub_docs_username", "docs-user")
    monkeypatch.setattr(get_settings(), "hub_docs_password", "docs-password")
    client = TestClient(app)

    response = client.get("/docs", auth=("docs-user", "wrong-password"))

    assert response.status_code == 401


def test_api_documentation_fails_closed_without_basic_configuration(monkeypatch):
    monkeypatch.setattr(get_settings(), "hub_docs_username", "")
    monkeypatch.setattr(get_settings(), "hub_docs_password", "")
    client = TestClient(app)

    response = client.get("/docs")

    assert response.status_code == 503


def test_openapi_schema_requires_bearer_and_health_is_public(monkeypatch):
    monkeypatch.setattr(get_settings(), "hub_api_token", "test-secret")
    monkeypatch.setattr(get_settings(), "hub_docs_username", "docs-user")
    monkeypatch.setattr(get_settings(), "hub_docs_password", "docs-password")
    client = TestClient(app)

    response = client.get("/openapi.json", auth=("docs-user", "docs-password"))

    assert response.status_code == 200
    schema = response.json()
    assert schema["paths"]["/echo/echo"]["post"]["security"] == [{"HTTPBearer": []}]
    assert "security" not in schema["paths"]["/health"]["get"]
    api_response = client.post("/echo/echo", params={"message": "hola"})
    assert api_response.status_code == 401


def test_mcp_auth_middleware_rejects_missing_token():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    async def mcp_endpoint(request):
        return PlainTextResponse("mcp")

    mcp_app = Starlette(routes=[Route("/mcp", mcp_endpoint)])
    mcp_app.add_middleware(BearerTokenMiddleware, token_provider=lambda: "test-secret")
    client = TestClient(mcp_app)

    response = client.get("/mcp")

    assert response.status_code == 401

    authorized_response = client.get("/mcp", headers={"Authorization": "Bearer test-secret"})

    assert authorized_response.status_code == 200


def test_standalone_mcp_http_app_requires_bearer_token(monkeypatch):
    from API.services.essay_evaluator_mcp import _create_mcp_http_app

    monkeypatch.setattr(get_settings(), "hub_api_token", "test-secret")
    client = TestClient(_create_mcp_http_app())

    response = client.post("/mcp")

    assert response.status_code == 401


def test_standalone_mcp_http_fails_closed_when_client_auth_is_enabled(monkeypatch):
    from API.services.essay_evaluator_mcp import _create_mcp_http_app

    monkeypatch.setenv("API_CLIENT_AUTH_ENABLED", "true")
    monkeypatch.setattr(get_settings(), "hub_api_token", "test-secret")
    client = TestClient(_create_mcp_http_app())

    response = client.post("/mcp", headers={"Authorization": "Bearer test-secret"})

    assert response.status_code == 503
