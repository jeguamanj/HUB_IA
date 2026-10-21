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

Para desarrollo local se conserva el modo heredado con `HUB_API_TOKEN`. Para
staging o producción usa `API_CLIENT_AUTH_ENABLED=true` y credenciales
individuales por integración; consulta [INSTRUCCIONES_USO_API.md](INSTRUCCIONES_USO_API.md)
para emitirlas, consultar metadatos y gestionar su expiración, rotación y
revocación.

Configura `HUB_API_TOKEN` para las operaciones de API y `HUB_DOCS_USERNAME` /
`HUB_DOCS_PASSWORD` para abrir `/docs`, `/redoc` y `/openapi.json`. Swagger
solicita autenticación HTTP Basic para cargar la documentación y, por separado,
el Bearer token para ejecutar operaciones. Solo `GET /health` queda público.
Si faltan credenciales, los recursos correspondientes responden `503`.
`GET /ready` también es público y solo informa disponibilidad tras completar
el arranque.

```powershell
uv run uvicorn API.server:app --port 10000 --reload
```

- **URL Base:** `http://localhost:10000`
- **Healthcheck público:** `http://localhost:10000/health`
- **Documentación Swagger UI:** `http://localhost:10000/docs`
- **Documentación Redoc:** `http://localhost:10000/redoc`

---

## 📍 Endpoints Principales

| Prefijo | Módulo | Descripción |
| :--- | :--- | :--- |
| `/context` | Contexto Institucional | Registro, consulta y enriquecimiento por ranuras de sesiones institucionales (`/init`, `/{id}`, `/{id}/slot/{slot}`). |
| `/health` | Health | Healthcheck público mínimo para balanceadores y monitoreo. |
| `/echo` | Echo | Servicio de eco de prueba (requiere Bearer token). |
| `/llm` | Chat LLM & Modelos | Chat con inyección de contexto (`/chat`), diagnóstico Ollama (`/ollama/status`, `/ollama/models`) y catálogo de modelos free (`/models/free`). |
| `/api` | Zoom Reports | Generación y exportación de reportes Excel (`/zoom/report/excel`). |
| `/zoom/manage` | Zoom Management | Configuración de sesiones, hosts alternativos y panelistas. |
| `/zoom/licenses` | Zoom Licenses | Evaluación y auditoría de licencias Zoom. |
| `/ensayos` | Evaluador Ensayos | Evaluación síncrona (`/evaluar-flujo-completo`) y flujo asíncrono LMS (`/webhook-canvas`, `/enqueue`, `/estado/{evaluation_id}`) con rúbricas y contexto institucional. |

Consulta [INSTRUCCIONES_USO_API.md](INSTRUCCIONES_USO_API.md) para ver ejemplos detallados de payloads y respuestas de cada endpoint.

Consulta [PLAN_HUB.md](PLAN_HUB.md) para revisar los pendientes y las etapas de seguridad y gestión de contexto LLM, y [DEPLOYMENT_PRODUCTION.md](DEPLOYMENT_PRODUCTION.md) para la preparación de Azure y Ubuntu. Los runbooks no autorizan por sí mismos un despliegue.

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
4. **Filtrado Anti-Desperdicio de Tokens en Webhooks:** Al procesar webhooks de Canvas LMS, el HUB separa la telemetría administrativa (`courseId`, `submissionId`, `userId`, logística de entrega) de la directiva evaluativa. El LLM recibe el ensayo, la rúbrica y una directiva institucional comprimida al presupuesto de tokens configurado, evitando inyectar metadatos superfluos al modelo.

### Evaluación masiva desde Canvas

`POST /ensayos/webhook-canvas` encola cada entrega y responde `202 Accepted` con `evaluation_id`,
`context_id` y datos de correlación. El LMS consulta `GET /ensayos/estado/{evaluation_id}` hasta
recibir el resultado o un estado de error; no debe esperar la calificación en la respuesta del webhook.

