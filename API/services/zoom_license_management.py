# API/services/zoom_license_management.py
# -----------------------------------------------------------------------------
# Auditoría de licencias Zoom. FASE 2 · C2.3 `zoom-async`: usa capa asíncrona
# (httpx) para no bloquear el event-loop de FastAPI.
# =============================================================================

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
import csv
import io

# Importar funciones de Zoom y configuración compatible (capa async)
try:
    from API.utils.zoom_api_utils import (
        async_zoom_get_user,
        async_zoom_get_licensed_users,
        async_zoom_get_scheduled_meetings,
        async_zoom_get_host_meetings,
        async_get_zoom_auth_headers,
    )
    from API.config import ZOOM_CONFIG
except ImportError:
    # pyrefly: ignore [missing-import]
    from utils.zoom_api_utils import (
        async_zoom_get_user,
        async_zoom_get_licensed_users,
        async_zoom_get_scheduled_meetings,
        async_zoom_get_host_meetings,
        async_get_zoom_auth_headers,
    )
    # pyrefly: ignore [missing-import]
    from config import ZOOM_CONFIG


router = APIRouter()

# ============================================================
# CONFIGURACIÓN DE POLÍTICAS
# ============================================================

GRUPOS_PROTEGIDOS = [
    "Autoridades",
    "TI",
    "Soporte",
    "Docentes con carga",
    "Eventos",
]

LOOKBACK_DAYS = 90


# ============================================================
# MODELOS DE RESPUESTA
# ============================================================

class LicenseEvaluation(BaseModel):
    user_id: str
    email: str
    mantener: bool
    razon: str


# ============================================================
# LÓGICA DE DECISIÓN PRINCIPAL (pura, sin I/O)
# ============================================================

def evaluar_usuario(user: dict, scheduled: list, host_usage: list):
    """
    Aplica reglas para determinar si debe conservar o no licencia.
    """
    # 1. Rol crítico
    if user.get("role_name") in ["Admin", "Owner"]:
        return True, "Rol crítico (Admin/Owner)"

    # 2. Grupos protegidos
    group_ids = user.get("group_ids", [])
    if group_ids:
        return True, "Pertenece a un grupo protegido"

    # 3. Reuniones programadas
    if scheduled:
        return True, "Tiene reuniones programadas/recurrentes"

    # 4. Actividad como host
    if host_usage:
        return True, f"Actividad como host en los últimos {LOOKBACK_DAYS} días"

    # 5. Último login
    last_login = user.get("last_login_time")
    if last_login:
        try:
            dt_last_login = datetime.strptime(last_login, "%Y-%m-%dT%H:%M:%SZ")
            if dt_last_login > datetime.utcnow() - timedelta(days=LOOKBACK_DAYS):
                return True, "Login reciente"
        except Exception:
            pass

    return False, "Sin actividad como host, sin reuniones y sin rol/grupo protegido"


# ============================================================
# ENDPOINT: Evaluar un usuario individual
# ============================================================

@router.get("/evaluate/{user_id}", response_model=LicenseEvaluation)
async def evaluate_user(user_id: str):
    user = await async_zoom_get_user(user_id)
    scheduled = await async_zoom_get_scheduled_meetings(user_id)
    host_usage = await async_zoom_get_host_meetings(user_id, LOOKBACK_DAYS)

    mantener, razon = evaluar_usuario(user, scheduled, host_usage)

    return LicenseEvaluation(
        user_id=user_id,
        email=user.get("email"),
        mantener=mantener,
        razon=razon,
    )


# ============================================================
# ENDPOINT: Escaneo completo de la cuenta
# ============================================================

@router.get("/scan")
async def scan_all_users():
    users = await async_zoom_get_licensed_users()
    results = []

    for u in users:
        uid = u["id"]
        scheduled = await async_zoom_get_scheduled_meetings(uid)
        host_usage = await async_zoom_get_host_meetings(uid)

        mantener, razon = evaluar_usuario(u, scheduled, host_usage)

        results.append({
            "user_id": uid,
            "email": u["email"],
            "mantener": mantener,
            "razon": razon,
        })

    return {
        "total_evaluados": len(results),
        "data": results,
    }


# ============================================================
# ENDPOINT: Reporte en CSV
# ============================================================

@router.get("/report/csv")
async def report_csv():
    users = await async_zoom_get_licensed_users()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["user_id", "email", "mantener", "razon"])

    for u in users:
        uid = u["id"]
        scheduled = await async_zoom_get_scheduled_meetings(uid)
        host_usage = await async_zoom_get_host_meetings(uid)

        mantener, razon = evaluar_usuario(u, scheduled, host_usage)

        writer.writerow([uid, u["email"], mantener, razon])

    return {"csv": buffer.getvalue()}


# ============================================================
# ENDPOINT: Retirar licencia (downgrade)
# ============================================================

@router.post("/downgrade/{user_id}")
async def downgrade_license(user_id: str):
    """
    Cambia una licencia a BASIC (type = 1).
    """
    import httpx

    url = f"https://api.zoom.us/v2/users/{user_id}"
    body = {"type": 1}
    headers = await async_get_zoom_auth_headers()

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.patch(url, headers=headers, json=body)

    if response.status_code not in (200, 204):
        raise HTTPException(response.status_code, response.text)

    return {"success": True, "user_id": user_id}


# ============================================================
# FUNCION get_router() para mantener el estándar del proyecto
# ============================================================

def get_router():
    return router