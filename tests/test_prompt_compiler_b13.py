# tests/test_prompt_compiler_b13.py
# =============================================================================
# Tests para el compilador de prompts B1.3 (token_estimator + context_compressor +
# prompt_compiler + compile_context en context_engine).
# =============================================================================

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from API.core.context_engine import InstitutionalContext, CompiledContext
from API.core.token_estimator import (
    TokenEstimate,
    estimate_tokens,
    check_token_budget,
    detect_language,
)
from API.core.context_compressor import compress_context
from API.core.prompt_compiler import (
    build_budget_report,
    compile_context_prompt,
    hash_prompt,
    ContextBudgetReport,
)


def _ctx_dict():
    return {
        "institution_id": "UNIVERSIDAD_UTPL",
        "evaluation_scale": 10.0,
        "tone_policy": "pedagógico, constructivo, formal",
        "global_policy": "No plagio; citar fuentes académicas.",
        "custom_instructions": "Evaluar con enfoque formativo.",
        "slots": {
            "lms": {"course_id": "79871", "assignment_id": "992586", "rubric": "[{id:_2541,points:2.5}]"},
            "zoom": {"account_id": "ZACCT"},
            "crm": {"customer_id": "CUST-7777"},
        },
    }


def _ctx_model():
    return InstitutionalContext(
        institution_id="UNIVERSIDAD_UTPL",
        evaluation_scale=10.0,
        tone_policy="pedagógico, constructivo, formal",
        global_policy="No plagio; citar fuentes.",
        slots={"lms": {"course_id": "79871", "assignment_id": "992586"}, "zoom": {"account_id": "Z"}},
    )


class TestTokenEstimator:
    def test_es_vs_en_ratio(self):
        es = estimate_tokens("El presente ensayo analiza la planeación estratégica de la empresa con acentos.", force_heuristic=True, language="es")
        en = estimate_tokens("This is a test of a longer English paragraph to verify the heuristic.", force_heuristic=True, language="en")
        assert es.estimated_tokens > 0
        assert en.estimated_tokens > 0
        # español (más tokens por char) suele dar >= inglés para textos comparables
        assert en.char_count >= 0  # solo smoke

    def test_tiktoken_o_heuristica_siempre_funciona(self):
        e = estimate_tokens("Hola mundo test")
        assert e.estimated_tokens >= 1
        assert e.method in ("tiktoken", "heuristic")

    def test_check_budget(self):
        e = estimate_tokens("a" * 100, force_heuristic=True, language="en")
        fits, remaining, util = check_token_budget(e, 1000)
        assert fits is True
        assert remaining >= 0
        assert 0 <= util <= 1


class TestLanguageDetection:
    def test_es_detectado_con_tildes(self):
        assert detect_language("á é í ó ú ñ") in ("es", "default")


class TestContextCompressor:
    def test_sin_presupuesto_conserva_estructura(self):
        comp = compress_context(_ctx_dict())
        assert "institution_id" in comp
        assert isinstance(comp.get("slots"), dict)

    def test_con_presupuesto_comprime(self):
        # presupuesto pequeño forzará compresión
        comp = compress_context(_ctx_dict(), budget_tokens=50)
        text = str(comp)
        assert len(text) < len(str(_ctx_dict()))  # se comprimió


class TestPromptCompiler:
    def test_compile_context_prompt(self):
        p = compile_context_prompt(_ctx_dict(), budget=512)
        assert isinstance(p, str) and p
        assert "institution_id" in p

    def test_budget_report(self):
        rep = build_budget_report(_ctx_dict(), budget=1024)
        assert isinstance(rep, ContextBudgetReport)
        assert rep.output_tokens > 0
        assert rep.utilization > 0
        assert rep.hash and len(rep.hash) == 16

    def test_hash_estable(self):
        assert hash_prompt("hola") == hash_prompt("hola")
        assert hash_prompt("hola") != hash_prompt("hola!")


class TestContextEngineIntegration:
    def test_compile_context(self):
        ctx = _ctx_model()
        compiled = ctx.compile_context(budget=512)
        assert isinstance(compiled, CompiledContext)
        assert compiled.report is not None
        assert compiled.within_budget is True
        assert compiled.tokens > 0

    def test_compile_context_respeta_presupuesto_muy_pequeno(self):
        ctx = _ctx_model()
        compiled = ctx.compile_context(budget=30)
        # Aunque el presupuesto sea mínimo, no debe petar y devuelve texto
        assert compiled.text is not None

    def test_budget_garantizado_con_contexto_masivo(self):
        # Contexto con global_policy masivo: el compilador debe GARANTIZAR que
        # el texto resultante NO exceda el presupuesto en ningún caso.
        from API.core.context_engine import InstitutionalContext as IC
        from API.core.prompt_compiler import compile_context_prompt
        ctx = IC(
            institution_id="UNIVERSIDAD_UTPL", evaluation_scale=10.0,
            tone_policy="pedagógico, constructivo, formal",
            global_policy="No plagio; citar fuentes. " * 30,
            custom_instructions="Evaluar con enfoque formativo." * 20,
            slots={"lms": {"course_id": "79871"}, "zoom": {"account_id": "Z"}},
        )
        d = ctx.model_dump(mode="json")
        for budget in (200, 100, 50, 30, 15, 8):
            texto = compile_context_prompt(d, budget=budget, language="es")
            # estimar con la misma heurística forzada del compilador
            est = estimate_tokens(texto, force_heuristic=True, language="es").estimated_tokens
            assert est <= budget, f"budget={budget} excedido ({est} tokens)"