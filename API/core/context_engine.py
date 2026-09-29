# API/core/context_engine.py

import os
import time
import uuid
import contextvars
from typing import Optional, Dict, Any, Tuple, List
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

try:
    from API.core.logging import get_logger
except ImportError:  # pragma: no cover
    from core.logging import get_logger

try:
    from API.core.schemas import (
        SCHEMA_VERSION,
        migrate_slot_data,
        validate_slot_data,
        ensure_schema,
    )
except ImportError:  # pragma: no cover
    from core.schemas import (
        SCHEMA_VERSION,
        migrate_slot_data,
        validate_slot_data,
        ensure_schema,
    )

logger = get_logger("core.context_engine")

# ContextVar local para propagación transparente a través de llamadas asíncronas
_current_context_var: contextvars.ContextVar[Optional['InstitutionalContext']] = contextvars.ContextVar(
    "current_institutional_context", default=None
)


def get_current_context() -> Optional['InstitutionalContext']:
    """
    Retorna el contexto institucional activo para la solicitud actual en ejecución.
    Permite que cualquier herramienta MCP o módulo acceda al contexto sin recibirlo en su firma.
    """
    return _current_context_var.get()


def set_current_context(ctx: Optional['InstitutionalContext']):
    """Establece el contexto institucional en la variable de contexto."""
    _current_context_var.set(ctx)


