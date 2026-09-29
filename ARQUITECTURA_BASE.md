# 🏗️ ARQUITECTURA BASE — MCP HUB SERVER IA

> **Propósito:** Mapa de referencia técnico del proyecto y **plan oficial de desarrollo**, redactado como **semilla de contexto** para agentes especializados. Documenta componentes, flujos, contratos, deuda técnica y el **Roadmap de Agentes Especializados (sección §7)** que ordena su ejecución por fases.

---

## 1. 📌 Visión General

Servidor centralizado en **Python 3.11+ / FastAPI** que actúa como **CORE de IA institucional**:
orquesta múltiples herramientas **MCP (Model Context Protocol)**, expone una **capa REST** para integraciones
externas (N8N, Canvas LMS, brokers STOMP/Artemis) y centraliza **directivas institucionales de IA**
(contexto, tono, escala evaluativa, políticas éticas) para que **todas** las herramientas compartan la misma
identidad sin duplicar prompts.

**Dominios cubiertos hoy:**
- Evaluación pedagógica de ensayos con rúbrica (LLM + Canvas LMS).
- Reportes y gestión de sesiones Zoom (meetings/webinars).
- Auditoría de licencias Zoom.
- Broker de mensajes asíncrono ActiveMQ Artemis (STOMP).
- Inferencia LLM con catálogo dinámico y fallback ultrarrápido.

---

## 2. 🧱 Arquitectura en 3 Capas (patrón mencionado en SYNERGY de tus equipos)

El proyecto se estructura naturalmente en **3 capas** que coinciden con la organización de equipos/agentes
que quieres formar:

```
┌───────────────────────────────────────────────────────────────────────┐
│                     1. CORE ARCHITECT (Núcleo)                        │
│   FastAPI lifespan · Middlewares · config.py · server.py ·        │
│   Ciclo de vida (Artemis) · Registro de routers                      │
└───────────────▲───────────────────────────────────────────────────────┘
                │  X-Context-ID · X-Tenant-ID · headers institucionales
┌───────────────▼───────────────────────────────────────────────────────┐
│                     2. CONTEXT FABRIC (Telar de Contexto)             │
│   API/core/context_engine.py                                          │
│   InstitutionalContext · ContextStore (TTL) · contextvars ·           │
│   to_system_instruction(compact=True) · slots{lms,zoom,crm,custom}    │
└───────────────▲───────────────────────────────────────────────────────┘
                │  get_current_context() → inyección transparente al LLM
┌───────────────▼───────────────────────────────────────────────────────┐
│                     3. MCP INTEGRATOR (Integración)                   │
│   API/services/*            → Herramientas MCP (FastMCP @mcp.tool)  │
│   API/llm_providers/*       → Motor LLM (catálogo + fallback)        │
│   API/utils/*                → zoom_api_utils · document_parser       │
│   API/core/context_engine   → storage in-memory (Redis-ready)         │
└───────────────────────────────────────────────────────────────────────┘
```

**Puntos de acoplamiento (dependencias):**
- `server.py` importa todos los routers y el middleware de contexto.
- Todos los servicios consultan `get_current_context()` del **CONTEXT FABRIC** para inyectar directivas al LLM.
- Las herramientas MCP del **MCP INTEGRATOR** son reutilizadas tanto por los routers REST (`/ensayos`, `/llm`)
  como por el adaptador Artemis (`artemis_adapter.py`).

---

## 3. ⚙️ Árbol del Proyecto (con roles)

```text
MCP_HUB_SERVER_IA/
├── API/
│   ├── server.py                     # CORE: app FastAPI, lifespan, montaje de routers
│   ├── config.py                     # CORE: credenciales + ACTIVE_SERVICES
│   ├── core/
│   │   ├── __init__.py               # EXPORTS del motor de contexto
│   │   └── context_engine.py         # CONTEXT FABRIC: InstitutionalContext, ContextStore, Middleware
│   ├── llm_providers/                # MCP INTEGRATOR (Motor LLM)
│   │   ├── openrouter_catalog.py     #   Catálogo dinámico + scoring + caché TTL de modelos free
│   │   ├── modelo.py                 #   Hedged Racing + Circuit Breaker (motor principal)
│   │   ├── ollama.py                 #   Proveedor On-Premise local
│   │   ├── deepseek.py               #   Cliente DeepSeek vía OpenRouter
│   │   ├── gemini.py                 #   Cliente Google Gemma vía OpenRouter
│   │   └── openai.py                 #   ⚠️ STUB (solo mock, sin implementación real)
│   ├── services/                     # MCP INTEGRATOR (Herramientas/dominios)
│   │   ├── essay_evaluator_mcp.py    #   FastMCP: obtener_ensayos, evaluar_con_rubrica, guardar_reporte, consultar_modelos
│   │   ├── llm_chat_service.py       #   REST: /chat, /models/free, /ollama/*
│   │   ├── context_service.py        #   REST: /context/* (CRUD + slots)
│   │   ├── zoom_reports.py           #   Excel de reuniones/webinars (±1500 líneas, con DEUDA)
│   │   ├── zoom_management_service.py#   Gestión de sesiones y panelistas
│   │   ├── zoom_license_management.py#   Auditoría de licencias
│   │   ├── artemis_adapter.py        #   Listener STOMP → consume herramientas MCP
│   │   └── echo_service.py           #   Healthcheck
│   ├── utils/
│   │   ├── document_parser.py        #   PDF/DOCX/TXT/URL/Base64 → texto estructurado
│   │   └── zoom_api_utils.py         #   OAuth Zoom + helpers de sesión/licencias
│   └── __pycache__/                  # ⚠️ artefactos de ejecución (no versionar)
├── storage/evaluaciones/             # Reportes generados (JSON + Markdown por id_entrega)
├── image/                            # Banner para encabezados de Excel (ZOOM_HEADER_IMAGE)
├── INSTRUCCIONES_USO_API.md          # Manual exhaustivo de contratos JSON
├── readme.md                         # Guía general
├── pyproject.toml / uv.lock / requirements.txt   # deps (uv)
└── .env                              # credenciales (NO versionar)
```

