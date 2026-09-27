"""Durable, credential-safe CALM progress snapshots for Sakinah runs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from loguru import logger


def calm_progress_object_key(session_id: str) -> str:
    """Return the stable object key for one simulation's score snapshots."""
    return f"sakinah/calm/{session_id}.json"


def calm_progress_payload(
    *, session_id: str, calm_turns: list[dict[str, Any]]
) -> bytes:
    """Serialize only structured CALM state, never prompts or transcripts."""
    persisted_turns = [
        {
            field: turn.get(field)
            for field in (
                "turn_id",
                "calm_scores",
                "calm_confidence",
                "trend",
                "significant_changes",
                "safety_state",
                "clinical_evaluation",
            )
            if field in turn
        }
        for turn in calm_turns
        if isinstance(turn, dict)
    ]
    payload = {
        "schema_version": 1,
        "session_id": session_id,
        "updated_at": datetime.now(UTC).isoformat(),
        "calm_turns": persisted_turns,
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


async def persist_calm_progress(
    *, session_id: str, calm_turns: list[dict[str, Any]]
) -> dict[str, str] | None:
    """Write the latest score snapshot to the active artifact backend.

    PostgreSQL remains the queryable source of truth.  This object-store copy
    gives the configured MinIO/S3 backend the same in-progress recovery point
    without storing the generated prompt or the call transcript.  Storage
    outages must not stop a clinical conversation, so callers receive ``None``
    and retain the relational update.
    """
    from api.services.storage import get_current_storage_backend, storage_fs

    object_key = calm_progress_object_key(session_id)
    try:
        saved = await storage_fs.acreate_file_from_bytes(
            object_key,
            calm_progress_payload(session_id=session_id, calm_turns=calm_turns),
        )
    except Exception as exc:  # noqa: BLE001 - score backup must not end the call
        logger.warning(
            "CALM object-store snapshot failed session_id={} backend={} error={}",
            session_id,
            get_current_storage_backend().value,
            type(exc).__name__,
        )
        return None
    if not saved:
        logger.warning(
            "CALM object-store snapshot was not saved session_id={} backend={}",
            session_id,
            get_current_storage_backend().value,
        )
        return None
    return {
        "storage_backend": get_current_storage_backend().value,
        "object_key": object_key,
    }
