"""Regression coverage for authenticated Sakinah persistence."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from api.routes.sakinah import _wait_for_workflow_artifacts
from api.db.sakinah_persistence_client import _structured_call_scores
from api.enums import CallType, WorkflowRunMode
from api.services.sakinah.simulation import SAKINAH_ROLE


async def _make_user(db_session, slug: str):
    user, _ = await db_session.get_or_create_user_by_provider_id(f"{slug}_user")
    organization, _ = await db_session.get_or_create_organization_by_provider_id(
        f"{slug}_org", user.id
    )
    await db_session.update_user_selected_organization(user.id, organization.id)
    return await db_session.get_user_by_id(user.id)


def _scenario_payload(title: str) -> dict[str, str]:
    return {
        "title": title,
        "mode": "structured",
        "persona": "A person who is hesitant to disclose information.",
        "behaviour": "Answer briefly at first, then disclose more when asked calmly.",
        "age": "34",
        "gender": "Female",
        "language": "English",
        "other_language": "",
        "emotion": "Guarded",
        "communication_style": "Short, cautious answers",
        "initial_information": "I need help with a difficult situation.",
        "hidden_information": "There is additional context to disclose later.",
        "disclosure": "Disclose the hidden information after rapport is built.",
        "background": "Has had a stressful week.",
        "additional_factors": "Avoid assumptions.",
        "notes": "Persistence regression fixture.",
        "freestyle_prompt": "",
    }


async def test_scenario_crud_is_persistent_and_user_scoped(
    test_client_factory, db_session
):
    owner = await _make_user(db_session, "sakinah_persistence_owner")
    outsider = await _make_user(db_session, "sakinah_persistence_outsider")

    async with test_client_factory(owner) as client:
        created = await client.post(
            "/api/v1/sakinah/scenarios", json=_scenario_payload("Persistent scenario")
        )
        assert created.status_code == 200, created.text
        scenario = created.json()
        scenario_id = scenario["id"]

        listed = await client.get("/api/v1/sakinah/scenarios")
        assert [item["id"] for item in listed.json()["scenarios"]] == [scenario_id]

        updated_payload = _scenario_payload("Updated persistent scenario")
        updated_payload["notes"] = "Updated after re-authentication."
        updated = await client.put(
            f"/api/v1/sakinah/scenarios/{scenario_id}", json=updated_payload
        )
        assert updated.status_code == 200
        assert updated.json()["title"] == "Updated persistent scenario"

    async with test_client_factory(outsider) as client:
        assert (await client.get("/api/v1/sakinah/scenarios")).json() == {
            "scenarios": []
        }
        assert (
            await client.put(
                f"/api/v1/sakinah/scenarios/{scenario_id}",
                json=_scenario_payload("Should not be allowed"),
            )
        ).status_code == 404
        assert (
            await client.delete(f"/api/v1/sakinah/scenarios/{scenario_id}")
        ).status_code == 404

    async with test_client_factory(owner) as client:
        assert (await client.get("/api/v1/sakinah/scenarios")).json()["scenarios"][0][
            "notes"
        ] == "Updated after re-authentication."
        assert (
            await client.delete(f"/api/v1/sakinah/scenarios/{scenario_id}")
        ).status_code == 204
        assert (await client.get("/api/v1/sakinah/scenarios")).json() == {
            "scenarios": []
        }


async def test_run_summary_persists_transcript_preview_and_recording_refs(
    test_client_factory, db_session
):
    owner = await _make_user(db_session, "sakinah_run_owner")
    outsider = await _make_user(db_session, "sakinah_run_outsider")
    session_id = str(uuid.uuid4())
    started_at = datetime.now(UTC)

    await db_session.create_sakinah_run(
        session_id=session_id,
        user_id=owner.id,
        agent_id=101,
        run_id=202,
        scenario="A persisted run scenario",
        started_at=started_at,
    )
    completed = await db_session.complete_sakinah_run(
        user_id=owner.id,
        session_id=session_id,
        status="completed",
        ended_at=datetime.now(UTC),
        transcript="[now] user: Hello\n[now] assistant: How can I help?\n",
        conversation=[{"role": SAKINAH_ROLE, "text": "How can I help?"}],
        preview_data={"turns": 1, "summary": "saved"},
        recording_url="https://storage.example/recording.wav",
        transcript_url="https://storage.example/transcript.txt",
        recording_file_reference={"mixed": "recording.wav"},
        timings={"duration_ms": 1200},
    )
    assert completed is not None

    async with test_client_factory(owner) as client:
        response = await client.get(f"/api/v1/sakinah/runs/{session_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["agent_id"] == 101
        assert payload["run_id"] == 202
        assert payload["transcript"].startswith("[now] user: Hello")
        assert payload["conversation"][0]["text"] == "How can I help?"
        assert payload["preview_data"]["summary"] == "saved"
        assert payload["recording_url"].endswith("recording.wav")
        assert payload["recording_file_reference"] == {"mixed": "recording.wav"}

    async with test_client_factory(outsider) as client:
        assert (await client.get("/api/v1/sakinah/runs")).json() == {"runs": []}
        assert (
            await client.get(f"/api/v1/sakinah/runs/{session_id}")
        ).status_code == 404


async def test_scenario_preserves_free_text_other_language(test_client_factory, db_session):
    owner = await _make_user(db_session, "sakinah_other_language")
    payload = _scenario_payload("Welsh-language scenario")
    payload.update({"language": "Other", "other_language": "Welsh"})

    async with test_client_factory(owner) as client:
        response = await client.post("/api/v1/sakinah/scenarios", json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["language"] == "Other"
        assert response.json()["other_language"] == "Welsh"


async def test_end_session_artifact_lookup_waits_for_pipeline_upload():
    delayed_lookup = AsyncMock(
        side_effect=[
            {},
            {"recording_url": "recordings/303.wav", "transcript_url": None},
        ]
    )
    with (
        patch(
            "api.routes.sakinah.db_client.get_workflow_run_artifacts_for_user",
            new=delayed_lookup,
        ),
        patch("api.routes.sakinah.asyncio.sleep", new=AsyncMock()),
    ):
        artifacts = await _wait_for_workflow_artifacts(7, 303)

    assert artifacts == {
        "recording_url": "recordings/303.wav",
        "transcript_url": None,
    }
    assert delayed_lookup.await_count == 2


def test_structured_call_scores_keep_speakers_in_separate_native_runs():
    calm_turns = [
        {
            "turn_id": "sakinah-turn",
            "role": "sakinah",
            "calm_scores": {"response_quality.empathy": 8},
            "calm_confidence": {"response_quality.empathy": 7},
        },
        {
            "turn_id": "caller-turn",
            "role": "service_user",
            "calm_scores": {"anxiety": 6},
            "calm_confidence": {"anxiety": 8},
        },
    ]

    sakinah, _, _ = _structured_call_scores(calm_turns, role="sakinah")
    caller, _, _ = _structured_call_scores(calm_turns, role="service_user")

    assert sakinah["turns"] == [
        {
            "turn_id": "sakinah-turn",
            "role": "sakinah",
            "scoring_method": "unknown",
            "scored_at": None,
            "scores": {"response_quality.empathy": 8},
            "confidence": {"response_quality.empathy": 7},
            "trend": {},
            "significant_changes": {},
        }
    ]
    assert caller["turns"][0]["turn_id"] == "caller-turn"


async def test_run_details_exposes_paired_score_only_timeline_in_org(
    test_client_factory, db_session
):
    """Either native run can present both simulation roles without transcripts."""
    owner = await _make_user(db_session, "sakinah_timeline_owner")
    outsider = await _make_user(db_session, "sakinah_timeline_outsider")
    sakinah_workflow = await db_session.create_workflow(
        "Timeline Sakinah", {}, owner.id, owner.selected_organization_id
    )
    caller_workflow = await db_session.create_workflow(
        "Timeline Service User", {}, owner.id, owner.selected_organization_id
    )
    sakinah_run = await db_session.create_workflow_run(
        "Timeline Sakinah run",
        sakinah_workflow.id,
        WorkflowRunMode.SMALLWEBRTC.value,
        owner.id,
        call_type=CallType.INBOUND,
        organization_id=owner.selected_organization_id,
    )
    caller_run = await db_session.create_workflow_run(
        "Timeline Service User run",
        caller_workflow.id,
        WorkflowRunMode.SMALLWEBRTC.value,
        owner.id,
        call_type=CallType.INBOUND,
        organization_id=owner.selected_organization_id,
    )
    session_id = str(uuid.uuid4())
    await db_session.create_sakinah_run(
        session_id=session_id,
        user_id=owner.id,
        agent_id=sakinah_workflow.id,
        run_id=sakinah_run.id,
        service_user_agent_id=caller_workflow.id,
        service_user_run_id=caller_run.id,
        scenario="Score timeline fixture",
        started_at=datetime.now(UTC),
    )
    await db_session.update_sakinah_run_progress(
        user_id=owner.id,
        session_id=session_id,
        preview_data={},
        calm_turns=[
            {
                "turn_id": "caller-2",
                "role": "service_user",
                "scoring_method": "llm_evaluation",
                "scored_at": "2026-09-27T10:00:02+00:00",
                "calm_scores": {"anxiety_fear": 7},
                "calm_confidence": {"anxiety_fear": 8},
                "trend": {"anxiety_fear": "worsening"},
                "utterance_verbatim": "must not reach the details API",
            },
            {
                "turn_id": "sakinah-1",
                "role": "sakinah",
                "scoring_method": "llm_evaluation",
                "scored_at": "2026-09-27T10:00:01+00:00",
                "calm_scores": {"response_quality.empathy": 9},
                "calm_confidence": {"response_quality.empathy": 8},
                "trend": {"response_quality.empathy": "stable"},
                "prompt_sent_to_llm": "must not reach the details API",
            },
            {
                "turn_id": "caller-1",
                "role": "service_user",
                "scoring_method": "rule_based_calm",
                "scored_at": "2026-09-27T10:00:01+00:00",
                "calm_scores": {"anxiety_fear": 6},
                "calm_confidence": {"anxiety_fear": 7},
            },
        ],
    )

    async with test_client_factory(owner) as client:
        response = await client.get(
            f"/api/v1/workflow/{caller_workflow.id}/runs/{caller_run.id}"
        )
        assert response.status_code == 200, response.text
        timeline = response.json()["calm_score_timeline"]
        assert timeline["session_id"] == session_id
        assert [role["role"] for role in timeline["roles"]] == [
            "sakinah",
            "service_user",
        ]
        caller_turns = timeline["roles"][1]["turns"]
        assert [turn["turn_id"] for turn in caller_turns] == ["caller-1", "caller-2"]
        assert [turn["turn_index"] for turn in caller_turns] == [1, 2]
        assert caller_turns[0]["scores"] == {"anxiety_fear": 6}
        assert "utterance_verbatim" not in response.text
        assert "prompt_sent_to_llm" not in response.text

        # A run ID is not sufficient to bypass either org or workflow scoping.
        wrong_workflow = await client.get(
            f"/api/v1/workflow/{sakinah_workflow.id}/runs/{caller_run.id}"
        )
        assert wrong_workflow.status_code == 404

    async with test_client_factory(outsider) as client:
        assert (
            await client.get(
                f"/api/v1/workflow/{caller_workflow.id}/runs/{caller_run.id}"
            )
        ).status_code == 404


async def test_normal_sakinah_run_persists_role_aware_score_timeline_in_org(
    test_client_factory, db_session
):
    """Browser tests and incoming calls share one run but retain both roles."""
    owner = await _make_user(db_session, "normal_calm_owner")
    outsider = await _make_user(db_session, "normal_calm_outsider")
    workflow = await db_session.create_workflow(
        "Sakinah Scenario Console", {}, owner.id, owner.selected_organization_id
    )
    run = await db_session.create_workflow_run(
        "Normal Sakinah call",
        workflow.id,
        WorkflowRunMode.SMALLWEBRTC.value,
        owner.id,
        call_type=CallType.INBOUND,
        organization_id=owner.selected_organization_id,
    )
    persisted = await db_session.update_call_calm_score_progress(
        workflow_run_id=run.id,
        organization_id=owner.selected_organization_id,
        calm_turns=[
            {
                "turn_id": "service_user-1",
                "role": "service_user",
                "scoring_method": "llm_evaluation",
                "scored_at": "2026-09-27T11:00:01+00:00",
                "calm_scores": {"state.emotion.anxiety_fear": 7},
                "calm_confidence": {"state.emotion.anxiety_fear": 8},
                "utterance_verbatim": "must not persist",
            },
            {
                "turn_id": "sakinah-1",
                "role": "sakinah",
                "scoring_method": "llm_evaluation",
                "scored_at": "2026-09-27T11:00:02+00:00",
                "calm_scores": {"response_quality.empathy": 9},
                "calm_confidence": {"response_quality.empathy": 8},
                "prompt_sent_to_llm": "must not persist",
            },
        ],
    )
    assert persisted is True

    async with test_client_factory(owner) as client:
        response = await client.get(f"/api/v1/workflow/{workflow.id}/runs/{run.id}")
        assert response.status_code == 200, response.text
        timeline = response.json()["calm_score_timeline"]
        assert [track["role"] for track in timeline["roles"]] == [
            "sakinah",
            "service_user",
        ]
        assert all(track["run_id"] == run.id for track in timeline["roles"])
        assert "must not persist" not in response.text

    # The worker's organization-scoped write and the details endpoint both
    # refuse cross-tenant access to an otherwise valid run id.
    assert (
        await db_session.update_call_calm_score_progress(
            workflow_run_id=run.id,
            organization_id=outsider.selected_organization_id,
            calm_turns=[],
        )
    ) is False
    async with test_client_factory(outsider) as client:
        assert (await client.get(f"/api/v1/workflow/{workflow.id}/runs/{run.id}")).status_code == 404
