"""Regression tests for simulation draft binding and role isolation."""

from types import SimpleNamespace

import pytest

from api.services.workflow.run_creation import prepare_workflow_run_inputs


class _Definitions:
    def __init__(self, draft=None):
        self.draft = draft
        self.calls = []

    async def get_draft_version(self, workflow_id):
        self.calls.append(workflow_id)
        return self.draft


@pytest.mark.asyncio
async def test_simulation_inputs_prefer_each_role_draft_and_keep_template_context():
    published = SimpleNamespace(id=1, template_context_variables={"published": True})
    sakinah_draft = SimpleNamespace(id=2, template_context_variables={"role": "sakinah"})
    service_user_draft = SimpleNamespace(id=3, template_context_variables={"role": "service-user"})
    sakinah = SimpleNamespace(id=10, released_definition=published, current_definition=published)
    service_user = SimpleNamespace(id=11, released_definition=published, current_definition=published)

    sakinah_inputs = await prepare_workflow_run_inputs(
        _Definitions(sakinah_draft), sakinah, initial_context={"scenario": "x"}, use_draft=True, include_template_context=True
    )
    service_user_inputs = await prepare_workflow_run_inputs(
        _Definitions(service_user_draft), service_user, initial_context={"scenario": "x"}, use_draft=True, include_template_context=True
    )
    assert sakinah_inputs.definition_id == 2
    assert service_user_inputs.definition_id == 3
    assert sakinah_inputs.initial_context["role"] == "sakinah"
    assert service_user_inputs.initial_context["role"] == "service-user"


@pytest.mark.asyncio
async def test_simulation_inputs_fall_back_to_published_without_mutating_org_config():
    published = SimpleNamespace(id=1, template_context_variables={"published": True})
    workflow = SimpleNamespace(id=10, released_definition=published, current_definition=published)
    organization_config = {"tts": {"voice": "organisation-voice"}}
    inputs = await prepare_workflow_run_inputs(
        _Definitions(), workflow, initial_context={"role_voice": "service-user-voice"}, use_draft=True, include_template_context=True
    )
    assert inputs.definition_id == 1
    assert inputs.initial_context["published"] is True
    assert organization_config == {"tts": {"voice": "organisation-voice"}}
