"""Persistence for asynchronous secondary artifact replication."""

from datetime import UTC, datetime

from sqlalchemy import select

from api.db.base_client import BaseDBClient
from api.db.models import ArtifactReplicationStatusModel


class ArtifactReplicationClient(BaseDBClient):
    async def upsert_artifact_replication_status(
        self,
        *,
        run_id: int,
        artifact_type: str,
        primary_backend: str,
        primary_bucket: str | None,
        primary_object_key: str,
        s3_bucket: str | None,
        s3_object_key: str | None,
        checksum_sha256: str | None = None,
        size_bytes: int | None = None,
    ) -> ArtifactReplicationStatusModel:
        """Record a confirmed primary artifact without duplicating its source."""
        async with self.async_session() as session:
            result = await session.execute(
                select(ArtifactReplicationStatusModel).where(
                    ArtifactReplicationStatusModel.run_id == run_id,
                    ArtifactReplicationStatusModel.artifact_type == artifact_type,
                    ArtifactReplicationStatusModel.primary_object_key
                    == primary_object_key,
                )
            )
            row = result.scalars().first()
            if row is None:
                row = ArtifactReplicationStatusModel(
                    run_id=run_id,
                    artifact_type=artifact_type,
                    primary_saved=True,
                    primary_backend=primary_backend,
                    primary_bucket=primary_bucket,
                    primary_object_key=primary_object_key,
                    s3_bucket=s3_bucket,
                    s3_object_key=s3_object_key,
                    checksum_sha256=checksum_sha256,
                    size_bytes=size_bytes,
                    replication_status="pending",
                )
                session.add(row)
            else:
                row.primary_saved = True
                row.primary_backend = primary_backend
                row.primary_bucket = primary_bucket
                row.s3_bucket = s3_bucket
                row.s3_object_key = s3_object_key
                row.checksum_sha256 = checksum_sha256
                row.size_bytes = size_bytes
            await session.commit()
            await session.refresh(row)
            return row

    async def update_artifact_replication_status(
        self,
        *,
        run_id: int,
        primary_object_key: str,
        replication_status: str,
        s3_saved: bool,
        retry_count: int,
        checksum_sha256: str | None = None,
        size_bytes: int | None = None,
        last_error_class: str | None = None,
        uploaded: bool | None = None,
    ) -> ArtifactReplicationStatusModel | None:
        """Persist a replication attempt; no artifact content is stored here."""
        del uploaded  # compatibility with the job result payload
        async with self.async_session() as session:
            result = await session.execute(
                select(ArtifactReplicationStatusModel).where(
                    ArtifactReplicationStatusModel.run_id == run_id,
                    ArtifactReplicationStatusModel.primary_object_key
                    == primary_object_key,
                )
            )
            row = result.scalars().first()
            if row is None:
                return None
            now = datetime.now(UTC)
            row.replication_status = replication_status
            row.s3_saved = s3_saved
            row.retry_count = retry_count
            row.checksum_sha256 = checksum_sha256 or row.checksum_sha256
            row.size_bytes = size_bytes if size_bytes is not None else row.size_bytes
            row.last_error_class = last_error_class
            row.last_attempt_at = now
            row.s3_uploaded_at = now if s3_saved else None
            await session.commit()
            await session.refresh(row)
            return row

    async def get_pending_artifact_replications(
        self, *, limit: int = 100
    ) -> list[ArtifactReplicationStatusModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(ArtifactReplicationStatusModel)
                .where(
                    ArtifactReplicationStatusModel.primary_saved.is_(True),
                    ArtifactReplicationStatusModel.s3_saved.is_(False),
                    ArtifactReplicationStatusModel.replication_status.in_(
                        ("pending", "failed")
                    ),
                )
                .order_by(ArtifactReplicationStatusModel.updated_at.asc())
                .limit(limit)
            )
            return list(result.scalars().all())
