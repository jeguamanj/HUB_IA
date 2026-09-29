from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import requests
import os
import base64
import pandas as pd
import urllib.parse
from dotenv import load_dotenv
from datetime import datetime
from typing import List, Dict, Optional
from openpyxl.styles import Alignment

# Cargar variables de entorno
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv()

router = APIRouter()

# Credenciales
ZOOM_ACCOUNT_ID = os.getenv("ZOOM_ACCOUNT_ID")
ZOOM_CLIENT_ID = os.getenv("ZOOM_CLIENT_ID")
ZOOM_CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET")
ZOOM_HEADER_IMAGE = os.getenv("ZOOM_HEADER_IMAGE")  # Ruta opcional del banner para encabezados


class ZoomReportRequest(BaseModel):
    meeting_id: str


class RecurringSessionInfo(BaseModel):
    session_id: str
    start_time: str
    end_time: str
    duration: int
    participants_count: int
    registrants_count: int
    status: str


def generate_zoom_token():
    """Genera un token válido de Zoom"""
    credentials = f"{ZOOM_CLIENT_ID}:{ZOOM_CLIENT_SECRET}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    url = f"https://zoom.us/oauth/token?grant_type=account_credentials&account_id={ZOOM_ACCOUNT_ID}"
    headers = {"Authorization": f"Basic {encoded_credentials}"}
    response = requests.post(url, headers=headers)
    if response.status_code == 200:
        return response.json().get("access_token")
    raise HTTPException(status_code=response.status_code, detail=response.text)


def format_meeting_id(meeting_id: str) -> str:
    """Formatea el meeting ID para los diferentes endpoints de Zoom"""
    if '%' in meeting_id:
        meeting_id = urllib.parse.unquote(meeting_id)
    return meeting_id


def detect_session_type(meeting_id: str, headers: dict) -> str:
    """Detecta si el ID corresponde a una reunión (meeting) o webinar"""
    formatted_id = format_meeting_id(meeting_id)
    # Intentar primero como meeting
    try:
        url_meeting = f"https://api.zoom.us/v2/meetings/{formatted_id}"
        response_meeting = requests.get(url_meeting, headers=headers)
        if response_meeting.status_code == 200:
            print(f"✅ Detectado como MEETING: {formatted_id}")
            return "meeting"
    except Exception as e:
        print(f"❌ Error al verificar meeting: {e}")

    # Intentar como webinar
    try:
        url_webinar = f"https://api.zoom.us/v2/webinars/{formatted_id}"
        response_webinar = requests.get(url_webinar, headers=headers)
        if response_webinar.status_code == 200:
            print(f"✅ Detectado como WEBINAR: {formatted_id}")
            return "webinar"
    except Exception as e:
        print(f"❌ Error al verificar webinar: {e}")

    # Si no se puede detectar, asumir meeting por defecto
    print(f"⚠️ No se pudo detectar el tipo de sesión, asumiendo MEETING: {formatted_id}")
    return "meeting"


def is_recurring_meeting(session_data: dict) -> bool:
    """
    Determina si una reunión es recurrente usando múltiples indicadores.
    Más robusto que solo verificar recurrence.type.
    """
    recurrence = session_data.get("recurrence", {})

    # 1. Tipo de recurrencia válido (1-8)
    recurrence_type = recurrence.get("type")
    if recurrence_type in [1, 2, 3, 4, 5, 6, 7, 8]:
        return True

    # 2. Campos que indican recurrencia aunque type sea 0 o nulo
    if (
        recurrence.get("repeat_interval") or
        recurrence.get("weekly_days") or
        recurrence.get("monthly_day") or
        recurrence.get("monthly_week") or
        recurrence.get("monthly_week_day")
    ):
        return True

    # 3. Si tiene múltiples ocurrencias (aunque no esté en recurrence)
    if session_data.get("occurrences") and len(session_data["occurrences"]) > 1:
        return True

    # 4. Si tiene end_times o end_date_time, puede ser recurrente
    if recurrence.get("end_times") or recurrence.get("end_date_time"):
        return True

    return False


def extract_custom_questions_columns(registrants: list) -> tuple:
    """Extrae todas las preguntas personalizadas únicas y crea columnas separadas"""
    all_questions = set()
    
    # Primero, recopilar todos los títulos de preguntas únicos
    for registrant in registrants:
        if registrant.get("custom_questions"):
            for question in registrant["custom_questions"]:
                title = question.get("title", "").strip()
                if title:
                    all_questions.add(title)
    
    # Convertir a lista ordenada para consistencia
    questions_list = sorted(list(all_questions))
    
    # Crear diccionario de mapeo para cada registrant
    registrants_with_questions = []
    for registrant in registrants:
        registrant_data = registrant.copy()
        
        # Inicializar todas las columnas de preguntas como vacías
        for question_title in questions_list:
            registrant_data[f"Pregunta: {question_title}"] = ""
        
        # Llenar las respuestas correspondientes
        if registrant.get("custom_questions"):
            for question in registrant["custom_questions"]:
                title = question.get("title", "").strip()
                value = question.get("value", "").strip()
                if title:
                    registrant_data[f"Pregunta: {title}"] = value
        
        registrants_with_questions.append(registrant_data)
    
    return registrants_with_questions, questions_list

def count_unique_participants(participants: list) -> int:
    """Cuenta participantes únicos por email, igual que en las hojas individuales"""
    unique_emails = set()
    for participant in participants:
        email = participant.get("user_email", "").lower()
        if email:  # Solo contar emails válidos
            unique_emails.add(email)
    return len(unique_emails)
# ==============================================================================
# NUEVA FUNCIÓN: Obtención de Reporte de Preguntas y Respuestas (Q&A)
# ==============================================================================
def get_qa_report_with_pagination(session_uuid: str, headers: dict) -> List[Dict]:
    """
    Obtiene todos los datos de Q&A para un webinar (no disponible para meetings) 
    usando paginación. Utiliza el UUID codificado de la sesión pasada.
    """
    import requests # Asegúrate de que requests esté importado en tu script
    from typing import List, Dict
    
    all_qa_entries = []
    page_token = None
    
    # Endpoint de Q&A para WEBINARS pasados (usa el UUID)
    base_url = f"https://api.zoom.us/v2/past_webinars/{session_uuid}/qa"
    
    while True:
        params = {"page_size": 300}
        if page_token:
            params["next_page_token"] = page_token

        print(f"📄 Obteniendo página de Q&A para UUID: {session_uuid}")
        res = requests.get(base_url, headers=headers, params=params)

        if res.status_code == 200:
            data = res.json()
            current_qa = data.get("questions", [])
            all_qa_entries.extend(current_qa)

            page_token = data.get("next_page_token")
            if not page_token:
                print(f"🏁 No hay más páginas de Q&A → total: {len(all_qa_entries)}")
                break
        elif res.status_code == 404:
            # ⚠️ Si devuelve 404, asumimos que no hay Q&A para la sesión y rompemos
            print("⚠️ Endpoint de Q&A no encontrado (típico si la sesión no tuvo Q&A o no es Webinar).")
            break
        else:
            print(f"❌ Error obteniendo Q&A: {res.status_code} - {res.text}")
            break

    # Procesar y aplanar los datos de Q&A (una fila por cada interacción)
    formatted_qa = []
    
    for entry in all_qa_entries: 
        asker_name = entry.get("name", "")
        asker_email = entry.get("email", "")
        
        # 1. Iterar sobre 'question_details' (Array que contiene la pregunta)
        question_details = entry.get("question_details", [])

        for q_detail in question_details:
            
            # Buscamos 'create_time', aunque tu API lo devuelva vacío
            question_time = q_detail.get("create_time", "")
            question = q_detail.get("question", "")
            
            # Listas para respuestas (detallado) y respuesta simple
            answers_list = q_detail.get("answer_details", []) 
            simple_answer = q_detail.get("answer", "") # Clave encontrada en tu debug
            
            
            # Flujo A: La respuesta está en el formato detallado (moderno de Zoom)
            if answers_list:
                for answer in answers_list:
                    formatted_qa.append({
                        "Nombre (Pregunta)": asker_name,
                        "Email (Pregunta)": asker_email,
                        "Pregunta": question,
                        "Hora de la Pregunta": question_time,
                        
                        "Respuesta": answer.get("content", ""), # Texto de respuesta detallado
                        
                        "Nombre (Respuesta)": answer.get("name", ""),
                        "Email (Respuesta)": answer.get("email", ""),
                        "Hora de la Respuesta": answer.get("create_time", ""), # Hora de respuesta detallada
                        "Tipo de Interacción": "PREGUNTA CON RESPUESTA (Detallada)",
                    })
            
            # Flujo B: La respuesta es simple, usando el campo 'answer' 
            elif simple_answer:
                formatted_qa.append({
                    "Nombre (Pregunta)": asker_name,
                    "Email (Pregunta)": asker_email,
                    "Pregunta": question,
                    "Hora de la Pregunta": question_time,
                    "Respuesta": simple_answer, # Texto de respuesta simple
                    "Nombre (Respuesta)": "(Respuesta Simple)",
                    "Email (Respuesta)": "",
                    "Hora de la Respuesta": "", # No hay metadata de hora/nombre/email para la respuesta simple
                    "Tipo de Interacción": "PREGUNTA CON RESPUESTA (Simple)",
                })

            # Flujo C: Pregunta sin respuesta (ni detallada ni simple)
            else:
                formatted_qa.append({
                    "Nombre (Pregunta)": asker_name,
                    "Email (Pregunta)": asker_email,
                    "Pregunta": question,
                    "Hora de la Pregunta": question_time,
                    "Respuesta": "",
                    "Nombre (Respuesta)": "",
                    "Email (Respuesta)": "",
                    "Hora de la Respuesta": "",
                    "Tipo de Interacción": "PREGUNTA SIN RESPUESTA",
                })
    
    # 💡 Devuelve [] si no hay datos, lo que permite la verificación condicional en la función llamadora
    return formatted_qa