El contexto común de una tarea se identifica de forma estable por institución, curso y assignment, y
persiste descripción, rúbrica, escala y metadatos compartidos en la base configurada. Por tanto, todos
los alumnos de la misma tarea reutilizan el contexto, mientras `user_id_canvas` y `submission_id`
siguen asociados a cada job individual. Para persistencia durable en SQLite/PostgreSQL, instalar el
extra de base de datos (`uv sync --extra db`) y configurar `DB_DSN`. `DATABASE_URL`
se admite como alias heredado únicamente si `DB_DSN` no está definida; si ambas
están definidas, prevalece `DB_DSN`. Un valor vacío deja los backends en memoria
y no proporciona durabilidad.

Para pruebas de integración con PostgreSQL, usa una base aislada `hub_test` y un
rol dedicado que no sea superusuario. Ejecuta como administrador de PostgreSQL:

```sql
CREATE ROLE hub_test_user LOGIN PASSWORD 'replace_with_a_local_secret';
CREATE DATABASE hub_test OWNER hub_test_user ENCODING 'UTF8';
```

Configura en `.env` local (sin subirlo al repositorio) un DSN SQLAlchemy async:

```dotenv
DB_DSN=postgresql+asyncpg://hub_test_user:URL_ENCODED_PASSWORD@localhost:5432/hub_test
```

