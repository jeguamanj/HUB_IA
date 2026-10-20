# Plan de evolución y preparación para producción del HUB

**Estado:** autenticación por cliente completada localmente; suite local en verde. PMV on-prem está preparado para aprovisionamiento de DB nueva con roles separados y ejecución detrás de systemd/Nginx; aún no se ha ejecutado ni desplegado. Azure/VPN es un despliegue posterior. Zoom/MCP privado explícitamente pasa a fase 2. La discrepancia histórica de checksum `001` aplica si se reutiliza la base afectada; el aprovisionador solo acepta bases nuevas.  
**Fecha de elaboración:** 14 de octubre de 2026.  
**Alcance:** persistencia PostgreSQL para operaciones y métricas, seguridad de FastAPI/MCP, gestión y compresión de contexto para LLM, cierre de pendientes y preparación operativa.  
**Regla de ejecución:** los cambios funcionales requieren autorización explícita de la persona responsable; la ejecución local de autenticación por cliente fue autorizada el 2026-10-20.

## 1. Objetivo

Definir una ruta incremental para llevar el HUB a producción con controles de acceso verificables, uso responsable del contexto enviado a los modelos, persistencia y operación confiables, y sin romper contratos existentes de FastAPI, MCP o Canvas.

La hoja de ruta prioriza primero los riesgos de exposición y los criterios de operación. La incorporación de nuevos dominios MCP se mantiene después de estabilizar y proteger la plataforma.

## 2. Estado observado y pendientes heredados

Esta sección resume la documentación y los puntos de entrada del código revisados para preparar el plan. No constituye una auditoría de seguridad ni una validación de runtime.

### Capacidades existentes que conviene conservar

- FastAPI monta routers de contexto, LLM, Zoom, ensayos y healthcheck.
- El evaluador expone herramientas FastMCP y documenta los transportes STDIO y Streamable HTTP.
- El Context Fabric persiste contexto institucional y ya dispone de compilación de prompts con presupuesto y compresión heurística.
- La integración Canvas asíncrona permite encolar entregas y consultar el resultado por `evaluation_id`.
- El proyecto contempla SQLAlchemy async y `DB_DSN` para SQLite/PostgreSQL; la cola, el tracker y los contextos usan persistencia, pero hay que verificar qué datos de evaluaciones y de consumo LLM/MCP quedan realmente guardados, con qué retención y qué consultas métricas soportan.
- El manual indica que la rúbrica sigue siendo necesaria en cada evaluación: reutilizar el contexto en el HUB no implica por sí solo que el proveedor LLM deje de recibir o facturar esos tokens.

### Pendientes que aparecen en el plan técnico actual

- **A0.2 / Gate 0:** ejecutar la suite automatizada en un entorno con dependencias instaladas y registrar el resultado. El plan previo dice que pytest estaba pendiente; existen tests en el árbol actual, pero su estado y cobertura deben confirmarse con una corrida reproducible.
- **C2.1:** robustecer la salida estructurada del evaluador; no depender únicamente de regex para interpretar respuestas del modelo.
- **C2.2:** decidir si se implementa un proveedor OpenAI real o si se retira del catálogo.
- **C2.4:** refactorizar `zoom_reports.py`, reducir duplicación y cerrar el trabajo síncrono diferido.
- **D3.x:** CRM, asistencia académica y analítica Zoom son extensiones posteriores, no requisitos para la primera salida a producción.
- **Despliegue:** confirmar configuración de base durable, copias de seguridad, TLS, observabilidad, recuperación y procedimiento de despliegue. La contenerización puede evaluarse según el entorno final, no se asume obligatoria.
- **PostgreSQL operativo:** confirmar si PostgreSQL será la base objetivo de producción y diseñar temprano persistencia y métricas de evaluaciones, herramientas MCP y consumo de proveedores; no asumir que la persistencia actual de cola/contexto ya cubre analítica histórica.

### Brecha de seguridad que debe comprobarse en el diseño

En el arranque revisado de FastAPI se montan los routers sin que allí aparezca una política global de autenticación/autorización. La guía muestra FastMCP tanto en STDIO como en Streamable HTTP, pero no documenta un esquema de credenciales de HUB. Esto no demuestra que no exista protección en un gateway externo; antes de producción hay que inventariar y probar todos los límites reales de red y aplicación.

La descarga de archivos desde URLs suministradas en entregas también debe incluirse en el análisis de amenazas, por el acceso a destinos externos y a redes internas que podría habilitar un cliente malicioso o mal configurado.

### Enfoque de contexto que ya existe y su límite

El HUB ya tiene estimación de tokens, presupuesto de prompt y compresión heurística. La extracción de entidades o el almacenamiento del contexto en el servidor no equivale a un resumen semántico ni reduce automáticamente los tokens que recibe el LLM. Cualquier ahorro debe medirse sobre el prompt efectivamente enviado al proveedor y no solo sobre el tamaño del payload LMS → HUB.

## 3. Principios de trabajo

1. **Acceso denegado por defecto** para operaciones sensibles; las excepciones deben ser explícitas y justificadas.
2. **Separar autenticación de autorización:** identificar al cliente no basta; hay que limitar acciones, ámbitos institucionales y recursos.
3. **Compatibilidad aditiva:** cambios de contratos REST/MCP se versionan o se agregan sin alterar silenciosamente clientes existentes.
4. **Minimización de datos:** no guardar ni registrar tokens, URLs firmadas, ensayos completos o información personal si no es necesario.
5. **Resumen con trazabilidad:** conservar fuente, versión, fecha, método y reglas de invalidación; nunca convertir un resumen en fuente autoritativa.
6. **Medición antes de optimizar:** establecer un baseline de seguridad, calidad, tokens, latencia y costo antes de fijar metas cuantitativas.
7. **Gates verificables:** cada etapa termina con pruebas y evidencia; no se avanza por estado supuesto o solo por documentación.

## 4. Etapas propuestas

### Etapa 0 — Decisiones, inventario y baseline

**Objetivo:** cerrar incertidumbres antes de escoger mecanismos o cambiar contratos.

**Trabajo propuesto**

- Inventariar rutas FastAPI, herramientas MCP y transportes, consumidores (Canvas, N8N, administradores) y exposición de red por entorno.
- Confirmar si existe gateway, proxy, SSO o autenticación fuera de la aplicación, qué rutas quedan públicas y quién administra credenciales.
- Ejecutar la suite de tests en un entorno reproducible; registrar fallos, cobertura relevante y comportamiento actual de los flujos Canvas, Zoom y contextos.
- Revisar configuración de persistencia, restauración, logs y despliegue existente sin incluir secretos reales en el repositorio.
- Definir requisitos de privacidad: clases de datos, retención de ensayos, contexto y auditoría.

**Entregables:** inventario de superficies y clientes; baseline de tests; decisiones registradas sobre identidad, exposición, datos y entorno.

**Gate 0:** propietarios funcionales y técnicos confirman qué clientes deben autenticarse y qué recursos puede consultar o modificar cada uno.

### Etapa 1 — Persistencia PostgreSQL y métricas operativas