def get_registrants_with_pagination(meeting_id: str, session_type: str, headers: dict) -> list:
    """Obtiene todos los registrants de un meeting/webinar usando paginación"""

    all_registrants = []
    page_token = None
    max_retries = 3
    retry_count = 0

    if session_type == "meeting":
        url = f"https://api.zoom.us/v2/meetings/{meeting_id}/registrants"
    else:
        url = f"https://api.zoom.us/v2/webinars/{meeting_id}/registrants"

    while True:
        # ✅ IMPORTANTE: Agregar parámetro 'occurrence_id' para webinars recurrentes
        # y asegurar que Zoom devuelva TODOS los campos estándar (phone, city, etc.)
        params = {
            "page_size": 100, 
            "status": "approved",
            # Para webinars, esto puede ser necesario en algunos casos
            "occurrence_id": ""  # Zoom usará la ocurrencia principal si está vacío
        }
        if page_token:
            params["next_page_token"] = page_token

        print(f"📄 Obteniendo página de registrants con params: {params}")
        res = requests.get(url, headers=headers, params=params)

        if res.status_code == 200:
            data = res.json()
            current_registrants = data.get("registrants", [])
            all_registrants.extend(current_registrants)

            print(f"✅ Página obtenida: {len(current_registrants)} registrants")
            print(f"📊 Total acumulado: {len(all_registrants)} registrants")
            print(f"📈 Total records según API: {data.get('total_records', 'N/A')}")

            page_token = data.get("next_page_token")
            if not page_token:
                print(f"🏁 No hay más páginas → total final: {len(all_registrants)} registrants")
                break

            retry_count = 0  # reset

        elif res.status_code == 400 and retry_count < max_retries:
            retry_count += 1
            print(f"⚠️ Error 400 (token expirado/inválido), reintento {retry_count}/{max_retries}")
            print(f"📝 Respuesta: {res.text}")

            page_token = None  # reiniciar desde primera página
            continue

        else:
            print(f"❌ Error obteniendo registrants: {res.status_code}")
            print(f"📝 Respuesta: {res.text}")
            break

    print(f"✅ Total registrants obtenidos: {len(all_registrants)}")
    
    # DEBUG: Mostrar estructura del primer registrant para verificar campos
    if all_registrants:
        import json
        print("\n🔍 DEBUG - Estructura del PRIMER registrant obtenido de la API:")
        print(json.dumps(all_registrants[0], indent=2, ensure_ascii=False))
        print(f"\n🔍 DEBUG - Claves disponibles en el primer registrant:")
        print(list(all_registrants[0].keys()))
        
        # ✅ Verificación específica de los campos faltantes
        print(f"\n🔍 VERIFICACIÓN DE CAMPOS OBLIGATORIOS:")
        print(f"   - 'phone' presente: {'phone' in all_registrants[0]}")
        print(f"   - 'city' presente: {'city' in all_registrants[0]}")
        if 'phone' in all_registrants[0]:
            print(f"     Valor: '{all_registrants[0]['phone']}'")
        if 'city' in all_registrants[0]:
            print(f"     Valor: '{all_registrants[0]['city']}'")
    
    return all_registrants


def check_recurring_sessions(meeting_id: str, headers: dict, session_type: str) -> Dict:
    """Verifica si la sesión es recurrente y obtiene información de todas las instancias"""
    formatted_id = format_meeting_id(meeting_id)
    print(f"🔍 Verificando recurrencia para {session_type} {formatted_id}")

    # Paso 1: Obtener info principal
    base_url = "https://api.zoom.us/v2"
    url_session_info = f"{base_url}/{ 'meetings' if session_type == 'meeting' else 'webinars' }/{formatted_id}"

    res_session_info = requests.get(url_session_info, headers=headers)
    if res_session_info.status_code != 200:
        print(f"❌ Error obteniendo información de sesión: {res_session_info.text}")
        return {"is_recurring": False, "sessions": []}

    session_data = res_session_info.json()

    # Paso 2: Verificar instancias
    url_instances = f"{base_url}/past_{ 'meetings' if session_type == 'meeting' else 'webinars' }/{formatted_id}/instances"
    print(f"🔗 URL Instances: {url_instances}")

    res_instances = requests.get(url_instances, headers=headers)
    print(f"📊 Status Instances: {res_instances.status_code}")

    if res_instances.status_code != 200:
        print(f"❌ No se pudieron obtener instancias: {res_instances.text}")
        return {"is_recurring": False, "sessions": []}

    key = "meetings" if session_type == "meeting" else "webinars"
    instances = res_instances.json().get(key, [])

    if len(instances) < 2:
        print(f"❌ Solo {len(instances)} instancia(s) encontradas → no es recurrente")
        return {"is_recurring": False, "sessions": []}

    print(f"✅ Recurrencia confirmada con {len(instances)} instancias")

    # Paso 3: Procesar instancias
    sessions_info = []
    for i, instance in enumerate(instances):
        print(f"📋 Procesando instancia {i+1}/{len(instances)}")
        instance_uuid = instance.get("uuid")
        instance_id = instance.get("id")
        start_time = instance.get("start_time")
        end_time = instance.get("end_time")
        duration = instance.get("duration", 0)

        if not instance_uuid:
            print("   ⚠️ Instancia sin UUID, saltando...")
            continue

        try:
            participants = get_participants_with_pagination(instance_uuid, session_type, headers)
            participants_count = count_unique_participants(participants)
            print(f"   🎯 Participantes únicos: {participants_count} (de {len(participants)} entradas)")
        except Exception as e:
            print(f"   ❌ Error obteniendo participantes: {str(e)}")
            participants_count = 0

        sessions_info.append({
            "session_id": instance_id,
            "uuid": instance_uuid,
            "start_time": start_time,
            "end_time": end_time,
            "duration": duration,
            "participants_count": participants_count,
            "registrants_count": 0,  # se actualiza luego
            "status": "completed" if end_time else "scheduled"
        })

    # Paso 4: Registrants comunes
    print("🔍 Obteniendo registrants comunes...")
    try:
        common_registrants = get_registrants_with_pagination(formatted_id, session_type, headers)
        common_registrants_count = len(common_registrants)
        print(f"✅ Registrants comunes encontrados: {common_registrants_count}")
    except Exception as e:
        print(f"❌ Error obteniendo registrants comunes: {str(e)}")
        common_registrants_count = 0

    # Actualizar cada instancia
    for session in sessions_info:
        session["registrants_count"] = common_registrants_count

    return {
        "is_recurring": True,
        "sessions": sessions_info,
        "recurrence_info": session_data.get("recurrence", {}),
        "total_instances": len(instances),
        "detection_method": "instances_endpoint",
        "common_registrants_count": common_registrants_count
    }


def get_session_details(session_id: str, headers: dict, session_type: str) -> Dict:
    """Obtiene detalles completos de una sesión específica (meeting o webinar)"""
    import urllib.parse
    encoded_id = urllib.parse.quote_plus(session_id)

    if session_type == "meeting":
        url_session = f"https://api.zoom.us/v2/past_meetings/{encoded_id}"
    else:
        url_session = f"https://api.zoom.us/v2/past_webinars/{encoded_id}"

    res_session = requests.get(url_session, headers=headers)
    if res_session.status_code != 200:
        print(f"❌ Error obteniendo detalles de sesión: {res_session.text}")
        return {}
    return res_session.json()



