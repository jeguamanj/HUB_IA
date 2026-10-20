# Esquema PostgreSQL del HUB

Este documento describe las tablas creadas por las migraciones `001` a
`004`. Las migraciones `001` y `002` están aplicadas y verificadas en `hub_test` (PostgreSQL 17,
puerto 5433). La migración `001` requiere PostgreSQL 15 o superior por el uso
de `UNIQUE NULLS NOT DISTINCT`.

> **Estado del PMV:** en desarrollo, el backend aplica las migraciones
> PostgreSQL pendientes al iniciar. En `APP_ENV=production`, el arranque solo
> verifica que todas estén aplicadas y falla cerrado si falta alguna. El flujo
> asíncrono usa jobs, grupos de intentos, eventos, intentos técnicos y
> telemetría LLM/MCP. La ruta síncrona todavía usa el tracker legado; faltan
> validación de checksums y pruebas de operación/seguridad en staging.

## Aplicación desde el backend

En desarrollo, con `DB_DSN` configurado con un DSN async de PostgreSQL, el
`lifespan` de la API ejecuta en orden los archivos `NNN_*.sql` que no figuren
en `hub_schema_migrations`. Para producción, configura `MIGRATION_DB_DSN`
solo en un paso administrativo/de despliegue y ejecuta:

```powershell
python -m API.core.postgres_migrations apply
```

El comando aplica las migraciones con el rol DDL. Configura por separado
`DB_DSN` para el proceso de API con un rol runtime sin permisos DDL. En
`APP_ENV=production`, la API no aplica cambios de esquema: verifica que el
ledger exista y que todas las versiones del repositorio estén registradas;
si faltan, el arranque falla y solicita ejecutar el comando de migración. No
imprime el DSN. Las migraciones contienen su propia transacción; para SQLite no
se ejecuta este esquema PostgreSQL. El runner omite versiones ya registradas y todavía no compara checksums. La
migración `004` crea las tablas del ContextStore (`contexts`) y del tracker
legado (`submission_groups`) para que la API de producción no necesite
privilegios DDL al arrancar.

## Base de datos de producción nueva

Para aprovisionar una base nueva y vacía, desde la raíz del repositorio y con
las dependencias instaladas:

```powershell
python -m scripts.prepare_production_db
```

El asistente solicita el DSN DBA de forma oculta, nombre de base, roles y
contraseñas ocultas; exige confirmación explícita. Requiere PostgreSQL 15+ y
TLS para hosts remotos (`sslmode=require`, `verify-ca` o `verify-full`).
Crea roles distintos `hub_migrator` (DDL) y `hub_runtime` (sin DDL), una base
nueva, aplica las migraciones `001`–`004` y concede al rol runtime solo
`CONNECT`, `USAGE` y DML sobre tablas/secuencias actuales y futuras.
Si el DSN contiene caracteres reservados en usuario o contraseña, deben estar
codificados como URL antes de introducirlo en el prompt oculto.

El script **rechaza bases/roles existentes** y nunca borra datos. No usar para
reconciliar un esquema histórico: la discrepancia del checksum de `001` en
`hub_test` es distinta a la creación limpia de producción y sigue requiriendo
reconciliación manual si se pretende reutilizar esa base. No ejecutar el
asistente hasta disponer de una ventana aprobada y acceso DBA.

Después, inyecta `DB_DSN=postgresql+asyncpg://...` con el rol runtime en el
servicio. Usa `MIGRATION_DB_DSN` con el rol DDL solo durante operaciones de
migración. No pongas la contraseña en argumentos de comandos, Git, logs o
archivos dentro del checkout.

## Vista general y relaciones

```text
evaluation_submission_groups 1 ─── N evaluation_jobs
evaluation_jobs               1 ─── N evaluation_attempts
evaluation_jobs               1 ─── N evaluation_status_events
evaluation_jobs               1 ─── N mcp_invocations (opcional)
evaluation_jobs               1 ─── N llm_invocations
evaluation_attempts           1 ─── N llm_invocations
hub_api_clients               1 ─── N hub_api_client_events
hub_schema_migrations         (registro independiente del esquema aplicado)
```

