"""Deployment source identity stays string-typed across reusable workflow calls.

GitHub's context declares run_id and run_attempt as strings, and reusable
workflow inputs must match their producers. This checks the real call graph,
including the nested Terraform calls, before any main-only job is allocated.
"""

import copy
from pathlib import Path

import pytest

from scripts.ci_contract import DEPLOYMENTS
from workflow_helpers import load_workflow, workflow_triggers

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FIELDS = ("source_run_id", "source_run_attempt")


def workflows():
    return {path.name: load_workflow(path)
            for path in (ROOT / ".github/workflows").glob("*.yml")}


def source_calls(documents):
    for caller_name, caller in documents.items():
        for job_name, job in caller.get("jobs", {}).items():
            uses = job.get("uses", "")
            if not uses.startswith("./.github/workflows/"):
                continue
            trigger = workflow_triggers(documents[uses.rsplit("/", 1)[1]])
            declared = (trigger.get("workflow_call") or {}).get("inputs", {})
            if any(field in declared for field in SOURCE_FIELDS):
                yield caller_name, caller, job_name, job


def assert_source_contract(documents, caller_name, caller, job_name, job):
    prefix = "./.github/workflows/"
    assert job["uses"].startswith(prefix), "source identity must reach a checked-in workflow"
    callee_name = job["uses"][len(prefix):]
    declared = workflow_triggers(documents[callee_name])["workflow_call"]["inputs"]
    for field in ("executor_sha", *SOURCE_FIELDS):
        assert field in job.get("with", {}), f"{caller_name}.{job_name} must pass {field}"
        assert declared[field]["required"] is True, f"{callee_name} must require {field}"
        assert declared[field]["type"] == "string", f"{callee_name}.{field} must be string"
    if caller_name == "ci.yml":
        assert job["with"]["executor_sha"] == "${{ needs.ci-ready.outputs.tested_sha }}"
        for field, context in zip(SOURCE_FIELDS, ("run_id", "run_attempt")):
            expected = "${{ format('{0}', github." + context + ") }}"
            assert job["with"][field] == expected, f"{job_name}.{field} must produce a string"
    else:
        incoming = workflow_triggers(caller)["workflow_call"]["inputs"]
        for field in ("executor_sha", *SOURCE_FIELDS):
            assert incoming[field]["required"] is True, f"{caller_name} must require {field}"
            assert incoming[field]["type"] == "string", f"{caller_name}.{field} must be string"
            assert job["with"][field] == "${{ inputs." + field + " }}", "nested call must retain exact source identity"


def test_every_ci_deployment_and_nested_call_preserves_string_source_identity():
    documents = workflows()
    calls = list(source_calls(documents))
    ci_calls = {job_name.replace("-", "_") for caller_name, _, job_name, _ in calls if caller_name == "ci.yml"}
    assert ci_calls == set(DEPLOYMENTS), "every selected deployment must have a checked source contract"
    assert any(caller_name != "ci.yml" for caller_name, *_ in calls), "nested Terraform forwarding must be checked"
    for call in calls:
        assert_source_contract(documents, *call)


@pytest.mark.parametrize("callee", ("pmtiles-cdn-sync.yml", "catalog-viewer-deploy.yml"))
@pytest.mark.parametrize("field", SOURCE_FIELDS)
def test_original_number_schema_cannot_accept_string_context_identity(callee, field):
    documents = copy.deepcopy(workflows())
    workflow_triggers(documents[callee])["workflow_call"]["inputs"][field]["type"] = "number"
    call = next(call for call in source_calls(documents) if call[3]["uses"].endswith("/" + callee))
    with pytest.raises(AssertionError, match=rf"{callee}\.{field} must be string"):
        assert_source_contract(documents, *call)


@pytest.mark.parametrize("target", DEPLOYMENTS)
@pytest.mark.parametrize("field", SOURCE_FIELDS)
def test_numeric_source_producer_is_rejected_for_every_deployment(target, field):
    documents = copy.deepcopy(workflows())
    job_name = target.replace("_", "-")
    caller = documents["ci.yml"]
    job = caller["jobs"][job_name]
    context = "run_id" if field == "source_run_id" else "run_attempt"
    job["with"][field] = "${{ fromJSON(github." + context + ") }}"
    with pytest.raises(AssertionError, match=rf"{job_name}\.{field} must produce a string"):
        assert_source_contract(documents, "ci.yml", caller, job_name, job)


@pytest.mark.parametrize("field", ("executor_sha", *SOURCE_FIELDS))
def test_nested_call_cannot_disappear_when_a_required_source_input_is_missing(field):
    documents = copy.deepcopy(workflows())
    original = list(source_calls(documents))
    caller_name, caller, job_name, job = next(call for call in original if call[0] != "ci.yml")
    del job["with"][field]
    calls = list(source_calls(documents))
    assert len(calls) == len(original), "incomplete source forwarding must remain in the checked graph"
    with pytest.raises(AssertionError, match=rf"{caller_name}\.{job_name} must pass {field}"):
        assert_source_contract(documents, caller_name, caller, job_name, job)


@pytest.mark.parametrize("field", ("executor_sha", *SOURCE_FIELDS))
def test_nested_source_identity_cannot_be_optional(field):
    documents = copy.deepcopy(workflows())
    call = next(call for call in source_calls(documents) if call[0] != "ci.yml")
    workflow_triggers(call[1])["workflow_call"]["inputs"][field]["required"] = False
    with pytest.raises(AssertionError, match=rf"{call[0]} must require {field}"):
        assert_source_contract(documents, *call)
