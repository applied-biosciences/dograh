from types import SimpleNamespace

import pytest

from api.routes import call_history


@pytest.mark.asyncio
async def test_call_replay_returns_short_lived_signed_url_without_storage_details(
    monkeypatch,
):
    class _Storage:
        async def aget_signed_url(self, key, expiration, force_inline):
            assert key == "recordings/2026/09/service-user/call/call.wav"
            assert expiration == 300
            suffix = "inline" if force_inline else "download"
            return f"https://signed.example/replay?mode={suffix}"

    class _DB:
        audit = None

        async def get_call_replay_for_user(self, call_id, **kwargs):
            assert call_id == "call-1"
            assert kwargs["organization_id"] == 7
            return {
                "call_id": call_id,
                "agent_run_id": 42,
                "storage_backend": None,
                "recording_key": "recordings/2026/09/service-user/call/call.wav",
                "transcript": "user: Hello",
                "utterances": [],
            }

        async def record_audit_event(self, **kwargs):
            self.audit = kwargs

    fake_db = _DB()
    monkeypatch.setattr(call_history, "db_client", fake_db)
    monkeypatch.setattr(call_history, "storage_fs", _Storage())
    response = await call_history.get_call_replay(
        "call-1",
        expires_in=300,
        user=SimpleNamespace(selected_organization_id=7, is_superuser=False),
    )
    payload = response.model_dump()
    assert payload["recording_signed_url"].startswith("https://signed.example/")
    assert "recording_key" not in payload
    assert "bucket" not in payload
    assert payload["recordings"][0]["track"] == "mixed"
    assert payload["recordings"][0]["signed_url"].startswith("https://signed.example/")
    assert payload["recording_download_url"].endswith("mode=download")
    assert payload["recordings"][0]["download_url"].endswith("mode=download")
    assert fake_db.audit["event_type"] == "recording_replay_url_issued"
    assert fake_db.audit["event_metadata"]["expires_in"] == 300


@pytest.mark.asyncio
async def test_call_replay_returns_sql_details_when_recording_is_unavailable(monkeypatch):
    class _Storage:
        async def aget_signed_url(self, key, expiration, force_inline):
            assert key == "recordings/2026/09/service-user/call/missing.wav"
            raise OSError("storage unavailable")

    class _DB:
        audit = None

        async def get_call_replay_for_user(self, call_id, **kwargs):
            return {
                "call_id": call_id,
                "agent_run_id": 42,
                "recording_key": "recordings/2026/09/service-user/call/missing.wav",
                "transcript": "user: Hello",
                "utterances": [{"speaker": "user", "transcript": "Hello"}],
                "calm_score": {"current": 4},
            }

        async def record_audit_event(self, **kwargs):
            self.audit = kwargs

    fake_db = _DB()
    monkeypatch.setattr(call_history, "db_client", fake_db)
    monkeypatch.setattr(call_history, "storage_fs", _Storage())
    response = await call_history.get_call_replay(
        "call-1",
        expires_in=300,
        user=SimpleNamespace(selected_organization_id=7, is_superuser=False),
    )

    assert response.recording_signed_url is None
    assert response.recordings == []
    assert response.unavailable_recordings == ["mixed"]
    assert response.transcript == "user: Hello"
    assert response.utterances == [{"speaker": "user", "transcript": "Hello"}]
    assert response.calm_score == {"current": 4}
    assert fake_db.audit["outcome"] == "partial"
    assert fake_db.audit["event_metadata"]["unavailable_tracks"] == ["mixed"]


@pytest.mark.asyncio
async def test_lookup_requires_authorized_run_and_phone_correlation(monkeypatch):
    class _DB:
        audit = None

        async def get_call_replay_for_user(self, run_id, **kwargs):
            assert run_id == "opaque-run-id"
            assert kwargs["phone_number"] == "+44201234"
            assert kwargs["organization_id"] == 7
            assert kwargs["allow_native_run_id"] is True
            return {"call_id": run_id, "agent_run_id": 9, "transcript": "", "utterances": [], "calm_score": {"current": 4}}

        async def record_audit_event(self, **kwargs):
            self.audit = kwargs

    fake_db = _DB()
    monkeypatch.setattr(call_history, "db_client", fake_db)
    response = await call_history.lookup_run_details(
        call_history.RunDetailsLookupRequest(run_id="opaque-run-id", phone_number="+44201234"),
        user=SimpleNamespace(selected_organization_id=7, is_superuser=False),
    )
    assert response.calm_score == {"current": 4}
    assert "44201234" not in response.model_dump_json()
    assert fake_db.audit["resource_id"] == "opaque-run-id"
