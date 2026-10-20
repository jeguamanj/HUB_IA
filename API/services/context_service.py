# API/services/context_service.py

from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

try:
    from API.config import get_settings
    from API.core.context_engine import (
        DEFAULT_LMS_INSTITUTION_ID,
        InstitutionalContext,
        context_store,
        get_current_context,
        lms_activity_context_id,
    )
except ImportError:
    # pyrefly: ignore [missing-import]
    from config import get_settings  # type: ignore
    from core.context_engine import (
        DEFAULT_LMS_INSTITUTION_ID,
        InstitutionalContext,
        context_store,
        get_current_context,
        lms_activity_context_id,
    )

router = APIRouter()


def _assert_client_context_access(request: Request, ctx: InstitutionalContext) -> None:
    principal = getattr(request.state, "api_client", None)
    if principal is None:
        return
    if (
        principal.institution_id != ctx.institution_id
        or (principal.tenant_id is not None and principal.tenant_id != ctx.tenant_id)
    ):
        raise HTTPException(status_code=404, detail="Context not found")


class InitContextRequest(BaseModel):
    """Payload para inicializar un contexto institucional."""
    context_id: Optional[str] = Field(None, description="ID personalizado opcional o autogenerado")
    institution_id: Optional[str] = Field(DEFAULT_LMS_INSTITUTION_ID, description="Nombre o código de la institución")
    tenant_id: Optional[str] = Field("GENERAL", description="Facultad, sede o departamento")
    system_source: Optional[str] = Field("API_CLIENT", description="Sistema origen: CANVAS_LMS, HUBSPOT_CRM, N8N, etc.")
    user_id: Optional[str] = Field(None, description="Identificador único del usuario")
    user_role: Optional[str] = Field("DOCENTE", description="Rol del usuario: DOCENTE, ESTUDIANTE, ADMIN, etc.")
    evaluation_scale: Optional[float] = Field(20.0, description="Escala de calificación (ej. 20.0 o 100.0)")
    tone_policy: Optional[str] = Field("pedagógico, constructivo y formal", description="Tono requerido")
    global_policy: Optional[str] = Field(None, description="Normativa institucional obligatoria")
    custom_instructions: Optional[str] = Field(None, description="Instrucciones particulares de la sesión")
    ttl_seconds: Optional[int] = Field(3600, ge=1, description="Tiempo de vida de la sesión en segundos (default 1h)")
    context_grace_days: Optional[int] = Field(None, ge=0, le=365)
    slots: Optional[Dict[str, Dict[str, Any]]] = Field(default_factory=dict, description="Ranuras iniciales (lms, zoom, crm, etc.)")


class UpdateSlotRequest(BaseModel):
    """Payload para actualizar o enriquecer una ranura de herramienta."""
    data: Dict[str, Any] = Field(..., description="Datos específicos de la herramienta a incorporar")

class CloseContextRequest(BaseModel):
    context_grace_days: Optional[int] = Field(
        None,
        ge=0,
        le=365,
        description="Período de gracia después del cierre; por defecto usa LMS_CONTEXT_GRACE_DAYS.",
    )


@router.post("/init", summary="Inicializar Contexto Institucional")
async def init_context(
    request: InitContextRequest,
    http_request: Request = None,
):
    """
    Crea o registra una sesión de contexto institucional.
    Devuelve un `context_id` para reutilizar en cualquier herramienta MCP o endpoint sin reenviar el contexto.
    """
    principal = getattr(http_request.state, "api_client", None) if http_request else None
    if principal is not None and (
        principal.institution_id != request.institution_id
        or (principal.tenant_id is not None and principal.tenant_id != request.tenant_id)
    ):
        raise HTTPException(status_code=404, detail="Context not found")
    kwargs = request.model_dump(exclude_unset=True)
    if not kwargs.get("context_id"):
        kwargs.pop("context_id", None)
    requested_grace_days = kwargs.pop("context_grace_days", None)

    slots = kwargs.get("slots") or {}
    lms_slot = slots.get("lms") or {}
    course_id = str(lms_slot.get("course_id") or "").strip()
    assignment_id = str(lms_slot.get("assignment_id") or "").strip()
    is_lms_activity = bool(course_id and assignment_id)
    if is_lms_activity:
        if context_store.backend is None:
            raise HTTPException(
                status_code=503,
                detail="La persistencia durable de contextos LMS no está disponible; configure DB_DSN.",
            )
        context_id = lms_activity_context_id(
            str(kwargs.get("institution_id") or DEFAULT_LMS_INSTITUTION_ID),
            course_id,
            assignment_id,
        )
        requested_context_id = kwargs.get("context_id")
        if requested_context_id and requested_context_id != context_id:
            raise HTTPException(
                status_code=422,
                detail="Para un contexto LMS, context_id se deriva de institución, curso y tarea; omite el ID personalizado.",
            )
        kwargs["context_id"] = context_id
        kwargs["institution_id"] = str(
            kwargs.get("institution_id") or DEFAULT_LMS_INSTITUTION_ID
        )
        kwargs["lifecycle_type"] = "activity"
        kwargs["grace_period_days"] = (
            requested_grace_days
            if requested_grace_days is not None
            else get_settings().lms_context_grace_days
        )

    ctx = InstitutionalContext(**kwargs)
    if is_lms_activity:
        existing = await context_store.get_or_restore(ctx.context_id)
        if existing is not None:
            existing.institution_id = ctx.institution_id
            existing.tenant_id = ctx.tenant_id
            existing.system_source = ctx.system_source
            existing.evaluation_scale = ctx.evaluation_scale
            existing.tone_policy = ctx.tone_policy
            existing.global_policy = ctx.global_policy
            existing.custom_instructions = ctx.custom_instructions
            if requested_grace_days is not None:
                existing.grace_period_days = requested_grace_days
            existing.set_slot("lms", lms_slot)
            existing.lifecycle_type = "activity"
            existing.lifecycle_status = "active"
            existing.closed_at = None
            existing.retention_until = None
            ctx = existing
    await context_store.save_durable(ctx)
    return {
        "status": "success",
        "context_id": ctx.context_id,
        "expires_in_seconds": ctx.ttl_seconds if ctx.lifecycle_type == "session" else None,
        "lifecycle_type": ctx.lifecycle_type,
        "lifecycle_status": ctx.lifecycle_status,
        "grace_period_days": ctx.grace_period_days if ctx.lifecycle_type == "activity" else None,
        "institution_id": ctx.institution_id,
        "tenant_id": ctx.tenant_id,
        "user_role": ctx.user_role,
        "system_source": ctx.system_source,
        "compiled_instruction": ctx.to_system_instruction()
    }


