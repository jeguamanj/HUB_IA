# =============================================================================
# tests/test_zoom_async.py — C2.3 `zoom-async`
# -----------------------------------------------------------------------------
# Valida la migración síncrona→asíncrona de los servicios Zoom:
#   1. Ningún endpoint de los routers migrados es síncrono (todos async → no
#      bloquean el event-loop de FastAPI).
#   2. Los archivos migrados ya NO importan/usan `requests` (usan httpx async).
#   3. Existen las utilidades async (`async_get_zoom_auth_headers`, etc.) y son
#      funciones coroutine.
#   4. Los helpers puros (sin I/O) siguen funcionando.
# =============================================================================

import inspect
import sys
import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from API.services import zoom_license_management
from API.services import zoom_management_service
from API.services import zoom_reports
from API.utils import zoom_api_utils


class TestAsyncEndpoints:
    def test_zoom_license_endpoints_are_async(self):
        endpoints = [r.endpoint for r in zoom_license_management.router.routes if hasattr(r, "endpoint")]
        assert endpoints, "no hay endpoints"
        for ep in endpoints:
            assert inspect.iscoroutinefunction(ep), f"{ep.__name__} NO es async"


class TestZoomReportThreadpool:
    def test_blocking_report_handlers_are_sync_and_use_fastapi_threadpool(self):
        handlers = {
            route.path: route.endpoint
            for route in zoom_reports.router.routes
            if hasattr(route, "endpoint")
        }
        for path in ("/zoom/report/check", "/zoom/report/excel"):
            assert path in handlers
            assert not inspect.iscoroutinefunction(handlers[path])

    def test_reports_reuse_shared_zoom_helpers(self):
        assert zoom_reports.generate_zoom_token is zoom_api_utils.generate_zoom_token
        assert zoom_reports.format_meeting_id is zoom_api_utils.format_meeting_id
        assert zoom_reports.detect_session_type is zoom_api_utils.detect_session_type

    def test_report_client_reuses_thread_local_pool(self):
        session = zoom_reports._get_zoom_session()
        try:
            assert zoom_reports._get_zoom_session() is session
            assert session.get_adapter("https://")._pool_maxsize == 4
        finally:
            session.close()
            del zoom_reports._zoom_session_local.session

    def test_report_requests_have_long_timeout_and_reject_overload(self, monkeypatch):
        class FakeSession:
            def __init__(self):
                self.kwargs = None

            def get(self, url, **kwargs):
                self.kwargs = kwargs
                return "response"

        session = FakeSession()
        monkeypatch.setattr(zoom_reports, "_get_zoom_session", lambda: session)
        assert zoom_reports._zoom_get("https://api.zoom.us/v2/test") == "response"
        assert session.kwargs["timeout"] == 60.0

        acquired = []
        try:
            for _ in range(zoom_reports.MAX_CONCURRENT_ZOOM_REPORTS):
                zoom_reports._report_semaphore.acquire()
                acquired.append(True)

            with pytest.raises(HTTPException) as error:
                zoom_reports._limited_report_concurrency(lambda: None)()
            assert error.value.status_code == 429
            assert error.value.headers["Retry-After"] == "5"
        finally:
            for _ in acquired:
                zoom_reports._report_semaphore.release()

    def test_zoom_management_endpoints_are_async(self):
        router = zoom_management_service.get_router()
        endpoints = [r.endpoint for r in router.routes if hasattr(r, "endpoint")]
        assert endpoints, "no hay endpoints"
        for ep in endpoints:
            assert inspect.iscoroutinefunction(ep), f"{ep.__name__} NO es async"


class TestNoBlockingRequests:
    def test_no_requests_in_license_service(self):
        source = inspect.getsource(zoom_license_management)
        assert "import requests" not in source
        assert "requests." not in source

    def test_no_requests_in_management_service(self):
        source = inspect.getsource(zoom_management_service)
        assert "import requests" not in source
        assert "requests." not in source


class TestAsyncUtils:
    def test_async_auth_helpers_exist_and_are_coroutines(self):
        for name in (
            "async_generate_zoom_token",
            "async_get_zoom_auth_headers",
            "async_detect_session_type",
            "async_zoom_get_user",
            "async_zoom_get_licensed_users",
            "async_zoom_get_scheduled_meetings",
            "async_zoom_get_host_meetings",
        ):
            fn = getattr(zoom_api_utils, name, None)
            assert fn is not None, f"falta {name}"
            assert inspect.iscoroutinefunction(fn), f"{name} NO es async"

    def test_sync_helpers_preserved_for_backward_compat(self):
        # C2.3 mantiene la capa síncrona para zoom_reports.py (hasta C2.4).
        for name in ("generate_zoom_token", "get_zoom_auth_headers", "detect_session_type"):
            assert callable(getattr(zoom_api_utils, name, None))

    def test_sync_zoom_helpers_use_short_network_timeouts(self, monkeypatch):
        class Response:
            status_code = 200

        calls = []

        def fake_get(url, **kwargs):
            calls.append(kwargs)
            return Response()

        monkeypatch.setattr(zoom_api_utils.requests, "get", fake_get)
        assert zoom_api_utils.detect_session_type("123", {}) == "meeting"
        assert calls[0]["timeout"] == 5.0

    def test_async_helpers_reuse_bounded_http_client(self, monkeypatch):
        monkeypatch.setattr(zoom_api_utils, "_ASYNC_ZOOM_CLIENT", None)

        async def verify_client_pool():
            first = await zoom_api_utils.get_async_zoom_client()
            second = await zoom_api_utils.get_async_zoom_client()
            assert first is second
            await zoom_api_utils.close_async_zoom_client()
            assert first.is_closed

        asyncio.run(verify_client_pool())


def test_zoom_license_bulk_scans_reject_overlapping_requests(monkeypatch):
    semaphore = asyncio.Semaphore(1)
    monkeypatch.setattr(zoom_license_management, "_scan_semaphore", semaphore)

    async def handler():
        return "complete"

    protected_handler = zoom_license_management._single_concurrent_scan(handler)

    async def run():
        assert await protected_handler() == "complete"
        await semaphore.acquire()
        try:
            with pytest.raises(HTTPException) as error:
                await protected_handler()
            assert error.value.status_code == 429
            assert error.value.headers["Retry-After"] == "5"
        finally:
            semaphore.release()

    asyncio.run(run())


class TestPureHelpers:
    def test_format_meeting_id(self):
        assert zoom_api_utils.format_meeting_id("123456789") == "123456789"

    def test_is_recurring_meeting(self):
        rec = {"recurrence": {"type": 2, "repeat_interval": 1}}
        assert zoom_api_utils.is_recurring_meeting(rec) is True
        single = {"recurrence": {}, "occurrences": []}
        assert zoom_api_utils.is_recurring_meeting(single) is False