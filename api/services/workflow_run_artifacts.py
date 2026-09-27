"""Upload end-of-call artifacts (recordings, transcript) to object storage.

Called from the pipeline process itself, straight from the in-memory call
buffers, so no local file ever has to cross a process/host boundary (no
shared /tmp between web and ARQ workers). Uploads happen before the
workflow-completion job is enqueued so QA and webhooks see the artifacts
in storage.
"""

import asyncio
import hashlib
import io
import json
import wave
from datetime import UTC, datetime

from loguru import logger

from api.constants import RECORD_CALLS
from api.db import db_client
from api.services.s3_secondary_replication import schedule_s3_replication
from api.services.storage import get_current_storage_backend, storage_fs


def _recording_metadata(
    storage_key: str,
    storage_backend: str,
    track: str,
    data: bytes | None = None,
) -> dict:
    duration = None
    if data:
        try:
            with wave.open(io.BytesIO(data), "rb") as wav_file:
                duration = wav_file.getnframes() / wav_file.getframerate()
        except (EOFError, wave.Error, ZeroDivisionError):
            pass
    return {
        "storage_key": storage_key,
        "storage_backend": storage_backend,
        "format": "wav",
        "track": track,
        "duration_seconds": duration,
        "size_bytes": len(data) if data is not None else None,
    }


async def _upload_bytes(
    workflow_run_id: int,
    data: bytes,
    storage_key: str,
    label: str,
) -> bool:
    for attempt in range(3):
        try:
            if await storage_fs.acreate_file_from_bytes(storage_key, data):
                logger.info("Uploaded {} for workflow run {}", label, workflow_run_id)
                return True
        except Exception:
            logger.warning(
                "Storage upload attempt {} failed for workflow run {} ({})",
                attempt + 1,
                workflow_run_id,
                label,
            )
        if attempt < 2:
            await asyncio.sleep(0.25 * (2**attempt))
    logger.error(
        "Storage upload failed after retries for workflow run {} ({})",
        workflow_run_id,
        label,
    )
    return False


async def _persist_recording_metadata(
    workflow_run_id: int,
    metadata: dict,
    *,
    update_run: bool = False,
) -> bool:
    """Persist one artifact's metadata without stopping other artifact work."""
    saved = True
    if update_run:
        try:
            await db_client.update_workflow_run(
                run_id=workflow_run_id,
                recording_url=metadata["storage_key"],
                storage_backend=metadata["storage_backend"],
                recording_object_key=metadata["storage_key"],
                recording_duration_seconds=metadata["duration_seconds"],
                recording_format=metadata["format"],
                recording_size_bytes=metadata["size_bytes"],
            )
        except Exception:
            saved = False
            logger.warning(
                "Recording run metadata write failed for workflow run {}",
                workflow_run_id,
            )
    try:
        await db_client.upsert_call_recording(
            agent_run_id=workflow_run_id,
            storage_backend=metadata["storage_backend"],
            object_key=metadata["storage_key"],
            duration_seconds=metadata["duration_seconds"],
            format=metadata["format"],
            size_bytes=metadata["size_bytes"],
            track=metadata["track"],
        )
    except Exception:
        saved = False
        logger.warning(
            "Recording metadata write failed for workflow run {} ({})",
            workflow_run_id,
            metadata["track"],
        )
    return saved


async def _persist_run_fields(workflow_run_id: int, **fields) -> bool:
    try:
        await db_client.update_workflow_run(run_id=workflow_run_id, **fields)
        return True
    except Exception:
        logger.warning(
            "Artifact metadata write failed for workflow run {}", workflow_run_id
        )
        return False


def _log_storage_audit(audit: dict) -> None:
    """Emit storage outcome metadata without logging transcript/audio content."""
    logger.bind(storage_audit=True).info(
        "workflow_run_artifact_finalization run_id={} primary_status={} artifacts={}",
        audit["run_id"],
        audit["overall_primary_status"],
        audit["artifact_count"],
    )