La tabla `submission_groups` creada originalmente por `001` se llama
`evaluation_submission_groups` después de aplicar `002`. PostgreSQL actualiza
automáticamente la referencia FK de `evaluation_jobs` durante el renombrado.
El nombre `submission_groups` queda libre para el tracker legado de la
aplicación, que tiene un modelo diferente.

## Tablas

### `hub_schema_migrations`

Registro de las migraciones que se aplicaron a la base.

| Columna | Tipo | Uso |
|---|---|---|
| `version` | `VARCHAR(32)`, PK | Identificador ordenable de la migración, por ejemplo `001`. |
| `name` | `TEXT` | Nombre descriptivo de la migración. |
| `checksum` | `CHAR(64)` | SHA-256 asociado al contenido de la migración. |
| `applied_at` | `TIMESTAMPTZ` | Fecha de aplicación; por defecto, hora actual de PostgreSQL. |

**Uso operativo:** consultar esta tabla antes de desplegar una migración y
mantener una secuencia única por entorno. No editar migraciones ya aplicadas;
los cambios posteriores deben ser una nueva migración numerada. Existe una
discrepancia detectada entre el checksum de `001` guardado en `hub_test` y el
valor declarado en el archivo actual; resolverla antes de usar la tabla como
verificación automatizada de integridad en despliegues.

### `hub_api_clients` y `hub_api_client_events`

La migración `003` incorpora credenciales opacas individuales para integraciones.
`hub_api_clients` guarda solo SHA-256 de tokens aleatorios de alta entropía,
cliente, institución, tenant opcional, scopes permitidos, expiración, revocación
y último uso. El token original se muestra una sola vez por el comando
administrativo local y nunca se persiste. Las escrituras de emisión, rotación y
revocación generan eventos en `hub_api_client_events`, sin incluir el token.

La API admite únicamente los scopes `context:read`, `context:write`,
`context:close`, `context:delete`, `echo:use`, `essays:read`, `essays:submit`,
`llm:chat` y `llm:read`. No se emiten scopes
para Zoom, MCP remoto ni consultas globales de contextos porque aún no aplican
aislamiento por recurso. Los scopes de ensayo solo se permiten a credenciales
de institución (sin `tenant_id`), ya que `evaluation_jobs` aún no persiste el
tenant. La autenticación de clientes exige que `X-Institution-ID` coincida con
la credencial; si el token está ligado a un tenant, también exige `X-Tenant-ID`.

La emisión, rotación y revocación se realizan fuera de banda con
`python -m API.core.manage_api_clients`; no hay rutas HTTP administrativas.
`API_CLIENT_AUTH_ENABLED=true` activa el modo de clientes y hace que falle
cerrado si el repositorio no está disponible. En `APP_ENV=production` el modo
es obligatorio. El bearer compartido `HUB_API_TOKEN` queda para desarrollo
local y no se acepta cuando el modo de clientes está habilitado.

### `evaluation_submission_groups`

Contador de intentos académicos agrupados por institución, LMS, curso, tarea y
estudiante. El registro no depende de `submission_id`, ya que el LMS puede
reutilizarlo al reenviar una entrega.

| Columna | Tipo | Uso |
|---|---|---|
| `submission_group_id` | `BIGINT IDENTITY`, PK | Identificador interno del grupo. |
| `institution_id` | `TEXT`, nullable | Institución; `NULL` se considera parte de la identidad única. |
| `lms` | `TEXT` | Origen LMS. |
| `course_id` | `TEXT` | Curso. |
| `assignment_id` | `TEXT` | Actividad/tarea. |
| `user_id` | `TEXT` | Estudiante. |
| `consumed_attempts` | `INTEGER` | Intentos académicos completados; no puede ser negativo. |
| `reserved_attempts` | `INTEGER` | Intentos reservados para evaluaciones en curso; no puede ser negativo. |
| `created_at`, `updated_at` | `TIMESTAMPTZ` | Creación y última actualización; ambas usan hora actual por defecto. |

Una restricción única evita grupos duplicados para la misma identidad compuesta.
La aplicación debe actualizar reservas y consumo en transacciones coordinadas
con `evaluation_jobs`. La restricción evita valores negativos, pero la
consistencia entre el contador y las evaluaciones relacionadas depende de la
lógica transaccional de la aplicación.