---

## 4. 🔄 Flujos Transversales Clave

### 4.1. Inyección de Contexto Institucional (el corazón)
```text
Cliente → POST /context/init → { context_id: "ctx_abc123" }
Cliente → POST /ensayos/evaluar-flujo-completo
           header: X-Context-ID: ctx_abc123
                │
                ▼  InstitutionalContextMiddleware (context_engine)
                ▼  context_store.get(ctx_id) → set _current_context_var
                ▼  essay_evaluator_mcp → get_current_context()
                ▼  _construir_prompt_evaluacion() → ctx.to_system_instruction(compact=True)
                ▼  LLM recibe directiva compacta + ensayo + rúbrica (sin metadatos)
```
- **Modo 1:** `X-Context-ID` (reutilización, máximo ahorro).
- **Modo 2:** headers efímeros `X-Institution-ID` / `X-Tenant-ID` / `X-User-Role` (TTL 5 min).
- **Modo 3:** integración N8N (init → reusar → enriquecer slot → delete/expira).

### 4.2. Pipeline de Evaluación de Ensayos (3 tools MCP)
```text
evaluar-flujo-completo
  ├─ 0. Sincroniza/crea InstitutionalContext (persiste rúbrica en slot lms)
  ├─ 1. obtener_ensayos()     → parse_document_input() (URL/local/Base64/texto)
  ├─ 2. evaluar_con_rubrica() → _construir_prompt_evaluacion() → LLM → parseo JSON defensivo
  └─ 3. guardar_reporte()     → storage/evaluaciones/{id}/reporte_{json,md} + notificación webhook
```
- Acepta payload nativo Canvas (`assignment`/`submission`/`attachment`), formato directo (`origen_ensayo`/`origen_rubrica`),
  o **Patrón Cero Duplicación** (rúbrica solo en el contexto + webhook ultraligero).
- Genera `rubric_assessment_canvas` listo para la API REST de Canvas (SpeedGrader).

### 4.3. Motor LLM de Conmutación Ultrarrápida (Hedged Racing)
```text
/llm/chat provider="modelo"
  → openrouter_catalog.get_prioritized_free_model_ids()  (scoring + caché 30 min)
  → modelo._async_hedged_chat_fallback():
       [0.0s] dispara Modelo #1
       [3.5s] soft-timeout → dispara Modelo #2 en paralelo (máx. 2 concurrentes)
       [ganador] el primero con respuesta válida cancela al resto
  → Circuit Breaker: 429/502/timeout → cooldown 3 min (ModelHealthTracker)
```
- Env: `LLM_SOFT_TIMEOUT=3.5`, `LLM_HARD_TIMEOUT=12.0`, `LLM_MAX_CONCURRENT=2`, `OPENROUTER_MODELS_CACHE_TTL=1800`.
- Ollama (on-premise) disponible para datos confidenciales (`/llm/ollama/*`).

---

## 5. 📍 Mapa de Endpoints (REST)

| Ruta | Servicio | Métodos |
| :--- | :--- | :--- |
| `/context` | context_service | `/init`, `/{id}`, `/{id}/slot/{slot}`, `/active/current`, `DELETE /{id}` |
| `/llm/chat` | llm_chat_service | POST chat con fallback |
| `/llm/models/free`, `/llm/models/refresh` | llm_chat_service | GET/POST catálogo dinámico |
| `/llm/ollama/status`, `/llm/ollama/models` | llm_chat_service | GET diagnóstico on-premise |
| `/ensayos/evaluar-flujo-completo`, `/ensayos/webhook-canvas` | essay_evaluator_mcp | POST pipeline completo |
| `/api/zoom/report/check`, `/api/zoom/report/excel` | zoom_reports | POST check y generación Excel |
| `/zoom/manage/*` | zoom_management_service | PATCH/POST/DELETE/GET sesiones y panelistas |
| `/zoom/licenses/*` | zoom_license_management | GET evaluate/scan/report/csv, POST downgrade |
| `/echo` | echo_service | POST healthcheck |
| `/docs`, `/redoc` | FastAPI | Swagger / Redoc |