**Objetivo:** establecer desde temprano una fuente durable para el ciclo de vida de las evaluaciones y para medir actividad, consumo y desempeño de los MCPs.

**Diseño y trabajo propuesto**

- **Configuración de base:** `DB_DSN` será la variable canónica. `DATABASE_URL` se aceptará solo como alias de compatibilidad cuando `DB_DSN` no esté definido; si ambas existen, prevalece `DB_DSN`. Documentar DSN async de PostgreSQL/SQLite, validar dialecto/driver al iniciar y exponer en logs solo el backend/dialecto, nunca credenciales. En el modo de producción, las operaciones que prometen durabilidad no deben degradarse silenciosamente a memoria: `/ensayos/enqueue` solo confirma `202` tras el commit durable.
- Confirmar PostgreSQL como base objetivo para producción, versión/servicio administrado, disponibilidad, cifrado en tránsito, backups, restauración y responsables operativos. SQLite puede seguir para desarrollo/pruebas si se conserva compatibilidad.
- Inventariar las tablas/repositorios actuales de contextos, cola y submission tracker; definir qué datos están persistidos, cuáles viven solo en memoria o archivos y qué garantías ofrece cada flujo ante reinicio, reintento o concurrencia.
- Diseñar esquema versionado y migraciones reproducibles para:
  - el payload validado que acepta `POST /ensayos/enqueue` y los payloads equivalentes de los otros puntos de entrada asíncronos; guardar en PostgreSQL el contenido necesario para que el worker pueda ejecutar o recuperar el trabajo tras reinicios;
  - trabajos de evaluación y un historial auditable de transiciones de estado (aceptado/encolado, procesando, completado, fallido), con `evaluation_id`, fechas de creación/actualización/inicio/fin, intentos, errores categorizados y correlación/idempotencia;
  - identidad de auditoría disponible en la solicitud (usuario/estudiante y cliente o integración que originó el envío), institución/curso/tarea y referencias a la entrega, distinguiendo actor que solicita de estudiante evaluado y evitando inferir identidades no suministradas;
  - resultado JSON completo de la evaluación, incluyendo nota, retroalimentación, desglose por rúbrica y estado final; almacenar asimismo referencia al reporte/artefacto generado cuando corresponda;
  - uso de LLM por solicitud/intento: proveedor, modelo, tokens de entrada/salida reportados por proveedor cuando estén disponibles, estimaciones cuando no, latencia, costo cuando sea conocido, resultado/fallback y error categorizado;
  - invocaciones MCP: herramienta, cliente/tenant autorizado, marcas de tiempo, duración, estado y correlación con evaluación cuando aplique;
  - contexto/assignment y metadatos de auditoría mínimos necesarios para consultas agregadas.
- **Diseño inicial acordado para el flujo de ensayos:** PostgreSQL es el registro durable del proceso. Cada solicitud aceptada tiene un `evaluation_id` y una fila principal de evaluación; primero se guardan el payload y sus identificadores de correlación, y solo después se confirma `202`. El worker actualiza el estado de esa misma evaluación y, al finalizar, guarda allí el JSON completo de respuesta. El LMS consulta `GET /ensayos/estado/{evaluation_id}` para conocer el estado y obtener el resultado persistido cuando esté disponible. El flujo acordado es de consulta/polling iniciado por el LMS; no requiere un worker de entrega saliente del HUB.
- **Contrato previsto del endpoint de estado/resultados:** devolver `404 NOT_FOUND` cuando el `evaluation_id` no exista; para trabajos `PENDING` o `EVALUATING`, devolver el estado y datos de correlación disponibles sin resultado; para `ERROR`, devolver estado y error categorizado sin detalles internos sensibles; y para `EVALUATED`, devolver el JSON canónico de resultado almacenado en la fila de PostgreSQL, incluyendo `evaluation_id` y correlación disponible. El LMS conserva el identificador recibido al encolar y consulta el endpoint hasta completar o alcanzar su política de reintento.
- Como modelo lógico inicial (nombres sujetos a revisión durante el diseño detallado):
  - `evaluation_jobs`: una fila por evaluación; `evaluation_id`, payload JSONB, resultado JSONB, estado actual (`PENDING`, `EVALUATING`, `EVALUATED`, `ERROR`), fechas, número de intentos, error categorizado y datos de correlación/identidad disponibles. El payload y resultado se almacenan en PostgreSQL; no se replica el ensayo en logs.
  - `evaluation_status_events`: historial append-only ligado a `evaluation_id`, con estado anterior/nuevo, fecha y metadatos operativos mínimos.
  - `llm_invocations` y registro de invocaciones MCP: detalle por llamada ligado a evaluación cuando aplique, para medir proveedor/herramienta, modelo, tokens reportados o estimados, duración, costo conocido y resultado. Son telemetría operativa y no reemplazan el registro funcional de evaluación.
- **Primer borrador de datos por entidad (diseño lógico; no es DDL):**

| Entidad | Identificador y relaciones | Datos propuestos | Propósito / reglas |
|---|---|---|---|
| `evaluation_jobs` | PK `evaluation_id` opaco y único. | `status`; `source`/`lms`; `institution_id`; `course_id`; `assignment_id`; `user_id`; `submission_id`; `context_id`; `attempt_number`; `count_attempt`; `max_attempts_snapshot`; `payload` JSONB; `result` JSONB nullable; `error_code` y mensaje seguro; `created_at`, `updated_at`, `started_at`, `finished_at`; número de ejecuciones del worker; `idempotency_key` nullable. | Una fila representa una evaluación solicitada. Encolar conserva la entrada necesaria para ejecutar; el worker actualiza esa misma fila y coloca el resultado canónico final. El payload contiene contenido sensible de ensayo/rúbrica: acceso restringido, no incluirlo en logs. `result` incluye el JSON servido por el endpoint de estado, no solo nota/modelo. |
| `submission_groups` | PK lógica compuesta por `institution_id`, `lms`, `course_id`, `assignment_id`, `user_id` (o clave sustituta con restricción única equivalente). | `max_attempts` efectivo; intentos reservados/consumidos según política aprobada; timestamps; opcionalmente versión/lock de concurrencia. | Representa el contador de intentos por estudiante y tarea; no se agrupa por `submission_id`, porque el LMS puede reutilizarlo. Solo se crea/usa cuando la identidad mínima requerida está disponible y `count_attempt=true`. Debe permitir aplicar el límite de forma transaccional ante solicitudes simultáneas. |
| `evaluation_status_events` | PK propia; FK `evaluation_id`. | estado anterior/nuevo; fecha; actor/tipo de cambio (API, worker, recuperación); intento de procesamiento; código de motivo y metadatos no sensibles. | Historial append-only; no guardar payload, respuesta completa ni secretos en eventos. |
| `llm_invocations` | PK propia; FK `evaluation_id`. | proveedor/modelo; número de llamada/fallback; inicio/fin/duración; tokens de entrada/salida y fuente (`provider`, `estimated`, `unknown`); costo y fuente/moneda cuando disponibles; estado/error categorizado. | Métricas por cada llamada o fallback, sin guardar prompt ni respuesta cruda del proveedor por defecto. |
| `mcp_invocations` | PK propia; `evaluation_id` nullable cuando no corresponda. | herramienta; cliente/tenant autenticado disponible; inicio/fin/duración; estado y error categorizado; correlación. | Trazabilidad de operaciones MCP; sin duplicar contenido sensible del trabajo. |

