import asyncio
import json

import pytest

from API.core.context_backend import SQLAlchemyContextBackend
from API.core.context_backend import InMemoryContextBackend
from API.core.context_engine import (
    ContextStore,
    InstitutionalContext,
    context_store,
    get_current_context,
    init_durable_store,
    use_current_context,
)
from API.core.evaluation_queue import (
    EvaluationManager,
    InMemoryEvaluationBackend,
    SQLAlchemyEvaluationBackend,
)
from API.services import essay_evaluator_mcp as essay
from API.services import context_service


def _request(*, student_id, submission_id, course_id="C1", assignment_id="A1", institution_id="I1"):
    return essay.SolicitudEvaluacionDirecta(
        origen_ensayo=f"Ensayo de {student_id}",
        origen_rubrica="Criterio: claridad (10 puntos)",
        institution_id=institution_id,
        course_id=course_id,
        assignment_id=assignment_id,
        assignment_name="Ensayo argumentativo",
        assignment_description="Analiza las fuentes indicadas.",
        user_id_canvas=student_id,
        submission_id=submission_id,
        assignment={
            "id": assignment_id,
            "course_id": course_id,
            "name": "Ensayo argumentativo",
            "description": "Analiza las fuentes indicadas.",
            "points_possible": 10,
            "rubric": [{"id": "r1", "description": "Claridad", "points": 10}],
        },
    )


def test_assignment_context_is_shared_only_by_institution_course_and_assignment():
    async def scenario():
        store = ContextStore(backend=InMemoryContextBackend())
        first = await essay._prepare_assignment_context(
            _request(student_id="U1", submission_id="S1"), store=store
        )
        second = await essay._prepare_assignment_context(
            _request(student_id="U2", submission_id="S2"), store=store
        )
        other_course = await essay._prepare_assignment_context(
            _request(student_id="U3", submission_id="S3", course_id="C2"), store=store
        )
        other_assignment = await essay._prepare_assignment_context(
            _request(student_id="U4", submission_id="S4", assignment_id="A2"), store=store
        )
        other_institution = await essay._prepare_assignment_context(
            _request(student_id="U5", submission_id="S5", institution_id="I2"), store=store
        )

        assert first.context_id == second.context_id
        assert first.context_id != other_course.context_id
        assert first.context_id != other_assignment.context_id
        assert first.context_id != other_institution.context_id
        lms = first.get_slot("lms")
        assert lms["course_id"] == "C1"
        assert lms["assignment_id"] == "A1"
        assert lms["assignment_description"] == "Analiza las fuentes indicadas."
        assert lms["rubric"]
        assert lms["assignment_metadata"]["points_possible"] == 10
        assert first.evaluation_scale == 10
        assert all(
            lms.get(key) in (None, "")
            for key in ("user_id_canvas", "submission_id", "submission_type")
        )

    asyncio.run(scenario())


def test_lms_slot_supplies_shared_assignment_fields_to_minimal_canvas_payload():
    context = InstitutionalContext(
        institution_id="I1",
        evaluation_scale=10,
        slots={
            "lms": {
                "course_id": "C1",
                "assignment_id": "A1",
                "assignment_name": "Análisis FODA",
                "assignment_description": "Analiza la situación de una organización.",
                "rubric": [{"id": "r1", "description": "Análisis", "points": 10}],
            }
        },
    )
    payload = {
        "assignment": {
            "id": "A1",
            "courseId": "C1",
            "pointsPossible": 10,
        },
        "submission": {
            "id": "S1",
            "userId": "U1",
            "type": "online_text_entry",
            "body": "Texto del ensayo",
        },
    }

    with use_current_context(context):
        request = essay.SolicitudEvaluacionDirecta.model_validate(payload)

    assert request.origen_rubrica == context.get_slot("lms")["rubric"]
    assert request.assignment_description == context.get_slot("lms")["assignment_description"]
    assert request.assignment_name == "Análisis FODA"
    assert request.course_id == "C1"
    assert request.assignment_id == "A1"