---

## 6. 🐛 Deuda Técnica y Puntos de Atención (crítico para continuar el desarrollo)

| # | Área | Problema | Recomendación |
|:--|:---|:---|:---|
| 1 | `openai.py` | Es un **stub** ("respuesta ficticia") — no llama a OpenAI real | Implementar cliente real o eliminarlo del catálogo |
| 2 | `zoom_reports.py` | ~1500 líneas, lógica duplicada con `zoom_api_utils.py`, funciones referenciadas pero indefinidas (`get_participants_with_pagination`, `create_*_sheet` comparten helpers con managers), `raise HTTPException` dentro de `except` de `requests` | Refactorizar en módulos: `zoom_client`, `excel_builder`, `recurrence_service` |
| 3 | Auto-Corrección de JSON LLM | Parseo JSON defensivo con regex (frágil ante modelos nuevos) | Migrar a `response_format`/structured outputs de OpenRouter o validación con Pydantic + reintento |
| 4 | Persistencia | ContextStore es **in-memory** (se pierde en restart; "Redis-ready" declarado pero no implementado) | Implementar backend Redis/TTL real o SQLite para durable sessions |
| 5 | Configuración duplicada | Zoom credenciales en `config.py` y en `zoom_api_utils.py`/`zoom_reports.py` por separado | Centralizar en `config.py` + `get_settings()` |
| 6 | Errores `requests` | `zoom_*` usan `requests` síncrono (bloquea el event-loop de FastAPI) | Migrar a `httpx.AsyncClient` (como ya hace `modelo.py`/`document_parser.py`) |
| 7 | `.env` sin esquema | No hay `.env.example` / validación de credenciales obligatorias | Crear `.env.example` + validación en boot de `config.py` |
| 8 | Pruebas | No se detectan tests automatizados | Añadir `tests/` (pytest) para pipeline de ensayos, contexto y catálogo |
| 9 | `__pycache__` versionados | Artefactos .pyc en el árbol (`.gitignore` incompleto) | Limpiar y ajustar `.gitignore` |

---

## 7. 🗺️ Roadmap de Agentes Especializados (plan oficial)

> **Decisiones de dirección (confirmadas):**
> - 🎯 **Objetivo:** Estabilizar/sanear el código existente **y** ampliar dominios — **priorizando las herramientas MCP**.
> - ✅ **Medida de éxito por agente:** cambio funcional atómico y retrocompatible + tests automatizados + documentación actualizada.
> - 🔀 **Orquestación:** fases secuenciales por equipo, cada una con un **hito verificable** antes de seguir.
> - 📎 **Entregable:** este documento como fuente única de verdad del plan.

### 7.0. Reglas de ejecución (transversales a todos los agentes)

| Regla | Descripción |
| :--- | :--- |
| R1. **Un cambio atómico por agente** | Cada agente produce un único PR reversible y autocontenido. |
| R2. **Retro-compatibilidad obligatoria** | No alterar contractos REST/MCP existentes (headers, payloads, respuestas). Extensiones van *aditivas*. |
| R3. **Tests que prueban el cambio** | `pytest` con casos que cubran la lógica modificada (no solo humo). |
| R4. **Documentación acompañante** | Actualizar `readme.md` y/o `INSTRUCCIONES_USO_API.md` en el mismo PR. |
| R5. **Validación en runtime** | `uv run uvicorn API.server:app --port 10000 --reload` y verificación en Swagger `/docs` de los endpoints afectados antes de dar por cerrado. |
| R6. **No gestionar `.env` real** | Usar `.env.example` como referencia; nunca exponer credenciales en código ni docs. |

### 7.1. Fases y dependencias (orden de ejecución)

```text
FASE 0  (A-lote)  FUNDACIÓN CORE
 GATE 0  ──────────►  FASE 1  (B-lote)  ENDURECER CONTEXT FABRIC
                          GATE 1  ─────►  FASE 2  (C-lote)  AMPLIAR & SANEAR MCP INTEGRATOR
                                              │
                                              └──────────►  FASE 3  (continuo)  NUEVAS HERRAMIENTAS MCP
```
- **Regla de green-to-green:** una fase no comienza hasta que el **gate** de la anterior se cierra (hito verificado).
- **Fase 3 es continua:** puede correr en paralelo con mantenimiento, una vez que Fase 2 estabilizó el motor y los helpers.

---

### FASE 0 — FUNDACIÓN CORE (Equipo A) · *hito: servidor sano y config centralizado*
> 0 es el punto de partida: sin config estable ni tests, ningún otro agente puede validar de forma segura.

