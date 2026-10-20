# 📘 Guía de Uso: API de Evaluación de Ensayos con Rúbrica (MCP HUB Server)

Esta guía describe cómo levantar y utilizar el microservicio de evaluación automatizada de ensayos académicos con rúbricas pedagógicas mediante LLMs con fallback inteligente, soporte nativo para **Canvas LMS** y adaptación genérica a cualquier sistema LMS (Moodle, Blackboard, etc.).

## Autenticación HTTP

### Modo de staging y producción: credenciales individuales

En staging y producción se usan credenciales opacas individuales por
integración, guardadas como hashes en PostgreSQL y limitadas por institución,
tenant y scopes. Configura `API_CLIENT_AUTH_ENABLED=true`; en este modo el
bearer compartido descrito en la sección local no autoriza operaciones.
`GET /health` y `GET /ready` son las únicas rutas públicas.

Para emitir una credencial, configura `DB_DSN` en el entorno seguro del
operador, aplica las migraciones iniciando el HUB y ejecuta el comando
administrativo fuera de banda. La credencial se imprime una sola vez:

```powershell
python -m API.core.manage_api_clients issue `
  --name "Canvas staging" `
  --institution-id "institution-001" `
  --scopes "essays:submit,essays:read,context:read,context:write,llm:chat" `
  --actor "operador-institucional" `
  --expires-at "2027-10-20T00:00:00+00:00"
```

Guarda el token directamente en el gestor de secretos del consumidor. PostgreSQL
conserva únicamente SHA-256. La expiración y el actor son obligatorios; no
incluyas el token en código, documentación, tickets ni logs.

Rota o revoca con el `client_id` devuelto al emitir:

```powershell
python -m API.core.manage_api_clients rotate `
  --client-id "<client-id>" `
  --actor "operador-institucional" `
  --expires-at "2027-10-20T00:00:00+00:00"

python -m API.core.manage_api_clients revoke `
  --client-id "<client-id>" `
  --actor "operador-institucional"
```

Lista metadatos sin secretos; por defecto omite credenciales revocadas:

```powershell
python -m API.core.manage_api_clients list
python -m API.core.manage_api_clients list --include-revoked
```

Cada solicitud debe enviar `X-Institution-ID` igual al valor asignado al token.
Si la credencial tiene `tenant_id`, también debe enviar el mismo
`X-Tenant-ID`. Scopes permitidos: `context:read`, `context:write`,
`context:close`, `context:delete`, `echo:use`, `essays:read`, `essays:submit`,
`llm:chat` y `llm:read`. Los scopes de ensayo
solo se asignan a clientes ligados a una institución porque `evaluation_jobs`
aún no persiste ownership por tenant. El listado global de contextos, Zoom y
MCP remoto están denegados en este modo hasta que tengan aislamiento
verificable. Un `403` indica scope o ámbito insuficiente.

### Modo local heredado

Las instrucciones siguientes que usan `HUB_API_TOKEN` aplican únicamente al
modo local de desarrollo. No publiques ese modo en staging ni en Internet.

Las operaciones de contexto, evaluación, LLM y Zoom requieren un token Bearer
configurado en `HUB_API_TOKEN`. La documentación web (`/docs`, `/redoc` y
`/openapi.json`) usa usuario y contraseña HTTP Basic propios, configurados en
`HUB_DOCS_USERNAME` y `HUB_DOCS_PASSWORD`; no son el Bearer del API. La única
En modo local, `GET /health` y `GET /ready` son públicas.

Configura un secreto aleatorio y privado en el `.env` local o en el gestor de
secretos del entorno:

```ini
HUB_API_TOKEN=<secreto-aleatorio-largo>
HUB_DOCS_USERNAME=<usuario-para-documentacion>
HUB_DOCS_PASSWORD=<contraseña-larga-para-documentacion>
```

No publiques estos valores en el repositorio, documentación, logs ni código
cliente. Si faltan el usuario o la contraseña de documentación, las rutas de
Swagger responden `503`; credenciales Basic incorrectas responden `401`. Si
falta `HUB_API_TOKEN`, las operaciones protegidas responden `503`.

### Abrir Swagger desde el navegador

1. Configura `HUB_DOCS_USERNAME` y `HUB_DOCS_PASSWORD` en el `.env` local o en
   el gestor de secretos del entorno. Son credenciales únicamente para abrir la
   documentación.
2. Configura `HUB_API_TOKEN` por separado. Este token autoriza las llamadas a
   las operaciones del API y no debe ser igual a las credenciales de docs.
3. Reinicia el servidor y abre `http://localhost:10000/docs` (o `/redoc`).
   El navegador solicitará usuario y contraseña HTTP Basic. `/openapi.json`
   también requiere esas credenciales para cargar el esquema.
4. En Swagger, pulsa **Authorize** y proporciona el Bearer token del API.
   Swagger lo enviará en las llamadas a operaciones protegidas. Tener acceso a
   la interfaz no concede por sí solo permisos sobre el API.

Basic Auth no cifra las credenciales; fuera de `localhost`, accede únicamente
mediante HTTPS. No expongas directamente Uvicorn a Internet: utiliza el gateway
o proxy HTTPS aprobado para el entorno.

### Consumo paso a paso

1. **Configura el token en el servidor.** Genera un secreto aleatorio largo con
   un gestor de contraseñas o de secretos. En desarrollo local, guárdalo en
   `.env` (archivo ignorado por Git); en producción, usa el gestor de secretos
   de la plataforma y no un archivo dentro de la imagen o del repositorio.
   Reinicia el servidor después de cambiar la variable: la configuración se
   carga al iniciar el proceso.
2. **Prepara el consumidor autorizado.** Canvas, N8N o el cliente que llama al
   HUB debe recuperar el mismo secreto desde su propio almacén de secretos y
   exponerlo al proceso cliente como la variable `HUB_API_TOKEN`; el ejemplo de
   PowerShell de abajo la utiliza desde el entorno del proceso. No lo incluyas
   en el código fuente, JavaScript del navegador, URLs, payloads, capturas de
   pantalla ni logs.
3. **Inicia el servidor** como se indica en la sección 1. Si `HUB_API_TOKEN` no
   está configurado, el proceso puede iniciar, pero las rutas protegidas
   responden `503`; configura el secreto y reinicia.
4. **Comprueba la disponibilidad** sin enviar credenciales. Solo `GET /health`
   es público:

   ```powershell
   Invoke-RestMethod -Method Get -Uri "http://localhost:10000/health"
   ```

   Debe responder `{"status":"ok"}`. Este endpoint solo confirma que la
   aplicación responde; no valida la conexión con PostgreSQL, Canvas ni los
   proveedores LLM.
5. **Llama una operación protegida** añadiendo el header
   `Authorization: Bearer <token>`. El siguiente ejemplo usa una variable de
   entorno que el cliente debe obtener de forma segura desde su gestor de
   secretos:

```powershell
$headers = @{
    "Content-Type" = "application/json"
    "Authorization" = "Bearer $env:HUB_API_TOKEN"
}
Invoke-RestMethod -Method Post `
  -Uri "http://localhost:10000/llm/chat" `
  -Headers $headers `
  -Body '{"prompt":"Hola"}'
```

6. **Para encolar una evaluación**, envía el header en `/ensayos/enqueue`.
   Sustituye el texto y la rúbrica de ejemplo por los datos de la entrega; el
   payload completo y sus campos admitidos se detallan en la sección 3:

   ```powershell
   $payload = @{
       id_entrega = "ENTREGA_001"
       origen_ensayo = "Texto del ensayo del estudiante..."
       origen_rubrica = @(
           @{
               id = "criterio_1"
               points = 10
               description = "Argumentación y uso de evidencia"
               ratings = @(
                   @{ id = "nivel_1"; points = 10; description = "Argumentación sólida y sustentada" },
                   @{ id = "nivel_2"; points = 5; description = "Argumentación parcial" },
                   @{ id = "nivel_3"; points = 0; description = "No presenta argumentación" }
               )
           }
       )
       proveedor_llm = "modelo"
   } | ConvertTo-Json -Depth 8

   $acuse = Invoke-RestMethod -Method Post `
       -Uri "http://localhost:10000/ensayos/enqueue" `
       -Headers $headers `
       -Body $payload

   $acuse
   ```

   Una respuesta `202 Accepted` significa que se aceptó el trabajo, no que la
   evaluación haya terminado. Guarda el `evaluation_id` devuelto.
7. **Consulta el resultado** usando el mismo header y el identificador recibido:

   ```powershell
   $evaluationId = $acuse.evaluation_id
   Invoke-RestMethod -Method Get `
       -Uri "http://localhost:10000/ensayos/estado/$evaluationId" `
       -Headers $headers
   ```

   Si el estado es `PENDING` o `EVALUATING`, espera y vuelve a consultar. Usa el
   resultado canónico cuando el trabajo termine; no trates el acuse `202` como
   una calificación.
8. **Usa Swagger opcionalmente** en `/docs`: primero autentícate con las
   credenciales Basic del paso «Abrir Swagger desde el navegador»; después pulsa
   **Authorize** y pega el valor del Bearer token del API (sin escribir
   `Bearer `). Son credenciales distintas. Luego ejecuta la operación protegida.

**Respuestas de autenticación**

| HTTP | Significado | Acción |
|---|---|---|
| `401 Unauthorized` | Falta o no coincide el Bearer del API en una operación, o el usuario/contraseña Basic no coincide en documentación. | Verifica qué credencial corresponde al recurso solicitado. |
| `403 Forbidden` | Reservado para controles de autorización/permisos cuando se habiliten. | Solicita al administrador los permisos necesarios; no compartas credenciales de mayor privilegio. |
| `503 Service Unavailable` | Falta `HUB_API_TOKEN` para operaciones o falta `HUB_DOCS_USERNAME`/`HUB_DOCS_PASSWORD` para documentación. | Configura el secreto correspondiente y reinicia la aplicación. |

El MCP remoto iniciado con `--http` requiere el mismo header Bearer. Para STDIO,
que no es HTTP, el acceso depende de quién pueda ejecutar el proceso y acceder
a su configuración local. El token compartido actual es una protección inicial:
no lo reutilices entre clientes no confiables; la gestión de tokens separados
por consumidor y su revocación individual están previstas en el plan de seguridad.

---

## 1. 🚀 Levantar el Servidor

Asegúrate de estar en la carpeta raíz del proyecto (`MCP_HUB_SERVER_IA`) y ejecuta:

```powershell
uv run uvicorn API.server:app --reload --port 10000
```

- **URL Base:** `http://localhost:10000`
- **Healthcheck público:** `http://localhost:10000/health`
- **Documentación Interactiva (Swagger UI):** `http://localhost:10000/docs`
- **Documentación Alternativa (Redoc):** `http://localhost:10000/redoc`

> [!NOTE]
> El servidor utiliza las credenciales configuradas en el archivo `.env` de la raíz (`OPENROUTER_API_KEY`, etc.). Si se usa el proveedor `"modelo"`, se activa el sistema de **fallback automático gratuito** con alta disponibilidad entre modelos optimizados de OpenRouter (`nvidia/nemotron-3-nano` ➔ `google/gemma-3-27b-it` ➔ `qwen/qwen-2.5-72b-instruct`).

---

## 2. 📍 Endpoints Disponibles

| Método | Ruta | Descripción |
| :--- | :--- | :--- |
| `POST` | `/context/init` | **Inicializar Contexto Institucional**: Registra sesión institucional y genera `context_id` para reusar en todas las herramientas sin reenviar prompts pesados. |
| `GET` | `/context/{context_id}` | Consulta el estado y las ranuras activas de una sesión de contexto institucional. |
| `POST` | `/context/{context_id}/slot/{slot_name}` | Actualiza o enriquece una ranura de herramienta (`lms`, `zoom`, `crm`, `custom`). |
| `POST` | `/context/{context_id}/close` | Cierra una actividad LMS e inicia su período de gracia configurable. |
| `DELETE` | `/context/{context_id}` | Elimina una sesión de contexto institucional. |
| `GET` | `/context/active/current` | Inspecciona el contexto inyectado automáticamente en la solicitud actual. |
| `GET` | `/health` | Healthcheck mínimo público; no requiere autenticación. |
| `POST` | `/ensayos/evaluar-flujo-completo` | **Evaluador síncrono**: procesa la entrega y devuelve el resultado completo al terminar. |
| `POST` | `/ensayos/webhook-canvas` | **Webhook asíncrono Canvas LMS**: recibe una entrega, la encola y responde `202 Accepted` con `evaluation_id` y correlación. |
| `POST` | `/ensayos/enqueue` | Encola una evaluación directa y responde `202 Accepted`, sin esperar al LLM. |
| `GET` | `/ensayos/estado/{evaluation_id}` | Consulta el estado o recupera el resultado canónico de una evaluación encolada. |
| `POST` | `/llm/chat` | Chat directo con LLMs con soporte para contexto institucional, fallback OpenRouter (`modelo`) y On-Premise (`ollama`). |
| `GET` | `/llm/ollama/status` | Verifica disponibilidad y conexión con el servidor On-Premise local de Ollama. |
| `GET` | `/llm/ollama/models` | Lista modelos instalados en la instancia local de Ollama. |
| `GET` | `/llm/models/free` | Consulta en vivo el catálogo dinámico de modelos gratuitos de OpenRouter con context length, reasoning y ranking. |
| `POST` | `/llm/models/refresh` | Fuerza la actualización inmediata de la caché de modelos gratuitos de OpenRouter. |
| `GET` | `/docs` | Interfaz interactiva de Swagger UI protegida por usuario y contraseña HTTP Basic; las operaciones requieren Bearer token. |

---

## 3. 📝 Estructura del Payload de Entrada

El servicio admite el payload nativo del **Webhook de Canvas LMS** (con archivo o texto) y el formato directo explícito. El webhook Canvas y `/ensayos/enqueue` encolan el trabajo y responden `202`; `/ensayos/evaluar-flujo-completo` conserva el procesamiento síncrono para integraciones que necesitan esperar el resultado en la misma petición.

