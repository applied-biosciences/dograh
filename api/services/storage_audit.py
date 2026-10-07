"""Read-only verification of workflow-run persistence and object storage."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from pathlib import Path
from typing import Any

from api.constants import AWS_RECORDINGS_BUCKET, ENABLE_AWS_S3_SECONDARY, RECORD_CALLS
from api.db import db_client
from api.enums import StorageBackend
from api.services.storage import storage_fs


def _status(*, exists: bool, expected: bool) -> str:
    if exists:
        return "verified"
    return "missing" if expected else "not_expected"


async def _verify_object(object_key: str, expected_size: int | None) -> dict[str, Any]:
    metadata = await storage_fs.aget_file_metadata(object_key)
    if metadata is None:
        return {"key": object_key, "status": "missing"}

    checksum = None
    with tempfile.TemporaryDirectory(prefix="dograh-storage-audit-") as temp_dir:
        local_path = str(Path(temp_dir) / "object")
        if await storage_fs.adownload_file(object_key, local_path):
            digest = hashlib.sha256()
            data = await asyncio.to_thread(Path(local_path).read_bytes)
            digest.update(data)
            checksum = digest.hexdigest()

    return {
        "key": object_key,
        "status": "verified",
        "size_bytes": metadata.get("size"),
        "content_type": metadata.get("content_type"),
        "etag": metadata.get("etag"),
        "checksum_sha256": checksum,
        "size_matches_metadata": expected_size is None
        or metadata.get("size") == expected_size,
        "last_modified": metadata.get("modified_at"),
    }


def _s3_status(
    expected: dict[str, str | None], replications: list[Any]
) -> str:
    """Report current artifacts only, never infer copy success from config."""
    configured = bool(AWS_RECORDINGS_BUCKET or ENABLE_AWS_S3_SECONDARY)
    if not expected:
        return "not_expected" if configured else "not_configured"
    by_key = {row.primary_object_key: row for row in replications}
    statuses = []
    for key, checksum in expected.items():
        row = by_key.get(key)
        if row is None or (checksum and row.checksum_sha256 != checksum):
            statuses.append("pending" if configured else "unknown")
        elif row.replication_status == "failed":
            statuses.append("failed")
        elif row.replication_status == "synced" and row.s3_saved:
            statuses.append("verified")
        elif row.s3_bucket:
            statuses.append("pending")
        else:
            statuses.append("not_configured")
    if "failed" in statuses:
        return "failed"
    if all(status == "verified" for status in statuses):
        return "verified"
    if "pending" in statuses:
        return "pending"
    return "unknown" if "unknown" in statuses else "not_configured"


async def audit_run_storage(
    run_id: int,
    *,
    organization_id: int | None = None,
) -> dict[str, Any]:
    """Verify one run's relational records and referenced local objects.

    This is intentionally read-only. It uses the normal DB client and storage
    abstraction and never creates tokens, uploads objects, or changes records.
    """
    run = await db_client.get_workflow_run(
        run_id,
        organization_id=organization_id,
    )
    if run is None:
        return {
            "run_id": run_id,
            "postgres": {
                "status": "missing",
                "run_exists": False,
                "transcript_exists": False,
                "recording_metadata_exists": False,
            },
            "minio": {"status": "not_expected", "configured": False, "objects": []},
            "aws": {"status": _s3_status({}, [])},
        }

    utterances = await db_client.get_utterances_for_run(run_id)
    recordings = await db_client.get_call_recordings_for_run(run_id)
    transcript_key = run.transcript_object_key or run.transcript_url
    transcript_expected = bool(transcript_key or run.full_transcript)
    calm_metadata = (run.extra or {}).get("calm_scoring") if run.extra else None
    calm_key = calm_metadata.get("object_key") if isinstance(calm_metadata, dict) else None
    calm_expected = bool((run.annotations or {}).get("calm_scoring") or calm_key)

    # CALM replication uses immutable revisions, not the mutable display object.
    expected_s3 = {
        recording.object_key: recording.checksum_sha256 for recording in recordings
    }
    if transcript_key:
        expected_s3[transcript_key] = None
    if isinstance(calm_metadata, dict):
        if "objects" in calm_metadata:
            for item in calm_metadata["objects"]:
                if item.get("object_key"):
                    expected_s3[item["object_key"]] = item.get("checksum_sha256")
        elif calm_key:
            expected_s3[calm_key] = calm_metadata.get("checksum_sha256")
    replications = await db_client.get_artifact_replications_for_run(
        run_id, organization_id=organization_id
    )

    objects: list[dict[str, Any]] = []
    if transcript_key:
        objects.append(
            {
                "type": "transcript",
                **await _verify_object(transcript_key, None),
            }
        )

    for recording in recordings:
        object_result = await _verify_object(recording.object_key, recording.size_bytes)
        if recording.checksum_sha256 and object_result.get("checksum_sha256"):
            object_result["checksum_matches_database"] = (
                recording.checksum_sha256 == object_result["checksum_sha256"]
            )
        objects.append(
            {
                "type": recording.track,
                "database_checksum_sha256": recording.checksum_sha256,
                **object_result,
            }
        )
    if calm_key:
        objects.append(
            {
                "type": "calm_scoring",
                "database_checksum_sha256": calm_metadata.get("checksum_sha256"),
                **await _verify_object(calm_key, calm_metadata.get("size_bytes")),
            }
        )

    run_mode = getattr(run, "mode", None)
    expected_audio = bool(
        recordings
        or (
            RECORD_CALLS
            and run.is_completed
            and run_mode not in {"textchat", "chat"}
        )
    )
    expected_artifacts = expected_audio or calm_expected
    transcript_status = _status(
        exists=bool(run.full_transcript or transcript_key), expected=transcript_expected
    )
    audio_found = any(
        item["type"] != "transcript" and item["status"] == "verified"
        for item in objects
    )
    minio_configured = run.storage_backend == StorageBackend.MINIO.value
    minio_status = "not_configured"
    if minio_configured:
        if not objects:
            minio_status = "pending" if expected_artifacts else "not_expected"
        elif expected_artifacts and not audio_found and not any(item["type"] == "calm_scoring" and item["status"] == "verified" for item in objects):
            minio_status = "missing"
        elif all(item["status"] == "verified" for item in objects):
            minio_status = "verified"
        else:
            minio_status = "missing" if objects else (
                "pending" if expected_artifacts else "not_expected"
            )

    return {
        "run_id": run_id,
        "postgres": {
            "status": "verified",
            "run_exists": True,
            "transcript_exists": bool(run.full_transcript or transcript_key),
            "transcript_status": transcript_status,
            "calm_scoring_status": (
                "verified" if any(item["type"] == "calm_scoring" and item["status"] == "verified" for item in objects)
                else "missing" if calm_expected else "not_expected"
            ),
            "utterance_count": len(utterances),
            "recording_metadata_exists": bool(recordings),
            "recording_metadata_count": len(recordings),
            "storage_backend": run.storage_backend,
            "workflow_id": run.workflow_id,
            "organization_id": organization_id,
        },
        "minio": {
            "status": minio_status,
            "configured": minio_configured,
            "expected_audio": expected_audio,
            "audio_found": audio_found,
            "objects_found": sum(item["status"] == "verified" for item in objects),
            "objects": objects,
        },
        "aws": {"status": _s3_status(expected_s3, replications)},
    }
