# API/services/essay_evaluator_mcp.py

from API.llm_providers import openai
import os
import json
import re
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Union
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict, AliasChoices, field_validator, model_validator
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP
import httpx

try:
    from API.core.logging import get_logger
except ImportError:  # pragma: no cover
    from core.logging import get_logger

logger = get_logger("services.essay_evaluator_mcp")

# Asegurar que el directorio raíz esté en sys.path para ejecuciones directas
import sys
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from API.utils.document_parser import parse_document_input
    from API.llm_providers import deepseek, gemini, modelo, ollama
    from API.core.context_engine import (
        get_current_context,
        set_current_context,
        InstitutionalContext,
        context_store
    )
    from API.core.submission_tracker import get_tracker
    from API.core.evaluation_queue import get_manager, STATUS_PENDING, STATUS_EVALUATED
except ImportError:
    # pyrefly: ignore [missing-import]
    from utils.document_parser import parse_document_input  # type: ignore
    # pyrefly: ignore [missing-import]
    from llm_providers import deepseek, gemini, modelo, ollama  # type: ignore
    # pyrefly: ignore [missing-import]
    from core.context_engine import (  # type: ignore
        get_current_context,
        set_current_context,
        InstitutionalContext,
        context_store
    )
    # pyrefly: ignore [missing-import]
    from core.submission_tracker import get_tracker  # type: ignore
    # pyrefly: ignore [missing-import]
    from core.evaluation_queue import get_manager, STATUS_PENDING, STATUS_EVALUATED  # type: ignore

# Inicializar FastMCP Server
mcp = FastMCP("evaluador-ensayos-mcp")

# Router FastAPI para acceso REST complementario
router = APIRouter()

# Carpeta base para almacenamiento local de reportes
STORAGE_DIR = Path(__file__).resolve().parent.parent.parent / "storage" / "evaluaciones"


# ============================================================
# MODELOS PYDANTIC PARA VALIDACIÓN Y ESTRUCTURA (LMS Y RÚBRICA)
# ============================================================

class DatosLMS(BaseModel):
    """Estructura normalizada de datos de trazabilidad e integración con LMS."""
    id_lms: Optional[str] = Field("CANVAS", description="Identificador del sistema LMS (Canvas, Moodle, etc.)", validation_alias=AliasChoices("id_lms", "ID_LMS", "sistema_lms"))
    course_id: Optional[str] = Field(None, description="Identificador del curso LMS (s.course_id)", validation_alias=AliasChoices("course_id", "s.course_id", "id_curso", "ID_CURSO", "Id_curso"))
    user_id_canvas: Optional[str] = Field(None, description="Identificador del usuario en el LMS (s.user_id_canvas)", validation_alias=AliasChoices("user_id_canvas", "s.user_id_canvas", "id_usuario", "user_id", "ID_USUARIO", "Id_usuario"))
    assignment_id: Optional[str] = Field(None, description="Identificador de la tarea/actividad (g.assignment_id)", validation_alias=AliasChoices("assignment_id", "g.assignment_id", "id_asignacion", "id_actividad", "id_tarea", "ID_ACTIVIDAD"))
    assignment_name: Optional[str] = Field(None, description="Nombre o título de la actividad", validation_alias=AliasChoices("assignment_name", "titulo_actividad", "nombre_actividad"))
    submission_id: Optional[str] = Field(None, description="Identificador de la entrega (g.submission_id)", validation_alias=AliasChoices("submission_id", "g.submission_id", "id_submision", "id_submission", "ID_SUBMISION", "Id_submision"))
    submission_type: Optional[str] = Field(None, description="Tipo de envío realizado por el usuario (g.submission_type)", validation_alias=AliasChoices("submission_type", "g.submission_type", "tipo_envio", "tipo_submision", "TIPO_ENVIO"))

    model_config = ConfigDict(populate_by_name=True)


class NivelRatingRubrica(BaseModel):
    """Nivel de desempeño o rating individual de un criterio según la rúbrica."""
    id: Optional[str] = Field(None, description="Identificador del nivel en el LMS (ej. 'blank', '_2068')", validation_alias=AliasChoices("id", "id_nivel", "rating_id"))
    points: float = Field(..., description="Puntos asignados a este nivel de desempeño", validation_alias=AliasChoices("points", "puntos", "puntaje", "score"))
    description: str = Field(..., description="Nombre o descripción breve del nivel (ej. 'Cumple', 'No cumple')", validation_alias=AliasChoices("description", "descripcion", "nombre", "nivel"))
    long_description: Optional[str] = Field("", description="Descripción detallada del criterio en este nivel", validation_alias=AliasChoices("long_description", "descripcion_detallada"))

    model_config = ConfigDict(populate_by_name=True)


class CriterioRubrica(BaseModel):
    """Criterio de evaluación de la rúbrica."""
    id: Optional[str] = Field(None, description="Identificador único del criterio en el LMS (ej. '_830')", validation_alias=AliasChoices("id", "id_criterio", "criterion_id"))
    points: float = Field(..., description="Puntos máximos posibles para este criterio", validation_alias=AliasChoices("points", "puntos", "puntaje_maximo", "max_points"))
    description: str = Field(..., description="Descripción o nombre del criterio a evaluar", validation_alias=AliasChoices("description", "descripcion", "criterio", "titulo", "name"))
    long_description: Optional[str] = Field(None, description="Descripción extendida o guía pedagógica del criterio", validation_alias=AliasChoices("long_description", "descripcion_detallada"))
    ignore_for_scoring: Optional[bool] = Field(False, description="Si es True, este criterio no suma a la nota final", validation_alias=AliasChoices("ignore_for_scoring", "ignorar_para_calificacion"))
    criterion_use_range: Optional[bool] = Field(False, description="Si es True, admite rangos continuos de calificación", validation_alias=AliasChoices("criterion_use_range", "usa_rango"))
    ratings: List[NivelRatingRubrica] = Field(default_factory=list, description="Lista de niveles o escalas de desempeño", validation_alias=AliasChoices("ratings", "niveles", "escalas", "levels"))

    model_config = ConfigDict(populate_by_name=True)


class RubricaGenerica(BaseModel):
    """Modelo genérico y adaptable para rúbricas de Canvas LMS, Moodle, Blackboard u otros sistemas."""
    titulo: Optional[str] = Field("Rúbrica de Evaluación", description="Título de la rúbrica", validation_alias=AliasChoices("title", "titulo", "name"))
    points_possible: Optional[float] = Field(None, description="Puntaje total o escala máxima", validation_alias=AliasChoices("points_possible", "escala_maxima", "puntos_posibles"))
    criterios: List[CriterioRubrica] = Field(..., description="Lista de criterios que conforman la rúbrica", validation_alias=AliasChoices("criteria", "criterios"))

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def parsear(cls, raw: Any) -> Optional['RubricaGenerica']:
        """Parsea entrada que puede ser un string JSON, lista directa (Canvas) o diccionario."""
        if not raw:
            return None
        if isinstance(raw, cls):
            return raw
        try:
            if isinstance(raw, str):
                trimmed = raw.strip()
                if (trimmed.startswith("[") and trimmed.endswith("]")) or (trimmed.startswith("{") and trimmed.endswith("}")):
                    parsed_json = json.loads(trimmed)
                    return cls.parsear(parsed_json)
                return None
            elif isinstance(raw, list):
                # Formato puro de Canvas LMS: [ {id, points, description, ratings: [...]}, ... ]
                criterios_obj = [CriterioRubrica.model_validate(item) for item in raw]
                total = sum(c.points for c in criterios_obj if not c.ignore_for_scoring)
                return cls(titulo="Rúbrica Canvas LMS", points_possible=total, criterios=criterios_obj)
            elif isinstance(raw, dict):
                # Objeto contenedor {criteria: [...]} o {criterios: [...]}
                if "criterios" in raw or "criteria" in raw:
                    return cls.model_validate(raw)
                # O si viene un solo criterio directo en dict
                if "points" in raw and ("description" in raw or "descripcion" in raw):
                    c = CriterioRubrica.model_validate(raw)
                    return cls(titulo="Rúbrica de Criterio Único", points_possible=c.points, criterios=[c])
        except Exception:
            return None
        return None

    def calcular_escala_maxima(self) -> float:
        if self.points_possible is not None and self.points_possible > 0:
            return float(self.points_possible)
        total = sum(c.points for c in self.criterios if not c.ignore_for_scoring)
        return float(total) if total > 0 else 20.0

    def a_texto_evaluacion(self) -> str:
        lineas = [f"RÚBRICA DE EVALUACIÓN: {self.titulo} (Escala Máxima: {self.calcular_escala_maxima()} pts)\n"]
        for idx, crit in enumerate(self.criterios, 1):
            ignorado = " [FORMATIVO - NO SUMA A NOTA FINAL]" if crit.ignore_for_scoring else ""
            lineas.append(f"CRITERIO {idx}: {crit.description} (ID: {crit.id or idx} | Máximo: {crit.points} pts){ignorado}")
            # Solo incluir long_description si aporta información distinta a description para ahorrar tokens
            if crit.long_description and crit.long_description.strip() != crit.description.strip():
                lineas.append(f"  Detalle: {crit.long_description.strip()}")
            if crit.ratings:
                lineas.append("  Niveles de Desempeño:")
                for r in crit.ratings:
                    detalle = ""
                    if r.long_description and r.long_description.strip() != r.description.strip():
                        detalle = f" - {r.long_description.strip()}"
                    id_txt = f" (ID Nivel: {r.id})" if r.id else ""
                    lineas.append(f"    * [{r.points} pts] {r.description}{id_txt}{detalle}")
            lineas.append("")
        return "\n".join(lineas)