### 3.1. Modalidad A: Payload Nativo de Webhook Canvas LMS

Este formato es emitido directamente por integraciones de Canvas LMS (o flujos intermedios en N8N / Webhooks):

#### Caso 1: Rúbrica + Archivo Adjunto descargable (`attachment.url` para Word .docx o PDF)
```json
{
  "success": true,
  "assignment": {
    "id": "992586",
    "courseId": "79871",
    "name": "[APEB1-30%] Investigación y propuesta: Analice la situación actual de una empresa...",
    "pointsPossible": "10.00",
    "rubric": [
      {
        "id": "_2541",
        "points": 2.5,
        "description": "Contextualización de la institución",
        "ratings": [
          { "id": "blank", "points": 2.5, "description": "Excelente" },
          { "id": "_5282", "points": 2.0, "description": "Muy bueno" }
        ]
      }
    ]
  },
  "submission": {
    "id": "57423885",
    "type": "online_upload",
    "userId": "182199",
    "score": "7.75"
  },
  "attachment": {
    "id": "24498662",
    "name": "TAREA ADMINISTRACIÓN UNIDAD 3 PLANEACIÓN ARIANA ALMEIDA.docx",
    "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "size": "77585",
    "url": "https://utpl.test.instructure.com/files/24498662/download?download_frd=1&verifier=H4dCJL9zTwxr72n8xh8gvpz5RtZ8TDF4JI6TDJDl",
    "txt": null
  },
  "ai": {
    "score": null,
    "feedback": null,
    "result": null,
    "status": "PENDING"
  }
}
```

#### Caso 2: Rúbrica + Texto directo (`online_text_entry` en `submission.body` o `attachment.txt`)
Para tareas donde el alumno redacta directamente en el editor web de Canvas:
```json
{
  "success": true,
  "assignment": {
    "id": "992586",
    "courseId": "79871",
    "name": "Actividad de Redacción en Línea",
    "pointsPossible": "10.00",
    "rubric": [ ... ]
  },
  "submission": {
    "id": "57423885",
    "type": "online_text_entry",
    "userId": "182199",
    "body": "El presente trabajo analiza la empresa local 'Café Lojano'..."
  },
  "attachment": {
    "url": null,
    "txt": "El presente trabajo analiza la empresa local 'Café Lojano'..."
  }
}
```

---

### 3.2. Modalidad B: Formato Directo Explícito (`origen_ensayo` + `origen_rubrica`)

```json
{
  "s.course_id": "CURSO_IA_101",
  "s.user_id_canvas": "USR_89432",
  "g.assignment_id": "ACT_ENSAYO_FINAL",
  "g.submission_id": "SUB_2026_0987",
  "g.submission_type": "online_upload",
  "id_lms": "CANVAS_PROD",
  "origen_ensayo": "El presente ensayo analiza el impacto de la planeación estratégica en la toma de decisiones empresariales...",
  "origen_rubrica": [
    {
      "id": "_830",
      "points": 2.5,
      "description": "Presenta el contexto y problemática de la empresa",
      "long_description": null,
      "ignore_for_scoring": false,
      "criterion_use_range": false,
      "ratings": [
        { "id": "blank", "points": 2.5, "description": "Cumple", "long_description": "" },
        { "id": "blank_2", "points": 0.0, "description": "No cumple", "long_description": "" }
      ]
    },
    {
      "id": "_7089",
      "points": 2.5,
      "description": "Analiza cómo la planeación influye en la formulación de decisiones estratégicas",
      "long_description": null,
      "ignore_for_scoring": false,
      "criterion_use_range": false,
      "ratings": [
        { "id": "_2068", "points": 2.5, "description": "Cumple", "long_description": "" },
        { "id": "_2159", "points": 0.0, "description": "No cumple", "long_description": "" }
      ]
    }
  ],
  "id_entrega": "SUB_2026_0987",
  "proveedor_llm": "modelo",
  "instrucciones_adicionales": "Evaluar con enfoque pedagógico constructivo y citar evidencia textual del estudiante.",
  "canal_entrega": "local",
  "webhook_lms_url": ""
}
```

---

### 3.3. Modalidad C: Patrón Cero Duplicación (Zero-Duplication Webhook - Máximo Ahorro)

En entornos de producción masiva con cientos de estudiantes por aula, enviar la rúbrica completa (`assignment.rubric`), la descripción logística de la actividad (`assignment.description`) y los datos del curso en cada webhook de cada estudiante representa un desperdicio enorme de ancho de banda y tokens.

Para evitar repetir datos comunes, el HUB comparte y persiste automáticamente el contexto por `institution_id`, `course_id` y `assignment_id`. La primera entrega del lote debe aportar la asignación/rúbrica, o la integración debe haberla precargado en el contexto LMS. Las siguientes entregas pueden omitir descripción y rúbrica, pero deben conservar los IDs de institución, curso y tarea para que el HUB resuelva el mismo contexto:

```json
{
  "institution_id": "UNIVERSIDAD_TECNICA_PARTICULAR_DE_LOJA_UTPL",
  "course_id": "79871",
  "assignment_id": "992586",
  "submission": {
    "id": "57423885",
    "userId": "182199",
    "type": "online_upload"
  },
  "attachment": {
    "name": "TAREA_FODA_ESTUDIANTE.docx",
    "url": "https://utpl.test.instructure.com/files/24498662/download?download_frd=1&verifier=H4dCJL9zTwxr72n8xh8gvpz5RtZ8TDF4JI6TDJDl"
  }
}
```

La primera solicitud puede usar el payload Canvas completo de la sección 3.1; las solicitudes posteriores del mismo lote reutilizan el contexto persistido. Para el flujo de evaluación LMS, la identidad compartida se deriva de institución/curso/tarea; un `X-Context-ID` arbitrario no sustituye ni cambia esa identidad. El header sigue disponible para las demás rutas que usan el middleware de contexto institucional.

*El HUB descarga el archivo .docx/.pdf del estudiante, obtiene la rúbrica del contexto compartido y encola el trabajo. La respuesta inicial es `202 Accepted`; consulta luego `/ensayos/estado/{evaluation_id}` para recibir la calificación.*

---

## 4. 🔍 Detalle Exhaustivo de Parámetros

### A. Variables de Trazabilidad e Integración LMS

Todas las variables son opcionales y admiten múltiples alias para integrarse sin fricción con consultas SQL, payloads nativos de Canvas o Moodle:

| Variable | Tipo | Default | Alias Aceptados | Descripción |
| :--- | :---: | :---: | :--- | :--- |
| `course_id` | `string` | `null` | `s.course_id`, `course_id`, `id_curso`, `ID_CURSO`, `Id_curso` | Identificador del curso en el LMS. |
| `user_id_canvas` | `string` | `null` | `s.user_id_canvas`, `user_id_canvas`, `id_usuario`, `user_id`, `ID_USUARIO`, `Id_usuario` | Identificador único o ID numérico del estudiante en el LMS. |
| `assignment_id` | `string` | `null` | `g.assignment_id`, `assignment_id`, `id_asignacion`, `id_actividad`, `id_tarea`, `ID_ACTIVIDAD` | Identificador de la tarea o asignación. |
| `assignment_name` | `string` | `null` | `assignment_name`, `titulo_actividad`, `nombre_actividad` | Nombre o título de la actividad académica en el LMS. |
| `submission_id` | `string` | `null` | `g.submission_id`, `submission_id`, `id_submision`, `id_submission`, `ID_SUBMISION`, `Id_submision` | Identificador único del envío del estudiante. |
| `submission_type` | `string` | `null` | `g.submission_type`, `submission_type`, `tipo_envio`, `tipo_submision`, `TIPO_ENVIO` | Tipo de entrega (`online_upload`, `online_text_entry`, `online_url`, etc.). |
| `id_lms` | `string` | `"CANVAS"` | `id_lms`, `ID_LMS`, `sistema_lms` | Nombre del LMS (`Canvas`, `Moodle`, `Blackboard`, etc.). |

> [!TIP]
> **Flexibilidad de Entrada**:
> Puedes enviar estas variables como campos planos en la raíz del JSON (ej: `"s.course_id": "101"`), o agrupadas dentro de un objeto `"datos_lms": { "course_id": "101", ... }`. El backend las normaliza automáticamente en ambos casos.

---

### B. Parámetros de Ensayo, Rúbrica y Configuración

| Variable | Tipo | Obligatorio | Valores Soportados | Descripción |
| :--- | :---: | :---: | :--- | :--- |
| `origen_ensayo` | `string` | **Sí** | • Texto plano directo.<br>• Ruta a archivo local (`.docx`, `.pdf`, `.txt`).<br>• Cadena Base64 (`data:...;base64,...`).<br>• URL HTTP/HTTPS de descarga. | Contenido o referencia al ensayo del estudiante. |
| `origen_rubrica` | `list`, `dict` o `string` | **Sí** | • Lista JSON nativa de Canvas LMS.<br>• Diccionario JSON de rúbrica genérica.<br>• Ruta a archivo local (`.docx`, `.pdf`, `.json`, `.txt`).<br>• Cadena Base64 (`data:...;base64,...`).<br>• URL de descarga.<br>• Texto plano con descripción de criterios. | Contenido o referencia a la rúbrica evaluadora. |
| `id_entrega` | `string` | No | `string` libre (ej: `"SUB_001"`). | Carpeta de almacenamiento local en `storage/evaluaciones/`. Si no se envía pero se provee `submission_id`, se sincroniza con este último. |
| `proveedor_llm` | `string` | No | • `"modelo"` (Default - Fallback gratuito OpenRouter).<br>• `"gemma"` / `"gemini"` (Google).<br>• `"deepseek"` (DeepSeek Chat).<br>• `"openai"` (OpenAI GPT-4o). | Motor de inteligencia artificial para la evaluación. |
| `instrucciones_adicionales` | `string` | No | Texto libre. | Indicaciones pedagógicas complementarias para guiar la corrección del LLM. |
| `canal_entrega` | `string` | No | • `"local"` (Default - Guarda JSON y Markdown en disco).<br>• `"webhook"` (Envía el resultado vía HTTP POST a `webhook_lms_url`).<br>• `"todos"` (Guarda en disco y notifica al webhook). | Canal de persistencia y notificación de la evaluación. |
| `webhook_lms_url` | `string` | No | URL válida `https://...` | Endpoint de destino si `canal_entrega` es `"webhook"` o `"todos"`. |

---

## 5. 🧩 Modelos Pydantic de Rúbricas (Canvas y Genéricas)

