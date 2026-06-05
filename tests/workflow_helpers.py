from __future__ import annotations

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