En desarrollo, el `lifespan` aplica migraciones pendientes al iniciar. En
producción, primero sigue el apartado
[Aprovisionamiento PostgreSQL de producción](#aprovisionamiento-postgresql-de-producci%C3%B3n)
para elegir el flujo según exista o no la base. El script de base nueva ya
instala las migraciones; para una base existente, aplica las migraciones
pendientes con `python -m API.core.postgres_migrations apply` en un paso
administrativo usando `MIGRATION_DB_DSN` (rol DDL). El proceso de API usa
`DB_DSN` con un rol runtime sin permisos DDL y solo verifica que el esquema esté
al día. Si faltan migraciones, producción no inicia. No guardes ambas
credenciales en el entorno runtime de la API.
Si la contraseña contiene caracteres reservados de URL, deben codificarse.
Mantén PostgreSQL de pruebas separado de cualquier base con datos reales.

### Aprovisionamiento PostgreSQL de producción

El script `python -m scripts.prepare_production_db` **solo se ejecuta para
crear desde cero una base que todavía no existe**, junto con dos roles nuevos:
uno DDL (`hub_migrator`) y uno runtime (`hub_runtime`). También instala las
migraciones actuales y configura los permisos iniciales. Ejecútalo una sola
vez, desde una versión aprobada del repositorio y con autorización/acceso DBA.

**Si la base todavía no existe:**

1. Confirma que tampoco existen los roles que vas a crear. El script rechaza
   tanto una base existente (aunque esté vacía) como cualquiera de esos roles.
2. Ejecuta desde la raíz del proyecto:

   ```powershell
   python -m scripts.prepare_production_db
   ```

3. En el prompt oculto, proporciona el DSN de una base administrativa
   (`postgres` u otra base existente), con usuario DBA y TLS si la conexión es
   remota. El DSN debe incluir usuario y contraseña; codifica como URL los
   caracteres reservados. El script oculta la entrada y no la guarda. Después
   ingresa el nombre nuevo de la base, los nombres/contraseñas de los roles y
   confirma escribiendo exactamente `CREAR <nombre_de_base>`.

**Si la base ya existe, no ejecutes ese script**, aunque solo falte el rol
runtime o la base parezca vacía. No intentes resolverlo agregando el usuario
DBA al DSN: ese requisito solo corrige el formato de conexión para el flujo de
base nueva, no convierte el script en una herramienta de actualización. Sigue
la guía [Base existente y rol runtime pendiente](migrations/README.md#base-existente-y-rol-runtime-pendiente)
para crear/verificar el rol runtime, revisar el ledger y aplicar migraciones
con el rol DDL. Si ya hay esquema o historial de migraciones, detente ante
discrepancias y resuélvelas con backup antes de modificarlo.

En ambos flujos, la API debe usar `DB_DSN` con el rol runtime. `MIGRATION_DB_DSN`
es solo para aplicar migraciones mediante
`python -m API.core.postgres_migrations apply`; **no es el comando de
aprovisionamiento** y no debe quedar configurado en el servicio runtime.

Para `APP_ENV=production`, configura `API_CLIENT_AUTH_ENABLED=true`,
`HUB_DOCS_USERNAME`, `HUB_DOCS_PASSWORD` (mínimo 16 caracteres),
`DB_DSN=postgresql+asyncpg://...` y
`ATTACHMENT_ALLOWED_HOSTS` con los dominios del LMS/CDN separados por coma.
El arranque falla si falta alguno. `/health` informa liveness y `/ready` solo
queda listo después de inicializar la cola. Los tokens individuales tienen
scopes por institución; Zoom privado/MCP pasa a fase 2. Para el PMV on-prem se
incluyen plantillas de systemd y Nginx con TLS, bind local y límite de
peticiones; deben parametrizarse para el servidor y dominio reales antes de
instalarlas. Consulta [DEPLOYMENT_PRODUCTION.md](DEPLOYMENT_PRODUCTION.md).

### Migraciones de evaluaciones PostgreSQL

En desarrollo, con `DB_DSN` apuntando a una base PostgreSQL, el `lifespan`
aplica automáticamente los scripts numerados pendientes en orden. Para
producción, aplica primero las migraciones con `MIGRATION_DB_DSN` mediante
`python -m API.core.postgres_migrations apply`; no arranques el proceso de API
con el rol DDL. Los comandos `psql` siguientes son una alternativa manual para
desarrollo o pruebas:

```powershell
psql -h localhost -p 5433 -U hub_test_user -d hub_test -v ON_ERROR_STOP=1 -f migrations\001_initial_evaluation_persistence.sql
psql -h localhost -p 5433 -U hub_test_user -d hub_test -v ON_ERROR_STOP=1 -f migrations\002_rename_evaluation_submission_groups.sql
psql -h localhost -p 5433 -U hub_test_user -d hub_test -v ON_ERROR_STOP=1 -f migrations\003_api_client_tokens.sql
psql -h localhost -p 5433 -U hub_test_user -d hub_test -v ON_ERROR_STOP=1 -f migrations\004_production_runtime_tables.sql
```

Para el despliegue, prefiere el comando del runner con el DSN de migración
inyectado desde el entorno seguro, no desde un argumento ni en el historial
interactivo:

```powershell
python -m API.core.postgres_migrations apply
```

La migración `002` renombra el contador de intentos a
`evaluation_submission_groups` para reservar `submission_groups` al tracker
existente. No vuelvas a ejecutar migraciones ya aplicadas; consulta su registro:

```sql
SELECT version, name, checksum, applied_at
FROM hub_schema_migrations
ORDER BY version;
```

La descripción de las siete tablas, sus relaciones, columnas y consideraciones
operativas está en [migrations/README.md](migrations/README.md).

El flujo asíncrono del evaluador escribe en `evaluation_jobs`, reserva y consume
intentos en `evaluation_submission_groups`, registra eventos e intentos técnicos,
recupera leases vencidos y persiste telemetría LLM/MCP sin prompts ni argumentos.
`/ensayos/estado/{evaluation_id}` lee ese job. La ruta síncrona y el tracker
legado todavía no comparten la misma transacción. El runner omite migraciones ya
registradas por versión, pero aún no valida checksums; resolver la discrepancia
conocida de `001` antes de automatizar producción.

El proveedor OpenAI usa `OPENAI_API_KEY`, `OPENAI_ENDPOINT` y `OPENAI_MODEL`.
Los reportes Zoom conservan rutas y formato Excel, comparten OAuth/helpers y
ejecutan handlers síncronos en el threadpool. Artemis queda fuera del PMV actual.
