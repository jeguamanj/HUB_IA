# API/utils/text_cleaner.py
# -*- coding: utf-8 -*-
"""
Sanitizador de texto común para descripciones y rúbricas provenientes del LMS.

Problemas que resuelve:
  - Caracteres de control (\\t, \\r, \\n dentro de celdas o entre líneas).
  - Espacios múltiples/seguidos generados por tabulaciones en plantillas.
  - Separadores estéticos (barras, guiones largos, etc.) repetidos.
  - Saltos de bloque innecesarios (múltiples \\n\\n seguidos).
  - Valores "basura" (p. ej. respuestas vacías, placeholders repetidos).

Funciona de forma determinista y sin dependencias externas (regex + unicode).
"""

import re
import unicodedata
from typing import Optional

# Caracteres de control no imprimibles (excepto \\n que puede interesar).
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Tabulaciones y espacios múltiples -> un solo espacio.
_MULTISPACE = re.compile(r"[ \t\u00a0]+")

# Múltiples saltos de línea -> máximo 1.
_MULTINEWLINE = re.compile(r"\n\s*\n")

# Renglones que son solo separadores estéticos (---, ===, ***, ...).
_SEPARATORS = re.compile(r"^\s*[-=*_#]{3,}\s*$", re.MULTILINE)

# Renglones que son solo un número/título de sección genérico (1., 2., a., b.)
_EMPTY_OR_PLACEHOLDER = re.compile(
    r"^\s*(N/A|n/a|Ninguno|ninguno|null|None|-\s*-|---)\s*$", re.IGNORECASE | re.MULTILINE
)

# Patrones que detectan valores "basura" / placeholders.
_PLACEHOLDERS = re.compile(
    r"^\s*(N/A|n/a|Ninguno|ninguno|null|None|-\s*-|---)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def strip_invisible_unicode(text: str) -> str:
    """Elimina caracteres invisibles Unicode (BOM, ZWSP, ZWNJ, etc.)."""
    if not text:
        return ""
    return "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")


def strip_control_chars(text: str) -> str:
    """Elimina caracteres de control (\\t, \\r, \\x00-\\x1f, \\x7f) excepto \\n."""
    if not text:
        return ""
    # Permitimos \\n (nueva línea) pero quitamos tabs, carriage returns, etc.
    return _CONTROL.sub("", text)


def remove_separator_lines(text: str) -> str:
    """Elimina líneas que son solo separadores estéticos (---, ===, ***, ***)."""
    if not text:
        return ""
    return _SEPARATORS.sub("", text)


def remove_placeholders(text: str) -> str:
    """Elimina líneas que son solo placeholders/valores nulos (N/A, null, ---, etc.)."""
    if not text:
        return ""
    return _PLACEHOLDERS.sub("", text)


def normalize_whitespace(text: str) -> str:
    """Normaliza espacios, tabs y saltos de línea a un formato limpio."""
    if not text:
        return ""
    # 1) Tabs y espacios múltiples -> un solo espacio
    out = _MULTISPACE.sub(" ", text)
    # 2) Saltos de línea múltiples -> uno solo
    out = _MULTINEWLINE.sub("\n", out)
    return out.strip()


def clean_text(
    text: Optional[str],
    *,
    collapse_lines: bool = True,
    limit_chars: Optional[int] = None,
) -> str:
    """
    Saneo completo de un texto (descripción o rúbrica) paso a paso.

    Args:
        text: texto bruto (p. ej. `assignment.description` o cell de rúbrica).
        collapse_lines: si True, une todo a una línea (recomendado para prompts).
        limit_chars: si se indica, recorta (por carácter) tras limpiar.

    Returns:
        str limpio y compacto.
    """
    if not text:
        return ""

    out = text

    # 1) Invisibles y control chars.
    out = strip_invisible_unicode(text)
    out = strip_control_chars(out)

    # 1) Quitar separadores estéticos (---, ===, etc.) ANTES de compactar.
    out = remove_separator_lines(out)

    # 2) Quitar placeholders/valores nulos (N/A, null, etc.) ANTES de compactar.
    out = remove_placeholders(out)

    # 3) Compactar líneas y espacios (últimos pasos).
    out = normalize_whitespace(out)

    # Recorte opcional por longitud.
    if limit_chars and len(out) > limit_chars:
        out = out[: limit_chars - 1].rstrip() + "…"

    return out


def clean_rubrica_line(
    text: Optional[str],
    *,
    collapse_tabs: bool = True,
    limit_chars: Optional[int] = None,
) -> str:
    """
    Limpieza específica para celdas/valores de una rúbrica (p. ej. `long_description`).

    A diferencia de `clean_text`, PRESERVA espacios/saltos internos para no perder
    el significado de celdas multilínea, pero elimina tabs y control enemiga.
    """
    if not text:
        return ""
    out = strip_invisible_unicode(text)
    out = strip_control_chars(out)
    if collapse_tabs:
        out = out.replace("\t", " ")
    out = re.sub(r"[ \t]+", " ", out).strip()

    if limit_chars and len(out) > limit_chars:
        out = out[: limit_chars - 1].rstrip() + "…"

    return out


__all__ = [
    "clean_text",
    "clean_rubrica_line",
    "strip_control_chars",
    "normalize_whitespace",
    "remove_separator_lines",
    "remove_placeholders",
    "strip_invisible_unicode",
]