class CriterioEvaluado(BaseModel):
    # Complementos de la rúbrica del LMS:
    id_criterio: Optional[str] = Field(None, description="Identificador único del criterio en el LMS (ej. '_830')", validation_alias=AliasChoices("id_criterio", "criterion_id", "id"))
    id_nivel: Optional[str] = Field(None, description="Identificador único del nivel/rating obtenido (ej. 'blank', '_2068')", validation_alias=AliasChoices("id_nivel", "rating_id", "id_rating"))

    # Campos existentes preservados con alias exhaustivos:
    criterio: str = Field(..., description="Nombre del criterio de evaluación según la rúbrica", validation_alias=AliasChoices("criterio", "criterion", "name", "nombre", "titulo", "title", "description", "descripcion"))
    nivel_alcanzado: Optional[str] = Field("Alcanzado", description="Nivel obtenido (ej. Sobresaliente, Notable, Aprobado, Insuficiente, Cumple, No cumple)", validation_alias=AliasChoices("nivel_alcanzado", "nivel", "rating", "rating_description", "desempeno"))
    puntaje_obtenido: float = Field(..., description="Puntos asignados al estudiante en este criterio", validation_alias=AliasChoices("puntaje_obtenido", "puntos", "score", "points", "puntaje", "nota", "calificacion"))
    puntaje_maximo: float = Field(..., description="Puntaje máximo posible para este criterio", validation_alias=AliasChoices("puntaje_maximo", "puntos_maximos", "max_points", "points_possible", "max_score", "escala"))
    justificacion: Optional[str] = Field("", description="Explicación detallada del porqué de la calificación", validation_alias=AliasChoices("justificacion", "justification", "comentario", "comentarios", "comments", "feedback", "razon", "explicacion"))
    evidencias_en_texto: List[str] = Field(default_factory=list, description="Citas o fragmentos del ensayo que fundamentan la nota", validation_alias=AliasChoices("evidencias_en_texto", "evidencias", "citas", "quotes", "evidence", "citas_textuales"))

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("evidencias_en_texto", mode="before")
    @classmethod
    def normalizar_evidencias(cls, v: Any) -> List[str]:
        if v is None:
            return []
        if isinstance(v, str):
            v_str = v.strip()
            return [v_str] if v_str else []
        if isinstance(v, list):
            return [str(item).strip() for item in v if item]
        return []

    @field_validator("puntaje_obtenido", "puntaje_maximo", mode="before")
    @classmethod
    def parsear_numerico(cls, v: Any) -> float:
        if v is None or v == "":
            return 0.0
        try:
            return float(v)
        except (ValueError, TypeError):
            return 0.0

    @model_validator(mode='after')
    def normalizar_y_acotar_puntaje(self) -> 'CriterioEvaluado':
        if self.puntaje_obtenido < 0:
            self.puntaje_obtenido = 0.0
        if self.puntaje_maximo <= 0:
            self.puntaje_maximo = max(self.puntaje_obtenido, 1.0)
        # Acotar de forma amigable para evitar excepciones fatales:
        if self.puntaje_obtenido > self.puntaje_maximo:
            self.puntaje_obtenido = self.puntaje_maximo
        if not self.justificacion:
            self.justificacion = f"Nivel asignado: {self.nivel_alcanzado}. Puntaje obtenido: {self.puntaje_obtenido}/{self.puntaje_maximo}."
        return self


