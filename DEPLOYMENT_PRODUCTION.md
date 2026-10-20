# Preparación de producción: Azure y Ubuntu on-premise

## Topología acordada

- Dos despliegues de aplicación separados: Azure App Service y un servidor
  Ubuntu on-premise.
- Ambos usarán la PostgreSQL autoritativa on-premise. La base y la conectividad
  son dependencias compartidas; esto no es alta disponibilidad independiente.
- Azure solo podrá acceder a PostgreSQL por VPN privada. La VPN está pendiente;
  no exponer el puerto de PostgreSQL a Internet.
- El proveedor de almacenamiento de artefactos compartidos todavía no está
  decidido. Para un PMV en un solo host on-prem, los archivos locales son
  suficientes si se respaldan. No habilitar Azure y Ubuntu como dos hosts
  equivalentes hasta migrar informes bajo `storage/evaluaciones` a un storage
  compartido.
- No existe todavía un staging separado. Estas tareas preparan producción; no
  autorizan ni ejecutan un despliegue real.

## Bloqueos antes de activar Azure

1. **Solo para Azure:** crear y probar VPN site-to-site hacia la red on-premise. Restringir el
   firewall PostgreSQL al origen privado de Azure y a los hosts autorizados.
2. **Solo para dos hosts activos:** elegir e implementar object storage compartido para informes. Definir
   credenciales, retención, borrado, backups y migración de archivos locales.
3. Si se reutilizará una base existente, resolver la discrepancia histórica del
   checksum `001` y confirmar backup/restauración. El aprovisionador de este
   runbook solo acepta bases nuevas; para ellas se aplican las migraciones
   versionadas actuales desde cero.
4. Ejecutar migraciones desde el host administrativo on-premise con el rol DDL;
   el servicio de API debe usar únicamente el rol runtime. No introducir el DSN
   DDL en Azure ni en el entorno normal del servicio.
5. Configurar App Service/Linux Python 3.11, Health Check `/ready`, acceso a
   PostgreSQL privado y settings de producción. Mantener la API detrás de TLS,
   gateway/controles del borde y acceso administrativo restringido.
6. Zoom privado/MCP queda fuera del PMV y pasa a fase 2. Mantener cerradas sus
   acciones hasta definir identidad humana verificable y permisos/contexto.

## Zoom: límites de operación y autorización

Los reportes síncronos reutilizan una sesión HTTP por hilo, aplican timeout de
60 segundos por petición a Zoom y admiten como máximo dos reportes simultáneos;
las solicitudes adicionales reciben `429` con `Retry-After`. Los escaneos
masivos de licencias se ejecutan de uno en uno. Los helpers síncronos de Zoom
usan un timeout de 5 segundos. Las rutas asíncronas reutilizan un cliente HTTP
por proceso/event loop con pool limitado a 10 conexiones y 5 keep-alive, y
cierran el cliente al apagar la API; conservan su timeout de 15 segundos.

`/ready` indica que la aplicación completó su inicialización; Zoom no es una
dependencia del PMV y no afecta este health check. La verificación OAuth Zoom
se hará cuando se implemente la fase 2.

Estos límites evitan que reportes simultáneos ocupen sin límite los workers,
pero no sustituyen una cola durable para trabajos que excedan el tiempo del
cliente HTTP. Ninguna llamada real a Zoom se ejecutó como parte de esta
preparación.

## Azure App Service

El workflow `.github/workflows/deploy.yml` es manual, solo despliega desde
`main`, ejecuta toda la suite antes del despliegue, requiere el GitHub
Environment `production-azure` y comprueba `/health` después de publicar.
Configurar revisores obligatorios en ese Environment antes de habilitarlo.

Configurar en `production-azure`:

- Variables `AZURE_WEBAPP_NAME` y `AZURE_WEBAPP_URL` (URL HTTPS base, sin ruta).
- Secreto `AZURE_WEBAPP_PUBLISH_PROFILE`, limitado a la Web App de producción.
- Variables de ejecución en Azure App Settings o gestor de secretos de Azure,
  no en el workflow ni en el código.
- Activar `SCM_DO_BUILD_DURING_DEPLOYMENT=true` en App Settings para que Oryx
  instale las dependencias de `requirements.txt` al publicar el paquete.

App Settings mínimos:

| Nombre | Uso |
|---|---|
| `APP_ENV` | `production` |
| `API_CLIENT_AUTH_ENABLED` | `true`; autenticación individual por cliente |
| `DB_DSN` | PostgreSQL runtime on-prem, alcanzable por VPN privada |
| `HUB_DOCS_USERNAME` / `HUB_DOCS_PASSWORD` | Basic Auth de documentación; contraseña de al menos 16 caracteres |
| `ATTACHMENT_ALLOWED_HOSTS` | Dominios exactos de LMS/CDN necesarios |
| `OPENROUTER_API_KEY` | Solo si el proveedor habilitado es OpenRouter |
| `MAX_SUBMISSION_ATTEMPTS` / `LOG_LEVEL` | Política operativa y logging |

