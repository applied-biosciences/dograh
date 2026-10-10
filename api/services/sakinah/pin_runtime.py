"""DTMF-only Sakinah PIN runtime boundary.

The processors deliberately consume keypad and PIN-entry audio before STT and
never forward a digit frame to the normal conversation pipeline.
"""

from __future__ import annotations

import hmac
import re
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    InputDTMFFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from api.constants import SAKINAH_PIN_LENGTH
from api.db import db_client
from api.services.sakinah.calm.safety import score_safety
from api.services.sakinah.continuity import (
    bounded_continuity_prompt,
    internal_reference,
    retrieve_bounded_continuity,
)


class SakinahIdentityRuntime:
    """Own the in-call state machine and the ephemeral PIN buffer."""

    def __init__(
        self,
        *,
        engine: Any,
        organization_id: int,
        identity: dict[str, Any],
        emit_action_event: Callable[..., Awaitable[None]] | None = None,
    ):
        self.engine = engine
        self.organization_id = organization_id
        self.service_user_id = identity.get("service_user_id")
        self.caller_identifier_id = identity.get("caller_identifier_id")
        self.state = identity.get("caller_status", "UNKNOWN")
        self.mode = "PIN_REQUIRED" if identity.get("pin_required") else "NONE"
        self._digits = ""
        self._registration_pin: str | None = None
        self._choice_pending = False
        self._assistant_text = ""
        self._emit_action_event = emit_action_event
        self._current_turn_id: int | None = None

    @property
    def input_locked(self) -> bool:
        return self.mode in {"PIN_REQUIRED", "PIN_REGISTRATION", "PIN_CONFIRM"}

    async def _safe_state(self, values: dict[str, Any], *, private: dict[str, Any] | None = None):
        await self.engine.update_sakinah_security_state(values, private_context=private)

    async def _speak(self, text: str) -> None:
        await self.engine.queue_sakinah_security_message(text)

    async def _action(
        self,
        action: str,
        *,
        status: str,
        details: dict[str, Any],
        turn_id: int | None = None,
    ) -> None:
        if self._emit_action_event is None:
            return
        if turn_id is None:
            turn_id = self._current_turn_id
        try:
            await self._emit_action_event(
                action=action,
                status=status,
                details=details,
                turn_id=turn_id,
            )
        except Exception as exc:  # noqa: BLE001 - observability must not block support
            logger.warning(
                "Sakinah action event emission failed for {} ({})",
                action,
                type(exc).__name__,
            )

    @staticmethod
    def _safe_loaded_call_details(context: dict[str, Any]) -> list[dict[str, Any]]:
        calls = []
        for item in context.get("previous_calls") or []:
            if not isinstance(item, dict):
                continue
            calls.append(
                {
                    "call_ref": internal_reference(
                        item.get("workflow_run_id"), prefix="call"
                    ),
                    "completed_at": item.get("completed_at") or item.get("started_at"),
                    "bounded_summary": str(item.get("summary") or "")[:240],
                }
            )
        return calls

    @staticmethod
    def _safe_fact_categories(context: dict[str, Any]) -> list[str]:
        categories = []
        for item in context.get("durable_facts") or []:
            if not isinstance(item, dict):
                continue
            category = str(
                item.get("fact_category") or item.get("memory_type") or ""
            ).strip()
            if category and category not in categories:
                categories.append(category[:64])
        return categories

    async def _emit_retrieval_event(self, context: dict[str, Any]) -> None:
        retrieval_status = context.get("retrieval_status")
        if retrieval_status not in {"available", "unavailable", "error"}:
            retrieval_status = (
                "available" if context.get("continuity_available") else "unavailable"
            )
        event_status = {
            "available": "success",
            "unavailable": "unavailable",
            "error": "error",
        }[retrieval_status]
        await self._action(
            "CONTINUITY_RETRIEVAL",
            status=event_status,
            details={
                "choice": "continue",
                "retrieval_status": retrieval_status,
                "previous_calls_requested": int(
                    context.get("previous_calls_requested", 2)
                ),
                "previous_calls_loaded": int(
                    context.get(
                        "previous_calls_loaded",
                        len(context.get("previous_calls") or []),
                    )
                ),
                "durable_facts_requested": True,
                "durable_facts_loaded": int(
                    context.get(
                        "durable_facts_loaded",
                        len(context.get("durable_facts") or []),
                    )
                ),
                "raw_transcripts_loaded": False,
                "raw_transcripts_injected": False,
                "storage_source": context.get(
                    "storage_source", "backend_continuity_service"
                ),
                "bounded_context_ref": context.get("bounded_context_ref"),
                "previous_calls": self._safe_loaded_call_details(context),
                "durable_fact_categories": self._safe_fact_categories(context),
            },
        )

    async def bypass_continuity_for_safety(self, *, reason: str) -> None:
        """Release the continuity gate so independent safety routing can lead."""
        self._choice_pending = False
        self.mode = "NONE"
        self.state = "CONTINUITY_DECLINED"
        await self._action(
            "CONTINUITY_CHOICE",
            status="bypassed",
            details={
                "caller_result": "returning",
                "choice": "unclear",
                "session_access_mode": "returning_profile",
                "historic_context_loaded_before_choice": False,
                "bypass_reason": reason,
            },
        )
        await self._action(
            "CONTINUITY_RETRIEVAL",
            status="bypassed",
            details={
                "choice": "unclear",
                "retrieval_status": "unavailable",
                "previous_calls_requested": 0,
                "previous_calls_loaded": 0,
                "durable_facts_requested": False,
                "durable_facts_loaded": 0,
                "raw_transcripts_loaded": False,
                "raw_transcripts_injected": False,
                "storage_source": "backend_continuity_service",
                "bounded_context_ref": None,
                "previous_calls": [],
                "durable_fact_categories": [],
                "bypass_reason": reason,
            },
        )
        await self._action(
            "CONTINUITY_CONTEXT_INJECTED",
            status="bypassed",
            details={
                "injection_status": "bypassed",
                "previous_calls_available": 0,
                "durable_facts_available": 0,
                "bounded_context_only": True,
                "raw_transcripts_injected": False,
                "context_ref": None,
                "bypass_reason": reason,
            },
        )
        await self._safe_state(
            {
                "caller_status": self.state,
                "continuity_authorised": False,
                "continuity_available": False,
                "continuity_choice": "unclear",
                "retrieval_status": "bypassed",
                "previous_calls_loaded": 0,
                "durable_facts_loaded": 0,
                "raw_transcripts_loaded": False,
                "raw_transcripts_injected": False,
                "safety_precedence": True,
            },
            private={
                "memory_context": "No historic continuity is authorised for this call."
            },
        )
        await self._persist_choice("safety_bypass", self.state)

    async def _persist_choice(self, choice: str, state: str) -> None:
        try:
            await db_client.update_workflow_run(
                self.engine._workflow_run_id,
                caller_state=state,
                gathered_context={"continuity_choice": choice},
            )
        except Exception:  # noqa: BLE001 - optional state persistence cannot block calls
            logger.warning("Unable to persist Sakinah continuity state")

    async def _record_continuity_permission(self) -> bool:
        try:
            latest = await db_client.get_latest_privacy_permission(
                self.service_user_id, permission_type="memory_use"
            )
            if latest is not None and not latest.granted:
                return False
            if not await db_client.is_memory_permitted(
                self.service_user_id, permission_type="memory_use"
            ):
                return False
            await db_client.record_privacy_permission(
                organization_id=self.organization_id,
                service_user_id=self.service_user_id,
                permission_type="memory_use",
                granted=True,
                verification_level="verified",
                source_workflow_run_id=self.engine._workflow_run_id,
            )
            return True
        except Exception:  # noqa: BLE001 - retrieval must fail closed
            logger.warning("Sakinah continuity permission could not be recorded")
            return False

    async def handle_digit(self, digit: str) -> None:
        if self.mode not in {"PIN_REQUIRED", "PIN_REGISTRATION", "PIN_CONFIRM"}:
            return
        if digit == "*":
            self._digits = ""
            return
        if digit == "#":
            if len(self._digits) == SAKINAH_PIN_LENGTH:
                await self._finish_digits()
            return
        if not digit.isascii() or not digit.isdigit():
            return
        if len(self._digits) >= SAKINAH_PIN_LENGTH:
            return
        self._digits += digit
        if len(self._digits) == SAKINAH_PIN_LENGTH:
            await self._finish_digits()

    async def _finish_digits(self) -> None:
        entered = self._digits
        self._digits = ""
        try:
            if self.mode == "PIN_REQUIRED":
                logger.bind(event="pin_verification_started").info(
                    "Sakinah PIN verification started"
                )
                result = await db_client.verify_sakinah_pin(
                    organization_id=self.organization_id,
                    service_user_id=self.service_user_id,
                    pin=entered,
                )
                if result.identity_access_authorised:
                    self.state = "VERIFIED"
                    self.mode = "NONE"
                    self._choice_pending = True
                    await self._safe_state(
                        {
                            "caller_status": "VERIFIED",
                            "pin_required": True,
                            "pin_verification_status": "verified",
                            "identity_access_authorised": True,
                            "continuity_authorised": False,
                            "continuity_available": False,
                        }
                    )
                    await self._speak(
                        "Your identity has been verified. Would you like to continue "
                        "from where we left off, or start fresh today?"
                    )
                else:
                    self.state = "LOCKED" if result.locked else "PIN_REQUIRED"
                    # A failed attempt never authorises history. End this
                    # call's PIN entry mode so the caller can continue a
                    # fresh conversation; the persistent credential counter
                    # still enforces lockout on subsequent attempts.
                    self.mode = "NONE"
                    await self._safe_state(
                        {
                            "caller_status": self.state,
                            "pin_verification_status": (
                                "locked" if result.locked else "failed"
                            ),
                            "identity_access_authorised": False,
                            "continuity_authorised": False,
                            "continuity_available": False,
                        }
                    )
                    await self._speak(
                        "That PIN was not accepted. I cannot access previous "
                        "conversation information. We can continue with a fresh "
                        "conversation today."
                    )
            elif self.mode == "PIN_REGISTRATION":
                self._registration_pin = entered
                self.mode = "PIN_CONFIRM"
                await self._speak("Please enter the same PIN again using the keypad.")
            else:
                if self._registration_pin is None or not hmac.compare_digest(
                    self._registration_pin, entered
                ):
                    logger.bind(event="pin_enrolment_failed").warning(
                        "Sakinah PIN enrolment failed"
                    )
                    self._registration_pin = None
                    self.mode = "NONE"
                    await self._safe_state({"pin_registration_status": "failure"})
                    await self._speak("The PIN entries did not match. We can continue without creating a PIN.")
                    return
                registration_pin = self._registration_pin
                self._registration_pin = None
                self.mode = "NONE"
                allowed = (
                    (self.engine._gathered_context or {}).get("profile_binding_status")
                    != "unverified_existing"
                    and await db_client.is_memory_permitted(
                        self.service_user_id, permission_type="memory_storage"
                    )
                )
                success = allowed and await db_client.register_sakinah_pin(
                    organization_id=self.organization_id,
                    service_user_id=self.service_user_id,
                    pin=registration_pin,
                )
                if not success:
                    logger.bind(event="pin_enrolment_failed").warning(
                        "Sakinah PIN enrolment failed"
                    )
                await self._safe_state(
                    {
                        "pin_registration_status": "success" if success else "failure",
                        "pin_required": False,
                    }
                )
                await self._speak(
                    "Your Sakinah PIN has been created successfully."
                    if success
                    else "I could not create a PIN right now. We can continue without one."
                )
        except Exception:  # noqa: BLE001 - auth failure must fail closed
            self._registration_pin = None
            self._digits = ""
            self.mode = "NONE" if self.state == "PIN_REQUIRED" else self.mode
            logger.warning("Sakinah PIN operation failed; continuing without history")
            await self._safe_state(
                {
                    "identity_access_authorised": False,
                    "continuity_authorised": False,
                    "continuity_available": False,
                    "pin_verification_status": "failed",
                }
            )
        finally:
            # Best-effort clearing of immutable Python string references.
            entered = ""

    async def handle_user_text(self, text: str, *, turn_id: int | None = None) -> None:
        if turn_id is not None:
            self._current_turn_id = turn_id
        normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
        if not normalized:
            return
        if self._choice_pending and score_safety(text).get("requires_immediate_action"):
            await self.bypass_continuity_for_safety(
                reason="independent_safety_signal"
            )
            return
        if self._choice_pending and self.state == "VERIFIED":
            if re.search(r"\b(start|begin)\s+(fresh|new)\b|something different", normalized):
                self._choice_pending = False
                self.state = "CONTINUITY_DECLINED"
                logger.bind(event="continuity_fresh_selected").info(
                    "Sakinah caller selected fresh conversation"
                )
                await self._safe_state(
                    {
                        "caller_status": self.state,
                        "continuity_authorised": False,
                        "continuity_available": False,
                        "continuity_choice": "new",
                        "retrieval_status": "bypassed",
                        "previous_calls_loaded": 0,
                        "durable_facts_loaded": 0,
                        "raw_transcripts_loaded": False,
                        "raw_transcripts_injected": False,
                    },
                    private={
                        "memory_context": "No historic continuity is authorised for this call."
                    },
                )
                await self._action(
                    "CONTINUITY_CHOICE",
                    status="success",
                    details={
                        "caller_result": "returning",
                        "choice": "new",
                        "session_access_mode": "returning_profile",
                        "historic_context_loaded_before_choice": False,
                    },
                )
                await self._action(
                    "CONTINUITY_RETRIEVAL",
                    status="bypassed",
                    details={
                        "choice": "new",
                        "retrieval_status": "unavailable",
                        "previous_calls_requested": 0,
                        "previous_calls_loaded": 0,
                        "durable_facts_requested": False,
                        "durable_facts_loaded": 0,
                        "raw_transcripts_loaded": False,
                        "raw_transcripts_injected": False,
                        "storage_source": "backend_continuity_service",
                        "bounded_context_ref": None,
                        "previous_calls": [],
                        "durable_fact_categories": [],
                    },
                )
                await self._action(
                    "CONTINUITY_CONTEXT_INJECTED",
                    status="bypassed",
                    details={
                        "injection_status": "bypassed",
                        "previous_calls_available": 0,
                        "durable_facts_available": 0,
                        "bounded_context_only": True,
                        "raw_transcripts_injected": False,
                        "context_ref": None,
                    },
                )
                await self._persist_choice("new", self.state)
                return
            if re.search(r"\b(continue|carry on|pick up|where we left)\b", normalized):
                self._choice_pending = False
                logger.bind(event="continuity_continue_selected").info(
                    "Sakinah caller selected continuity"
                )
                await self._action(
                    "CONTINUITY_CHOICE",
                    status="success",
                    details={
                        "caller_result": "returning",
                        "choice": "continue",
                        "session_access_mode": "returning_profile",
                        "historic_context_loaded_before_choice": False,
                    },
                )
                if not await self._record_continuity_permission():
                    self.state = "CONTINUITY_DECLINED"
                    await self._safe_state(
                        {
                            "caller_status": self.state,
                            "continuity_authorised": False,
                            "continuity_available": False,
                            "continuity_choice": "continue",
                            "retrieval_status": "unavailable",
                            "previous_calls_loaded": 0,
                            "durable_facts_loaded": 0,
                            "raw_transcripts_loaded": False,
                            "raw_transcripts_injected": False,
                        },
                        private={
                            "memory_context": "Historic continuity is unavailable for this call."
                        },
                    )
                    await self._action(
                        "CONTINUITY_RETRIEVAL",
                        status="unavailable",
                        details={
                            "choice": "continue",
                            "retrieval_status": "unavailable",
                            "previous_calls_requested": 2,
                            "previous_calls_loaded": 0,
                            "durable_facts_requested": True,
                            "durable_facts_loaded": 0,
                            "raw_transcripts_loaded": False,
                            "raw_transcripts_injected": False,
                            "storage_source": "backend_continuity_service",
                            "bounded_context_ref": None,
                            "previous_calls": [],
                            "durable_fact_categories": [],
                        },
                    )
                    await self._action(
                        "CONTINUITY_CONTEXT_INJECTED",
                        status="unavailable",
                        details={
                            "injection_status": "empty",
                            "previous_calls_available": 0,
                            "durable_facts_available": 0,
                            "bounded_context_only": True,
                            "raw_transcripts_injected": False,
                            "context_ref": None,
                        },
                    )
                    await self._persist_choice("continue", self.state)
                    return
                try:
                    context = await retrieve_bounded_continuity(
                        organization_id=self.organization_id,
                        service_user_id=self.service_user_id,
                        current_run_id=self.engine._workflow_run_id,
                        verified=True,
                    )
                except Exception as exc:  # noqa: BLE001 - continuity is optional
                    logger.warning(
                        "Sakinah continuity retrieval raised ({})", type(exc).__name__
                    )
                    context = {
                        "continuity_available": False,
                        "retrieval_status": "error",
                        "previous_calls_requested": 2,
                        "previous_calls_loaded": 0,
                        "durable_facts_requested": True,
                        "durable_facts_loaded": 0,
                        "raw_transcripts_loaded": False,
                        "raw_transcripts_injected": False,
                        "storage_source": "backend_continuity_service",
                        "bounded_context_ref": internal_reference(
                            self.engine._workflow_run_id, prefix="continuity"
                        ),
                        "previous_calls": [],
                        "durable_facts": [],
                        "previous_plans": [],
                    }
                await self._emit_retrieval_event(context)
                self.state = "CONTINUITY_AUTHORISED"
                prompt_context = bounded_continuity_prompt(context)
                retrieval_status = context.get("retrieval_status") or (
                    "available" if context.get("continuity_available") else "unavailable"
                )
                injection_status = (
                    "injected"
                    if context.get("previous_calls") or context.get("durable_facts")
                    else "empty"
                )
                await self._action(
                    "CONTINUITY_CONTEXT_INJECTED",
                    status="success" if injection_status == "injected" else "unavailable",
                    details={
                        "injection_status": injection_status,
                        "previous_calls_available": len(
                            context.get("previous_calls") or []
                        ),
                        "durable_facts_available": len(
                            context.get("durable_facts") or []
                        ),
                        "bounded_context_only": True,
                        "raw_transcripts_injected": False,
                        "context_ref": context.get("bounded_context_ref"),
                    },
                )
                await self._safe_state(
                    {
                        "caller_status": self.state,
                        "continuity_authorised": True,
                        "continuity_available": bool(context.get("continuity_available")),
                        "continuity_choice": "continue",
                        "retrieval_status": retrieval_status,
                        "previous_calls_loaded": len(context.get("previous_calls") or []),
                        "durable_facts_loaded": len(context.get("durable_facts") or []),
                        "raw_transcripts_loaded": False,
                        "raw_transcripts_injected": False,
                    },
                    private={
                        "memory_context": prompt_context,
                        "continuity_context": prompt_context,
                    },
                )
                await self._persist_choice("continue", self.state)
                return
        if self.mode == "PIN_OFFER":
            if re.search(r"\b(yes|yeah|yep|please|okay|ok)\b", normalized):
                try:
                    allowed = await db_client.is_memory_permitted(
                        self.service_user_id, permission_type="memory_storage"
                    )
                except Exception:  # noqa: BLE001 - optional enrolment cannot block calls
                    allowed = False
                if allowed:
                    self.mode = "PIN_REGISTRATION"
                    logger.bind(event="pin_enrolment_started").info(
                        "Sakinah PIN enrolment started"
                    )
                    await self._safe_state({"pin_registration_status": "pending"})
                    await self._speak("Please enter a new PIN using the telephone keypad.")
                else:
                    self.mode = "NONE"
                    logger.bind(event="pin_enrolment_failed").warning(
                        "Sakinah PIN enrolment unavailable"
                    )
                    await self._safe_state({"pin_registration_status": "unavailable"})
            elif re.search(r"\b(no|not now|don't)\b", normalized):
                self.mode = "NONE"
                await self._safe_state({"pin_registration_status": "declined"})

    def note_assistant_text(self, text: str) -> None:
        self._assistant_text = (self._assistant_text + " " + (text or ""))[-1_500:]
        if self.mode == "NONE" and self.service_user_id:
            offer_detected = re.search(
                r"\b(pin|passcode)\b.*\b(create|set up|register|secure|recognise|recognize)\b"
                r"|\b(create|set up|register)\b.*\b(pin|passcode)\b",
                self._assistant_text.lower(),
            ) or (
                "pin" in self._assistant_text.lower()
                and "would you like" in self._assistant_text.lower()
                and "enter your pin" not in self._assistant_text.lower()
            )
            if offer_detected:
                self.mode = "PIN_OFFER"


class SakinahSecureInputProcessor(FrameProcessor):
    def __init__(self, runtime: SakinahIdentityRuntime):
        super().__init__()
        self.runtime = runtime

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputDTMFFrame):
            await self.runtime.handle_digit(frame.button.value)
            return
        if self.runtime.input_locked and isinstance(frame, InputAudioRawFrame):
            return
        await self.push_frame(frame, direction)


class SakinahIdentityUserObserver(FrameProcessor):
    def __init__(self, runtime: SakinahIdentityRuntime):
        super().__init__()
        self.runtime = runtime
        self._turn_id = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            self._turn_id += 1
            await self.runtime.handle_user_text(frame.text, turn_id=self._turn_id)
        await self.push_frame(frame, direction)


class SakinahIdentityOutputObserver(FrameProcessor):
    def __init__(self, runtime: SakinahIdentityRuntime):
        super().__init__()
        self.runtime = runtime

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, (LLMTextFrame, TTSTextFrame)):
            self.runtime.note_assistant_text(frame.text)
        await self.push_frame(frame, direction)
