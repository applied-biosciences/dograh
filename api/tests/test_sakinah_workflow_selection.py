from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from api.services.sakinah.workflow import (
    SERVICE_USER_WORKFLOW_NAME,
    WORKFLOW_NAME,
    ensure_sakinah_workflow,
    ensure_service_user_workflow,
    is_sakinah_workflow,
)


def _workflow(workflow_id: int, name: str, voice: str | None):
    configurations = (
        {
            "model_configuration_v2_override": {
                "version": 2,
                "mode": "byok",
                "byok": {
                    "mode": "pipeline",
                    "pipeline": {
                        "tts": {"provider": "elevenlabs", "voice": voice}
                    },
                },
            }
        }
        if voice
        else {}
    )
    definition = SimpleNamespace(
        id=workflow_id + 100,
        status="published",
        created_at=datetime(2026, 9, workflow_id, tzinfo=UTC),
        workflow_configurations=configurations,
        workflow_json={"nodes": [{"type": "endCall"}]},
    )
    return SimpleNamespace(
        id=workflow_id,
        name=name,
        created_at=datetime(2026, 9, workflow_id, tzinfo=UTC),
        workflow_configurations={},
        released_definition=definition,
        current_definition=definition,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "ensure", "unconfigured_id", "configured_id", "voice"),
    [
        (WORKFLOW_NAME, ensure_sakinah_workflow, 17, 8, "sakinah-voice"),
        (
            SERVICE_USER_WORKFLOW_NAME,
            ensure_service_user_workflow,
            18,
            9,
            "service-user-voice",
        ),
    ],
)
async def test_seed_selection_prefers_the_configured_voice_duplicate(
    name, ensure, unconfigured_id, configured_id, voice
):
    unconfigured = _workflow(unconfigured_id, name, None)
    configured = _workflow(configured_id, name, voice)
    by_id = {item.id: item for item in (unconfigured, configured)}

    class FakeDB:
        async def get_all_workflows(self, *, organization_id):
            assert organization_id == 1
            # Deliberately return the unconfigured duplicate first.
            return [unconfigured, configured]

        async def get_workflow(self, workflow_id, *, organization_id):
            assert organization_id == 1
            return by_id[workflow_id]

        async def get_draft_version(self, workflow_id):
            return None

    user = SimpleNamespace(id=4, selected_organization_id=1)
    selected = await ensure(FakeDB(), user)

    assert selected.id == configured_id
    assert (
        selected.released_definition.workflow_configurations
        ["model_configuration_v2_override"]["byok"]["pipeline"]["tts"]["voice"]
        == voice
    )


def test_live_calm_selection_uses_the_pinned_run_configuration():
    workflow = SimpleNamespace(name="Ordinary support agent", workflow_configurations={})

    assert is_sakinah_workflow(
        workflow, {"calm_scoring": {"enabled": True}}
    )
    assert not is_sakinah_workflow(
        workflow, {"calm_scoring": {"enabled": False}}
    )