`HUB_API_TOKEN` no sustituye la autenticación de clientes en producción. No
configurar `MIGRATION_DB_DSN` en App Service. La API no debe tener permisos DDL.

## Ubuntu on-premise: procedimiento manual

El script histórico `deploy_server.sh` está bloqueado: eliminaba paquetes y
logs del host, podía clonar un repositorio distinto, configuraba SQLite/
`DATABASE_URL` y escribía secretos directamente en un `.env`. No volver a
ejecutarlo.

Preparación manual requerida:

1. Usar una versión Ubuntu LTS soportada con Python 3.11, cuenta de servicio
   dedicada sin login interactivo y directorios de aplicación/estado con
   propietarios y permisos mínimos.
2. Obtener una release identificada por tag/commit aprobado; no usar `git pull`
   sin fijar versión. Instalar dependencias desde `requirements.txt`.
3. Mantener secretos fuera del checkout, por ejemplo en un archivo de entorno
   protegido para systemd o en un gestor de secretos; permisos solo para root y
   la cuenta del servicio. No guardar `.env` en GitHub ni pegar secretos en
   terminales compartidas.
4. Configurar las mismas variables runtime requeridas en Azure (con su propio
   `DB_DSN` runtime), excepto valores específicos del proveedor/host. No copiar
   `MIGRATION_DB_DSN` al servicio.
5. Preparar la base nueva con `python -m scripts.prepare_production_db` desde
   el checkout aprobado. El asistente pide DSN DBA, nombre de base y
   contraseñas sin imprimirlas, exige confirmación, rechaza bases/roles
   existentes y aplica `001`–`004`. Requiere PostgreSQL 15+; revisar
   [migrations/README.md](./migrations/README.md) y obtener backup/ventana
   aprobados antes de ejecutarlo.
6. Configurar `DB_DSN` runtime y `MIGRATION_DB_DSN` DDL por separado. Mantener
   los secretos en `/etc/mcp-hub/runtime.env` (root propietario, permisos
   `0640`, grupo `mcp-hub`) o gestor de secretos; nunca dentro del checkout.
7. Revisar/adaptar e instalar las plantillas
   [mcp-hub.service.example](./deploy/ubuntu/mcp-hub.service.example) y
   [mcp-hub.conf.example](./deploy/nginx/mcp-hub.conf.example). Uvicorn queda
   ligado a localhost, systemd aplica hardening básico, Nginx termina TLS,
   redirige HTTP→HTTPS, limita peticiones por IP y permite hasta 180 s para
   peticiones de reporte. Sustituir dominio/certificado/rutas/usuario antes de
   habilitar; no abrir directamente el puerto 8000 al exterior.
8. Antes de activar cambios de esquema, respaldar y verificar restauración;
   reiniciar el servicio y comprobar `/ready`, autenticación, enqueue, consulta
   de estado y logs sin secretos.
9. Registrar el commit desplegado y el procedimiento de rollback. El rollback
   de código no debe revertir migraciones de base automáticamente.

No se creó ni instaló un servicio en el host Ubuntu: faltan sus datos de acceso,
dominio, proxy/TLS y configuración de secretos, y esta solicitud fue de
preparación, no de ejecución remota.

## Variables y credenciales

`DB_DSN` es la conexión runtime PostgreSQL. `MIGRATION_DB_DSN` se inyecta solo
en la operación administrativa de migraciones. `API_CLIENT_AUTH_ENABLED=true`
es obligatorio y los clientes se emiten fuera de banda con
`python -m API.core.manage_api_clients`; la base guarda hashes, nunca el token
en claro. Las credenciales de clientes se deben provisionar en el gestor de
secretos de cada consumidor.

No configurar una sola `HUB_API_TOKEN` compartida entre los dos despliegues.
Separar valores y controlar acceso por entorno, rotación y auditoría.

## Estado

Preparación local únicamente. El aprovisionador de base nueva, la migración
`004` (tablas runtime sin DDL en el servicio) y las plantillas systemd/Nginx
quedaron preparadas; el script antiguo sigue bloqueado. No se ha conectado a
Azure, no se ha accedido al servidor, no se han creado bases ni aplicado
migraciones y no se han enviado secretos. Azure/VPN y storage compartido para
dos hosts siguen pendientes. Zoom privado/MCP quedó explícitamente en fase 2.
