# API/services/context_service.py

from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

try:
    from API.core.context_engine import (
        InstitutionalContext,
        context_store,
        get_current_context
    )
except ImportError:
    # pyrefly: ignore [missing-import]
    from core.context_engine import (
        InstitutionalContext,
        context_store,
        get_current_context
    )

router = APIRouter()


class InitContextRequest(BaseModel):
    """Payload para inicializar un contexto institucional."""
    context_id: Optional[str] = Field(None, description="ID personalizado opcional o autogenerado")
    institution_id: Optional[str] = Field("UNIVERSIDAD_INSTITUCIONAL", description="Nombre o código de la institución")
    tenant_id: Optional[str] = Field("GENERAL", description="Facultad, sede o departamento")
    system_source: Optional[str] = Field("API_CLIENT", description="Sistema origen: CANVAS_LMS, HUBSPOT_CRM, N8N, etc.")
    user_id: Optional[str] = Field(None, description="Identificador único del usuario")
    user_role: Optional[str] = Field("DOCENTE", description="Rol del usuario: DOCENTE, ESTUDIANTE, ADMIN, etc.")
    evaluation_scale: Optional[float] = Field(20.0, description="Escala de calificación (ej. 20.0 o 100.0)")
    tone_policy: Optional[str] = Field("pedagógico, constructivo y formal", description="Tono requerido")
    global_policy: Optional[str] = Field(None, description="Normativa institucional obligatoria")
    custom_instructions: Optional[str] = Field(None, description="Instrucciones particulares de la sesión")
    ttl_seconds: Optional[int] = Field(3600, description="Tiempo de vida en segundos (default 1h)")
    slots: Optional[Dict[str, Dict[str, Any]]] = Field(default_factory=dict, description="Ranuras iniciales (lms, zoom, crm, etc.)")


class UpdateSlotRequest(BaseModel):
    """Payload para actualizar o enriquecer una ranura de herramienta."""
    data: Dict[str, Any] = Field(..., description="Datos específicos de la herramienta a incorporar")


@router.post("/init", summary="Inicializar Contexto Institucional")
async def init_context(request: InitContextRequest):
    """
    Crea o registra una sesión de contexto institucional.
    Devuelve un `context_id` para reutilizar en cualquier herramienta MCP o endpoint sin reenviar el contexto.
    """
    kwargs = request.model_dump(exclude_unset=True)
    if not kwargs.get("context_id"):
        kwargs.pop("context_id", None)

    ctx = context_store.create(**kwargs)
    return {
        "status": "success",
        "context_id": ctx.context_id,
        "expires_in_seconds": ctx.ttl_seconds,
        "institution_id": ctx.institution_id,
        "tenant_id": ctx.tenant_id,
        "user_role": ctx.user_role,
        "system_source": ctx.system_source,
        "compiled_instruction": ctx.to_system_instruction()
    }


@router.get("/{context_id}", summary="Consultar Contexto Activo")
async def get_context_by_id(context_id: str):
    """
    Recupera el contexto activo a partir de su ID.
    """
    ctx = context_store.get(context_id)
    if not ctx:
        raise HTTPException(status_code=404, detail=f"Contexto '{context_id}' no encontrado o expirado.")
    return ctx


@router.post("/{context_id}/slot/{slot_name}", summary="Actualizar Ranura de Herramienta")
async def update_slot(context_id: str, slot_name: str, request: UpdateSlotRequest):
    """
    Actualiza la ranura de una herramienta específica (ej: 'lms', 'zoom', 'crm') dentro de un contexto activo.
    """
    ctx = context_store.get(context_id)
    if not ctx:
        raise HTTPException(status_code=404, detail=f"Contexto '{context_id}' no encontrado o expirado.")

    ctx.set_slot(slot_name, request.data)
    context_store.save(ctx)

    return {
        "status": "success",
        "context_id": ctx.context_id,
        "slot_updated": slot_name,
        "slot_data": ctx.get_slot(slot_name)
    }


@router.delete("/{context_id}", summary="Eliminar Contexto")
async def delete_context_by_id(context_id: str):
    """
    Elimina explícitamente una sesión de contexto.
    """
    deleted = context_store.delete(context_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Contexto '{context_id}' no encontrado.")
    return {"status": "success", "message": f"Contexto '{context_id}' eliminado correctamente."}


@router.get("/active/current", summary="Contexto Activo de la Solicitud Actual")
async def get_current_request_context():
    """
    Devuelve el contexto inyectado automáticamente en la llamada actual por el middleware.
    """
    ctx = get_current_context()
    if not ctx:
        return {"active": False, "message": "No hay contexto institucional asociado a esta solicitud."}
    return {
        "active": True,
        "context": ctx,
        "system_instruction": ctx.to_system_instruction()
    }


def get_router() -> APIRouter:
    return router
