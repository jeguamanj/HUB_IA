# API/core/token_estimator.py
# -*- coding: utf-8 -*-
"""
Estimador de tokens robusto para presupuestos de contexto LLM.

Soporta dos modos:
  1) tiktoken (preciso) si la librería está instalada y el modelo es compatible.
  2) heurística adaptativa por idioma (fallback sin dependencias externas).

Regla base: tokens ≈ caracteres / chars_por_token(idioma).
  - Inglés:   ~4.0 chars/token
  - Español:  ~3.3 chars/token (tildes, ñ, morfología rica)
  - etc.

Se incluye padding/overhead por mensaje (role + formateo interno) para respetar
la ventana de contexto de la API de chat.
"""

import logging
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# =============================================================================
# HEURÍSTICAS Y CONSTANTES
# =============================================================================

# Ratio caracteres/token por idioma (promedios empíricos ajustados).
CHARS_PER_TOKEN: Dict[str, float] = {
    "en": 4.0,      # Inglés
    "es": 3.3,      # Español
    "pt": 3.5,      # Portugués
    "fr": 3.7,      # Francés
    "de": 3.2,      # Alemán
    "zh": 1.5,      # Chino
    "ja": 1.8,      # Japonés
    "ko": 2.0,      # Coreano
    "default": 3.5, # Fallback conservador
}

# Tokens extra de formateo por mensaje (role + overhead interno de la API).
MESSAGE_OVERHEAD_TOKENS = 3
# Tokens extra por mensaje de sistema (formato interno / system prompt).
SYSTEM_MESSAGE_OVERHEAD = 4

# Mapeo de modelos a encodings de tiktoken (estándar OpenAI).
MODEL_ENCODING_MAP: Dict[str, str] = {
    "gpt-4": "cl100k_base",
    "gpt-4-32k": "cl100k_base",
    "gpt-4-turbo": "cl100k_base",
    "gpt-4o": "o200k_base",
    "gpt-4o-mini": "o200k_base",
    "gpt-5": "o200k_base",
    "gpt-3.5-turbo": "cl100k_base",
    "gpt-3.5-turbo-16k": "cl100k_base",
    "text-embedding-ada-002": "cl100k_base",
    # Fallback
    "default": "cl100k_base",
}

# Idiomas soportados por la heurística.
_SUPPORTED_LANGUAGES = set(CHARS_PER_TOKEN.keys())


# =============================================================================
# DATACLASS DE RESULTADO
# =============================================================================

@dataclass(frozen=True)
class TokenEstimate:
    """Resultado de una estimación de tokens."""
    estimated_tokens: int
    method: str                      # "tiktoken" | "heuristic" | "empty"
    char_count: int
    language: Optional[str] = "auto"
    model: Optional[str] = None
    confidence: float = 1.0          # 1.0 = tiktoken exacto; <1.0 = heurística

    @property
    def is_exact(self) -> bool:
        return self.method == "tiktoken"

    def to_dict(self) -> Dict:
        return {
            "estimated_tokens": self.estimated_tokens,
            "method": self.method,
            "char_count": self.char_count,
            "language": self.language,
            "model": self.model,
            "confidence": round(self.confidence, 2),
        }


# =============================================================================
# DETECCIÓN DE IDIOMA (heurística ligera)
# =============================================================================

def detect_language(text: str, hint: Optional[str] = None) -> str:
    """
    Detecta idioma de forma heurística rápida (sin dependencias pesadas).
    Prioriza el `hint` si se proporciona (p. ej. 'es', 'en').
    """
    if hint and str(hint).lower() in _SUPPORTED_LANGUAGES:
        return str(hint).lower()

    if not text:
        return "default"

    sample = text[:2000]
    non_ascii = sum(1 for c in sample if ord(c) > 127)
    ratio = non_ascii / max(len(sample), 1)

    if ratio > 0.3:
        # Presencia de diacríticos combinados (tildes combinables) -> romance
        if any("\u0300" <= c <= "\u036f" for c in sample):
            return "es"
        if any("\u3040" <= c <= "\u30ff" for c in sample):
            return "ja"          # Hiragana/Katakana
        if any("\uac00" <= c <= "\ud7af" for c in sample):
            return "ko"          # Hangul
        if any("\u4e00" <= c <= "\u9fff" for c in sample):
            return "zh"          # CJK
        return "es"              # Latino con diacríticos
    return "en"


def _chars_per_token_for(language: str) -> float:
    return CHARS_PER_TOKEN.get(language, CHARS_PER_TOKEN["default"])


# =============================================================================
# TIKTOKEN (PRECISO) CON CACHE
# =============================================================================

@lru_cache(maxsize=16)
def _get_tiktoken_encoding(model: str = "default"):
    """Devuelve el encoding de tiktoken para un modelo. Lanza ImportError si falta."""
    import tiktoken

    encoding_name = MODEL_ENCODING_MAP.get(str(model).lower(), MODEL_ENCODING_MAP["default"])
    try:
        return tiktoken.get_encoding(encoding_name)
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


# =============================================================================
# FUNCIONES DE ESTIMACIÓN
# =============================================================================