**Contenido previsto del JSON `payload`:** copia validada de los datos requeridos para recuperar el trabajo: origen del ensayo y rúbrica en forma reprocesable, consigna/metadatos de actividad necesarios, metadatos de LMS y correlación disponibles, configuración de evaluación (proveedor solicitado, canal, `context_id`, `count_attempt`, `max_attempts` y otros parámetros funcionales). Excluir credenciales, tokens, headers de autenticación y secretos de URLs; si el procesamiento depende de una URL temporal o firmada, diseñar antes de implementar cómo preservar acceso sin persistir una credencial expuesta. La retención y protección del texto original quedan sujetas a decisión de privacidad.

**Contenido previsto del JSON `result`:** resultado canónico para el LMS: `status`, `evaluation_id`, `submission_id`, `nota_final`, `escala_maxima`, `feedback`, `fortalezas`, `areas_mejora`, `recomendaciones`, `rubric_assessment_canvas`, `modelo_utilizado` y `datos_lms`; añadir desglose `criterios` y metadatos de documentos solo si el contrato funcional aprobado los requiere. No asumir que datos secundarios internos del reporte local forman parte de la respuesta HTTP.

**Reglas de identidad e intentos que el esquema debe respetar:**

- `evaluation_id` identifica una ejecución lógica individual y no debe derivarse únicamente de `submission_id`. Canvas puede conservar el mismo `submission_id` al reenviar; cada nueva entrega que deba evaluarse necesita su propia evaluación y resultado, mientras que un reintento HTTP idempotente de la misma solicitud debe devolver el mismo `evaluation_id`.
- La clave del grupo de intentos incluye institución/LMS, curso, tarea y estudiante. Un `submission_id` identifica una entrega en el LMS, pero no sustituye la clave del grupo ni demuestra que la entrega no sea un reenvío.
- Si faltan `course_id`, `assignment_id` o `user_id`, el comportamiento actual es evaluar sin aplicar ni registrar el límite; el esquema debe representar `count_attempt=false/no elegible` y no inventar identidad.
- `count_attempt=true` aplica el `max_intentos` recibido cuando exista; de lo contrario usa `MAX_SUBMISSION_ATTEMPTS` (actual default documentado: 2). `count_attempt=false` corresponde a reevaluación administrativa y no consume el límite ordinario.
- El contador actual registra el intento después de una evaluación exitosa y el endpoint síncrono consulta el límite antes de llamar al modelo. **Reglas acordadas para el flujo asíncrono:** un intento académico se consume solo cuando el trabajo alcanza `EVALUATED`; al encolar se reserva capacidad contra el límite para evitar que solicitudes concurrentes lo excedan; si el trabajo alcanza el estado terminal `ERROR`, la reserva se libera y no consume intento. Reserva, liberación y consumo deben ser transaccionales. Una reevaluación con `count_attempt=false` no consume ni reserva un intento normal. Los reintentos técnicos conservan el mismo `evaluation_id` y quedan trazados como intentos de procesamiento; una nueva solicitud de evaluación académica recibe un `evaluation_id` nuevo. Política acordada: hasta 3 intentos de procesamiento por ciclo automático, con espera incremental; agotados, el trabajo pasa a `ERROR` terminal y solo una acción explícita puede reabrirlo con el mismo `evaluation_id`. La acción explícita no consume un segundo intento académico. Sigue pendiente cómo reacquirir la reserva atómicamente al reabrirlo y qué hacer si ya no hay capacidad disponible.

**Escenarios que debe cubrir la evaluación del esquema antes de implementarlo:**

| Escenario | Comportamiento/datos esperados para validar |
|---|---|
| Nueva solicitud válida | Crear `evaluation_jobs` en `PENDING`, persistir payload y correlación; responder `202` solo tras confirmar commit y entregar `evaluation_id`. |
| Consulta antes de ejecución o durante ella | El LMS consulta el mismo ID y recibe estado/correlación sin resultado prematuro; polling no crea ni duplica trabajos. |
| Evaluación completada | Actualizar esa fila a `EVALUATED`, guardar el JSON canónico completo; lecturas posteriores del endpoint devuelven ese JSON sin volver a invocar el modelo. |
| Error de proveedor/modelo o de procesamiento | Registrar evento y detalle por intento; los reintentos técnicos conservan el `evaluation_id`. Ejecutar hasta 3 intentos de procesamiento por ciclo automático con espera incremental; agotados, pasar a `ERROR` terminal y liberar la reserva sin consumir intento. Una acción explícita puede reabrir el trabajo con el mismo ID; definir reacquisición de reserva en ese caso. |
| Timeout/reenvío del mismo POST | Con clave de idempotencia, devolver la evaluación original; sin clave confiable, no deduplicar solo por `submission_id`, ya que puede ser un reenvío real de Canvas. |
| Canvas reenvía con el mismo `submission_id` | Crear otra evaluación cuando sea una nueva solicitud académica, mantener el grupo de intentos y conservar ambos resultados con sus `evaluation_id` distintos. |
| Límite alcanzado | No iniciar llamada LLM; devolver el rechazo contractual (actualmente HTTP 429) y no crear una evaluación procesable. Definir si se requiere un evento de rechazo auditable separado. Las reservas activas cuentan contra el límite para impedir sobrepasarlo concurrentemente. |
| Trabajo termina en `ERROR` | Registrar el fallo y liberar la reserva de intento en la misma operación lógica; no incrementar intentos consumidos. Probar fallos antes y después de iniciar llamadas al proveedor. |
| Reintento técnico | Mantener el mismo `evaluation_id`; agregar evento/contador del intento de procesamiento y preservar el historial de fallos; no consumir un segundo intento académico. Aplicar hasta 3 intentos por ciclo automático con espera incremental; tras `ERROR` terminal, un reintento requiere acción explícita. Definir reacquisición transaccional de la reserva y comportamiento si el grupo alcanzó el límite. |
| Nueva evaluación académica | Crear un `evaluation_id` nuevo aunque apunte al mismo `submission_id`; aplicar la regla de límite del grupo de intentos. |
| Reevaluación administrativa (`count_attempt=false`) | Permitir la evaluación autorizada sin incrementar el contador normal; conservarla en historial, con actor/origen administrativo si se dispone de esa identidad. |
| Falta metadata de grupo | Procesar según compatibilidad actual sin contabilizar intento; identificarlo explícitamente en la fila para no aparentar un control aplicado. |
| Caída tras commit o worker reiniciado | El trabajo aceptado sigue existiendo; recuperar estados abandonados `EVALUATING` y evitar que dos workers lo ejecuten simultáneamente. |
| `evaluation_id` no existente / acceso no autorizado | Responder `404` o el resultado de autorización definido, sin exponer si existe un ID de otro tenant ni sus datos. |