@router.post("/zoom/report/check")
async def check_zoom_report(request: ZoomReportRequest):
    """Verifica si existen inscritos para la reunión y devuelve estadísticas."""
    import requests
    import urllib.parse
    import pandas as pd
    from fastapi import HTTPException
    
    # ----------------------------------------------------------------------
    # ASUMIR FUNCIONES DE SOPORTE EXISTENTES:
    # - generate_zoom_token()
    # - format_meeting_id()
    # - detect_session_type()
    # - check_recurring_sessions()
    # - get_registrants_with_pagination()
    # - get_qa_report_with_pagination()
    # - extract_custom_questions_columns()
    # - (y la función 'generate_recurring_session_excel' NO se llama aquí)
    # ----------------------------------------------------------------------
    
    token = generate_zoom_token()
    headers = {"Authorization": f"Bearer {token}"}
    
    formatted_meeting_id = format_meeting_id(request.meeting_id)
    session_type = detect_session_type(formatted_meeting_id, headers)
    
    # Verificar si es una sesión recurrente
    print(f"🔍 Verificando si la {session_type} {formatted_meeting_id} es recurrente...")
    recurring_info = check_recurring_sessions(formatted_meeting_id, headers, session_type)

    # Obtener información detallada de la sesión principal (para el encabezado)
    # Nota: Este bloque de info de sesión es duplicado/simplificado para el chequeo
    entity = 'meetings' if session_type == 'meeting' else 'webinars'
    url_session_info = f"https://api.zoom.us/v2/{entity}/{formatted_meeting_id}"
    res_session_info = requests.get(url_session_info, headers=headers)
    session_data = res_session_info.json() if res_session_info.status_code == 200 else {}
    
    # ----------------------------------------------------------------------
    # LÓGICA RECURRENTE
    # ----------------------------------------------------------------------
    if recurring_info["is_recurring"]:
        print(f"🔄 Sesión recurrente detectada con {len(recurring_info['sessions'])} instancias")
        
        # Obtener y contar Registrados Comunes (ya está en recurring_info)
        registrants_count = len(recurring_info.get('common_registrants_list', []))
        
        # Calcular participantes totales únicos (ejemplo simplificado)
        total_unique_participants = sum(
            session.get('total_unique_participants', 0) for session in recurring_info['sessions']
        )
        
        # OMITIMOS el antiguo 'if request.generate_report' que causaba el error
        
        return {
            "message": f"✅ {session_type.title()} recurrente verificada.",
            "is_recurring": True,
            "topic": session_data.get("topic", "N/A"),
            "start_time": session_data.get("start_time", "N/A"),
            "total_instances": len(recurring_info['sessions']),
            "registrants_count": registrants_count,
            "total_unique_participants": total_unique_participants,
            "sessions": [
                {
                    "uuid": session['uuid'],
                    "start_time": session['start_time'],
                    "participants_count": session.get('participants_count', 0)
                }
                for session in recurring_info['sessions']
            ]
        }

    # ----------------------------------------------------------------------
    # LÓGICA NO RECURRENTE (Checkeo de existencia)
    # ----------------------------------------------------------------------
    
    # 1. Obtener Registrados (usa la función robusta get_registrants_with_pagination)
    try:
        registrants = get_registrants_with_pagination(formatted_meeting_id, session_type, headers)
        registrants_count = len(registrants)
    except Exception as e:
        # Aquí se captura cualquier error no 404/3001 que la función robusta no maneje
        print(f"❌ Error en registrants: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error obteniendo registrants: {str(e)}")
        
    if registrants_count == 0:
        return {"message": f"⚠️ La {session_type} {request.meeting_id} no tiene inscritos o no se encontraron."}
        
    # 2. Obtener UUID para participantes y Q&A
    entity_past = 'past_meetings' if session_type == 'meeting' else 'past_webinars'
    url_past_info = f"https://api.zoom.us/v2/{entity_past}/{formatted_meeting_id}"
    res_past_info = requests.get(url_past_info, headers=headers)
    
    if res_past_info.status_code != 200:
        raise HTTPException(status_code=res_past_info.status_code, detail=f"Error obteniendo UUID/Info de sesión pasada: {res_past_info.text}")
        
    uuid = res_past_info.json().get("uuid")
    encoded_uuid = urllib.parse.quote_plus(uuid)

    # 3. Obtener Q&A (solo si es webinar)
    qa_entries = []
    if session_type == "webinar":
        qa_entries = get_qa_report_with_pagination(encoded_uuid, headers)
        
    # 4. Obtener Participantes (solo contar, ya que este es el CHECK endpoint)
    # Asumimos que hay una función de conteo o reutilizamos la lógica de paginación
    
    # Aquí puedes usar tu función get_participants_with_pagination y luego contar
    # Por simplicidad, asumimos una función de solo conteo o la ejecución completa:
    
    # Llama a tu función que recupera todos los participantes
    url_participants = f"https://api.zoom.us/v2/{entity_past}/{encoded_uuid}/participants"
    # La paginación real debe ir aquí para un conteo preciso.
    # Usaremos una aproximación simple para no recrear toda la función de paginación:
    try:
        # *Reemplaza esta sección con tu paginación de participantes real*
        res_participants = requests.get(url_participants, headers=headers, params={"page_size": 1})
        total_unique_participants = res_participants.json().get('total_records', 'N/A')
    except Exception:
        total_unique_participants = 'N/A'
    
    # 5. Retornar las estadísticas (JSON)
    return {
        "message": f"✅ {session_type.title()} {request.meeting_id} verificada con éxito.",
        "is_recurring": False,
        "topic": session_data.get("topic", "N/A"),
        "start_time": session_data.get("start_time", "N/A"),
        "registrants_count": registrants_count,
        "participants_count": total_unique_participants,
        "qa_count": len(qa_entries),
        "uuid": uuid
    }
    
# NOTA: Todo el código de 'with pd.ExcelWriter...' y 'FileResponse' 
# ha sido ELIMINADO de este endpoint, dejando esa responsabilidad
# únicamente en el endpoint router.post("/zoom/report/excel").

async def generate_recurring_session_excel(meeting_id: str, session_type: str, recurring_info: dict, headers: dict):
    """Genera Excel para una sesión recurrente con múltiples hojas, incluyendo Q&A para Webinars."""
    formatted_meeting_id = format_meeting_id(meeting_id)
    
    print(f"🔄 Generando reporte para sesión recurrente con {len(recurring_info['sessions'])} instancias")
    
    # Obtener información de la sesión principal para la cabecera
    if session_type == "meeting":
        url_session_info = f"https://api.zoom.us/v2/meetings/{formatted_meeting_id}"
    else:  # webinar
        url_session_info = f"https://api.zoom.us/v2/webinars/{formatted_meeting_id}"
    
    res_session_info = requests.get(url_session_info, headers=headers)
    session_data = res_session_info.json() if res_session_info.status_code == 200 else {}

    print(f"🟢 Respuesta completa de la sesión: {session_data}")
    try:
        alternative_hosts = session_data.get("settings", {}).get("alternative_hosts", "")
        print(f"🟢 Valor raw de alternative_hosts: {alternative_hosts}")
        if isinstance(alternative_hosts, list):
            alternative_hosts = ";".join(alternative_hosts)
        print(f"🟢 Valor procesado de alternative_hosts: {alternative_hosts}")
    except Exception as e:
        print(f"❌ Excepción al obtener alternative_hosts: {e}")
        alternative_hosts = ""

    # Crear archivo Excel
    file_path = f"/tmp/zoom_{session_type}_recurring_report_{meeting_id}.xlsx"
    
    # OBTENER REGISTRANTS UNA SOLA VEZ ANTES DE RECORRER LAS INSTANCIAS
    print(f"📊 Obteniendo inscritos comunes ANTES de procesar instancias...")
    # Asumiendo que get_common_registrants devuelve una lista de diccionarios
    common_registrants = get_common_registrants(formatted_meeting_id, session_type, headers)
    print(f"✅ Registrants comunes obtenidos: {len(common_registrants)}")

    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        # Crear hoja de resumen
        create_summary_sheet(writer, session_data, recurring_info, session_type, meeting_id)
        
        # Crear hoja de inscritos (registrants) que son comunes para todas las sesiones
        if common_registrants:
            # Asumiendo que df_common_registrants es un DataFrame o lista para la función create_registrants_sheet
            create_registrants_sheet(writer, "Inscritos_Comunes", common_registrants)
        
        # Crear hoja para cada sesión
        for i, session in enumerate(recurring_info["sessions"]):
            session_uuid = session.get("uuid")
            start_time = session.get("start_time", "")
            
            # Formatear fecha para el nombre de la hoja
            if start_time:
                try:
                    # Formato seguro para evitar errores de zona horaria si 'Z' está presente
                    dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                    sheet_name = f"Sesión_{dt.strftime('%d-%m-%Y')}"
                except:
                    sheet_name = f"Sesión_{i+1}"
            else:
                sheet_name = f"Sesión_{i+1}"
            
            print(f"📊 Procesando {sheet_name}... ({i+1}/{len(recurring_info['sessions'])})")
            
            # Obtener datos de esta sesión específica usando el UUID
            if session_uuid:
                encoded_uuid = urllib.parse.quote_plus(session_uuid) # NUEVO: Codificar UUID
                
                # Obtener Participantes (asumiendo que get_session_data_by_uuid hace esto)
                # NOTA: Si get_session_data_by_uuid usa el endpoint /reports/participants, debe ser cambiado
                # para usar get_participants_with_pagination directamente, ya que los reports son más fiables.
                # Por simplicidad, asumo que get_session_data_by_uuid devuelve solo participantes.
                session_participants, session_registrants = get_session_data_by_uuid(session_uuid, session_type, headers, formatted_meeting_id)

                # NUEVO: Obtener Q&A para la instancia si es un webinar
                qa_data = []
                if session_type == "webinar":
                    encoded_uuid = urllib.parse.quote_plus(session_uuid) 
                    qa_data = get_qa_report_with_pagination(encoded_uuid, headers) # Llama a la función
                    print(f"    🎯 Q&A para {sheet_name}: {len(qa_data)} entradas.")
    
    # --- BLOQUE DE DEPURACIÓN PARA VERIFICAR LA INTEGRIDAD DE LOS DATOS ---
                    if qa_data:
                        import json # Asegurar que json esté disponible
                        print("\n" + "="*50)
                        print(f"🔍 Primera entrada Q&A (DEBUG - {sheet_name}):")
                        print(json.dumps(qa_data[0], indent=2, ensure_ascii=False))
                        print("="*50 + "\n")
    # --- FIN BLOQUE DE DEPURACIÓN ---
                    else:
                        print(f"    ⚠️ Sesión {sheet_name} no es Webinar, no se obtiene Q&A.")
                
                if session_participants or qa_data: # Se crea la hoja si hay participantes O Q&A
                    # MODIFICADO: Pasar 'qa_data' a la función de creación de hoja
                    create_session_sheet(
                        writer, 
                        sheet_name, 
                        session, 
                        session_participants, 
                        common_registrants, # Se usa la lista común de registrados
                        session_type,
                        qa_data # ¡DATOS Q&A AÑADIDOS!
                    )
                else:
                    print(f"   ⚠️ No se encontraron participantes ni datos Q&A para {sheet_name}")
            else:
                print(f"   ❌ Sesión sin UUID, saltando...")
    
    print("✅ Archivo Excel recurrente generado exitosamente (100%)")
    
    return FileResponse(
        file_path, 
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"zoom_{session_type}_recurring_report_{meeting_id}.xlsx"
    )

