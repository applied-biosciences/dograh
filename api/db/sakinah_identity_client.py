"""Persistence for secure Sakinah credentials and identity gates."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import select

from api.constants import (
    SAKINAH_PIN_ENABLED,
    SAKINAH_PIN_LOCKOUT_SECONDS,
    SAKINAH_PIN_MAX_ATTEMPTS,
)
from api.db.base_client import BaseDBClient
from api.db.models import ServiceUserCredentialModel, ServiceUserModel
from api.services.sakinah.pin_security import hash_pin, validate_pin, verify_pin

PIN_CREDENTIAL_TYPE = "sakinah_pin"


@dataclass(frozen=True)
class PinVerificationResult:
    pin_required: bool
    pin_verification_status: str
    identity_access_authorised: bool
    locked: bool = False


class SakinahIdentityClient(BaseDBClient):
    async def has_active_sakinah_pin(
        self, *, organization_id: int, service_user_id: str
    ) -> bool:
        if not SAKINAH_PIN_ENABLED:
            return False
        async with self.async_session() as session:
            result = await session.execute(
                select(ServiceUserCredentialModel.id).where(
                    ServiceUserCredentialModel.organization_id == organization_id,
                    ServiceUserCredentialModel.service_user_id == service_user_id,
                    ServiceUserCredentialModel.credential_type == PIN_CREDENTIAL_TYPE,
                    ServiceUserCredentialModel.status.in_(("active", "locked")),
                )
            )
            return result.scalar_one_or_none() is not None

    async def register_sakinah_pin(
        self, *, organization_id: int, service_user_id: str, pin: str
    ) -> bool:
        if not SAKINAH_PIN_ENABLED:
            return False
        pin = validate_pin(pin)
        pin_hash, pin_salt = hash_pin(pin)
        now = datetime.now(UTC)
        async with self.async_session() as session:
            user = (
                await session.execute(
                    select(ServiceUserModel)
                    .where(
                        ServiceUserModel.id == service_user_id,
                        ServiceUserModel.organization_id == organization_id,
                        ServiceUserModel.status == "active",
                    )
                    .with_for_update()
                )
            ).scalars().first()
            if user is None:
                return False
            credential = (
                await session.execute(
                    select(ServiceUserCredentialModel)
                    .where(
                        ServiceUserCredentialModel.organization_id == organization_id,
                        ServiceUserCredentialModel.service_user_id == service_user_id,
                        ServiceUserCredentialModel.credential_type == PIN_CREDENTIAL_TYPE,
                    )
                    .with_for_update()
                )
            ).scalars().first()
            if credential is None:
                credential = ServiceUserCredentialModel(
                    id=str(uuid.uuid4()),
                    organization_id=organization_id,
                    service_user_id=service_user_id,
                    credential_type=PIN_CREDENTIAL_TYPE,
                    created_at=now,
                )
                session.add(credential)
            credential.pin_hash = pin_hash
            credential.pin_salt = pin_salt
            credential.hash_algorithm = "bcrypt_v1"
            credential.status = "active"
            credential.failed_attempt_count = 0
            credential.locked_until = None
            credential.updated_at = now
            await session.commit()
        logger.bind(event="pin_enrolment_success").info(
            "Sakinah PIN enrolment completed"
        )
        return True

    async def verify_sakinah_pin(
        self, *, organization_id: int, service_user_id: str, pin: str
    ) -> PinVerificationResult:
        if not SAKINAH_PIN_ENABLED:
            return PinVerificationResult(False, "disabled", False)
        now = datetime.now(UTC)
        async with self.async_session() as session:
            credential = (
                await session.execute(
                    select(ServiceUserCredentialModel)
                    .where(
                        ServiceUserCredentialModel.organization_id == organization_id,
                        ServiceUserCredentialModel.service_user_id == service_user_id,
                        ServiceUserCredentialModel.credential_type == PIN_CREDENTIAL_TYPE,
                    )
                    .with_for_update()
                )
            ).scalars().first()
            if credential is None or credential.status not in {"active", "locked"}:
                return PinVerificationResult(True, "unavailable", False)
            if credential.locked_until and credential.locked_until > now:
                logger.bind(event="pin_locked").warning("Sakinah PIN is locked")
                return PinVerificationResult(True, "locked", False, locked=True)
            if credential.locked_until and credential.locked_until <= now:
                credential.status = "active"
                credential.locked_until = None
                credential.failed_attempt_count = 0
            valid = verify_pin(pin, credential.pin_hash)
            if valid:
                credential.failed_attempt_count = 0
                credential.locked_until = None
                credential.status = "active"
                credential.last_verified_at = now
                credential.updated_at = now
                await session.commit()
                logger.bind(event="pin_verification_success").info(
                    "Sakinah PIN verification succeeded"
                )
                return PinVerificationResult(True, "verified", True)

            credential.failed_attempt_count = (credential.failed_attempt_count or 0) + 1
            credential.updated_at = now
            locked = credential.failed_attempt_count >= SAKINAH_PIN_MAX_ATTEMPTS
            if locked:
                credential.status = "locked"
                credential.locked_until = now + timedelta(
                    seconds=SAKINAH_PIN_LOCKOUT_SECONDS
                )
            await session.commit()
        logger.bind(event="pin_locked" if locked else "pin_verification_failed").warning(
            "Sakinah PIN verification was not accepted"
        )
        return PinVerificationResult(
            True, "locked" if locked else "failed", False, locked=locked
        )