El sistema cuenta con un modelo Pydantic polimórfico [RubricaGenerica](file:///c:/Users/jeguaman2/IA_GENERATIVA/MCP_HUB_SERVER_IA/API/services/essay_evaluator_mcp.py#L79-L138) que valida y procesa tanto la estructura nativa de Canvas LMS como rúbricas genéricas de cualquier otro entorno.

### A. Estructura Pydantic Interna

```python
class NivelRatingRubrica(BaseModel):
    id: Optional[str]               # ID en Canvas/LMS (ej: 'blank', '_2068')
    points: float                   # Puntaje asignado al nivel (ej: 2.5)
    description: str                # Nombre o resumen del nivel (ej: 'Cumple', 'No cumple')
    long_description: Optional[str] # Detalle descriptivo del nivel

class CriterioRubrica(BaseModel):
    id: Optional[str]                     # ID del criterio (ej: '_830')
    points: float                         # Puntaje máximo del criterio
    description: str                      # Título o enunciado del criterio
    long_description: Optional[str]       # Guía pedagógica extendida
    ignore_for_scoring: Optional[bool]    # Si es True, no suma a la nota final
    criterion_use_range: Optional[bool]   # Si admite rangos intermedios
    ratings: List[NivelRatingRubrica]     # Escala de niveles de desempeño

class RubricaGenerica(BaseModel):
    titulo: Optional[str]                 # Título general de la rúbrica
    points_possible: Optional[float]      # Escala máxima total
    criterios: List[CriterioRubrica]      # Lista de criterios
```

### B. Cálculo Dinámico de Escala de Calificación

El evaluador **no impone una escala rígida de 20 puntos**. En su lugar:
1. `RubricaGenerica.calcular_escala_maxima()` suma los `points` de cada criterio (omitiendo aquellos con `ignore_for_scoring = True`).
2. Si la rúbrica de Canvas suma 5 puntos, la escala máxima será 5.0. Si suma 10 o 100 puntos, la escala máxima será exactamente 10.0 o 100.0.
3. El LLM es instruido dinámicamente para calificar dentro de los límites y niveles exactos de la rúbrica.

---

## 6. 🎛 Ejemplos JSON Completos para Cada Caso de Uso

A continuación se presentan los payloads JSON completos y listos para copiar y pegar en Swagger UI, Postman o cualquier cliente HTTP para cada una de las modalidades admitidas.

```mermaid
graph TD
    A["origen_rubrica"] --> B{¿Es Lista o Dict JSON?}
    B -- Sí --> C["Valida con Pydantic RubricaGenerica<br>Conserva IDs de Criterios y Ratings<br>Calcula Escala Dinámica"]
    B -- No --> D{¿Es Archivo Local o URL?}
    D -- Sí: .docx / .pdf / .json / .txt --> E["document_parser extrae texto y tablas<br>Si es .json, se valida como RubricaGenerica"]
    D -- No --> F{¿Es Binario en Base64?}
    F -- Sí: data:...;base64,... --> G["document_parser decodifica en memoria<br>Extrae texto estructurado y tablas"]
    F -- No --> H["Se procesa como Texto Plano Pedagógico"]
```

---

### Caso 1: Rúbrica Nativa Canvas LMS con Trazabilidad Completa (`s.*` y `g.*`)
> **Uso típico**: Integración estándar con Canvas LMS. Envía la rúbrica como una lista de criterios JSON con sus ratings y los identificadores de trazabilidad con prefijos de tabla (`s.course_id`, `g.assignment_id`, etc.). El ensayo se envía como texto plano directo.

```json
{
  "s.course_id": "CURSO_CANVAS_101",
  "s.user_id_canvas": "EST_89432",
  "g.assignment_id": "ACT_ENSAYO_FINAL",
  "g.submission_id": "SUB_CANVAS_7788",
  "g.submission_type": "online_upload",
  "id_lms": "CANVAS_PROD",
  "origen_ensayo": "El presente ensayo analiza el impacto de la planeación estratégica en la toma de decisiones empresariales. Se examinan los factores internos y externos que condicionan la sostenibilidad de las organizaciones en entornos competitivos. Se concluye que sin una planificación rigurosa, las decisiones operativas carecen de dirección...",
  "origen_rubrica": [
    {
      "id": "_830",
      "points": 2.5,
      "description": "Presenta el contexto y problemática de la empresa",
      "long_description": "El estudiante describe con claridad el entorno empresarial y formula la problemática central.",
      "ignore_for_scoring": false,
      "criterion_use_range": false,
      "ratings": [
        {
          "id": "blank",
          "points": 2.5,
          "description": "Cumple",
          "long_description": "Describe exhaustivamente el contexto y formula con precisión el problema."
        },
        {
          "id": "blank_2",
          "points": 0.0,
          "description": "No cumple",
          "long_description": "Omite el contexto o la problemática carece de relación con el tema."
        }
      ]
    },
    {
      "id": "_7089",
      "points": 2.5,
      "description": "Analiza cómo la planeación influye en la formulación de decisiones estratégicas",
      "long_description": "Demuestra pensamiento crítico vinculando los objetivos de largo plazo con decisiones tácticas.",
      "ignore_for_scoring": false,
      "criterion_use_range": false,
      "ratings": [
        {
          "id": "_2068",
          "points": 2.5,
          "description": "Cumple",
          "long_description": "Analiza con profundidad la influencia estratégica de la planeación."
        },
        {
          "id": "_2159",
          "points": 0.0,
          "description": "No cumple",
          "long_description": "No evidencia análisis de la influencia en las decisiones."
        }
      ]
    }
  ],
  "id_entrega": "SUB_CANVAS_7788",
  "proveedor_llm": "modelo",
  "instrucciones_adicionales": "Evaluar con enfoque pedagógico formativo y citar evidencia textual del ensayo.",
  "canal_entrega": "local"
}
```

---

### Caso 2: Rúbrica Genérica LMS (Estructura Objeto con Criterios en Español o Inglés)
> **Uso típico**: Plataformas como Moodle, Blackboard, Google Classroom o sistemas propios donde la rúbrica viene estructurada como un objeto que agrupa un título general y la lista de criterios.

```json
{
  "id_lms": "MOODLE_PROD",
  "course_id": "MDL_BIO_2026",
  "user_id_canvas": "ALU_4501",
  "assignment_id": "TAREA_GENETICA_02",
  "submission_id": "ENVIO_MDL_9021",
  "submission_type": "online_text_entry",
  "origen_ensayo": "Las terapias de edición genética mediante CRISPR-Cas9 representan un cambio de paradigma en la medicina moderna. Esta tecnología permite modificar secuencias de ADN con alta precisión, abriendo posibilidades terapéuticas para enfermedades monogénicas. Sin embargo, surgen dilemas bioéticos cruciales en relación con la modificación de la línea germinal humana...",
  "origen_rubrica": {
    "titulo": "Rúbrica de Ensayo Científico y Bioética",
    "points_possible": 20.0,
    "criterios": [
      {
        "id": "crit_marco_teorico",
        "points": 5.0,
        "description": "Rigor conceptual y fundamentación teórica",
        "long_description": "Uso preciso de terminología científica y referencias bibliográficas actualizadas.",
        "ratings": [
          { "id": "rat_teoria_alta", "points": 5.0, "description": "Sobresaliente: Marco teórico riguroso y actualizado" },
          { "id": "rat_teoria_media", "points": 3.0, "description": "Aceptable: Conceptos correctos pero referencias limitadas" },
          { "id": "rat_teoria_baja", "points": 1.0, "description": "Insuficiente: Errores conceptuales notorios" }
        ]
      },
      {
        "id": "crit_argumentacion",
        "points": 10.0,
        "description": "Profundidad argumentativa y dilemas bioéticos",
        "long_description": "Capacidad crítica para sopesar beneficios médicos frente a riesgos éticos.",
        "ratings": [
          { "id": "rat_arg_alta", "points": 10.0, "description": "Excelente análisis crítico y ponderación ética" },
          { "id": "rat_arg_media", "points": 6.5, "description": "Argumentación básica con poca discusión de dilemas" },
          { "id": "rat_arg_baja", "points": 2.0, "description": "Argumentación superficial sin justificación ética" }
        ]
      },
      {
        "id": "crit_conclusiones",
        "points": 5.0,
        "description": "Síntesis y conclusiones prospectivas",
        "ratings": [
          { "id": "rat_conc_alta", "points": 5.0, "description": "Conclusiones coherentes y prospectiva sólida" },
          { "id": "rat_conc_baja", "points": 1.5, "description": "Conclusiones desconectadas del cuerpo del ensayo" }
        ]
      }
    ]
  },
  "proveedor_llm": "modelo",
  "canal_entrega": "local"
}
```

---

### Caso 3: Documentos en Archivos Locales en Disco (`.docx` o `.pdf`)
> **Uso típico**: Procesamiento por lotes o scripts locales donde los ensayos de los alumnos y/o las rúbricas se encuentran descargados en el sistema de archivos del servidor.

```json
{
  "s.course_id": "CURSO_DERECHO_301",
  "s.user_id_canvas": "EST_33091",
  "g.assignment_id": "ENSAYO_JURISPRUDENCIA",
  "g.submission_id": "SUB_DISCO_001",
  "g.submission_type": "online_upload",
  "id_lms": "CANVAS_PROD",
  "origen_ensayo": "C:\\evaluaciones\\entregas\\ensayo_estudiante_33091.docx",
  "origen_rubrica": "C:\\evaluaciones\\rubricas\\rubrica_evaluacion_derecho.docx",
  "id_entrega": "SUB_DISCO_001",
  "proveedor_llm": "modelo",
  "canal_entrega": "local"
}
```

> [!NOTE]
> El extractor `document_parser.py` detecta automáticamente la extensión del archivo, lee los párrafos e interpreta todas las tablas tabuladas del documento DOCX o PDF sin necesidad de configuración adicional.

---

### Caso 4: Documentos Binarios en Base64 (Sin guardar en disco)
> **Uso típico**: Microservicios, arquitecturas sin servidor (AWS Lambda, Google Cloud Run) o webhooks que transmiten archivos binarios directamente en memoria en formato Data URI RFC 2397 o Base64 estándar.

```json
{
  "s.course_id": "CURSO_CONTABILIDAD_202",
  "s.user_id_canvas": "EST_77123",
  "g.assignment_id": "AUDITORIA_FINAL",
  "g.submission_id": "SUB_B64_5521",
  "g.submission_type": "online_upload",
  "id_lms": "CANVAS_PROD",
  "origen_ensayo": "data:application/pdf;base64,JVBERi0xLjQKJcfsj6IKMSAwIG9iago8PAovVHlwZSAvQ2F0YWxvZwovUGFnZXMgMiAwIFI...",
  "origen_rubrica": "data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,UEsDBBQAAAAIAI123456...",
  "proveedor_llm": "modelo",
  "canal_entrega": "local"
}
```

> [!TIP]
> Puedes enviar el binario con el prefijo Data URI (ej. `data:application/pdf;base64,...`) o como cadena Base64 pura. El motor detecta los marcadores binarios de formato ZIP (`.docx`) y cabeceras PDF de forma autónoma.

---

### Caso 5: Ensayo y Rúbrica en Texto Plano Directo (Formato Rápido / Docente)
> **Uso típico**: Evaluaciones ágiles, pruebas interactivas en Swagger o cuando el docente redacta la rúbrica de forma cualitativa sin IDs de sistema.

```json
{
  "s.course_id": "CURSO_LITERATURA_101",
  "s.user_id_canvas": "USR_5502",
  "g.assignment_id": "ENSAYO_DON_QUIJOTE",
  "g.submission_id": "ENTREGA_TEXTO_01",
  "id_lms": "CANVAS",
  "origen_ensayo": "En la novela Don Quijote de la Mancha, Cervantes contrapone el idealismo caballeresco con el pragmatismo de Sancho Panza. A lo largo de la obra se evidencia cómo la locura quijotesca actúa como un mecanismo de subversión frente a la rígida estructura social del Siglo de Oro español. Los diálogos entre el caballero andante y su escudero reflejan una dialéctica permanente entre ilusión y realidad...",
  "origen_rubrica": "Criterio 1: Contextualización histórica y literaria de la obra (Máximo 5 puntos).\nCriterio 2: Análisis crítico y dialéctica de los personajes principales (Máximo 10 puntos).\nCriterio 3: Coherencia argumentativa, redacción y estilo académico (Máximo 5 puntos).",
  "proveedor_llm": "modelo",
  "instrucciones_adicionales": "Enfatizar en la coherencia de las citas y la corrección gramatical.",
  "canal_entrega": "local"
}
```

---

### Caso 6: Trazabilidad Agrupada en el Sub-objeto `datos_lms`
> **Uso típico**: Cuando el cliente o middleware prefiere encapsular los metadatos institucionales dentro de un objeto JSON estructurado en vez de enviar campos planos en la raíz.

```json
{
  "datos_lms": {
    "id_lms": "CANVAS_INSTITUCIONAL",
    "course_id": "CURSO_INGENIERIA_501",
    "user_id_canvas": "ING_8849",
    "assignment_id": "PROYECTO_CAPSTONE",
    "submission_id": "SUB_NESTED_091",
    "submission_type": "online_upload"
  },
  "origen_ensayo": "El presente informe técnico describe la arquitectura de microservicios desarrollada para la gestión de datos distribuidos en alta concurrencia. Se emplearon patrones como Circuit Breaker y colas asíncronas para garantizar resiliencia...",
  "origen_rubrica": [
    {
      "id": "_capstone_arch",
      "points": 10.0,
      "description": "Viabilidad y resiliencia de la arquitectura de software",
      "ratings": [
        { "id": "r_alto", "points": 10.0, "description": "Arquitectura modular y tolerante a fallos" },
        { "id": "r_medio", "points": 6.0, "description": "Arquitectura funcional pero con puntos únicos de fallo" },
        { "id": "r_bajo", "points": 2.0, "description": "Diseño monolítico sin justificación" }
      ]
    },
    {
      "id": "_capstone_test",
      "points": 10.0,
      "description": "Estrategia de pruebas y métricas de rendimiento",
      "ratings": [
        { "id": "r2_alto", "points": 10.0, "description": "Pruebas de estrés y benchmarking documentados" },
        { "id": "r2_bajo", "points": 3.0, "description": "Sin métricas claras ni pruebas de carga" }
      ]
    }
  ],
  "proveedor_llm": "modelo",
  "canal_entrega": "local"
}
```

---

### Caso 7: Descarga Remota desde URLs Públicas (HTTP / HTTPS)
> **Uso típico**: Cuando el LMS o un servicio cloud (Amazon S3, Google Cloud Storage, Azure Blob) genera URLs públicas o prefirmadas temporales para acceder a los archivos.

```json
{
  "s.course_id": "CURSO_HISTORIA_404",
  "s.user_id_canvas": "EST_2291",
  "g.assignment_id": "ENSAYO_GUERRA_FRIA",
  "g.submission_id": "SUB_URL_8830",
  "g.submission_type": "online_url",
  "id_lms": "CANVAS",
  "origen_ensayo": "https://storage.universidad.edu/entregas/2026/ensayo_estudiante_2291.pdf",
  "origen_rubrica": "https://storage.universidad.edu/rubricas/rubrica_historia_2026.json",
  "proveedor_llm": "modelo",
  "canal_entrega": "local"
}
```

---

### Caso 8: Notificación Automática vía Webhook (`canal_entrega: "webhook"` o `"todos"`)
> **Uso típico**: Flujos totalmente desacoplados. Tras finalizar la evaluación con IA, el servidor ejecuta un `POST` HTTP enviando el resultado completo a la URL del webhook especificada.

```json
{
  "s.course_id": "CURSO_ECONOMIA_201",
  "s.user_id_canvas": "EST_99014",
  "g.assignment_id": "ASG_POLITICA_MONETARIA",
  "g.submission_id": "SUB_WEBHOOK_110",
  "g.submission_type": "online_upload",
  "id_lms": "CANVAS_PROD",
  "origen_ensayo": "El impacto de la elevación de tasas de interés por parte del banco central incide directamente en la contracción del crédito de consumo y la desaceleración de la inflación...",
  "origen_rubrica": [
    {
      "id": "_rub_eco_01",
      "points": 5.0,
      "description": "Dominio de modelos macroeconómicos",
      "ratings": [
        { "id": "nivel_optimo", "points": 5.0, "description": "Aplica modelos macroeconómicos con precisión" },
        { "id": "nivel_deficiente", "points": 1.0, "description": "Errores graves en la interpretación de variables" }
      ]
    }
  ],
  "canal_entrega": "todos",
  "webhook_lms_url": "https://mi-lms.universidad.edu/api/v1/webhooks/recepcion-calificaciones",
  "proveedor_llm": "modelo"
}
```

---

## 7. 📤 Estructura de la Respuesta JSON

La respuesta contiene la calificación final, los comentarios cualitativos, las evidencias textuales citadas y el bloque nativo `rubric_assessment_canvas`:

```json
{
  "pipeline": "completado",
  "status": "success",
  "modelo_utilizado": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
  
  "datos_lms": {
    "id_lms": "CANVAS_PROD",
    "course_id": "CURSO_CANVAS_101",
    "user_id_canvas": "EST_99482",
    "assignment_id": "ASG_ENSAYO_01",
    "submission_id": "SUB_CANVAS_7788",
    "submission_type": "online_upload"
  },

  "id_lms": "CANVAS_PROD",
  "course_id": "CURSO_CANVAS_101",
  "user_id_canvas": "EST_99482",
  "assignment_id": "ASG_ENSAYO_01",
  "submission_id": "SUB_CANVAS_7788",
  "submission_type": "online_upload",
  "id_entrega": "SUB_CANVAS_7788",

  "nota_final": 5.0,
  "escala_maxima": 5.0,

  "criterios": [
    {
      "id_criterio": "_830",
      "criterio": "Presenta el contexto y problemática de la empresa",
      "id_nivel": "blank",
      "nivel_alcanzado": "Cumple",
      "puntaje_obtenido": 2.5,
      "puntaje_maximo": 2.5,
      "justificacion": "El ensayo introduce claramente la problemática organizacional analizada.",
      "evidencias_en_texto": [
        "El presente ensayo analiza el contexto y planeación estratégica..."
      ]
    },
    {
      "id_criterio": "_7089",
      "criterio": "Analiza cómo la planeación influye en la formulación de decisiones estratégicas",
      "id_nivel": "_2068",
      "nivel_alcanzado": "Cumple",
      "puntaje_obtenido": 2.5,
      "puntaje_maximo": 2.5,
      "justificacion": "Se demuestra la relación causa-efecto entre plan estratégico y decisiones operativas.",
      "evidencias_en_texto": [
        "Se concluye que las decisiones empresariales dependen del diagnóstico inicial..."
      ]
    }
  ],

  "rubric_assessment_canvas": {
    "_830": {
      "points": 2.5,
      "rating_id": "blank",
      "comments": "El ensayo introduce claramente la problemática organizacional analizada."
    },
    "_7089": {
      "points": 2.5,
      "rating_id": "_2068",
      "comments": "Se demuestra la relación causa-efecto entre plan estratégico y decisiones operativas."
    }
  },

  "fortalezas": [
    "Comprensión clara de los conceptos de planeación estratégica.",
    "Adecuada contextualización de la problemática empresarial."
  ],
  "areas_mejora": [
    "Profundizar en casos empíricos y cursos de acción específicos."
  ],
  "resumen_retroalimentacion": "El estudiante presenta un análisis ordenado y coherente que cumple satisfactoriamente con los criterios de la rúbrica.",
  "recomendaciones": "Revisar lecturas sobre planeación táctica y operativa para complementar futuras entregas.",

  "documentos_info": {
    "ensayo_tipo": "text",
    "ensayo_palabras": 85,
    "rubrica_tipo": "lms_rubric_json"
  },

  "evaluacion": {
    "id_entrega": "SUB_CANVAS_7788",
    "estudiante": "EST_99482",
    "titulo_ensayo": "Impacto de la planeación estratégica en la toma de decisiones empresariales",
    "nota_final": 5.0,
    "escala_maxima": 5.0,
    "criterios": [
      {
        "id_criterio": "_830",
        "id_nivel": "blank",
        "criterio": "Presenta el contexto y problemática de la empresa",
        "nivel_alcanzado": "Cumple",
        "puntaje_obtenido": 2.5,
        "puntaje_maximo": 2.5,
        "justificacion": "El ensayo introduce claramente la problemática organizacional analizada.",
        "evidencias_en_texto": [
          "El presente ensayo analiza el contexto y planeación estratégica..."
        ]
      },
      {
        "id_criterio": "_7089",
        "id_nivel": "_2068",
        "criterio": "Analiza cómo la planeación influye en la formulación de decisiones estratégicas",
        "nivel_alcanzado": "Cumple",
        "puntaje_obtenido": 2.5,
        "puntaje_maximo": 2.5,
        "justificacion": "Se demuestra la relación causa-efecto entre plan estratégico y decisiones operativas.",
        "evidencias_en_texto": [
          "Se concluye que las decisiones empresariales dependen del diagnóstico inicial..."
        ]
      }
    ],
    "fortalezas": [
      "Comprensión clara de los conceptos de planeación estratégica.",
      "Adecuada contextualización de la problemática empresarial."
    ],
    "areas_mejora": [
      "Profundizar en casos empíricos y cursos de acción específicos."
    ],
    "resumen_retroalimentacion": "El estudiante presenta un análisis ordenado y coherente que cumple satisfactoriamente con los criterios de la rúbrica.",
    "recomendaciones": [
      "Revisar lecturas sobre planeación táctica y operativa para complementar futuras entregas."
    ],
    "rubric_assessment_canvas": {
      "_830": { "points": 2.5, "rating_id": "blank", "comments": "El ensayo introduce claramente la problemática organizacional analizada." },
      "_7089": { "points": 2.5, "rating_id": "_2068", "comments": "Se demuestra la relación causa-efecto entre plan estratégico y decisiones operativas." }
    }
  },

  "reporte": {
    "status": "success",
    "id_entrega": "SUB_CANVAS_7788",
    "archivos_locales": {
      "json": "C:\\...\\storage\\evaluaciones\\SUB_CANVAS_7788\\reporte_evaluacion.json",
      "markdown": "C:\\...\\storage\\evaluaciones\\SUB_CANVAS_7788\\reporte_evaluacion.md"
    },
    "notificacion_lms": {
      "canal": "local",
      "enviado": false
    }
  }
}
```

> [!IMPORTANT]
> **Integración Directa con la API REST de Canvas LMS**:
> El objeto generado `rubric_assessment_canvas` coincide de forma exacta con la especificación oficial de Canvas:
> ```http
> PUT /api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}
> Content-Type: application/json
> 
> {
>   "rubric_assessment": {
>     "_830": { "points": 2.5, "rating_id": "blank", "comments": "..." },
>     "_7089": { "points": 2.5, "rating_id": "_2068", "comments": "..." }
>   }
> }
> ```

---

## 8. 💻 Ejemplos de Código para Invocar la API

### Ejemplo 1: PowerShell (Windows)

```powershell
$payload = @{
    "s.course_id" = "CURSO_101"
    "s.user_id_canvas" = "EST_99482"
    "g.assignment_id" = "ASG_01"
    "g.submission_id" = "SUB_7788"
    "g.submission_type" = "online_upload"
    "id_lms" = "CANVAS_PROD"
    "origen_ensayo" = "El presente ensayo analiza el contexto y planeación estratégica de la empresa..."
    "origen_rubrica" = @(
        @{
            id = "_830"
            points = 2.5
            description = "Presenta el contexto y problemática de la empresa"
            ratings = @(
                @{ id = "blank"; points = 2.5; description = "Cumple" },
                @{ id = "blank_2"; points = 0.0; description = "No cumple" }
            )
        },
        @{
            id = "_7089"
            points = 2.5
            description = "Analiza cómo la planeación influye en la formulación de decisiones estratégicas"
            ratings = @(
                @{ id = "_2068"; points = 2.5; description = "Cumple" },
                @{ id = "_2159"; points = 0.0; description = "No cumple" }
            )
        }
    )
    "proveedor_llm" = "modelo"
    "canal_entrega" = "local"
} | ConvertTo-Json -Depth 6

$response = Invoke-RestMethod -Uri "http://localhost:10000/ensayos/evaluar-flujo-completo" `
                              -Method POST `
                              -Headers @{"Content-Type"="application/json"} `
                              -Body $payload

Write-Host "Nota Final:" $response.nota_final "/" $response.escala_maxima
Write-Host "Modelo Utilizado:" $response.modelo_utilizado
$response.rubric_assessment_canvas | ConvertTo-Json
```

---

### Ejemplo 2: cURL (Bash / Linux / Git Bash)

```bash
curl -X POST "http://localhost:10000/ensayos/evaluar-flujo-completo" \
  -H "Content-Type: application/json" \
  -d '{
    "s.course_id": "CURSO_101",
    "s.user_id_canvas": "EST_99482",
    "g.assignment_id": "ASG_01",
    "g.submission_id": "SUB_7788",
    "g.submission_type": "online_upload",
    "id_lms": "CANVAS_PROD",
    "origen_ensayo": "Texto del ensayo académico del estudiante...",
    "origen_rubrica": [
      {
        "id": "_830",
        "points": 2.5,
        "description": "Contexto y problemática",
        "ratings": [
          { "id": "blank", "points": 2.5, "description": "Cumple" },
          { "id": "blank_2", "points": 0.0, "description": "No cumple" }
        ]
      }
    ],
    "proveedor_llm": "modelo",
    "canal_entrega": "local"
  }'