class EvaluacionResultado(BaseModel):
    id_entrega: Optional[str] = Field(None, description="Identificador único de la entrega o tarea")
    estudiante: Optional[str] = Field("Estudiante", description="Nombre o código del estudiante")
    titulo_ensayo: Optional[str] = Field("Ensayo Académico", description="Título del ensayo evaluado")
    nota_final: float = Field(..., description="Calificación cuantitativa final calculada", validation_alias=AliasChoices("nota_final", "nota", "calificacion", "final_score", "score"))
    escala_maxima: float = Field(20.0, description="Escala máxima de calificación (ej. 10.0, 20.0 o 100.0)", validation_alias=AliasChoices("escala_maxima", "max_points", "points_possible", "escala"))
    criterios: List[CriterioEvaluado] = Field(default_factory=list, description="Desglose por cada criterio de la rúbrica", validation_alias=AliasChoices("criterios", "criteria", "desglose"))
    fortalezas: List[str] = Field(default_factory=list, description="Principales fortalezas académicas demostradas", validation_alias=AliasChoices("fortalezas", "strengths", "puntos_fuertes", "aspectos_positivos"))
    areas_mejora: List[str] = Field(default_factory=list, description="Oportunidades concretas de mejora pedagógica", validation_alias=AliasChoices("areas_mejora", "debilidades", "oportunidades_mejora", "weaknesses", "aspectos_a_mejorar"))
    resumen_retroalimentacion: Union[str, List[str]] = Field(default="", description="Comentario global formativo para el estudiante", validation_alias=AliasChoices("resumen_retroalimentacion", "resumen", "feedback_global", "retroalimentacion", "summary", "comentario_general"))
    recomendaciones: Union[str, List[str]] = Field(default="", description="Sugerencias de lecturas o técnicas para futuros trabajos", validation_alias=AliasChoices("recomendaciones", "sugerencias", "recommendations", "consejos"))
    
    # Formato nativo para Canvas LMS API
    rubric_assessment_canvas: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="Objeto rubric_assessment formateado para Canvas LMS: {criterion_id: {points, rating_id, comments}}"
    )

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("fortalezas", "areas_mejora", mode="before")
    @classmethod
    def normalizar_listas(cls, v: Any) -> List[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [line.strip() for line in v.split("\n") if line.strip()]
        if isinstance(v, list):
            return [str(item).strip() for item in v if item]
        return []

    @field_validator("nota_final", "escala_maxima", mode="before")
    @classmethod
    def parsear_nota(cls, v: Any) -> float:
        if v is None or v == "":
            return 0.0
        try:
            return float(v)
        except (ValueError, TypeError):
            return 0.0

    @model_validator(mode='after')
    def consolidar_evaluacion(self) -> 'EvaluacionResultado':
        # 1. Recalcular nota_final y escala_maxima coherentemente a partir de los criterios
        if self.criterios:
            suma_obtenida = round(sum(c.puntaje_obtenido for c in self.criterios), 2)
            suma_maxima = round(sum(c.puntaje_maximo for c in self.criterios), 2)
            if suma_maxima > 0:
                self.escala_maxima = suma_maxima
            self.nota_final = min(suma_obtenida, self.escala_maxima)

        # 2. Auto-generar rubric_assessment_canvas si no viene dado
        if not self.rubric_assessment_canvas and self.criterios:
            assessment = {}
            for c in self.criterios:
                cid = c.id_criterio or c.criterio
                entry = {
                    "points": c.puntaje_obtenido,
                    "comments": c.justificacion or (f"Nivel: {c.nivel_alcanzado}")
                }
                if c.id_nivel:
                    entry["rating_id"] = c.id_nivel
                assessment[cid] = entry
            self.rubric_assessment_canvas = assessment

        if self.nota_final > self.escala_maxima:
            self.nota_final = self.escala_maxima
        if self.nota_final < 0:
            self.nota_final = 0.0

        # Garantizar que resumen y recomendaciones tengan contenido formativo mínimo si vinieron vacíos
        if not self.resumen_retroalimentacion and self.criterios:
            self.resumen_retroalimentacion = f"Evaluación completada con calificación final de {self.nota_final}/{self.escala_maxima} según los criterios establecidos en la rúbrica."
        if not self.recomendaciones and self.areas_mejora:
            self.recomendaciones = ["Continuar fortaleciendo las áreas identificadas como oportunidades de mejora."]

        return self


class SolicitudEvaluacionDirecta(BaseModel):
    origen_ensayo: Optional[str] = Field(None, description="Texto directo, ruta a archivo .pdf/.docx, URL o Base64")
    origen_rubrica: Optional[Union[str, List[Dict[str, Any]], Dict[str, Any]]] = Field(
        None, description="Texto directo, ruta a archivo .pdf/.docx, URL, Base64 o lista/JSON estructurado de la rúbrica (Canvas/LMS)"
    )
    id_entrega: str = Field("entrega_001", description="Identificador único de la entrega")
    proveedor_llm: str = Field("modelo", description="Proveedor LLM: 'modelo' (recomendado con fallback automático gratuito), 'deepseek', 'gemini', 'gemma', 'openai', 'ollama'")
    instrucciones_adicionales: str = Field("", description="Directrices pedagógicas adicionales")
    canal_entrega: str = Field("local", description="Canal: local, webhook, todos")
    webhook_lms_url: Optional[str] = Field(None, description="URL callback del LMS")
    # Control de crónica de envíos (FASE 3): si True (default), el envío se
    # registra/verifica contra el límite de intentos por tarea-estudiante.
    contar_intento: bool = Field(True, description="Si False, no se aplica límite ni se registra el intento (útil para re-evaluar/admin)")
    # Límite de envíos por tarea-estudiante, configurable por petición (LTI).
    # Si es None, se usa el valor global (`MAX_SUBMISSION_ATTEMPTS` del .env).
    max_intentos: Optional[int] = Field(None, description="Máximo de envíos evaluables para esta tarea (sobrescribe la config global). Dejar vacío para usar MAX_SUBMISSION_ATTEMPTS")

    # Bloques nativos para payloads que provienen directamente del Webhook de Canvas LMS
    assignment: Optional[Dict[str, Any]] = None
    submission: Optional[Dict[str, Any]] = None
    attachment: Optional[Dict[str, Any]] = None
    ai: Optional[Dict[str, Any]] = None
    success: Optional[bool] = None

    # Variables LMS actualizadas y ampliadas (soporta s.*, g.* y anteriores)
    id_lms: Optional[str] = Field("CANVAS", description="Identificador del sistema LMS (ej. Canvas, Moodle, Blackboard)", validation_alias=AliasChoices("id_lms", "ID_LMS", "sistema_lms", "Id_lms"))
    course_id: Optional[str] = Field(None, description="Identificador del curso LMS (s.course_id)", validation_alias=AliasChoices("course_id", "s.course_id", "id_curso", "ID_CURSO", "Id_curso"))
    user_id_canvas: Optional[str] = Field(None, description="Identificador del usuario en el LMS (s.user_id_canvas)", validation_alias=AliasChoices("user_id_canvas", "s.user_id_canvas", "id_usuario", "user_id", "ID_USUARIO", "Id_usuario"))
    assignment_id: Optional[str] = Field(None, description="Identificador de la tarea/actividad (g.assignment_id)", validation_alias=AliasChoices("assignment_id", "g.assignment_id", "id_asignacion", "id_actividad", "id_tarea", "ID_ACTIVIDAD"))
    assignment_name: Optional[str] = Field(None, description="Título o nombre de la actividad en el LMS", validation_alias=AliasChoices("assignment_name", "titulo_actividad", "nombre_actividad"))
    assignment_description: Optional[str] = Field(None, description="Descripción/consigna de la actividad (assignment.description del LMS)", validation_alias=AliasChoices("assignment_description", "descripcion_tarea", "consigna", "assignment.description"))
    submission_id: Optional[str] = Field(None, description="Identificador de la entrega (g.submission_id)", validation_alias=AliasChoices("submission_id", "g.submission_id", "id_submision", "id_submission", "ID_SUBMISION", "Id_submision"))
    submission_type: Optional[str] = Field(None, description="Tipo de envío realizado por el usuario (g.submission_type)", validation_alias=AliasChoices("submission_type", "g.submission_type", "tipo_envio", "tipo_submision", "TIPO_ENVIO"))

    # Objeto datos_lms opcional agrupado
    datos_lms: Optional[DatosLMS] = None

    # Compatibilidad con campos anteriores
    id_curso: Optional[str] = Field(None, description="Alias histórico de course_id", validation_alias=AliasChoices("id_curso", "ID_CURSO"))
    id_usuario: Optional[str] = Field(None, description="Alias histórico de user_id_canvas", validation_alias=AliasChoices("id_usuario", "ID_USUARIO"))
    id_submision: Optional[str] = Field(None, description="Alias histórico de submission_id", validation_alias=AliasChoices("id_submision", "ID_SUBMISION"))

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode='before')
    @classmethod
    def normalizar_payload_canvas(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        # Detectar si viene la estructura nativa del Webhook de Canvas LMS
        assignment_data = data.get("assignment")
        submission_data = data.get("submission")
        attachment_data = data.get("attachment")

        if assignment_data or submission_data or attachment_data:
            # 1. Rúbrica desde assignment.rubric si no vino explícita
            if not data.get("origen_rubrica") and isinstance(assignment_data, dict):
                data["origen_rubrica"] = assignment_data.get("rubric")

            # 2. Ensayo desde attachment (URL docx/pdf o txt plano) o submission (body/text)
            if not data.get("origen_ensayo"):
                # Caso 1: Archivo adjunto por URL descargable
                if isinstance(attachment_data, dict) and attachment_data.get("url"):
                    data["origen_ensayo"] = attachment_data.get("url")
                # Caso 2: Archivo con texto plano ya extraído en attachment.txt
                elif isinstance(attachment_data, dict) and attachment_data.get("txt"):
                    data["origen_ensayo"] = attachment_data.get("txt")
                # Caso 2 alternativo: Envío en línea (online_text_entry) en submission.body o submission.text
                elif isinstance(submission_data, dict) and submission_data.get("body"):
                    data["origen_ensayo"] = submission_data.get("body")
                elif isinstance(submission_data, dict) and submission_data.get("text"):
                    data["origen_ensayo"] = submission_data.get("text")

            # 3. Metadatos del LMS extraídos de assignment y submission (para trazabilidad interna, no para el LLM)
            if isinstance(assignment_data, dict):
                data.setdefault("course_id", str(assignment_data.get("courseId") or assignment_data.get("course_id") or ""))
                data.setdefault("assignment_id", str(assignment_data.get("id") or ""))
                if assignment_data.get("name") and not data.get("assignment_name"):
                    data["assignment_name"] = str(assignment_data.get("name"))
                # Descripción/consigna de la actividad: se guarda para trazabilidad
                # y se inyecta de forma controlada en el prompt (sin la logística
                # operativa redundante, ver _construir_prompt_evaluacion).
                if assignment_data.get("description") and not data.get("assignment_description"):
                    data["assignment_description"] = str(assignment_data.get("description"))

            if isinstance(submission_data, dict):
                data.setdefault("user_id_canvas", str(submission_data.get("userId") or submission_data.get("user_id") or ""))
                data.setdefault("submission_id", str(submission_data.get("id") or ""))
                data.setdefault("submission_type", submission_data.get("type") or submission_data.get("submission_type"))
                data.setdefault("id_entrega", str(submission_data.get("id") or "entrega_canvas"))

            if isinstance(attachment_data, dict) and attachment_data.get("name") and not data.get("id_entrega"):
                data["id_entrega"] = attachment_data.get("name")

            data.setdefault("id_lms", "CANVAS")

        # 4. Si origen_rubrica o metadatos no vinieron en el payload, recuperar del contexto institucional activo
        ctx = get_current_context()
        if ctx:
            lms_slot = ctx.get_slot("lms")
            if not data.get("origen_rubrica") and lms_slot.get("rubric"):
                data["origen_rubrica"] = lms_slot.get("rubric")
            if not data.get("course_id") and lms_slot.get("course_id"):
                data["course_id"] = lms_slot.get("course_id")
            if not data.get("assignment_id") and lms_slot.get("assignment_id"):
                data["assignment_id"] = lms_slot.get("assignment_id")
            if not data.get("assignment_name") and lms_slot.get("assignment_name"):
                data["assignment_name"] = lms_slot.get("assignment_name")

        # Validaciones de presencia mínima
        if not data.get("origen_ensayo"):
            raise ValueError(
                "No se encontró el contenido del ensayo. Debe proporcionar 'origen_ensayo' o 'attachment.url' / 'attachment.txt' / 'submission.body'."
            )
        if not data.get("origen_rubrica"):
            raise ValueError(
                "No se encontró la rúbrica de evaluación. Debe proporcionar 'origen_rubrica', 'assignment.rubric' o registrarla en el contexto institucional vía 'X-Context-ID'."
            )

        return data

    @model_validator(mode='after')
    def consolidar_campos_lms(self) -> 'SolicitudEvaluacionDirecta':
        if self.datos_lms:
            self.id_lms = self.id_lms or self.datos_lms.id_lms
            self.course_id = self.course_id or self.datos_lms.course_id
            self.user_id_canvas = self.user_id_canvas or self.datos_lms.user_id_canvas
            self.assignment_id = self.assignment_id or self.datos_lms.assignment_id
            self.assignment_name = self.assignment_name or self.datos_lms.assignment_name
            self.submission_id = self.submission_id or self.datos_lms.submission_id
            self.submission_type = self.submission_type or self.datos_lms.submission_type

        # Sincronizar alias históricos
        self.course_id = self.course_id or self.id_curso
        self.user_id_canvas = self.user_id_canvas or self.id_usuario
        self.submission_id = self.submission_id or self.id_submision

        # Mantener actualizados los campos de compatibilidad
        self.id_curso = self.course_id
        self.id_usuario = self.user_id_canvas
        self.id_submision = self.submission_id
        return self


# ============================================================
# HERRAMIENTA 1: OBTENER ENSAYOS Y RÚBRICAS (@mcp.tool)
# ============================================================

@mcp.tool()
async def obtener_ensayos(
    origen_ensayo: str,
    origen_rubrica: Union[str, List[Any], Dict[str, Any]],
    tipo_origen: str = "auto"
) -> Dict[str, Any]:
    """
    Herramienta MCP para leer y extraer texto estructurado de ensayos y rúbricas.
    Soporta múltiples orígenes:
    - Lista o diccionario estructurado de rúbrica (Canvas LMS puro o genérico)
    - URL de descarga (http:// o https://)
    - Archivos binarios en disco (PDF o Word .docx)
    - Binarios en Base64
    - Cadenas de texto plano directo (sin conversión)

    Args:
        origen_ensayo: URL, ruta local, Base64 o texto directo del ensayo.
        origen_rubrica: URL, ruta local, Base64, texto directo o lista/dict JSON de la rúbrica.
        tipo_origen: 'auto', 'url', 'file', 'base64' o 'text'.

    Returns:
        Diccionario con el contenido en texto y metadatos de ambos documentos.
    """
    ensayo_parsed = await parse_document_input(origen_ensayo, hint="ensayo")
    if ensayo_parsed.get("status") == "error":
        return {
            "status": "error",
            "mensaje": f"Error procesando ensayo: {ensayo_parsed.get('error')}"
        }

    # 1. Verificar si origen_rubrica es o contiene una rúbrica estructurada (Canvas / JSON / dict)
    rubrica_obj = RubricaGenerica.parsear(origen_rubrica)
    if rubrica_obj:
        rubrica_info = {
            "tipo_detectado": "lms_rubric_json",
            "origen_detectado": "structured_data",
            "metadatos": {
                "titulo": rubrica_obj.titulo,
                "escala_calculada": rubrica_obj.calcular_escala_maxima(),
                "num_criterios": len(rubrica_obj.criterios),
                "rubrica_estructurada": rubrica_obj.model_dump()
            },
            "contenido_texto": rubrica_obj.a_texto_evaluacion()
        }
    else:
        # Fallback a document_parser estándar si es archivo, url o texto directo
        if not isinstance(origen_rubrica, str):
            origen_rubrica_str = json.dumps(origen_rubrica, ensure_ascii=False)
        else:
            origen_rubrica_str = origen_rubrica

        rubrica_parsed = await parse_document_input(origen_rubrica_str, hint="rubrica")
        if rubrica_parsed.get("status") == "error":
            return {
                "status": "error",
                "mensaje": f"Error procesando rúbrica: {rubrica_parsed.get('error')}"
            }

        texto_rubrica = rubrica_parsed.get("text", "")
        # Comprobar si el texto extraído del archivo/string era un JSON de rúbrica
        rubrica_de_texto = RubricaGenerica.parsear(texto_rubrica)
        if rubrica_de_texto:
            rubrica_info = {
                "tipo_detectado": "lms_rubric_json",
                "origen_detectado": rubrica_parsed.get("source_type"),
                "metadatos": {
                    "titulo": rubrica_de_texto.titulo,
                    "escala_calculada": rubrica_de_texto.calcular_escala_maxima(),
                    "num_criterios": len(rubrica_de_texto.criterios),
                    "rubrica_estructurada": rubrica_de_texto.model_dump(),
                    "original_metadata": rubrica_parsed.get("metadata")
                },
                "contenido_texto": rubrica_de_texto.a_texto_evaluacion()
            }
        else:
            rubrica_info = {
                "tipo_detectado": rubrica_parsed.get("file_type"),
                "origen_detectado": rubrica_parsed.get("source_type"),
                "metadatos": rubrica_parsed.get("metadata"),
                "contenido_texto": texto_rubrica
            }

    return {
        "status": "success",
        "ensayo": {
            "tipo_detectado": ensayo_parsed.get("file_type"),
            "origen_detectado": ensayo_parsed.get("source_type"),
            "metadatos": ensayo_parsed.get("metadata"),
            "contenido_texto": ensayo_parsed.get("text")
        },
        "rubrica": rubrica_info
    }


# ============================================================
# HERRAMIENTA 2: EVALUAR CON RÚBRICA (@mcp.tool)
# ============================================================

def _extraer_consigna_evaluable(descripcion: str) -> str:
    """
    Depura la descripción/consigna de una actividad LMS para inyectar al LLM
    únicamente la parte evaluable, descartando logística operativa redundante
    (rutas de entrega, fechas, "unifique en Word", encabezados de plantilla).

    Estrategia:
      - Si la plantilla trae un bloque "Actividad de aprendizaje:", se captura
        dicho contenido y todo lo que le siga hasta encontrar una sección
        operativa conocida (entregas, estrategias, instrumento) o longitud máx.
      - Fallback: se concatenan los párrafos que no pertenecen a secciones
        operativas, recortando los primeros renglones genéricos ("Descripción
        de la actividad").
    """
    if not descripcion or not descripcion.strip():
        return ""

    import re

    lineas = [ln.strip() for ln in descripcion.splitlines() if ln.strip()]

    # Prefijos de sección OPERATIVA (no aportan criterio evaluativo).
    secciones_operativas = (
        "componente de aprendizaje",
        "estrategias de trabajo",
        "instrumento de evaluación",
        "tipo de recurso",
        "tema de la unidad",
        "unidad 3:",
        "unidad 4:",
        "resultados de aprendizaje",
        "envíe",
        "presente el documento",
        "unifique todo",
    )
    # Prefijos de cabecera de plantilla (antes de la actividad).
    cabecera_plantilla = (
        "descripción de la actividad",
        "descripcion de la actividad",
    )

    unidades: list[str] = []

    # 1) Modo estructurado: buscar el bloque sustantivo de la actividad.
    #    Se conservan las líneas desde 'Actividad de aprendizaje:' hasta que
    #    aparezca una sección operativa o se agote el texto.
    idx_inicio = next(
        (i for i, ln in enumerate(lineas) if ln.lower().startswith("actividad de aprendizaje")),
        None,
    )

    # Cabecera "Descripción de la actividad:" indeseada.
    def es_cabecera(ln: str) -> bool:
        return any(ln.lower().startswith(k) for k in cabecera_plantilla)

    if idx_inicio is not None:
        # Recoger desde la línea que explica (después de ':') en adelante.
        recogiendo = False
        for ln in lineas[idx_inicio + 1 :]:
            low = ln.lower()
            # Detenerse ante una sección operativa.
            if any(low.startswith(sec) for sec in secciones_operativas):
                break
            recogiendo = True
            if ln and not es_cabecera(ln):
                unidades.append(ln)
        # Si no hubo nada tras 'Actividad de aprendizaje:' intentar leer la
        # propia línea (p. ej. 'Actividad de aprendizaje: Analice...').
        if not unidades and ":" in lineas[idx_inicio]:
            trozo = lineas[idx_inicio].split(":", 1)[1].strip()
            if trozo:
                unidades.append(trozo)

    # 2) Fallback plano: quitar cabeceras y secciones operativas.
    if not unidades:
        for ln in lineas:
            low = ln.lower()
            if any(low.startswith(sec) for sec in secciones_operativas):
                continue
            if es_cabecera(ln) or ln.lower() == "rúbrica" or ln.lower() == "rubrica":
                continue
            unidades.append(ln)

    consigna = " ".join(unidades).strip()
    # Compactar espacios múltiples.
    consigna = re.sub(r"\s+", " ", consigna)
    return consigna[:1200]


def _construir_prompt_evaluacion(
    texto_ensayo: str,
    texto_rubrica: str,
    instrucciones: str = "",
    escala_maxima_sugerida: Optional[float] = None,
    descripcion_tarea: Optional[str] = None,
) -> str:
    ctx = get_current_context()
    context_block = ""
    if ctx:
        # Modo compacto: inyecta directiva institucional de alta densidad, excluyendo metadatos de LMS y decoradores
        context_block = f"\n{ctx.to_system_instruction(compact=True)}\n"
        if not escala_maxima_sugerida and ctx.evaluation_scale:
            escala_maxima_sugerida = ctx.evaluation_scale

    escala_ejemplo = escala_maxima_sugerida if escala_maxima_sugerida else 10.0
    nota_ejemplo = round(escala_ejemplo * 0.8, 1)

    # CONSIGNA / DESCRIPCIÓN DE LA TAREA: contexto de QUÉ se pedía al estudiante.
    # Se inyecta depurada (sin la logística operativa redundante) para que el
    # evaluador juzgue la pertinencia y completitud frente a lo solicitado.
    bloque_consigna = ""
    if descripcion_tarea and descripcion_tarea.strip():
        # Extraer solo la parte sustantiva: descripción/actividad/resultado.
        consigna_limpia = _extraer_consigna_evaluable(descripcion_tarea)
        if consigna_limpia:
            bloque_consigna = (
                f"\n--- DESCRIPCIÓN / CONSIGNA DE LA ACTIVIDAD (para juzgar pertinencia) ---\n"
                f"{consigna_limpia}\n"
                f"--- FIN CONSIGNA ---\n"
            )

    escala_info = (
        f"La escala máxima total calculada de la rúbrica es {escala_ejemplo} puntos."
        if escala_maxima_sugerida
        else "Calcula con precisión la escala máxima sumando los puntajes máximos de los criterios de la rúbrica."
    )

    instruccion_pedagogica = ""
    if instrucciones and instrucciones.strip():
        instruccion_pedagogica = f"6. Directriz pedagógica docente: {instrucciones.strip()}\n"
    else:
        instruccion_pedagogica = "6. La retroalimentación debe ser formativa, constructiva y orientada a mejorar la redacción académica del estudiante.\n"

    return f"""Eres un Evaluador Académico Experto y Riguroso. Tu misión es calificar el siguiente ensayo aplicando de forma objetiva, estricta y transparente la rúbrica de evaluación provista.{context_block}{bloque_consigna}

INSTRUCCIONES CLAVE OBLIGATORIAS:
1. Aplica EXCLUSIVAMENTE los criterios, descriptores y niveles establecidos en la rúbrica.
2. Si la rúbrica incluye identificadores (ID de criterio como '_830', '_7089' o ID de nivel/rating como 'blank', '_2068'), consérvalos exactamente en 'id_criterio' e 'id_nivel'. Si la rúbrica no especifica IDs, déjalos como null.
3. Para CADA criterio evaluado, debes incluir OBLIGATORIAMENTE:
   - 'justificacion': Explicación pedagógica detallada del por qué de la nota otorgada.
   - 'evidencias_en_texto': Lista con al menos una o dos citas textuales exactas del ensayo que respaldan la calificación.
4. Incluye SIEMPRE los campos formativos globales:
   - 'fortalezas': Lista de los principales puntos fuertes demostrados.
   - 'areas_mejora': Lista de aspectos específicos que requieren corrección o mayor desarrollo.
   - 'resumen_retroalimentacion': Comentario global formativo y constructivo de 2 o 3 párrafos.
   - 'recomendaciones': Lista o texto con recomendaciones prácticas para futuras entregas.
5. Calcula la nota final con precisión sumando los puntajes de los criterios individuales. {escala_info}
{instruccion_pedagogica}

IMPORTANTE: Responde ÚNICAMENTE con un objeto JSON válido (sin explicaciones previas ni bloques de texto fuera del JSON) con la siguiente estructura exacta:
{{
  "titulo_ensayo": "Título inferido del ensayo",
  "estudiante": "Nombre si figura en el ensayo o 'Estudiante'",
  "nota_final": {nota_ejemplo},
  "escala_maxima": {escala_ejemplo},
  "criterios": [
    {{
      "id_criterio": "_830",
      "criterio": "Nombre o descripción del criterio según la rúbrica",
      "id_nivel": "blank",
      "nivel_alcanzado": "Cumple",
      "puntaje_obtenido": {round(escala_ejemplo * 0.4, 1)},
      "puntaje_maximo": {round(escala_ejemplo * 0.5, 1)},
      "justificacion": "Explicación detallada justificando el desempeño y puntaje asignado...",
      "evidencias_en_texto": ["Cita textual exacta del ensayo que demuestra el nivel alcanzado."]
    }}
  ],
  "fortalezas": ["Identificación clara de conceptos clave."],
  "areas_mejora": ["Profundizar en la argumentación con evidencias empíricas."],
  "resumen_retroalimentacion": "El estudiante presenta un análisis ordenado...",
  "recomendaciones": ["Revisar literatura complementaria y estructurar una conclusión más sólida."]
}}

--- RÚBRICA DE EVALUACIÓN ---
{texto_rubrica}

--- ENSAYO A CALIFICAR ---
{texto_ensayo}
"""


@mcp.tool()
async def evaluar_con_rubrica(
    texto_ensayo: str,
    texto_rubrica: str,
    proveedor_llm: str = "deepseek",
    instrucciones_adicionales: str = "",
    escala_sugerida: Optional[float] = None,
    descripcion_tarea: Optional[str] = None
) -> Dict[str, Any]:
    """
    Herramienta MCP que envía el ensayo y la rúbrica al LLM para generar una calificación
    estructurada y detallada por criterios.

    Args:
        texto_ensayo: Texto completo del ensayo académico.
        texto_rubrica: Texto o tabla de la rúbrica con criterios y niveles.
        proveedor_llm: Proveedor a utilizar ('deepseek', 'gemini', 'openai', 'modelo').
        instrucciones_adicionales: Directrices pedagógicas opcionales del docente.
        escala_sugerida: Escala máxima total calculada de la rúbrica si aplica.
        descripcion_tarea: Consigna/descripción de la actividad LMS (assignment.description)
            para juzgar pertinencia frente a lo solicitado. Si es None, se recupera
            del slot 'lms' del contexto institucional activo.

    Returns:
        Diccionario con el desglose de notas, justificaciones y feedback en formato JSON estructurado.
    """
    if not texto_ensayo or not texto_ensayo.strip():
        return {"status": "error", "mensaje": "El texto del ensayo no puede estar vacío."}

    if not texto_rubrica or not texto_rubrica.strip():
        return {"status": "error", "mensaje": "La rúbrica no puede estar vacía."}

    # Recuperar consigna desde el contexto si no viene explícita.
    if not descripcion_tarea:
        ctx = get_current_context()
        if ctx:
            lms_slot = ctx.get_slot("lms")
            descripcion_tarea = lms_slot.get("assignment_description") or lms_slot.get("descripcion_tarea")

    prompt = _construir_prompt_evaluacion(
        texto_ensayo=texto_ensayo,
        texto_rubrica=texto_rubrica,
        instrucciones=instrucciones_adicionales,
        escala_maxima_sugerida=escala_sugerida,
        descripcion_tarea=descripcion_tarea
    )

    # Invocación al LLM
    raw_response_text = ""
    prov = (proveedor_llm or "modelo").strip().lower()
    if prov in ["modelo", "free", "openrouter-free"]:
        llm_result = await modelo.chat(prompt)
        if "error" in llm_result:
            return {"status": "error", "mensaje": f"Error del LLM (Modelo Free Fallback): {llm_result['error']}"}
        raw_response_text = llm_result.get("response", "") or llm_result.get("text", "")
    elif prov in ["gemini", "gemma"]:
        llm_result = await gemini.chat(prompt)
        if "error" in llm_result:
            if "429" in str(llm_result.get("error", "")):
                logger.warning("Evaluador Ensayos: Gemma saturado (429). Ejecutando fallback automático con modelo.py...")
                llm_result = await modelo.chat(prompt)
                if "error" in llm_result:
                    return {"status": "error", "mensaje": f"Error del LLM tras fallback: {llm_result['error']}"}
            else:
                return {"status": "error", "mensaje": f"Error del LLM (Gemma): {llm_result['error']}"}
        raw_response_text = llm_result.get("response", "") or llm_result.get("text", "")
    elif prov == "deepseek":
        llm_result = await deepseek.chat(prompt)
        if "error" in llm_result:
            return {"status": "error", "mensaje": f"Error del LLM: {llm_result['error']}"}
        raw_response_text = llm_result.get("response", "")
    elif prov == "openai":
        llm_result = await openai.chat(prompt)
        if "error" in llm_result:
            return {"status": "error", "mensaje": f"Error del LLM (OpenAI): {llm_result['error']}"}
        raw_response_text = llm_result.get("response", "") or llm_result.get("text", "")
    elif prov in ["ollama", "onpremise", "local"]:
        llm_result = await ollama.chat(prompt)
        if "error" in llm_result:
            return {"status": "error", "mensaje": f"Error del LLM (Ollama On-Premise): {llm_result['error']}"}
        raw_response_text = llm_result.get("response", "") or llm_result.get("text", "")
    else:
        llm_result = await modelo.chat(prompt)
        if "error" in llm_result:
            return {"status": "error", "mensaje": f"Error del LLM ({proveedor_llm}): {llm_result['error']}"}
        raw_response_text = llm_result.get("response", "") or llm_result.get("text", "")

    # Limpieza y parseo de JSON
    modelo_utilizado = llm_result.get("model_used") or llm_result.get("model") or prov
    evaluacion_dict = {}
    try:
        clean_text = raw_response_text.strip()
        # 1. Eliminar bloques de razonamiento <think> o <thought> de modelos como Nemotron o DeepSeek R1
        clean_text = re.sub(r'<think>.*?</think>', '', clean_text, flags=re.DOTALL)
        clean_text = re.sub(r'<thought>.*?</thought>', '', clean_text, flags=re.DOTALL)

        # 2. Extraer bloque de código JSON si existe
        if "```json" in clean_text:
            clean_text = clean_text.split("```json")[1].split("```")[0].strip()
        elif "```" in clean_text:
            clean_text = clean_text.split("```")[1].split("```")[0].strip()
        else:
            start_idx = clean_text.find("{")
            end_idx = clean_text.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                clean_text = clean_text[start_idx:end_idx+1]

        # 3. Intentar parsear JSON directamente o reparando comas finales
        try:
            evaluacion_dict = json.loads(clean_text)
        except json.JSONDecodeError:
            fixed_text = re.sub(r',\s*([\]}])', r'\1', clean_text)
            evaluacion_dict = json.loads(fixed_text)

        if escala_sugerida and "escala_maxima" not in evaluacion_dict:
            evaluacion_dict["escala_maxima"] = escala_sugerida

        # 4. Validación y consolidación con Pydantic
        resultado = EvaluacionResultado(**evaluacion_dict)
        return {
            "status": "success",
            "evaluacion": resultado.model_dump(),
            "modelo_utilizado": modelo_utilizado
        }
    except Exception as e:
        # Recuperación inteligente si evaluacion_dict tiene criterios
        if isinstance(evaluacion_dict, dict) and evaluacion_dict.get("criterios"):
            try:
                criterios_raw = evaluacion_dict.get("criterios", [])
                criterios_limpios = []
                for c in criterios_raw:
                    if isinstance(c, dict):
                        criterios_limpios.append(CriterioEvaluado(**c))
                if criterios_limpios:
                    res_repaired = EvaluacionResultado(
                        titulo_ensayo=evaluacion_dict.get("titulo_ensayo", "Ensayo Académico"),
                        estudiante=evaluacion_dict.get("estudiante", "Estudiante"),
                        nota_final=evaluacion_dict.get("nota_final", 0.0),
                        escala_maxima=evaluacion_dict.get("escala_maxima", escala_sugerida or 20.0),
                        criterios=criterios_limpios,
                        fortalezas=evaluacion_dict.get("fortalezas", []),
                        areas_mejora=evaluacion_dict.get("areas_mejora", []),
                        resumen_retroalimentacion=evaluacion_dict.get("resumen_retroalimentacion", ""),
                        recomendaciones=evaluacion_dict.get("recomendaciones", "")
                    )
                    return {
                        "status": "success",
                        "evaluacion": res_repaired.model_dump(),
                        "modelo_utilizado": modelo_utilizado
                    }
            except Exception:
                pass

        eval_fallback = evaluacion_dict if isinstance(evaluacion_dict, dict) and evaluacion_dict else {}
        return {
            "status": "partial_success",
            "advertencia": f"No se pudo estructurar el JSON estricto: {str(e)}",
            "evaluacion": eval_fallback,
            "respuesta_cruda": raw_response_text,
            "modelo_utilizado": modelo_utilizado
        }


# ============================================================
# HERRAMIENTA 3: GUARDAR REPORTE Y SINCRONIZAR LMS (@mcp.tool)
# ============================================================

def _generar_markdown_reporte(
    datos: Dict[str, Any],
    id_entrega: str,
    datos_lms: Optional[Dict[str, Any]] = None,
    modelo_utilizado: Optional[str] = None
) -> str:
    """Genera un reporte en Markdown estético y legible."""
    criterios = datos.get("criterios", [])
    criterios_rows = []
    for c in criterios:
        evidencias = "<br/>".join([f"- _{e}_" for e in c.get("evidencias_en_texto", [])])
        crit_nombre = c.get('criterio', '')
        if c.get('id_criterio'):
            crit_nombre = f"{crit_nombre} <br/><sub>ID: `{c.get('id_criterio')}`</sub>"
        nivel_nombre = c.get('nivel_alcanzado', '')
        if c.get('id_nivel'):
            nivel_nombre = f"{nivel_nombre} <sub>(`{c.get('id_nivel')}`)</sub>"
        row = f"| **{crit_nombre}** | {nivel_nombre} | {c.get('puntaje_obtenido')} / {c.get('puntaje_maximo')} | {c.get('justificacion')}<br/>{evidencias} |"
        criterios_rows.append(row)

    tabla_criterios = "\n".join(criterios_rows)

    fortalezas = "\n".join([f"- {f}" for f in datos.get("fortalezas", [])])
    mejoras = "\n".join([f"- {m}" for f in datos.get("areas_mejora", []) for m in ([f] if isinstance(f, str) else f)])

    datos_lms = datos_lms or {}
    lms_items = []
    if datos_lms.get("id_lms"):
        lms_items.append(f"**LMS**: {datos_lms['id_lms']}")
    course_id = datos_lms.get("course_id") or datos_lms.get("id_curso")
    if course_id:
        lms_items.append(f"**Curso**: {course_id}")
    assignment_id = datos_lms.get("assignment_id")
    assignment_name = datos_lms.get("assignment_name")
    if assignment_id:
        desc_actividad = f"{assignment_id} ({assignment_name})" if assignment_name else assignment_id
        lms_items.append(f"**Actividad**: {desc_actividad}")
    elif assignment_name:
        lms_items.append(f"**Actividad**: {assignment_name}")
    user_id = datos_lms.get("user_id_canvas") or datos_lms.get("id_usuario")
    if user_id:
        lms_items.append(f"**Usuario LMS**: {user_id}")
    sub_id = datos_lms.get("submission_id") or datos_lms.get("id_submision")
    if sub_id:
        lms_items.append(f"**ID Envío**: {sub_id}")
    sub_type = datos_lms.get("submission_type")
    if sub_type:
        lms_items.append(f"**Tipo Envío**: {sub_type}")

    lms_line = f"- {' | '.join(lms_items)}\n" if lms_items else ""
    modelo_line = f"- **Modelo de IA Evaluador**: `{modelo_utilizado}`\n" if modelo_utilizado else ""

    recs = datos.get('recomendaciones', '')
    if isinstance(recs, list):
        texto_recs = "\n".join([f"- {r}" for r in recs])
    else:
        texto_recs = str(recs) if recs else "Continuar profundizando en la argumentación."

    resumen = datos.get('resumen_retroalimentacion', '')
    if isinstance(resumen, list):
        texto_resumen = "\n\n".join([str(r) for r in resumen])
    else:
        texto_resumen = str(resumen) if resumen else "Sin comentarios generales."

    canvas_block = ""
    rubric_canvas = datos.get("rubric_assessment_canvas")
    if rubric_canvas:
        canvas_json_str = json.dumps(rubric_canvas, indent=2, ensure_ascii=False)
        canvas_block = f"""---

## 5. Mapeo para Sincronización LMS (Canvas API `rubric_assessment`)
```json
{canvas_json_str}
```
"""

    md = f"""# Informe de Evaluación de Ensayo Académico

- **ID de Entrega**: `{id_entrega}`
{lms_line}{modelo_line}- **Estudiante**: {datos.get('estudiante', 'Estudiante')}
- **Título del Ensayo**: {datos.get('titulo_ensayo', 'Ensayo Académico')}
- **Fecha de Calificación**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
- **Calificación Final**: **{datos.get('nota_final', 0)} / {datos.get('escala_maxima', 20.0)}**

---

## 1. Desglose de Calificación por Rúbrica

| Criterio | Nivel Alcanzado | Puntaje | Justificación y Evidencias |
| :--- | :--- | :---: | :--- |
{tabla_criterios}

---

## 2. Fortalezas Demostradas
{fortalezas if fortalezas else "No registradas."}

## 3. Oportunidades de Mejora
{mejoras if mejoras else "No registradas."}

---

## 4. Retroalimentación Pedagógica Global
{texto_resumen}

### Recomendaciones:
{texto_recs}
{canvas_block}"""
    return md


@mcp.tool()
async def guardar_reporte(
    id_entrega: str,
    datos_evaluacion: Dict[str, Any],
    canal_entrega: str = "local",
    webhook_lms_url: Optional[str] = None,
    metadata_estudiante: Optional[Dict[str, Any]] = None,
    datos_lms: Optional[Dict[str, Any]] = None,
    modelo_utilizado: Optional[str] = None
) -> Dict[str, Any]:
    """
    Herramienta MCP para persistir el reporte de evaluación en local (JSON y Markdown)
    y opcionalmente notificar al LMS peticionario (vía Webhook HTTP o cola).

    Args:
        id_entrega: Código o identificador único de la entrega.
        datos_evaluacion: Diccionario con la evaluación resultante de evaluar_con_rubrica.
        canal_entrega: 'local', 'webhook', 'artemis' o 'todos'.
        webhook_lms_url: URL de callback del LMS si canal_entrega incluye webhook.
        metadata_estudiante: Diccionario opcional con datos del estudiante.
        datos_lms: Diccionario con course_id, user_id_canvas, assignment_id, submission_id, submission_type, id_lms.
        modelo_utilizado: Nombre del modelo LLM que ejecutó la evaluación.

    Returns:
        Diccionario con las rutas de los archivos generados y el estado del envío.
    """
    if not id_entrega:
        id_entrega = f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # Sanitizar id_entrega para nombres de archivos
    safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '_', id_entrega)

    # 1. Almacenamiento Local
    target_folder = STORAGE_DIR / safe_id
    target_folder.mkdir(parents=True, exist_ok=True)

    json_path = target_folder / "reporte_evaluacion.json"
    md_path = target_folder / "reporte_evaluacion.md"

    payload_guardado = {
        "id_entrega": id_entrega,
        "fecha_evaluacion": datetime.utcnow().isoformat(),
        "modelo_utilizado": modelo_utilizado,
        "datos_lms": datos_lms or {},
        "metadata_estudiante": metadata_estudiante or {},
        "evaluacion": datos_evaluacion
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload_guardado, f, indent=2, ensure_ascii=False)

    md_content = _generar_markdown_reporte(datos_evaluacion, id_entrega, datos_lms, modelo_utilizado)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    resultado = {
        "status": "success",
        "id_entrega": id_entrega,
        "modelo_utilizado": modelo_utilizado,
        "datos_lms": datos_lms or {},
        "archivos_locales": {
            "json": str(json_path),
            "markdown": str(md_path)
        },
        "notificacion_lms": {"canal": canal_entrega, "enviado": False}
    }

    # 2. Despacho a LMS vía Webhook si corresponde
    url_destino = webhook_lms_url or os.getenv("LMS_WEBHOOK_URL", "")
    if canal_entrega in ["webhook", "todos"] and url_destino:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url_destino, json=payload_guardado)
                resultado["notificacion_lms"] = {
                    "canal": "webhook",
                    "url": url_destino,
                    "enviado": resp.status_code in [200, 201, 202, 204],
                    "status_code": resp.status_code
                }
        except Exception as e:
            resultado["notificacion_lms"] = {
                "canal": "webhook",
                "enviado": False,
                "error": f"Error al notificar webhook LMS: {str(e)}"
            }

    return resultado


