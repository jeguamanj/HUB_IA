# API/core/context_engine.py

import os
import time
import uuid
import hashlib
import json
import contextvars
from contextlib import contextmanager
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


# Shared identity fallback used by LMS context initialization and evaluation.
DEFAULT_LMS_INSTITUTION_ID = "UNIVERSIDAD_TECNICA_PARTICULAR_DE_LOJA_UTPL"


def lms_activity_context_id(institution_id: str, course_id: str, assignment_id: str) -> str:
    identity = json.dumps(
        [institution_id, course_id, assignment_id],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"ctx_lms_{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


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


@contextmanager
def use_current_context(ctx: Optional['InstitutionalContext']):
    """Temporarily bind a context to the current async task and restore it afterward."""
    token = _current_context_var.set(ctx)
    try:
        yield
    finally:
        _current_context_var.reset(token)


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
    lifecycle_type: str = Field(default="session")
    lifecycle_status: str = Field(default="active")
    grace_period_days: int = Field(default=3, ge=0, le=365)
    closed_at: Optional[float] = None
    retention_until: Optional[float] = None

    # Versión del contrato de slots (FASE 1 · B1.1 `context-contract`).
    # Contextos persistidos con anterioridad no traen el campo → `ensure_schema`
    # lo rellena con SCHEMA_VERSION al ser cargados.
    schema_version: int = Field(default=SCHEMA_VERSION, description="Versión del contrato de los slots")

    # 1. Identidad y Gobernanza Institucional (Global)
    institution_id: str = Field(default="UNIVERSIDAD TECNICA_PARTICULAR DE LOJA - UTPL", description="UTPL")
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
        if self.lifecycle_type == "activity":
            if self.lifecycle_status != "closed" or self.retention_until is None:
                return False
            return time.time() > self.retention_until
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

    def to_system_instruction(
        self,
        compact: bool = False,
        budget_tokens: Optional[int] = None,
        language: Optional[str] = None,
    ) -> str:
        """
        Compila el contexto institucional en un bloque de instrucciones de alta densidad
        para inyectar en el System Prompt de cualquier LLM (Ollama, NVIDIA, DeepSeek, etc.).

        Modos de operación (granularidad B1.3):

        1. `budget_tokens` (int) → Compresión por PRESUPUESTO. Delega en el compilador
           avanzado (`compile_context`), que estima tokens y comprime de forma
           inteligente (prioriza campos críticos, recorta slots, conserva entidades)
           GARANTIZANDO que el resultado quepa en la ventana de contexto dada.
           Es la vía recomendada: no es un binario, sino un tope medible de tokens.

        2. `compact=True/False` (modo legacy) → Comportamiento original conservado
           para no romper llamadas existentes. Sigue siendo binario, pero se
           mantiene por compatibilidad.

        Args:
            compact: Modo compacto legacy (omite metadatos administrativos y decoradores).
            budget_tokens: Tope máximo de tokens. Si se indica, se usa el compilador
                           avanzado para respetarlo (ignora compact).
            language: Hint de idioma (es/en...) para la heurística de tokens.

        Returns:
            str con el bloque de instrucciones optimizado.
        """
        # Vía avanzada (recomendada): presupuesto medible de tokens.
        if budget_tokens is not None:
            return self.compile_context(budget=budget_tokens, language=language).text

        # Vía legacy (retro-compatible): binaria.
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

    # ------------------------------------------------------------------
    # Compilación avanzada con presupuesto de tokens (B1.3 prompt-compiler)
    # ------------------------------------------------------------------
    def compile_context(
        self,
        budget: Optional[int] = None,
        language: Optional[str] = None,
    ) -> "CompiledContext":
        """
        Compila el contexto usando el compilador avanzado (comprime si excede
        el presupuesto de tokens y genera metadatos de auditoría).

        Args:
            budget: presupuesto máximo de tokens del prompt de sistema.
            language: hint de idioma (es/en/...).

        Returns:
            CompiledContext(text, report, compact).
        """
        from API.core.prompt_compiler import (
            build_budget_report,
            compile_context_prompt,
        )

        # serializar el contexto a dict (model_dump) para el compilador
        ctx_dict = self.model_dump(mode="json")

        if budget is None:
            budget = int(os.getenv("PROMPT_SYSTEM_BUDGET", "1024"))

        report = build_budget_report(ctx_dict, budget=budget, language=language)
        text = compile_context_prompt(ctx_dict, budget=budget, language=language)

        return CompiledContext(text=text, report=report, compact=False)


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


class CompiledContext:
    """
    Resultado de la compilación avanzada de contexto (B1.3 prompt-compiler).
    Incluye metadatos de auditoría de presupuesto.
    """

    __slots__ = ("text", "report", "compact")

    def __init__(self, text: str, report: Any, compact: bool):
        self.text = text
        self.report = report      # ContextBudgetReport
        self.compact = compact

    @property
    def tokens(self) -> int:
        return self.report.output_tokens

    @property
    def within_budget(self) -> bool:
        return self.report.utilization <= 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "report": self.report.to_dict(),
            "compact": self.compact,
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

    async def save_durable(self, ctx: InstitutionalContext) -> None:
        """Update the in-memory cache and require a successful durable write."""
        self._contexts[ctx.context_id] = ctx
        if self._backend is not None:
            await self._backend.save(self._to_record(ctx))

    async def get_or_restore(self, context_id: str) -> Optional[InstitutionalContext]:
        """Return a cached context or restore it from the durable backend."""
        cached = self.get(context_id)
        if cached is not None:
            return cached
        if self._backend is None:
            return None

        record = await self._backend.get(context_id)
        if record is None:
            return None
        ctx = InstitutionalContext.model_validate(record.data)
        if ctx.is_expired():
            await self._backend.delete(context_id)
            return None
        self._contexts[context_id] = ctx
        return ctx

    async def close_activity(
        self,
        context_id: str,
        grace_period_days: Optional[int] = None,
    ) -> Optional[InstitutionalContext]:
        """Close an LMS activity context and start its post-close retention window."""
        ctx = await self.get_or_restore(context_id)
        if ctx is None:
            return None
        if ctx.lifecycle_type != "activity":
            raise ValueError("Solo se pueden cerrar contextos asociados a actividades LMS.")
        if grace_period_days is not None:
            if not 0 <= grace_period_days <= 365:
                raise ValueError("grace_period_days debe estar entre 0 y 365.")
            ctx.grace_period_days = grace_period_days
        if ctx.lifecycle_status != "closed":
            ctx.lifecycle_status = "closed"
            ctx.closed_at = time.time()
            ctx.retention_until = ctx.closed_at + ctx.grace_period_days * 86400
        await self.save_durable(ctx)
        return ctx

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

    def stats(self) -> Dict[str, Any]:
        self._clean_expired()
        return {
            "total": len(self._contexts),
            "backend": getattr(self._backend, "name", "memory"),
        }

    async def close(self) -> None:
        """Close the durable backend at application shutdown."""
        backend = self._backend
        self._backend = None
        if backend is not None:
            await backend.close()

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
        try:
            # Force connection/schema errors to surface during startup rather
            # than leaving a backend selected that cannot persist contexts.
            _run_async_sync(backend.list_ids())
            _run_async_sync(context_store.load_from_backend())
        except Exception as exc:
            logger.warning("ContextStore: backend durable no disponible: %s", exc)
            _run_async_sync(backend.close())
            context_store._backend = None
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
            active_context = await context_store.get_or_restore(context_id)

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
