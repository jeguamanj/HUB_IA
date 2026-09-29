# API/services/artemis_adapter.py

import os
import json
import asyncio
from typing import Optional, Dict, Any

try:
    from API.core.logging import get_logger
except ImportError:  # pragma: no cover
    from core.logging import get_logger

try:
    import stomp
    HAS_STOMP = True
except ImportError:
    HAS_STOMP = False

try:
    from API.services.essay_evaluator_mcp import (
        obtener_ensayos,
        evaluar_con_rubrica,
        guardar_reporte
    )
except ImportError:
    from services.essay_evaluator_mcp import (
        obtener_ensayos,
        evaluar_con_rubrica,
        guardar_reporte
    )

logger = get_logger("services.artemis_adapter")

# Configuración de variables de entorno para ActiveMQ Artemis
ARTEMIS_HOST = os.getenv("ARTEMIS_HOST", "localhost")
ARTEMIS_PORT = int(os.getenv("ARTEMIS_PORT", 61613))  # Puerto STOMP típico de Artemis
ARTEMIS_USER = os.getenv("ARTEMIS_USER", "artemis")
ARTEMIS_PASSWORD = os.getenv("ARTEMIS_PASSWORD", "artemis")
ARTEMIS_IN_QUEUE = os.getenv("ARTEMIS_IN_QUEUE", "/queue/lms.evaluaciones.in")
ARTEMIS_OUT_QUEUE = os.getenv("ARTEMIS_OUT_QUEUE", "/queue/lms.calificaciones.out")
ARTEMIS_SELECTOR_KEY = "tipo"
ARTEMIS_SELECTOR_VAL = "calificar ensayos IA"


