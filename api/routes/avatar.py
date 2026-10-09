"""SpatialReal avatar session endpoint.

Mints a short-lived AvatarKit session token so the browser can drive the
SpatialReal avatar in SDK mode. The SpatialReal API key never leaves the
backend; the client only receives the public app id, the avatar id, and the
session token (max 24h validity, capped by SpatialReal).

Token exchange (per the avatarkit server SDK):
POST {console_endpoint}/session-tokens with header X-Api-Key and body
{"expireAt": <unix seconds>} -> {"sessionToken": "..."}.
"""

import time
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from api.constants import (
    SPATIALREAL_API_KEY,
    SPATIALREAL_APP_ID,
    SPATIALREAL_AVATAR_ID,
    SPATIALREAL_CHARACTER_ENDPOINT,
    SPATIALREAL_CONSOLE_ENDPOINT,
    SPATIALREAL_TOKEN_TTL,
)
from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.avatar import resolve_avatar_settings

router = APIRouter(prefix="/avatar", tags=["avatar"])

SESSION_TOKEN_PATH = "/session-tokens"

# Org-configuration key holding the user-imported avatars. Built-in avatars
# live in code (below) and are merged in on read, so correcting a built-in id
# is a code change and never requires DB surgery.
AVATAR_LIBRARY_KEY = "avatar_library"

BUILTIN_AVATARS: list[dict] = [
    {"avatar_id": "9ac36877-6a37-44c0-8f74-f682752b1346", "name": "Saudi Male"},
    {"avatar_id": "d5211078-994b-4346-91e2-fa9c77b71bbe", "name": "British Female"},
    {"avatar_id": "02f297a8-8bc0-49a5-b690-1970183dc839", "name": "Saudi Female"},
]


class AvatarSessionResponse(BaseModel):
    """Client-side configuration for an AvatarKit SDK-mode session."""

    app_id: str
    avatar_id: str
    session_token: str
    expires_at: int


class AvatarConfigResponse(BaseModel):
    """Whether the avatar feature is configured, and in which driving mode."""

    enabled: bool
    mode: str  # "sdk" | "host" | "off"


class AvatarLibraryEntry(BaseModel):
    """One selectable avatar: a SpatialReal character id plus a display name."""

    avatar_id: str
    name: str
    builtin: bool = False
    image_url: str | None = None


class AvatarLibraryResponse(BaseModel):
    """The org's avatar library and the deployment-default avatar id."""

    avatars: list[AvatarLibraryEntry]
    default_avatar_id: str


