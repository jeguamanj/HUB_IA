# API/core/prompt_compiler.py
# -*- coding: utf-8 -*-
"""
Compilador de prompts del Contexto Institucional (B1.3).

Orquesta:
  - `token_estimator`  -> estima tokens (tiktoken/heurística).
  - `context_compressor` -> comprime el contexto al presupuesto.

Exposición pública:
  - `compile_context_prompt(context, ...)` -> str (prompt de sistema listo).
  - `estimate_context_prompt(context, ...)` -> TokenEstimate.
  - `ContextBudgetReport` -> metadatos del compilado (para auditoría).
"""

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from API.core.context_compressor import compress_context, serialize_context
from API.core.token_estimator import TokenEstimate, estimate_tokens


def _est(text: str, language: Optional[str] = None, model: str = "default") -> TokenEstimate:
    """Estimación interna determinista (heurística forzada) consistente."""
    return estimate_tokens(text, language=language, force_heuristic=True, model=model)

logger = logging.getLogger(__name__)

# Presupuestos por defecto (configurables por entorno).
DEFAULT_SYSTEM_TOKEN_BUDGET = 1024
DEFAULT_SYSTEM_SAFETY_MARGIN = 0.1  # 10% de margen de seguridad


@dataclass
class ContextBudgetReport:
    """
    Metadatos de la compilación de un prompt de contexto, útil para auditoría.
    """
    input_tokens: int
    output_tokens: int
    compressed: bool
    budget: int
    utilization: float
    method: str
    hash: str
    char_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "compressed": self.compressed,
            "budget": self.budget,
            "utilization": round(self.utilization, 3),
            "method": self.method,
            "hash": self.hash,
            "char_count": self.char_count,
        }


def hash_prompt(text: str) -> str:
    """Devuelve un hash sha256 del prompt (trazabilidad/versionado)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def compile_context_prompt(
    context: Dict[str, Any],
    budget: Optional[int] = None,
    language: Optional[str] = None,
    max_system_chars: Optional[int] = None,
) -> str:
    """
    Compone el system-prompt de contexto dentro del presupuesto de tokens.

    Args:
        context: dict del `InstitutionalContext` (model_dump con slots).
        budget:  presupuesto máximo de tokens (default DEFAULT_SYSTEM_TOKEN_BUDGET).
        language: hint de idioma para la heurística.
        max_system_chars: limitación de caracteres finales (opcional).

    Returns:
        str con el prompt de sistema optimizado.
    """
    if not isinstance(context, dict):
        context = {}
    budget = budget or DEFAULT_SYSTEM_TOKEN_BUDGET

    # 1) Comprimir el contexto al presupuesto.
    comprimido = compress_context(context, budget_tokens=budget, language=language)

    # 2) Construir bloque de texto usando el serializador CANÓNICO (el mismo con
    #    el que el compresor verificó el presupuesto -> consistente).
    prompt = serialize_context(comprimido)

    # 3) Aplicar límite de caracteres si se pidió.
    if max_system_chars and len(prompt) > max_system_chars:
        prompt = prompt[:max_system_chars]

    # 4) GARANTÍA FINAL del presupuesto: si el texto serializado aún excede,
    #    se recorta por caracteres de forma determinista (nunca se pasa).
    #    El sufijo de corte ("…") se INCLUYE en el conteo para no exceder.
    SUFIX = "…"
    if _est(prompt, language).estimated_tokens > budget:
        lo, hi = 0, len(prompt)
        while lo < hi:
            mid = (lo + hi) // 2
            trial = prompt[: mid + 1].rstrip() + SUFIX
            if _est(trial, language).estimated_tokens <= budget:
                lo = mid + 1
            else:
                hi = mid
        # `lo` es el mayor índice tal que prompt[:lo]+SUFIX cabe.
        recortado = prompt[:lo].rstrip() + SUFIX
        # Comprobación de seguridad final: si aún excede, quitar sufijo.
        if _est(recortado, language).estimated_tokens > budget:
            recortado = prompt[:lo-1].rstrip() if lo > 1 else ""
        prompt = recortado

    return prompt


def estimate_context_prompt(
    context: Dict[str, Any],
    budget: Optional[int] = None,
    language: Optional[str] = None,
    model: str = "default",
) -> TokenEstimate:
    """Estima tokens del prompt compilado (también comprime si hace falta)."""
    prompt = compile_context_prompt(context, budget=budget, language=language)
    return _est(prompt, language, model)


def build_budget_report(
    context: Dict[str, Any],
    budget: Optional[int] = None,
    language: Optional[str] = None,
) -> ContextBudgetReport:
    """
    Genera un reporte de presupuesto (para auditoría y logs) sin repetir trabajo.
    """
    budget = budget or DEFAULT_SYSTEM_TOKEN_BUDGET
    # Input (sin comprimir) y output (comprimido).
    texto_sin = serialize_context(context)
    input_est = _est(texto_sin, language)

    prompt_out = compile_context_prompt(context, budget=budget, language=language)
    output_est = _est(prompt_out, language)

    compressed = input_est.estimated_tokens > output_est.estimated_tokens
    utilization = output_est.estimated_tokens / budget if budget > 0 else 1.0

    return ContextBudgetReport(
        input_tokens=input_est.estimated_tokens,
        output_tokens=output_est.estimated_tokens,
        compressed=compressed,
        budget=budget,
        utilization=utilization,
        method=output_est.method,
        hash=hash_prompt(prompt_out),
        char_count=len(prompt_out),
    )


__all__ = [
    "compile_context_prompt",
    "estimate_context_prompt",
    "build_budget_report",
    "ContextBudgetReport",
    "hash_prompt",
    "DEFAULT_SYSTEM_TOKEN_BUDGET",
    "DEFAULT_SYSTEM_SAFETY_MARGIN",
]