### `evaluation_jobs`

Registro principal y fuente de verdad de cada evaluación lógica. Conserva el
payload de entrada, el resultado JSON entregable al LMS y el estado actual.

| Columna | Tipo | Uso |
|---|---|---|
| `evaluation_id` | `VARCHAR(128)`, PK | ID público/estable de la evaluación; no debe derivarse solo de `submission_id`. |
| `status` | `VARCHAR(16)` | Estado: `PENDING`, `EVALUATING`, `EVALUATED` o `ERROR`; por defecto `PENDING`. |
| `source` | `TEXT` | Origen del trabajo; por defecto `unknown`. |
| `lms`, `institution_id`, `course_id`, `assignment_id`, `user_id`, `submission_id`, `context_id` | `TEXT`, nullable salvo `source` | Correlación del LMS, estudiante, tarea y contexto disponibles. |
| `submission_group_id` | `BIGINT`, FK | Grupo de intentos; referencia `evaluation_submission_groups`, no se borra mientras existan trabajos asociados. |
| `count_attempt` | `BOOLEAN` | Indica si se contabiliza el intento académico; por defecto `TRUE`. |
| `attempt_tracking` | `VARCHAR(32)` | Seguimiento: `NOT_APPLICABLE`, `RESERVED`, `CONSUMED`, `RELEASED` o `SKIPPED_MISSING_IDENTITY`. |
| `attempt_number` | `INTEGER`, nullable | Número de intento académico, si aplica; debe ser positivo. |
| `max_attempts_snapshot` | `INTEGER`, nullable | Límite efectivo copiado al crear el trabajo; no puede ser negativo. |
| `payload` | `JSONB`, requerido | Datos validados necesarios para ejecutar/recuperar la evaluación. |
| `result` | `JSONB`, nullable | Respuesta canónica completa; obligatorio cuando `status = EVALUATED`. |
| `error_code`, `error_message` | `TEXT`, nullable | Error categorizado y mensaje seguro para consulta/diagnóstico. No guardar secretos ni detalles internos sensibles. |
| `idempotency_client_id`, `idempotency_key` | `TEXT`, nullable | Par de identidad para deduplicación HTTP; ambos deben estar presentes o ausentes. |
| `request_sha256` | `CHAR(64)`, nullable | Huella hexadecimal de la solicitud para validar reuso de clave. |
| `processing_cycles` | `INTEGER` | Ciclos técnicos de procesamiento; no puede ser negativo. |
| `worker_attempts` | `INTEGER` | Ejecuciones del worker; no puede ser negativo. |
| `max_processing_attempts` | `SMALLINT` | Máximo de intentos técnicos configurado; por defecto `3`, debe ser positivo. |
| `available_at` | `TIMESTAMPTZ` | Momento desde el que el trabajo puede ser recogido; por defecto ahora. |
| `lease_owner`, `lease_expires_at` | `TEXT`, `TIMESTAMPTZ`, nullable | Worker propietario y vencimiento de su lease. |
| `created_at`, `updated_at` | `TIMESTAMPTZ` | Creación y última actualización; por defecto ahora. |
| `started_at`, `finished_at` | `TIMESTAMPTZ`, nullable | Inicio y fin del procesamiento. Los estados terminales requieren `finished_at`. |

Índices apoyan la búsqueda de trabajos pendientes, por tenant/estado, por
entrega y por grupo/estado. Hay además una unicidad parcial de
(`idempotency_client_id`, `idempotency_key`) cuando se proporciona una clave.
`EVALUATED` exige `result` y un seguimiento de intento compatible; `ERROR`
exige liberar la reserva cuando corresponda.

**Datos sensibles:** `payload` puede contener el ensayo, rúbrica y datos
educativos. `result` también puede contener información personal o académica.
Restringir acceso, excluir ambos de logs/trazas, definir retención y borrado, y
proteger backups antes de producción. No persistir tokens, credenciales,
headers de autenticación ni URLs con secretos.

### `evaluation_attempts`

Historial de ejecuciones técnicas del worker para un mismo `evaluation_id`.
No equivale al intento académico del estudiante: una evaluación puede tener
varios intentos técnicos sin consumir varios intentos académicos.

