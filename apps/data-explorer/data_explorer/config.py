"""Configuration for the isolated, read-only Data Explorer service."""

from __future__ import annotations

from dataclasses import dataclass
import os


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be configured")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str
    admin_token: str | None
    local_test_mode: bool
    audit_log_path: str
    s3_bucket: str | None
    s3_region: str
    s3_endpoint_url: str | None
    object_store_access_key: str | None
    object_store_secret_key: str | None
    presign_expiry_seconds: int
    package_max_bytes: int
    # The production replication destination can intentionally keep the
    # logical MinIO key below a separate AWS prefix (for example ``aws/``).
    # This is a key prefix, not a bucket name, and is never derived from a
    # local MinIO endpoint.
    s3_key_prefix: str | None = None

    @classmethod
    def from_environment(cls) -> "Settings":
        # This intentionally uses a distinct URL. Deployment must point it at a
        # PostgreSQL role granted SELECT only, never Dograh's application role.
        local_test_mode = os.getenv("DATA_EXPLORER_LOCAL_TEST_MODE", "false").lower() == "true"
        admin_token = os.getenv("DATA_EXPLORER_ADMIN_TOKEN", "").strip() or None
        if not admin_token and not local_test_mode:
            raise RuntimeError("DATA_EXPLORER_ADMIN_TOKEN must be configured outside localhost test mode")
        aws_bucket = (
            os.getenv("DATA_EXPLORER_S3_BUCKET")
            or os.getenv("AWS_RECORDINGS_BUCKET")
            or os.getenv("S3_BUCKET")
        )
        # An explicit AWS/S3 bucket must never inherit local MinIO endpoint or
        # credentials. With endpoint_url omitted, aioboto3 uses AWS endpoint
        # resolution plus its default IAM role/profile/SSO credential chain.
        endpoint_url = (
            os.getenv("DATA_EXPLORER_OBJECT_STORE_ENDPOINT_URL")
            or os.getenv("S3_ENDPOINT_URL")
            or (None if aws_bucket else _endpoint(os.getenv("MINIO_ENDPOINT")))
        )
        return cls(
            database_url=_required("DATA_EXPLORER_DATABASE_READONLY_URL"),
            admin_token=admin_token,
            local_test_mode=local_test_mode,
            audit_log_path=os.getenv("DATA_EXPLORER_AUDIT_LOG_PATH", "/var/log/calmos-data-explorer/audit.jsonl"),
            s3_bucket=aws_bucket or os.getenv("MINIO_BUCKET"),
            s3_region=os.getenv("AWS_REGION") or os.getenv("S3_REGION", "eu-west-2"),
            s3_endpoint_url=endpoint_url,
            # These are deliberately MinIO-specific local-development values.
            # AWS authentication always remains the SDK default provider chain.
            object_store_access_key=(None if aws_bucket else (os.getenv("DATA_EXPLORER_MINIO_ACCESS_KEY") or os.getenv("MINIO_ACCESS_KEY") or None)),
            object_store_secret_key=(None if aws_bucket else (os.getenv("DATA_EXPLORER_MINIO_SECRET_KEY") or os.getenv("MINIO_SECRET_KEY") or None)),
            presign_expiry_seconds=int(os.getenv("DATA_EXPLORER_PRESIGN_EXPIRY_SECONDS", "300")),
            package_max_bytes=int(os.getenv("DATA_EXPLORER_PACKAGE_MAX_BYTES", "104857600")),
            s3_key_prefix=(os.getenv("DATA_EXPLORER_S3_PREFIX") or os.getenv("AWS_S3_PREFIX") or "").strip("/") or None,
        )


def _endpoint(value: str | None) -> str | None:
    if not value:
        return None
    return value if "://" in value else f"http://{value}"
