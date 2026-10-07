from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.db.call_persistence_client import CallerIdentityResolution
from api.services.pipecat.realtime_feedback_events import (
    build_sakinah_continuity_action_event,
)
from api.services.sakinah import continuity
from api.services.sakinah.pin_runtime import SakinahIdentityRuntime
from api.utils.transcript import generate_transcript_text


def _identity_db(*, created: bool):
    class IdentityDB:
        async def resolve_caller_identity(self, *_args, **_kwargs):
            return CallerIdentityResolution(
                service_user=SimpleNamespace(id="service-user-secret"),
                caller_identifier=SimpleNamespace(id="caller-id-secret"),
                created=created,
            )

        async def has_active_sakinah_pin(self, **_kwargs):
            return created is False

    return IdentityDB()


@pytest.mark.asyncio
async def test_lookup_event_state_is_deterministic_and_safe(monkeypatch):
    monkeypatch.setattr(continuity, "db_client", _identity_db(created=False))
    result = await continuity.prepare_sakinah_identity(
        organization_id=7,
        call_context={"caller_number": "+441234567890"},
    )

    assert result["caller_result"] == "returning"
    assert result["lookup_status"] == "success"
    assert result["session_access_mode"] == "returning_profile"
    assert "+441234567890" not in str(result)
    assert "service-user-secret" in result["service_user_id"]


@pytest.mark.asyncio
async def test_new_and_unknown_lookup_do_not_query_continuity(monkeypatch):
    new_db = _identity_db(created=True)
    monkeypatch.setattr(continuity, "db_client", new_db)
    new_result = await continuity.prepare_sakinah_identity(
        organization_id=7,
        call_context={"caller_number": "+441234567890"},
    )
    assert new_result["caller_result"] == "new"
    assert new_result["continuity_available"] is False

    unknown_db = AsyncMock()
    monkeypatch.setattr(continuity, "db_client", unknown_db)
    unknown_result = await continuity.prepare_sakinah_identity(
        organization_id=7,
        call_context={"caller_number": "not-a-number"},
    )
    assert unknown_result["caller_result"] == "unknown"
    assert unknown_result["lookup_status"] == "success" or unknown_result["lookup_status"] == "unavailable"
    unknown_db.resolve_caller_identity.assert_not_awaited()


def _runtime(monkeypatch, events):
    engine = SimpleNamespace(
        _workflow_run_id=101,
        _gathered_context={},
        update_sakinah_security_state=AsyncMock(),
        queue_sakinah_security_message=AsyncMock(),
    )
    monkeypatch.setattr(
        "api.services.sakinah.pin_runtime.db_client.update_workflow_run",
        AsyncMock(),
    )
    async def emit_action_event(**event):
        events.append(event)

    runtime = SakinahIdentityRuntime(
        engine=engine,
        organization_id=7,
        identity={"caller_status": "VERIFIED", "service_user_id": "service-user"},
        emit_action_event=emit_action_event,
    )
    runtime._choice_pending = True
    return runtime, engine


@pytest.mark.asyncio
async def test_new_choice_bypasses_retrieval_and_injection(monkeypatch):
    events = []
    runtime, _engine = _runtime(monkeypatch, events)
    retrieve = AsyncMock()
    monkeypatch.setattr("api.services.sakinah.pin_runtime.retrieve_bounded_continuity", retrieve)

    await runtime.handle_user_text("start new")

    retrieve.assert_not_awaited()
    assert [event["action"] for event in events] == [
        "CONTINUITY_CHOICE",
        "CONTINUITY_RETRIEVAL",
        "CONTINUITY_CONTEXT_INJECTED",
    ]
    assert events[0]["details"]["choice"] == "new"
    assert events[1]["status"] == "bypassed"
    assert events[2]["details"]["injection_status"] == "bypassed"