| Columna | Tipo | Uso |
|---|---|---|
| `evaluation_attempt_id` | `BIGINT IDENTITY`, PK | Identificador interno de la ejecución. |
| `evaluation_id` | `VARCHAR(128)`, FK | Evaluación propietaria; se elimina en cascada si se elimina el trabajo. |
| `processing_cycle` | `INTEGER` | Ciclo técnico, debe ser positivo. |
| `attempt_number` | `SMALLINT` | Número de ejecución dentro del ciclo, debe ser positivo. |
| `status` | `VARCHAR(16)` | `RUNNING`, `SUCCEEDED`, `FAILED` o `ABANDONED`. |
| `started_at` | `TIMESTAMPTZ` | Inicio; por defecto hora actual. |
| `finished_at` | `TIMESTAMPTZ`, nullable | Nulo mientras `RUNNING`; requerido para los estados restantes. |
| `error_code`, `error_message` | `TEXT`, nullable | Error categorizado y mensaje asociado al intento. |

La combinación de evaluación, ciclo y número de intento es única. El índice por
evaluación e inicio facilita reconstruir la secuencia de ejecución.

### `evaluation_status_events`

Bitácora ordenable de eventos y transiciones de cada evaluación.

| Columna | Tipo | Uso |
|---|---|---|
| `evaluation_status_event_id` | `BIGINT IDENTITY`, PK | Identificador interno del evento. |
| `evaluation_id` | `VARCHAR(128)`, FK | Evaluación asociada; eliminación en cascada con el trabajo. |
| `event_number` | `INTEGER` | Secuencia por evaluación; debe ser positiva y única dentro de ella. |
| `event_type` | `TEXT` | Tipo de evento (por ejemplo, cambio de estado, reserva o recuperación). |
| `from_status`, `to_status` | `VARCHAR(16)`, nullable | Estados anterior y nuevo; si se informan deben ser estados válidos. |
| `actor_type` | `TEXT` | Tipo de actor que originó el evento; por defecto `system`. |
| `actor_id` | `TEXT`, nullable | Identificador del actor si está disponible. |
| `processing_cycle` | `INTEGER`, nullable | Ciclo técnico relacionado. |
| `attempt_number` | `SMALLINT`, nullable | Intento técnico relacionado. |
| `occurred_at` | `TIMESTAMPTZ` | Momento del evento; por defecto hora actual. |
| `metadata` | `JSONB` | Metadatos operativos mínimos; por defecto objeto JSON vacío. |

El índice por evaluación y fecha facilita el historial. Evitar incluir ensayo,
resultado completo, tokens o credenciales en `metadata`. La tabla no impone por
sí sola inmutabilidad; el rol de la aplicación debe tener permisos limitados
para preservar el carácter append-only esperado.

### `llm_invocations`

Telemetría de llamadas a modelos LLM ejecutadas durante un intento técnico.

| Columna | Tipo | Uso |
|---|---|---|
| `llm_invocation_id` | `BIGINT IDENTITY`, PK | Identificador de la llamada. |
| `evaluation_id` | `VARCHAR(128)`, FK | Evaluación asociada; eliminación en cascada con el trabajo. |
| `evaluation_attempt_id` | `BIGINT`, FK | Intento técnico asociado; eliminación en cascada con el intento. |
| `call_number` | `SMALLINT` | Número de llamada dentro del intento; positivo. |
| `provider`, `model` | `TEXT`, `TEXT` nullable | Proveedor y modelo reportados. |
| `is_fallback` | `BOOLEAN` | Indica si se usó un proveedor/modelo alternativo. |
| `status` | `VARCHAR(16)` | `SUCCESS`, `ERROR`, `TIMEOUT` o `CANCELLED`. |
| `started_at`, `finished_at` | `TIMESTAMPTZ` | Inicio y fin; el inicio usa hora actual por defecto. |
| `duration_ms` | `BIGINT`, nullable | Duración en milisegundos; no puede ser negativa. |
| `input_tokens`, `output_tokens` | `BIGINT`, nullable | Tokens de entrada y salida; no pueden ser negativos. |
| `token_source` | `VARCHAR(16)` | Fuente del conteo: `provider`, `estimated` o `unknown`. |
| `cost_amount` | `NUMERIC(20,10)`, nullable | Costo conocido; no puede ser negativo. |
| `cost_currency` | `CHAR(3)`, nullable | Moneda del costo. |
| `error_code`, `error_message` | `TEXT`, nullable | Categoría y mensaje seguro del error. |

