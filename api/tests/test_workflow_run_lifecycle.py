from datetime import UTC, datetime

import pytest

from api.enums import WorkflowRunState
from api.tests.integrations._run_pipeline_helpers import create_workflow_run_rows


@pytest.mark.asyncio
async def test_update_workflow_run_persists_completion_lifecycle_fields(
    db_session, async_session
):
    workflow_run, _user, _workflow = await create_workflow_run_rows(
        db_session,
        async_session,
        workflow_definition={
            "nodes": [
                {
                    "id": "start",
                    "type": "startCall",
                    "data": {"name": "Start", "is_start": True},
                    "position": {"x": 0, "y": 0},
                }
            ],
            "edges": [],
        },
        name_prefix="Lifecycle",
        provider_id_suffix="lifecycle",
    )
    ended_at = datetime(2026, 9, 27, 1, 2, 3, tzinfo=UTC)

    updated = await db_session.update_workflow_run(
        workflow_run.id,
        is_completed=True,
        state=WorkflowRunState.COMPLETED.value,
        ended_at=ended_at,
        duration_seconds=42.5,
        call_status="completed",
        provider_call_id="provider-call-1",
        termination_reason="user_hangup",
    )

    assert updated.is_completed is True
    assert updated.state == WorkflowRunState.COMPLETED.value
    assert updated.ended_at == ended_at
    assert updated.duration_seconds == 42.5
    assert updated.call_status == "completed"
    assert updated.provider_call_id == "provider-call-1"
    assert updated.termination_reason == "user_hangup"
