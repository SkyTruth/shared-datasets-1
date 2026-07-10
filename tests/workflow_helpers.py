from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import yaml


def load_workflow(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def workflow_triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    # PyYAML follows YAML 1.1 and treats the GitHub Actions key `on` as a bool.
    return workflow.get("on") or workflow.get(True) or {}


def workflow_steps_by_name(
    workflow: dict[str, Any],
    job_name: str,
) -> dict[str, dict[str, Any]]:
    return {
        step["name"]: step
        for step in workflow["jobs"][job_name]["steps"]
        if "name" in step
    }


def workflow_step_run(
    workflow: dict[str, Any],
    job_name: str,
    step_name: str,
) -> str:
    return str(workflow_steps_by_name(workflow, job_name)[step_name].get("run", ""))


def workflow_all_step_runs(workflow: dict[str, Any], job_name: str) -> str:
    return "\n".join(
        str(step.get("run", "")) for step in workflow["jobs"][job_name]["steps"]
    )


def terraform_targets(plan_run: str) -> set[str]:
    return {
        target.strip("'\"")
        for target in re.findall(r"(?:^|\s)-target=([^\s\\]+)", plan_run)
    }


def python_literal_string_set(run: str, name: str) -> set[str]:
    match = re.search(rf"{re.escape(name)}\s*=\s*\{{(?P<body>.*?)\}}", run, re.DOTALL)
    if not match:
        return set()
    parsed = ast.literal_eval("{" + match.group("body") + "}")
    return set(parsed)


TARGET_APPLY_WORKFLOW_USES = "./.github/workflows/prod-terraform-target-apply.yml"


def newline_values(value: Any) -> set[str]:
    return {line.strip() for line in str(value or "").splitlines() if line.strip()}


def assert_target_apply_caller(
    testcase,
    workflow_path: Path,
    *,
    expected_name: str,
    push_paths: set[str],
    job_name: str = "sync",
    expected_job_if: str | None = "${{ github.event_name != 'pull_request' }}",
    expected_needs: str | None = None,
    sync_name: str,
    refusal_prefix: str,
    expected_targets: set[str],
    expected_allowed_exact: set[str] | None = None,
    expected_allowed_patterns: set[str] | None = None,
    expected_tf_vars: set[str] | None = None,
    expected_terraform_dir: str | None = None,
    expected_block_deletes: bool | None = None,
    expected_post_apply_wait_seconds: int | None = None,
    blocked_resources: set[str] = frozenset(),
) -> dict[str, Any]:
    """Assert a workflow job is a constrained caller of the reusable prod target apply."""
    workflow = load_workflow(workflow_path)
    trigger = workflow_triggers(workflow)
    job = workflow["jobs"][job_name]
    inputs = job.get("with", {})

    testcase.assertEqual(workflow["name"], expected_name)
    testcase.assertEqual(trigger["push"]["branches"], ["main"])
    testcase.assertEqual(set(trigger["push"]["paths"]), push_paths)
    testcase.assertIn("workflow_dispatch", trigger)
    testcase.assertNotIn("pull_request", trigger)
    testcase.assertEqual(workflow["permissions"], {"contents": "read", "id-token": "write"})
    testcase.assertEqual(job["uses"], TARGET_APPLY_WORKFLOW_USES)
    if expected_job_if is None:
        testcase.assertNotIn("if", job)
    else:
        testcase.assertEqual(job["if"], expected_job_if)
    if expected_needs is None:
        testcase.assertNotIn("needs", job)
    else:
        testcase.assertEqual(job["needs"], expected_needs)

    testcase.assertEqual(inputs["sync_name"], sync_name)
    testcase.assertEqual(inputs["refusal_prefix"], refusal_prefix)
    testcase.assertEqual(newline_values(inputs["targets"]), expected_targets)
    testcase.assertEqual(
        newline_values(inputs["allowed_exact"]),
        expected_allowed_exact if expected_allowed_exact is not None else expected_targets,
    )
    if expected_allowed_patterns is not None:
        testcase.assertEqual(newline_values(inputs.get("allowed_patterns", "")), expected_allowed_patterns)
    if expected_tf_vars is not None:
        testcase.assertEqual(newline_values(inputs.get("tf_vars", "")), expected_tf_vars)
    if expected_terraform_dir is None:
        testcase.assertNotIn("terraform_dir", inputs)
    else:
        testcase.assertEqual(inputs["terraform_dir"], expected_terraform_dir)
    if expected_block_deletes is None:
        testcase.assertNotIn("block_deletes", inputs)
    else:
        testcase.assertEqual(inputs["block_deletes"], expected_block_deletes)
    if expected_post_apply_wait_seconds is None:
        testcase.assertNotIn("post_apply_wait_seconds", inputs)
    else:
        testcase.assertEqual(inputs["post_apply_wait_seconds"], expected_post_apply_wait_seconds)

    for resource in blocked_resources:
        testcase.assertNotIn(resource, newline_values(inputs["targets"]))
        testcase.assertNotIn(resource, newline_values(inputs["allowed_exact"]))

    return workflow