# ============================================================
# MODELO DEL ENVELOPE DE CONTEXTO INSTITUCIONAL
# ============================================================
class InstitutionalContext(BaseModel):
    """
    Contenedor universal de contexto institucional por ranuras (Namespaced Context Fabric).
    Permite almacenar tanto identidad global como datos específicos de herramientas presentes y futuras.
    """
    context_id: str = Field(default_factory=lambda: f"ctx_{uuid.uuid4().hex[:12]}")
    created_at: float = Field(default_factory=time.time)
    ttl_seconds: int = Field(default=3600)  # 1 hora por defecto

    # Versión del contrato de slots (FASE 1 · B1.1 `context-contract`).
    # Contextos persistidos con anterioridad no traen el campo → `ensure_schema`
    # lo rellena con SCHEMA_VERSION al ser cargados.
    schema_version: int = Field(default=SCHEMA_VERSION, description="Versión del contrato de los slots")

    # 1. Identidad y Gobernanza Institucional (Global)
    institution_id: str = Field(default="UNIVERSIDAD_TECNICA_PARTICULAR_DE_LOJA_UTPL", description="Código o nombre de la institución")
    tenant_id: Optional[str] = Field(default="GENERAL", description="Facultad, sede o departamento")
    system_source: str = Field(default="GENERIC_API", description="Sistema origen: CANVAS_LMS, HUBSPOT_CRM, ZOOM_APP, N8N_FLOW")
    user_id: Optional[str] = Field(default=None, description="Identificador único del usuario actor")
    user_role: Optional[str] = Field(default="ESTUDIANTE", description="Rol: DOCENTE, ESTUDIANTE, COORDINADOR, ADMIN")

    # 2. Políticas y Directivas transversales
    evaluation_scale: Optional[float] = Field(default=10.0, description="Escala de calificación (ej. 10.0 vigesimal, 100.0)")
    tone_policy: str = Field(default="pedagógico, constructivo y formal", description="Directiva de tono para los LLMs")
    global_policy: Optional[str] = Field(
        default=None,
        description="Normativas éticas o institucionales obligatorias"
    )
    custom_instructions: Optional[str] = Field(
        default=None,
        description="Instrucciones adicionales particulares de la sesión"
    )

    # 3. Ranuras Modulares por Dominio (Slots extensibles para cualquier herramienta MCP)
    slots: Dict[str, Dict[str, Any]] = Field(
        default_factory=lambda: {
            "lms": {},     # course_id, assignment_id, rubric, scale
            "zoom": {},    # account_id, default_hosts, recording_rules
            "crm": {},     # customer_id, pipeline_stage, lead_status
            "custom": {}   # clave-valor extensible para futuras herramientas
        }
    )

    def is_expired(self) -> bool:
        return time.time() > (self.created_at + self.ttl_seconds)

    def get_slot(self, slot_name: str) -> Dict[str, Any]:
        """Obtiene la ranura específica de una herramienta sin generar error si no existe."""
        return self.slots.setdefault(slot_name, {})

    def set_slot(self, slot_name: str, data: Dict[str, Any], migrate: bool = True):
        """
        Actualiza o registra los datos de una herramienta en su ranura.

        Si `migrate=True` (default), los datos se normalizan contra el esquema
        tipado del slot (B1.1) conservando campos desconocidos (forward-compatible)
        — nunca pierde datos y nunca lanza por validación.
        """
        if migrate:
            data = migrate_slot_data(slot_name, data, from_version=self.schema_version)
        current = self.slots.setdefault(slot_name, {})
        current.update(data)

    def validate_slot(self, slot_name: str, data: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Valida los datos de una ranura contra su esquema tipado (B1.1)."""
        return validate_slot_data(slot_name, data)

    def migrate_all_slots(self, to_version: int = SCHEMA_VERSION) -> None:
        """
        Re-normaliza todas las ranuras conocidas al contrato actual, in situ.
        No destructivo: conserva campos desconocidos.
        """
        for name in list(self.slots.keys()):
            self.slots[name] = migrate_slot_data(
                name, self.slots[name], from_version=self.schema_version, to_version=to_version
            )
        self.schema_version = to_version

    def to_system_instruction(self, compact: bool = False) -> str:
        """
        Compila el contexto institucional en un bloque de instrucciones de alta densidad
        para inyectar en el System Prompt de cualquier LLM (Ollama, NVIDIA, DeepSeek, etc.).
        Cuando compact=True (por ejemplo en evaluación de ensayos), omite metadatos administrativos
        (course_id, assignment_id, account_id) y decoradores para maximizar el ahorro de tokens.
        """
        if compact:
            partes = [f"Directiva: {self.institution_id}"]
            if self.evaluation_scale:
                partes.append(f"Escala: 0 a {self.evaluation_scale}")
            if self.tone_policy:
                partes.append(f"Tono: {self.tone_policy}")
            if self.global_policy:
                partes.append(f"Política: {self.global_policy}")
            if self.custom_instructions:
                partes.append(f"Instrucción: {self.custom_instructions}")
            return f"[{' | '.join(partes)}]"

        lines = [
            f"--- DIRECTIVAS INSTITUCIONALES DEL HUB ---",
            f"Institución: {self.institution_id} | Facultad/Sede: {self.tenant_id or 'General'}",
            f"Sistema origen: {self.system_source} | Rol del actor: {self.user_role}",
            f"Tono requerido: {self.tone_policy}"
        ]

        if self.evaluation_scale:
            lines.append(f"Escala de evaluación institucional: 0 a {self.evaluation_scale}")

        if self.global_policy:
            lines.append(f"Política institucional obligatoria: {self.global_policy}")

        if self.custom_instructions:
            lines.append(f"Instrucciones particulares: {self.custom_instructions}")

        # Inyectar resúmenes de ranuras si tienen información relevante
        lms_slot = self.slots.get("lms", {})
        if lms_slot.get("course_id"):
            lines.append(f"Contexto Académico LMS: Curso {lms_slot.get('course_id')} - Asignación {lms_slot.get('assignment_id', 'N/A')}")

        zoom_slot = self.slots.get("zoom", {})
        if zoom_slot.get("account_id"):
            lines.append(f"Contexto Sesiones Zoom: Cuenta {zoom_slot.get('account_id')}")

        crm_slot = self.slots.get("crm", {})
        if crm_slot.get("customer_id"):
            lines.append(f"Contexto CRM: Cliente/Lead {crm_slot.get('customer_id')}")

        lines.append("--- FIN DIRECTIVAS INSTITUCIONALES ---")
        return "\n".join(lines)

    def compile_system_prompt(
        self,
        compact: bool = False,
        budget: Optional[int] = None,
    ) -> "SystemPrompt":
        """
        Compila el system prompt anteponiéndole un presupuesto de tokens (B1.3).

        Args:
            compact: Modo compacto (para evaluación de ensayos: omite metadatos
                     admin LMS y decoradores).
            budget:  Tope de tokens. None → usa el budget por defecto según modo.

        Returns:
            SystemPrompt(text, tokens, budget, within_budget).
        """
        text = self.to_system_instruction(compact=compact)
        tokens = estimate_tokens(text)
        if budget is None:
            budget = COMPACT_TOKEN_BUDGET if compact else FULL_TOKEN_BUDGET
        return SystemPrompt(text=text, tokens=tokens, budget=budget)


# ============================================================
# COMPILADOR DE PROMPT INSTITUCIONAL (FASE 1 · B1.3 `prompt-compiler`)
# ------------------------------------------------------------
# Presupuesto de tokens por modo + estimación. Reutilizable para cualquier
# herramienta que quiera inyectar la directiva de contexto al LLM.
# ============================================================

# Aproximación de tokens por carácter (inglés/español mixto).
_CHARS_PER_TOKEN = float(os.getenv("PROMPT_CHARS_PER_TOKEN", "4"))
# Presupuestos de tokens por modo (env-overridable para ajuste fino).
COMPACT_TOKEN_BUDGET = int(os.getenv("PROMPT_COMPACT_BUDGET", "64"))
FULL_TOKEN_BUDGET = int(os.getenv("PROMPT_FULL_BUDGET", "256"))


class SystemPrompt:
    """Resultado del compilador: texto + medición de tokens + presupuesto."""

    __slots__ = ("text", "tokens", "budget")

    def __init__(self, text: str, tokens: int, budget: int):
        self.text = text
        self.tokens = tokens
        self.budget = budget

    @property
    def within_budget(self) -> bool:
        return self.tokens <= self.budget

    def report(self) -> Dict[str, Any]:
        return {
            "tokens": self.tokens,
            "budget": self.budget,
            "within_budget": self.within_budget,
        }


def estimate_tokens(text: str) -> int:
    """
    Estima el número de tokens de un texto.

    Heurística ligera: `ceil(caracteres / _CHARS_PER_TOKEN)`. Suficiente para
    presupuestar prompts cortos de contexto; no sustituye a un tokenizador real
    (tiktoken), pero es determinista y barato en tiempo de ejecución.
    """
    if not text:
        return 0
    import math

    return max(1, math.ceil(len(text) / max(1.0, _CHARS_PER_TOKEN)))


def compile_system_prompt(
    context: Optional["InstitutionalContext"] = None,
    compact: bool = False,
    budget: Optional[int] = None,
) -> SystemPrompt:
    """
    Función módulo-nivel para compilar el prompt del contexto institucional.

    Args:
        context: Contexto activo. Si es None, usa `get_current_context()`.
        compact: Modo compacto (anti-desperdicio de tokens en evaluación).
        budget:  Tope de tokens por modo; None → default según `compact`.

    Returns:
        SystemPrompt compilado.
    """
    ctx = context or get_current_context()
    if ctx is None:
        base = ""
    else:
        base = ctx.to_system_instruction(compact=compact)
    tokens = estimate_tokens(base)
    if budget is None:
        budget = COMPACT_TOKEN_BUDGET if compact else FULL_TOKEN_BUDGET
    return SystemPrompt(text=base, tokens=tokens, budget=budget)


# ============================================================
# ALMACENAMIENTO DE CONTEXTOS
# ------------------------------------------------------------
# Interfaz en memoria (retro-compatible) con capa de persistencia durable
# opcional (FASE 1 · B1.2): SQLite / PostgreSQL vía `ContextBackend`.
#   - El punto caliente de lectura es en memoria (rápido).
#   - create()/save() hacen *write-through best-effort* al backend durable.
#   - load_from_backend() restaura contextos durables al arrancar.
# ============================================================

def _safe_backend_schedule(coro) -> None:
    """Ejecuta un coroutine de persistencia sin bloquear ni propagar errores.

    Si hay un event-loop en marcha (típico en FastAPI) programa la tarea en
    segundo plano; si no, ejecuta best-effort en bucle acoplado. Nunca lanza.
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # Tarea de fondo; errores se capturan dentro del coroutine.
        task = asyncio.ensure_future(coro)
        # Evitar "task was destroyed but it is pending" si el loop cierra.
        task.add_done_callback(lambda t: t.exception())
    else:
        # Sin loop en marcha: ejecutar síncrono best-effort con loop propio.
        try:
            asyncio.run(coro)
        except Exception:
            pass


def _run_async_sync(coro, timeout: float = 5.0) -> Any:
    """Ejecuta un coroutine y devuelve su resultado (para rutas de inicialización)."""
    import asyncio

    if asyncio.iscoroutine(coro):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    asyncio.run, coro
                ).result(timeout=timeout)
        return asyncio.run(coro)
    return coro


