"""Memory-saving policy tests; no database or model provider is contacted."""

import importlib.util
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from api import constants
from api.db import call_persistence_client
from api.services import call_persistence
from api.services.memory import extraction
from api.tasks.function_names import FunctionNames


@pytest.mark.parametrize(
    "value, expected",
    [(True, True), (False, False), (" YES ", True), ("true", True),
     ("1", True), (" No ", False), ("FALSE", False), ("0", False),
     (None, None), ("", None), ("maybe", None), (1, None), (0, None), ({}, None)],
)
def test_consent_values(value, expected):
    assert extraction._as_bool(value) is expected


@pytest.mark.parametrize("setting", [None, "", "Sakinah Decision Agent v2, CALM  - inbound, , Sakinah Scenario Console"])
def test_workflow_setting_normalizes_names_without_changing_default(monkeypatch, setting):
    if setting is None:
        monkeypatch.delenv("MEMORY_WORKFLOW_NAMES", raising=False)
    else:
        monkeypatch.setenv("MEMORY_WORKFLOW_NAMES", setting)
    monkeypatch.delenv("MEMORY_REQUIRE_EXPLICIT_CONSENT", raising=False)
    spec = importlib.util.spec_from_file_location("memory_test_constants", constants.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.MEMORY_REQUIRE_EXPLICIT_CONSENT is False
    expected = (
        {
            "sakinah decision agent v2",
            "calm - inbound",
            "sakinah scenario console",
            "sakinah decision agent v3.1",
            "sakinah decision agent v4.1",
        }
        if setting is None
        else {
            " ".join(name.split()).lower()
            for name in setting.split(",")
            if name.strip()
        }
    )
    assert module.MEMORY_WORKFLOW_NAMES == expected
    assert module.memory_workflow_allowed("  SAKINAH\tScenario   Console ") is ("sakinah scenario console" in expected)
    assert module.memory_workflow_allowed(" CALM  - inbound ") is ("calm - inbound" in expected)
    assert module.memory_workflow_allowed(" Sakinah Decision Agent v3.1 ") is ("sakinah decision agent v3.1" in expected)
    assert module.memory_workflow_allowed(" Sakinah Decision Agent v4.1 ") is ("sakinah decision agent v4.1" in expected)
    assert not module.memory_workflow_allowed("Other workflow")
    assert not module.memory_workflow_allowed(None)
    assert not module.memory_workflow_allowed("  ")


@pytest.fixture
def saving(monkeypatch):
    run = SimpleNamespace(
        id=44, service_user_id="test-service-user", caller_state="FIRST_TIME",
        workflow=SimpleNamespace(name="Sakinah Scenario Console", organization_id=7),
        gathered_context={}, full_transcript="USER: I enjoy gardening.", logs={}, extra={},
    )
    user = SimpleNamespace(memory_enabled=True, status="active")
    state = SimpleNamespace(permission=None)
    events = []
    session = AsyncMock()
    session.get.return_value = user

    async def select_permission(_query):
        result = Mock()
        result.scalars.return_value.first.return_value = state.permission
        return result

    session.execute.side_effect = select_permission
    context = AsyncMock()
    context.__aenter__.return_value = session
    client = object.__new__(call_persistence_client.CallPersistenceClient)
    client.async_session = Mock(return_value=context)

    async def record_permission(**kwargs):
        events.append("consent")
        state.permission = SimpleNamespace(granted=kwargs["granted"])

    async def permitted(*args, **kwargs):
        events.append("permission")
        return await client.is_memory_permitted(*args, **kwargs)

    async def opt_out(**_kwargs):
        events.append("opt_out")
        user.memory_enabled = False

    db = SimpleNamespace(
        get_workflow_run_by_id=AsyncMock(return_value=run),
        record_privacy_permission=AsyncMock(side_effect=record_permission),
        update_workflow_run=AsyncMock(),
        is_memory_permitted=AsyncMock(side_effect=permitted),
        get_utterances_for_run=AsyncMock(return_value=[]),
        record_memory_opt_out=AsyncMock(side_effect=opt_out),
        create_or_confirm_memory=AsyncMock(),
        persist_call_snapshot=AsyncMock(),
    )
    llm = SimpleNamespace(run_inference=AsyncMock(return_value=(
        '{"memory_storage_allowed":true,"call_summary":"We talked about gardening.","memories":['
        '{"memory_type":"preference","memory_text":"Enjoys gardening"}]}'
    )))
    monkeypatch.setattr(extraction, "MEMORY_ENABLED", True)
    monkeypatch.setattr(constants, "MEMORY_WORKFLOW_NAMES", {
        "sakinah scenario console", "sakinah decision agent v2", "calm - inbound",
        "sakinah decision agent v3.1", "sakinah decision agent v4.1",
    })
    monkeypatch.setattr(call_persistence_client, "MEMORY_REQUIRE_EXPLICIT_CONSENT", False)
    monkeypatch.setattr(extraction, "db_client", db)
    monkeypatch.setattr(extraction, "get_resolved_ai_model_configuration", AsyncMock(return_value=SimpleNamespace(effective=SimpleNamespace(llm=True))))
    monkeypatch.setattr(extraction, "create_llm_service", Mock(return_value=llm))
    monkeypatch.setattr(extraction, "_embed_memories", AsyncMock(return_value=[None]))
    return SimpleNamespace(run=run, user=user, state=state, db=db, llm=llm, events=events, session=session)


@pytest.mark.asyncio
@pytest.mark.parametrize("name, expected", [
    ("Sakinah Scenario Console", 1), ("Sakinah Decision Agent v2", 1),
    ("Sakinah Decision Agent v3.1", 1), ("Sakinah Decision Agent v4.1", 1),
    ("CALM  - inbound", 1), ("  calm\t- INBOUND ", 1),
    ("Unrelated agent", 0), (None, 0), ("", 0),
])
async def test_workflow_eligibility(saving, name, expected):
    saving.run.workflow.name = name
    assert await extraction.extract_and_store_memories(44) == expected
    assert saving.db.create_or_confirm_memory.await_count == expected
    if not expected:
        saving.db.is_memory_permitted.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("consent, existing, required, expected", [
    (True, None, False, 1), (False, None, False, 0),
    ("yes", None, False, 1), ("no", None, False, 0),
    (None, None, False, 1), ("maybe", None, False, 1),
    (None, False, False, 0), ("maybe", False, False, 0),
    (None, None, True, 0), (True, None, True, 1),
    (False, True, False, 0), (None, True, True, 1),
])
async def test_consent_and_existing_permission(saving, monkeypatch, consent, existing, required, expected):
    if consent is not None:
        saving.run.gathered_context["memory_consent"] = consent
    if existing is not None:
        saving.state.permission = SimpleNamespace(granted=existing)
    monkeypatch.setattr(call_persistence_client, "MEMORY_REQUIRE_EXPLICIT_CONSENT", required)
    assert await extraction.extract_and_store_memories(44) == expected
    assert saving.db.create_or_confirm_memory.await_count == expected
    if extraction._as_bool(consent) is None:
        saving.db.record_privacy_permission.assert_not_awaited()
        assert saving.events == ["permission"]
    elif extraction._as_bool(consent) is False:
        saving.db.record_privacy_permission.assert_not_awaited()
        assert saving.events == []
    else:
        assert saving.events == ["consent", "consent", "permission"]
        assert saving.db.record_privacy_permission.await_count == 2
        assert [
            call.kwargs["permission_type"]
            for call in saving.db.record_privacy_permission.await_args_list
        ] == ["memory_storage", "memory_use"]
        saving.db.record_privacy_permission.assert_any_await(
            organization_id=7, service_user_id="test-service-user",
            permission_type="memory_storage", granted=True,
            verification_level="none", source_workflow_run_id=44,
        )
    if not expected:
        saving.llm.run_inference.assert_not_awaited()


@pytest.mark.asyncio
async def test_verified_consent_provenance(saving):
    saving.run.caller_state = "VERIFIED"
    saving.run.gathered_context = {"memory_consent": True}
    assert await extraction.extract_and_store_memories(44) == 1
    assert saving.db.record_privacy_permission.call_args.kwargs["verification_level"] == "verified"


@pytest.mark.asyncio
async def test_ai_call_summary_is_saved_without_raw_transcript_fallback(saving):
    saving.run.extra = {"existing": "value"}

    assert await extraction.extract_and_store_memories(44) == 1

    saving.db.update_workflow_run.assert_awaited_once_with(
        44,
        extra={
            "continuity_summary": {
                "summary": "We talked about gardening.",
                "source_workflow_run_id": 44,
            }
        },
    )


@pytest.mark.asyncio
async def test_declined_call_is_not_sent_for_memory_extraction(saving):
    saving.run.gathered_context = {"memory_consent": False}

    assert await extraction.extract_and_store_memories(44) == 0

    saving.db.record_privacy_permission.assert_not_awaited()
    saving.db.is_memory_permitted.assert_not_awaited()
    saving.llm.run_inference.assert_not_awaited()
    saving.db.update_workflow_run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", ["disabled", "inactive", "missing_user", "missing_identity", "missing_workflow", "missing_run", "global_disabled", "unverified_existing"])
async def test_existing_protections_prevent_saving(saving, monkeypatch, guard):
    saving.run.gathered_context = {"memory_consent": True}
    if guard == "disabled":
        saving.user.memory_enabled = False
    elif guard == "inactive":
        saving.user.status = "deleted"
    elif guard == "missing_user":
        saving.session.get.return_value = None
    elif guard == "missing_identity":
        saving.run.service_user_id = None
    elif guard == "missing_workflow":
        saving.run.workflow = None
    elif guard == "missing_run":
        saving.db.get_workflow_run_by_id.return_value = None
    elif guard == "global_disabled":
        monkeypatch.setattr(extraction, "MEMORY_ENABLED", False)
    else:
        saving.run.gathered_context["profile_binding_status"] = "unverified_existing"
    assert await extraction.extract_and_store_memories(44) == 0
    saving.llm.run_inference.assert_not_awaited()
    saving.db.create_or_confirm_memory.assert_not_awaited()
    if guard not in {"disabled", "inactive", "missing_user"}:
        saving.db.record_privacy_permission.assert_not_awaited()
        saving.db.is_memory_permitted.assert_not_awaited()


@pytest.mark.asyncio
async def test_consent_persistence_failure_stops_permission_and_extraction(saving, monkeypatch):
    saving.run.gathered_context = {"memory_consent": True}
    saving.db.record_privacy_permission.side_effect = RuntimeError("private caller detail")
    logger = Mock()
    monkeypatch.setattr(extraction, "logger", logger)
    assert await extraction.extract_and_store_memories(44) == 0
    saving.db.is_memory_permitted.assert_not_awaited()
    saving.db.create_or_confirm_memory.assert_not_awaited()
    saving.llm.run_inference.assert_not_awaited()
    logger.warning.assert_called_once_with("Memory consent could not be recorded")


@pytest.mark.asyncio
@pytest.mark.parametrize("consent", [None, False, True])
async def test_mid_call_refusal_overrides_consent_before_saving(saving, consent):
    saving.run.gathered_context = {"memory_consent": consent}
    saving.run.full_transcript = "USER: I enjoy gardening.\nUSER: Please don’t remember this.\nASSISTANT: Understood."
    assert await extraction.extract_and_store_memories(44) == 0
    saving.db.record_memory_opt_out.assert_awaited_once()
    saving.llm.run_inference.assert_not_awaited()
    saving.db.create_or_confirm_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_classified_refusal_prevents_saving(saving):
    saving.llm.run_inference.return_value = '{"memory_storage_allowed":false,"memories":[]}'
    assert await extraction.extract_and_store_memories(44) == 0
    saving.db.record_memory_opt_out.assert_awaited_once()
    saving.db.create_or_confirm_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_hangup_snapshot_queues_extraction_and_saves_without_answer(saving, monkeypatch):
    saving.run.gathered_context = {"call_status": "user_hangup"}
    monkeypatch.setattr(call_persistence, "db_client", saving.db)
    enqueue = AsyncMock()
    monkeypatch.setattr(call_persistence, "_enqueue_job", enqueue)
    await call_persistence.persist_workflow_run_call_data(None, 44)
    saving.db.persist_call_snapshot.assert_awaited_once_with(
        44, events=None, transcript_text=saving.run.full_transcript,
    )
    enqueue.assert_awaited_once_with(FunctionNames.EXTRACT_CALL_MEMORIES, 44)
    await call_persistence.extract_workflow_run_memories(None, 44)
    saving.db.record_privacy_permission.assert_not_awaited()
    saving.db.create_or_confirm_memory.assert_awaited_once()