@router.get("/list", summary="Listar Contextos Guardados")
async def list_contextos(active_only: bool = True):
    """
    Lista los contextos institucionales guardados (resumen para auditoría/UI).
    `?active_only=false` incluye también los expirados (aunque se limpian solos).
    """
    items = context_store.list_contexts(active_only=active_only)
    return {
        "status": "success",
        "total": len(items),
        "contextos": items,
    }


@router.get("/stats", summary="Estadísticas de Contextos")
async def stats_contextos():
    """
    Devuelve estadísticas de los contextos guardados (total, activos, por slot).
    """
    return {"status": "success", **context_store.stats()}


@router.get("/{context_id}", summary="Consultar Contexto Activo")
async def get_context_by_id(context_id: str, request: Request = None):
    """
    Recupera el contexto activo a partir de su ID.
    """
    ctx = await context_store.get_or_restore(context_id)
    if not ctx:
        raise HTTPException(status_code=404, detail=f"Contexto '{context_id}' no encontrado o expirado.")
    if request is not None:
        _assert_client_context_access(request, ctx)
    return ctx

@router.post("/{context_id}/slot/{slot_name}", summary="Actualizar Ranura de Herramienta")
async def update_slot(
    context_id: str,
    slot_name: str,
    request: UpdateSlotRequest,
    http_request: Request = None,
):
    """
    Actualiza la ranura de una herramienta específica (ej: 'lms', 'zoom', 'crm') dentro de un contexto activo.
    """
    ctx = await context_store.get_or_restore(context_id)
    if not ctx:
        raise HTTPException(status_code=404, detail=f"Contexto '{context_id}' no encontrado o expirado.")
    if http_request is not None:
        _assert_client_context_access(http_request, ctx)
    if ctx.lifecycle_type == "activity" and ctx.lifecycle_status == "closed":
        raise HTTPException(
            status_code=409,
            detail="La actividad LMS está cerrada; vuelva a inicializarla antes de modificar sus datos.",
        )

    ctx.set_slot(slot_name, request.data)
    await context_store.save_durable(ctx)

    return {
        "status": "success",
        "context_id": ctx.context_id,
        "slot_updated": slot_name,
        "slot_data": ctx.get_slot(slot_name)
    }

@router.post("/{context_id}/close", summary="Cerrar una actividad LMS")
async def close_context(
    context_id: str,
    request: CloseContextRequest = CloseContextRequest(),
    http_request: Request = None,
):
    """Close an LMS activity context and retain it for the configured grace period."""
    existing = await context_store.get_or_restore(context_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Contexto '{context_id}' no encontrado o expirado.")
    if http_request is not None:
        _assert_client_context_access(http_request, existing)
    try:
        ctx = await context_store.close_activity(
            context_id,
            grace_period_days=request.context_grace_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Contexto '{context_id}' no encontrado o expirado.")
    return {
        "status": "closed",
        "context_id": ctx.context_id,
        "lifecycle_type": ctx.lifecycle_type,
        "lifecycle_status": ctx.lifecycle_status,
        "closed_at": ctx.closed_at,
        "grace_period_days": ctx.grace_period_days,
        "retention_until": ctx.retention_until,
    }


@router.delete("/{context_id}", summary="Eliminar Contexto")
async def delete_context_by_id(context_id: str, request: Request = None):
    """
    Elimina explícitamente una sesión de contexto.
    """
    ctx = await context_store.get_or_restore(context_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Contexto '{context_id}' no encontrado.")
    if request is not None:
        _assert_client_context_access(request, ctx)
    deleted = context_store.delete(context_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Contexto '{context_id}' no encontrado.")
    return {"status": "success", "message": f"Contexto '{context_id}' eliminado correctamente."}


@router.get("/active/current", summary="Contexto Activo de la Solicitud Actual")
async def get_current_request_context(request: Request = None):
    """
    Devuelve el contexto inyectado automáticamente en la llamada actual por el middleware.
    """
    ctx = get_current_context()
    if not ctx:
        return {"active": False, "message": "No hay contexto institucional asociado a esta solicitud."}
    if request is not None:
        _assert_client_context_access(request, ctx)
    return {
        "active": True,
        "context": ctx,
        "system_instruction": ctx.to_system_instruction()
    }


def get_router() -> APIRouter:
    return router