async def upload_workflow_run_artifacts(
    workflow_run_id: int,
    *,
    mixed_audio_wav: bytes | None = None,
    user_audio_wav: bytes | None = None,
    bot_audio_wav: bytes | None = None,
    transcript_text: str | None = None,
) -> dict:
    """Upload call artifacts to object storage and persist their metadata.

    Each artifact is uploaded independently; a failure is logged and the
    remaining artifacts are still attempted.
    """
    storage_backend = get_current_storage_backend()
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    call_id = str(getattr(workflow_run, "call_id", None) or workflow_run_id)
    service_user_id = str(getattr(workflow_run, "service_user_id", None) or "anonymous")
    started_at = getattr(workflow_run, "started_at", None) or datetime.now(UTC)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    artifact_root = (
        f"{started_at.year:04d}/{started_at.month:02d}/{service_user_id}/{call_id}"
    )
    if not RECORD_CALLS:
        mixed_audio_wav = user_audio_wav = bot_audio_wav = None

    recordings_metadata: dict[str, dict] = {}
    bucket = getattr(storage_fs, "bucket_name", None)
    audit: dict = {
        "run_id": workflow_run_id,
        "storage_backend": storage_backend.value,
        "postgres": {"run_row_id": workflow_run_id, "status": "verified"},
        "postgres_saved": True,
        "recordings": {"bucket": bucket, "status": "not_expected", "objects": []},
        "transcript": {"bucket": bucket, "status": "not_expected"},
    }

    if mixed_audio_wav:
        recording_url = f"recordings/{artifact_root}/call.wav"
        logger.info(
            f"Uploading mixed audio to {storage_backend.name} - workflow_run_id: {workflow_run_id}"
        )
        if await _upload_bytes(
            workflow_run_id, mixed_audio_wav, recording_url, "mixed audio"
        ):
            recordings_metadata["mixed"] = _recording_metadata(
                recording_url, storage_backend.value, "mixed", mixed_audio_wav
            )
            audit["postgres_saved"] &= await _persist_recording_metadata(
                workflow_run_id, recordings_metadata["mixed"], update_run=True
            )
            audit["recordings"]["objects"].append(
                {
                    "type": "mixed",
                    "object_key": recording_url,
                    "size_bytes": len(mixed_audio_wav),
                    "status": "success",
                }
            )
        else:
            audit["recordings"]["objects"].append(
                {
                    "type": "mixed",
                    "object_key": recording_url,
                    "size_bytes": len(mixed_audio_wav),
                    "status": "failed",
                }
            )

    if user_audio_wav:
        user_recording_url = f"recordings/{artifact_root}/user.wav"
        logger.info(
            f"Uploading user audio to {storage_backend.name} - workflow_run_id: {workflow_run_id}"
        )
        if await _upload_bytes(
            workflow_run_id, user_audio_wav, user_recording_url, "user audio"
        ):
            recordings_metadata["user"] = _recording_metadata(
                user_recording_url, storage_backend.value, "user", user_audio_wav
            )
            audit["postgres_saved"] &= await _persist_recording_metadata(
                workflow_run_id, recordings_metadata["user"]
            )
            audit["recordings"]["objects"].append(
                {
                    "type": "user",
                    "object_key": user_recording_url,
                    "size_bytes": len(user_audio_wav),
                    "status": "success",
                }
            )
        else:
            audit["recordings"]["objects"].append(
                {
                    "type": "user",
                    "object_key": user_recording_url,
                    "size_bytes": len(user_audio_wav),
                    "status": "failed",
                }
            )

    if bot_audio_wav:
        bot_recording_url = f"recordings/{artifact_root}/bot.wav"
        logger.info(
            f"Uploading bot audio to {storage_backend.name} - workflow_run_id: {workflow_run_id}"
        )
        if await _upload_bytes(
            workflow_run_id, bot_audio_wav, bot_recording_url, "bot audio"
        ):
            recordings_metadata["bot"] = _recording_metadata(
                bot_recording_url, storage_backend.value, "bot", bot_audio_wav
            )
            audit["postgres_saved"] &= await _persist_recording_metadata(
                workflow_run_id, recordings_metadata["bot"]
            )
            audit["recordings"]["objects"].append(
                {
                    "type": "bot",
                    "object_key": bot_recording_url,
                    "size_bytes": len(bot_audio_wav),
                    "status": "success",
                }
            )
        else:
            audit["recordings"]["objects"].append(
                {
                    "type": "bot",
                    "object_key": bot_recording_url,
                    "size_bytes": len(bot_audio_wav),
                    "status": "failed",
                }
            )

    if recordings_metadata:
        audit["postgres_saved"] &= await _persist_run_fields(
            workflow_run_id,
            storage_backend=storage_backend.value,
            extra={"recordings": recordings_metadata},
        )

    if transcript_text:
        transcript_url = f"transcripts/{artifact_root}/transcript.txt"
        logger.info(
            f"Uploading transcript to {storage_backend.name} - workflow_run_id: {workflow_run_id}"
        )
        if await _upload_bytes(
            workflow_run_id,
            transcript_text.encode("utf-8"),
            transcript_url,
            "transcript",
        ):
            audit["postgres_saved"] &= await _persist_run_fields(
                workflow_run_id,
                transcript_url=transcript_url,
                storage_backend=storage_backend.value,
                full_transcript=transcript_text,
            )
            audit["transcript"].update(
                {"object_key": transcript_url, "status": "success"}
            )
        else:
            audit["transcript"].update(
                {"object_key": transcript_url, "status": "failed"}
            )

    recording_objects = audit["recordings"]["objects"]
    if recording_objects:
        audit["recordings"]["status"] = (
            "success"
            if all(item["status"] == "success" for item in recording_objects)
            else "partial"
        )
    primary_objects = [*recording_objects]
    if audit["transcript"]["status"] != "not_expected":
        primary_objects.append(audit["transcript"])
    audit["artifact_count"] = len(primary_objects)
    audit["postgres"]["status"] = "verified" if audit["postgres_saved"] else "failed"
    audit["overall_primary_status"] = (
        "partial"
        if not audit["postgres_saved"]
        or any(item["status"] == "failed" for item in primary_objects)
        else "success"
    )
    audit["overall_status"] = audit["overall_primary_status"]
    audit["s3"] = await schedule_s3_replication(workflow_run_id, audit)
    _log_storage_audit(audit)
    return audit