class ContextStore:
    """
    Almacén de contextos institucionales con TTL automático.

    - En memoria para lecturas rápidas (comportamiento original).
    - `backend`: ContextBackend opcional (SQLite/PostgreSQL/memoria) al que se
      replica cada alta/actualización para durabilidad (write-through best-effort).
    """

    def __init__(self, backend=None):
        self._contexts: Dict[str, InstitutionalContext] = {}
        self._backend = backend  # ContextBackend opcional (None → solo memoria)

    # -- backend durable ------------------------------------------------
    @property
    def backend(self):
        return self._backend

    def _to_record(self, ctx: InstitutionalContext) -> "ContextRecord":
        from API.core.context_backend import ContextRecord

        return ContextRecord(
            context_id=ctx.context_id,
            created_at=ctx.created_at,
            ttl_seconds=ctx.ttl_seconds,
            data=ctx.model_dump(mode="json"),
        )

    async def _persist_write(self, ctx: InstitutionalContext) -> None:
        if self._backend is None:
            return
        try:
            await self._backend.save(self._to_record(ctx))
        except Exception as exc:  # nunca romper el flujo por persistencia
            logger.warning("ContextStore: fallo de persistencia para %s: %s", ctx.context_id, exc)

    async def _persist_delete(self, context_id: str) -> None:
        if self._backend is None:
            return
        try:
            await self._backend.delete(context_id)
        except Exception as exc:
            logger.warning("ContextStore: fallo al borrar durable %s: %s", context_id, exc)

    def persist_now(self, ctx: "InstitutionalContext") -> None:
        """Write-through síncrono best-effort (bloquea sólo si no hay loop)."""
        _safe_backend_schedule(self._persist_write(ctx))

    async def load_from_backend(self) -> int:
        """
        Carga los contextos no expirados del backend durable a memoria.
        Devuelve el número de contextos restaurados. No-op sin backend.
        """
        if self._backend is None:
            return 0
        from API.core.context_backend import ContextBackend

        restored = 0
        try:
            for cid in await self._backend.list_ids():
                rec = await self._backend.get(cid)
                if rec is None:
                    continue
                try:
                    ctx = InstitutionalContext(**rec.data)
                except Exception:
                    ctx = InstitutionalContext.model_validate(rec.data)
                if ctx.is_expired():
                    continue
                self._contexts[ctx.context_id] = ctx
                restored += 1
        except Exception as exc:
            logger.warning("ContextStore: no se pudo cargar backend durable: %s", exc)
        logger.info("ContextStore: %s contexto(s) restaurado(s) desde backend durable.", restored)
        return restored

    # -- API original (retro-compatible) --------------------------------
    def create(self, **kwargs) -> InstitutionalContext:
        ctx = InstitutionalContext(**kwargs)
        self._contexts[ctx.context_id] = ctx
        self.persist_now(ctx)
        self._clean_expired()
        return ctx

    def get(self, context_id: str) -> Optional[InstitutionalContext]:
        self._clean_expired()
        ctx = self._contexts.get(context_id)
        if ctx and not ctx.is_expired():
            return ctx
        return None

    def save(self, ctx: InstitutionalContext):
        self._contexts[ctx.context_id] = ctx
        self.persist_now(ctx)

    def delete(self, context_id: str) -> bool:
        if context_id in self._contexts:
            del self._contexts[context_id]
            _safe_backend_schedule(self._persist_delete(context_id))
            return True
        return False

    def _clean_expired(self):
        now = time.time()
        expired = [cid for cid, c in self._contexts.items() if now > (c.created_at + c.ttl_seconds)]
        for cid in expired:
            del self._contexts[cid]