def create_summary_sheet(writer, session_data: dict, recurring_info: dict, session_type: str, meeting_id: str):
    from openpyxl.drawing.image import Image as XLImage

    summary_data = []
    alternative_hosts_raw = session_data.get("settings", {}).get("alternative_hosts", "")
    alternative_hosts_list = [email.strip() for email in alternative_hosts_raw.split(";") if email.strip()]
    if not alternative_hosts_list:
        alternative_hosts_list = [session_data.get("host_email", "").strip()]

    for session in recurring_info["sessions"]:
        start_time = session.get("start_time", "")
        participants = session.get("participants", [])
        end_time = session.get("end_time", "")

        # Formatear fecha de inicio
        formatted_start = ""
        if start_time:
            try:
                dt_start = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                formatted_start = dt_start.strftime("%d/%m/%Y %H:%M")
            except Exception:
                formatted_start = start_time

        # Buscar leave_time del alternative_host
        final_leave_time = None
        used_email = None
        for alt_email in alternative_hosts_list:
            for p in participants:
                if p.get("user_email", "").lower() == alt_email.lower():
                    print(f"Buscando leave_time para alternative_host: {alt_email} → encontrado: {p.get('leave_time', None)}")
                    if p.get("leave_time"):
                        final_leave_time = p.get("leave_time")
                        used_email = alt_email
                        break
            if final_leave_time:
                break

        # Si no se encuentra, buscar host principal
        if not final_leave_time:
            host_email = session_data.get("host_email", "").strip().lower()
            for p in participants:
                if p.get("user_email", "").lower() == host_email:
                    print(f"Buscando leave_time para host principal: {host_email} → encontrado: {p.get('leave_time', None)}")
                    if p.get("leave_time"):
                        final_leave_time = p.get("leave_time")
                        used_email = host_email
                        break

        # Si tampoco está, tomar el leave_time más tardío de todos los participantes
        if not final_leave_time and participants:
            leave_times = [p.get("leave_time") for p in participants if p.get("leave_time")]
            print(f"Leave times de todos los participantes: {leave_times}")
            if leave_times:
                final_leave_time = max(leave_times)
                used_email = "último participante"

        # Si no se encuentra, usar end_time de la instancia
        if not final_leave_time:
            final_leave_time = end_time
            used_email = "end_time API"

        print(f"Instancia: {session.get('uuid', '')} | Host usado para duración: {used_email} | leave_time: {final_leave_time}")

        # Calcular duración en minutos
        duration_minutes = 0
        try:
            if start_time and final_leave_time:
                dt_start = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                dt_end = datetime.fromisoformat(final_leave_time.replace('Z', '+00:00'))
                duration_minutes = round((dt_end - dt_start).total_seconds() / 60, 2)
                print(f"Duración calculada: {duration_minutes} minutos")
            else:
                print("No se pudo calcular duración por falta de datos.")
        except Exception as e:
            print(f"❌ Error calculando duración: {e}")
            duration_minutes = session.get("duration", 0)

        # Formatear hora de finalización
        formatted_end = ""
        if final_leave_time:
            try:
                dt_end = datetime.fromisoformat(final_leave_time.replace('Z', '+00:00'))
                formatted_end = dt_end.strftime("%d/%m/%Y %H:%M")
            except:
                formatted_end = final_leave_time

        summary_data.append({
            "UUID de Sesión": session.get("uuid", "N/A"),
            "Fecha y Hora": formatted_start,
            "Hora de inicio": formatted_start,
            "Hora de finalización": formatted_end,
            "Duración (min)": duration_minutes,
            "Inscritos": session.get("registrants_count", 0),
            "Participantes": session.get("participants_count", 0),
            "Anfitrión alternativo": alternative_hosts_raw,
            "Tasa de Asistencia": f"{(session.get('participants_count', 0) / session.get('registrants_count', 1) * 100):.1f}%" if session.get("registrants_count", 0) > 0 else "0%",
        })

    df_summary = pd.DataFrame(summary_data)
    df_summary = df_summary.sort_values("Fecha y Hora")

    # Información de la sesión principal (encabezado)
    session_info_data = {
        "Información de la Sesión": [
            f"Tipo: {session_type.title()}",
            f"ID Principal: {meeting_id}",
            f"Anfitrión (creador): {session_data.get('host_email', 'N/A')}",  # <-- NUEVO
            f"Total de Sesiones: {len(recurring_info['sessions'])}",
            f"Título: {session_data.get('topic', 'N/A')}",
            f"Descripción: {session_data.get('agenda', 'N/A')}",
            f"Configuración de Recurrencia: {recurring_info.get('recurrence_info', {}).get('type', 'N/A')}"
        ]
    }
    df_info = pd.DataFrame(session_info_data)

    # Escribir en Excel
    df_info.to_excel(writer, index=False, sheet_name="Resumen", startrow=2)
    df_summary.to_excel(writer, index=False, sheet_name="Resumen", startrow=len(session_info_data["Información de la Sesión"]) + 4)

    worksheet = writer.sheets["Resumen"]

    # Insertar imagen en A1, ajustada al 70% y alto de la fila 1 a 70px
    try:
        if ZOOM_HEADER_IMAGE and os.path.exists(ZOOM_HEADER_IMAGE):
            img = XLImage(ZOOM_HEADER_IMAGE)
            img.width = int(img.width * 0.7)
            img.height = int(img.height * 0.7)
            img.anchor = "A1"
            worksheet.add_image(img)
            worksheet.row_dimensions[1].height = 70
    except Exception as e:
        print(f"❌ Error insertando imagen en hoja Resumen: {e}")

    # Ajustar ancho de columnas
    for column in worksheet.columns:
        max_length = 0
        column_letter = column[0].column_letter
        for cell in column:
            try:
                if cell.value and len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except Exception:
                pass
        adjusted_width = min(max_length + 2, 50)
        worksheet.column_dimensions[column_letter].width = adjusted_width

def get_session_data(session_id: str, session_type: str, headers: dict) -> tuple:
    """Obtiene datos de participantes y registrants para una sesión específica usando session_id"""
    participants = []
    registrants = []
    
    # Obtener participantes
    if session_type == "meeting":
        url_participants = f"https://api.zoom.us/v2/report/meetings/{session_id}/participants"
    else:
        url_participants = f"https://api.zoom.us/v2/report/webinars/{session_id}/participants"
    
    res_participants = requests.get(url_participants, headers=headers)
    if res_participants.status_code == 200:
        participants = res_participants.json().get("participants", [])
    
    # Obtener registrants con paginación
    try:
        registrants = get_registrants_with_pagination(session_id, session_type, headers)
    except Exception as e:
        print(f"   ❌ Error obteniendo registrants: {str(e)}")
        registrants = []
    
    return participants, registrants

def get_session_data_by_uuid(session_uuid: str, session_type: str, headers: dict, meeting_id: str) -> tuple:
    """
    Obtiene datos de participantes para una sesión específica usando UUID.
    - Usa la función mejorada get_participants_with_pagination
    - Los registrants no se obtienen aquí (se devuelven vacíos)
    """
    participants = []

    print(f"   🔍 Obteniendo datos para UUID: {session_uuid} ({session_type})")

    try:
        # Ahora get_participants_with_pagination solo necesita uuid/id y session_type
        participants = get_participants_with_pagination(session_uuid, session_type, headers)
        print(f"   🎯 Total de participantes obtenidos: {len(participants)}")
    except Exception as e:
        print(f"   ❌ Error obteniendo participantes: {str(e)}")
        participants = []

    print(f"   ℹ️ Registrants se obtienen una sola vez para toda la sesión (no aquí)")

    return participants, []

def get_common_registrants(meeting_id: str, session_type: str, headers: dict) -> list:
    """Obtiene los registrants (inscritos) comunes para todas las sesiones recurrentes"""
    print(f"🔍 Obteniendo inscritos comunes para {session_type} {meeting_id}")
    
    try:
        registrants = get_registrants_with_pagination(meeting_id, session_type, headers)
        print(f"✅ Inscritos comunes encontrados: {len(registrants)}")
        return registrants
    except Exception as e:
        print(f"❌ Error obteniendo inscritos comunes: {str(e)}")
        return []

