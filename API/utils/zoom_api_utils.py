# API/utils/zoom_api_utils.py
# -----------------------------------------------------------------------------
# Utilidades de la API de Zoom.
#
# Capa SÍNCRONA (retro-compatible): funciones `requests` existentes (usadas por
# zoom_reports.py, que se refactoriza en C2.4).
# Capa ASÍNCRONA (FASE 2 · C2.3 `zoom-async`): `async_*` sobre `httpx.AsyncClient`
# para NO bloquear el event-loop de FastAPI en los routers de servicios.
# =============================================================================

import requests
import os
import base64
import urllib.parse
from dotenv import load_dotenv
from fastapi import HTTPException
from typing import Dict, Optional, List, Any

try:
    from API.config import settings
except ImportError:  # pragma: no cover
    from config import settings

try:
    from API.core.logging import get_logger
except ImportError:  # pragma: no cover
    from core.logging import get_logger

logger = get_logger("utils.zoom_api_utils")

# Cargar variables de entorno (idempotente; la config ya carga .env).
load_dotenv()

# Credenciales de entorno (reutilizadas de tu proyecto). Preferir `settings.*`.
ZOOM_ACCOUNT_ID = os.getenv("ZOOM_ACCOUNT_ID") or getattr(settings, "zoom_account_id", "")
ZOOM_CLIENT_ID = os.getenv("ZOOM_CLIENT_ID") or getattr(settings, "zoom_client_id", "")
ZOOM_CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET") or getattr(settings, "zoom_client_secret", "")

_DEFAULT_TIMEOUT = 15.0


# =============================================================================
# 1. AUTENTICACIÓN — variante síncrona (retro-compatible)
# =============================================================================

def generate_zoom_token() -> str:
    """
    Genera un token de acceso válido de Zoom usando credenciales de cuenta.
    Copia exacta de la lógica de tu archivo de reportes.
    """
    if not all([ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, ZOOM_ACCOUNT_ID]):
        raise HTTPException(
            status_code=500,
            detail="Credenciales de Zoom incompletas en variables de entorno."
        )

    credentials = f"{ZOOM_CLIENT_ID}:{ZOOM_CLIENT_SECRET}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()

    url = f"https://zoom.us/oauth/token?grant_type=account_credentials&account_id={ZOOM_ACCOUNT_ID}"
    headers = {"Authorization": f"Basic {encoded_credentials}"}

    response = requests.post(url, headers=headers)

    if response.status_code == 200:
        return response.json().get("access_token")

    raise HTTPException(
        status_code=response.status_code,
        detail=f"Fallo al generar el token de Zoom: {response.text}"
    )


def get_zoom_auth_headers() -> Dict[str, str]:
    """
    Función de utilidad que genera el token y devuelve los headers de autorización
    necesarios para las peticiones a la API.
    """
    token = generate_zoom_token()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# =============================================================================
# 2. UTILIDADES COMUNES (síncronas, sin I/O) — retro-compatible
# =============================================================================

def format_meeting_id(meeting_id: str) -> str:
    """
    Formatea el meeting ID (decodificación URL) para los diferentes endpoints de Zoom.
    """
    if '%' in meeting_id:
        meeting_id = urllib.parse.unquote(meeting_id)
    return meeting_id


def detect_session_type(meeting_id: str, headers: dict) -> str:
    """
    Detecta si el ID corresponde a una reunión (meeting) o webinar.
    (Síncrona; mantener para paths síncronos. Preferir `async_detect_session_type`.)
    """
    formatted_id = format_meeting_id(meeting_id)

    url_meeting = f"https://api.zoom.us/v2/meetings/{formatted_id}"
    response_meeting = requests.get(url_meeting, headers=headers)
    if response_meeting.status_code == 200:
        return "meeting"

    url_webinar = f"https://api.zoom.us/v2/webinars/{formatted_id}"
    response_webinar = requests.get(url_webinar, headers=headers)
    if response_webinar.status_code == 200:
        return "webinar"

    return "meeting"


def is_recurring_meeting(session_data: dict) -> bool:
    """
    Determina si una reunión es recurrente usando múltiples indicadores.
    (Sin I/O — reutilizable en ambas capas.)
    """
    recurrence = session_data.get("recurrence", {})
    recurrence_type = recurrence.get("type")

    if (
        recurrence_type in [1, 2, 3, 4, 5, 6, 7, 8] or
        recurrence.get("repeat_interval") or
        recurrence.get("weekly_days") or
        recurrence.get("monthly_day") or
        recurrence.get("monthly_week") or
        recurrence.get("monthly_week_day") or
        recurrence.get("end_times") or
        recurrence.get("end_date_time") or
        (session_data.get("occurrences") and len(session_data["occurrences"]) > 1)
    ):
        return True

    return False


# =============================================================================
# 3. GESTIÓN DE LICENCIAS — capa síncrona (retro-compatible)
# =============================================================================

def zoom_get_user(user_id: str):
    url = f"https://api.zoom.us/v2/users/{user_id}"
    headers = get_zoom_auth_headers()
    response = requests.get(url, headers=headers)

    if response.status_code not in (200, 201):
        raise HTTPException(response.status_code, response.text)

    return response.json()


def zoom_get_licensed_users():
    url = "https://api.zoom.us/v2/users"
    params = {"status": "active", "page_size": 300}
    headers = get_zoom_auth_headers()
    results = []

    while True:
        r = requests.get(url, headers=headers, params=params)
        if r.status_code != 200:
            raise HTTPException(r.status_code, r.text)

        data = r.json()
        users = data.get("users", [])
        licensed = [u for u in users if u.get("type", 1) != 1]
        results.extend(licensed)

        next_token = data.get("next_page_token")
        if not next_token:
            break
        params["next_page_token"] = next_token

    return results


