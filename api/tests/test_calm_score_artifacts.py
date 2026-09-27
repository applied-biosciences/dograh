"""Regression tests for the authorized CALM object-history contract."""

import json
from types import SimpleNamespace

from api.services import workflow_run_artifacts


async def test_calm_snapshot_keeps_authorized_prompt_and_is_replicated(monkeypatch):
    written: dict[str, object] = {}

    async def get_run(_run_id):
        return SimpleNamespace(id=31)

    async def upload(run_id, payload, object_key, label):
        written.update(run_id=run_id, payload=json.loads(payload), object_key=object_key, label=label)
        return True

    async def replicate(run_id, audit, *, job_suffix):
        assert run_id == 31
        assert audit["scores"]["objects"][0]["object_key"] == written["object_key"]
        assert job_suffix == "calm-sakinah-sakinah-1"
        return {"status": "queued"}

    monkeypatch.setattr(workflow_run_artifacts.db_client, "get_workflow_run_by_id", get_run)
    monkeypatch.setattr(workflow_run_artifacts, "_upload_bytes", upload)
    monkeypatch.setattr(workflow_run_artifacts, "schedule_s3_replication", replicate)
    monkeypatch.setattr(workflow_run_artifacts, "get_current_storage_backend", lambda: SimpleNamespace(value="minio"))
    result = await workflow_run_artifacts.persist_calm_score_snapshot(31, [{
        "turn_id": "sakinah-1", "role": "sakinah", "scoring_method": "llm_evaluation", "scored_at": "2026-09-27T12:00:00Z",
        "calm_scores": {"response_quality.empathy": 8}, "calm_confidence": {"response_quality.empathy": 9},
        "trend": {"response_quality.empathy": "improving"}, "private_utterance": "must not be written", "prompt": "must not be written", "api_key": "must not be written",
        "engineered_prompt": "authorized evaluation trace",
    }], role="sakinah")
    assert result["status"] == "success"
    assert written["object_key"] == "scores/workflow-run-31/calm-sakinah-turn-sakinah-1.json"
    assert "must not be written" not in json.dumps(written["payload"])
    assert written["payload"]["artifact_kind"] == "dograh-calm-turn-history/v2"
    assert written["payload"]["scored_at"] == "2026-09-27T12:00:00Z"
    assert written["payload"]["engineered_prompt"] == "authorized evaluation trace"