def estimate_tokens_heuristic(text: str, language: Optional[str] = None) -> TokenEstimate:
    """Estimación heurística rápida basada en ratio chars/token por idioma."""
    if not text:
        return TokenEstimate(0, "empty", 0, "empty", confidence=0.5)

    lang = language or detect_language(text)
    cpt = _chars_per_token_for(lang)

    # Overhead de formateo: ~1 token extra por cada 100 caracteres (aprox.)
    formatting_overhead = max(1, len(text) // 100)
    estimated = math.ceil(len(text) / cpt) + formatting_overhead

    return TokenEstimate(
        estimated_tokens=max(1, int(estimated)),
        method="heuristic",
        char_count=len(text),
        language=lang,
        confidence=0.7,
    )


def estimate_tokens_tiktoken(text: str, model: str = "default") -> TokenEstimate:
    """Estimación exacta con tiktoken. Requiere la librería instalada."""
    encoding = _get_tiktoken_encoding(model)
    token_count = len(encoding.encode(text))
    return TokenEstimate(
        estimated_tokens=token_count,
        method="tiktoken",
        char_count=len(text),
        language="auto",
        model=model,
        confidence=1.0,
    )


def estimate_tokens(
    text: str,
    model: str = "default",
    language: Optional[str] = None,
    force_heuristic: bool = False,
) -> TokenEstimate:
    """Estima tokens; intenta tiktoken y cae a heurística si no es posible."""
    if not text or not text.strip():
        return TokenEstimate(0, "empty", 0, "empty", confidence=1.0)

    if not force_heuristic:
        try:
            return estimate_tokens_tiktoken(text, model)
        except Exception:
            pass  # cae a heurística
    return estimate_tokens_heuristic(text, language)


def estimate_message_list_tokens(
    messages: List[Dict[str, str]],
    model: str = "default",
    language: Optional[str] = None,
) -> TokenEstimate:
    """
    Estima tokens para una lista de mensajes estilo chat (OpenAI).
    Incluye overhead por mensaje (role + formateo interno) y de sistema.
    """
    if not messages:
        return TokenEstimate(0, "empty", 0, "empty", confidence=1.0)

    total_content = " ".join(m.get("content", "") for m in messages if m.get("content"))
    lang = detect_language(total_content, language)
    # Base del contenido (heurística; tiktoken en una lista requiere concat reminders).
    base = estimate_tokens_heuristic(total_content, lang)

    # Overheads:
    #   - Cada mensaje suma ~3 tokens (role + separadores).
    #   - Un mensaje 'system' suma ~4 tokens extra (system prompt).
    overlay = len(messages) * MESSAGE_OVERHEAD_TOKENS
    system_overlay = SYSTEM_MESSAGE_OVERHEAD if any(m.get("role") == "system" for m in messages) else 0

    estimated = base.estimated_tokens + overlay + system_overlay

    return TokenEstimate(
        estimated_tokens=estimated,
        method="heuristic",
        char_count=len(total_content),
        language=lang,
        model=model,
        confidence=0.7,
    )


def estimate_prompt_tokens(
    system_prompt: str,
    user_prompt: str,
    model: str = "default",
    language: Optional[str] = None,
) -> TokenEstimate:
    """
    Estima tokens de un prompt compuesto (system + user) dinámico.
    Incluye overhead de mensaje de sistema.
    """
    system_est = estimate_tokens(system_prompt, model, language) if system_prompt else None
    user_est = estimate_tokens(user_prompt, model, language)

    system_tokens = system_est.estimated_tokens if system_est else 0
    system_overlay = SYSTEM_MESSAGE_OVERHEAD if system_prompt else 0
    total = system_tokens + user_est.estimated_tokens + MESSAGE_OVERHEAD_TOKENS + system_overlay

    return TokenEstimate(
        estimated_tokens=total,
        method=user_est.method,
        char_count=len((system_prompt or "") + user_prompt),
        language=language or "auto",
        confidence=min((system_est.confidence if system_est else 1.0), user_est.confidence),
    )


# =============================================================================
# PRESUPUESTO DE TOKENS
# =============================================================================

def check_token_budget(
    estimate: TokenEstimate,
    budget: int,
    safety_margin: float = 0.1,
) -> Tuple[bool, int, float]:
    """
    Valida si una estimación cabe en el presupuesto (con margen de seguridad).

    Returns:
        (fits: bool, remaining: int, utilization: float 0..1)
    """
    safety_limit = int(budget * (1 - safety_margin))
    fits = estimate.estimated_tokens <= safety_limit
    remaining = max(0, safety_limit - estimate.estimated_tokens)
    utilization = estimate.estimated_tokens / budget if budget > 0 else 1.0
    return fits, remaining, round(utilization, 3)


# =============================================================================
# EXPORTS
# =============================================================================
__all__ = [
    "TokenEstimate",
    "estimate_tokens",
    "estimate_tokens_heuristic",
    "estimate_tokens_tiktoken",
    "estimate_message_list_tokens",
    "estimate_prompt_tokens",
    "check_token_budget",
    "detect_language",
    "CHARS_PER_TOKEN",
    "MODEL_ENCODING_MAP",
    "MESSAGE_OVERHEAD_TOKENS",
    "SYSTEM_MESSAGE_OVERHEAD",
]