| ID | Agente | Misión | Archivos de trabajo | Estado |
| :--- | :--- | :--- | :--- | :--- |
| A0.1 | `boot-settings` | Centralizar la configuración en un módulo único (`get_settings()`), crear `.env.example`, validar credenciales obligatorias al arrancar con mensajes claros | `API/config.py`, `API/server.py`, `.env.example` | ✅ **implementado** (v1) |
| A0.2 | `tests-harness` | Parametrizar `pyproject.toml` (pytest dev-group), crear `tests/` y un *baseline* con mocks (sin llamadas reales a OpenRouter/Zoom) | `pyproject.toml`, `pytest.ini`, `tests/` (conftest, config test) | 🔶 **en curso (infra lista; pytest pendiente de instalar)** |
| A0.3 | `log-config` | Estandarizar logging (nivel, formato, contexto) y reemplazar los `print()` de depuración dispersos por `logger` | `API/core/logging.py` (nuevo), `API/config.py`, `API/server.py`, `modelo.py`, `artemis_adapter.py`, `essay_evaluator_mcp.py`, `zoom_management_service.py` | ✅ **implementado** (v1) — `zoom_reports.py` diferido a C2.4 |

**GATE 0 (se cierra la fase):** servidor arranca limpio, `.env.example` documentado, suite `pytest` en verde y logs estandarizados. A partir de aquí cualquier cambio se puede verificar con la suite.

> **Nota A0.3 (log-config):** se creó `API/core/logging.py` (helper `get_logger()`, `configure_logging()`, formato/nivel por `LOG_LEVEL`). Se migraron a `logger` los `print()` de `server.py`, `modelo.py`, `artemis_adapter.py`, `essay_evaluator_mcp.py` y `zoom_management_service.py` (**0 `print()` restantes** en esos módulos). Los ~110 `print()` de `zoom_reports.py` se **difieren deliberadamente a C2.4 `zoom-refactor`** (FASE 2), porque ese archivo será reestructurado y migrarlos ahora sería trabajo duplicado.

> **Nota de entorno (sandbox):** la instalación de `pytest` (vía `uv`/`pip`) está bloqueada en este entorno por
> restricciones de permisos de subproceso/y caché (`uv` no puede interrogar el intérprete de `.venv`; `ensurepip`
> falla por zona temp). La dependencia queda **declarada** en `pyproject.toml` (`dependency-groups.dev`) y los tests
> escritos esperan ejecutarse con `uv sync --group dev` desde un entorno normal. Los supuestos de los tests de
> `test_config.py` ya fueron verificados manualmente contra el código real.

---

### FASE 1 — ENDURECER CONTEXT FABRIC (Equipo B) · *hito: contexto robusto y reutilizable*
> El Context Fabric es el "cerebro de gobernanza": debe ser sólido y versionable antes de registrar nuevas herramientas.

| ID | Agente | Misión | Archivos de trabajo | Criterios de aceptación (gate 1) |
| :--- | :--- | :--- | :--- | :--- |
| B1.1 | `context-contract` | Esquemas Pydantic **versionados** por slot (`lms`, `zoom`, `crm`, `custom`) + `schema_version` en `InstitutionalContext` + migración forward-compatible que conserva campos desconocidos | `API/core/schemas.py` (nuevo), `API/core/context_engine.py`, `API/core/__init__.py` | ✅ **implementado (v1)** — Slots validados por tipo; contextos sin versión cargan sin romperse (default `SCHEMA_VERSION`); campos futuros nunca se pierden (extra="allow"); `set_slot`/`migrate_all_slots` no destructivos |
| B1.2 | `durable-store` | Backend de persistencia del `ContextStore` con TTL — **SQLite (default) y PostgreSQL**, conmutables vía `DB_DSN` (SQLAlchemy async) y **fallback a memoria con aviso** si el driver/base no está disponible | `API/core/context_backend.py` (nuevo), `API/core/context_engine.py`, `API/config.py`, `.env.example` | ✅ **implementado (v1)** — Contexto sobrevive restart (test); TTL respetado; interfaz de `context_store` intacta (write-through best-effort) |
| B1.3 | `prompt-compiler` | Compilador de system prompt con **presupuesto de tokens medible** por modo y garantía de **no filtrado de metadatos LMS** en modo compact | `API/core/context_engine.py` (`compile_system_prompt`, `SystemPrompt`, `estimate_tokens`), `tests/` | ✅ **implementado (v1)** — compact ≤ `PROMPT_COMPACT_BUDGET` (64); full ≤ `PROMPT_FULL_BUDGET` (256); `estimate_tokens`; sin fuga de course_id/assignment_id/account_id en compact (test) |

**GATE 1 (se cierra la fase):** ~~contexto con esquemas versionados, persistencia durable (TTL) y compilador de prompts con presupuesto de tokens testeado~~ — **✅ GATE 1 CERRADO** (B1.1 `context-contract`, B1.2 `durable-store`, B1.3 `prompt-compiler` implementados y verificados). Las nuevas herramientas MCP podrán reutilizar un contexto fiable y durable.