class AvatarLibraryAddRequest(BaseModel):
    """Import one avatar by its SpatialReal Studio id."""

    avatar_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=80)
    image_url: str | None = Field(default=None, max_length=2000)

    @field_validator("avatar_id", "name", "image_url", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("image_url")
    @classmethod
    def _http_only(cls, value: str | None) -> str | None:
        if not value:
            return None
        if not value.startswith(("http://", "https://")):
            raise ValueError("Image URL must start with http:// or https://")
        return value


def _is_configured() -> bool:
    return bool(SPATIALREAL_APP_ID and SPATIALREAL_API_KEY and SPATIALREAL_AVATAR_ID)


async def _resolve_settings_for_run(
    workflow_run_id: int | None, user: UserModel
) -> dict:
    """Effective avatar settings — the run's pinned workflow config over env.

    Uses the same resolution as the pipeline (run definition's
    workflow_configurations), so browser and backend always agree.
    """
    run_configs = None
    if workflow_run_id is not None:
        workflow_run = await db_client.get_workflow_run(
            workflow_run_id, organization_id=user.selected_organization_id
        )
        if not workflow_run:
            raise HTTPException(status_code=404, detail="Workflow run not found")
        if workflow_run.definition is not None:
            run_configs = workflow_run.definition.workflow_configurations
    return resolve_avatar_settings(run_configs)


@router.get("/config", response_model=AvatarConfigResponse)
async def get_avatar_config(
    workflow_run_id: int | None = Query(default=None),
    user: UserModel = Depends(get_user),
) -> AvatarConfigResponse:
    settings = await _resolve_settings_for_run(workflow_run_id, user)
    return AvatarConfigResponse(enabled=settings["enabled"], mode=settings["mode"])


@router.post("/session", response_model=AvatarSessionResponse)
async def create_avatar_session(
    workflow_run_id: int | None = Query(default=None),
    user: UserModel = Depends(get_user),
) -> AvatarSessionResponse:
    """Mint a SpatialReal session token for the current user."""
    if not _is_configured():
        raise HTTPException(status_code=503, detail="Avatar engine not configured")

    settings = await _resolve_settings_for_run(workflow_run_id, user)
    if not settings["enabled"]:
        raise HTTPException(
            status_code=503, detail="Avatar disabled for this workflow"
        )

    expires_at = int(time.time()) + SPATIALREAL_TOKEN_TTL
    session_token = await mint_spatialreal_token(expires_at)

    logger.debug(f"Minted SpatialReal session token for user {user.id}")
    return AvatarSessionResponse(
        app_id=SPATIALREAL_APP_ID,
        avatar_id=await resolve_servable_avatar_id(settings["avatar_id"]),
        session_token=session_token,
        expires_at=expires_at,
    )


def _load_store(raw: object) -> tuple[list[dict], set[str]]:
    """Normalize the stored config value.

    Accepts the legacy bare list of user avatars as well as the current
    ``{"avatars": [...], "hidden_builtins": [...]}`` shape.
    """
    if isinstance(raw, dict):
        avatars = [item for item in raw.get("avatars") or [] if item.get("avatar_id")]
        return avatars, set(raw.get("hidden_builtins") or [])
    if isinstance(raw, list):
        return [item for item in raw if item.get("avatar_id")], set()
    return [], set()


def _dump_store(avatars: list[dict], hidden_builtins: set[str]) -> dict:
    return {"avatars": avatars, "hidden_builtins": sorted(hidden_builtins)}


def _merged_library(
    user_avatars: list[dict], hidden_builtins: set[str] | None = None
) -> list[AvatarLibraryEntry]:
    """Built-ins first (minus deleted ones), then user imports, de-duplicated."""
    hidden = hidden_builtins or set()
    entries = [
        AvatarLibraryEntry(**item, builtin=True)
        for item in BUILTIN_AVATARS
        if item["avatar_id"] not in hidden
    ]
    seen = {b["avatar_id"] for b in BUILTIN_AVATARS}
    for item in user_avatars:
        avatar_id = item.get("avatar_id")
        if not avatar_id or avatar_id in seen:
            continue
        seen.add(avatar_id)
        entries.append(
            AvatarLibraryEntry(
                avatar_id=avatar_id,
                name=item.get("name") or avatar_id,
                image_url=item.get("image_url"),
            )
        )
    return entries


async def _verify_avatar_exists(avatar_id: str) -> None:
    """Check the id against SpatialReal's public character endpoint.

    Raises HTTPException with a user-facing message when the id is malformed,
    unknown to SpatialReal, or the engine is unreachable.
    """
    try:
        uuid.UUID(avatar_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                "That doesn't look like an avatar ID — use the copy button on "
                "the avatar card in SpatialReal Studio."
            ),
        ) from None

    endpoint = f"{SPATIALREAL_CHARACTER_ENDPOINT.rstrip('/')}/{avatar_id}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(endpoint)
        data = response.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.error(f"SpatialReal character lookup failed for {avatar_id}: {e}")
        raise HTTPException(
            status_code=502, detail="Could not reach SpatialReal to verify the avatar ID"
        ) from e

    # SpatialReal reports failures in an `errors` array (HTTP status is not
    # reliable); a valid character always carries its characterId.
    if data.get("errors") or not data.get("characterId"):
        logger.info(
            f"SpatialReal rejected avatar id {avatar_id}: {str(data)[:300]}"
        )
        raise HTTPException(
            status_code=404,
            detail=(
                "SpatialReal has no avatar with this ID. Copy the full ID from "
                "SpatialReal Studio and make sure the avatar shows Completed."
            ),
        )


# Session-time existence checks, cached so repeated calls with the same
# avatar don't re-hit SpatialReal. (avatar_id -> (exists, checked_at))
_EXISTS_CACHE: dict[str, tuple[bool, float]] = {}
_EXISTS_CACHE_TTL = 600.0


async def resolve_servable_avatar_id(avatar_id: str | None) -> str:
    """Return an avatar id that the serving backend actually has.

    A workflow can point at an avatar that SpatialReal's serving API doesn't
    know (deleted in Studio, or created on a backend this deployment isn't
    connected to). Serving that id would leave the call with a blank avatar
    panel, so fall back to the deployment default instead.
    """
    candidate = avatar_id or SPATIALREAL_AVATAR_ID
    if not candidate or candidate == SPATIALREAL_AVATAR_ID:
        return candidate

    now = time.time()
    cached = _EXISTS_CACHE.get(candidate)
    if cached and now - cached[1] < _EXISTS_CACHE_TTL:
        exists = cached[0]
    else:
        endpoint = f"{SPATIALREAL_CHARACTER_ENDPOINT.rstrip('/')}/{candidate}"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(endpoint)
            data = response.json()
            exists = bool(data.get("characterId")) and not data.get("errors")
        except (httpx.HTTPError, ValueError):
            # Can't verify — serve the configured id rather than silently
            # overriding the operator's choice on a transient failure.
            return candidate
        _EXISTS_CACHE[candidate] = (exists, now)

    if exists:
        return candidate
    logger.warning(
        f"Avatar {candidate} not found on the serving backend; "
        f"falling back to default {SPATIALREAL_AVATAR_ID!r}"
    )
    return SPATIALREAL_AVATAR_ID


@router.get("/library", response_model=AvatarLibraryResponse)
async def get_avatar_library(
    user: UserModel = Depends(get_user),
) -> AvatarLibraryResponse:
    """List the org's selectable avatars (built-ins plus imports)."""
    raw = await db_client.get_configuration_value(
        user.selected_organization_id, AVATAR_LIBRARY_KEY, default=[]
    )
    user_avatars, hidden = _load_store(raw)
    return AvatarLibraryResponse(
        avatars=_merged_library(user_avatars, hidden),
        default_avatar_id=SPATIALREAL_AVATAR_ID,
    )