```

---

### Ejemplo 3: Python (`httpx` o `requests`)

```python
import httpx

url = "http://localhost:10000/ensayos/evaluar-flujo-completo"

payload = {
    "s.course_id": "CURSO_101",
    "s.user_id_canvas": "EST_99482",
    "g.assignment_id": "ASG_01",
    "g.submission_id": "SUB_7788",
    "g.submission_type": "online_upload",
    "origen_ensayo": "El presente ensayo analiza el impacto de la planeación...",
    "origen_rubrica": [
        {
            "id": "_830",
            "points": 2.5,
            "description": "Presenta el contexto y problemática de la empresa",
            "ratings": [
                {"id": "blank", "points": 2.5, "description": "Cumple"},
                {"id": "blank_2", "points": 0.0, "description": "No cumple"}
            ]
        }
    ],
    "proveedor_llm": "modelo",
    "canal_entrega": "local"
}

with httpx.Client(timeout=120.0) as client:
    response = client.post(url, json=payload)
    resultado = response.json()
    print(f"Nota: {resultado['nota_final']} / {resultado['escala_maxima']}")
    print(f"Canvas Rubric Assessment: {resultado['rubric_assessment_canvas']}")
```

---

### Ejemplo 4: JavaScript / TypeScript (`fetch`)

```javascript
const payload = {
  "s.course_id": "CURSO_101",
  "s.user_id_canvas": "EST_99482",
  "g.assignment_id": "ASG_01",
  "g.submission_id": "SUB_7788",
  "g.submission_type": "online_upload",
  "origen_ensayo": "El presente ensayo analiza...",
  "origen_rubrica": [
    {
      "id": "_830",
      "points": 2.5,
      "description": "Presenta el contexto y problemática",
      "ratings": [
        { "id": "blank", "points": 2.5, "description": "Cumple" },
        { "id": "blank_2", "points": 0.0, "description": "No cumple" }
      ]
    }
  ],
  "proveedor_llm": "modelo"
};

const response = await fetch("http://localhost:10000/ensayos/evaluar-flujo-completo", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(payload)
});

const data = await response.json();
console.log("Nota final:", data.nota_final);
console.log("Rubric Assessment Canvas:", data.rubric_assessment_canvas);
```

---

## 9. 📁 Archivos Generados en Disco (`storage/evaluaciones/`)

Cada evaluación ejecutada con `canal_entrega: "local"` o `"todos"` crea una carpeta con el ID de la entrega (`id_entrega` o `submission_id`):

```
storage/
└── evaluaciones/
    └── SUB_CANVAS_7788/
        ├── reporte_evaluacion.json   # JSON completo con datos LMS, criterios, evidencias y rubric_assessment_canvas
        └── reporte_evaluacion.md     # Informe pedagógico visual en Markdown listo para descargar o imprimir
```

El informe Markdown incluye:
1. Encabezado con datos del curso, estudiante, tarea, envío y modelo IA utilizado.
2. Resumen ejecutivo con nota obtenida sobre la escala máxima calculada.
3. Tabla de evaluación de criterios con niveles, puntajes y justificación.
4. Citas textuales extraídas del ensayo como respaldo probatorio.
5. Secciones formativas de Fortalezas, Áreas de Mejora y Recomendaciones.
6. Bloque JSON estructurado de integración con Canvas LMS.

---

## 10. 📡 Integración Asíncrona con ActiveMQ Artemis (STOMP)

Para arquitecturas basadas en eventos o colas empresariales, el servicio [artemis_adapter.py](file:///c:/Users/jeguaman2/IA_GENERATIVA/MCP_HUB_SERVER_IA/API/services/artemis_adapter.py) escucha y responde de manera desacoplada:

- **Cola de Entrada:** `/queue/lms.evaluaciones.in` (Selector JMS: `tipo = 'calificar ensayos IA'`)
- **Cola de Salida:** `/queue/lms.calificaciones.out` (o header `reply-to`)
- **Mapeo de Variables:** Extrae `s.course_id`, `s.user_id_canvas`, `g.assignment_id`, `g.submission_id`, `g.submission_type` y procesa rúbricas JSON o binarias.
- **Respuesta:** Encola el resultado completo incluyendo `rubric_assessment_canvas` y preservando el `correlation-id` de la solicitud.

---

## 11. 🤖 Servicios de Inferencia LLM y Catálogo Dinámico (`/llm`)

El servidor incorpora un subsistema de inteligencia artificial con **catálogo dinámico en tiempo real**, **scoring de idoneidad**, **caché en memoria con TTL** y **fallback resiliente** ante saturaciones o fallos de proveedores en OpenRouter.

### 11.1. `GET /llm/models/free` - Consulta en Vivo del Catálogo de Modelos Gratuitos

Consulta directamente la API oficial de OpenRouter (`https://openrouter.ai/api/v1/models`) para descubrir los modelos gratuitos actualmente disponibles, aplicando filtros de idoneidad y ordenándolos mediante un algoritmo de scoring.

#### Parámetros Query:
- `refresh` (opcional, booleano, default `false`): Si es `true`, ignora la caché local y fuerza una consulta fresca a OpenRouter.

#### Ejemplo de Solicitud (cURL):
```bash
curl -X GET "http://localhost:10000/llm/models/free"
```

#### Ejemplo de Solicitud Forzando Recarga:
```bash
curl -X GET "http://localhost:10000/llm/models/free?refresh=true"
```

