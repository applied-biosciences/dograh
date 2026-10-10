"""Secure Sakinah identity gates and bounded continuity retrieval."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from loguru import logger

from api.constants import MEMORY_ENABLED, MEMORY_MAX_RESULTS, SAKINAH_PIN_ENABLED
from api.db import db_client

CONTINUITY_WORKFLOW_NAMES = {"sakinah decision agent v2", "calm - inbound"}
CONTINUITY_STATES = {
    "UNKNOWN",
    "FIRST_TIME",
    "RECOGNISED",
    "PIN_REQUIRED",
    "PIN_VERIFICATION_PENDING",
    "VERIFIED",
    "CONTINUITY_DECLINED",
    "CONTINUITY_AUTHORISED",
    "LOCKED",
}
_DIGIT_RUN = re.compile(r"\b\d{4,}\b")


def internal_reference(value: object, *, prefix: str = "ref") -> str | None:
    """Return a stable, non-sensitive correlation reference for observability."""
    if value is None or str(value).strip() == "":
        return None
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{digest}"


def normalize_workflow_name(name: str | None) -> str:
    return " ".join((name or "").split()).lower()


def is_continuity_workflow(name: str | None) -> bool:
    return normalize_workflow_name(name) in CONTINUITY_WORKFLOW_NAMES


def _safe_greeting(state: str) -> str:
    if state == "PIN_REQUIRED":
        return (
            "Hello, you're speaking with Sakinah. For your privacy, please enter "
            "your PIN using the telephone keypad."
        )
    return "Hello, you're speaking with Sakinah. How can I support you today?"


def safe_identity_context(
    *,
    state: str,
    service_user_id: str | None = None,
    caller_identifier_id: str | None = None,
    pin_required: bool = False,
    lookup_status: str = "unavailable",
    caller_result: str = "unknown",
) -> dict[str, Any]:
    """Build only safe state; no names, PINs, hashes or historical text."""
    return {
        "caller_status": state,
        "caller_result": caller_result,
        "lookup_status": lookup_status,
        "session_access_mode": (
            "returning_profile"
            if caller_result == "returning"
            else "new_profile" if caller_result == "new" else "unknown_profile"
        ),
        "service_user_id": service_user_id,
        "caller_identifier_id": caller_identifier_id,
        "pin_required": pin_required,
        "pin_verification_status": "required" if pin_required else "not_required",
        "identity_access_authorised": False,
        "continuity_authorised": False,
        "continuity_available": False,
        "continuity_choice": None,
        "retrieval_status": "unavailable",
        "previous_calls_loaded": 0,
        "durable_facts_loaded": 0,
        "raw_transcripts_loaded": False,
        "raw_transcripts_injected": False,
        "memory_available": False,
        "memory_authorisation_level": "none",
        "memory_context": (
            "No historic continuity is available. Do not infer or disclose any "
            "information from caller recognition."
        ),
        "greeting_override": _safe_greeting(state),
    }


async def prepare_sakinah_identity(
    *, organization_id: int | None, call_context: dict[str, Any]
) -> dict[str, Any]:
    """Resolve a possible caller and determine whether a PIN gate is needed."""
    if not MEMORY_ENABLED or not organization_id:
        return safe_identity_context(state="UNKNOWN", lookup_status="unavailable")
    identifier = next(
        (
            str(call_context[key])
            for key in ("caller_identifier", "caller_number", "from_number")
            if call_context.get(key)
        ),
        None,
    )
    if not identifier or not re.search(r"\d{3,}", identifier):
        return safe_identity_context(state="UNKNOWN", lookup_status="unavailable")
    try:
        resolution = await db_client.resolve_caller_identity(
            organization_id,
            identifier,
        )
        if resolution.created:
            state = "FIRST_TIME"
            caller_result = "new"
            pin_required = False
        else:
            caller_result = "returning"
            has_pin = await db_client.has_active_sakinah_pin(
                organization_id=organization_id,
                service_user_id=resolution.service_user.id,
            )
            state = "PIN_REQUIRED" if has_pin and SAKINAH_PIN_ENABLED else "RECOGNISED"
            pin_required = state == "PIN_REQUIRED"
        result = safe_identity_context(
            state=state,
            service_user_id=resolution.service_user.id,
            caller_identifier_id=resolution.caller_identifier.id,
            pin_required=pin_required,
            lookup_status="success",
            caller_result=caller_result,
        )
        logger.bind(event="returning_caller_candidate").info(
            "Sakinah caller candidate resolved"
        )
        return result
    except Exception:  # noqa: BLE001 - identity enrichment must not block calls
        logger.warning("Sakinah identity lookup failed; continuing without history")
        return safe_identity_context(state="UNKNOWN", lookup_status="error")


def _redact_numeric_runs(text: str) -> str:
    return _DIGIT_RUN.sub("[redacted]", text)


def _summary_for_run(run: Any) -> str:
    extra = run.extra if isinstance(run.extra, dict) else {}
    existing = extra.get("continuity_summary")
    if isinstance(existing, dict):
        summary = existing.get("summary")
    else:
        summary = existing
    if isinstance(summary, str) and summary.strip():
        return _redact_numeric_runs(summary.strip())[:1_200]
    return ""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def build_bounded_continuity_context(
    previous_runs: list[Any],
    memories: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create a bounded prompt object, never including raw transcripts."""
    return {
        "continuity_authorised": True,
        "previous_calls": [
            {
                "workflow_run_id": run.id,
                "started_at": _iso(run.started_at),
                "completed_at": _iso(getattr(run, "ended_at", None)),
                "summary": _summary_for_run(run),
            }
            for run in previous_runs[:2]
        ],
        "durable_facts": [
            {
                "memory_type": memory.get("memory_type"),
                "memory_text": _redact_numeric_runs(str(memory.get("memory_text") or ""))[
                    :1_000
                ],
                "sensitivity": memory.get("sensitivity"),
                "verbal_reference_allowed": bool(
                    memory.get("verbal_reference_allowed")
                ),
                "explicit_detail_allowed": bool(
                    memory.get("explicit_detail_allowed")
                ),
            }
            for memory in memories[:MEMORY_MAX_RESULTS]
            if memory.get("internal_context_allowed")
        ],
        "previous_plans": [
            {
                "memory_text": _redact_numeric_runs(str(memory.get("memory_text") or ""))[
                    :1_000
                ],
                "memory_type": memory.get("memory_type"),
            }
            for memory in memories
            if memory.get("memory_type") == "previous_plan"
            and memory.get("internal_context_allowed")
        ][:MEMORY_MAX_RESULTS],
    }


