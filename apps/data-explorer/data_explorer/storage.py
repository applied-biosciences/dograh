"""Private object-store access using the AWS SDK default credential chain."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath

import aioboto3
from botocore.exceptions import BotoCoreError, ClientError

from .config import Settings


_BROWSABLE_PREFIXES = (
    "recordings/",
    "transcripts/",
    "calm-scoring/",
    "scores/",
    "deployment-smoke/",
)


def browsable_key(key: str) -> str:
    """Validate an object key accepted by the AWS storage-browser endpoints."""
    normalized = key.strip().lstrip("/")
    if ".." in normalized.split("/") or not normalized.startswith(_BROWSABLE_PREFIXES):
        raise ObjectNotAvailable("Object key is outside the Explorer storage scope")
    return normalized


class ObjectNotAvailable(Exception):
    pass


def safe_filename(value: str) -> str:
    """Return a single, portable filename; never trust an object key as a path."""
    name = PurePath(value.replace("\\", "/")).name
    safe = "".join(char for char in name if char.isalnum() or char in "._-")
    return safe or "file"


@dataclass(frozen=True)
class ObjectMetadata:
    content_type: str | None
    size_bytes: int | None


class S3ObjectStore:
    def __init__(self, settings: Settings):
        self.bucket = settings.s3_bucket
        self.region = settings.s3_region
        self.endpoint_url = settings.s3_endpoint_url
        self.expiry = settings.presign_expiry_seconds
        self.key_prefix = settings.s3_key_prefix
        # Supplying credentials is only for a configured S3-compatible local
        # store such as MinIO. With normal AWS settings both values are None,
        # which leaves boto3/aioboto3 on its default role/profile/SSO chain.
        session_kwargs = {}
        if settings.s3_endpoint_url and settings.object_store_access_key and settings.object_store_secret_key:
            session_kwargs = {
                "aws_access_key_id": settings.object_store_access_key,
                "aws_secret_access_key": settings.object_store_secret_key,
            }
        self.session = aioboto3.Session(**session_kwargs)

    def _client_kwargs(self) -> dict:
        values = {"region_name": self.region}
        if self.endpoint_url:
            values["endpoint_url"] = self.endpoint_url
        return values

    def _require_bucket(self) -> str:
        if not self.bucket:
            raise ObjectNotAvailable("Object storage is not configured")
        return self.bucket

    def _object_key(self, key: str) -> str:
        """Map a logical Dograh object key onto its configured S3 prefix."""
        normalized = key.lstrip("/")
        if not self.key_prefix or normalized.startswith(f"{self.key_prefix}/"):
            return normalized
        return f"{self.key_prefix}/{normalized}"

    def _logical_key(self, key: str) -> str:
        if self.key_prefix and key.startswith(f"{self.key_prefix}/"):
            return key[len(self.key_prefix) + 1:]
        return key

    @staticmethod
    def _validate_browsable_key(key: str) -> str:
        return browsable_key(key)

    async def list_objects(
        self, *, prefix: str = "recordings/", continuation_token: str | None = None, page_size: int = 50
    ) -> dict:
        """List a bounded, allowlisted page of private bucket objects."""
        logical_prefix = self._validate_browsable_key(prefix)
        if continuation_token and len(continuation_token) > 4096:
            raise ObjectNotAvailable("Invalid object listing cursor")
        try:
            request = {
                "Bucket": self._require_bucket(),
                "Prefix": self._object_key(logical_prefix),
                "MaxKeys": min(100, max(1, page_size)),
            }
            if continuation_token:
                request["ContinuationToken"] = continuation_token
            async with self.session.client("s3", **self._client_kwargs()) as client:
                response = await client.list_objects_v2(**request)
            return {
                "items": [
                    {
                        "object_key": self._logical_key(item["Key"]),
                        "file_name": safe_filename(item["Key"]),
                        "size_bytes": item.get("Size"),
                        "last_modified": item["LastModified"].isoformat() if item.get("LastModified") else None,
                    }
                    for item in response.get("Contents", [])
                ],
                "next_cursor": response.get("NextContinuationToken"),
            }
        except (BotoCoreError, ClientError) as exc:
            raise ObjectNotAvailable("Bucket objects are unavailable") from exc

    async def metadata(self, key: str) -> ObjectMetadata:
        try:
            async with self.session.client("s3", **self._client_kwargs()) as client:
                result = await client.head_object(Bucket=self._require_bucket(), Key=self._object_key(key))
            return ObjectMetadata(result.get("ContentType"), result.get("ContentLength"))
        except (BotoCoreError, ClientError) as exc:
            raise ObjectNotAvailable("Associated object is unavailable") from exc

    async def presigned_download(self, key: str, filename: str, *, inline: bool) -> str:
        try:
            async with self.session.client("s3", **self._client_kwargs()) as client:
                # A signature can be generated for any arbitrary key. Check
                # first so callers never receive a plausible-looking URL for
                # a stale primary-store reference that was not replicated.
                object_key = self._object_key(key)
                await client.head_object(Bucket=self._require_bucket(), Key=object_key)
                return await client.generate_presigned_url(
                    "get_object",
                    Params={
                        "Bucket": self._require_bucket(),
                        "Key": object_key,
                        "ResponseContentDisposition": f'{"inline" if inline else "attachment"}; filename="{safe_filename(filename)}"',
                    },
                    ExpiresIn=self.expiry,
                )
        except (BotoCoreError, ClientError) as exc:
            raise ObjectNotAvailable("Associated object is unavailable") from exc

    async def bytes(self, key: str, max_bytes: int) -> bytes:
        try:
            async with self.session.client("s3", **self._client_kwargs()) as client:
                response = await client.get_object(Bucket=self._require_bucket(), Key=self._object_key(key))
                size = response.get("ContentLength")
                if size is not None and size > max_bytes:
                    raise ObjectNotAvailable("Object exceeds package size limit")
                body = await response["Body"].read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise ObjectNotAvailable("Object exceeds package size limit")
                return body
        except ObjectNotAvailable:
            raise
        except (BotoCoreError, ClientError) as exc:
            raise ObjectNotAvailable("Associated object is unavailable") from exc