class ArtemisEvaluationListener(stomp.ConnectionListener if HAS_STOMP else object):
    """
    Listener STOMP para ActiveMQ Artemis que captura peticiones etiquetadas
    como 'calificar ensayos IA', ejecuta el pipeline MCP y publica la respuesta.
    """

    def __init__(self, conn: Any, loop: asyncio.AbstractEventLoop):
        self.conn = conn
        self.loop = loop

    def on_error(self, frame):
        logger.error(f"[Artemis] Error en el broker: {frame.body}")

    def on_message(self, frame):
        headers = frame.headers or {}
        body = frame.body

        # Validar etiqueta/filtro de mensaje
        tipo_msg = headers.get(ARTEMIS_SELECTOR_KEY, "")
        if tipo_msg != ARTEMIS_SELECTOR_VAL and headers.get("type") != ARTEMIS_SELECTOR_VAL:
            logger.debug(f"[Artemis] Mensaje ignorado (header '{tipo_msg}' no coincide con '{ARTEMIS_SELECTOR_VAL}')")
            return

        logger.info(f"[Artemis] Mensaje recibido etiquetado '{ARTEMIS_SELECTOR_VAL}'. Procesando...")

        # Ejecutar el procesamiento asíncrono en el bucle de eventos
        asyncio.run_coroutine_threadsafe(
            self._procesar_y_responder(headers, body),
            self.loop
        )

    async def _procesar_y_responder(self, headers: Dict[str, Any], body: str):
        correlation_id = headers.get("correlation-id", "")
        reply_to = headers.get("reply-to", ARTEMIS_OUT_QUEUE)

        try:
            datos_peticion = json.loads(body)
            id_entrega = datos_peticion.get("id_entrega", "desconocido")
            id_lms = datos_peticion.get("id_lms") or datos_peticion.get("ID_LMS") or "CANVAS"
            course_id = datos_peticion.get("course_id") or datos_peticion.get("s.course_id") or datos_peticion.get("id_curso") or datos_peticion.get("ID_CURSO")
            user_id_canvas = datos_peticion.get("user_id_canvas") or datos_peticion.get("s.user_id_canvas") or datos_peticion.get("id_usuario") or datos_peticion.get("user_id") or datos_peticion.get("ID_USUARIO")
            assignment_id = datos_peticion.get("assignment_id") or datos_peticion.get("g.assignment_id") or datos_peticion.get("id_asignacion") or datos_peticion.get("id_actividad") or datos_peticion.get("id_tarea")
            submission_id = datos_peticion.get("submission_id") or datos_peticion.get("g.submission_id") or datos_peticion.get("id_submision") or datos_peticion.get("id_submission") or datos_peticion.get("ID_SUBMISION")
            submission_type = datos_peticion.get("submission_type") or datos_peticion.get("g.submission_type") or datos_peticion.get("tipo_envio") or datos_peticion.get("tipo_submision")
            
            datos_lms = {
                "id_lms": id_lms,
                "course_id": course_id,
                "user_id_canvas": user_id_canvas,
                "assignment_id": assignment_id,
                "submission_id": submission_id,
                "submission_type": submission_type,
                # Compatibilidad histórica
                "id_curso": course_id,
                "id_usuario": user_id_canvas,
                "id_submision": submission_id
            }

            origen_ensayo = datos_peticion.get("origen_ensayo", "")
            origen_rubrica = datos_peticion.get("origen_rubrica", "")
            proveedor_llm = datos_peticion.get("proveedor_llm", "deepseek")
            instrucciones = datos_peticion.get("instrucciones_adicionales", "")
            metadata_estudiante = datos_peticion.get("metadata_estudiante", {})

            # 1. Tool MCP: obtener_ensayos
            docs = await obtener_ensayos(origen_ensayo, origen_rubrica)
            if docs.get("status") != "success":
                raise Exception(f"Fallo al obtener documentos: {docs.get('mensaje')}")

            escala_sugerida = docs["rubrica"].get("metadatos", {}).get("escala_calculada")

            # 2. Tool MCP: evaluar_con_rubrica
            eval_res = await evaluar_con_rubrica(
                texto_ensayo=docs["ensayo"]["contenido_texto"],
                texto_rubrica=docs["rubrica"]["contenido_texto"],
                proveedor_llm=proveedor_llm,
                instrucciones_adicionales=instrucciones,
                escala_sugerida=escala_sugerida
            )

            datos_eval = eval_res.get("evaluacion", {})
            modelo_calificador = eval_res.get("modelo_utilizado") or proveedor_llm

            if not datos_eval.get("criterios"):
                logger.warning("Artemis: criterios vacíos en primera pasada. Reintentando con modelo...")
                eval_res = await evaluar_con_rubrica(
                    texto_ensayo=docs["ensayo"]["contenido_texto"],
                    texto_rubrica=docs["rubrica"]["contenido_texto"],
                    proveedor_llm="modelo",
                    instrucciones_adicionales=instrucciones,
                    escala_sugerida=escala_sugerida
                )
                datos_eval = eval_res.get("evaluacion", {})
                modelo_calificador = eval_res.get("modelo_utilizado") or "modelo"

            if user_id_canvas and datos_eval.get("estudiante") in ["Estudiante", None]:
                datos_eval["estudiante"] = user_id_canvas
            if submission_id:
                datos_eval["id_entrega"] = submission_id

            # 3. Tool MCP: guardar_reporte
            id_entrega_final = submission_id if (submission_id and id_entrega in ["desconocido", "entrega_001"]) else id_entrega
            reporte = await guardar_reporte(
                id_entrega=id_entrega_final,
                datos_evaluacion=datos_eval,
                canal_entrega="artemis",
                metadata_estudiante=metadata_estudiante,
                datos_lms=datos_lms,
                modelo_utilizado=modelo_calificador
            )

            # Publicar respuesta en la cola de salida de Artemis
            respuesta_lms = {
                "status": "success",
                "modelo_utilizado": modelo_calificador,
                "datos_lms": datos_lms,
                "id_lms": id_lms,
                "course_id": course_id,
                "user_id_canvas": user_id_canvas,
                "assignment_id": assignment_id,
                "submission_id": submission_id,
                "submission_type": submission_type,
                # Compatibilidad histórica
                "id_curso": course_id,
                "id_usuario": user_id_canvas,
                "id_submision": submission_id,
                "id_entrega": id_entrega_final,
                "correlation_id": correlation_id,
                "nota_final": datos_eval.get("nota_final"),
                "escala_maxima": datos_eval.get("escala_maxima"),
                "criterios": datos_eval.get("criterios"),
                "rubric_assessment_canvas": datos_eval.get("rubric_assessment_canvas"),
                "resumen_retroalimentacion": datos_eval.get("resumen_retroalimentacion"),
                "fortalezas": datos_eval.get("fortalezas"),
                "areas_mejora": datos_eval.get("areas_mejora"),
                "recomendaciones": datos_eval.get("recomendaciones"),
                "evaluacion": datos_eval,
                "archivos_locales": reporte.get("archivos_locales")
            }

            self.conn.send(
                destination=reply_to,
                body=json.dumps(respuesta_lms, ensure_ascii=False),
                headers={
                    "correlation-id": correlation_id,
                    "tipo": "resultado calificacion IA",
                    "content-type": "application/json"
                }
            )
            logger.info(f"[Artemis] Calificación enviada con éxito para id_entrega: {id_entrega_final} -> {reply_to}")

        except Exception as e:
            logger.error(f"[Artemis] Error procesando calificación: {str(e)}")
            error_payload = {
                "status": "error",
                "correlation_id": correlation_id,
                "error": str(e)
            }
            if self.conn and self.conn.is_connected():
                self.conn.send(
                    destination=reply_to,
                    body=json.dumps(error_payload),
                    headers={"correlation-id": correlation_id, "tipo": "error"}
                )