def test_lms_context_init_uses_activity_identity_and_close_starts_grace(monkeypatch):
    async def scenario():
        store = ContextStore(backend=InMemoryContextBackend())
        monkeypatch.setattr(context_service, "context_store", store)
        created = await context_service.init_context(
            context_service.InitContextRequest(
                institution_id="I1",
                context_grace_days=2,
                slots={
                    "lms": {
                        "course_id": "C1",
                        "assignment_id": "A1",
                        "rubric": [{"id": "r1", "points": 10}],
                    }
                },
            )
        )
        assert created["lifecycle_type"] == "activity"
        assert created["lifecycle_status"] == "active"
        assert created["expires_in_seconds"] is None
        assert created["grace_period_days"] == 2

        ctx_id = created["context_id"]
        evaluation_context = await essay._prepare_assignment_context(
            _request(student_id="U1", submission_id="S1"),
            store=store,
        )
        assert evaluation_context.context_id == ctx_id

        closed = await context_service.close_context(
            ctx_id,
            context_service.CloseContextRequest(),
        )
        assert closed["lifecycle_status"] == "closed"
        assert closed["grace_period_days"] == 2
        assert closed["retention_until"] == closed["closed_at"] + 2 * 86400

    asyncio.run(scenario())


def test_lms_context_init_requires_a_persistence_backend(monkeypatch):
    async def scenario():
        monkeypatch.setattr(context_service, "context_store", ContextStore(backend=None))
        with pytest.raises(context_service.HTTPException) as error:
            await context_service.init_context(
                context_service.InitContextRequest(
                    institution_id="I1",
                    slots={"lms": {"course_id": "C1", "assignment_id": "A1"}},
                )
            )
        assert error.value.status_code == 503

    asyncio.run(scenario())


def test_lms_context_identity_matches_when_institution_is_omitted(monkeypatch):
    async def scenario():
        store = ContextStore(backend=InMemoryContextBackend())
        monkeypatch.setattr(context_service, "context_store", store)
        created = await context_service.init_context(
            context_service.InitContextRequest(
                slots={"lms": {"course_id": "C-default", "assignment_id": "A-default"}}
            )
        )
        request = _request(
            student_id="U1",
            submission_id="S1",
            institution_id="I1",
            course_id="C-default",
            assignment_id="A-default",
        ).model_copy(update={"institution_id": None})

        evaluation_context = await essay._prepare_assignment_context(request, store=store)
        assert evaluation_context.context_id == created["context_id"]
        assert evaluation_context.institution_id == "UNIVERSIDAD_TECNICA_PARTICULAR_DE_LOJA_UTPL"

    asyncio.run(scenario())


def test_client_context_read_hides_context_from_another_tenant(monkeypatch):
    from starlette.requests import Request
    from API.core.client_tokens import ApiClientPrincipal

    async def scenario():
        store = ContextStore(backend=InMemoryContextBackend())
        context = InstitutionalContext(
            context_id="ctx_tenant_a",
            institution_id="I1",
            tenant_id="tenant-a",
        )
        await store.save_durable(context)
        monkeypatch.setattr(context_service, "context_store", store)
        request = Request({"type": "http", "headers": [], "state": {}})
        request.state.api_client = ApiClientPrincipal(
            client_id="client-1",
            client_name="Tenant B",
            institution_id="I1",
            tenant_id="tenant-b",
            scopes=frozenset({"context:read"}),
        )

        with pytest.raises(context_service.HTTPException) as error:
            await context_service.get_context_by_id(context.context_id, request)

        assert error.value.status_code == 404

    asyncio.run(scenario())


def test_assignment_context_persists_and_reloads_from_sqlite(tmp_path):
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("aiosqlite")

    async def scenario():
        dsn = f"sqlite+aiosqlite:///{tmp_path.joinpath('contexts.db').as_posix()}"
        first_store = ContextStore(backend=SQLAlchemyContextBackend(dsn))
        ctx = await essay._prepare_assignment_context(
            _request(student_id="U1", submission_id="S1"), store=first_store
        )
        await first_store.backend.close()

        fresh_store = ContextStore(backend=SQLAlchemyContextBackend(dsn))
        restored = await fresh_store.get_or_restore(ctx.context_id)
        assert restored is not None
        assert restored.context_id == ctx.context_id
        assert restored.institution_id == "I1"
        assert restored.get_slot("lms")["assignment_description"] == "Analiza las fuentes indicadas."
        assert restored.get_slot("lms")["rubric"]
        await fresh_store.backend.close()

    asyncio.run(scenario())