**Secuencia propuesta de implementación de Etapa 1 (pendiente de autorización; no ejecutar todavía):**

1. Cerrar decisiones de operación y privacidad: PostgreSQL objetivo, propietarios, secretos, backups/restauración, retención/borrado del payload y resultado, y contenido mínimo permitido.
2. Definir contrato y esquema relacional final: estados/transiciones, claves de tenant, JSONB, restricciones/índices mínimos, FK, timestamps, contador y reserva de intentos, eventos y política de idempotencia. Resolver cómo se reserva capacidad al reabrir un `ERROR` terminal.
3. Implementar configuración y migraciones versionadas: `DB_DSN` canónica con precedencia documentada sobre `DATABASE_URL`; instalación limpia, actualización y rollback verificables; no depender de `create_all` como mecanismo de evolución de esquema.
4. Consolidar `evaluation_jobs` como fuente de verdad y enlazar `submission_groups` mediante transacciones: escritura del payload + evento inicial + reserva antes del `202`; commit fallido debe producir error HTTP, nunca aceptación en memoria en despliegue durable.
5. Corregir identidad/idempotencia de solicitudes: `evaluation_id` opaco e independiente de `submission_id`; un reenvío técnico idempotente recupera el ID original mediante clave de idempotencia del cliente, mientras que una nueva solicitud académica obtiene otro ID aunque el LMS reutilice el `submission_id`.
6. Implementar ciclo del worker: reclamar trabajo de forma exclusiva, recuperar leases vencidos, registrar cada transición/intento, hasta 3 intentos automáticos con espera incremental y guardar el JSON canónico en el mismo `evaluation_jobs`; en `ERROR` terminal liberar la reserva sin consumir intento.
7. Alinear `GET /ensayos/estado/{evaluation_id}` con el contrato acordado y protegerlo con autenticación/autorización, tenant y límites de polling; probar respuestas pendientes, evaluadas, fallidas, inexistentes y no autorizadas.
8. Añadir telemetría LLM/MCP como persistencia complementaria sin prompts ni contenido sensible por defecto; mantenerla separada del ciclo crítico de encolado/resultado.
9. Validar SQLite/PostgreSQL, reinicios, concurrencia, idempotencia, límite de intentos, reintentos, retención, aislamiento, backup/restauración y fallos de DB. Cerrar Gate 1 solo con evidencia de staging y contrato documentado para el LMS.

- Tratar PostgreSQL como registro durable de auditoría de la evaluación: la fila y el payload de `/ensayos/enqueue` deben confirmarse antes de responder `202`; el resultado JSON debe actualizar el mismo trabajo al terminar. La respuesta `202` no se considera aceptada si el payload y el identificador durable no quedaron confirmados en DB.
- Definir recuperación de trabajos que queden en `EVALUATING` por caída del proceso, mediante vencimiento/lease o criterio equivalente; hacer idempotente el encolado y seguro el reintento del polling para no duplicar evaluaciones.
- Definir para `GET /ensayos/estado/{evaluation_id}` autenticación/autorización, aislamiento por institución/cliente, límites de polling, disponibilidad del resultado y política de retención; el identificador de evaluación no debe considerarse por sí solo una credencial de acceso.
- Separar la persistencia funcional/auditable de la telemetría: el payload necesario para procesar, resultado JSON, estados y usuario/correlación se guardan con acceso restringido; prompts y trazas de proveedor no se duplican en logs por defecto.
- Definir reglas temporales diferenciadas para payloads, resultados, identificadores personales, contexto, métricas agregadas y logs: plazo de retención, base legal/propósito, eliminación o anonimización al vencer, tratamiento de entregas reintentadas y excepciones de conservación por auditoría. Las métricas agregadas solo se retienen a largo plazo si ya no permiten identificar estudiantes.
- Mantener como decisión abierta si los reportes/artefactos adicionales al JSON de respuesta se guardan en PostgreSQL, como archivos/almacenamiento de objetos con referencia durable en DB, o en ambos; asegurar que la política temporal aplique coherentemente a copia y referencia.
- Definir claves de tenant/institución y controles de acceso a filas o consultas para que una métrica administrativa no exponga entregas de otra institución.
- Acordar transacciones y consistencia entre cola, tracker y resultado; índices/particionado solo con evidencia de volumen; pool y límites de conexiones; estrategia de migración/rollback y compatibilidad con los datos SQLite existentes.
- Añadir consultas/API administrativas de métricas solo con autorización y agregación adecuada; no ampliar endpoints públicos de estado individual para exponer datos globales.

**Métricas mínimas que se deben poder obtener**

- Volumen de trabajos por período, estado, institución/curso/tarea y herramienta MCP, con permisos apropiados.
- Auditoría por `evaluation_id`: payload recibido y validado, identidad/correlación disponible, secuencia temporal de estados, reintentos, resultado JSON y referencia al reporte.
- Tasa de éxito/fallo/reintento y tiempos de espera/procesamiento.
- Tokens de entrada/salida, proveedor/modelo, latencia y costo medido o estimado, identificando explícitamente métricas faltantes.
- Longitud de cola y antigüedad de trabajos pendientes, para operación y alertas.

**Pruebas y gate**

- Pruebas de migración, persistencia tras reinicio, concurrencia, idempotencia/reintento, integridad de referencias y recuperación de backup.
- Verificar que `/ensayos/enqueue` persista payload e identidad antes de responder `202`, que el worker recupere trabajos tras reinicio y que el resultado JSON y su historial de estados queden vinculados al mismo `evaluation_id`.
- Verificar el contrato de `GET /ensayos/estado/{evaluation_id}` para estados pendientes, en proceso, evaluados, fallidos e inexistentes; comprobar que el LMS puede consultar repetidamente el mismo identificador y recibe el JSON persistido al completar, sin duplicar el trabajo ni filtrar resultados de otro tenant.
- Verificar que usuario/estudiante, cliente, institución y entrega queden auditables cuando se suministran, sin guardar secretos ni contenido que no se haya autorizado.
- Probar expiración de retención, borrado/anonimización, consistencia de artefactos y que los datos ya vencidos no aparezcan en consultas de auditoría o métricas.
- Ejecutar consultas de métricas con datos de prueba y comprobar aislamiento entre tenants.
- Validar fallos de DB, saturación del pool y recuperación sin aceptar como durable un trabajo que no quedó persistido.

**Gate 1:** PostgreSQL de staging configurado; migraciones aplicables desde una instalación limpia; `/ensayos/enqueue` persiste payload y auditoría antes de responder `202`; resultado JSON y transiciones sobreviven reinicio bajo el mismo `evaluation_id`; retención, privacidad, respaldo y recuperación están acordados y probados.

### Etapa 2 — Seguridad de acceso para FastAPI y MCP

**Objetivo:** exigir credenciales y permisos antes de exponer el HUB a redes o clientes de producción.