def create_registrants_sheet(writer, sheet_name: str, registrants: list):
    """Crea una hoja para mostrar los inscritos comunes"""
    print(f"📊 Creando hoja: {sheet_name}")

    # Procesar registrants con preguntas personalizadas como columnas separadas
    # Unificada para meetings y webinars: solo columnas con datos reales y preguntas personalizadas
    field_map = {
        "address": "Dirección",
        "city": "Ciudad",
        "country": "País",
        "zip": "Código Postal",
        "state": "Estado/Provincia",
        "phone": "Teléfono",
        "industry": "Industria",
        "org": "Organización",
        "job_title": "Cargo",
        "purchasing_time_frame": "Marco de Compra",
        "role_in_purchase_process": "Rol en Proceso de Compra",
        "no_of_employees": "Número de Empleados",
        "comments": "Comentarios",
        "status": "Estado",
        "create_time": "Fecha de Registro",
        "join_url": "URL de Ingreso"
    }
    registrants_processed = []
    for registrant in registrants:
        reg_dict = {}
        reg_dict["Nombre"] = f"{registrant.get('first_name', '')} {registrant.get('last_name', '')}".strip()
        reg_dict["Email"] = registrant.get("email", "")
        # ✅ IMPORTANTE: Inicializar TODOS los campos del field_map, incluso si están vacíos
        # Esto asegura que las columnas se creen en el DataFrame
        for field in field_map:
            col_name = field_map[field]
            value = registrant.get(field, "")
            # Ahora SIEMPRE se agrega la columna, incluso si está vacía
            reg_dict[col_name] = value
        reg_dict["custom_questions"] = registrant.get("custom_questions", [])
        registrants_processed.append(reg_dict)

    registrants_with_questions, questions_list = extract_custom_questions_columns(registrants_processed)
    df_registrants = pd.DataFrame(registrants_with_questions)
    if "custom_questions" in df_registrants.columns:
        df_registrants = df_registrants.drop("custom_questions", axis=1)
    
    # Punto de control: mostrar columnas y verificar si tienen datos
    # ✅ AHORA: Incluir TODAS las columnas de field_map (ya están inicializadas)
    # Más preguntas personalizadas si existen
    cols_with_data = ["Nombre", "Email"]
    cols_with_data.extend([field_map[f] for f in field_map])  # Agregar todos los campos del mapa
    cols_with_data.extend([col for col in df_registrants.columns if col.startswith("Pregunta: ")])  # Agregar preguntas personalizadas
    
    # Asegurar que solo existan columnas que están en el DataFrame
    cols_with_data = [c for c in cols_with_data if c in df_registrants.columns]
    
    print(f"🟢 Columnas con datos reales y activas en registrados: {cols_with_data}")
    for col in cols_with_data:
        muestra = df_registrants[col].head(5).tolist()
        print(f"   - {col}: {muestra}")
    # Filtrar DataFrame para solo columnas con datos
    df_registrants = df_registrants[cols_with_data]
    # Asegurar columnas mínimas si está vacío
    if df_registrants.empty:
        df_registrants = pd.DataFrame(columns=["Nombre", "Email"])
    df_registrants.to_excel(writer, index=False, sheet_name=sheet_name, startrow=3)
    worksheet = writer.sheets[sheet_name]
    # Insertar imagen en A1, ajustada al 70% y alto de la fila 1 a 70px
    try:
        from openpyxl.drawing.image import Image as XLImage
        if ZOOM_HEADER_IMAGE and os.path.exists(ZOOM_HEADER_IMAGE):
            img = XLImage(ZOOM_HEADER_IMAGE)
            img.width = int(img.width * 0.7)
            img.height = int(img.height * 0.7)
            img.anchor = "A1"
            worksheet.add_image(img)
            worksheet.row_dimensions[1].height = 70
    except Exception:
        pass

    # Ajustar ancho de columnas
    for column in worksheet.columns:
        max_length = 0
        column_letter = column[0].column_letter
        for cell in column:
            try:
                if cell.value and len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except Exception:
                pass
        adjusted_width = min(max_length + 2, 50)
        worksheet.column_dimensions[column_letter].width = adjusted_width

def create_session_sheet(
    writer, 
    sheet_name: str, 
    session: dict, 
    participants: list, 
    registrants: list, 
    session_type: str, 
    qa_data: Optional[List[Dict]] = None # NUEVO: Parámetro opcional para Q&A
):
    """
    Crea las hojas de Participantes, Registrados (si aplica) y Q&A (si es Webinar) 
    para una sesión específica.
    """
    print(f"📊 Creando hojas de reporte para la sesión: {sheet_name}")
    
    # ----------------------------------------------------------------------
    # 1. PREPARACIÓN DE ENCABEZADOS Y METADATOS (Reutilizado de tu código)
    # ----------------------------------------------------------------------
    header_labels = ["ID de Sesión:", "Tema:", "Tipo:", "ID de Host:"]
    header_values = [
        str(session.get("id", "")),
        session.get("topic", ""),
        session_type,
        session.get("host_email", "")
    ]
    
    # Función local para insertar la imagen (asumiendo que ZOOM_HEADER_IMAGE está definido globalmente)
    def insert_header_image_local(worksheet):
        """Inserta la imagen de encabezado y ajusta la altura de la fila 1."""
        try:
            from openpyxl.drawing.image import Image as XLImage
            # Asumiendo que ZOOM_HEADER_IMAGE y os están disponibles globalmente en el scope de tu script
            if 'ZOOM_HEADER_IMAGE' in globals() and os.path.exists(globals()['ZOOM_HEADER_IMAGE']):
                img = XLImage(globals()['ZOOM_HEADER_IMAGE'])
                img.width = int(img.width * 0.7)
                img.height = int(img.height * 0.7)
                img.anchor = "A1"
                worksheet.add_image(img)
                worksheet.row_dimensions[1].height = 70
        except Exception as e:
            print(f"❌ Error insertando imagen: {e}")

    # Función local para ajustar el ancho de las columnas
    def auto_fit_columns(worksheet):
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 120) # Ancho máximo de 50
            worksheet.column_dimensions[column_letter].width = adjusted_width

    # ----------------------------------------------------------------------
    # 2. PROCESAMIENTO Y CREACIÓN DE HOJA 'PARTICIPANTES' (Tu lógica original)
    # ----------------------------------------------------------------------
    print(f"   📈 Total de participantes recibidos: {len(participants)}")
    
    # [Lógica de consolidación de participantes por email...]
    participants_consolidated = {}
    for participant in participants:
        email = participant.get("user_email", "").lower()
        name = participant.get("name", "")
        
        # Consolidation logic remains the same (ensuring earliest join, latest leave, sum duration)
        if email in participants_consolidated:
            existing = participants_consolidated[email]
            if participant.get("join_time", "") < existing.get("Hora de Ingreso", "9999"):
                existing["Hora de Ingreso"] = participant.get("join_time", "")
            if participant.get("leave_time", "") > existing.get("Hora de Salida", ""):
                existing["Hora de Salida"] = participant.get("leave_time", "")
            
            existing["User ID"] = participant.get("user_id", existing.get("User ID", ""))
            existing["_duracion_segundos"] += participant.get("duration", 0)
            existing["Duración (minutos)"] = round(existing["_duracion_segundos"] / 60, 0)
            existing["Número de Entradas"] += 1
            
        else:
            participants_consolidated[email] = {
                "User ID": participant.get("user_id", ""),
                "Nombre y apellidos": name,
                "Email": email,
                "Hora de Ingreso": participant.get("join_time", ""),
                "Hora de Salida": participant.get("leave_time", ""),
                "_duracion_segundos": participant.get("duration", 0),
                "Duración (minutos)": round(participant.get("duration", 0) / 60, 0) if participant.get("duration") else 0,
                "Invitado": "SI" if participant.get("internal_user", False) else "NO",
                "Número de Entradas": 1
            }

    print(f"   🔄 Participantes consolidados: {len(participants_consolidated)}")
    
    total_duration_seconds = sum(p["_duracion_segundos"] for p in participants_consolidated.values())
    participants_processed = []
    for i, participant in enumerate(participants_consolidated.values(), 1):
        participant_copy = participant.copy()
        participant_copy["N°"] = i
        participant_copy.pop("_duracion_segundos", None)
        participants_processed.append(participant_copy)
    
    # Escritura de Hoja 'Participantes'
    participants_sheet_name = f"{sheet_name}_Participantes"
    if participants_processed:
        df_participants = pd.DataFrame(participants_processed)
        columns_order = ["N°"] + [col for col in df_participants.columns if col != "N°"]
        df_participants = df_participants[columns_order]
    else:
        df_participants = pd.DataFrame(columns=["N°", "Nombre y apellidos", "Email", "Hora de Ingreso", "Hora de Salida", "Duración (minutos)", "Número de Entradas"])

    df_participants.to_excel(writer, index=False, sheet_name=participants_sheet_name, startrow=3)
    worksheet_participants = writer.sheets[participants_sheet_name]
    
    # Aplicar formato (imagen, encabezado y auto-ajuste)
    insert_header_image_local(worksheet_participants)
    for col_idx, label in enumerate(header_labels, start=1):
        worksheet_participants.cell(row=1, column=col_idx, value=label)
    for col_idx, value in enumerate(header_values, start=1):
        worksheet_participants.cell(row=2, column=col_idx, value=value)
    auto_fit_columns(worksheet_participants)

    print(f"   ✅ Hoja '{participants_sheet_name}' creada.")
    

    # ----------------------------------------------------------------------
    # 4. NUEVA HOJA 'Q&A' (Preguntas y Respuestas)
    # ----------------------------------------------------------------------
    if qa_data and session_type == "webinar":
        print("   💬 Creando hoja de Preguntas y Respuestas (Q&A)...")
        
        # 1. Definición explícita de las columnas para asegurar el orden y el contenido
        qa_column_order = [
            "Nombre (Pregunta)", "Email (Pregunta)", "Pregunta", "Hora de la Pregunta",
            "Respuesta", "Nombre (Respuesta)", "Email (Respuesta)", "Hora de la Respuesta",
            "Tipo de Interacción"
        ]

        try:
            # Crea el DataFrame forzando el orden de las columnas
            df_qa = pd.DataFrame(qa_data, columns=qa_column_order)
            qa_sheet_name = f"{sheet_name}_Q&A"
            
            # Exportar a Excel, comenzando en la fila 3 para dejar espacio al encabezado
            df_qa.to_excel(writer, index=False, sheet_name=qa_sheet_name, startrow=3)
            
            worksheet_qa = writer.sheets[qa_sheet_name]
            
            # 🛑 PASO CRÍTICO: Aplicar Envoltura de Texto (Wrap Text) a Pregunta y Respuesta
            # Esto permite que el texto largo se muestre en varias líneas
            col_indices_to_wrap = [qa_column_order.index("Pregunta") + 1, qa_column_order.index("Respuesta") + 1]

            # Iterar sobre las filas que contienen datos (a partir de la fila 4, ya que startrow=3)
            for row in worksheet_qa.iter_rows(min_row=4, max_row=worksheet_qa.max_row):
                for cell in row:
                    # Si la columna actual es 'Pregunta' o 'Respuesta'
                    if cell.column in col_indices_to_wrap:
                        # Asegurar el uso del import: from openpyxl.styles import Alignment
                        cell.alignment = Alignment(wrap_text=True) 

            # 2. Aplicar el FORMATO de encabezado (SIN DUPLICAR)
            
            # 2a. Insertar imagen y ajustar altura de fila 1
            insert_header_image_local(worksheet_qa) 
            
            # 2b. Escribir encabezados de la sesión (Filas 1 y 2)
            for col_idx, label in enumerate(header_labels, start=1):
                worksheet_qa.cell(row=1, column=col_idx, value=label)
            for col_idx, value in enumerate(header_values, start=1):
                worksheet_qa.cell(row=2, column=col_idx, value=value)
            
            # 2c. Aplicar ajuste de columna (Con límite de 120, debe ser suficiente)
            auto_fit_columns(worksheet_qa) 
            
            print(f"   ✅ Hoja '{qa_sheet_name}' creada con {len(qa_data)} interacciones de Q&A.")
        
        except Exception as e:
            print(f"   ❌ ERROR al crear DataFrame de Q&A para {sheet_name}: {e}")

        
        # Aplicar formato (imagen, encabezado y auto-ajuste)
        insert_header_image_local(worksheet_qa) 
        for col_idx, label in enumerate(header_labels, start=1):
            worksheet_qa.cell(row=1, column=col_idx, value=label)
        for col_idx, value in enumerate(header_values, start=1):
            worksheet_qa.cell(row=2, column=col_idx, value=value)
            
        auto_fit_columns(worksheet_qa)
        print(f"   ✅ Hoja '{qa_sheet_name}' creada con {len(qa_data)} interacciones de Q&A.")
    elif session_type != "webinar" and qa_data is not None:
        print("   ⚠️ Sesión no es Webinar. Se omite la hoja Q&A.")
    
    # ----------------------------------------------------------------------
    # 5. RETORNO DE RESULTADOS
    # ----------------------------------------------------------------------
    return {
        "participants_count": len(participants_processed),
        "total_duration_seconds": total_duration_seconds,
        "unique_emails": len(participants_consolidated),
        "total_entries": len(participants)
    }