# ============================================================
# HERRAMIENTA 4: CONSULTAR CATÁLOGO DE MODELOS FREE (@mcp.tool)
# ============================================================

@mcp.tool()
async def consultar_modelos_gratuitos_openrouter(forzar_refresco: bool = False) -> Dict[str, Any]:
    """
    Herramienta MCP para consultar en tiempo real el catálogo dinámico de modelos gratuitos de OpenRouter,
    con sus capacidades (context_length, reasoning, multimodal/visión) priorizados para su uso.
    """
    try:
        from API.llm_providers.openrouter_catalog import get_free_models_catalog
    except ImportError:
        # pyrefly: ignore [missing-import]
        from llm_providers.openrouter_catalog import get_free_models_catalog
    return await get_free_models_catalog(force_refresh=forzar_refresco)


# ============================================================
# ENDPOINTS REST FASTAPI COMPLEMENTARIOS
# ============================================================

def _consolidar_resultado(
    *,
    evaluation_id: str,
    submission_id: Optional[str],
    nota_final: Optional[float],
    escala_maxima: Optional[float],
    feedback: Optional[str],
    fortalezas: Optional[list],
    areas_mejora: Optional[list],
    recomendaciones: Optional[list],
    rubric_assessment_canvas: Optional[dict],
    datos_lms: Optional[dict],
    modelo: Optional[str],
    contexto_extra: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Construye la respuesta CANÓNICA y consolidada (sin duplicar bloques).

    Estructura única de salida del MCP de ensayos: pensada para:
      - Devolverse de forma mínima al LMS (calificación + feedback + rúbrica Canvas).
      - Evitar la repetición interna que confundía (criterios repetidos en
        raíz + ai.result + evaluacion).

    Nota: `rubric_assessment_canvas` se conserva porque es el formato NATIVO que
    Canvas/SpeedGrader espera vía `rubric_assessment`.
    """
    resultado: Dict[str, Any] = {
        "status": "EVALUADO",          # estado final ante el LMS/consulta
        "evaluation_id": evaluation_id,
        "submission_id": submission_id or "",
        "nota_final": nota_final,
        "escala_maxima": escala_maxima,
        "feedback": feedback,
        "fortalezas": fortalezas or [],
        "areas_mejora": areas_mejora or [],
        "recomendaciones": recomendaciones or [],
        "rubric_assessment_canvas": rubric_assessment_canvas or {},
        "modelo_utilizado": modelo,
        "datos_lms": datos_lms or {},
    }
    if contexto_extra:
        resultado.update(contexto_extra)
    return resultado


def _consolidar_desde_datos_eval(
    evaluacion: Dict[str, Any],
    *,
    evaluation_id: str,
    submission_id: Optional[str] = None,
    datos_lms: Optional[dict] = None,
    modelo: Optional[str] = None,
) -> Dict[str, Any]:
    """Consolida a partir del dict `evaluacion` (EvaluacionResultado.model_dump())."""
    return _consolidar_resultado(
        evaluation_id=evaluation_id,
        submission_id=submission_id or evaluacion.get("id_entrega") or evaluacion.get("submission_id"),
        nota_final=evaluacion.get("nota_final"),
        escala_maxima=evaluacion.get("escala_maxima"),
        feedback=evaluacion.get("resumen_retroalimentacion") or evaluacion.get("feedback"),
        fortalezas=evaluacion.get("fortalezas") or [],
        areas_mejora=evaluacion.get("areas_mejora") or [],
        recomendaciones=evaluacion.get("recomendaciones") or [],
        rubric_assessment_canvas=evaluacion.get("rubric_assessment_canvas") or {},
        datos_lms=datos_lms,
        modelo=modelo,
    )

@router.post("/evaluar-flujo-completo", tags=["Evaluador Ensayos"])
async def evaluar_flujo_completo(peticion: SolicitudEvaluacionDirecta):
    """
    Ejecuta el pipeline completo de las 3 herramientas en una sola llamada REST:
    1. obtener_ensayos -> 2. evaluar_con_rubrica -> 3. guardar_reporte
    Inyecta y sincroniza automáticamente el contexto institucional y los metadatos del LMS.
    """
    # Control de crónica de envíos (FASE 3): si `contar_intento` está activo se
    # verifica el límite por (course_id + assignment_id + user_id) antes de gastar
    # tokens, rechazando con 429 si el estudiante ya alcanzó el máximo permitido.
    if peticion.contar_intento:
        if not (peticion.course_id and peticion.assignment_id and peticion.user_id_canvas):
            logger.warning(
                "Envío sin trazabilidad completa (course/assignment/user). Se evalúa pero no se registra intento."
            )
        else:
            try:
                tracker = get_tracker()
                # Límite efectivo: el del payload (max_intentos) sobrescribe el global.
                limite_efectivo = peticion.max_intentos if peticion.max_intentos is not None else tracker.max_attempts
                permitido, motivo = await tracker.check_allowed(
                    user_id=peticion.user_id_canvas,
                    course_id=peticion.course_id,
                    assignment_id=peticion.assignment_id,
                    max_attempts=limite_efectivo,
                )
                if not permitido:
                    raise HTTPException(
                        status_code=429,
                        detail={
                            "status": "LÍMITE_DE_ENVÍOS",
                            "motivo": motivo,
                            "max_intentos": limite_efectivo,
                            "mensaje": (
                                f"El estudiante '{peticion.user_id_canvas}' ha alcanzado el máximo de "
                                f"envíos evaluables ({limite_efectivo}) para la actividad "
                                f"'{peticion.assignment_id}'."
                            ),
                        },
                    )
            except HTTPException:
                raise
            except Exception as exc:
                logger.warning("SubmissionTracker: no se pudo validar límite (%s); se continúa.", exc)

    # Paso 0: Sincronizar o Instanciar Contexto Institucional Activo
    # Garantiza que el curso, asignación, escala y directivas se inyecten en el System Prompt del LLM
    ctx = get_current_context()
    if not ctx:
        ctx = InstitutionalContext(
            system_source=peticion.id_lms or "CANVAS",
            user_id=peticion.user_id_canvas,
            user_role="ESTUDIANTE",
            slots={
                "lms": {
                    "course_id": peticion.course_id,
                    "assignment_id": peticion.assignment_id,
                    "assignment_name": peticion.assignment_name,
                    "assignment_description": peticion.assignment_description,
                    "submission_id": peticion.submission_id,
                    "submission_type": peticion.submission_type,
                    "rubric": peticion.origen_rubrica
                }
            }
        )
        context_store.save(ctx)
        set_current_context(ctx)
    else:
        lms_update = {}
        if peticion.course_id:
            lms_update["course_id"] = peticion.course_id
        if peticion.assignment_id:
            lms_update["assignment_id"] = peticion.assignment_id
        if peticion.assignment_name:
            lms_update["assignment_name"] = peticion.assignment_name
        if peticion.assignment_description:
            lms_update["assignment_description"] = peticion.assignment_description
        if peticion.submission_id:
            lms_update["submission_id"] = peticion.submission_id
        if peticion.submission_type:
            lms_update["submission_type"] = peticion.submission_type
        if peticion.origen_rubrica and not ctx.get_slot("lms").get("rubric"):
            lms_update["rubric"] = peticion.origen_rubrica
        if lms_update:
            ctx.set_slot("lms", lms_update)
            context_store.save(ctx)

    # Paso 1: Obtener documentos
    docs = await obtener_ensayos(peticion.origen_ensayo, peticion.origen_rubrica)
    if docs.get("status") != "success":
        raise HTTPException(status_code=400, detail=docs.get("mensaje", "Error leyendo documentos"))

    texto_ensayo = docs["ensayo"]["contenido_texto"]
    texto_rubrica = docs["rubrica"]["contenido_texto"]
    escala_sugerida = docs["rubrica"].get("metadatos", {}).get("escala_calculada")

    # Paso 2: Evaluar con rúbrica
    eval_res = await evaluar_con_rubrica(
        texto_ensayo=texto_ensayo,
        texto_rubrica=texto_rubrica,
        proveedor_llm=peticion.proveedor_llm,
        instrucciones_adicionales=peticion.instrucciones_adicionales,
        escala_sugerida=escala_sugerida,
        descripcion_tarea=peticion.assignment_description
    )

    if eval_res.get("status") not in ["success", "partial_success"]:
        raise HTTPException(status_code=500, detail=eval_res.get("mensaje", "Fallo en la evaluación"))

    datos_eval = eval_res.get("evaluacion", {})
    modelo_calificador = eval_res.get("modelo_utilizado") or peticion.proveedor_llm

    # Reintento de seguridad si la primera llamada no produjo criterios
    if not datos_eval.get("criterios"):
        logger.warning("Evaluador Ensayos: criterios vacíos en primera pasada. Reintentando con modelo.py...")
        eval_res = await evaluar_con_rubrica(
            texto_ensayo=texto_ensayo,
            texto_rubrica=texto_rubrica,
            proveedor_llm="modelo",
            instrucciones_adicionales=peticion.instrucciones_adicionales,
            escala_sugerida=escala_sugerida,
            descripcion_tarea=peticion.assignment_description
        )
        datos_eval = eval_res.get("evaluacion", {})
        modelo_calificador = eval_res.get("modelo_utilizado") or "modelo"

    if not datos_eval.get("criterios"):
        raise HTTPException(
            status_code=500,
            detail=f"La evaluación con IA no pudo estructurar los criterios de la rúbrica. {eval_res.get('advertencia', '')}"
        )

    # Mapeo consolidado de datos del LMS para trazabilidad (enriquecido con contexto institucional si existe)
    ctx = get_current_context()
    lms_slot = ctx.get_slot("lms") if ctx else {}

    course_id = peticion.course_id or peticion.id_curso or lms_slot.get("course_id")
    user_id_canvas = peticion.user_id_canvas or peticion.id_usuario or (ctx.user_id if ctx else None)
    assignment_id = peticion.assignment_id or lms_slot.get("assignment_id")
    assignment_name = peticion.assignment_name or lms_slot.get("assignment_name")
    submission_id = peticion.submission_id or peticion.id_submision or lms_slot.get("submission_id")
    submission_type = peticion.submission_type or lms_slot.get("submission_type")
    id_lms = peticion.id_lms or (ctx.system_source if ctx else "CANVAS")

    datos_lms = {
        "id_lms": id_lms,
        "course_id": course_id,
        "user_id_canvas": user_id_canvas,
        "assignment_id": assignment_id,
        "assignment_name": assignment_name,
        "submission_id": submission_id,
        "submission_type": submission_type,
        # Mantener compatibilidad histórica
        "id_curso": course_id,
        "id_usuario": user_id_canvas,
        "id_submision": submission_id
    }

    # Asignar usuario/submisión a la evaluación si no venían especificados
    if user_id_canvas and datos_eval.get("estudiante") in ["Estudiante", None]:
        datos_eval["estudiante"] = user_id_canvas
    if submission_id:
        datos_eval["id_entrega"] = submission_id
    if assignment_name and datos_eval.get("titulo_ensayo") in ["Ensayo Académico", None]:
        datos_eval["titulo_ensayo"] = assignment_name

    # Si se proporcionó submission_id y id_entrega tiene el valor por defecto, usar submission_id para la carpeta
    id_entrega_final = submission_id if (submission_id and peticion.id_entrega == "entrega_001") else peticion.id_entrega

    # Paso 3: Guardar y notificar
    reporte = await guardar_reporte(
        id_entrega=id_entrega_final,
        datos_evaluacion=datos_eval,
        canal_entrega=peticion.canal_entrega,
        webhook_lms_url=peticion.webhook_lms_url,
        datos_lms=datos_lms,
        modelo_utilizado=modelo_calificador
    )

    # Registro de crónica de envío (FASE 3): solo cuando contar_intento está
    # activo y hay trazabilidad completa. Almacena la evaluación para métricas.
    intento_registrado = False
    intento_info: Dict[str, Any] = {}
    if peticion.contar_intento and submission_id and user_id_canvas and course_id and assignment_id:
        try:
            tracker = get_tracker()
            reg_ok, intento_info, _motivo = await tracker.register(
                submission_id=submission_id,
                user_id=user_id_canvas,
                course_id=course_id,
                assignment_id=assignment_id,
                nota=datos_eval.get("nota_final"),
                escala_maxima=datos_eval.get("escala_maxima"),
                modelo=modelo_calificador,
            )
            intento_registrado = reg_ok
        except Exception as exc:
            logger.warning("SubmissionTracker: fallo al registrar evaluación (%s).", exc)

    return {
        # --- CANON (raíz): estructura única de salida para el LMS / consulta de estado ---
        # Mejorar con Opción C (adaptador por LMS) cuando se conecte otro LMS.
        "status": "EVALUADO",               # estado final (sync) / canon
        "pipeline": "completado",
        "evaluation_id": id_entrega_final,
        "submission_id": submission_id or "",
        "modelo_utilizado": modelo_calificador,
        # --- Calificación y feedback directo ---
        "nota_final": datos_eval.get("nota_final"),
        "escala_maxima": datos_eval.get("escala_maxima", (ctx.evaluation_scale if ctx and ctx.evaluation_scale else 10.0)),
        "feedback": datos_eval.get("resumen_retroalimentacion"),
        "fortalezas": datos_eval.get("fortalezas") or [],
        "areas_mejora": datos_eval.get("areas_mejora") or [],
        "recomendaciones": datos_eval.get("recomendaciones") or [],
        # --- Desglose por criterio (auditoría) ---
        "criterios": datos_eval.get("criterios"),
        # --- Formato nativo Canvas/SpeedGrader (lo que el LMS consume para escribir nota) ---
        "rubric_assessment_canvas": datos_eval.get("rubric_assessment_canvas") or {},
        "datos_lms": datos_lms,
        # --- Información de documentos procesados ---
        "documentos_info": {
            "ensayo_tipo": docs["ensayo"]["tipo_detectado"],
            "ensayo_palabras": docs["ensayo"]["metadatos"].get("word_count", 0),
            "rubrica_tipo": docs["rubrica"]["tipo_detectado"]
        },
        # --- Bloque 'ai' resumido (compatibilidad webhook Canvas) ---
        "ai": {
            "score": datos_eval.get("nota_final"),
            "feedback": datos_eval.get("resumen_retroalimentacion"),
            "status": "EVALUADO",
        },
        # --- Crónica de envío / métricas ---
        "intento": {
            "contado": peticion.contar_intento,
            "registrado": intento_registrado,
            "info": intento_info,
        },
        # --- Persistencia ---
        "reporte": reporte,
        # Deprecated: `evaluacion` se conserva por compatibilidad con clientes antiguos
        # que leían el desglose completo; código nuevo debe usar `criterios` raíz.
        "evaluacion": datos_eval,
    }


async def _evaluar_async_pipeline(payload_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluador para el worker de la cola asíncrona. Recibe el payload LTI (dict)
    y devuelve la respuesta CANÓNICA (el propio canon en la raíz de
    `evaluar_flujo_completo`), sin duplicados.
    """
    req = SolicitudEvaluacionDirecta.model_validate(payload_dict)
    resp = await evaluar_flujo_completo(req)
    if resp.get("status") != "EVALUADO":
        raise RuntimeError(resp.get("feedback") or "Evaluación fallida sin feedback")
    return resp


@router.post("/webhook-canvas", tags=["Evaluador Ensayos"])
async def webhook_canvas_entrypoint(peticion: SolicitudEvaluacionDirecta):
    """
    Punto de entrada dedicado para webhooks del LMS Canvas.
    Acepta nativamente el payload estándar de Canvas (assignment + submission + attachment) tanto para
    archivos descargables (.docx/.pdf) como para envíos de texto plano.
    """
    return await evaluar_flujo_completo(peticion)


# ============================================================
# MODO ASÍNCRONO (cola + consulta de estado)
# ------------------------------------------------------------
# El LMS encola con POST /ensayos/enqueue (202 inmediato) y consulta
# GET /ensayos/estado/{evaluation_id}. El worker procesa 1 a la vez.
# El evaluador del worker es `_evaluar_async_pipeline` (definido arriba).
# ============================================================

@router.post("/enqueue", tags=["Evaluador Ensayos"])
async def enqueue_evaluacion(peticion: SolicitudEvaluacionDirecta):
    """
    Encola una evaluación en el modo ASÍNCRONO.
    Responde 202 de inmediato con `evaluation_id` y estado PENDING; el LMS debe
    consultar `GET /ensayos/estado/{evaluation_id}` hasta que esté EVALUADO.
    No espera al LLM ni gasta la respuesta.
    """
    payload = peticion.model_dump(mode="json")
    manager = get_manager()
    try:
        info = await manager.enqueue(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"No se pudo encolar: {exc}")
    return {"status": "ENCOLADO", "estado": info.get("estado"), "evaluation_id": info.get("evaluation_id")}


@router.get("/estado/{evaluation_id}", tags=["Evaluador Ensayos"])
async def estado_evaluacion(evaluation_id: str):
    """
    Consulta el estado de una evaluación asíncrona.
      - PENDING / EVALUATING → aún no evaluado (el LMS puede reintentar).
      - EVALUATED → devuelve el resultado (nota, feedback, rubric_assessment_canvas).
      - ERROR → devuelve el motivo.
    """
    manager = get_manager()
    code, body = await manager.get_status(evaluation_id)
    return JSONResponse(status_code=code, content=body)


def get_router() -> APIRouter:
    return router


def init_async_evaluator() -> None:
    """
    Registra el evaluador asíncrono (worker de cola) sobre el manager singleton.
    Se invoca desde el lifespan de `server.py` tras inicializar la cola.
    El worker procesará las evaluaciones con `_evaluar_async_pipeline`.
    """
    from API.core.evaluation_queue import get_manager

    get_manager()._evaluator = _evaluar_async_pipeline


# ============================================================
# RUN STANDALONE MCP SERVER
# ============================================================

if __name__ == "__main__":
    import sys
    transport = "stdio"
    if "--http" in sys.argv:
        transport = "streamable-http"
    logger.info("Iniciando Servidor MCP de Evaluación de Ensayos (transporte: %s)...", transport)
    mcp.run(transport=transport)
