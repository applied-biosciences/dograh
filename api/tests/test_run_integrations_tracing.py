from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.tasks import run_integrations


def _workflow_run(nodes):
    return SimpleNamespace(
        id=77,
        name="Post-call run",
        workflow_id=11,
        workflow=SimpleNamespace(name="Test workflow"),
        campaign_id=None,
        definition=SimpleNamespace(
            id=5,
            workflow_json={"nodes": nodes, "edges": []},
        ),
        created_at=None,
        initial_context={},
        gathered_context={},
        usage_info={},
        annotations={},
        extra={},
        recording_url=None,
        transcript_url=None,
    )


@pytest.mark.asyncio
async def test_tracing_registration_failure_does_not_abort_webhooks(monkeypatch):
    webhook_node = {
        "id": "webhook-1",
        "type": "webhook",
        "position": {"x": 0, "y": 0},
        "data": {
            "name": "Final webhook",
            "enabled": True,
            "endpoint_url": "https://example.test/hook",
            "payload_template": {},
        },
    }
    workflow_run = _workflow_run([webhook_node])
    db = SimpleNamespace(
        get_workflow_run_with_context=AsyncMock(return_value=(workflow_run, 42)),
        get_configuration_value=AsyncMock(
            return_value={
                "host": "https://langfuse.example.test",
                "public_key": "public",
                "secret_key": "secret",
            }
        ),
        ensure_public_access_token=AsyncMock(return_value="token"),
    )
    enqueue = AsyncMock()

    monkeypatch.setattr(run_integrations, "db_client", db)
    monkeypatch.setattr(
        run_integrations,
        "register_org_langfuse_credentials",
        lambda **_kwargs: (_ for _ in ()).throw(
            AttributeError("OTLPSpanExporter has no attribute _headers")
        ),
    )
    monkeypatch.setattr(
        run_integrations, "has_completion_handlers", lambda _definition: False
    )
    monkeypatch.setattr(run_integrations, "_enqueue_webhook_delivery", enqueue)

    await run_integrations.run_integrations_post_workflow_run(None, 77)

    enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_genuine_completion_orchestration_error_is_still_raised(monkeypatch):
    workflow_run = _workflow_run([{"id": "integration-1", "type": "custom"}])
    db = SimpleNamespace(
        get_workflow_run_with_context=AsyncMock(return_value=(workflow_run, 42)),
        get_configuration_value=AsyncMock(return_value=None),
        ensure_public_access_token=AsyncMock(return_value="token"),
    )

    monkeypatch.setattr(run_integrations, "db_client", db)
    monkeypatch.setattr(
        run_integrations, "has_completion_handlers", lambda _definition: True
    )
    monkeypatch.setattr(
        run_integrations,
        "run_completion_handlers",
        AsyncMock(side_effect=RuntimeError("integration provider failed")),
    )
    monkeypatch.setattr(run_integrations, "log_failure", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="integration provider failed"):
        await run_integrations.run_integrations_post_workflow_run(None, 77)