> **Nota B1.2 (durable-store):** la persistencia usa **SQLAlchemy 2.x async**. SQLite y PostgreSQL comparten interfaz; conmutar solo cambia `DB_DSN` en `.env`:
> ```ini
> # SQLite (default)
> DB_DSN=sqlite+aiosqlite:///./storage/hub.db
> # PostgreSQL
> DB_DSN=postgresql+asyncpg://usuario:pass@host:5432/nombre_db
> ```
> Drivers opcionales instalables con `uv sync --extra db`. Sin driver/service disponible, el HUB **degrada a memoria con aviso por log** y sigue sirviendo (no se cae). Los tests de backend (`tests/test_context_backend.py`) no requieren drivers (solo memoria + lógica raw); el arranque con SQLAlchemy real se valida con `uv sync --extra db`.

---

### FASE 2 — AMPLIAR & SANEAR MCP INTEGRATOR (Equipo C) · *hito: motor LLM y Zoom sólidos*
> Con contexto y tests estables de fondo, aquí se sanean los dos puntos ásperos actuales: el **parseo JSON del LLM** y **Zoom**, además de cerrar el stub de OpenAI.

| ID | Agente | Misión | Archivos de trabajo | Criterios de aceptación (gate 2) |
| :--- | :--- | :--- | :--- | :--- |
| C2.1 | `structured-output` | Migrar el parseo de JSON del evaluador a **structured-outputs** (OpenRouter/Ollama) + reintento ante fallo de schema; mantener la limpieza defensiva residual como fallback | `API/services/essay_evaluator_mcp.py`, `API/llm_providers/modelo.py`, `API/llm_providers/ollama.py` | Evaluación devuelve `EvaluacionResultado` válido sin depender solo de regex (test con respuesta parcial); fallback a Pydantic intacto |
| C2.2 | `openai-real` | Implementar cliente OpenAI real (vía OpenRouter o API OpenAI) reemplazando el stub, o retirarlo limpiamente del catálogo | `API/llm_providers/openai.py` | `provider='openai'` devuelve respuesta real o error claro y controlado; no rompe `essay_evaluator_mcp` |
| C2.3 | `zoom-async` | Migrar las llamadas de `zoom_*` de `requests` (síncrono que bloquea el event-loop) a `httpx.AsyncClient` | `API/utils/zoom_api_utils.py`, `API/services/zoom_management_service.py`, `API/services/zoom_license_management.py` | ✅ **implementado (v1)** — Auth OAuth async (`async_get_zoom_auth_headers`); capa `async_*` en `zoom_api_utils`; endpoints de management/licencias **todos async** (sin `requests`); capa síncrona preservada para `zoom_reports.py`; `/zoom/*` registrados y OK |
| C2.4 | `zoom-refactor` | Extraer de `zoom_reports.py` (~1500 líneas) un **cliente reutilizable** + builders de Excel en módulos `services/zoom/` separados; eliminar duplicación con `zoom_api_utils` (+ migrar sus ~40 `requests` restantes y ~110 `print`) | `API/services/zoom_reports.py` → `API/services/zoom/{client,excel_builder,recurrence}.py` | Sin lógica duplicada crítica; `/api/zoom/report/*` sigue generando Excel idéntico (smoke test); **hito final de la FASE 2** |

**GATE 2 (se cierra la fase):** motor LLM con salida estructurada confiable, OpenAI funcional o retirado, Zoom async y sin duplicación. La base está lista para acoplar dominios nuevos con garantía de no-regresión (gracias a Fase 0–1).

> **Nota C2.3 (zoom-async):** se migraron a `httpx.AsyncClient` los **routers de servicio** (`zoom_management_service.py`, `zoom_license_management.py` — todos sus endpoints quedaron `async`) y se añadió una **capa async** (`async_*`) en `zoom_api_utils.py`. `zoom_reports.py` (~40 `requests` y ~110 `print`) queda **diferido a C2.4 `zoom-refactor`**, por estar destinado a reestructurarse en módulos; mientras tanto su capa síncrona sigue intacta (retro-compatible). Validado: `/zoom/*` el registro en OpenAPI (12 rutas) y los 8 tests de `tests/test_zoom_async.py`.

---

### FASE 3 — NUEVAS HERRAMIENTAS MCP (Equipo D) · *continua, con patrón replicable*
> Una vez estable el *pedestal* (Fase 0–2), cada nueva herramienta MCP sigue el **mismo patrón** y cierra su propio mini-hito. Esta fase puede correr en paralelo con mantenimiento.