**Clasificación de exposición:** el piloto se planea con un dominio público y acceso desde Internet. Por ello, el control actual de token compartido de entorno es transitorio y **no cumple por sí solo el gate de publicación pública**. Hasta cerrar este gate, mantener el servicio en staging detrás de VPN/allowlist de IP o de un gateway institucional con controles equivalentes; no publicar directamente el proceso Uvicorn.

**Diseño recomendado para evaluar**

- Para integraciones servidor-a-servidor controladas por la institución, considerar tokens opacos por cliente, con secreto mostrado una sola vez, almacenamiento solo de hash, rotación, expiración, revocación y scopes.
- Si se requiere identidad de usuarios, SSO o federación, evaluar OAuth 2.1/OIDC con un proveedor institucional. No crear un sistema propio de emisión de JWT sin necesidad comprobada.
- Incorporar un **bootstrap del primer administrador fuera de banda**, dependiente de PostgreSQL de la Etapa 1: un comando de administración o flujo de instalación de un solo uso crea el registro inicial con rol de superadministrador dentro de una transacción; debe fallar de forma segura si ya existe un administrador activo y no dejar una ruta HTTP pública de bootstrap.
- Persistir en PostgreSQL la identidad administrativa, estado, rol, emisor/subject de OIDC cuando aplique, fechas de creación/último acceso y eventos de auditoría. Preferir OIDC con MFA para administradores humanos; si se aprueba autenticación local, almacenar únicamente hashes de contraseña con algoritmo robusto (Argon2id), nunca contraseñas reversibles.
- Una vez establecido el superadministrador, permitir que solo él administre clientes de integración y sus tokens mediante endpoints administrativos protegidos: crear, consultar metadatos, rotar, revocar/desactivar y asignar scopes/tenant/expiración. No devolver secretos después de la emisión inicial, no permitir autoasignación de privilegios y auditar cada cambio con actor, fecha y resultado.
- Almacenar tokens opacos por consumidor como hashes no reversibles en PostgreSQL; asociar cada credencial a un cliente/tenant, permisos, estado, vencimiento y metadatos de uso. La autenticación debe identificar al consumidor y dejar esa identidad disponible para correlación con evaluaciones y llamadas MCP.
- Definir recuperación administrativa (pérdida del superadministrador, rotación de emergencia y revocación total) como procedimiento operacional fuera de banda, con doble control si lo requiere la institución; no crear un bypass oculto en la API.
- Proteger las rutas FastAPI sensibles, incluidas inicialización/cambio/cierre/eliminación de contextos, evaluación, administración Zoom y acceso a resultados. Definir excepciones mínimas para salud y metadatos públicos.
- Diseñar autenticación para MCP remoto Streamable HTTP de acuerdo con el transporte y los clientes compatibles; no asumir que el middleware HTTP de FastAPI protege un servidor MCP ejecutado aparte.
- Para MCP STDIO, documentar el límite de confianza local: acceso controlado por el host, permisos del proceso y protección de configuración/secrets. Un token HTTP no sustituye esos controles.
- Aplicar scopes/roles y aislamiento por institución/tenant en los recursos, además de autenticación. Un `context_id` no debe actuar como credencial.
- Establecer TLS en tránsito en el borde de producción y documentar el proxy de confianza; CORS con allowlist solo donde aplique a navegadores.
- Añadir límites de tamaño, timeouts, rate limits, protección contra repetición cuando corresponda y logs de auditoría sin credenciales ni contenido sensible.
- Revisar la descarga de adjuntos: permitir solo protocolos y destinos previstos, bloquear loopback/redes privadas y metadata endpoints, validar redirecciones y DNS, y fijar límites de tamaño/tiempo/tipo.
- Proteger Swagger/OpenAPI según el entorno y evitar mensajes de error con trazas o secretos.
- Para el piloto público, colocar el servicio detrás de un proxy inverso o gateway administrado con TLS vigente, terminación segura, límites de tamaño/tiempo/conexiones, rate limiting, protección contra abuso y logs de acceso sin secretos. No confiar en CORS como control de acceso.
- Configurar credenciales distintas para la documentación y para cada consumidor; limitar `/docs`, `/redoc` y `/openapi.json` por Basic Auth/OIDC y, si es posible, VPN/allowlist. La autenticación de Swagger solo permite consultar la documentación; las llamadas a las operaciones requieren además token de API y scopes.
- Aplicar mínimo privilegio al piloto: habilitar solo rutas necesarias; denegar por defecto operaciones administrativas de Zoom, modificación/eliminación de contextos y métricas globales a consumidores que no las requieran.
- Añadir límites de tasa/cuota por cliente para evaluación, chat y consultas; limitar tamaño de payload/adjuntos, concurrencia de jobs y gasto/uso LLM. Definir respuestas ante saturación y alertas de abuso.
- Probar explícitamente controles contra SSRF en descarga de `attachment.url`, enumeración/IDOR de resultados y contextos, acceso cruzado entre instituciones, repetición de solicitudes y abuso de endpoints costosos.
- Usar datos sintéticos o anonimizados en el piloto inicial salvo aprobación institucional explícita para datos académicos reales; fijar consentimiento/base de uso, retención y procedimiento de borrado antes de invitar participantes.
- Preparar procedimiento de incidente: revocar credencial comprometida, bloquear cliente/IP, rotar secretos, preservar evidencia no sensible, notificar responsables y recuperar el servicio.

**Pruebas y gate**

- Sin credenciales: `401` en rutas protegidas; con credencial válida pero sin permisos: `403`.
- Probar expiración, revocación, rotación, scopes, aislamiento entre instituciones y abuso de límites.
- Probar bootstrap con DB vacía, concurrencia de inicialización, rechazo del bootstrap cuando ya existe administrador, persistencia tras reinicio, y ausencia de secretos en DB, respuestas y logs.
- Probar que endpoints administrativos rechacen clientes ordinarios, cambios de rol no autorizados y asignaciones de scopes/tenants fuera del permiso del actor; verificar auditoría y recuperación administrativa documentada.
- Probar acceso MCP remoto sin credencial y con credencial inválida; verificar por separado el modo STDIO.
- Revisar que healthchecks y excepciones públicas no revelen información interna.
- Ejecutar pruebas externas desde Internet contra staging: TLS y cabeceras del borde, rate limiting/cuotas, límites de carga, rutas expuestas, intentos de acceso cruzado/IDOR, SSRF y ausencia de operaciones administrativas no autorizadas.
- Simular compromiso y revocación de una credencial de consumidor, picos de solicitudes, proveedor LLM indisponible y cola saturada; confirmar alertas, aislamiento y recuperación.
- Confirmar que documentación y credenciales de API usan secretos distintos y que la exposición de `/docs` no permite invocar operaciones sin un token con permisos adecuados.
- Ejecutar revisión de seguridad enfocada y pruebas de regresión del contrato.