def test_unusable_sqlalchemy_backend_degrades_to_memory():
    previous_backend = context_store.backend
    try:
        store = init_durable_store("sqlite+missingdriver:///:memory:")
        assert store.backend is None
        assert store.stats()["backend"] == "memory"
    finally:
        context_store._backend = previous_backend


def test_sqlite_queue_worker_persists_job_correlation(tmp_path):
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("aiosqlite")

    async def scenario():
        dsn = f"sqlite+aiosqlite:///{tmp_path.joinpath('queue.db').as_posix()}"

        async def evaluator(payload):
            return {"context_id": payload["context_id"], "status": "EVALUADO"}

        manager = EvaluationManager(
            backend=SQLAlchemyEvaluationBackend(dsn),
            evaluator=evaluator,
        )
        info = await manager.enqueue({
            "submission_id": "SQL-S1",
            "user_id_canvas": "SQL-U1",
            "course_id": "SQL-C1",
            "assignment_id": "SQL-A1",
            "context_id": "SQL-CTX1",
        })
        assert await manager.process_one()
        status, result = await manager.get_status(info["evaluation_id"])
        assert status == 200
        assert result["context_id"] == "SQL-CTX1"
        assert result["submission_id"] == "SQL-S1"
        assert result["user_id_canvas"] == "SQL-U1"
        await manager.backend.close()

        fresh_backend = SQLAlchemyEvaluationBackend(dsn)
        restored = await fresh_backend.get(info["evaluation_id"])
        assert restored is not None
        assert restored["result"]["assignment_id"] == "SQL-A1"
        await fresh_backend.close()

    asyncio.run(scenario())


def test_async_workers_rehydrate_shared_context_without_student_contamination(monkeypatch):
    async def scenario():
        store = ContextStore(backend=InMemoryContextBackend())
        monkeypatch.setattr(essay, "context_store", store)
        observed = []

        async def fake_pipeline(request, **kwargs):
            ctx = get_current_context()
            slot = dict(ctx.get_slot("lms"))
            observed.append((ctx.context_id, slot, request.user_id_canvas, request.submission_id))
            return {
                "status": "EVALUADO",
                "submission_id": request.submission_id,
                "user_id_canvas": request.user_id_canvas,
            }

        monkeypatch.setattr(essay, "_evaluar_flujo_completo_impl", fake_pipeline)
        manager = EvaluationManager(
            backend=InMemoryEvaluationBackend(),
            evaluator=essay._evaluar_async_pipeline,
        )
        monkeypatch.setattr(essay, "get_manager", lambda: manager)

        requests = [
            _request(student_id="U1", submission_id="S1"),
            _request(student_id="U2", submission_id="S2"),
        ]
        queued = []
        for request in requests:
            from starlette.requests import Request as StarletteRequest

            http_request = StarletteRequest({"type": "http", "headers": []})
            response = await essay.webhook_canvas_entrypoint(request, http_request)
            assert response.status_code == 202
            info = json.loads(response.body)
            assert info["status"] == "ENCOLADO"
            assert info["estado"] == "PENDING"
            assert info["evaluation_id"].startswith("eval_")
            assert info["context_id"]
            assert info["institution_id"] == "I1"
            assert info["submission_id"] == request.submission_id
            assert info["user_id_canvas"] == request.user_id_canvas
            assert info["course_id"] == "C1"
            assert info["assignment_id"] == "A1"
            queued.append(info)
            stats = await manager.stats()
            assert stats["total"] == len(queued)
            assert observed == []

            _, pending = await manager.get_status(info["evaluation_id"])
            assert pending["context_id"] == info["context_id"]
            assert pending["course_id"] == "C1"
            assert pending["assignment_id"] == "A1"

        assert await manager.process_one()
        assert await manager.process_one()
        _, first = await manager.get_status(queued[0]["evaluation_id"])
        _, second = await manager.get_status(queued[1]["evaluation_id"])

        assert first["context_id"] == second["context_id"]
        assert first["context_id"] == observed[0][0] == observed[1][0]
        assert first["submission_id"] == "S1" and first["user_id_canvas"] == "U1"
        assert second["submission_id"] == "S2" and second["user_id_canvas"] == "U2"
        for _, slot, _, _ in observed:
            assert slot["course_id"] == "C1"
            assert slot["assignment_id"] == "A1"
            assert slot.get("submission_id") is None
            assert slot.get("user_id_canvas") is None

    asyncio.run(scenario())