def bounded_continuity_prompt(context: dict[str, Any]) -> str:
    """Render a private, instruction-bearing, hard-bounded prompt section."""
    payload = json.dumps(context, ensure_ascii=False, separators=(",", ":"))[:7_000]
    prompt = (
        "<sakinah_continuity_context>\n"
        "Use this only to improve continuity. Do not recite stored records, say "
        "'according to your record', reveal internal metadata, or dump historic "
        "facts. Introduce permitted information naturally and follow each fact's "
        "verbal_reference_allowed and explicit_detail_allowed flags. Current caller "
        "statements override stale context; uncertainty must not be presented as fact.\n"
        f"{payload}\n"
        "</sakinah_continuity_context>"
    )
    return prompt[:8_192]


async def retrieve_bounded_continuity(
    *,
    organization_id: int,
    service_user_id: str,
    current_run_id: int,
    verified: bool = True,
) -> dict[str, Any]:
    """Fetch exactly the permitted previous calls and bounded active memories."""
    logger.bind(event="continuity_retrieval_started").info(
        "Sakinah continuity retrieval started"
    )
    try:
        if not await db_client.is_memory_permitted(
            service_user_id, permission_type="memory_use"
        ):
            return {
                "continuity_authorised": True,
                "continuity_available": False,
                "retrieval_status": "unavailable",
                "previous_calls_requested": 2,
                "previous_calls_loaded": 0,
                "durable_facts_requested": True,
                "durable_facts_loaded": 0,
                "raw_transcripts_loaded": False,
                "raw_transcripts_injected": False,
                "storage_source": "backend_continuity_service",
                "bounded_context_ref": internal_reference(
                    current_run_id, prefix="continuity"
                ),
                "previous_calls": [],
                "durable_facts": [],
                "previous_plans": [],
            }
        previous_runs = await db_client.get_last_two_eligible_previous_calls(
            organization_id=organization_id,
            service_user_id=service_user_id,
            current_run_id=current_run_id,
        )
        memories = await db_client.get_permitted_memories(
            service_user_id,
            verified=verified,
            limit=MEMORY_MAX_RESULTS,
        )
        bounded = build_bounded_continuity_context(previous_runs, memories)
        bounded["continuity_available"] = bool(
            bounded["previous_calls"] or bounded["durable_facts"]
        )
        bounded.update(
            {
                "retrieval_status": "available",
                "previous_calls_requested": 2,
                "previous_calls_loaded": len(bounded["previous_calls"]),
                "durable_facts_requested": True,
                "durable_facts_loaded": len(bounded["durable_facts"]),
                "raw_transcripts_loaded": False,
                "raw_transcripts_injected": False,
                "storage_source": "backend_continuity_service",
                "bounded_context_ref": internal_reference(
                    current_run_id, prefix="continuity"
                ),
            }
        )
        logger.bind(event="continuity_retrieval_success").info(
            "Sakinah continuity retrieval completed; previous_calls={} memories={}",
            len(bounded["previous_calls"]),
            len(bounded["durable_facts"]),
        )
        logger.bind(
            event="previous_calls_retrieved_count",
            count=len(bounded["previous_calls"]),
        ).info("Sakinah previous-call retrieval count recorded")
        logger.bind(
            event="memories_retrieved_count",
            count=len(bounded["durable_facts"]),
        ).info("Sakinah memory retrieval count recorded")
        return bounded
    except Exception as exc:  # noqa: BLE001 - continuity is optional enrichment
        logger.bind(event="continuity_retrieval_failed").warning(
            "Sakinah continuity retrieval failed; continuing fresh error_class={}",
            type(exc).__name__,
        )
        return {
            "continuity_authorised": True,
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
                current_run_id, prefix="continuity"
            ),
            "previous_calls": [],
            "durable_facts": [],
            "previous_plans": [],
        }


async def persist_continuity_summary(workflow_run_id: int, *, client: Any = None) -> None:
    """Retain compatibility with the old post-call hook without raw fallback."""
    persistence_client = client or db_client
    if not hasattr(persistence_client, "update_workflow_run"):
        return
    try:
        run = await persistence_client.get_workflow_run_by_id(workflow_run_id)
        if (
            run is None
            or not run.service_user_id
            or not run.workflow
            or not await persistence_client.is_memory_permitted(
                run.service_user_id, permission_type="memory_storage"
            )
        ):
            return
        extra = run.extra if isinstance(run.extra, dict) else {}
        if extra.get("continuity_summary"):
            return
    except Exception as exc:  # noqa: BLE001 - summary is optional enrichment
        logger.bind(event="continuity_summary_failed").warning(
            "Unable to persist Sakinah continuity summary error_class={}",
            type(exc).__name__,
        )
