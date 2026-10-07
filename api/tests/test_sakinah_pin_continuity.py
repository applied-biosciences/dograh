"""Focused security tests for the v1.47.0.27 Sakinah read boundary."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.audio.dtmf.types import KeypadEntry
from pipecat.frames.frames import InputAudioRawFrame, InputDTMFFrame
from pipecat.processors.frame_processor import FrameDirection

from api.db.sakinah_identity_client import PinVerificationResult
from api.services.sakinah import pin_runtime
from api.services.sakinah.continuity import (
    bounded_continuity_prompt,
    build_bounded_continuity_context,
    is_continuity_workflow,
)
from api.services.sakinah.pin_security import hash_pin, verify_pin


def test_pin_is_bcrypt_hashed_and_verifies_without_plaintext():
    digest, salt = hash_pin("4827")
    assert digest != "4827"
    assert salt not in {"4827", digest}
    assert digest.startswith("$2")
    assert verify_pin("4827", digest)
    assert not verify_pin("1111", digest)


def test_continuity_workflow_name_normalization():
    assert is_continuity_workflow(" CALM  - inbound ")
    assert is_continuity_workflow("Sakinah   Decision Agent v2")
    assert not is_continuity_workflow("Sakinah Scenario Console")


def test_bounded_context_excludes_raw_transcripts_and_limits_calls():
    runs = [
        SimpleNamespace(
            id=index,
            started_at=datetime(2026, 1, index, tzinfo=UTC),
            full_transcript=f"caller: private transcript {index}",
            extra={},
        )
        for index in (3, 2, 1)
    ]
    context = build_bounded_continuity_context(
        runs,
        [
            {
                "memory_type": "personal_fact",
                "memory_text": "Caller likes gardening",
                "internal_context_allowed": True,
                "verbal_reference_allowed": True,
                "explicit_detail_allowed": False,
                "sensitivity": "normal",
            }
        ],
    )
    assert [item["workflow_run_id"] for item in context["previous_calls"]] == [3, 2]
    assert '"summary":"caller:' not in bounded_continuity_prompt(context)
    assert "Caller likes gardening" in bounded_continuity_prompt(context)
    assert len(bounded_continuity_prompt(context)) <= 8_600


def test_bounded_context_can_summarise_persisted_utterance_fallback():
    run = SimpleNamespace(id=7, started_at=None, full_transcript=None, extra={})
    utterance = SimpleNamespace(speaker="caller", transcript="Caller needs follow-up")
    context = build_bounded_continuity_context(
        [run], [], {7: [utterance]}
    )
    assert context["previous_calls"][0]["summary"] == "Caller needs follow-up"


@pytest.mark.asyncio
async def test_dtmf_is_consumed_before_stt_and_never_spoken_or_forwarded(monkeypatch):
    engine = SimpleNamespace(
        _workflow_run_id=99,
        _gathered_context={},
        update_sakinah_security_state=AsyncMock(),
        queue_sakinah_security_message=AsyncMock(),
    )
    runtime = pin_runtime.SakinahIdentityRuntime(
        engine=engine,
        organization_id=7,
        identity={
            "caller_status": "PIN_REQUIRED",
            "service_user_id": "service-user",
            "caller_identifier_id": "caller-id",
            "pin_required": True,
        },
    )
    monkeypatch.setattr(
        pin_runtime.db_client,
        "verify_sakinah_pin",
        AsyncMock(
            return_value=PinVerificationResult(True, "verified", True)
        ),
    )
    processor = pin_runtime.SakinahSecureInputProcessor(runtime)
    forwarded = []
    async def record_forwarded(frame, _direction):
        forwarded.append(frame)

    processor.push_frame = AsyncMock(side_effect=record_forwarded)

    await processor.process_frame(
        InputAudioRawFrame(audio=b"\x00" * 320, sample_rate=8_000, num_channels=1),
        FrameDirection.DOWNSTREAM,
    )
    for digit in "4827":
        await processor.process_frame(
            InputDTMFFrame(button=KeypadEntry(digit)), FrameDirection.DOWNSTREAM
        )

    pin_runtime.db_client.verify_sakinah_pin.assert_awaited_once_with(
        organization_id=7, service_user_id="service-user", pin="4827"
    )
    assert forwarded == []
    spoken = " ".join(call.args[0] for call in engine.queue_sakinah_security_message.await_args_list)
    assert "4827" not in spoken
    assert engine.update_sakinah_security_state.await_args.args[0][
        "identity_access_authorised"
    ] is True


@pytest.mark.asyncio
async def test_wrong_pin_fails_closed_and_allows_only_fresh_conversation(monkeypatch):
    engine = SimpleNamespace(
        _workflow_run_id=102,
        _gathered_context={},
        update_sakinah_security_state=AsyncMock(),
        queue_sakinah_security_message=AsyncMock(),
    )
    runtime = pin_runtime.SakinahIdentityRuntime(
        engine=engine,
        organization_id=7,
        identity={
            "caller_status": "PIN_REQUIRED",
            "service_user_id": "service-user",
            "pin_required": True,
        },
    )
    monkeypatch.setattr(
        pin_runtime.db_client,
        "verify_sakinah_pin",
        AsyncMock(return_value=PinVerificationResult(True, "failed", False)),
    )

    for digit in "1111":
        await runtime.handle_digit(digit)

    assert runtime.state == "PIN_REQUIRED"
    assert runtime.input_locked is False
    state = engine.update_sakinah_security_state.await_args.args[0]
    assert state["identity_access_authorised"] is False
    assert state["continuity_authorised"] is False
    spoken = " ".join(
        call.args[0] for call in engine.queue_sakinah_security_message.await_args_list
    )
    assert "previous conversation information" in spoken


@pytest.mark.asyncio
async def test_start_fresh_never_retrieves_history(monkeypatch):
    engine = SimpleNamespace(
        _workflow_run_id=100,
        _gathered_context={},
        update_sakinah_security_state=AsyncMock(),
        queue_sakinah_security_message=AsyncMock(),
    )
    runtime = pin_runtime.SakinahIdentityRuntime(
        engine=engine,
        organization_id=7,
        identity={"caller_status": "VERIFIED", "service_user_id": "service-user"},
    )
    runtime._choice_pending = True
    retrieve = AsyncMock()
    monkeypatch.setattr(pin_runtime, "retrieve_bounded_continuity", retrieve)

    await runtime.handle_user_text("start fresh")

    retrieve.assert_not_awaited()
    assert runtime.state == "CONTINUITY_DECLINED"
    assert engine.update_sakinah_security_state.await_args.kwargs["private_context"][
        "memory_context"
    ].startswith("No historic")


@pytest.mark.asyncio
async def test_continue_records_gate_and_retrieves_bounded_context(monkeypatch):
    engine = SimpleNamespace(
        _workflow_run_id=101,
        _gathered_context={},
        update_sakinah_security_state=AsyncMock(),
        queue_sakinah_security_message=AsyncMock(),
    )
    runtime = pin_runtime.SakinahIdentityRuntime(
        engine=engine,
        organization_id=7,
        identity={"caller_status": "VERIFIED", "service_user_id": "service-user"},
    )
    runtime._choice_pending = True
    monkeypatch.setattr(
        pin_runtime.db_client,
        "get_latest_privacy_permission",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        pin_runtime.db_client,
        "is_memory_permitted",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        pin_runtime.db_client,
        "record_privacy_permission",
        AsyncMock(),
    )
    retrieve = AsyncMock(
        return_value={
            "continuity_authorised": True,
            "continuity_available": True,
            "previous_calls": [{"workflow_run_id": 9, "summary": "A bounded summary"}],
            "durable_facts": [],
            "previous_plans": [],
        }
    )
    monkeypatch.setattr(pin_runtime, "retrieve_bounded_continuity", retrieve)

    await runtime.handle_user_text("continue")

    retrieve.assert_awaited_once_with(
        organization_id=7, service_user_id="service-user", current_run_id=101
    )
    assert runtime.state == "CONTINUITY_AUTHORISED"
    private = engine.update_sakinah_security_state.await_args.kwargs["private_context"]
    assert "<sakinah_continuity_context>" in private["memory_context"]
    assert "4827" not in private["memory_context"]


def test_memory_extraction_rejects_inferred_clinical_fact():
    from api.services.memory.extraction import _safe_proposal

    assert _safe_proposal(
        {
            "memory_type": "clinical_context",
            "memory_text": "Caller has a diagnosis",
            "caller_stated": False,
        }
    ) is None


def test_dtmf_log_redaction():
    from api.logging_config import redact_dtmf_log_message

    message = "DTMF event: {'digit': '4827', 'tone': '4'}"
    redacted = redact_dtmf_log_message(message)
    assert "4827" not in redacted
    assert "'digit': '[REDACTED]'" in redacted
