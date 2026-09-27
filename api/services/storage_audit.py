"""Read-only verification of workflow-run persistence and object storage."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from pathlib import Path
from typing import Any

from api.constants import (
    AWS_RECORDINGS_BUCKET,
    AWS_REGION,
    ENABLE_AWS_S3_SECONDARY,
    RECORD_CALLS,
)
from api.db import db_client
from api.enums import StorageBackend
from api.services.filesystem.s3 import S3FileSystem
from api.services.s3_secondary_replication import _destination_key
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
            "aws": {"status": "missing", "objects_found": 0, "objects": []},
        }

    utterances = await db_client.get_utterances_for_run(run_id)
    recordings = await db_client.get_call_recordings_for_run(run_id)
    transcript_key = run.transcript_object_key or run.transcript_url
    transcript_expected = bool(transcript_key or run.full_transcript)

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

    run_mode = getattr(run, "mode", None)
    expected_audio = bool(
        recordings
        or (
            RECORD_CALLS
            and run.is_completed
            and run_mode not in {"textchat", "chat"}
        )
    )
    # A run records the primary backend that wrote it.  AWS primary and AWS
    # secondary are distinct: a primary-S3 run has already been checked above
    # through ``storage_fs`` and must not be reported as an unused secondary.
    primary_is_s3 = run.storage_backend == StorageBackend.S3.value
    aws_objects: list[dict[str, Any]] = []
    aws_status = "not_configured"
    aws_role = None
    if primary_is_s3:
        aws_role = "primary"
        aws_objects = [dict(item) for item in objects]
        if not aws_objects:
            aws_status = "pending" if expected_audio else "not_expected"
        elif all(item["status"] == "verified" for item in aws_objects):
            aws_status = "verified"
        else:
            aws_status = "missing"
    elif ENABLE_AWS_S3_SECONDARY and AWS_RECORDINGS_BUCKET:
        aws_role = "secondary"
        replication_rows = await db_client.get_artifact_replications_for_run(run_id)
        if replication_rows:
            aws_fs = S3FileSystem(
                bucket_name=AWS_RECORDINGS_BUCKET, region_name=AWS_REGION
            )
            for row in replication_rows:
                key = row.s3_object_key or _destination_key(row.primary_object_key)
                try:
                    metadata = await asyncio.wait_for(
                        aws_fs.aget_file_metadata(key), timeout=5
                    )
                    exists = metadata is not None
                except Exception:
                    metadata = None
                    exists = False
                aws_objects.append(
                    {
                        "type": row.artifact_type,
                        "key": key,
                        "status": "verified" if exists else "missing",
                        "replication_status": row.replication_status,
                        "size_bytes": metadata.get("size") if metadata else None,
                    }
                )
            if aws_objects and all(item["status"] == "verified" for item in aws_objects):
                aws_status = "verified"
            elif any(item["status"] == "pending" for item in aws_objects):
                aws_status = "pending"
            else:
                aws_status = "missing"
        else:
            # Older runs may predate the replication index. Still verify known
            # transcript/recording keys directly instead of reporting a
            # configured AWS destination as unused without checking it.
            known_objects = [item for item in objects if item.get("key")]
            if known_objects:
                aws_fs = S3FileSystem(
                    bucket_name=AWS_RECORDINGS_BUCKET, region_name=AWS_REGION
                )
                for item in known_objects:
                    key = _destination_key(item["key"])
                    try:
                        metadata = await asyncio.wait_for(
                            aws_fs.aget_file_metadata(key), timeout=5
                        )
                    except Exception:
                        metadata = None
                    aws_objects.append(
                        {
                            "type": item["type"],
                            "key": key,
                            "status": "verified" if metadata else "missing",
                            "size_bytes": metadata.get("size") if metadata else None,
                        }
                    )
                aws_status = (
                    "verified"
                    if all(item["status"] == "verified" for item in aws_objects)
                    else "missing"
                )
            else:
                aws_status = "pending" if objects else "not_expected"

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
            minio_status = "pending" if expected_audio else "not_expected"
        elif expected_audio and not audio_found:
            minio_status = "missing"
        elif all(item["status"] == "verified" for item in objects):
            minio_status = "verified"
        else:
            minio_status = "missing" if objects else (
                "pending" if expected_audio else "not_expected"
            )

    return {
        "run_id": run_id,
        "postgres": {
            "status": "verified",
            "run_exists": True,
            "transcript_exists": bool(run.full_transcript or transcript_key),
            "transcript_status": transcript_status,
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
        "aws": {
            "status": aws_status,
            "role": aws_role,
            "bucket": (
                getattr(storage_fs, "bucket_name", None)
                if primary_is_s3
                else (AWS_RECORDINGS_BUCKET if ENABLE_AWS_S3_SECONDARY else None)
            ),
            "objects_found": sum(item["status"] == "verified" for item in aws_objects),
            "objects": aws_objects,
        },
    }