# NOTA: Asegúrate de que las variables 'requests', 'pd', 'os', 'List', 'Dict', 
# 'Optional' y la función 'insert_header_image' o su lógica equivalente 
# estén correctamente importadas/definidas en el scope de tu script.


def get_polls_for_meeting(meeting_id, session_type, headers, is_recurrent=False, instance_uuids=None):
    """
    Obtiene los polls para meetings/webinars simples o recurrentes.
    - Para simples: consulta el endpoint directo.
    - Para recurrentes: consulta cada instancia por UUID.
    """
    polls_data = []
    if session_type == "meeting":
        if is_recurrent and instance_uuids:
            for uuid in instance_uuids:
                url = f"https://api.zoom.us/v2/report/meetings/{uuid}/polls"
                res = requests.get(url, headers=headers)
                if res.status_code == 200:
                    polls = res.json().get("polls", [])
                    for poll in polls:
                        poll["instance_uuid"] = uuid
                        polls_data.append(poll)
        else:
            url = f"https://api.zoom.us/v2/report/meetings/{meeting_id}/polls"
            res = requests.get(url, headers=headers)
            if res.status_code == 200:
                polls = res.json().get("polls", [])
                polls_data.extend(polls)
    elif session_type == "webinar":
        if is_recurrent and instance_uuids:
            for uuid in instance_uuids:
                url = f"https://api.zoom.us/v2/report/webinars/{uuid}/polls"
                res = requests.get(url, headers=headers)
                if res.status_code == 200:
                    polls = res.json().get("polls", [])
                    for poll in polls:
                        poll["instance_uuid"] = uuid
                        polls_data.append(poll)
        else:
            url = f"https://api.zoom.us/v2/report/webinars/{meeting_id}/polls"
            res = requests.get(url, headers=headers)
            if res.status_code == 200:
                polls = res.json().get("polls", [])
                polls_data.extend(polls)
    return polls_data

def get_participants_with_pagination(uuid_or_id: str, session_type: str, headers: dict) -> list:
    """
    Obtiene todos los participantes de una sesión (meeting o webinar),
    manejando correctamente casos simples y recurrentes.
    - Para meetings: usa /past_meetings/{uuid}/participants
    - Para webinars simples: usa /past_webinars/{webinar_id}/participants (ID numérico)
    - Para webinars recurrentes: usa /past_webinars/{uuid}/participants (UUID de instancia)
    """
    import urllib.parse
    import requests

    all_participants = []

    def fetch_participants_for_uuid(endpoint_base: str, id_or_uuid: str, is_webinar_simple=False) -> list:
        """Helper para obtener participantes con paginación en un uuid o id concreto."""
        if is_webinar_simple:
            url = f"{endpoint_base}/{id_or_uuid}/participants"
        else:
            encoded_uuid = urllib.parse.quote_plus(id_or_uuid)
            url = f"{endpoint_base}/{encoded_uuid}/participants"
        participants = []
        page_token = None

        while True:
            params = {"page_size": 100}
            if page_token:
                params["next_page_token"] = page_token

            res = requests.get(url, headers=headers, params=params)
            if res.status_code == 200:
                data = res.json()
                current = data.get("participants", [])
                participants.extend(current)

                page_token = data.get("next_page_token")
                if not page_token:
                    break
            else:
                print(f"❌ Error {res.status_code} obteniendo participantes de {url}")
                print(f"📝 Respuesta: {res.text}")
                break

        return participants

    # --- Meetings ---
    if session_type == "meeting":
        # Intentar obtener instancias (si es recurrente)
        url_instances = f"https://api.zoom.us/v2/past_meetings/{uuid_or_id}/instances"
        res = requests.get(url_instances, headers=headers)

        if res.status_code == 200:
            instances = res.json().get("meetings", [])
            if instances:
                print(f"🔄 Reunión recurrente detectada: {len(instances)} instancias")
                for inst in instances:
                    inst_uuid = inst.get("uuid")
                    if inst_uuid:
                        all_participants.extend(fetch_participants_for_uuid("https://api.zoom.us/v2/past_meetings", inst_uuid))
            else:
                # Caso reunión simple
                all_participants.extend(fetch_participants_for_uuid("https://api.zoom.us/v2/past_meetings", uuid_or_id))
        else:
            # Si 404, no es recurrente → usar directamente como simple
            all_participants.extend(fetch_participants_for_uuid("https://api.zoom.us/v2/past_meetings", uuid_or_id))

    # --- Webinars ---
    elif session_type == "webinar":
        # Intentar obtener instancias (si es recurrente)
        url_instances = f"https://api.zoom.us/v2/past_webinars/{uuid_or_id}/instances"
        res = requests.get(url_instances, headers=headers)

        if res.status_code == 200:
            instances = res.json().get("webinars", [])
            if instances:
                print(f"🔄 Webinar recurrente detectada: {len(instances)} instancias")
                for inst in instances:
                    inst_uuid = inst.get("uuid")
                    if inst_uuid:
                        all_participants.extend(fetch_participants_for_uuid("https://api.zoom.us/v2/past_webinars", inst_uuid))
            else:
                # Caso webinar simple: usar el ID numérico, no UUID
                all_participants.extend(fetch_participants_for_uuid("https://api.zoom.us/v2/past_webinars", uuid_or_id, is_webinar_simple=True))
        else:
            # Si 404, no es recurrente → usar directamente como simple
            all_participants.extend(fetch_participants_for_uuid("https://api.zoom.us/v2/past_webinars", uuid_or_id, is_webinar_simple=True))

    else:
        raise ValueError("❌ session_type inválido. Debe ser 'meeting' o 'webinar'.")

    print(f"✅ Total de participantes obtenidos: {len(all_participants)}")
    return all_participants