# Instancia singleton del almacén de contextos
context_store = ContextStore()


def init_durable_store(dsn: str = "", logger_override=None) -> "ContextStore":
    """
    Conecta el ContextStore singleton al backend durable (SQLite/PostgreSQL/memoria)
    y restaura los contextos no expirados.

    Args:
        dsn: DSN SQLAlchemy async (vacío/"memory" → memoria).
        logger_override: logger opcional para mensajes de degradación.

    Returns:
        El `context_store` singleton ya configurado.
    """
    global context_store
    from API.core.context_backend import make_context_backend

    backend = make_context_backend(dsn, logger=logger_override or logger)

    if backend is None or getattr(backend, "name", "memory") == "memory":
        backend = None  # sin backend durable: contexto solo en memoria

    context_store._backend = backend
    if backend is not None:
        _run_async_sync(context_store.load_from_backend())
    else:
        logger.info("ContextStore: sin backend durable (memoria transitoria).")

    return context_store


# ============================================================
# MIDDLEWARE FASTAPI PARA INYECCIÓN AUTOMÁTICA
# ============================================================
class InstitutionalContextMiddleware(BaseHTTPMiddleware):
    """
    Middleware que intercepta peticiones entrantes:
    1. Si trae 'X-Context-ID', recupera el contexto del ContextStore.
    2. Si trae 'X-Tenant-ID' o 'X-Institution-ID', crea un contexto efímero en vuelo.
    3. Establece la variable de contexto para que cualquier herramienta o LLM acceda transparentemente.
    4. Devuelve el 'X-Context-ID' en los headers de respuesta para trazabilidad.
    """
    async def dispatch(self, request: Request, call_next):
        context_id = request.headers.get("X-Context-ID") or request.query_params.get("context_id")
        tenant_id = request.headers.get("X-Tenant-ID")
        institution_id = request.headers.get("X-Institution-ID")
        user_role = request.headers.get("X-User-Role")
        system_source = request.headers.get("X-Source-System") or "HTTP_REQUEST"

        active_context: Optional[InstitutionalContext] = None

        if context_id:
            active_context = context_store.get(context_id)

        # Si no había un context_id registrado pero el cliente envía headers institucionales, crear uno efímero
        if not active_context and (tenant_id or institution_id or user_role):
            active_context = InstitutionalContext(
                institution_id=institution_id or "UNIVERSIDAD_INSTITUCIONAL",
                tenant_id=tenant_id or "GENERAL",
                user_role=user_role or "ESTUDIANTE",
                system_source=system_source,
                ttl_seconds=300  # 5 minutos para llamadas efímeras
            )
            context_store.save(active_context)

        # Inyectar en contextvars para el ciclo de vida de la petición
        token = _current_context_var.set(active_context)

        try:
            response: Response = await call_next(request)
            if active_context:
                response.headers["X-Context-ID"] = active_context.context_id
            return response
        finally:
            # Restaurar estado previo
            _current_context_var.reset(token)
