"""Tests for the org avatar library endpoints (/avatar/library)."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.routes.avatar as avatar_routes
from api.routes.avatar import BUILTIN_AVATARS, router
from api.services.auth.depends import get_user

FAKE_USER = SimpleNamespace(id=1, selected_organization_id=7)

VALID_NEW_ID = "11111111-2222-4333-8444-555555555555"


class FakeAsyncClient:
    """Stands in for httpx.AsyncClient; serves a canned character response."""

    payload: dict = {}
    calls: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        FakeAsyncClient.calls.append(url)
        payload = FakeAsyncClient.payload
        return SimpleNamespace(json=lambda: payload, status_code=200)


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user] = lambda: FAKE_USER

    store: dict[tuple[int, str], list] = {}

    async def fake_get_configuration_value(organization_id, key, default=None):
        return store.get((organization_id, key), default)

    async def fake_upsert_configuration(organization_id, key, value):
        store[(organization_id, key)] = value

    monkeypatch.setattr(
        avatar_routes.db_client,
        "get_configuration_value",
        fake_get_configuration_value,
    )
    monkeypatch.setattr(
        avatar_routes.db_client, "upsert_configuration", fake_upsert_configuration
    )
    monkeypatch.setattr(avatar_routes, "SPATIALREAL_AVATAR_ID", "avatar-env")
    monkeypatch.setattr(avatar_routes.httpx, "AsyncClient", FakeAsyncClient)
    FakeAsyncClient.payload = {}
    FakeAsyncClient.calls = []

    test_client = TestClient(app)
    test_client.store = store
    return test_client


class TestGetLibrary:
    def test_empty_store_returns_builtins(self, client):
        response = client.get("/avatar/library")
        assert response.status_code == 200
        body = response.json()
        assert body["default_avatar_id"] == "avatar-env"
        assert [a["avatar_id"] for a in body["avatars"]] == [
            b["avatar_id"] for b in BUILTIN_AVATARS
        ]
        assert all(a["builtin"] for a in body["avatars"])

    def test_merges_stored_and_dedupes_builtins(self, client):
        client.store[(7, "avatar_library")] = [
            # Duplicate of a builtin: must not appear twice.
            {"avatar_id": BUILTIN_AVATARS[0]["avatar_id"], "name": "dup"},
            {"avatar_id": VALID_NEW_ID, "name": "Omani Male"},
        ]
        body = client.get("/avatar/library").json()
        ids = [a["avatar_id"] for a in body["avatars"]]
        assert ids.count(BUILTIN_AVATARS[0]["avatar_id"]) == 1
        added = next(a for a in body["avatars"] if a["avatar_id"] == VALID_NEW_ID)
        assert added["name"] == "Omani Male"
        assert added["builtin"] is False


class TestAddAvatar:
    def test_malformed_id_rejected_without_lookup(self, client):
        response = client.post(
            "/avatar/library", json={"avatar_id": "not-a-uuid", "name": "X"}
        )
        assert response.status_code == 400
        assert "copy button" in response.json()["detail"]
        assert FakeAsyncClient.calls == []

    def test_unknown_id_rejected(self, client):
        FakeAsyncClient.payload = {
            "errors": [{"code": "NOT_FOUND", "detail": "avatar not found"}]
        }
        response = client.post(
            "/avatar/library", json={"avatar_id": VALID_NEW_ID, "name": "X"}
        )
        assert response.status_code == 404
        assert "SpatialReal has no avatar" in response.json()["detail"]
        assert client.store == {}

    def test_valid_id_appended_and_returned(self, client):
        FakeAsyncClient.payload = {"characterId": VALID_NEW_ID, "version": "2.1.0"}
        response = client.post(
            "/avatar/library",
            json={"avatar_id": f"  {VALID_NEW_ID}  ", "name": "  Omani Male "},
        )
        assert response.status_code == 200
        body = response.json()
        added = next(a for a in body["avatars"] if a["avatar_id"] == VALID_NEW_ID)
        assert added["name"] == "Omani Male"
        assert added["builtin"] is False
        # Persisted for the org, trimmed.
        assert client.store[(7, "avatar_library")] == [
            {"avatar_id": VALID_NEW_ID, "name": "Omani Male"}
        ]

    def test_reimport_updates_name_and_image(self, client):
        FakeAsyncClient.payload = {"characterId": VALID_NEW_ID}
        first = client.post(
            "/avatar/library", json={"avatar_id": VALID_NEW_ID, "name": "Omani Male"}
        )
        assert first.status_code == 200
        FakeAsyncClient.calls = []

        second = client.post(
            "/avatar/library",
            json={
                "avatar_id": VALID_NEW_ID,
                "name": "renamed",
                "image_url": "https://example.com/omani.jpg",
            },
        )
        assert second.status_code == 200
        # No second SpatialReal lookup; the stored entry is replaced.
        assert FakeAsyncClient.calls == []
        assert client.store[(7, "avatar_library")] == [
            {
                "avatar_id": VALID_NEW_ID,
                "name": "renamed",
                "image_url": "https://example.com/omani.jpg",
            }
        ]
        updated = next(
            a for a in second.json()["avatars"] if a["avatar_id"] == VALID_NEW_ID
        )
        assert updated["name"] == "renamed"
        assert updated["image_url"] == "https://example.com/omani.jpg"

    def test_image_url_must_be_http(self, client):
        response = client.post(
            "/avatar/library",
            json={
                "avatar_id": VALID_NEW_ID,
                "name": "X",
                "image_url": "javascript:alert(1)",
            },
        )
        assert response.status_code == 422

    def test_builtin_import_is_idempotent(self, client):
        response = client.post(
            "/avatar/library",
            json={"avatar_id": BUILTIN_AVATARS[1]["avatar_id"], "name": "dup"},
        )
        assert response.status_code == 200
        assert FakeAsyncClient.calls == []
        assert client.store == {}
        ids = [a["avatar_id"] for a in response.json()["avatars"]]
        assert ids.count(BUILTIN_AVATARS[1]["avatar_id"]) == 1
