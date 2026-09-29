# =============================================================================
# tests/test_schemas.py — B1.1 `context-contract`
# -----------------------------------------------------------------------------
# Valida:
#   1. Slots tipados por dominio (lms/zoom/crm/custom) con forward-compat.
#   2. Migración de datos sueltos → contrato vigente, conservando campos
#      desconocidos (campos futuros no se pierden).
#   3. `schema_version` en `InstitutionalContext`: nuevo, viejo (sin versión) y
#      explícito cargan sin romper el contrato.
#   4. `set_slot(...)` con migración tipada y `migrate_all_slots` no destructivos.
# =============================================================================

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from API.core.context_engine import InstitutionalContext
from API.core.schemas import (
    SCHEMA_VERSION,
    LmsSlot,
    ZoomSlot,
    CrmSlot,
    SLOT_SCHEMAS,
    migrate_slot_data,
    validate_slot_data,
)


class TestSlotSchemas:
    def test_known_slots_registered(self):
        for name in ("lms", "zoom", "crm", "custom"):
            assert name in SLOT_SCHEMAS

    def test_validate_ok_and_unknown(self):
        assert validate_slot_data("lms", {"course_id": "101"}) == (True, [])
        assert validate_slot_data("zoom", {"storage_days": 60}) == (True, [])
        # Slots desconocidos no rompen nada.
        assert validate_slot_data("otro_dominio", {"k": "v"}) == (True, [])

    def test_migrate_normalizes_and_keeps_extras(self):
        out = migrate_slot_data("lms", {"course_id": "1", "rubric": [{"id": "r"}], "futuro": "keep"})
        assert out["course_id"] == "1"
        assert out["rubric"] == [{"id": "r"}]
        assert out["futuro"] == "keep"  # forward-compatible

    def test_migrate_pydantic_coerces_types(self):
        out = migrate_slot_data("zoom", {"storage_days": "45"})
        assert out["storage_days"] == 45
        assert isinstance(out["storage_days"], int)

    def test_migrate_accepts_model_instance(self):
        data = migrate_slot_data("crm", CrmSlot(customer_id="Z", lead_status="warm"))
        assert data["customer_id"] == "Z"


class TestContextSchemaVersion:
    def test_new_context_has_version(self):
        ctx = InstitutionalContext(slots={"lms": {"course_id": "101"}})
        assert ctx.schema_version == SCHEMA_VERSION

    def test_unversioned_context_loads(self):
        # Datos persistidos antes de B1.1 (sin schema_version).
        ctx = InstitutionalContext(**{"context_id": "c_old", "slots": {"lms": {"course_id": "5"}}})
        assert ctx.schema_version == SCHEMA_VERSION
        assert ctx.slots["lms"]["course_id"] == "5"

    def test_versioned_context_loads(self):
        ctx = InstitutionalContext(**{"schema_version": 1, "slots": {"zoom": {"account_id": "x"}}})
        assert ctx.schema_version == 1

    def test_validates_cannot_break(self):
        ctx = InstitutionalContext(slots={})
        ok, errs = ctx.validate_slot("crm", {"customer_id": "Z"})
        assert ok is True and errs == []


class TestMigrationNonDestructive:
    def test_set_slot_migration_keeps_future_fields(self):
        ctx = InstitutionalContext()
        ctx.set_slot("zoom", {"account_id": "ACCT", "storage_days": "30", "campo_futuro": True})
        assert ctx.slots["zoom"]["account_id"] == "ACCT"
        assert ctx.slots["zoom"]["campo_futuro"] is True
        assert ctx.slots["zoom"]["storage_days"] == 30

    def test_migrate_all_slots(self):
        ctx = InstitutionalContext(slots={"lms": {"course_id": "7", "extra": {"a": 1}}})
        ctx.migrate_all_slots()
        assert ctx.slots["lms"]["course_id"] == "7"
        assert ctx.slots["lms"]["extra"] == {"a": 1}
        assert ctx.schema_version == SCHEMA_VERSION