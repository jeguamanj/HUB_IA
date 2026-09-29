# 🚀 MCP HUB SERVER IA

Servidor centralizado basado en **FastAPI** y el ecosistema **MCP (Model Context Protocol)** para la gestión y orquestación de servicios inteligentes:

- 📊 **Reportes Avanzados de Zoom**: Generación de informes analíticos en Excel de reuniones y webinars simples y recurrentes (participantes, inscritos con preguntas personalizadas, Q&A).
- ⚙️ **Gestión y Configuración de Sesiones Zoom**: Actualización de configuraciones, hosts alternativos, altas/bajas/reemplazos de panelistas y obtención de enlaces (`start_url` y `join_url`).
- 👥 **Auditoría de Licencias Zoom**: Reglas de optimización de asignación de licencias para cuentas corporativas.
- 🎓 **Evaluación Pedagógica con IA (LMS / Canvas)**: Corrección y retroalimentación automatizada de ensayos académicos mediante rúbricas dinámicas, soporte multiformato (.pdf, .docx, texto plano) e integración bidireccional con Canvas LMS con soporte para Webhooks nativos y Patrón Cero Duplicación de Contexto (ahorro de hasta 800 tokens de prompt por entrega).
- 🧠 **Inyector de Contexto Institucional (Namespaced Context Fabric)**: Núcleo de gobernanza y equidad de IA que asegura ahorro de tokens (reutilización vía `X-Context-ID`), estandarización transversal de directivas y políticas éticas en todos los MCP tools, y escalabilidad horizontal modular por ranuras (`slots["lms"]`, `slots["zoom"]`, `slots["crm"]`, etc.).
- 🖥️ **Inferencia Local On-Premise (Ollama)**: Soporte nativo para modelos locales y abiertos sin costo de tokens ni salida a la nube para máxima privacidad institucional.
- 💬 **Proveedores LLM con Catálogo Dinámico y Fallback Inteligente**: Consulta en tiempo real de modelos gratuitos en OpenRouter (`openrouter-free`, `modelo`), ordenados por scoring de estabilidad y ventana de contexto, con fallback tolerante a fallos, y clientes para `deepseek`, `gemini/gemma`, `openai` y `ollama`.
- 📬 **Broker de Mensajes Asíncrono**: Adaptador STOMP para colas ActiveMQ Artemis.

---

## 📁 Estructura del Proyecto

```text
MCP_HUB_SERVER_IA/
├── API/
│   ├── core/                   # Núcleo de gobernanza y contexto
│   │   ├── __init__.py             # Exportaciones del motor de contexto
│   │   └── context_engine.py       # ContextStore, InstitutionalContext y Middleware
│   ├── llm_providers/          # Clientes LLM y Catálogo Dinámico
│   │   ├── openrouter_catalog.py   # Servicio de consulta, scoring y caché de modelos free
│   │   ├── modelo.py               # Fallback automático inteligente entre modelos gratuitos
│   │   ├── ollama.py               # Proveedor On-Premise local con diagnóstico
│   │   ├── deepseek.py             # Cliente DeepSeek vía OpenRouter
│   │   ├── gemini.py               # Cliente Google Gemma vía OpenRouter
│   │   └── openai.py               # Cliente OpenAI
│   ├── services/               # Microservicios modulares
│   │   ├── artemis_adapter.py          # Listener STOMP para colas LMS
│   │   ├── context_service.py          # Gestión y consulta de sesiones de contexto institucional
│   │   ├── echo_service.py             # Servicio de eco / healthcheck
│   │   ├── essay_evaluator_mcp.py      # Evaluador de ensayos con rúbrica y herramientas FastMCP
│   │   ├── llm_chat_service.py         # Router de chat LLM, diagnóstico Ollama y catálogo
│   │   ├── zoom_license_management.py  # Auditoría y reglas de licencias Zoom
│   │   ├── zoom_management_service.py  # Gestión de webinars/meetings y panelistas
│   │   └── zoom_reports.py             # Generador de reportes Excel
│   ├── utils/                  # Utilidades (zoom_api_utils, document_parser)
│   ├── config.py               # Configuración centralizada de servicios y credenciales
│   └── server.py               # Aplicación principal FastAPI con ciclo de vida (lifespan)
├── image/                      # Activos gráficos (encabezado_informe.png)
├── storage/                    # Almacenamiento local de evaluaciones y reportes generados
├── INSTRUCCIONES_USO_API.md    # Manual exhaustivo de endpoints y contratos JSON
├── pyproject.toml              # Dependencias y configuración uv
├── uv.lock                     # Lockfile de dependencias
└── .env                        # Variables de entorno y credenciales (privado)
```

---

## 🛠️ Instalación y Requisitos

Requisitos: **Python >= 3.11** y gestor de paquetes **uv**.

```powershell
# 1. Instalar dependencias con uv
uv sync

# 2. Configurar variables de entorno en el archivo .env
# (OPENROUTER_API_KEY, credenciales ZOOM, CANVAS_API_TOKEN, etc.)
```