| ID | Agente | Misión | Patrón de integración | Criterios de aceptación |
| :--- | :--- | :--- | :--- | :--- |
| D3.1 | `mcp-tool-crm` | Registrar herramienta MCP de CRM (estado de lead/pipeline) reusando `get_current_context()` y su slot `crm` | New `API/services/crm_mcp.py` + REST `/crm` + docs | Tool MCP disponible en `FastMCP`; inyecta directiva de contexto; docs updated |
| D3.2 | `mcp-tool-asistencia` | Herramienta de asistencia académica (consultas FAQ/estudiantes) con contexto institucional | New `API/services/asistencia_mcp.py` + docs | Tool MCP + endpoint REST + ejemplo en `INSTRUCCIONES_USO_API.md` |
| D3.3 | `mcp-tool-zoom-analytics` | Base de Zoom: reportes/analíticas como herramienta MCP reutilizable (aprovecha C2.4) | sobre `services/zoom/*` | Tool MCP expuesta + contrato JSON + docs |
| D3.4 | `mcp-registry` | (Opcional) Catálogo/registry interno de herramientas MCP registradas por dominio | `API/core/mcp_registry.py` | Lista en `/docs` o endpoint de registry; patrón de alta documentado |

> **Patrón replicable para cada nueva herramienta (checklist D):**
> 1. Registrar la **tool** con `@mcp.tool()` en un módulo `services/<dominio>_mcp.py`.
> 2. Exponer router REST `get_router()` y montarlo en `server.py` (prefijo propio).
> 3. Inyectar contexto con `get_current_context()` + su slot.
> 4. Elegir `proveedor_llm` (default `modelo`) con fallback ya probado.
> 5. Añadir caso de ejemplo a `INSTRUCCIONES_USO_API.md` + test básico.
> 6. Validar con R5 (arranque + Swagger).

---

### 7.2. Orden sugerido de arranque por agente (ruta crítica)

1. **A0.1 `boot-settings`** → 2. **A0.2 `tests-harness`** → 3. **A0.3 `log-config`** → *(GATE 0)*
4. **B1.2 `durable-store`** ✅ → 5. **B1.1 `context-contract`** ✅ → 6. **B1.3 `prompt-compiler`** ✅ → *(GATE 1 ✅)*
7. **C2.3 `zoom-async`** → 8. **C2.4 `zoom-refactor`** → 9. **C2.1 `structured-output`** → 10. **C2.2 `openai-real`** → *(GATE 2)*
11. **D3.1–D3.4** herramientas MCP prioritarias (CRM → asistencia → zoom-analytics)

> La ruta crítica prioriza, en cada equipo, el agente que **desbloquea** al resto (config → tests → logging; store → contract → prompt; async → refactor → structured → openai).

### 7.3. Contrato de salida oficial de cada agente (tarjeta de terminado)
1. **PR objetivo** — un cambio atómico, reversible y autocontenido.
2. **Tests** — `pytest` que cubren el cambio (Fase 0 aporta el harness).
3. **Docs** — `readme.md` y/o `INSTRUCCIONES_USO_API.md` actualizados.
4. **Retro-compatibilidad** — contractos REST/MCP intactos; extensiones aditivas.
5. **Validación** — `uv run uvicorn API.server:app --port 10000` + Swagger `/docs` de los endpoints afectados.
6. **Gate** — la fase se considera cerrada solo cuando su gate (tabla de la fase) se verifica.

---

### 7.4. Matriz de trazabilidad: deuda técnica → agente responsable

| Deuda técnica (del §6) | Fase | Agente de cierre |
| :--- | :--- | :--- |
| D1 `openai.py` stub | Fase 2 | C2.2 |
| D2 `zoom_reports.py` monolítico (1500 L) + duplicación | Fase 2 | C2.4 |
| D3 Parseo JSON LLM por regex frágil | Fase 2 | C2.1 |
| D4 ContextStore in-memory (no durable) | Fase 1 | B1.2 |
| D5 Config diseminada entre `config.py`, `zoom_api_utils`, `zoom_reports` | Fase 0 | A0.1 |
| D6 `zoom_*` con `requests` síncrono bloqueando el loop | Fase 2 | C2.3 ✅ (services) · C2.4 (zoom_reports) |
| D7 Sin `.env.example` / validación de credenciales | Fase 0 | A0.1 |
| D8 Sin tests automatizados | Fase 0 | A0.2 |
| D9 `__pycache__` versionados (`.gitignore`) | Fase 0 | A0.1 (limpieza + .gitignore) |
| — Slots sin schema versionado | Fase 1 | B1.1 |
| — Compilador de prompt sin presupuesto de tokens | Fase 1 | B1.3 |

---

## 7.6. 🔎 Afinado MCP de Evaluación de Ensayos (FASE 3 · LTI del LMS)

> **Entradas soportadas desde el LTI** (payload JSON con `assignment`, `submission`,
> `attachment`). Ambos casos se normalizan en `SolicitudEvaluacionDirecta`:

| Caso | Estructura | `origen_ensayo` resultante |
| :--- | :--- | :--- |
| **1. Archivo URL** | `submission.type=online_upload` + `attachment.url` (DOCX/PDF) | `attachment.url` (se descarga y extrae texto) |
| **2. Texto online** | `submission.type=online_text_entry` o `attachment.txt` | `submission.body` / `attachment.txt` (texto directo) |

