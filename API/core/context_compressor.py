# API/core/context_compressor.py
# -*- coding: utf-8 -*-
"""
Compresor semántico de contexto institucional con presupuesto de tokens.

Estrategia:
  - Construye un prompt candidato a partir de `InstitutionalContext`.
  - Estima tokens (usando `token_estimator`).
  - Si excede el presupuesto, comprime de forma inteligente:
      1) Prioriza campos críticos (directiva, escala, tono, política).
      2) Recorta/trunca bloques de slots (lms, zoom, crm) preservando IDs/entidades.
      3) Aplica compact=True como último recurso.

Este módulo NO depende de bibliotecas externas pesadas (solo `token_estimator`).
"""

import logging
import re
from typing import Any, Dict, Optional

from API.core.token_estimator import (
    TokenEstimate,
    check_token_budget,
    estimate_tokens,
    detect_language,
)


def _est(text: str, language: Optional[str] = None) -> TokenEstimate:
    """
    Estimación interna determinista (heurística forzada) para el presupuesto.
    No depende de si tiktoken está instalado; así el resultado final coincide
    con el recorte (consistente) en cualquier entorno.
    """
    return estimate_tokens(text, language=language, force_heuristic=True)

logger = logging.getLogger(__name__)

# Bloques de slots con prioridad alta (se conservan primero).
_SLOT_PRIORITY = ["lms", "zoom", "crm", "custom"]

# Claves de "identidad crítica" dentro de un contexto.
_CRITICAL_KEYS = [
    "institution_id",
    "evaluation_scale",
    "tone_policy",
    "global_policy",
    "custom_instructions",
]

# Entidades que suelen ser importantes para preservar en un recorte.
_ID_PATTERN = re.compile(r"([A-Za-z0-9_\-]{4,})")


def _pick_top_level(
    context: Dict[str, Any],
    keys: Optional[list] = None,
) -> Dict[str, Any]:
    """Selecciona un subdiccionario con las claves indicadas (o todas si keys=None)."""
    src = {k: v for k, v in context.items() if not isinstance(v, dict)}
    if keys is None:
        return src
    return {k: src.get(k) for k in keys if k in src}


def _serialize_slot(slot_name: str, data: Any) -> str:
    """Serializa un slot de forma compacta (clave:valor)."""
    if not data:
        return ""
    if isinstance(data, str):
        return slot_name + ":" + data
    if isinstance(data, dict):
        parts = []
        for k, v in data.items():
            parts.append(f"{k}={v}")
        return slot_name + "{" + "; ".join(parts) + "}"
    return slot_name + ":" + str(data)


def _recortar_texto(text: str, max_chars: int) -> str:
    """Recorta un texto de forma limpia respetando límites de caracteres."""
    if not text or len(text) <= max_chars:
        return text or ""
    return text[:max_chars].rstrip() + "…"


def _extractar_entidades_criticas(text: str, max_entities: int = 40) -> str:
    """Extrae entidades/tokens importantes de un texto para un resumen compacto."""
    if not text:
        return ""
    # Tomamos las palabras candidatas a identificadores (mayor longitud/media).
    tokens = _ID_PATTERN.findall(text)
    # Filtrar tokens demasiado genéricos (stopwords en español/inglés).
    stop = {
        "este", "esta", "para", "con", "por", "los", "las", "una", "uno", "que",
        "the", "and", "with", "from", "para", "como", "del", "sobre", "que",
        "entre", "hacer", "donde", "pero", "más", "mas", "sus", "cada", "otros",
    }
    filtrados = [t for t in tokens if t.lower() not in stop and len(t) >= 4]
    unicos = list(dict.fromkeys(filtrados))[:max_entities]
    return " ".join(unicos)


