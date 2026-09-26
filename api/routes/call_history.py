"""Authenticated call replay endpoints for CALMOS Connect."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.db import db_client
from api.services.auth.depends import get_user
from api.services.storage import get_storage_for_backend, storage_fs

router = APIRouter(prefix="/call-history", tags=["call-history"])


class RecordingReplayTrack(BaseModel):
    track: str
    signed_url: str


class CallReplayResponse(BaseModel):
    call_id: str
    agent_run_id: int
    recording_signed_url: str | None = None
    expires_in: int = Field(ge=60, le=900)
    transcript: str | None = None
    utterances: list[dict[str, Any]] = Field(default_factory=list)
    recordings: list[RecordingReplayTrack] = Field(default_factory=list)
    unavailable_recordings: list[str] = Field(default_factory=list)
    calm_score: dict[str, Any] = Field(default_factory=dict)
    safety_score: dict[str, Any] = Field(default_factory=dict)
    clinical_evaluation: dict[str, Any] = Field(default_factory=dict)


class RunDetailsLookupRequest(BaseModel):
    """Narrow lookup: callers provide a native/opaque run ID and their phone."""

    run_id: str = Field(min_length=1, max_length=64)
    phone_number: str = Field(min_length=3, max_length=64)


async def _build_replay_response(
    record: dict[str, Any], *, user, expires_in: int
) -> CallReplayResponse:
    """Issue short-lived object URLs only after the scoped DB lookup succeeds."""
    signed_url = None
    signed_tracks: list[RecordingReplayTrack] = []
    unavailable_tracks: list[str] = []
    recording_rows = record.get("recordings") or []
    if not recording_rows and record.get("recording_key"):
        recording_rows = [{
            "track": "mixed",
            "storage_backend": record.get("storage_backend"),
            "recording_key": record["recording_key"],
        }]
    for recording in recording_rows:
        track = recording.get("track") or "mixed"
        track = "assistant" if track == "bot" else track
        recording_key = recording.get("recording_key")
        track_url = None
        if recording_key:
            try:
                backend = recording.get("storage_backend")
                storage = get_storage_for_backend(backend) if backend else storage_fs
                track_url = await storage.aget_signed_url(
                    recording_key, expiration=expires_in, force_inline=True
                )
            except Exception:
                track_url = None

        # Media is optional for Run Details. The authorized SQL record remains
        # useful when an object was deleted, is being replicated, or its store
        # is temporarily unavailable.
        if not track_url:
            if track not in unavailable_tracks:
                unavailable_tracks.append(track)
            continue
        signed_tracks.append(RecordingReplayTrack(track=track, signed_url=track_url))
        if track == "mixed":
            signed_url = track_url

    try:
        await db_client.record_audit_event(
            organization_id=record.get("organization_id") or user.selected_organization_id,
            workflow_run_id=record["agent_run_id"],
            service_user_id=record.get("service_user_id"),
            actor_user_id=getattr(user, "id", None),
            event_type="recording_replay_url_issued",
            resource_type="workflow_run",
            resource_id=record["call_id"],
            outcome="partial" if unavailable_tracks else "success",
            event_metadata={
                "expires_in": expires_in,
                "tracks": [item.track for item in signed_tracks],
                "unavailable_tracks": unavailable_tracks,
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Replay auditing is temporarily unavailable") from exc

    return CallReplayResponse(
        call_id=record["call_id"],
        agent_run_id=record["agent_run_id"],
        recording_signed_url=signed_url,
        expires_in=expires_in,
        transcript=record.get("transcript"),
        utterances=record.get("utterances", []),
        recordings=signed_tracks,
        unavailable_recordings=unavailable_tracks,
        calm_score=record.get("calm_score") or {},
        safety_score=record.get("safety_score") or {},
        clinical_evaluation=record.get("clinical_evaluation") or {},
    )


@router.get("/{call_id}/replay", response_model=CallReplayResponse)
async def get_call_replay(
    call_id: str,
    expires_in: int = Query(default=300, ge=60, le=900),
    user=Depends(get_user),
) -> CallReplayResponse:
    record = await db_client.get_call_replay_for_user(
        call_id,
        organization_id=user.selected_organization_id,
        is_superuser=user.is_superuser,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Call not found")

    return await _build_replay_response(record, user=user, expires_in=expires_in)


@router.post("/lookup", response_model=CallReplayResponse)
async def lookup_run_details(
    request: RunDetailsLookupRequest,
    user=Depends(get_user),
) -> CallReplayResponse:
    """Resolve one authorized run by its opaque ID and phone correlation.

    The phone never appears in logs, responses, URLs, or broad listing APIs.
    Returning 404 for mismatch avoids turning this endpoint into an oracle.
    """
    record = await db_client.get_call_replay_for_user(
        request.run_id,
        phone_number=request.phone_number,
        organization_id=user.selected_organization_id,
        is_superuser=user.is_superuser,
        allow_native_run_id=True,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Call not found")
    return await _build_replay_response(record, user=user, expires_in=300)