### 7.6.1. Consigna de la actividad (`assignment.description`)
- Se extrae `assignment_description` del `assignment.description` del LMS.
- `_extraer_consigna_evaluable()` lo **depura**: descarta plantilla, unidad, secciones
  operativas y "Rúbrica", dejando solo la consigna evaluable (insumos → 800+ chars → ~135).
- Se inyecta en el prompt como bloque `--- DESCRIPCIÓN / CONSIGNA DE LA ACTIVIDAD ---`
  para que el evaluador juzgue **pertinencia y completitud** frente a lo solicitado.

### 7.6.2. Control de envíos por tarea-estudiante (`API/core/submission_tracker.py`)
- Límite **configurable en dos niveles**:
  - **Global** (`.env`): `MAX_SUBMISSION_ATTEMPTS` (default **2**) → `settings.max_submission_attempts`.
  - **Por tarea/petición** (payload LTI): campo `max_intentos` → sobrescribe el global
    si viene presente; si no, usa el global. (Ver `check_allowed(..., max_attempts=...)`.)
- Agrupación por `(course_id, assignment_id, user_id)` — no por `submission_id` (en Canvas
  un estudiante reenvía el **mismo** `submission_id` al reentregar; se cuenta cada reenvío).
- Backend **SQLite/PostgreSQL** (SQLAlchemy async, misma `DB_DSN`) con degradación a
  memoria (sin driver disponible).
- Sesión `SubmissionTracker` (singleton) inicializada en el lifespan de `server.py`.
  - `check_allowed()` → rechaza temprano con **HTTP 429** (`status: LÍMITE_DE_ENVÍOS`)
    *antes de gastar tokens*.
  - `register()` → escribe la evaluación en la crónica (`submission_id`, nota, escala,
    modelo, timestamps) para **métricas posteriores**.
  - Campo `contar_intento: bool` (default True) en el payload permite re-evaluar/admin.

### 7.6.3. Respuesta estandarizada al LMS
- `ai.status` pasa a **`EVALUADO`** (antes `COMPLETED`) tras una evaluación correcta.
- Se adiciona el bloque `intento: {contado, registrado, info}` con la crónica.
- Se conserva `rubric_assessment_canvas` (compatible con Canvas SpeedGrader), `nota_final`,
  `escala_maxima` y `resumen_retroalimentacion`.

### 7.6.2b. Modo ASÍNCRONO con cola y consulta de estado (`API/core/evaluation_queue.py`)
> **Problema resuelto:** el flujo síncrono bloqueaba la petición HTTP del LMS 100-300s por
> cada evaluación (timeouts/carga bajo volumen, y gasto innecesario de tokens en respuestas
> que el LMS descartaba).

**Flujo (recomendado en producción cuando el LMS no debe esperar):**

```text
LMS ──POST /ensayos/enqueue──▶ 202 {estado:PENDING, evaluation_id}  (¡inmediato!)
LMS ──GET  /ensayos/estado/{id}──▶ PENDING/EVALUATING  (reintenta)
worker (1 a la vez) procesa en background  ──▶ EVALUATED
LMS ──GET  /ensayos/estado/{id}──▶ EVALUATED {nota, feedback, rubric_assessment_canvas}
```

- **`POST /ensayos/enqueue`**: guarda la solicitud en BD (`evaluation_jobs`) como `PENDING`
  y devuelve **202** al instante con `evaluation_id` (= `eval_<submission_id>`).
- **`GET /ensayos/estado/{evaluation_id}`**: `PENDING`/`EVALUATING` → aún no; `EVALUATED` →
  devuelve el **resultado canónico consolidado**; `ERROR` → motivo; 404 si no existe.
- **Worker**: bucle asíncrono (1 evaluación a la vez) en el lifespan del servidor; saca el
  primer `PENDING`, lo pasa a `EVALUATING`, ejecuta el pipeline (descarga + LLM) y lo marca
  `EVALUATED`/`ERROR`. Controla el rate-limit de OpenRouter (429) y evita saturación.
- **Persistencia**: backend SQLAlchemy async (`evaluation_jobs`) con degradación a memoria
  (sin driver). Reintré de un `submission_id` ya `EVALUATED` no duplica: devuelve el resultado.
- **Endpoint síncrono** (`/ensayos/evaluar-flujo-completo`) se conserva (retro-compat), pero su
  salida ahora incluye `respuesta_consolidada` (canónica, sin duplicar).

### 7.6.2c. Salida consolidada (sin duplicados)
- Nueva función `_consolidar_resultado()` produce un **JSON canónico único**:
  ```json
  { "status": "EVALUADO", "evaluation_id", "submission_id", "nota_final",
    "escala_maxima", "feedback", "fortalezas", "areas_mejora", "recomendaciones",
    "rubric_assessment_canvas", "modelo_utilizado", "datos_lms" }
  ```