async def persist_calm_score_snapshot(
    workflow_run_id: int, calm_turns: list[dict]
) -> dict:
    """Persist an immutable, content-minimized in-progress CALM score snapshot.

    PostgreSQL remains the queryable source of truth. This object copy makes
    each completed score turn available in MinIO and, when configured, S3.
    Utterances, prompts, and generated responses are deliberately excluded.
    """
    run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if run is None or not calm_turns:
        return {"status": "not_expected"}
    last_turn = calm_turns[-1]
    score_data = {
        "workflow_run_id": workflow_run_id,
        "turn_id": last_turn.get("turn_id"),
        "calm_scores": last_turn.get("calm_scores") or {},
        "calm_confidence": last_turn.get("calm_confidence") or {},
        "trend": last_turn.get("trend") or {},
        "significant_changes": last_turn.get("significant_changes") or {},
    }
    payload = json.dumps(score_data, sort_keys=True, separators=(",", ":")).encode()
    storage_backend = get_current_storage_backend()
    turn_id = int(last_turn.get("turn_id") or len(calm_turns))
    object_key = f"scores/workflow-run-{workflow_run_id}/calm-turn-{turn_id:04d}.json"
    if not await _upload_bytes(
        workflow_run_id, payload, object_key, "CALM score snapshot"
    ):
        return {"status": "failed", "object_key": object_key}
    checksum = hashlib.sha256(payload).hexdigest()
    audit = {
        "run_id": workflow_run_id,
        "storage_backend": storage_backend.value,
        "scores": {
            "bucket": getattr(storage_fs, "bucket_name", None),
            "objects": [
                {
                    "type": "calm_scores",
                    "object_key": object_key,
                    "size_bytes": len(payload),
                    "checksum_sha256": checksum,
                    "status": "success",
                }
            ],
        },
    }
    secondary = await schedule_s3_replication(
        workflow_run_id, audit, job_suffix=f"calm-{turn_id}"
    )
    logger.info(
        "Persisted CALM snapshot run_id={} turn_id={} backend={} secondary_status={}",
        workflow_run_id,
        turn_id,
        storage_backend.value,
        secondary.get("status"),
    )
    return {"status": "success", "object_key": object_key, "secondary": secondary}
