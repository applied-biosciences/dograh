"""Resolve caller identity without treating caller ID as authentication."""

from __future__ import annotations

from typing import Any

from loguru import logger

from api.constants import MEMORY_ENABLED
from api.db import db_client


def _caller_identifier(context: dict[str, Any]) -> str | None:
    for key in ("caller_identifier", "caller_number", "from_number", "phone_number"):
        value = context.get(key)
        if value:
            return str(value)
    return None


def _prompt_context(caller_status: str, memories: list[dict[str, Any]]) -> str:
    lines = [
        "Caller continuity context is private and must not be read aloud as a list of records.",
        f"Caller state: {caller_status}.",
        "Do not reveal historic details in the opening greeting.",
        "Only refer to memory when identity and continuity are explicitly authorised.",
    ]
    for memory in memories:
        may_verbalize = bool(memory.get("may_verbalize"))
        may_use_explicit_detail = bool(memory.get("may_use_explicit_detail"))
        speech_rule = (
            "May be referred to naturally"
            if may_verbalize
            else "Internal context only: never mention, confirm, or quote this memory"
        )
        if may_verbalize and not may_use_explicit_detail:
            speech_rule += "; do not disclose explicit historic detail"
        lines.append(
            "Private memory: "
            f"[{memory['memory_type']}; sensitivity={memory['sensitivity']}; "
            f"speech_rule={speech_rule}] {memory['memory_text']}"
        )
    return "\n".join(lines)


async def prepare_memory_context(
    *, organization_id: int | None, call_context: dict[str, Any]
) -> dict[str, Any]:
    """Return safe caller state for legacy callers of this orchestrator.

    The legacy orchestrator is intentionally recognition-only. The dedicated
    Sakinah continuity runtime owns the verified-PIN plus Continue gate; this
    path never treats workflow context or caller ID as authentication.
    """
    unknown = {
        "caller_status": "UNKNOWN",
        "service_user_id": None,
        "caller_identifier_id": None,
        "preferred_name": None,
        "memory_available": False,
        "memory_authorisation_level": "none",
        "privacy_safe_previous_summary": None,
        "relevant_memories": [],
        "prompt_context": _prompt_context("UNKNOWN", []),
        "greeting_override": (
            "Hello, you're speaking with Sakinah. I'm here to listen and support you.\n"
            "What would you like me to call you, and what would you like to talk about today?"
        ),
    }
    if not MEMORY_ENABLED or not organization_id:
        return unknown
    identifier = _caller_identifier(call_context)
    if not identifier:
        return unknown

    try:
        resolution = await db_client.resolve_caller_identity(
            organization_id,
            identifier,
            preferred_name=(
                call_context.get("preferred_name") or call_context.get("caller_name")
            ),
        )
        service_user = resolution.service_user
        caller_identifier = resolution.caller_identifier
        caller_status = "FIRST_TIME" if resolution.created else "RECOGNISED"
        return {
            "caller_status": caller_status,
            "service_user_id": service_user.id,
            "caller_identifier_id": caller_identifier.id,
            "preferred_name": None,
            "memory_available": False,
            "memory_authorisation_level": "none",
            "privacy_safe_previous_summary": None,
            "relevant_memories": [],
            "prompt_context": _prompt_context(caller_status, []),
            "greeting_override": unknown["greeting_override"],
        }
    except Exception:  # noqa: BLE001 - caller lookup cannot block a live call
        logger.warning("Memory lookup failed; continuing with an UNKNOWN caller context")
        return unknown
