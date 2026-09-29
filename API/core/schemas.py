# =============================================================================
# API/core/schemas.py
# -----------------------------------------------------------------------------
# Esquemas Pydantic versionados para los slots del Context Fabric (FASE 1 ·
# Agente B1.1 `context-contract`).
#
# Objetivo:
#   - Definir contratos tipados por dominio (lms, zoom, crm, custom).
#   - Versionar esos contratos (SCHEMA_VERSION) para evolución no destructiva.
#   - Proveer migración forward-compatible de datos de slot (acepta versiones
#     antiguas y datos "sueltos", los normaliza sin romper lo existente).
#
# Compatibilidad:
#   - `InstitutionalContext` mantiene `slots: Dict[str, Dict[str, Any]]` como
#     contrato de almacenamiento (siempre JSON-serializable; nada cambia para
#     callers existentes). Este módulo añade una capa *de validación/migración*
#     opcional sobre ese contrato.
#   - `model_config = ConfigDict(extra="allow")` en cada slot: los campos nuevos
#     o desconocidos se conservan (forward-compatible), no se descartan.
# =============================================================================

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

from API.core.logging import get_logger

logger = get_logger("core.schemas")

# Versión actual del contrato de slots.
SCHEMA_VERSION = 1


# =============================================================================
# Slots tipados por dominio (extra="allow" → tolerantes a campos futuros)
# =============================================================================

class LmsSlot(BaseModel):
    """Ranura LMS (Canvas/Moodle/Blackboard...)."""
    model_config = ConfigDict(extra="allow")

    course_id: Optional[str] = None
    assignment_id: Optional[str] = None
    assignment_name: Optional[str] = None
    submission_id: Optional[str] = None
    submission_type: Optional[str] = None
    # Rúbrica (lista/dict/JSON raw); contenido arbitrario permitido.
    rubric: Optional[Any] = None
    scale: Optional[float] = None
    id_lms: Optional[str] = None


class ZoomSlot(BaseModel):
    """Ranura Zoom (meetings/webinars/licencias)."""
    model_config = ConfigDict(extra="allow")

    account_id: Optional[str] = None
    meeting_id: Optional[str] = None
    webinar_id: Optional[str] = None
    default_hosts: Optional[List[str]] = None
    recording_rules: Optional[Dict[str, Any]] = None
    storage_days: Optional[int] = None


class CrmSlot(BaseModel):
    """Ranura CRM (leads, pipeline, clientes)."""
    model_config = ConfigDict(extra="allow")

    customer_id: Optional[str] = None
    lead_status: Optional[str] = None
    pipeline_stage: Optional[str] = None
    owner_email: Optional[str] = None


class CustomSlot(BaseModel):
    """Ranura libre para herramientas futuras (clave-valor)."""
    model_config = ConfigDict(extra="allow")


# Registro de esquemas por nombre de slot.
SLOT_SCHEMAS: Dict[str, type[BaseModel]] = {
    "lms": LmsSlot,
    "zoom": ZoomSlot,
    "crm": CrmSlot,
    "custom": CustomSlot,
}


# =============================================================================
# Validación y migración de datos de slot
# =============================================================================

def validate_slot_data(slot_name: str, data: Any) -> Tuple[bool, List[str]]:
    """
    Valida los datos de una ranura contra su esquema tipado.

    Args:
        slot_name: Nombre del slot ('lms', 'zoom', 'crm', 'custom').
        data:      Datos a validar (dict, o instancia del esquema).

    Returns:
        (ok, errores): ok=True si pasa el esquema; errores lista de mensajes.
        Los slots desconocidos devuelven (True, []) (no se rompe nada).
    """
    schema = SLOT_SCHEMAS.get(slot_name)
    if schema is None:
        return True, []
    if data is None:
        return True, []
    try:
        if isinstance(data, BaseModel):
            schema(**data.model_dump())
        else:
            schema(**data) if isinstance(data, dict) else schema.model_validate(data)
    except Exception as exc:
        return False, [str(exc)]
    return True, []


def migrate_slot_data(
    slot_name: str,
    data: Any,
    from_version: int = 0,
    to_version: int = SCHEMA_VERSION,
) -> Dict[str, Any]:
    """
    Normaliza/valida los datos de una ranura al contrato vigente.

    - Accepta dicts sueltos (sin versión), instancias de slot o dicts ya
      poblados; devuelve siempre un `dict` con claves tipadas + extras.
    - Campos desconocidos se conservan (extra="allow"), por lo que datos de
      versiones futuras NO se pierden (forward-compatible).

    Args:
        slot_name:    Nombre del slot.
        data:         Datos de entrada (cualquier forma razonable).
        from_version: Versión de origen (0 = sin versionar).
        to_version:   Versión destino (normalmente SCHEMA_VERSION).

    Returns:
        Dict normalizado listo para guardarse en `context.slots[slot]`.
    """
    schema = SLOT_SCHEMAS.get(slot_name)
    if schema is None or data is None:
        return dict(data) if isinstance(data, dict) else {}

    if isinstance(data, BaseModel):
        data_dict = data.model_dump()
    elif isinstance(data, dict):
        data_dict = data
    else:
        try:
            data_dict = schema.model_validate(data).model_dump()
        except Exception:
            return {}

    try:
        # Instanciar y reconstruir: normaliza tipos de campos conocidos y
        # conserva los extras.
        validated = schema(**data_dict)
        out = validated.model_dump()
        # Conservar claves extra que Pydantic ya incluyó vía extra="allow".
        return out
    except Exception as exc:
        logger.warning(
            "migrate_slot_data(%s, v%s→v%s) fallo de validación; se devuelve raw: %s",
            slot_name, from_version, to_version, exc,
        )
        # Best-effort: devolver los datos originales (sin romper el contrato).
        return dict(data_dict)


def ensure_schema(dct: Dict[str, Any]) -> Dict[str, Any]:
    """
    Inyecta `schema_version` en un dict serializado (contexto) si no lo trae
    (compatibilidad con contextos persistidos antes de B1.1 → sin versión).
    """
    if "schema_version" not in dct:
        dct = dict(dct)
        dct["schema_version"] = SCHEMA_VERSION
    return dct


__all__ = [
    "SCHEMA_VERSION",
    "LmsSlot",
    "ZoomSlot",
    "CrmSlot",
    "CustomSlot",
    "SLOT_SCHEMAS",
    "validate_slot_data",
    "migrate_slot_data",
    "ensure_schema",
]