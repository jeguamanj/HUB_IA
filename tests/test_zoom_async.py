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
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from API.services import zoom_license_management
from API.services import zoom_management_service
from API.utils import zoom_api_utils


class TestAsyncEndpoints:
    def test_zoom_license_endpoints_are_async(self):
        endpoints = [r.endpoint for r in zoom_license_management.router.routes if hasattr(r, "endpoint")]
        assert endpoints, "no hay endpoints"
        for ep in endpoints:
            assert inspect.iscoroutinefunction(ep), f"{ep.__name__} NO es async"

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


class TestPureHelpers:
    def test_format_meeting_id(self):
        assert zoom_api_utils.format_meeting_id("123456789") == "123456789"

    def test_is_recurring_meeting(self):
        rec = {"recurrence": {"type": 2, "repeat_interval": 1}}
        assert zoom_api_utils.is_recurring_meeting(rec) is True
        single = {"recurrence": {}, "occurrences": []}
        assert zoom_api_utils.is_recurring_meeting(single) is False