---

## ▶️ Ejecutar el Servidor

```powershell
uv run uvicorn API.server:app --port 10000 --reload
```

- **URL Base:** `http://localhost:10000`
- **Documentación Swagger UI:** `http://localhost:10000/docs`
- **Documentación Redoc:** `http://localhost:10000/redoc`

---

## 📍 Endpoints Principales

| Prefijo | Módulo | Descripción |
| :--- | :--- | :--- |
| `/context` | Contexto Institucional | Registro, consulta y enriquecimiento por ranuras de sesiones institucionales (`/init`, `/{id}`, `/{id}/slot/{slot}`). |
| `/echo` | Echo | Verificación rápida de disponibilidad. |
| `/llm` | Chat LLM & Modelos | Chat con inyección de contexto (`/chat`), diagnóstico Ollama (`/ollama/status`, `/ollama/models`) y catálogo de modelos free (`/models/free`). |
| `/api` | Zoom Reports | Generación y exportación de reportes Excel (`/zoom/report/excel`). |
| `/zoom/manage` | Zoom Management | Configuración de sesiones, hosts alternativos y panelistas. |
| `/zoom/licenses` | Zoom Licenses | Evaluación y auditoría de licencias Zoom. |
| `/ensayos` | Evaluador Ensayos | Flujo completo de evaluación con rúbricas, contexto institucional e integración Canvas LMS. |

Consulta [INSTRUCCIONES_USO_API.md](INSTRUCCIONES_USO_API.md) para ver ejemplos detallados de payloads y respuestas de cada endpoint.

---

## ⚡ Motor LLM Ultrarrápido (Hedged Requests & Circuit Breaker)

El proveedor de modelos gratuitos (`modelo.py` / `/llm/chat`) implementa una arquitectura de **conmutación especulativa no bloqueante** para evitar demoras por saturación upstream en OpenRouter:

- **Carrera Especulativa (Hedged Racing):** Dispara el mejor candidato; si tras **3.5 segundos** (`LLM_SOFT_TIMEOUT`) no ha respondido, inicia el siguiente modelo en paralelo. El primero con respuesta válida gana y cancela el resto.
- **Circuit Breaker:** Detecta errores (429, 502, timeouts) y coloca los modelos inestables en enfriamiento temporal (3 minutos), evitando reintentos lentos en llamadas posteriores.
- **Promoción Dinámica:** Modela el ranking histórico en memoria, asegurando latencias típicas de **1.7 a 3.5 segundos** (reducción del 95% frente a los 56s de modelos en cola).

### Variables de Configuración Opcionales (`.env`):
| Variable | Default | Descripción |
| :--- | :--- | :--- |
| `LLM_SOFT_TIMEOUT` | `3.5` | Segundos antes de disparar el siguiente modelo en paralelo. |
| `LLM_HARD_TIMEOUT` | `12.0` | Timeout estricto máximo por petición individual a un modelo. |
| `LLM_MAX_CONCURRENT` | `2` | Número máximo de modelos compitiendo simultáneamente. |
| `OPENROUTER_MODELS_CACHE_TTL` | `1800` | Tiempo de vida de la caché del catálogo dinámico (30 min). |
| `OLLAMA_ENDPOINT` | `http://localhost:11434` | Endpoint del servidor de inferencia On-Premise de Ollama. |
| `OLLAMA_MODEL` | `llama3.3:latest` | Modelo local por defecto para inferencias On-Premise. |
| `OLLAMA_TIMEOUT` | `60.0` | Timeout para inferencias locales en Ollama. |

---

## 🧠 Inyector de Contexto Institucional (Ahorro de Tokens y Equidad)

Para convertir el HUB en el **Core de IA Institucional**, el sistema desacopla el contexto de las herramientas mediante **Namespaced Context Fabric**:

1. **Ahorro de Tokens:** Se registra el contexto una sola vez (`POST /context/init`) y se reutiliza mediante el header HTTP `X-Context-ID: ctx_abc123`, ahorrando millones de tokens en flujos de N8N o llamadas repetitivas.
2. **Equidad de Contexto:** Cualquier herramienta MCP invocada (Chat, Evaluador de Ensayos, Zoom, o futuras integraciones) consulta `get_current_context()`, aplicando automáticamente la misma política ética, escala evaluativa y tono pedagógico.
3. **Escalabilidad por Ranuras:** Arquitectura modular por slots (`lms`, `zoom`, `crm`, `custom`), permitiendo incorporar nuevas herramientas MCP sin alterar el modelo global ni realizar migraciones de datos.
4. **Filtrado Anti-Desperdicio de Tokens en Webhooks:** Al procesar webhooks de Canvas LMS o Moodle, el HUB separa la telemetría administrativa (`courseId`, `submissionId`, `userId`, logística de entrega) de la directiva evaluativa. El LLM recibe exclusivamente el ensayo, la rúbrica y una directiva compacta de una línea (`compact=True`), evitando inyectar metadatos superfluos al modelo.