@pytest.mark.asyncio
async def test_continue_reports_bounded_retrieval_and_raw_transcript_guard(monkeypatch):
    events = []
    runtime, engine = _runtime(monkeypatch, events)
    db = __import__("api.services.sakinah.pin_runtime", fromlist=["db_client"]).db_client
    monkeypatch.setattr(db, "get_latest_privacy_permission", AsyncMock(return_value=None))
    monkeypatch.setattr(db, "is_memory_permitted", AsyncMock(return_value=True))
    monkeypatch.setattr(db, "record_privacy_permission", AsyncMock())
    monkeypatch.setattr(
        "api.services.sakinah.pin_runtime.retrieve_bounded_continuity",
        AsyncMock(
            return_value={
                "continuity_available": True,
                "retrieval_status": "available",
                "previous_calls_requested": 2,
                "previous_calls_loaded": 1,
                "durable_facts_requested": True,
                "durable_facts_loaded": 2,
                "raw_transcripts_loaded": False,
                "raw_transcripts_injected": False,
                "storage_source": "backend_continuity_service",
                "bounded_context_ref": "continuity:abc",
                "previous_calls": [
                    {
                        "workflow_run_id": 9,
                        "started_at": datetime.now(UTC).isoformat(),
                        "summary": "bounded summary",
                    }
                ],
                "durable_facts": [
                    {"memory_type": "coping_preference"},
                    {"memory_type": "follow_up_item"},
                ],
                "previous_plans": [],
            }
        ),
    )

    await runtime.handle_user_text("continue")

    assert [event["action"] for event in events] == [
        "CONTINUITY_CHOICE",
        "CONTINUITY_RETRIEVAL",
        "CONTINUITY_CONTEXT_INJECTED",
    ]
    retrieval = events[1]
    assert retrieval["status"] == "success"
    assert retrieval["details"]["previous_calls_loaded"] == 1
    assert retrieval["details"]["durable_facts_loaded"] == 2
    assert retrieval["details"]["raw_transcripts_loaded"] is False
    assert retrieval["details"]["raw_transcripts_injected"] is False
    assert engine.update_sakinah_security_state.await_args.args[0][
        "raw_transcripts_injected"
    ] is False


@pytest.mark.asyncio
async def test_retrieval_error_is_visible_but_support_continues(monkeypatch):
    events = []
    runtime, _engine = _runtime(monkeypatch, events)
    db = __import__("api.services.sakinah.pin_runtime", fromlist=["db_client"]).db_client
    monkeypatch.setattr(db, "get_latest_privacy_permission", AsyncMock(return_value=None))
    monkeypatch.setattr(db, "is_memory_permitted", AsyncMock(return_value=True))
    monkeypatch.setattr(db, "record_privacy_permission", AsyncMock())
    monkeypatch.setattr(
        "api.services.sakinah.pin_runtime.retrieve_bounded_continuity",
        AsyncMock(side_effect=RuntimeError("storage unavailable")),
    )

    # The runtime boundary converts backend errors into an error action and
    # leaves the conversational path available to continue.
    await runtime.handle_user_text("continue")

    assert events[1]["action"] == "CONTINUITY_RETRIEVAL"
    assert events[1]["status"] == "error"
    assert events[2]["action"] == "CONTINUITY_CONTEXT_INJECTED"
    assert events[2]["details"]["injection_status"] == "empty"


@pytest.mark.asyncio
async def test_safety_signal_bypasses_continuity_without_querying_history(monkeypatch):
    events = []
    runtime, engine = _runtime(monkeypatch, events)
    retrieve = AsyncMock()
    monkeypatch.setattr(
        "api.services.sakinah.pin_runtime.retrieve_bounded_continuity", retrieve
    )

    await runtime.handle_user_text("I want to die")

    retrieve.assert_not_awaited()
    assert events[0]["action"] == "CONTINUITY_CHOICE"
    assert events[0]["status"] == "bypassed"
    assert events[0]["details"]["choice"] == "unclear"
    assert engine.update_sakinah_security_state.await_args.args[0][
        "safety_precedence"
    ] is True


def test_action_event_uses_existing_trace_envelope_and_export_box():
    event = build_sakinah_continuity_action_event(
        action="CONTINUITY_RETRIEVAL",
        call_id="run:101",
        turn_id=3,
        status="success",
        details={
            "retrieval_status": "available",
            "previous_calls_loaded": 1,
            "durable_facts_loaded": 2,
            "raw_transcripts_injected": False,
        },
    )

    assert event["type"] == "rtf-sakinah-continuity-action"
    assert event["payload"]["event_type"] == "sakinah.continuity.action"
    assert event["payload"]["display"]["visibility"] == "internal"
    retry = build_sakinah_continuity_action_event(
        action="CONTINUITY_RETRIEVAL",
        call_id="run:101",
        turn_id=3,
        status="success",
        details=event["payload"]["details"],
        timestamp=event["timestamp"],
    )
    assert retry["payload"]["action_event_id"] == event["payload"]["action_event_id"]
    exported = generate_transcript_text([event])
    assert "INTERNAL ACTION" in exported
    assert "Raw transcripts injected: no" in exported