def create_polls_sheet(writer, sheet_name, polls, header_labels, header_values, image_path=None):
    """
    Crea la hoja de Polls con el mismo formato que Participantes: encabezado y datos.
    """
    import pandas as pd
    # Procesar polls en DataFrame
    rows = []
    for poll in polls:
        poll_title = poll.get("title", "")
        poll_questions = poll.get("questions", [])
        for q in poll_questions:
            question = q.get("name", "")
            for option in q.get("options", []):
                rows.append({
                    "Título Poll": poll_title,
                    "Pregunta": question,
                    "Opción": option.get("name", ""),
                    "Votos": option.get("vote_count", 0),
                    "Porcentaje": option.get("vote_percentage", 0),
                    "Usuario": option.get("user", ""),
                    "Instancia UUID": poll.get("instance_uuid", "")
                })
    if not rows:
        return  # No crear hoja si no hay datos
    df_polls = pd.DataFrame(rows)
    # Escribir en Excel con encabezado igual a Participantes
    df_polls.to_excel(writer, index=False, sheet_name=sheet_name, startrow=3)
    worksheet = writer.sheets[sheet_name]
    # Insertar imagen en A1 si existe
    if image_path and os.path.exists(image_path):
        from openpyxl.drawing.image import Image as XLImage
        img = XLImage(image_path)
        img.width = int(img.width * 0.7)
        img.height = int(img.height * 0.7)
        img.anchor = "A1"
        worksheet.add_image(img)
        worksheet.row_dimensions[1].height = 70
    # Encabezado igual a Participantes
    for col_idx, label in enumerate(header_labels, start=1):
        worksheet.cell(row=1, column=col_idx, value=label)
    for col_idx, value in enumerate(header_values, start=1):
        worksheet.cell(row=2, column=col_idx, value=value)
    # Ajustar ancho de columnas
    for column in worksheet.columns:
        max_length = 0
        column_letter = column[0].column_letter
        for cell in column:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        adjusted_width = min(max_length + 2, 50)
        worksheet.column_dimensions[column_letter].width = adjusted_width

def get_qa_for_meeting(meeting_id, session_type, headers, is_recurrent=False, instance_uuids=None):
    """
    Obtiene las Q&A para meetings/webinars simples o recurrentes.
    - Para simples: consulta el endpoint directo.
    - Para recurrentes: consulta cada instancia por UUID.
    """
    qa_data = []
    # Zoom solo tiene Q&A en webinars, endpoint: /report/webinars/{webinarId}/qa y /report/webinars/{uuid}/qa
    if session_type == "webinar":
        if is_recurrent and instance_uuids:
            for uuid in instance_uuids:
                url = f"https://api.zoom.us/v2/report/webinars/{uuid}/qa"
                res = requests.get(url, headers=headers)
                if res.status_code == 200:
                    qa = res.json().get("questions", [])
                    for q in qa:
                        q["instance_uuid"] = uuid
                        qa_data.append(q)
        else:
            url = f"https://api.zoom.us/v2/report/webinars/{meeting_id}/qa"
            res = requests.get(url, headers=headers)
            if res.status_code == 200:
                qa = res.json().get("questions", [])
                qa_data.extend(qa)
    return qa_data

def get_qa_for_webinar(webinar_id: str, headers: dict) -> list:
    """
    Obtiene las preguntas Q&A de un webinar simple usando el ID numérico.
    Muestra en consola si hay preguntas y el número de preguntas.
    Si hay error 400, muestra el mensaje completo para diagnóstico.
    """
    import requests
    url_qa = f"https://api.zoom.us/v2/past_webinars/{webinar_id}/qa"
    res_qa = requests.get(url_qa, headers=headers)
    all_qas = []
    if res_qa.status_code == 200:
        qas = res_qa.json().get("questions", [])
        print(f"   Preguntas Q&A encontradas: {len(qas)}")
        if qas:
            print(f"      Ejemplo pregunta: {qas[0].get('question', '')}")
        all_qas.append({
            "uuid": webinar_id,
            "start_time": None,
            "qas": qas
        })
    elif res_qa.status_code == 400:
        print(f"   ❌ Error 400 obteniendo Q&A: {res_qa.text}")
    else:
        print(f"   ❌ Error obteniendo Q&A: {res_qa.status_code} - {res_qa.text}")
    return all_qas

def get_router() -> APIRouter:
    return router

@router.post("/zoom/report/excel")
async def get_zoom_report_excel(request: ZoomReportRequest):
    """
    Genera el reporte Excel para una sesión simple o recurrente.
    """
    token = generate_zoom_token()
    headers = {"Authorization": f"Bearer {token}"}
    formatted_meeting_id = format_meeting_id(request.meeting_id)
    session_type = detect_session_type(formatted_meeting_id, headers)
    recurring_info = check_recurring_sessions(formatted_meeting_id, headers, session_type)

    if recurring_info.get("is_recurring"):
        return await generate_recurring_session_excel(request.meeting_id, session_type, recurring_info, headers)
    else:
        return await generate_single_session_excel(request.meeting_id, session_type, headers)

async def generate_single_session_excel(meeting_id: str, session_type: str, headers: dict):
    import pandas as pd
    import os
    from datetime import datetime
    import urllib.parse

    formatted_meeting_id = format_meeting_id(meeting_id)

    # Obtener registrants
    registrants = get_registrants_with_pagination(formatted_meeting_id, session_type, headers)
    registrants_processed = []
    for registrant in registrants:
        registrants_processed.append({
            "Nombre": f"{registrant.get('first_name', '')} {registrant.get('last_name', '')}".strip(),
            "Email": registrant.get("email", ""),
            "Dirección": registrant.get("address", ""),
            "Ciudad": registrant.get("city", ""),
            "País": registrant.get("country", ""),
            "Código Postal": registrant.get("zip", ""),
            "Estado/Provincia": registrant.get("state", ""),
            "Teléfono": registrant.get("phone", ""),
            "Industria": registrant.get("industry", ""),
            "Organización": registrant.get("org", ""),
            "Cargo": registrant.get("job_title", ""),
            "Marco de Compra": registrant.get("purchasing_time_frame", ""),
            "Rol en Proceso de Compra": registrant.get("role_in_purchase_process", ""),
            "Número de Empleados": registrant.get("no_of_employees", ""),
            "Comentarios": registrant.get("comments", ""),
            "Estado": registrant.get("status", ""),
            "Fecha de Registro": registrant.get("create_time", ""),
            #"URL de Ingreso": registrant.get("join_url", ""),
            "custom_questions": registrant.get("custom_questions", [])
        })
    registrants_with_questions, questions_list = extract_custom_questions_columns(registrants_processed)
    df_registrants = pd.DataFrame(registrants_with_questions)
    if "custom_questions" in df_registrants.columns:
        df_registrants = df_registrants.drop("custom_questions", axis=1)

    # ✅ DEBUG: Mostrar todas las columnas del DataFrame de registrants
    print(f"\n🟢 COLUMNAS DEL DataFrame de registrants para Excel:")
    print(f"   Total columnas: {len(df_registrants.columns)}")
    print(f"   Lista de columnas: {list(df_registrants.columns)}")
    print(f"\n   ¿'Teléfono' en columnas?: {'Teléfono' in df_registrants.columns}")
    print(f"   ¿'Ciudad' en columnas?: {'Ciudad' in df_registrants.columns}")
    
    if "Teléfono" in df_registrants.columns:
        print(f"   Primeros 3 valores de 'Teléfono': {df_registrants['Teléfono'].head(3).tolist()}")
    if "Ciudad" in df_registrants.columns:
        print(f"   Primeros 3 valores de 'Ciudad': {df_registrants['Ciudad'].head(3).tolist()}")
    
    # ✅ FILTRAR COLUMNAS: Solo mostrar las que tienen datos (no están completamente vacías)
    cols_with_data = []
    for col in df_registrants.columns:
        # Siempre incluir Nombre y Email
        if col in ["Nombre", "Email"]:
            cols_with_data.append(col)
        # Incluir columnas que tienen al menos un valor no vacío
        elif df_registrants[col].notnull().any() and df_registrants[col].astype(str).str.strip().any():
            cols_with_data.append(col)
        # Incluir preguntas personalizadas
        elif col.startswith("Pregunta: "):
            cols_with_data.append(col)
    
    print(f"\n🟢 COLUMNAS CON DATOS (después de filtrar):")
    print(f"   Total columnas: {len(cols_with_data)}")
    print(f"   Lista de columnas: {cols_with_data}")
    
    # Aplicar filtro al DataFrame
    df_registrants = df_registrants[cols_with_data]

    # Obtener participantes (soporta webinars simples y recurrentes)
    all_participants = []
    if session_type == "webinar":
        url_instances = f"https://api.zoom.us/v2/past_webinars/{formatted_meeting_id}/instances"
        res_instances = requests.get(url_instances, headers=headers)
        if res_instances.status_code == 200:
            instances = res_instances.json().get("webinars", [])
            if instances:
                for inst in instances:
                    inst_uuid = inst.get("uuid")
                    if inst_uuid:
                        encoded_uuid = urllib.parse.quote_plus(inst_uuid)
                        url_participants = f"https://api.zoom.us/v2/past_webinars/{encoded_uuid}/participants"
                        page_token = None
                        while True:
                            params = {"page_size": 100}
                            if page_token:
                                params["next_page_token"] = page_token
                            res_participants = requests.get(url_participants, headers=headers, params=params)
                            if res_participants.status_code == 200:
                                data = res_participants.json()
                                current_participants = data.get("participants", [])
                                all_participants.extend(current_participants)
                                page_token = data.get("next_page_token")
                                if not page_token:
                                    break
                            else:
                                break
            else:
                url_participants = f"https://api.zoom.us/v2/past_webinars/{formatted_meeting_id}/participants"
                page_token = None
                while True:
                    params = {"page_size": 100}
                    if page_token:
                        params["next_page_token"] = page_token
                    res_participants = requests.get(url_participants, headers=headers, params=params)
                    if res_participants.status_code == 200:
                        data = res_participants.json()
                        current_participants = data.get("participants", [])
                        all_participants.extend(current_participants)
                        page_token = data.get("next_page_token")
                        if not page_token:
                            break
                    else:
                        break
        else:
            url_participants = f"https://api.zoom.us/v2/past_webinars/{formatted_meeting_id}/participants"
            page_token = None
            while True:
                params = {"page_size": 100}
                if page_token:
                    params["next_page_token"] = page_token
                res_participants = requests.get(url_participants, headers=headers, params=params)
                if res_participants.status_code == 200:
                    data = res_participants.json()
                    current_participants = data.get("participants", [])
                    all_participants.extend(current_participants)
                    page_token = data.get("next_page_token")
                    if not page_token:
                        break
                else:
                    break
    else:
        url_instances = f"https://api.zoom.us/v2/past_meetings/{formatted_meeting_id}/instances"
        res_instances = requests.get(url_instances, headers=headers)
        if res_instances.status_code == 200:
            instances = res_instances.json().get("meetings", [])
            if instances:
                for inst in instances:
                    inst_uuid = inst.get("uuid")
                    if inst_uuid:
                        encoded_uuid = urllib.parse.quote_plus(inst_uuid)
                        url_participants = f"https://api.zoom.us/v2/past_meetings/{encoded_uuid}/participants"
                        page_token = None
                        while True:
                            params = {"page_size": 100}
                            if page_token:
                                params["next_page_token"] = page_token
                            res_participants = requests.get(url_participants, headers=headers, params=params)
                            if res_participants.status_code == 200:
                                data = res_participants.json()
                                current_participants = data.get("participants", [])
                                all_participants.extend(current_participants)
                                page_token = data.get("next_page_token")
                                if not page_token:
                                    break
                            else:
                                break
            else:
                url_participants = f"https://api.zoom.us/v2/past_meetings/{formatted_meeting_id}/participants"
                page_token = None
                while True:
                    params = {"page_size": 100}

                    if page_token:
                        params["next_page_token"] = page_token
                    res_participants = requests.get(url_participants, headers=headers, params=params)
                    if res_participants.status_code == 200:
                        data = res_participants.json()
                        current_participants = data.get("participants", [])
                        all_participants.extend(current_participants)
                        page_token = data.get("next_page_token")
                        if not page_token:
                            break

                    else:
                        break
        else:
            url_participants = f"https://api.zoom.us/v2/past_meetings/{formatted_meeting_id}/participants"
            page_token = None
            while True:
                params = {"page_size": 100}
                if page_token:
                    params["next_page_token"] = page_token
                res_participants = requests.get(url_participants, headers=headers, params=params)
                if res_participants.status_code == 200:
                    data = res_participants.json()
                    current_participants = data.get("participants", [])
                    all_participants.extend(current_participants)
                    page_token = data.get("next_page_token")
                    if not page_token:
                        break
                else:
                    break

    # Consolidar participantes únicos por email y sumar duración
    participants_consolidated = consolidate_participants(all_participants)
    df_participants = pd.DataFrame(participants_consolidated)

    # LOGS
    print(f"🔢 Total de participantes (entradas): {len(all_participants)}")
    print(f"🔢 Total de participantes únicos: {len(participants_consolidated)}")

    # Obtener detalles de la sesión para el encabezado y datos de sesión
    session_info = get_session_info_for_excel(meeting_id, session_type, headers, len(participants_consolidated))
    # Forzar obtención de anfitriones alternativos en webinars simples si no está presente
    if session_type == "webinar" and not session_info.get("alternative_hosts"):
        # Buscar en settings alternativos si existe
        url = f"https://api.zoom.us/v2/webinars/{meeting_id}/settings"
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            settings = res.json()
            alt_hosts = settings.get("alternative_hosts", "")
            if isinstance(alt_hosts, list):
                alt_hosts = ";".join(alt_hosts)
            session_info["alternative_hosts"] = alt_hosts
            print(f"🟢 Anfitriones alternativos (webinar simple): {alt_hosts}")

    header_labels = [
        "Tema", "ID_session", "Anfitrión", "Duración (minutos)",
        "Hora de inicio", "Hora de finalización", "Participantes"
    ]
    header_values = [
        session_info.get("topic", "N/A"),
        meeting_id,
        session_info.get("host_email", "N/A"),
        session_info.get("duration", ""),
        session_info.get("start_time", ""),
        session_info.get("end_time", ""),
        session_info.get("total_unique_participants", 0)
    ]

    file_path = f"/tmp/zoom_{session_type}_report_{meeting_id}.xlsx"
    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        write_excel_with_header(writer, df_registrants, "Inscritos", header_labels, header_values, ZOOM_HEADER_IMAGE, session_info)
        write_excel_with_header(writer, df_participants, "Participantes", header_labels, header_values, ZOOM_HEADER_IMAGE, session_info)
    return FileResponse(
        file_path, 
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"zoom_{session_type}_report_{meeting_id}.xlsx"
    )
