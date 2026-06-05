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