class ArtemisConsumerService:
    """Administra el ciclo de vida de la conexión STOMP con ActiveMQ Artemis."""

    def __init__(self):
        self.conn: Optional[Any] = None
        self.is_running = False

    def start(self):
        if not HAS_STOMP:
            logger.warning("[Artemis] Librería 'stomp.py' no instalada. Servicio de cola desactivado.")
            return

        enabled = os.getenv("ARTEMIS_ENABLED", "false").lower() in ["true", "1", "yes"]
        if not enabled:
            logger.info("[Artemis] ARTEMIS_ENABLED está deshabilitado en configuración.")
            return

        try:
            loop = asyncio.get_event_loop()
            self.conn = stomp.Connection([(ARTEMIS_HOST, ARTEMIS_PORT)])
            listener = ArtemisEvaluationListener(self.conn, loop)
            self.conn.set_listener("EvaluadorIA", listener)
            self.conn.connect(ARTEMIS_USER, ARTEMIS_PASSWORD, wait=True)

            # Suscribirse con selector SQL-92 (JMS / STOMP estándar)
            selector = f"{ARTEMIS_SELECTOR_KEY} = '{ARTEMIS_SELECTOR_VAL}'"
            self.conn.subscribe(
                destination=ARTEMIS_IN_QUEUE,
                id="sub_mcp_evaluador",
                ack="auto",
                headers={"selector": selector}
            )
            self.is_running = True
            logger.info(f"[Artemis] Conectado a {ARTEMIS_HOST}:{ARTEMIS_PORT} y suscrito a {ARTEMIS_IN_QUEUE} con selector: {selector}")
        except Exception as e:
            logger.error(f"[Artemis] No se pudo conectar al broker Artemis: {str(e)}")

    def stop(self):
        if self.conn and self.conn.is_connected():
            try:
                self.conn.disconnect()
                logger.info("[Artemis] Desconectado del broker Artemis.")
            except Exception as e:
                logger.error(f"[Artemis] Error al desconectar: {str(e)}")
        self.is_running = False


# Instancia singleton del servicio
artemis_service = ArtemisConsumerService()