**Gate 2 (bloqueante para exposición pública):** ningún recurso sensible queda accesible sin identidad y permiso definidos; el primer administrador se aprovisiona fuera de banda y persiste en PostgreSQL; solo el superadministrador gestiona credenciales de integración; los secretos se muestran una sola vez y se guardan como hashes; cada consumidor usa credencial revocable y scopes mínimos; FastAPI y MCP remoto pasan pruebas de aislamiento; el borde HTTPS/gateway, rate limits, límites de carga, revisión SSRF/IDOR, alertas y respuesta a incidentes están verificados. La documentación usa protección diferenciada. Las credenciales de DB se gestionan como secretos y los permisos de aplicación limitan acceso a datos y métricas. **No exponer el piloto a Internet hasta cerrar y aprobar este gate.**

### Etapa 3 — Diseño y prueba del modelo de contexto para LLM (CML)

**Objetivo:** reducir tokens repetidos sin degradar la evaluación, perder políticas o presentar información resumida como dato original.

**Arquitectura a validar**

1. **Contexto canónico:** fuente estructurada y versionada para políticas institucionales, datos comunes de curso/tarea, rúbrica, preferencias y metadatos de procedencia.
2. **Contexto por solicitud:** datos particulares de la entrega, del usuario y del trabajo; aislamiento estricto respecto al contexto compartido.
3. **Capas de representación:** original conservado según política de retención; extracción determinista de campos; resumen compacto con versión, origen, fecha y hash de las fuentes.
4. **Ensamblado por presupuesto:** seleccionar bloques relevantes para cada herramienta/modelo, estimar el prompt real y respetar límites configurados.
5. **Caché e invalidación:** reutilizar una representación solo cuando coincidan fuente, identidad, versión de esquema, método y política; invalidarla al cambiar rúbrica o consigna.
6. **Proveedores:** comprobar si el proveedor/modelo soporta caché de prefijos o contexto reutilizable y si el ahorro se refleja en tokens facturados. No contar almacenamiento local como ahorro del proveedor.

**Guardas de calidad**

- No resumir ni truncar silenciosamente criterios, ratings o escalas autoritativas de una rúbrica.
- No asumir que el ensayo del estudiante puede comprimirse sin cambiar la calificación; cualquier resumen de trabajos requiere evaluación propia y aprobación.
- Tratar instrucciones externas y contenido de documentos como datos no confiables, no como instrucciones de sistema.
- Mantener políticas institucionales críticas completas o en una forma estructurada validable; si el presupuesto no permite preservarlas, fallar de forma explícita o cambiar de estrategia.
- Evitar incluir identificadores personales en resúmenes compartidos; definir retención y borrado.

**Experimento y métricas**

- Preparar un conjunto representativo de tareas y entregas anonimizadas o sintéticas con rúbrica de referencia.
- Comparar baseline y alternativas: prompt actual, selección/extracción estructurada, resumen extractivo y resumen asistido por modelo cuando sea justificable.
- Medir tokens reales de entrada/salida, costo, latencia, calidad de nota y retroalimentación, fidelidad a rúbrica, omisiones, alucinaciones y fuga entre usuarios/contextos.
- Fijar umbrales de aceptación después del baseline; evitar declarar ahorros o calidad sin evidencia.

**Gate 3:** existe una opción de CML que muestra ahorro medible y no-regresión aceptable en evaluación; resúmenes trazables e invalidables; datos sensibles y políticas quedan protegidos. Si ninguna opción cumple, mantener el compilador actual y no activar resumen automático.

### Etapa 4 — Implementación gradual del CML aprobado

**Objetivo:** incorporar solo la estrategia aprobada, manteniendo una ruta de rollback.

**Trabajo propuesto**

- Formalizar los tipos de contexto y la política de retención/versionado.
- Añadir generación y persistencia de resúmenes con provenance, hash e invalidación, sin sustituir la fuente original.
- Integrar el ensamblador en los puntos de uso LLM y comprobar el prompt realmente enviado.
- Añadir métricas sin contenido sensible: tokens antes/después, modo, versión de resumen, duración y resultado.
- Activar primero en modo sombra o por feature flag; comparar resultados sin influir notas reales.
- Habilitar por institución/consumidor de forma gradual, con desactivación rápida.

**Gate 4:** pruebas unitarias e integración, suite de regresión, evaluación comparativa aprobada y rollback demostrado.

### Etapa 5 — Cerrar deuda técnica de estabilidad

**Objetivo:** cerrar los pendientes heredados que afectan confiabilidad y mantenimiento.

- **C2.1:** structured output y validación tipada del resultado LLM, con reintento acotado y fallback explícito.
- **C2.2:** resolver OpenAI real frente a retiro del catálogo; documentar compatibilidad y errores esperados.
- **C2.4:** descomponer `zoom_reports.py`, compartir cliente/utilidades, migrar I/O síncrono que afecte event-loop y conservar el formato de reportes.
- Confirmar de manera verificable el Gate 0, incluida la ejecución de tests en CI o entorno reproducible.
- Mantener fuera de alcance cambios funcionales no relacionados con estos gates.

**Gate 5:** regresión completa de endpoints afectados, comportamiento LLM válido ante respuestas malformadas y reportes Zoom equivalentes.

### Etapa 6 — Preparación y salida controlada a producción

**Objetivo:** operar con seguridad, capacidad de recuperación y criterios de aceptación.

- Promover la PostgreSQL validada en Etapa 1, con backups y restauración probada, migraciones, monitoreo de capacidad y política de retención.
- Configurar secretos mediante el gestor de secretos del entorno; verificar rotación y ausencia de secretos en logs, repositorio, imágenes y documentación.
- Definir TLS, DNS, proxy/firewall, límites de tráfico, tamaño y concurrencia según capacidad medida.
- Añadir health/readiness, métricas, alertas de errores/cola/DB/proveedores LLM, trazabilidad por IDs no sensibles y runbooks de incidentes.
- Probar carga del worker, persistencia tras reinicio, reintentos, caída de proveedor, cola acumulada y restauración.
- Validar OpenAPI/MCP, flujos de Canvas, cierre/gracia de contexto y resultados en ambiente de staging.
- Desplegar primero a un grupo piloto con monitorización y procedimiento de rollback.

**Gate 6:** checklist de producción firmado, controles de seguridad aprobados, backups restaurables, alertas operativas y piloto estable. El piloto público solo se habilita si el Gate 2 está cerrado; de lo contrario, permanece limitado a VPN/allowlist o gateway institucional.

### Etapa 7 — Expansión de dominios MCP

Una vez estabilizada la plataforma, priorizar CRM, asistencia académica y analítica Zoom según valor de negocio. Cada herramienta debe reutilizar autorización, contexto y telemetría comunes, tener scopes propios, pruebas de aislamiento y contrato documentado. El registry MCP queda opcional hasta que el número de herramientas justifique su costo.

## 5. Orden recomendado y dependencias

```text
Etapa 0: inventario + baseline + decisiones
    ↓
Etapa 1: PostgreSQL + persistencia de evaluaciones + métricas
    ↓
Etapa 2: seguridad de acceso y aislamiento
    ↓
Etapa 3: diseño/benchmark CML
    ↓
Etapa 4: implementación CML con feature flag
    ↓
Etapa 5: cierre C2.1 / C2.2 / C2.4 y regresión
    ↓
Etapa 6: staging → piloto → producción
    ↓
Etapa 7: nuevos dominios MCP
```