@router.post("/library", response_model=AvatarLibraryResponse)
async def add_avatar_to_library(
    request: AvatarLibraryAddRequest,
    user: UserModel = Depends(get_user),
) -> AvatarLibraryResponse:
    """Import an avatar by SpatialReal id after verifying it exists.

    Re-importing an id that was already user-added updates its name/image
    (no re-verification). Re-importing a deleted built-in restores it;
    visible built-ins are fixed and return unchanged.
    """
    organization_id = user.selected_organization_id
    raw = await db_client.get_configuration_value(
        organization_id, AVATAR_LIBRARY_KEY, default=[]
    )
    user_avatars, hidden = _load_store(raw)

    if any(b["avatar_id"] == request.avatar_id for b in BUILTIN_AVATARS):
        if request.avatar_id in hidden:
            hidden.discard(request.avatar_id)
            await db_client.upsert_configuration(
                organization_id, AVATAR_LIBRARY_KEY, _dump_store(user_avatars, hidden)
            )
        return AvatarLibraryResponse(
            avatars=_merged_library(user_avatars, hidden),
            default_avatar_id=SPATIALREAL_AVATAR_ID,
        )

    entry: dict = {"avatar_id": request.avatar_id, "name": request.name}
    if request.image_url:
        entry["image_url"] = request.image_url

    if any(item["avatar_id"] == request.avatar_id for item in user_avatars):
        updated = [
            entry if item["avatar_id"] == request.avatar_id else item
            for item in user_avatars
        ]
    else:
        await _verify_avatar_exists(request.avatar_id)
        updated = user_avatars + [entry]
    await db_client.upsert_configuration(
        organization_id, AVATAR_LIBRARY_KEY, _dump_store(updated, hidden)
    )
    logger.info(
        f"Avatar {request.avatar_id} ({request.name!r}) added to library of "
        f"org {organization_id} by user {user.id}"
    )
    return AvatarLibraryResponse(
        avatars=_merged_library(updated, hidden),
        default_avatar_id=SPATIALREAL_AVATAR_ID,
    )


@router.delete("/library/{avatar_id}", response_model=AvatarLibraryResponse)
async def delete_avatar_from_library(
    avatar_id: str,
    user: UserModel = Depends(get_user),
) -> AvatarLibraryResponse:
    """Remove an avatar from the library.

    User-added avatars are deleted outright; built-ins are hidden and can be
    restored by importing their id again. Workflows still pointing at a
    removed avatar fall back to the deployment default at call time.
    """
    organization_id = user.selected_organization_id
    raw = await db_client.get_configuration_value(
        organization_id, AVATAR_LIBRARY_KEY, default=[]
    )
    user_avatars, hidden = _load_store(raw)

    if any(b["avatar_id"] == avatar_id for b in BUILTIN_AVATARS):
        hidden.add(avatar_id)
    elif any(item["avatar_id"] == avatar_id for item in user_avatars):
        user_avatars = [
            item for item in user_avatars if item["avatar_id"] != avatar_id
        ]
    else:
        raise HTTPException(status_code=404, detail="Avatar not in library")

    await db_client.upsert_configuration(
        organization_id, AVATAR_LIBRARY_KEY, _dump_store(user_avatars, hidden)
    )
    logger.info(
        f"Avatar {avatar_id} removed from library of org {organization_id} "
        f"by user {user.id}"
    )
    return AvatarLibraryResponse(
        avatars=_merged_library(user_avatars, hidden),
        default_avatar_id=SPATIALREAL_AVATAR_ID,
    )


async def mint_spatialreal_token(expires_at: int) -> str:
    """Exchange the API key for a SpatialReal session token.

    Raises HTTPException (502) on any exchange failure; shared by the
    authenticated route above and the public embed avatar endpoints.
    """
    endpoint = SPATIALREAL_CONSOLE_ENDPOINT.rstrip("/") + SESSION_TOKEN_PATH

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                endpoint,
                json={"expireAt": expires_at},
                headers={
                    "X-Api-Key": SPATIALREAL_API_KEY,
                    "Content-Type": "application/json",
                },
            )
    except httpx.HTTPError as e:
        logger.error(f"SpatialReal session token request failed: {e}")
        raise HTTPException(
            status_code=502, detail="Failed to reach avatar engine"
        ) from e

    if response.status_code != 200:
        logger.error(
            f"SpatialReal session token request returned {response.status_code}: "
            f"{response.text[:500]}"
        )
        raise HTTPException(
            status_code=502, detail="Avatar engine rejected token request"
        )

    data = response.json()
    session_token = data.get("sessionToken")
    if not session_token or data.get("errors"):
        logger.error(f"SpatialReal session token response invalid: {str(data)[:500]}")
        raise HTTPException(
            status_code=502, detail="Avatar engine returned invalid token response"
        )

    return session_token
