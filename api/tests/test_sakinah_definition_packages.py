"""Focused checks for the versioned Sakinah definition packages."""

import json
import re
import zipfile
from pathlib import Path

import pytest

from api.services.workflow.audit import audit_definition
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.workflow_graph import WorkflowGraph


PACKAGE_DIR = (
    Path(__file__).resolve().parents[2] / "docs" / "agents" / "v1.47.0.29"
)
PACKAGES = {
    "Sakinah Decision Agent v3.1": ("Sakinah Decision Agent v3.1.definition.zip", 9),
    "Sakinah Decision Agent v4.1": ("Sakinah Decision Agent v4.1.definition.zip", 8),
}


def _load_package(name: str) -> tuple[dict, dict]:
    filename, expected_edge_count = PACKAGES[name]
    with zipfile.ZipFile(PACKAGE_DIR / filename) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        payload = json.loads(archive.read("workflow_definition.json"))
    assert manifest["name"] == name
    assert manifest["node_count"] == 9
    assert manifest["edge_count"] == expected_edge_count
    assert payload["name"] == name
    return payload["workflow_definition"], manifest


@pytest.mark.parametrize("name", PACKAGES)
def test_revised_sakinah_definition_package_is_complete(name):
    definition, manifest = _load_package(name)
    nodes = definition["nodes"]
    edges = definition["edges"]
    node_ids = {node["id"] for node in nodes}

    assert len(nodes) == manifest["node_count"]
    assert len(edges) == manifest["edge_count"]
    assert not audit_definition(nodes, edges)
    WorkflowGraph(ReactFlowDTO.model_validate(definition))
    assert all(
        edge["source"] in node_ids and edge["target"] in node_ids for edge in edges
    )


@pytest.mark.parametrize("name", PACKAGES)
def test_revised_sakinah_definition_has_the_amended_memory_contract(name):
    definition, _ = _load_package(name)
    nodes = {node["id"]: node for node in definition["nodes"]}
    start = nodes["start-confidential-routing"]["data"]
    main_prompt = nodes["main-support"]["data"]["prompt"]
    global_prompt = nodes["global-safety"]["data"]["prompt"]
    memory_node = nodes["memory-consent-and-binding"]
    all_text = json.dumps(definition, ensure_ascii=False)

    assert start["pre_call_fetch_mode"] == "disabled"
    assert "Pre-call fetch is disabled" in start["prompt"]
    assert "{{memory_context}}" in global_prompt
    assert "caller_memory" not in all_text

    for prompt in (main_prompt, global_prompt):
        assert "Memory use (temporary, while PIN is on hold)" in prompt
        assert "remembered automatically unless they say no" in prompt

    memory_prompt = memory_node["data"]["prompt"]
    assert "remembered automatically unless they say no" in memory_prompt
    assert "If the caller says no" in memory_prompt
    assert any(
        edge["source"] == memory_node["id"]
        and edge["target"] == "main-support"
        and "conversation is continuing" in edge.get("data", {}).get("condition", "")
        for edge in definition["edges"]
    )

    obsolete_pin_offer = re.compile(
        r"\b(?:offer|ask|set up|create|choose|enrol|enroll)(?:\s+[^.!?\n]{0,40})?\s+pin\b",
        re.IGNORECASE,
    )
    assert not obsolete_pin_offer.search(all_text)