def zoom_get_scheduled_meetings(user_id: str):
    url = f"https://api.zoom.us/v2/users/{user_id}/meetings"
    headers = get_zoom_auth_headers()

    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        return []

    return r.json().get("meetings", [])


def zoom_get_host_meetings(user_id: str, days: int = 90):
    from datetime import datetime, timedelta

    end = datetime.utcnow()
    start = end - timedelta(days=days)

    url = f"https://api.zoom.us/v2/report/users/{user_id}/meetings"
    params = {
        "from": start.strftime("%Y-%m-%d"),
        "to": end.strftime("%Y-%m-%d")
    }
    headers = get_zoom_auth_headers()

    r = requests.get(url, headers=headers, params=params)

    if r.status_code == 404:
        return []  # No tiene reuniones como host
    if r.status_code != 200:
        raise HTTPException(r.status_code, r.text)

    return r.json().get("meetings", [])


# =============================================================================
# 4. CAPA ASÍNCRONA (FASE 2 · C2.3 `zoom-async`) — httpx.AsyncClient
#    No bloquea el event-loop de FastAPI.
# =============================================================================

async def async_generate_zoom_token(timeout: float = _DEFAULT_TIMEOUT) -> str:
    """Obtiene el token de acceso Zoom de forma asíncrona (httpx)."""
    if not all([ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, ZOOM_ACCOUNT_ID]):
        raise HTTPException(
            status_code=500,
            detail="Credenciales de Zoom incompletas en variables de entorno.",
        )

    credentials = f"{ZOOM_CLIENT_ID}:{ZOOM_CLIENT_SECRET}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()

    url = (
        f"https://zoom.us/oauth/token?grant_type=account_credentials"
        f"&account_id={ZOOM_ACCOUNT_ID}"
    )
    headers = {"Authorization": f"Basic {encoded_credentials}"}

    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers)

    if response.status_code == 200:
        token = response.json().get("access_token")
        if token:
            return token

    raise HTTPException(
        status_code=response.status_code,
        detail=f"Fallo al generar el token de Zoom: {response.text[:200]}",
    )


async def async_get_zoom_auth_headers(timeout: float = _DEFAULT_TIMEOUT) -> Dict[str, str]:
    """Headers de autorización (asíncrono). Reutiliza el token de la capa async."""
    token = await async_generate_zoom_token(timeout=timeout)
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def async_detect_session_type(
    meeting_id: str, headers: dict, timeout: float = _DEFAULT_TIMEOUT
) -> str:
    """Detecta si el ID es meeting o webinar (asíncrono)."""
    import httpx

    formatted = format_meeting_id(meeting_id)
    async with httpx.AsyncClient(timeout=timeout) as client:
        url_meeting = f"https://api.zoom.us/v2/meetings/{formatted}"
        rm = await client.get(url_meeting, headers=headers)
        if rm.status_code == 200:
            return "meeting"
        url_webinar = f"https://api.zoom.us/v2/webinars/{formatted}"
        rw = await client.get(url_webinar, headers=headers)
        if rw.status_code == 200:
            return "webinar"
    return "meeting"


async def _async_zoom_request(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json: Optional[Any] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = _DEFAULT_TIMEOUT,
):
    """
    Ejecuta una petición Zoom asíncrona y devuelve `httpx.Response`.
    Lanza `HTTPException` ante status >= 400 (salvo 404 que se devuelve como tal).
    """
    import httpx

    headers = headers or await async_get_zoom_auth_headers(timeout=timeout)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(
            method.upper(),
            url,
            headers=headers,
            json=json,
            params=params,
        )
    if response.status_code >= 500:
        raise HTTPException(status_code=response.status_code, detail=response.text[:300])
    return response


async def async_zoom_get_user(user_id: str, timeout: float = _DEFAULT_TIMEOUT) -> Dict[str, Any]:
    url = f"https://api.zoom.us/v2/users/{user_id}"
    resp = await _async_zoom_request("GET", url, timeout=timeout)
    if resp.status_code not in (200, 201):
        raise HTTPException(resp.status_code, resp.text)
    return resp.json()


async def async_zoom_get_licensed_users(timeout: float = _DEFAULT_TIMEOUT) -> List[Dict[str, Any]]:
    url = "https://api.zoom.us/v2/users"
    params: Dict[str, Any] = {"status": "active", "page_size": 300}
    results: List[Dict[str, Any]] = []

    while True:
        resp = await _async_zoom_request("GET", url, params=params, timeout=timeout)
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, resp.text)
        data = resp.json()
        users = data.get("users", [])
        results.extend([u for u in users if u.get("type", 1) != 1])
        next_token = data.get("next_page_token")
        if not next_token:
            break
        params["next_page_token"] = next_token
    return results


async def async_zoom_get_scheduled_meetings(user_id: str, timeout: float = _DEFAULT_TIMEOUT) -> List[Dict[str, Any]]:
    url = f"https://api.zoom.us/v2/users/{user_id}/meetings"
    resp = await _async_zoom_request("GET", url, timeout=timeout)
    if resp.status_code != 200:
        return []
    return resp.json().get("meetings", [])


async def async_zoom_get_host_meetings(user_id: str, days: int = 90, timeout: float = _DEFAULT_TIMEOUT) -> List[Dict[str, Any]]:
    from datetime import datetime, timedelta

    end = datetime.utcnow()
    start = end - timedelta(days=days)
    url = f"https://api.zoom.us/v2/report/users/{user_id}/meetings"
    params = {"from": start.strftime("%Y-%m-%d"), "to": end.strftime("%Y-%m-%d")}

    resp = await _async_zoom_request("GET", url, params=params, timeout=timeout)
    if resp.status_code == 404:
        return []
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, resp.text)
    return resp.json().get("meetings", [])