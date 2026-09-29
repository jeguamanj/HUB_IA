# =============================================================================
# tests/test_prompt_compiler.py — B1.3 `prompt-compiler`
# -----------------------------------------------------------------------------
# Valida:
#   1. El modo compact NO filtra metadatos LMS (course_id, assignment_id,
#      account_id, customer_id) al prompt del LLM.
#   2. Ambos modos (compact/full) se mantienen dentro de su presupuesto de
#      tokens (medible), garantizando ahorro y no desborde.
#   3. El estimador y la compilación a nivel módulo/current-context funcionan.
# =============================================================================

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from API.core.context_engine import (
    COMPACT_TOKEN_BUDGET,
    FULL_TOKEN_BUDGET,
    InstitutionalContext,
    compile_system_prompt,
    estimate_tokens,
    get_current_context,
    set_current_context,
)

_META_LEAKS = ("79871", "992586", "ZACCT-99", "CUST-777", "Ensayo FODA")


def _full_ctx() -> InstitutionalContext:
    return InstitutionalContext(
        institution_id="UNIVERSIDAD_UTPL",
        evaluation_scale=10.0,
        tone_policy="pedagogico, constructivo",
        global_policy="No plagio; citar fuentes.",
        slots={
            "lms": {
                "course_id": "79871",
                "assignment_id": "992586",
                "assignment_name": "Ensayo FODA",
                "rubric": [{"id": "_830", "points": 2.5}],
            },
            "zoom": {"account_id": "ZACCT-99"},
            "crm": {"customer_id": "CUST-777"},
        },
    )


class TestEstimateTokens:
    def test_empty(self):
        assert estimate_tokens("") == 0

    def test_nonempty(self):
        assert estimate_tokens("hola mundo") >= 1
        # Estimación crece con la longitud.
        long = estimate_tokens("x" * 400)
        short = estimate_tokens("x" * 10)
        assert long > short


class TestCompactNoLeak:
    def test_no_lms_metadata_in_compact(self):
        ctx = _full_ctx()
        sp = compile_system_prompt(ctx, compact=True)
        for leaked in _META_LEAKS:
            assert leaked not in sp.text, f"Fuga de metadato en compact: {leaked}"

    def test_compact_within_budget(self):
        ctx = _full_ctx()
        sp = compile_system_prompt(ctx, compact=True)
        assert sp.tokens <= COMPACT_TOKEN_BUDGET
        assert sp.within_budget is True


class TestFullMode:
    def test_full_includes_metadata_and_within_budget(self):
        ctx = _full_ctx()
        sp = compile_system_prompt(ctx, compact=False)
        # Modo full SÍ expone telemetría (registro/auditoría)...
        assert "79871" in sp.text
        # ...pero se mantiene dentro de su presupuesto.
        assert sp.tokens <= FULL_TOKEN_BUDGET
        assert sp.within_budget is True


class TestModuleLevelCompiler:
    def test_no_context_returns_empty(self):
        set_current_context(None)
        sp = compile_system_prompt(None, compact=True)
        assert sp.text == "" and sp.tokens == 0

    def test_uses_current_context(self):
        ctx = _full_ctx()
        set_current_context(ctx)
        try:
            sp = compile_system_prompt(compact=False)
            assert sp.text != "" and sp.tokens > 0
        finally:
            set_current_context(None)


class TestSystemPromptReport:
    def test_report_shape(self):
        ctx = _full_ctx()
        sp = compile_system_prompt(ctx, compact=True)
        r = sp.report()
        assert r["tokens"] == sp.tokens
        assert r["budget"] == sp.budget
        assert r["within_budget"] == sp.within_budget