El inventario de seguridad y CML puede prepararse durante Etapa 0. La implementación de autenticación depende de definir clientes/scopes y acceso a datos; el resumen CML no debe desplegarse con datos reales antes de acordar seguridad, privacidad y retención. La seguridad y el CML deben usar el modelo de persistencia, identidad y retención acordado para PostgreSQL. La decisión de hacer el piloto accesible desde un dominio público queda registrada, pero **no constituye autorización para publicar antes de cerrar el Gate 2**.

## 6. Decisiones que requieren aprobación

1. **Identidad administrativa y de clientes:** aprobar el bootstrap fuera de banda del primer superadministrador; decidir OIDC/MFA para administradores humanos; tokens opacos por integración como primera opción para servicios, o OAuth 2.1/OIDC desde el inicio si se requiere federación.
2. **Límite de confianza MCP:** MCP remoto autenticado directamente en el HUB o detrás de un gateway institucional; acceso local STDIO y sus responsables.
3. **Autorización:** scopes/roles y aislamiento por institución; clientes y operaciones que quedan explícitamente públicas.
4. **PostgreSQL y auditoría:** confirmar plataforma/servicio, responsables y respaldo/recuperación; formato JSON de resultados; identidad/campos auditables; y si reportes se guardan en DB, almacenamiento de archivos o ambos.
5. **Retención:** acordar plazos por payload, resultado JSON, identificadores, contextos, métricas y logs, junto con reglas de borrado/anonimización y excepciones institucionales.
6. **Métricas:** confirmar quién puede consultar métricas por institución/curso y si el costo se reporta como valor del proveedor o estimación cuando falten datos reales.
7. **CML y datos:** campos que pueden resumirse, datos que deben conservarse originales, retención/borrado y posibilidad o no de usar un LLM para resumir.
8. **Objetivos de calidad y costo:** umbral mínimo de fidelidad evaluativa y ahorro de tokens aceptable, a determinar tras medir baseline.
9. **Piloto público:** confirmar dominio, dueño operativo del gateway/WAF o proxy, allowlist administrativa, política de datos piloto y umbrales de rate limit/cuota/uso de LLM.
10. **Producción:** plataforma objetivo, responsable de secretos/DB/backups, necesidad de Docker y plan de despliegue/rollback.
11. **OpenAI:** proveedor funcional implementado; falta smoke test con credenciales y decidir modelo/costo de producción.

## 7. Registro de avance