- Se elimina la repetición interna que confundía (criterios repetidos en raíz + `ai.result` +
  `evaluacion`). Antes el LLM gastaba en devolver el mismo contenido varias veces y el JSON
  se inflaba; ahora `rubric_assessment_canvas` (nativo Canvas) es la única vía de criterios
  hacia el LMS, y `evaluacion` queda para auditoría.
- **Reducción real**: una sola llamada al LLM, sin re-empaquetar el mismo bloque en 4 sitios.

### 7.6.3. Modo CANON B implementado · Opción C (multi-LMS) DOCUMENTADA

> **Estado:** el proyecto implementa la **Opción B**: canon único en raíz del síncrono
> (`/ensayos/evaluar-flujo-completo`) y la consulta de estado async (`GET /ensayos/estado/{id}`)
> devuelve **ese mismo canon directo** (sin capas de control que dupliquen). El campo
> `respuesta_consolidada` interno se eliminó (dejó de repetir `datos_lms`/`rubric_assessment`).

**Decisión de arquitectura:** la evaluación se genera **una sola vez** en un **canon interno**
*independiente del LMS*. Hoy Canvas es el único consumidor, por lo que el canon ya incluye
`rubric_assessment_canvas` (formato nativo que Canvas/SpeedGrader exige). Cuando llegue un
**segundo LMS** (Moodle, Blackboard…), **no se debe tocar el pipeline** ni el canon: se aplica la
**Opción C**, un **adaptador de formato por LMS** que traduce el canon al contrato del destino.

```
           ┌───────────────────────────────┐
           │  CANON (fuente de verdad)     │  ← se genera UNA vez
           │  {nota, escala, feedback,     │
           │   criterios, evidencias,      │
           │   rubric_assessment_canvas,   │
           │   datos_lms, ...}             │
           └───────────────┬───────────────┘
                           │
          format_for_lms("canvas")  format_for_lms("moodle")  format_for_lms("blackboard")
                           │                    │                    │
                           ▼                    ▼                    ▼
                 rubric_assessment        feedback/grade        feedback + grade
```

**Cómo implementar la Opción C (cuando aplique):**

1. **Mantener el canon intacto**: la evaluación real (nota por criterio, justificación,
   evidencias) NO cambia entre LMS.
2. **Añadir un adaptador** en `essay_evaluator_mcp.py` (o un módulo `services/formats/`):
   ```python
   def format_for_lms(lms: str, canon: dict) -> dict:
       if lms == "canvas":     return {"rubric_assessment": canon["rubric_assessment_canvas"], "score": canon["nota_final"], "feedback": canon["feedback"]}
       if lms == "moodle":     return {"grade": canon["escala_maxima"], "feedback": canon["feedback"], "criteria": canon.get("criterios")}
       return canon  # genérico
   ```
3. **Parametrizar por `id_lms`**: ya se captura en `datos_lms["id_lms"]`; si viene
   `"moodle"`/`"blackboard"`, el endpoint devuelve `format_for_lms(...)`. Canvas queda por defecto
   (retro-compat).
4. **Añadir endpoint de diagnóstico por formato** (opcional): `GET /ensayos/formato/{lms}` para
   validar el contrato sin evaluar.

**Ventaja de la Opción B → C:** hoy (`B`) ya no hay duplicados ni confusión; el canon es
estable; y al ampliar a otro LMS (`C`) solo se **añade** un adaptador, sin reestructurar nada de
Canvas ni del pipeline (aditivo, sin romper lo existente). Mantener la consulta de estado con el
mismo canon hace que el LMS futuro reciba su formato con el mismo patrón.

### 7.6.4. Archivos tocados
- `API/services/essay_evaluator_mcp.py` — normalización LTI, consigna, tracker, estado EVALUADO.
- `API/core/submission_tracker.py` (nuevo) — control de envíos y métricas.
- `API/server.py` / `API/config.py` / `.env.example` — inicialización y `MAX_SUBMISSION_ATTEMPTS`.
- `tests/test_lti_payload.py`, `tests/test_submission_tracker.py` (nuevos).

**Pendiente para puesta en producción:** ejecutar `uv sync --extra db --group dev && uv run pytest`,
insonorizar `DB_DSN` en el `.env` real y (opcional) contenerizar (`Dockerfile`). El flujo LTI→MCP→LMS
queda validado con payloads reales de los casos 1 y 2 en esta misma sesión.

---

## 8. 🛠️ Comandos Útiles

```powershell
# Instalar   (gestor de paquetes uv)
uv sync

# Ejecutar API
uv run uvicorn API.server:app --port 10000 --reload
#   → http://localhost:10000/docs  (Swagger)
#   → http://localhost:10000/redoc (Redoc)

# MCP standalone (STDIO / HTTP)
uv run API/services/essay_evaluator_mcp.py
uv run API/services/essay_evaluator_mcp.py --http
```