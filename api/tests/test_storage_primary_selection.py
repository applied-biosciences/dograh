from api import constants
from api.enums import StorageBackend


def test_legacy_s3_switch_selects_s3_primary_for_existing_deployments(monkeypatch):
    monkeypatch.setattr(constants, "LEGACY_ENABLE_AWS_S3", True)
    monkeypatch.setattr(constants, "ENABLE_AWS_S3_PRIMARY", False)

    assert StorageBackend.get_current_backend() is StorageBackend.S3


def test_explicit_s3_primary_opt_in_is_still_supported(monkeypatch):
    monkeypatch.setattr(constants, "ENABLE_AWS_S3_PRIMARY", True)

    assert StorageBackend.get_current_backend() is StorageBackend.S3