| Fecha | Etapa | Estado | Evidencia / decisión |
|---|---|---|---|
| 2026-10-20 | Cierre de preparación PMV on-prem | Cambios locales listos para validar; sin ejecución remota | Se añadió migración `004` para `contexts` y `submission_groups`; el runtime PostgreSQL ya no intenta `create_all` para esas tablas. Se preparó `scripts.prepare_production_db`, que solo aprovisiona DB/roles nuevos, pide entradas sensibles ocultas, requiere confirmación y asigna DML al rol runtime; Azure/VPN y storage multi-host quedan para activación posterior. Plantillas systemd/Nginx aportan bind localhost, hardening básico, TLS, límites por IP y timeout de proxy. Zoom privado/MCP se pospone a fase 2; `/ready` solo refleja el arranque del PMV. Se ejecutará suite local; nada aplicado a producción. |
| 2026-10-20 | Optimización Zoom y autorización MCP | Optimización local de reportes; acciones privadas siguen bloqueadas | Se acordó autorizar reuniones mediante allowlist por cliente/institución. La identidad humana del chat aún no tiene mecanismo verificable definido; no confiar en headers de identidad sin firma y mantener MCP HTTP privado cerrado. Reportes reutilizan sesión por hilo, timeout HTTP de 60 s, máximo dos reportes concurrentes y rechazo `429`; escaneos masivos de licencias son exclusivos; helpers síncronos usan timeout de 5 s; rutas asíncronas comparten cliente por proceso con pool limitado (10 conexiones/5 keep-alive) y cierre en shutdown. `/ready` también verifica presencia de configuración Zoom, no el handshake remoto. Pruebas específicas, suite completa y compilación pasan. No se llamaron servicios Zoom ni se validó despliegue. |
| 2026-10-20 | Preparación local de Azure y Ubuntu | Runbook y workflow manual preparados; no desplegado | Se cambió Actions a ejecución manual desde `main`, con tests antes de deploy, GitHub Environment `production-azure`, variables de nombre/URL, secreto de publish profile y smoke test `/health`. Se añadió `DEPLOYMENT_PRODUCTION.md`; el script `deploy_server.sh` quedó bloqueado por acciones destructivas y configuración insegura. Arquitectura acordada: dos apps y PostgreSQL autoritativa on-prem; VPN y proveedor de artefactos compartidos pendientes. No se accedió a Azure/Ubuntu ni se aplicaron migraciones. No activar hasta resolver los bloqueos y obtener autorización expresa. |
| 2026-10-20 | Separación de migraciones DDL y API runtime | Implementación local lista; pendiente desplegar y validar en staging | Añadido `python -m API.core.postgres_migrations apply`, que usa `MIGRATION_DB_DSN` sin imprimirlo; en `APP_ENV=production`, el arranque ahora solo verifica versiones aplicadas y falla cerrado si faltan. Desarrollo conserva la aplicación automática. Pruebas de esquema inexistente, migraciones pendientes y esquema al día pasan. No se conectó ni modificó PostgreSQL. La discrepancia de checksum histórico de `001` sigue sin reconciliarse; la verificación valida versiones, no checksums. |
| 2026-10-20 | Autenticación y autorización por cliente | Implementación local completa; staging y Gate 2 pendientes | `003_api_client_tokens.sql` crea clientes y eventos de auditoría; credenciales aleatorias almacenadas como SHA-256, expiración, rotación y revocación. CLI fuera de banda: `issue`, `rotate`, `revoke` y `list` (sin mostrar secretos en listados). Scopes por operación, ámbito institucional y tenant, y comprobaciones de ownership en contextos/ensayos; se deniegan Zoom, MCP remoto y rutas globales sin aislamiento verificable. Suite completa `pytest tests -q -o filterwarnings=ignore` y compilación local pasan. No se aplicó migración ni se validó contra staging. Antes de exponer: aplicar y comprobar `003` con autorización del responsable; realizar pruebas de integración/aislamiento en staging; separar rol DDL/runtime; resolver checksum histórico de `001`; verificar TLS/gateway, límites de tasa/carga, logging/alertas y Gate 2. |
| 2026-10-20 | Baseline local urgente | Suite automatizada completa en verde; despliegue y gate de producción siguen pendientes | `pytest tests -q -o filterwarnings=ignore`: pasó la suite completa. Se corrigió la conversión tipada de `ARTEMIS_ENABLED`, `ARTEMIS_PORT` y `OLLAMA_TIMEOUT` al cargar variables de entorno; se alinearon las pruebas de configuración con la API vigente y con la exclusión de Artemis de esta fase. La ejecución fue local; no valida PostgreSQL de staging, TLS/gateway, aislamiento multi-tenant ni autorización por scopes. |
| 2026-10-19 | PMV PostgreSQL, OpenAI, Zoom y seguridad de aplicación | Implementación local verificada; GitHub, staging y gate de producción pendientes | Cola asíncrona conectada a `evaluation_jobs`, reserva/consumo de intentos, eventos, intentos técnicos, leases/recovery y telemetría LLM/MCP. IDs opacos y `Idempotency-Key`; integración real verificada contra `hub_test` (incluye límite, telemetría y lease agotado). OpenAI Chat Completions y ruta `/llm/chat` implementados con tests mock. Zoom reportes comparte OAuth/helpers y usa threadpool sin cambiar rutas/Excel. Seguridad: guard de `APP_ENV=production`, `/ready`, allowlist SSRF y límite de 25 MB. `pytest tests -q -o filterwarnings=ignore --ignore=tests/test_config.py`: verde; `test_config.py` depende de `validate_artemis`, fuera del alcance confirmado. No publicar aún: bearer compartido, falta scopes/aislamiento multi-tenant, separación de rol DDL/runtime, verificación de checksum `001`, TLS/gateway/rate limits y validación staging pendientes. Sin push ni PR: cuenta GitHub no conectada. Artemis excluido de esta fase. |
| 2026-10-16 | Migraciones iniciales PostgreSQL de evaluaciones | Aplicadas en `hub_test`; integración pendiente | Se aplicaron `001` y `002` en PostgreSQL 17.11, puerto 5433. El registro contiene ambas versiones y las siete tablas esperadas; `002` renombra `submission_groups` a `evaluation_submission_groups` para evitar la colisión con el tracker existente. La huella SHA-256 registrada para `001` en la base (`9a1de7e4…`) no coincide con la huella declarada en el archivo actual (`6e03e829…`); no se modificó retroactivamente esa migración ni su registro. Verificar y reconciliar esta discrepancia antes de automatizar la comprobación de checksums. La cola y el tracker aún no están adaptados al nuevo esquema; no usarlo como backend durable de la aplicación hasta completar esa integración y sus pruebas. |
| 2026-10-16 | Variable de conexión y secuencia de Etapa 1 | Acordado para el plan; no implementado | `DB_DSN` es la variable canónica; `DATABASE_URL` queda como alias solo cuando `DB_DSN` no exista, y si ambas están definidas prevalece `DB_DSN`. Se añade secuencia de implementación propuesta para la Etapa 1, aún pendiente de autorización. También se registra como brecha que el ID generado desde `submission_id` no satisface la regla acordada de IDs independientes para nuevas evaluaciones; la idempotencia debe basarse en una clave propia, no en `submission_id`. |
| 2026-10-16 | Reglas de intentos y reintentos | Acordadas para el diseño; no implementadas | El intento académico se consume solo en `EVALUATED`; pendiente/en proceso reserva capacidad y `ERROR` terminal libera la reserva sin consumir. El reintento técnico conserva el mismo `evaluation_id`; una nueva evaluación académica crea uno nuevo. Se permiten hasta 3 intentos de procesamiento por ciclo automático, con espera incremental; después se requiere acción explícita para reabrir el mismo ID. Pendiente definir reacquisición atómica de la reserva tras `ERROR` terminal y comportamiento si ya no hay capacidad. |
| 2026-10-15 | Diseño de persistencia de evaluaciones | Borrador de esquema para revisión; no implementado | Se aprueba guardar en PostgreSQL el payload necesario para procesar cada ensayo y el JSON de respuesta en el registro de la misma evaluación, identificado por `evaluation_id`. El LMS consultará `GET /ensayos/estado/{evaluation_id}`; no se incluye entrega saliente del HUB. El plan propone entidades lógicas para trabajo, grupo de intentos, eventos de estado y telemetría, además de escenarios de idempotencia, reenvío con `submission_id` reutilizado, límite, reevaluación administrativa, errores, reinicios y aislamiento. Las reglas de consumo de intentos, identidad de reintentos y reintento automático se acordaron el 2026-10-16. Siguen pendientes la reserva tras `ERROR` terminal, retención/borrado, operación PostgreSQL y almacenamiento de artefactos. No se modificó código ni se inició la ejecución del plan. |
| 2026-10-14 | Propuesta | Pendiente de aprobación | Plan creado; PostgreSQL, auditoría del payload aceptado por `/ensayos/enqueue`, resultado JSON e historial de estados con retención se priorizan en Etapa 1; se agrega a Etapa 2 bootstrap administrativo fuera de banda persistido en PostgreSQL y gestión controlada de tokens por superadministrador. El piloto se prevé público por dominio HTTPS; Gate 2 de seguridad es bloqueante antes de exposición y requiere credenciales individuales, scopes, gateway/TLS, límites, pruebas de abuso y plan de incidentes. No se ejecutaron cambios funcionales ni pruebas como parte de esta planificación. |
| 2026-10-14 | Baseline previo a piloto público | Completado; controles pendientes | Inventario: FastAPI monta rutas de contexto, ensayos, LLM, Zoom y echo; salvo `GET /health`, las rutas actuales usan un único `HUB_API_TOKEN` compartido y no se observó autorización por scopes/cliente. `/docs`, `/redoc` y `/openapi.json` también exigen ese Bearer, no usuario/contraseña de documentación. El MCP Streamable HTTP standalone reutiliza el mismo token. No se encontraron rate limits en la capa de aplicación revisada; protección del gateway, TLS real, dominio y allowlists requieren validación con el responsable del despliegue. `tests/test_api_auth.py`: 13 pruebas pasan. No se modificó código ni se expuso el servicio. |
| 2026-10-14 | Acceso a documentación | Implementado | `/docs`, `/redoc`, `/openapi.json` usan `HUB_DOCS_USERNAME`/`HUB_DOCS_PASSWORD` por HTTP Basic; las operaciones FastAPI mantienen Bearer `HUB_API_TOKEN`; solo `GET /health` es público. Pruebas enfocadas de auth: 18 pasan. Esto no sustituye las credenciales individuales/scopes ni el Gate 2 requerido para el piloto público. |

### Continuación de la bitácora

| Fecha | Actividad | Estado | Evidencia / siguiente paso |
|---|---|---|---|
| 2026-10-14 | Verificación de acceso a Swagger | Pendiente | Al abrir la documentación se recibió `API documentation authentication is not configured`. Confirmar que `HUB_DOCS_USERNAME` y `HUB_DOCS_PASSWORD` están definidas en el `.env` o entorno que utiliza el proceso, reiniciar el servidor y volver a probar `/docs`. No se han verificado las credenciales ni se considera resuelto el acceso. Un valor de credencial quedó expuesto accidentalmente en el intercambio; rotarlo y no registrar su valor en documentación, logs ni repositorio. Retomar esta comprobación en la próxima sesión. |

Al aprobar una etapa, actualizar esta tabla con alcance aceptado, responsable, PR/cambios, resultados de pruebas, riesgos remanentes y decisión de gate.
