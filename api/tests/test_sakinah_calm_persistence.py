import json

import pytest

from api.services.sakinah import calm_persistence


@pytest.mark.asyncio
async def test_calm_progress_writes_score_only_snapshot_to_active_storage(monkeypatch):
    class _Storage:
        saved = None

        async def acreate_file_from_bytes(self, key, value):
            self.saved = (key, value)
            return True

    storage = _Storage()
    monkeypatch.setattr("api.services.storage.storage_fs", storage)

    artifact = await calm_persistence.persist_calm_progress(
        session_id="session-1",
        calm_turns=[
            {
                "turn_id": "turn-1",
                "calm_scores": {"anxiety": {"score": 6}},
                "prompt_sent_to_llm": "must never be persisted",
                "utterance_verbatim": "must never be persisted",
            }
        ],
    )

    assert artifact is not None
    assert artifact["object_key"] == "sakinah/calm/session-1.json"
    saved = json.loads(storage.saved[1])
    assert saved["calm_turns"][0]["calm_scores"] == {"anxiety": {"score": 6}}
    assert "prompt_sent_to_llm" not in saved["calm_turns"][0]
    assert "utterance_verbatim" not in saved["calm_turns"][0]


@pytest.mark.asyncio
async def test_calm_progress_backup_failure_is_non_fatal(monkeypatch):
    class _Storage:
        async def acreate_file_from_bytes(self, _key, _value):
            raise RuntimeError("storage unavailable")

    monkeypatch.setattr("api.services.storage.storage_fs", _Storage())

    assert await calm_persistence.persist_calm_progress(
        session_id="session-2", calm_turns=[]
    ) is None