#### Estructura de Respuesta Exitosa (`200 OK`):
```json
{
  "status": "success",
  "total_free_models": 21,
  "last_updated": "2026-09-24T15:14:38Z",
  "cache_ttl_seconds": 1800,
  "cached": true,
  "models": [
    {
      "id": "nvidia/nemotron-3.5-lightning:free",
      "name": "NVIDIA: Nemotron 3.5 Lightning (free)",
      "description": "NVIDIA Nemotron 3.5 Lightning is an open mixture-of-experts model...",
      "context_length": 1000000,
      "provider_brand": "nvidia",
      "is_multimodal": false,
      "modality": "text->text",
      "supports_reasoning": true,
      "supports_tools": true,
      "supports_structured_outputs": false,
      "pricing": {
        "prompt": "0",
        "completion": "0"
      },
      "expiration_date": null
    },
    {
      "id": "qwen/qwen3.8-27b:free",
      "name": "Qwen 3.8 27B (free)",
      "description": "Qwen 3.8 is an instruction-tuned language model...",
      "context_length": 262144,
      "provider_brand": "qwen",
      "is_multimodal": false,
      "modality": "text->text",
      "supports_reasoning": true,
      "supports_tools": true,
      "supports_structured_outputs": true,
      "pricing": {
        "prompt": "0",
        "completion": "0"
      },
      "expiration_date": null
    }
  ]
}
```

---

### 11.2. `POST /llm/models/refresh` - Recarga Forzada de Caché

Invalida la caché en memoria del servidor y ejecuta una petición inmediata a OpenRouter para reconstruir la lista priorizada de modelos de contingencia.

#### Ejemplo de Solicitud (cURL):
```bash
curl -X POST "http://localhost:10000/llm/models/refresh"
```

---

### 11.3. `POST /llm/chat` - Chat Directo con Fallback Inteligente

Permite interactuar con modelos de lenguaje. Cuando se utiliza el proveedor `"openrouter-free"` o `"modelo"`, el sistema selecciona automáticamente el mejor modelo disponible del catálogo dinámico y, si ocurre cualquier saturación o fallo upstream, desciende en tiempo real por la cola de modelos de respaldo.

#### Propiedades del Payload de Entrada:
- `prompt` (obligatorio, string): Pregunta o instrucción para el modelo.
- `provider` (opcional, string, default `"deepseek"`):
  - `"openrouter-free"`, `"modelo"` o `"free"`: Fallback dinámico automático entre modelos gratuitos de OpenRouter (NVIDIA, Qwen, Gemma, Cohere, Liquid, etc.).
  - `"deepseek"`: DeepSeek Chat vía OpenRouter.
  - `"gemini"` o `"gemma"`: Google Gemma vía OpenRouter.
  - `"openai"`: OpenAI GPT.
- `model` (opcional, string): Modelo preferido para intentar en primera instancia (ej: `"nvidia/nemotron-3.5-lightning:free"`). Si se envían alias genéricos como `"modelo"` o `"free"`, el sistema los ignora de forma segura y usa el orden priorizado óptimo.

#### Ejemplo de Solicitud:
```json
{
  "provider": "openrouter-free",
  "prompt": "¿Por qué el 42 es la respuesta al sentido de la vida?",
  "model": "nvidia/nemotron-3.5-lightning:free"
}
```

#### Ejemplo de Respuesta Exitosa con Registro de Fallback:
```json
{
  "provider": "openrouter-free",
  "model_used": "nvidia/nemotron-3.5-lightning:free",
  "model": "nvidia/nemotron-3.5-lightning:free",
  "prompt": "¿Por qué el 42 es la respuesta al sentido de la vida?",
  "response": "El número 42 es célebre por la novela de ciencia ficción 'The Hitchhiker's Guide to the Galaxy' de Douglas Adams...",
  "text": "El número 42 es célebre por la novela de ciencia ficción...",
  "reasoning_details": null,
  "fallback_attempts": [
    {
      "model": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
      "status_code": 200,
      "motivo": "Error upstream en 200 (502)",
      "detalle": "Upstream error from Nvidia: ResourceExhausted: Worker local total request limit reached (16/16)"
    }
  ]
}
```

> **Garantía de Tolerancia a Fallos y Conmutación Ultrarrápida (Hedged Racing):**  
> 1. **Carrera Especulativa (Hedged Requests):** Si el modelo prioritario no devuelve respuesta en **3.5 segundos** (`LLM_SOFT_TIMEOUT`), el sistema no aborta la llamada sino que dispara inmediatamente el siguiente mejor modelo en paralelo. El primero que responda con éxito gana la carrera y cancela las tareas restantes.
> 2. **Circuit Breaker & Health Tracker:** Si un modelo falla con 429, 502 o agota el timeout de 12s, entra en enfriamiento automático (*cooldown*) de 3 minutos. Las peticiones posteriores no intentarán ese modelo mientras esté degradado.
> 3. **Detección de Errores Upstream en 200:** Si un proveedor responde con HTTP 200 pero incluye un error en el JSON (ej. saturación 502 de workers de NVIDIA) o devuelve texto vacío, se registra en `fallback_attempts` y continúa la carrera hacia el siguiente candidato sin interrumpir el flujo.

#### 11.3.1. Flujo de Ejecución de la Carrera de Modelos (Hedged Racing):

```text
[Solicitud Entrante]
        │
        ▼
┌────────────────────────────────────────────────────────┐
│            Circuit Breaker & Health Tracker            │
│   - Descarta modelos en enfriamiento (cooldown)        │
│   - Prioriza modelos con menor latencia histórica      │
└───────────────────────┬────────────────────────────────┘
                        │
                        ▼
┌────────────────────────────────────────────────────────┐
│        Motor Asíncrono de Carrera (Hedged Race)        │
│                                                        │
│  [0.0s] ──► Dispara Modelo #1 (Candidato óptimo)       │
│                │                                       │
│  [3.5s] ──► ¿No respondió? (Soft Timeout)             │
│                │                                       │
│                ├──► Dispara Modelo #2 en PARALELO      │
│                │    (Sin cancelar todavía el #1)       │
│                │                                       │
│  [4.5s] ──► ¡Modelo #2 responde en 1.0s!               │
│                │                                       │
│                ├──► GANA Modelo #2                     │
│                └──► Cancela las tareas restantes       │
└────────────────────────────────────────────────────────┘
```

#### 11.3.2. Comparativa de Latencia y Benchmarks Reales:

| Modelo | Latencia Secuencial Antigua | Latencia con Hedged Racing | Estado en OpenRouter |
| :--- | :--- | :--- | :--- |
| `nvidia/nemotron-3.5-lightning:free` | 56.46 s *(en cola upstream)* | **3.5s** *(conmutado en paralelo)* | Encolado por alta demanda |
| `nvidia/nemotron-3-ultra-550b-a55b:free` | 0.77 s | **0.77 - 2.2 s** *(Ganador directo)* | Altísima disponibilidad |
| `nvidia/nemotron-3-super-120b-a12b:free` | 1.68 s | **1.68 - 2.5 s** *(Ganador directo)* | Altísima disponibilidad |

#### 11.3.3. Parámetros de Configuración del Motor (`.env`):

| Variable | Tipo | Default | Descripción |
| :--- | :--- | :--- | :--- |
| `LLM_SOFT_TIMEOUT` | float | `3.5` | Tiempo en segundos tras el cual se lanza el siguiente modelo de respaldo en paralelo. |
| `LLM_HARD_TIMEOUT` | float | `12.0` | Tiempo máximo absoluto permitido por intento antes de descartar el modelo. |
| `LLM_MAX_CONCURRENT` | int | `2` | Número máximo de modelos compitiendo simultáneamente en la carrera. |
| `OPENROUTER_MODELS_CACHE_TTL` | int | `1800` | TTL en segundos de la caché del catálogo dinámico de modelos (30 min). |

---

## 12. 🛠️ Herramientas MCP Disponibles (`FastMCP`)

