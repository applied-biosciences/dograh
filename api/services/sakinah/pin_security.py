"""Pure PIN policy and hashing helpers for Sakinah identity flows."""

from __future__ import annotations

import bcrypt

from api.constants import SAKINAH_PIN_LENGTH


def validate_pin(value: str | None) -> str:
    """Validate a configured numeric PIN without returning sensitive errors."""
    if not isinstance(value, str) or len(value) != SAKINAH_PIN_LENGTH:
        raise ValueError("invalid pin")
    if not value.isascii() or not value.isdigit():
        raise ValueError("invalid pin")
    return value


def hash_pin(value: str) -> tuple[str, str]:
    pin = validate_pin(value)
    salt = bcrypt.gensalt()
    digest = bcrypt.hashpw(pin.encode("ascii"), salt)
    # The salt is metadata only; bcrypt's full digest remains the verifier.
    return digest.decode("ascii"), salt.decode("ascii")


def verify_pin(value: str, digest: str) -> bool:
    try:
        pin = validate_pin(value)
        return bcrypt.checkpw(pin.encode("ascii"), digest.encode("ascii"))
    except (TypeError, ValueError, UnicodeError):
        return False
