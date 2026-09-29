# API/services/zoom_management_service.py
# -----------------------------------------------------------------------------
# Gestión de configuraciones de sesiones y panelistas de Zoom.
# FASE 2 · C2.3 `zoom-async`: usa httpx.AsyncClient para no bloquear el loop.
# =============================================================================

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr
from typing import List, Optional
import json

import httpx

try:
    from API.core.logging import get_logger
except ImportError:  # pragma: no cover
    from core.logging import get_logger

logger = get_logger("services.zoom_management")

# Importación compatible (capa async)
try:
    from API.utils.zoom_api_utils import (
        async_get_zoom_auth_headers,
        async_detect_session_type,
        format_meeting_id,
    )
except ImportError:
    # pyrefly: ignore [missing-import]
    from utils.zoom_api_utils import (
        async_get_zoom_auth_headers,
        async_detect_session_type,
        format_meeting_id,
    )


# URL base para las peticiones a la API de Zoom
BASE_URL = "https://api.zoom.us/v2"
DEFAULT_TIMEOUT = 15.0

# -----------------------------------------------------
# MODELOS DE ENTRADA
# -----------------------------------------------------

class Panelist(BaseModel):
    """Modelo para agregar un nuevo panelista."""
    email: EmailStr
    name: str


class PanelistUpdate(BaseModel):
    """
    Modelo para modificar un panelista existente.
    Se usa para la operación GET+PUT de reemplazo.
    """
    email: Optional[EmailStr] = None
    name: Optional[str] = None


class MeetingUpdatePayload(BaseModel):
    """
    Datos que se pueden actualizar en una reunión o webinar de Zoom.
    """
    topic: Optional[str] = None
    agenda: Optional[str] = None
    # Campos que van dentro de 'settings'
    alternative_hosts: Optional[List[str]] = None
    approval_type: Optional[int] = None

    model_config = {"extra": "ignore"}


# -----------------------------------------------------
# Helpers de petición asíncrona
# -----------------------------------------------------

async def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)


async def _get_headers() -> dict:
    return await async_get_zoom_auth_headers()


# -----------------------------------------------------
# ROUTER
# -----------------------------------------------------