def compress_context(
    context: Dict[str, Any],
    budget_tokens: Optional[int] = None,
    language: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Devuelve un contexto comprimido que cabe en `budget_tokens` si es posible.

    Args:
        context: dict serializado del `InstitutionalContext` (model_dump).
        budget_tokens: presupuesto máximo de tokens del prompt de sistema.
        language: hint de idioma para la heurística de tokens.

    Returns:
        dict con el contexto recortado/comprimido. Si budget es None, devuelve
        el original (salvo compact ético básico: quita slots vacíos).
    """
    if not isinstance(context, dict):
        return context

    # 1) Parte alta (crítica) siempre presente.
    critical = _pick_top_level(context, _CRITICAL_KEYS)

    # 2) Serializar slots con prioridad.
    slots = context.get("slots", {}) if isinstance(context.get("slots"), dict) else {}
    slot_blocks: Dict[str, str] = {}
    for sname in _SLOT_PRIORITY:
        if sname in slots and slots.get(sname):
            slot_blocks[sname] = _serialize_slot(sname, slots[sname])

    # 3) Sin presupuesto: devolver crítica + slots no vacíos.
    if budget_tokens is None:
        out = dict(context)
        out.setdefault("slots", {})
        # quitar slots vacíos
        out["slots"] = {k: v for k, v in slots.items() if v}
        return out

    # 4) Con presupuesto: montar candidato y comprimir de forma ITERATIVA.
    #    Se añaden elementos en orden de prioridad; cada uno completo mientras
    #    quepa, y truncado (recortado) si es demasiado grande.
    texto_resultado = _serialize_candidato(dict(critical))
    est = _est(texto_resultado, language)
    fits, _, _ = check_token_budget(est, budget_tokens)

    if fits:
        # La crítica completa ya cabe (ignora slots extra si no caben).
        return dict(critical)

    # 5) No cabe la crítica completa: vamos a emitir la crítica TRUNCADA y los
    #    slots en orden de prioridad, cada uno truncado a su presupuesto.
    #    Prioridad absoluta: institutional/identidad primero.
    comprimido: Dict[str, Any] = {}

    # 5a) Campos críticos de máxima prioridad (identidad y escala).
    for key in ["institution_id", "evaluation_scale", "tone_policy"]:
        val = critical.get(key)
        if val is None:
            continue
        comprimido[key] = val

    # 5b) Campos opcionales (global_policy, custom_instructions) SOLO si caben
    #     de forma razonable; si no, se omiten (no se truncan mal).
    for key in ["global_policy", "custom_instructions"]:
        val = critical.get(key)
        if val is None:
            continue
        candidato_tmp = dict(comprimido)
        candidato_tmp[key] = val
        if _est(_serialize_candidato(candidato_tmp), language).estimated_tokens <= int(budget_tokens * 1.0):
            comprimido[key] = val

    # 5c) Slots en orden de prioridad, cada uno truncado agresivamente.
    comprimido.setdefault("slots", {})
    max_chars_slot = max(24, int(budget_tokens * 3.0))
    for sname in _SLOT_PRIORITY:
        block = slot_blocks.get(sname)
        if not block:
            continue
        comprimido["slots"][sname] = _recortar_texto(block, max_chars_slot)
        tmp_est = _est(_serialize_candidato(comprimido), language)
        if tmp_est.estimated_tokens > budget_tokens:
            del comprimido["slots"][sname]

    # 6) GARANTÍA FINAL (mejor esfuerzo): si aún excede, se recorta por caracteres.
    #    El recorte determinista final se aplica en `prompt_compiler.compile_context_prompt`
    #    sobre el texto serializado, con el MISMO `estimate_tokens` de reporte.
    return comprimido


def serialize_context(d: Dict[str, Any]) -> str:
    """Serializa un dict de contexto a un bloque de texto plano (k=v).
    Es el serializador CANÓNICO usado para estimar y para el resultado final,
    de modo que el presupuesto verificado coincide con el texto entregado."""
    # Caso especial: compresión final recortada -> devolver la cadena directa.
    if isinstance(d, dict) and "__recortado__" in d:
        return d["__recortado__"]
    return _serialize_candidato(d)


def _serialize_candidato(d: Dict[str, Any]) -> str:
    """Serializa un dict candidato para estimar/escribir (clave=valor)."""
    parts = []
    for k, v in d.items():
        if k == "slots":
            for sname, sval in (v or {}).items():
                parts.append(_serialize_slot(sname, sval))
        else:
            parts.append(f"{k}={v}")
    return " | ".join(parts)


__all__ = ["compress_context", "serialize_context", "extractar_entidades_criticas"]