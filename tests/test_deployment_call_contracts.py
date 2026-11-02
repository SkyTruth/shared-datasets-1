"""Deployment source identity stays string-typed across reusable workflow calls.

GitHub's context declares run_id and run_attempt as strings, and reusable
workflow inputs must match their producers. This checks the real call graph,
including the nested Terraform calls, before any main-only job is allocated.
"""

from pathlib import Path

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