def get_router():
    router = APIRouter()

    # --- 1. Actualización de Configuración General (PATCH /sessions/{id}/config) ---
    @router.patch("/sessions/{session_id}/config", tags=["Zoom Management"],
                  summary="Actualiza configuraciones generales (incluye hosts alternativos)")
    async def update_session_configuration(session_id: str, params: MeetingUpdatePayload):
        headers = await _get_headers()
        formatted_id = format_meeting_id(session_id)
        logger.info("Scope Requerido: webinar:write:webinar:admin o meeting:write:admin")

        try:
            session_type = await async_detect_session_type(formatted_id, headers)
            logger.info("Tipo de sesión detectado: %s para ID: %s", session_type.upper(), formatted_id)
        except HTTPException as e:
            logger.warning("Fallo en la detección de sesión para %s: %s", formatted_id, e.detail)
            raise HTTPException(status_code=e.status_code, detail=e.detail)

        # Separar campos de nivel superior de los campos de 'settings'
        payload = {}
        settings = {}
        raw_data = params.model_dump(exclude_none=True)
        for key, value in raw_data.items():
            if key in ["topic", "agenda"]:
                payload[key] = value
            else:
                settings[key] = value

        # Formatear alternative_hosts para Zoom (cadena separada por comas)
        if 'alternative_hosts' in settings and isinstance(settings['alternative_hosts'], list):
            settings['alternative_hosts'] = ",".join(settings['alternative_hosts'])

        if settings:
            payload['settings'] = settings

        if not payload:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No se proporcionaron datos para actualizar.")

        url = f"{BASE_URL}/{session_type}s/{formatted_id}"
        logger.debug("URL de la llamada (PATCH): %s", url)
        logger.debug("PAYLOAD enviado: %s", json.dumps(payload, indent=2))

        async with _client() as client:
            response = await client.patch(url, headers=headers, json=payload)
        logger.debug("RESPUESTA DE ZOOM - Código: %s", response.status_code)
        logger.debug("RESPUESTA DE ZOOM - Cuerpo: %s", response.text)

        if response.status_code not in [status.HTTP_204_NO_CONTENT, status.HTTP_200_OK]:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Error en API de Zoom al actualizar la sesión: {response.text}",
            )

        message = f"Configuración de {session_type.capitalize()} {session_id} actualizada. Hosts Alternativos deberían estar asignados."
        if response.status_code == status.HTTP_204_NO_CONTENT:
            message += " (Código 204 indica éxito)."

        return {"success": True, "message": message}

    # --- 2. Agregar panelistas (POST /webinars/{id}/panelists) ---
    @router.post("/webinars/{webinar_id}/panelists", tags=["Zoom Management"],
                 summary="Agrega uno o más panelistas a un Webinar")
    async def add_webinar_panelists(webinar_id: str, panelists: List[Panelist]):
        headers = await _get_headers()
        formatted_id = format_meeting_id(webinar_id)
        logger.info("Scope Requerido: webinar:write:panelist:admin")

        payload = {"panelists": [p.model_dump() for p in panelists]}
        url = f"{BASE_URL}/webinars/{formatted_id}/panelists"
        async with _client() as client:
            response = await client.post(url, headers=headers, json=payload)
        if response.status_code not in [status.HTTP_201_CREATED, status.HTTP_200_OK]:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Error en API de Zoom al agregar panelistas: {response.text}",
            )
        return {"success": True, "message": f"Panelistas agregados al Webinar {webinar_id}."}

    # --- 3. Eliminar panelista por email (DELETE /webinars/{id}/panelists/{email}) ---
    @router.delete("/webinars/{webinar_id}/panelists/{panelist_email}", tags=["Zoom Management"],
                   summary="Elimina un panelista específico de un webinar (Refleja botón 'Eliminar').")
    async def delete_panelist(webinar_id: str, panelist_email: EmailStr):
        headers = await _get_headers()
        formatted_id = format_meeting_id(webinar_id)
        logger.info("Scope Requerido: webinar:write:panelist:admin")

        url = f"{BASE_URL}/webinars/{formatted_id}/panelists/{panelist_email}"
        async with _client() as client:
            response = await client.delete(url, headers=headers)
        if response.status_code != status.HTTP_204_NO_CONTENT:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Error en API de Zoom al eliminar panelista: {response.text}",
            )
        return {"success": True, "message": f"Panelista {panelist_email} eliminado del Webinar {webinar_id}."}

    # --- 4. Obtener lista completa de panelistas (GET) ---
    @router.get("/webinars/{webinar_id}/panelists/list", tags=["Zoom Management"],
                summary="Obtiene la lista completa de panelistas del webinar.")
    async def get_all_panelists(webinar_id: str):
        headers = await _get_headers()
        formatted_id = format_meeting_id(webinar_id)
        logger.info("Scope Requerido: webinar:read:panelist:admin")

        url = f"{BASE_URL}/webinars/{formatted_id}/panelists"
        async with _client() as client:
            response = await client.get(url, headers=headers)
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Error al obtener lista de panelistas: {response.text}",
            )
        return response.json()

    # --- 5. Reemplazar/Modificar Panelista (GET + PUT de reemplazo) ---
    @router.put("/webinars/{webinar_id}/panelists/replace_data/{current_email}",
                tags=["Zoom Management"],
                summary="Modifica datos de un panelista (GET + PUT Reemplazo COMPLETO). Asume cambio de JoinURL.")
    async def replace_panelist_data(webinar_id: str, current_email: EmailStr, params: PanelistUpdate):
        headers = await _get_headers()
        formatted_id = format_meeting_id(webinar_id)
        logger.info("Scope Requerido: webinar:write:panelist:admin")

        update_data = params.model_dump(exclude_none=True)
        if not update_data:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Se requiere 'name' o 'email' para la actualización.")

        # PASO 1: Obtener la lista actual (GET)
        list_url = f"{BASE_URL}/webinars/{formatted_id}/panelists"
        async with _client() as client:
            list_response = await client.get(list_url, headers=headers)
        if list_response.status_code != 200:
            raise HTTPException(status_code=list_response.status_code, detail=f"Fallo al obtener la lista de panelistas: {list_response.text}")

        panelists_data = list_response.json().get("panelists", [])
        found = False

        # PASO 2: Reconstruir la lista aplicando la modificación (solo email/name)
        final_panelist_list = []
        for p in panelists_data:
            panelist_to_add = {"email": p.get("email"), "name": p.get("name")}
            if p.get("email") == current_email:
                panelist_to_add['email'] = update_data.get('email', panelist_to_add['email'])
                panelist_to_add['name'] = update_data.get('name', panelist_to_add['name'])
                found = True
            final_panelist_list.append(panelist_to_add)

        if not found:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Panelista con email {current_email} no encontrado.")

        # PASO 3: Reemplazar la lista completa (PUT)
        payload = {"panelists": final_panelist_list}
        put_url = f"{BASE_URL}/webinars/{formatted_id}/panelists"
        async with _client() as client:
            response = await client.put(put_url, headers=headers, json=payload)

        if response.status_code not in [status.HTTP_204_NO_CONTENT, status.HTTP_200_OK]:
            raise HTTPException(status_code=response.status_code, detail=f"Error en API de Zoom al reemplazar panelistas: {response.text}")

        return {"success": True, "message": f"Datos del panelista {current_email} actualizados mediante reemplazo. Se recomienda obtener la nueva JoinURL."}

    # --- 6. Verificación - Obtener Configuración de Sesión (Hosts Alternativos) ---
    @router.get("/sessions/{session_id}/config/hosts", tags=["Zoom Management"],
                summary="Obtiene la configuración de la sesión para verificar hosts alternativos.")
    async def get_session_config(session_id: str):
        headers = await _get_headers()
        formatted_id = format_meeting_id(session_id)
        logger.info("Scope Requerido: webinar:read:admin o meeting:read:admin")

        try:
            session_type = await async_detect_session_type(formatted_id, headers)
        except HTTPException as e:
            raise HTTPException(status_code=e.status_code, detail=e.detail)
        url = f"{BASE_URL}/{session_type}s/{formatted_id}"
        async with _client() as client:
            response = await client.get(url, headers=headers)
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=f"Error al obtener la sesión: {response.text}")
        data = response.json()
        alternative_hosts = data.get("settings", {}).get("alternative_hosts")
        return {
            "session_type": session_type,
            "topic": data.get("topic"),
            "alternative_hosts_raw": alternative_hosts,
        }

    # --- 7. Obtener Link de Panelista (GET /.../link) ---
    @router.get("/webinars/{webinar_id}/panelists/{email}/link", tags=["Zoom Management"],
                summary="Obtiene el link de registro/unión para un panelista")
    async def get_panelist_join_link(webinar_id: str, email: EmailStr):
        headers = await _get_headers()
        formatted_id = format_meeting_id(webinar_id)
        logger.info("Scope Requerido: webinar:read:panelist:admin")

        url = f"{BASE_URL}/webinars/{formatted_id}/panelists"
        async with _client() as client:
            response = await client.get(url, headers=headers)
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Error al obtener lista de panelistas: {response.text}",
            )
        data = response.json()
        panelist = next((p for p in data.get("panelists", []) if p.get("email") == email), None)
        if not panelist:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Panelista con email {email} no encontrado en el Webinar {webinar_id}.",
            )
        return {
            "success": True,
            "email": email,
            "join_url": panelist.get("join_url"),
            "message": "Enlace de unión obtenido exitosamente.",
        }

    # --- 8. Obtener Link de Inicio de Sesión (Start URL) ---
    @router.get("/sessions/{session_id}/link_inicio_sesion_anfitrion_alternativo", tags=["Zoom Management"],
                summary="Obtiene el link de inicio (Start URL) para el Host/Host Alternativo.")
    async def get_session_start_link(session_id: str):
        headers = await _get_headers()
        formatted_id = format_meeting_id(session_id)
        logger.info("Scope Requerido: webinar:read:admin o meeting:read:admin")

        try:
            session_type = await async_detect_session_type(formatted_id, headers)
        except HTTPException as e:
            raise HTTPException(status_code=e.status_code, detail=e.detail)

        url = f"{BASE_URL}/{session_type}s/{formatted_id}"
        logger.debug("URL de la llamada (GET Link): %s", url)

        async with _client() as client:
            response = await client.get(url, headers=headers)

        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=f"Error al obtener la sesión: {response.text}")

        data = response.json()
        start_url = data.get("start_url")
        join_url = data.get("join_url")

        if not start_url:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Start URL no disponible para esta sesión. Intente usar el Join URL.")

        return {
            "success": True,
            "session_type": session_type.capitalize(),
            "start_url": start_url,
            "join_url": join_url,
            "message": "Enlace de inicio (Start URL) para el Host/Host Alternativo obtenido exitosamente. El host debe estar logueado para usarlo.",
        }

    return router