La combinación de evaluación, intento técnico y número de llamada es única.
Permite análisis de latencia, tokens y costo, pero no está diseñada para
almacenar prompts ni respuestas crudas del proveedor. El ciclo de inserción
debe permitir registrar llamadas con error/timeout aunque la evaluación
posteriormente se reintente.

### `mcp_invocations`

Telemetría de herramientas MCP, tanto asociadas a una evaluación como a
operaciones no asociadas a ella.

| Columna | Tipo | Uso |
|---|---|---|
| `mcp_invocation_id` | `BIGINT IDENTITY`, PK | Identificador interno de la invocación. |
| `evaluation_id` | `VARCHAR(128)`, FK nullable | Evaluación relacionada; al eliminarla, se establece `NULL` para conservar la telemetría. |
| `client_id`, `institution_id` | `TEXT`, nullable | Cliente/tenant autenticado disponible. |
| `tool_name` | `TEXT` | Nombre de la herramienta; requerido. |
| `status` | `VARCHAR(16)` | `SUCCESS`, `ERROR`, `TIMEOUT` o `CANCELLED`. |
| `started_at`, `finished_at` | `TIMESTAMPTZ` | Inicio y fin; el inicio usa hora actual por defecto. |
| `duration_ms` | `BIGINT`, nullable | Duración en milisegundos; no puede ser negativa. |
| `correlation_id` | `TEXT`, nullable | Identificador de correlación de la operación. |
| `error_code`, `error_message` | `TEXT`, nullable | Categoría y mensaje seguro del error. |

Los índices permiten consultar actividad por institución/fecha y por
evaluación. La tabla no contiene campo JSON para argumentos de herramientas;
no agregar payloads sensibles a errores o logs asociados.

## Ciclo de vida resumido

1. El endpoint debe guardar `evaluation_jobs` con `payload`, correlación y
   estado `PENDING`; cuando aplique, coordinar la reserva en
   `evaluation_submission_groups` y registrar el evento inicial en la misma
   transacción antes de responder `202 Accepted`.
2. El worker toma el trabajo, registra su ejecución en
   `evaluation_attempts`, actualiza estado/eventos y registra llamadas LLM/MCP
   según corresponda.
3. Al completar, actualiza la misma evaluación a `EVALUATED`, guarda el JSON en
   `result` y consume la reserva académica atómicamente. En error terminal pasa
   a `ERROR`, libera la reserva y conserva los errores categorizados.
4. El LMS consulta `GET /ensayos/estado/{evaluation_id}`. La cola lee el estado
  y resultado de `evaluation_jobs`; el worker registra eventos, intentos y
  telemetría vinculada al job.

## Recomendaciones antes de producción

- Integrar y probar cola, worker, endpoint de estado y tracker contra este
  esquema; un `DB_DSN` configurado no demuestra que los endpoints estén usando
  estas tablas.
- Aplicar migraciones en orden, con backup previo y procedimiento de rollback
  revisado. Las migraciones `001` y `002` se aplican como transacciones.
- Usar roles separados: rol de migración con permisos DDL controlados y rol de
  runtime con solo los permisos DML necesarios. No usar superusuario para la
  aplicación.
- Definir TLS, gestión de secretos, rotación de credenciales, backups cifrados,
  restauraciones ensayadas, monitoreo de espacio/conexiones y retención de
  datos educativos.
- Definir política para errores y logs, y limitar quién puede leer
  `payload`, `result`, identidades y mensajes de error.
- Resolver el checksum discrepante de `001` y definir el procedimiento de
  verificación antes de automatizar despliegues.
- Revisar crecimiento y particionamiento/retención de historial y telemetría
  con volúmenes de producción. No borrar trabajos con telemetría si se necesita
  conservar análisis; `evaluation_attempts`, `evaluation_status_events` y
  `llm_invocations` se eliminan en cascada con su evaluación, mientras
  `mcp_invocations` conserva la fila y pone `evaluation_id` en `NULL`.