def write_excel_with_header(writer, df, sheet_name, header_labels, header_values, image_path=None, session_info=None):
    # Escribir DataFrame en la fila 13 (startrow=12)
    df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=12)
    worksheet = writer.sheets[sheet_name]
    # Insertar imagen en A1 si existe, ajustada al 70% y alto de la fila 1 a 80px
    if image_path and os.path.exists(image_path):
        from openpyxl.drawing.image import Image as XLImage
        img = XLImage(image_path)
        img.width = int(img.width * 0.7)
        img.height = int(img.height * 0.7)
        img.anchor = "A1"
        worksheet.add_image(img)
        worksheet.row_dimensions[1].height = 70

    # Escribir solo las etiquetas en la fila 2 (no los valores)
    #for col_idx, label in enumerate(header_labels, start=1):
     #   worksheet.cell(row=2, column=col_idx, value=label)
    # Elimina la escritura de valores en la fila 3
    # for col_idx, value in enumerate(header_values, start=1):
    #     worksheet.cell(row=3, column=col_idx, value=value)

    # Escribir datos de sesión en filas 4 a 10 si session_info está presente
    if session_info:
        worksheet.cell(row=4, column=1, value=f"Tema: {session_info.get('topic', '')}")
        worksheet.cell(row=5, column=1, value=f"ID_Meeting: {session_info.get('id', '')}")
        worksheet.cell(row=6, column=1, value=f"Anfitrión: {session_info.get('host_email', '')}")
        worksheet.cell(row=7, column=1, value=f"Anfitrión alternativo: {session_info.get('alternative_hosts', '')}")
        worksheet.cell(row=8, column=1, value=f"Duración sesión: {session_info.get('duration', '')} minutos")
        worksheet.cell(row=9, column=1, value=f"Hora Inicio: {session_info.get('start_time', '')}")
        worksheet.cell(row=10, column=1, value=f"Hora de Cierre: {session_info.get('end_time', '')}")
        worksheet.cell(row=11, column=1, value=f"Total de participantes (únicos): {session_info.get('total_unique_participants', '')}")

    # Ajustar ancho de columnas
    for column in worksheet.columns:
        max_length = 0
        column_letter = column[0].column_letter
        for cell in column:
            try:
                if cell.value and len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except Exception:
                pass
        adjusted_width = min(max_length + 2, 50)
        worksheet.column_dimensions[column_letter].width = adjusted_width

def consolidate_participants(participants):
    """Consolida participantes únicos por email, suma duración y cuenta entradas."""
    consolidated = {}
    for p in participants:
        email = p.get("user_email", "").lower()
        if not email:
            continue
        if email not in consolidated:
            consolidated[email] = p.copy()
            consolidated[email]["duration"] = p.get("duration", 0)
           

            consolidated[email]["Número de Entradas"] = 1
        else:
            consolidated[email]["duration"] += p.get("duration", 0)
            consolidated[email]["Número de Entradas"] += 1
    # Convertir duración a minutos y limpiar duplicados
    result = []
    for p in consolidated.values():
        p["Duración (minutos)"] = round(p["duration"] / 60, 2)
        result.append(p)
    return result

def get_session_info_for_excel(meeting_id: str, session_type: str, headers: dict, total_unique_participants: int = 0) -> dict:
    """
    Obtiene la información relevante de la sesión para el encabezado del Excel.
    """
    if session_type == "meeting":
        url = f"https://api.zoom.us/v2/meetings/{meeting_id}"
    else:
        url = f"https://api.zoom.us/v2/webinars/{meeting_id}"

    print(f"🔗 Consultando endpoint para sesión: {url}")
    res = requests.get(url, headers=headers)
    if res.status_code != 200:
        print(f"❌ Error obteniendo datos de sesión: {res.status_code} - {res.text}")
    session_info = res.json() if res.status_code == 200 else {}

    # Captura el campo alternative_hosts desde settings
    try:
        alternative_hosts = session_info.get("settings", {}).get("alternative_hosts", "")
        print(f"🟢 Valor raw de alternative_hosts: {alternative_hosts}")
        if isinstance(alternative_hosts, list):
            alternative_hosts = ";".join(alternative_hosts)
        print(f"🟢 Valor procesado de alternative_hosts: {alternative_hosts}")
    except Exception as e:
        print(f"❌ Excepción al obtener alternative_hosts: {e}")
        alternative_hosts = ""

    info = {
        "topic": session_info.get("topic", ""),
        "id": session_info.get("id", ""),
        "host_email": session_info.get("host_email", ""),
        "alternative_hosts": alternative_hosts,
        "duration": session_info.get("duration", ""),
        "start_time": session_info.get("start_time", ""),
        "end_time": session_info.get("end_time", ""),
        "total_unique_participants": total_unique_participants
    }
    print(f"🟢 Diccionario final de sesión para Excel: {info}")
    return info