El microservicio [essay_evaluator_mcp.py](file:///c:/Users/jeguaman2/IA_GENERATIVA/MCP_HUB_SERVER_IA/API/services/essay_evaluator_mcp.py) expone herramientas nativas bajo el protocolo MCP (**Model Context Protocol**) para clientes como Claude Desktop, Antigravity o agentes autónomos:

| Herramienta MCP | Parámetros Clave | Descripción |
| :--- | :--- | :--- |
| `obtener_ensayos` | `origen_ensayo`, `origen_rubrica` | Extrae y normaliza texto de rutas locales, URLs, Base64 o JSON (.pdf, .docx, .txt). |
| `evaluar_con_rubrica` | `texto_ensayo`, `texto_rubrica`, `proveedor_llm` | Evalúa el ensayo con rúbrica pedagógica usando el fallback dinámico de LLM. |
| `guardar_reporte` | `id_entrega`, `datos_evaluacion`, `canal_entrega`, `webhook_lms_url` | Guarda el informe Markdown/JSON en `storage/evaluaciones/` y notifica al LMS vía Webhook o Artemis. |
| `consultar_modelos_gratuitos_openrouter` | `forzar_refresco` (bool) | Consulta en tiempo real el catálogo dinámico de modelos gratuitos de OpenRouter con sus ventanas de contexto y capacidades. |

Para ejecutar el servidor MCP en modo standalone por consola:
```powershell
# Modo STDIO estándar
uv run API/services/essay_evaluator_mcp.py

# Modo HTTP Streamable
uv run API/services/essay_evaluator_mcp.py --http
```

---

## 13. 🧠 Inyector de Contexto Institucional (Namespaced Context Fabric)

El **Inyector de Contexto Institucional** es el núcleo de gobernanza y equidad de IA del HUB. Permite que múltiples herramientas MCP actuales (Chat, Evaluación de Ensayos, Auditoría y Reportes de Zoom) y futuras (CRM, Asistencia Académica, Mesa de Ayuda) compartan la misma identidad, directivas pedagógicas, normas éticas y datos de sesión sin duplicar información en cada llamada.

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        ARQUITECTURA DE CONTEXTO INSTITUCIONAL                          │
├────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                        │
│   Sistemas Externos           Core HUB (Middleware)              Herramientas MCP      │
│  ┌──────────────────┐       ┌────────────────────────┐         ┌─────────────────────┐ │
│  │   Canvas LMS     │──┐    │ ContextStore / Redis   │    ┌───►│ Evaluador Ensayos   │ │
│  └──────────────────┘  │    │  (ctx_0c73baa7b193)    │    │    └─────────────────────┘ │
│  ┌──────────────────┐  ├───►│           │            ├────┼───►│ LLM Chat Core       │ │
│  │     N8N Flow     │──┤    │           ▼            │    │    └─────────────────────┘ │
│  └──────────────────┘  │    │ contextvars.ContextVar │    ├───►│ Zoom Management     │ │
│  ┌──────────────────┐  │    │ (get_current_context)  │    │    └─────────────────────┘ │
│  │   HubSpot CRM    │──┘    └────────────────────────┘    └───►│ Futuras Tools MCP   │ │
│  └──────────────────┘                                          └─────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### 13.1. Tres Pilares de Diseño:

1. **💰 Ahorro Drástico de Tokens (Token Savings):**
   - El contexto institucional y los datos comunes de Canvas pueden inicializarse una vez y referenciarse con `X-Context-ID`, en vez de repetirlos en cada payload del LMS.
   - El ahorro del slot LMS corresponde al transporte y procesamiento de datos repetidos (`assignment.description`, título y rúbrica) entre el LMS y el HUB. Su magnitud depende del tamaño de esos campos y del número de entregas; no se debe extrapolar una cifra fija sin medir el payload real.
   - El ahorro de tokens de inferencia corresponde al bloque institucional compilado. La rúbrica y el texto del ensayo siguen siendo insumos necesarios para evaluar cada estudiante y no se cuentan como datos eliminados por el slot.

2. **⚖️ Equidad y Coherencia Transversal (Context Equity):**
   - Todos los modelos y herramientas consultan `get_current_context()`.
   - El método `to_system_instruction()` compila una directiva institucional formal de alta densidad:
     ```text
     --- DIRECTIVAS INSTITUCIONALES DEL HUB ---
     Institución: UNIVERSIDAD_TECNICA | Facultad/Sede: FACULTAD_INGENIERIA
     Sistema origen: CANVAS_LMS | Rol del actor: DOCENTE
     Tono requerido: constructivo, pedagógico y motivador
     Escala de evaluación institucional: 0 a 20.0
     Política institucional obligatoria: Fomentar el pensamiento crítico e identificar plagio.
     --- FIN DIRECTIVAS INSTITUCIONALES ---
     ```
   - Tanto si el estudiante chatea con el bot, como si un docente evalúa un ensayo o se audita una sesión de Zoom, la IA actúa bajo la **misma escala, mismo tono y mismas directivas institucionales**.

3. **🧩 Escalabilidad Modular por Ranuras (Domain Slots):**
   - El contexto divide la información en **ranuras aisladas** (`slots`):
     - `slots["lms"]`: `course_id`, `assignment_id`, `assignment_name`, `assignment_description`, `scale`, `rubric`
     - `slots["zoom"]`: `account_id`, `recording_policy`
     - `slots["crm"]`: `customer_id`, `lead_status`
     - `slots["custom"]`: Claves libres para nuevas herramientas MCP
   - Agregar una nueva herramienta MCP **no requiere modificar la base de datos ni los modelos globales**. Solo interactúa con su slot específico.

---

### 13.2. Ciclo de Vida y Endpoints REST del Contexto

#### A. Inicializar Sesión de Contexto (`POST /context/init`)
Crea la sesión de contexto en el almacén con tiempo de vida (TTL) configurable.

**Petición:**
```bash
curl -X POST http://localhost:10000/context/init \
  -H "Content-Type: application/json" \
  -d '{
    "institution_id": "UNIVERSIDAD_NACIONAL",
    "tenant_id": "FACULTAD_INGENIERIA",
    "system_source": "CANVAS_LMS",
    "user_role": "DOCENTE",
    "evaluation_scale": 20.0,
    "tone_policy": "constructivo, académico y motivador",
    "global_policy": "Fomentar el pensamiento crítico y uso riguroso de fuentes.",
    "custom_instructions": "Dar prioridad a la estructura metodológica en ensayos.",
    "ttl_seconds": 3600,
    "slots": {
      "lms": {
        "course_id": "ING-101",
        "assignment_id": "ENSAYO_PARCIAL_1"
      }
    }
  }'
```

**Respuesta (200 OK):**
```json
{
  "status": "success",
  "context_id": "ctx_0c73baa7b193",
  "expires_in_seconds": 3600,
  "institution_id": "UNIVERSIDAD_NACIONAL",
  "tenant_id": "FACULTAD_INGENIERIA",
  "user_role": "DOCENTE",
  "system_source": "CANVAS_LMS",
  "compiled_instruction": "--- DIRECTIVAS INSTITUCIONALES DEL HUB ---\nInstitución: UNIVERSIDAD_NACIONAL | Facultad/Sede: FACULTAD_INGENIERIA\nSistema origen: CANVAS_LMS | Rol del actor: DOCENTE\nTono requerido: constructivo, académico y motivador\nEscala de evaluación institucional: 0 a 20.0\nPolítica institucional obligatoria: Fomentar el pensamiento crítico y uso riguroso de fuentes.\nInstrucciones particulares: Dar prioridad a la estructura metodológica en ensayos.\nContexto Académico LMS: Curso ING-101 - Asignación ENSAYO_PARCIAL_1\n--- FIN DIRECTIVAS INSTITUCIONALES ---"
}
```

---

#### B. Consultar Contexto Activo (`GET /context/{context_id}`)
Permite inspeccionar el estado actual y los datos de cada ranura.

```bash
curl http://localhost:10000/context/ctx_0c73baa7b193
```

---

#### C. Actualizar o Enriquecer una Ranura (`POST /context/{context_id}/slot/{slot_name}`)
Ideal para flujos de N8N que recopilan información progresivamente (por ejemplo, tras consultar una API de Zoom o Canvas).

**Petición:**
```bash
curl -X POST http://localhost:10000/context/ctx_0c73baa7b193/slot/zoom \
  -H "Content-Type: application/json" \
  -d '{
    "data": {
      "account_id": "ZOOM_CAMPUS_CENTRAL",
      "meeting_id": "98765432101",
      "storage_days": 60
    }
  }'
```

---

#### D. Eliminar Contexto (`DELETE /context/{context_id}`)
Cierra la sesión antes de su expiración natural por TTL.

```bash
curl -X DELETE http://localhost:10000/context/ctx_0c73baa7b193
```

---

### 13.3. Modos de Activación y Uso

El servidor ofrece **tres mecanismos flexibles** para activar el contexto:

#### Modo 1: Encabezado `X-Context-ID` (Recomendado para Máximo Ahorro)
Inicializa el contexto con `POST /context/init` y simplemente incluye el header en cualquier llamada REST a las herramientas:

```bash
# Chat LLM con Contexto Inyectado
curl -X POST http://localhost:10000/llm/chat \
  -H "Content-Type: application/json" \
  -H "X-Context-ID: ctx_0c73baa7b193" \
  -d '{
    "prompt": "¿Cuáles son las pautas para calificar este trabajo?",
    "provider": "modelo"
  }'
```

*El LLM responderá automáticamente usando la escala vigesimal (0-20), las políticas antiplagio y el tono institucional de la sesión.*

```bash
# Evaluación de Ensayos con Autocompletado LMS desde el Contexto
curl -X POST http://localhost:10000/ensayos/evaluar-flujo-completo \
  -H "Content-Type: application/json" \
  -H "X-Context-ID: ctx_0c73baa7b193" \
  -d '{
    "origen_ensayo": "Contenido del ensayo del estudiante...",
    "origen_rubrica": "Criterio 1: Estructura (10 pts). Criterio 2: Argumentación (10 pts).",
    "proveedor_llm": "modelo"
  }'
```

*Los campos `course_id`, `assignment_id` y `escala_maxima` se completan de forma automática a partir del slot `lms` del contexto si no se envían en el body.*

---

#### Modo 2: Encabezados Efímeros en Vuelo (Stateless)
Si no deseas inicializar una sesión previa, puedes enviar encabezados institucionales directamente en cualquier petición. El middleware instanciará un contexto efímero con TTL de 5 minutos:

- `X-Institution-ID: UNIVERSIDAD_TECNICA`
- `X-Tenant-ID: INGENIERIA`
- `X-User-Role: DOCENTE`
- `X-Source-System: CANVAS_LMS`

---

#### Modo 3: Integración en N8N (Flujo Automatizado)

1. **Nodo 1 (HTTP Request):** Invoca `POST /context/init` al inicio del workflow y extrae `{{ $json.context_id }}` para los servicios que usan el contexto general.
2. **Nodos Intermedios (Tools):** Configura el header `X-Context-ID: {{ $('Nodo 1').item.json.context_id }}` en los nodos que consumen ese contexto general (`/llm/chat`, reportes Zoom, etc.). Para evaluaciones LMS, envía `institution_id`, `course_id` y `assignment_id`; el HUB deriva y persiste automáticamente el contexto compartido de la tarea.
3. **Nodo Enriquecedor:** Si el flujo obtiene datos adicionales (ej. ID de reunión Zoom o ID de entrega Canvas), ejecuta `POST /context/{context_id}/slot/lms` para actualizar la sesión.
4. **Nodo Final (Opcional):** Ejecuta `DELETE /context/{context_id}` o permite que expire por su TTL.

---

## 14. 🖥️ Proveedor On-Premise Ollama (Inferencia Local y Privacidad de Datos)

Para instituciones que manejan datos sensibles de estudiantes o investigación confidencial que no debe salir a la nube, el HUB incluye un proveedor nativo para **Ollama** ejecutándose on-premise en infraestructura local (servidor con GPUs NVIDIA o máquina corporativa).

### 14.1. Configuración en `.env`

Agrega las siguientes variables a tu archivo `.env`:

```ini
# Configuración On-Premise Ollama
OLLAMA_ENDPOINT=http://localhost:11434
OLLAMA_MODEL=llama3.3:latest
OLLAMA_TIMEOUT=60.0
```

> [!TIP]
> Si el servidor de Ollama está alojado en otro servidor dentro de la red institucional, simplemente configura:
> `OLLAMA_ENDPOINT=http://192.168.1.50:11434` o su dominio interno con SSL.

---

### 14.2. Endpoints de Diagnóstico y Salud de Ollama

#### A. Verificar Estado del Servicio (`GET /llm/ollama/status`)
Comprueba en menos de 1.5 segundos si el demonio local de Ollama está encendido y accesible sin bloquear peticiones.

**Respuesta:**
```json
{
  "provider": "ollama",
  "endpoint": "http://localhost:11434",
  "available": true,
  "default_model": "llama3.3:latest"
}
```

---

#### B. Listar Modelos Instalados Localmente (`GET /llm/ollama/models`)
Lista los modelos descargados en la máquina local (ej. `llama3.3`, `mistral`, `deepseek-r1:8b`, `qwen2.5-coder`).

**Respuesta:**
```json
{
  "provider": "ollama",
  "count": 2,
  "models": [
    {
      "name": "llama3.3:latest",
      "model": "llama3.3:latest",
      "size": 4661224576,
      "digest": "a6990fa3d...
    }
  ]
}
```

---

### 14.3. Invocación de Inferencia On-Premise

#### En Chat LLM (`POST /llm/chat`):
```json
{
  "prompt": "¿Cuáles son los principios pedagógicos del aprendizaje constructivista?",
  "provider": "ollama",
  "model": "llama3.3:latest"
}
```

#### En Evaluación de Ensayos (`POST /ensayos/evaluar-flujo-completo`):
```json
{
  "origen_ensayo": "Texto del ensayo confidencial...",
  "origen_rubrica": "Criterios de la rúbrica...",
  "proveedor_llm": "ollama"
}
```

*Si Ollama no estuviese encendido o accesible en ese momento, el servicio responde inmediatamente con un error estructurado (`status_code: connection_error`) sin provocar caídas ni congelamientos en el HUB.*

---

## 15. 🛡️ Control de Contexto Canvas, Filtrado Anti-Desperdicio de Tokens y Especificación de Webhook Mínimo

### 15.1. El Problema del Desperdicio de Tokens en Webhooks Académicos

En las integraciones tradicionales entre LMS (Canvas LMS, Moodle) y modelos LLM, los webhooks disparados tras la entrega de cada estudiante suelen incluir toda la carga institucional:
1. **Payload LMS duplicado**: sin un slot compartido, los datos de tarea y rúbrica pueden viajar otra vez del LMS al HUB para cada estudiante. Precargarlos evita esa repetición en la integración HTTP; no significa que la rúbrica se pueda quitar de cada evaluación del modelo.
2. **Descripción mezclada con logística**: `assignment.description` puede contener fechas, formato de entrega y avisos del EVA además de la consigna pedagógica. El HUB conserva la descripción común y prepara la consigna evaluable; el administrador debe evitar reenviar el bloque extenso en cada payload.
3. **Metadatos administrativos**: `courseId`, `assignmentId` y `submissionId` son necesarios para correlación, pero no forman parte de la directiva pedagógica. Mantenerlos en los campos/slots de LMS evita mezclarlos con las instrucciones de evaluación.

---

### 15.2. Arquitectura de Separación: Telemetría LMS vs Prompt Evaluativo

El HUB implementa una **arquitectura de doble flujo**:
- **Flujo de Telemetría y Trazabilidad (Interno):** El contexto compartido persiste los datos comunes de Canvas (`course_id`, `assignment_id`, `assignment_name`, `assignment_description`, `rubric`, escala y metadatos permitidos); cada evaluación mantiene sus claves individuales (`submission_id`, `user_id_canvas`, `submission_type`) en el trabajo y resultado. Se utilizan para:
  - Generar el informe en Markdown con cabecera legible.
  - Almacenar el archivo de auditoría JSON en `storage/evaluaciones/`.
  - Construir la respuesta HTTP y el callback para actualizar Canvas SpeedGrader vía API.
- **Flujo Evaluativo al LLM (Filtrado Inteligente):** Los IDs de Canvas y los datos del archivo adjunto no se usan como instrucciones del modelo. La consigna se depura y la rúbrica sí se procesa para cada ensayo, porque los criterios y ratings son necesarios para producir una evaluación válida.

#### Tabla Comparativa: Qué Recibe el HUB vs Qué se Envía al LLM

| Campo del Webhook Canvas | ¿Se Guarda en Telemetría / Reporte? | ¿Se Envía al LLM? | Razón de Optimización |
| :--- | :---: | :---: | :--- |
| `assignment.courseId` | **SÍ** | ❌ **NO** | Identidad y correlación del contexto compartido. |
| `assignment.id` | **SÍ** | ❌ **NO** | Identidad de la tarea y correlación. |
| `assignment.name` | **SÍ** | ❌ **NO** | Trazabilidad y nombre legible del reporte. |
| `assignment.description` | **SÍ** *(en contexto compartido)* | **PARCIAL** | La consigna evaluable se extrae; logística redundante se omite. |
| `assignment.rubric` | **SÍ** *(en contexto compartido)* | **SÍ, por evaluación** | Criterios y ratings se requieren para calificar; puede normalizarse y deduplicar texto redundante. |
| `submission.id` | **SÍ** | ❌ **NO** | ID de entrega para SpeedGrader y almacenamiento de carpetas. |
| `submission.userId` | **SÍ** | ❌ **NO** | Identificador del estudiante; se usa para nombrar el autor en el reporte sin sesgar al modelo. |
| `submission.type` | **SÍ** | ❌ **NO** | Telemetría técnica (`online_upload`, `online_text_entry`). |
| `attachment.url` | Se procesa para obtener el documento | ❌ **NO** | El endpoint/URL no se manda como prompt; el contenido extraído del archivo sí se evalúa. |
| Contenido del Ensayo | **SÍ** | **SÍ** | Texto académico esencial que debe ser calificado. |
| Directiva Institucional | **SÍ** | **SÍ** *(compilada por presupuesto)* | Escala, tono y políticas se inyectan usando el compilador de contexto; esto es distinto de la rúbrica de la tarea. |

---

### 15.3. Directiva Institucional Compilada por Presupuesto

En la evaluación asíncrona, el HUB compila el contexto institucional con un
presupuesto de hasta 512 tokens por defecto (`PROMPT_EVAL_CONTEXT_BUDGET`).
El límite es un máximo, no un objetivo: un contexto pequeño puede compilarse a
menos tokens. Para el contexto de UTPL usado en dos evaluaciones recientes, el
estimador heurístico midió 46 tokens compilados frente a 95 tokens para la
instrucción institucional completa (49 tokens, aproximadamente 51,6% menos por
evaluación). Es una estimación del bloque institucional, no el conteo real del
proveedor ni el uso total del prompt.

El texto compilado puede adoptar una forma breve como:

```text
[Directiva: UNIVERSIDAD_TECNICA_PARTICULAR_DE_LOJA_UTPL | Escala: 0 a 10.0 | Tono: pedagógico, constructivo y formal]
```

El ahorro del compilador aplica al contexto institucional. No incluye el ensayo
ni la rúbrica, que siguen siendo necesarios para evaluar cada entrega. Para
informar consumo/costo real de inferencia, el LMS o la integración del proveedor
debe registrar los tokens de prompt/completion reportados por el proveedor.

---

### 15.4. Deduplicación Automática de Descriptores de Rúbricas

En Canvas LMS es habitual que la rúbrica configure el mismo texto en el nombre del criterio y en su descripción extendida, o que el nivel de rating ("Cumple") repita el texto del criterio.
El parser `RubricaGenerica` detecta automáticamente si `long_description` es idéntica a `description`:
- Si son idénticas, **se omite el texto redundante**.
- Solo se incluye la descripción extendida si aporta descriptores de nivel cualitativo adicionales.

---

### 15.5. Integración del Webhook Asíncrono de Canvas

`POST /ensayos/webhook-canvas` siempre encola la entrega y responde `202 Accepted`. No espera la descarga/procesamiento del documento ni la inferencia LLM. Guarda el `evaluation_id` recibido y consulta `GET /ensayos/estado/{evaluation_id}` para obtener el resultado.

#### Contexto compartido por institución, curso y tarea

Cuando el evento incluye `institution_id`, `course_id` y `assignment_id`, el HUB deriva un `context_id` estable y persiste el contexto compartido de la tarea: descripción, rúbrica, escala y metadatos permitidos. Las entregas de estudiantes distintos de la misma institución/curso/tarea reutilizan ese contexto. Los datos del alumno y de su entrega se mantienen en cada trabajo de evaluación, no en el contexto común.

El payload Canvas estándar puede incluir `assignment.rubric`, descripción, envío y archivo/texto. La primera solicitud crea o actualiza el contexto; las siguientes completan los datos comunes sin almacenar `user_id_canvas` ni `submission_id` dentro del slot compartido. En evaluación LMS, un `X-Context-ID` explícito no sustituye el identificador derivado de institución/curso/tarea; conserva ese header para los demás endpoints que usan el contexto general.

También se puede preparar el contexto antes del lote mediante `/context/init` y `POST /context/{context_id}/slot/lms`. Esto es opcional para la identidad por curso/tarea, pero permite registrar con anticipación la rúbrica y descripción comunes.

#### Enviar la entrega

```bash
curl -i -X POST http://localhost:10000/ensayos/webhook-canvas \
  -H "Content-Type: application/json" \
  -d '{
    "institution_id": "UNIVERSIDAD_TECNICA_PARTICULAR_DE_LOJA_UTPL",
    "assignment": {
      "id": "992586",
      "courseId": "79871",
      "name": "Investigación y propuesta: Matriz FODA",
      "description": "Analice la situación de la empresa...",
      "pointsPossible": 10,
      "rubric": [ ... rúbrica de Canvas ... ]
    },
    "submission": {
      "id": "57423885",
      "userId": "182199",
      "type": "online_upload"
    },
    "attachment": {
      "name": "TAREA_FODA_ESTUDIANTE.docx",
      "url": "https://utpl.test.instructure.com/files/24498662/download?download_frd=1&verifier=..."
    }
  }'
```

La respuesta inmediata confirma recepción y trae la correlación necesaria para consultar el trabajo; aún no contiene nota ni feedback:

```json
{
  "status": "ENCOLADO",
  "estado": "PENDING",
  "evaluation_id": "eval_...",
  "context_id": "ctx_lms_...",
  "institution_id": "UNIVERSIDAD_TECNICA_PARTICULAR_DE_LOJA_UTPL",
  "course_id": "79871",
  "assignment_id": "992586",
  "user_id_canvas": "182199",
  "submission_id": "57423885"
}
```

El identificador es opaco para el cliente: úsalo exactamente como fue devuelto, sin construirlo desde el `submission_id`.

#### Consultar el estado

```bash
curl http://localhost:10000/ensayos/estado/eval_...
```

- `PENDING` o `EVALUATING`: el worker aún está procesando; vuelve a consultar con una espera apropiada.
- `EVALUATED`: la respuesta incluye el resultado canónico con calificación, feedback y `rubric_assessment_canvas`.
- `ERROR`: incluye el motivo en `error`.
- `NOT_FOUND`: el identificador no existe (HTTP `404`).

El mismo contrato asíncrono está disponible para payloads directos en `POST /ensayos/enqueue`. `POST /ensayos/evaluar-flujo-completo` continúa siendo síncrono y solo se recomienda cuando el cliente realmente necesita esperar el resultado durante esa petición.

### 15.6. Resultado Final para SpeedGrader de Canvas

Cuando `GET /ensayos/estado/{evaluation_id}` devuelve `EVALUATED`, la respuesta canónica incluye, entre otros, estos campos. Esta es la respuesta que debe usarse para sincronizar la calificación con Canvas; no el acuse `202` inicial.

```json
{
  "status": "EVALUADO",
  "evaluation_id": "eval_...",
  "context_id": "ctx_lms_...",
  "submission_id": "57423885",
  "course_id": "79871",
  "assignment_id": "992586",
  "user_id_canvas": "182199",
  "modelo_utilizado": "google/gemma-3-27b-it:free",
  "nota_final": 9.0,
  "escala_maxima": 10.0,
  "feedback": "El estudiante demostró un sólido dominio al contextualizar la institución...",
  "rubric_assessment_canvas": {
    "_2541": {
      "points": 2.5,
      "rating_id": "blank",
      "comments": "La contextualización incluye datos completos y bien organizados..."
    }
  },
  "datos_lms": {
    "id_lms": "CANVAS",
    "course_id": "79871",
    "assignment_id": "992586",
    "user_id_canvas": "182199",
    "submission_id": "57423885",
    "submission_type": "online_upload"
  }
}
```


---

## 16. Guía de Integración para el Administrador del LMS

Esta sección define el contrato REST que debe implementar el LMS (o su
integrador) para enviar entregas en segundo plano, consultar su avance y
recuperar el resultado. El flujo está pensado para procesar muchos estudiantes
que comparten una misma institución, curso y tarea.

### 16.1. Preparación y creación del contexto

Hay dos niveles de contexto y no deben confundirse:

1. **Contexto institucional general (opcional):** se crea con
   `POST /context/init`. Sirve para herramientas que reciben un `X-Context-ID`
   y para establecer políticas, tono, escala y directivas generales. La
   respuesta incluye el `context_id` y el TTL de sesión. Chat y otros contextos
   generales vencen conforme a ese TTL (una hora por defecto).
2. **Contexto compartido de evaluación por institución + curso + tarea:** al
   precargar el slot `lms` con `course_id` y `assignment_id`, `/context/init`
   crea una actividad durable con identidad estable. También se crea al recibir
   la primera evaluación completa con estos tres identificadores. El contexto
   permanece activo hasta que el LMS cierre la tarea mediante
   `POST /context/{context_id}/close`; desde entonces se conserva por el plazo
   de gracia (tres días por defecto).

Para el flujo de ensayos, el LMS **no debe crear un contexto por estudiante**.
Puede precargar los datos comunes de la tarea en el slot `lms` una sola vez por
institución/curso/tarea y reutilizar el `context_id` en las entregas. Al incluir
`slots.lms.course_id` y `slots.lms.assignment_id` en `/context/init`, el HUB
deriva el mismo identificador estable que utilizará la evaluación; el LMS no
debe calcularlo. Como alternativa, envíe la tarea completa en la primera
evaluación y reutilice el `context_id` devuelto por el HUB en las siguientes.

Ejemplo opcional de contexto institucional general:

```bash
curl -i -X POST http://localhost:10000/context/init \
  -H "Content-Type: application/json" \
  -d '{
    "institution_id": "UNIVERSIDAD_TECNICA_PARTICULAR_DE_LOJA_UTPL",
    "tenant_id": "FACULTAD_DE_ADMINISTRACION",
    "system_source": "CANVAS_LMS",
    "user_role": "DOCENTE",
    "evaluation_scale": 10,
    "tone_policy": "pedagógico, constructivo y formal",
    "global_policy": "Evaluar con criterios académicos y retroalimentación formativa.",
    "ttl_seconds": 3600
  }'
```

Respuesta ilustrativa `200 OK`:

```json
{
  "status": "success",
  "context_id": "ctx_...",
  "expires_in_seconds": 3600,
  "institution_id": "UNIVERSIDAD_TECNICA_PARTICULAR_DE_LOJA_UTPL"
}
```

El LMS debe guardar ese identificador y enviarlo como `X-Context-ID` al usar el
slot LMS precargado. En el primer envío de evaluación completa, debe guardar el
`context_id` recibido en el acuse `202`.

#### Precarga recomendada del slot `lms` para un lote

El slot `lms` permite registrar una vez los datos que son iguales para todos
los estudiantes: identidad de curso y tarea, consigna, escala y rúbrica. Para
el ejemplo real de Canvas, la identidad es institución UTPL + curso `79871` +
tarea `992586`. No incluir en este slot información individual como
`submission.id`, `submission.userId`, texto del estudiante, comentarios de
SpeedGrader ni URLs privadas de adjuntos.

Se puede crear y precargar el contexto en una llamada:

```bash
curl -i -X POST http://localhost:10000/context/init \
  -H "Content-Type: application/json" \
  -d '{
    "institution_id": "UNIVERSIDAD_TECNICA_PARTICULAR_DE_LOJA_UTPL",
    "tenant_id": "GENERAL",
    "system_source": "CANVAS_LMS",
    "user_role": "DOCENTE",
    "evaluation_scale": 10,
    "tone_policy": "pedagógico, constructivo y formal",
    "context_grace_days": 3,
    "slots": {
      "lms": {
        "course_id": "79871",
        "assignment_id": "992586",
        "assignment_name": "[APEB1-30%] Investigación y propuesta: Analice la situación actual de una empresa utilizando la matriz FODA y proponga un plan de mejora en base a los resultados del análisis",
        "assignment_description": "Analice la situación actual de una empresa utilizando la matriz FODA y proponga un plan de mejora. Incluya la descripción de la organización, cuatro elementos por cada dimensión FODA y un plan con al menos dos objetivos, dos estrategias por objetivo, actividades, responsables e indicadores.",
        "scale": 10,
        "rubric": [
          {
            "id": "_2541",
            "points": 2.5,
            "description": "Contextualización de la institución",
            "ratings": [
              { "id": "blank", "points": 2.5, "description": "Excelente", "long_description": "Descripción clara, completa y bien organizada." },
              { "id": "_5282", "points": 2, "description": "Muy bueno", "long_description": "Descripción adecuada, aunque falta algún detalle menor." },
              { "id": "_9978", "points": 1.25, "description": "Bueno", "long_description": "Información incompleta o poco clara." },
              { "id": "blank_2", "points": 0.5, "description": "Regular", "long_description": "Información muy escasa o desorganizada." }
            ]
          },
          {
            "id": "_9637",
            "points": 2.5,
            "description": "Análisis FODA",
            "ratings": [
              { "id": "_4544", "points": 2.5, "description": "Excelente", "long_description": "Presenta cuatro elementos bien redactados y coherentes por cada categoría." },
              { "id": "_1696", "points": 2, "description": "Muy bueno", "long_description": "Presenta al menos tres elementos por categoría, con coherencia." },
              { "id": "_2278", "points": 1.25, "description": "Bueno", "long_description": "Presenta menos de tres elementos en alguna categoría o hay poca claridad." },
              { "id": "_933", "points": 0.5, "description": "Regular", "long_description": "Análisis incompleto, poco reflexivo o confuso." }
            ]
          },
          {
            "id": "_901",
            "points": 2.5,
            "description": "Plan de mejora",
            "ratings": [
              { "id": "_4127", "points": 2.5, "description": "Excelente", "long_description": "Objetivos, estrategias y actividades bien definidas, viables y alineadas al FODA." },
              { "id": "_3467", "points": 2, "description": "Muy bueno", "long_description": "Plan adecuado, aunque algunas partes podrían desarrollarse mejor." },
              { "id": "_3078", "points": 1.25, "description": "Bueno", "long_description": "Plan poco detallado o con poca relación con el diagnóstico." },
              { "id": "_1569", "points": 0.5, "description": "Regular", "long_description": "Plan incompleto, sin coherencia ni aplicabilidad." }
            ]
          },
          {
            "id": "_5229",
            "points": 2.5,
            "description": "Pertinencia de la propuesta",
            "ratings": [
              { "id": "_4177", "points": 2.5, "description": "Excelente", "long_description": "Propuesta innovadora, adaptada a la realidad de la organización y con ideas originales." },
              { "id": "_4357", "points": 2, "description": "Muy bueno", "long_description": "Propuesta adecuada, con algunas ideas interesantes." },
              { "id": "_7751", "points": 1.25, "description": "Bueno", "long_description": "Propuesta poco novedosa o poco adaptada al contexto." },
              { "id": "_7520", "points": 0.5, "description": "Regular", "long_description": "Propuesta sin creatividad ni relación con la realidad institucional." }
            ]
          }
        ]
      }
    }
  }'
```

La respuesta incluye `context_id`, `lifecycle_type` y `grace_period_days`.
Guarde el ID y envíelo como header `X-Context-ID` en las peticiones de
evaluación. Personalice el período por actividad con `context_grace_days` entre
0 y 365; si se omite, se aplica `LMS_CONTEXT_GRACE_DAYS` (default 3). También
se puede inicializar el slot con solo `course_id` y `assignment_id` y completar
los demás datos comunes antes del primer envío mediante
`POST /context/{context_id}/slot/lms`, enviando `{ "data": { ...campos lms... } }`.
Esos dos identificadores deben estar presentes desde `/context/init` para que
el contexto tenga identidad estable y ciclo de vida de actividad; crear primero
una sesión genérica y añadirlos después no la convierte en actividad LMS.

El slot usa estos nombres canónicos:

| Campo de `slots.lms` | Equivalencia Canvas | Uso |
|---|---|---|
| `course_id` | `assignment.courseId` | Parte de la identidad compartida. |
| `assignment_id` | `assignment.id` | Parte de la identidad compartida. |
| `assignment_name` | `assignment.name` | Nombre legible para trazabilidad. |
| `assignment_description` | `assignment.description` | Consigna común; se conserva para las evaluaciones. |
| `rubric` | `assignment.rubric` | Criterios y ratings necesarios para calificar. |
| `scale` | `assignment.pointsPossible` | Escala de Canvas; en el ejemplo es 10. |

La petición Canvas de ejemplo incluye también `submission.comments` y el bloque
`ai`. Son datos de estado/retroalimentación previa de Canvas, no son necesarios
para iniciar una evaluación nueva: omítalos del payload al HUB, salvo que una
integración tenga un caso funcional explícito que los requiera.

#### Cierre de tarea y período de gracia

Cuando el LMS cierre la actividad, el LMS o su integrador debe invocar:

```bash
curl -i -X POST http://localhost:10000/context/ctx_lms_<identificador estable>/close \
  -H "Content-Type: application/json" \
  -d '{ "context_grace_days": 3 }'
```

Si se omite `context_grace_days`, se aplica `LMS_CONTEXT_GRACE_DAYS`. El plazo
empieza cuando se registra el cierre; no se calcula automáticamente desde
`due_at` o `lock_at`. Durante la gracia el contexto se puede restaurar para
finalizar trabajos encolados y hacer reintentos/consultas. Al vencer, el
contexto deja de ser restaurable; los trabajos que lo referencien fallarán con
HTTP `410` en vez de recrear silenciosamente otro contexto. Para entregas
posteriores, el LMS debe volver a inicializar la tarea con los datos comunes.

El HUB permite reabrir una actividad con `POST /context/init` y el mismo slot;
se reactiva el identificador estable. Los datos del slot no se pueden modificar
mientras la actividad esté cerrada; primero hay que reabrirla. Los contextos
generales de chat y otras sesiones no usan este endpoint y siguen su TTL
(`ttl_seconds`, una hora por defecto).

Configura `DB_DSN` con un backend durable. Si la persistencia de contextos LMS
no está disponible, la evaluación falla con `503` y no se acepta como durable.
Protege las operaciones de inicialización, actualización, cierre y eliminación
con autenticación/autorización en el gateway o integración del LMS.

### 16.2. Enviar una entrega para evaluación

El LMS envía una petición por entrega a
`POST /ensayos/webhook-canvas`. La respuesta `202 Accepted` significa que el
trabajo fue aceptado en la cola, **no** que ya tenga calificación.

Campos principales:

| Campo | Obligatorio para este flujo | Uso |
|---|---:|---|
| `institution_id` | Recomendado | Identifica la institución y participa en la identidad del contexto compartido. Puede omitirse en llamadas posteriores si el `X-Context-ID` ya la contiene. |
| `assignment.id` | Sí, salvo que esté precargado en el slot | Identificador de la tarea. Se recomienda repetirlo: ocupa poco y facilita validar la correlación. |
| `assignment.courseId` o `assignment.course_id` | Sí, salvo que esté precargado en el slot | Identificador del curso. Se recomienda repetirlo por la misma razón. |
| `assignment.name` | No si está precargado | Nombre legible de la actividad para trazabilidad. |
| `assignment.description` | No si está precargada | Consigna común; evita reenviar la descripción extensa en cada estudiante. |
| `assignment.pointsPossible` o `points_possible` | Recomendado | Escala máxima. Es un dato pequeño que puede repetirse en cada entrega. |
| `assignment.rubric` | Sí, salvo que esté precargada en `slots.lms.rubric` | Rúbrica Canvas completa, necesaria para evaluar. No eliminar ratings ni descriptores pedagógicos. |
| `submission.id` | Sí | Identificador de la entrega; se devuelve como correlación. |
| `submission.userId` | Sí | Identificador del estudiante para asociar el resultado en el LMS. |
| `submission.type` | Recomendado | Tipo de entrega Canvas, por ejemplo `online_upload` o `online_text_entry`. |
| `attachment.url` | Para entrega por archivo | URL que el HUB pueda descargar desde su servidor. |
| `attachment.txt` o `submission.body` | Alternativa a URL | Texto de la entrega ya disponible para enviar. |

Ejemplo completo con archivo adjunto y rúbrica:

```bash
curl -i -X POST http://localhost:10000/ensayos/webhook-canvas \
  -H "Content-Type: application/json" \
  -d '{
    "institution_id": "UNIVERSIDAD_TECNICA_PARTICULAR_DE_LOJA_UTPL",
    "assignment": {
      "id": "992586",
      "courseId": "79871",
      "name": "Ensayo: análisis estratégico",
      "description": "Analice la estrategia de una organización y justifique sus conclusiones con fuentes académicas.",
      "pointsPossible": 10,
      "rubric": [
        {
          "id": "_2541",
          "points": 5,
          "description": "Análisis y argumentación",
          "ratings": [
            { "id": "_r1", "points": 5, "description": "Argumentación completa y sustentada" },
            { "id": "_r2", "points": 3, "description": "Argumentación parcial" },
            { "id": "_r3", "points": 0, "description": "No presenta argumentación" }
          ]
        },
        {
          "id": "_2542",
          "points": 5,
          "description": "Uso de fuentes académicas",
          "ratings": [
            { "id": "_r4", "points": 5, "description": "Fuentes pertinentes, integradas y citadas" },
            { "id": "_r5", "points": 3, "description": "Uso limitado o irregular de fuentes" },
            { "id": "_r6", "points": 0, "description": "No utiliza fuentes" }
          ]
        }
      ]
    },
    "submission": {
      "id": "57423885",
      "userId": "182199",
      "type": "online_upload"
    },
    "attachment": {
      "name": "ensayo-estudiante.docx",
      "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "url": "https://lms.example.edu/files/123/download?token=URL_TEMPORAL"
    },
    "proveedor_llm": "modelo"
  }'
```

Para `online_text_entry`, se puede omitir `attachment.url` y enviar el contenido
en `submission.body` (o en `attachment.txt`). Si el LMS manda la rúbrica fuera
del bloque `assignment`, puede usar `origen_rubrica`. El identificador de
institución, curso y tarea debe conservarse igual en todas las entregas de ese
grupo. El `submission.id` y `submission.userId` sí cambian según el estudiante
y la entrega.

#### Payload mínimo por estudiante cuando el slot `lms` está precargado

Envía el `context_id` de `POST /context/init` en `X-Context-ID`. El middleware
activa el contexto antes de validar el body; el HUB recupera del slot `lms` la
rúbrica, la descripción, el nombre y, si falta, los identificadores de curso y
tarea. Conserva en el body las claves de entrega, el texto/archivo del
estudiante y los IDs ligeros de curso/tarea para que el LMS y el HUB puedan
comprobar la correlación:

```bash
curl -i -X POST http://localhost:10000/ensayos/webhook-canvas \
  -H "Content-Type: application/json" \
  -H "X-Context-ID: ctx_preparado_para_el_lote" \
  -d '{
    "institution_id": "UNIVERSIDAD_TECNICA_PARTICULAR_DE_LOJA_UTPL",
    "assignment": {
      "id": "992586",
      "courseId": "79871",
      "pointsPossible": "10.00"
    },
    "submission": {
      "id": "57423860",
      "userId": "181093",
      "type": "online_text_entry"
    },
    "attachment": {
      "contentType": "text/html",
      "txt": "Texto completo entregado por este estudiante..."
    },
    "proveedor_llm": "modelo"
  }'
```

En el ejemplo, el body ya no repite el título largo, la descripción completa,
los cuatro criterios ni sus 16 ratings. El `submission.id`, `submission.userId`
y el contenido de la entrega sí son propios de cada estudiante y se envían en
cada trabajo. Para una entrega de archivo, reemplaza `attachment.txt` por
`attachment.name`, `contentType` y `url` descargable por el HUB.

Usa el `context_id` devuelto por `/context/init` en el primer envío del lote.
El acuse `202` de esa primera evaluación devolverá el contexto de evaluación
persistido para institución/curso/tarea; almacénalo y úsalo como
`X-Context-ID` para los envíos posteriores del mismo grupo. No cruces ese ID
entre cursos, tareas o instituciones. Si no se precargó el slot, envía la
descripción y rúbrica en la primera evaluación; a partir del `context_id`
devuelto, las siguientes solicitudes pueden usar el payload reducido anterior.

El slot evita repetir datos comunes **en la integración LMS → HUB** y permite
reutilizar la identidad/contexto de la tarea. No elimina la rúbrica del trabajo
evaluativo: el modelo necesita los criterios para calificar cada ensayo. El
compilador reduce el bloque de contexto institucional; no debe anunciarse el
tamaño de la rúbrica como ahorro de tokens de inferencia, porque se procesa en
cada evaluación.

El servidor debe poder descargar `attachment.url` sin depender de cookies o
sesión interactiva del navegador. Si la URL requiere autenticación que el HUB no
tiene, el integrador debe entregar el texto extraído por un canal admitido (por
ejemplo `attachment.txt`/`submission.body`) o configurar un mecanismo de acceso
servidor a servidor. No se deben registrar URLs firmadas ni tokens de descarga
en logs del LMS.

### 16.3. Guardar el acuse y asociar el trabajo

Respuesta ilustrativa `202 Accepted`:

```json
{
  "status": "ENCOLADO",
  "estado": "PENDING",
  "evaluation_id": "eval_57423885",
  "context_id": "ctx_lms_...",
  "institution_id": "UNIVERSIDAD_TECNICA_PARTICULAR_DE_LOJA_UTPL",
  "course_id": "79871",
  "assignment_id": "992586",
  "user_id_canvas": "182199",
  "submission_id": "57423885"
}
```

El LMS debe persistir como mínimo:

- `evaluation_id`: clave para consultar el estado.
- `submission_id`, `user_id_canvas`, `course_id` y `assignment_id`: claves para
  correlacionar el resultado con la entrega y el libro de calificaciones.
- `context_id`: referencia de auditoría al contexto compartido.
- Fecha de recepción y último estado consultado.

No se debe construir el `evaluation_id` en el LMS ni asumir que una respuesta
`202` contiene la evaluación. En reintentos de envío con el mismo ID de entrega,
el HUB puede devolver el mismo trabajo si ya está pendiente, procesándose o
evaluado; el LMS debe conservar el `evaluation_id` devuelto y evitar crear
registros duplicados.

### 16.4. Consultar el estado y recolectar el resultado

El endpoint de consulta es:

```text
GET /ensayos/estado/{evaluation_id}
```

Ejemplo:

```bash
curl -i http://localhost:10000/ensayos/estado/eval_57423885 \
  -H "Accept: application/json"
```

Interpretación de respuesta:

| HTTP / `estado` o `status` | Significado | Acción del LMS |
|---|---|---|
| `200`, `PENDING` | Aceptada; aún no tomada por el worker. | Esperar y consultar de nuevo. |
| `200`, `EVALUATING` | El worker está procesando la entrega. | Esperar y consultar de nuevo. |
| `200`, `EVALUATED` o resultado con `status: EVALUADO` | Resultado disponible. | Persistirlo y sincronizar nota/feedback/rúbrica con el LMS. |
| `200`, `ERROR` | El proceso terminó con error. | Registrar `error` y activar el mecanismo de recuperación/soporte del LMS. |
| `404`, `NOT_FOUND` | El HUB no encuentra ese ID en su backend actual. | Verificar el ID, la URL/instancia consultada y si el trabajo fue encolado; no tratarlo como “sigue procesando”. |

Ejemplo de respuesta mientras espera:

```json
{
  "evaluation_id": "eval_57423885",
  "estado": "EVALUATING",
  "context_id": "ctx_lms_...",
  "submission_id": "57423885",
  "course_id": "79871",
  "assignment_id": "992586",
  "user_id_canvas": "182199"
}
```

El cliente debe hacer polling con espera entre consultas y un plazo máximo
configurable; no debe consultar en un bucle inmediato. Como valor inicial, puede
esperar 3–5 segundos y aplicar backoff progresivo (por ejemplo, hasta 15–30
segundos) mientras el estado sea `PENDING` o `EVALUATING`. El tiempo total
depende de la descarga del archivo y del proveedor/modelo LLM, no solo del
intervalo interno del worker.

Cuando termina, el mismo GET devuelve el resultado canónico, por ejemplo:

```json
{
  "status": "EVALUADO",
  "evaluation_id": "eval_57423885",
  "context_id": "ctx_lms_...",
  "submission_id": "57423885",
  "course_id": "79871",
  "assignment_id": "992586",
  "user_id_canvas": "182199",
  "nota_final": 8.5,
  "escala_maxima": 10,
  "feedback": "La argumentación es clara; se recomienda integrar mejor las fuentes.",
  "rubric_assessment_canvas": {
    "_2541": {
      "points": 4,
      "rating_id": "_r2",
      "comments": "La argumentación es pertinente, aunque algunos puntos requieren mayor sustento."
    },
    "_2542": {
      "points": 4.5,
      "rating_id": "_r4",
      "comments": "Las fuentes son pertinentes; falta uniformidad en las citas."
    }
  },
  "datos_lms": {
    "id_lms": "CANVAS",
    "course_id": "79871",
    "assignment_id": "992586",
    "user_id_canvas": "182199",
    "submission_id": "57423885",
    "submission_type": "online_upload"
  }
}
```

Para Canvas, el LMS usa `nota_final`, `feedback` y
`rubric_assessment_canvas` al actualizar SpeedGrader mediante la API de Canvas.
Debe verificar que el `submission_id`, `user_id_canvas`, `course_id` y
`assignment_id` del resultado coincidan con la entrega local antes de aplicar
una nota. El callback `webhook_lms_url`, si se usa, no reemplaza el GET de
estado como fuente canónica descrita por este contrato.

### 16.5. Alcance de la consulta de cola

El GET documentado consulta **un trabajo por `evaluation_id`**. No existe en
este contrato un endpoint `GET` para listar/buscar todos los trabajos de la
cola, filtrar por curso o descargar un lote de estados. Si el panel del LMS
necesita una vista de seguimiento, debe mantener su propio registro de los
`evaluation_id` recibidos y consultar cada uno, o acordar la incorporación de
un endpoint administrativo específico.

La cola es asíncrona y el worker procesa un trabajo a la vez por instancia. El
LMS no debe mantener abierta la petición de envío hasta completar la inferencia:
debe guardar el acuse `202`, continuar su flujo y recolectar el resultado con
GET. La documentación interactiva de la instancia (`/docs`) y su contrato
OpenAPI (`/openapi.json`) permiten verificar rutas y esquemas publicados.
