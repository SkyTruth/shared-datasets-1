"""Closed ingestion deployment policies; validate combinations before authentication."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def target(name, *, selection, targets=(), asset_root="", rollout_gate=False,
           paused_gate=False, plan_guard="update", canary_mode="terminal", after_verify=""):
    if (not re.fullmatch(r"[a-z][a-z0-9-]+", name) or not re.fullmatch(r"[a-z][a-z0-9_]+", selection)
            or not isinstance(targets, (list, tuple)) or not all(isinstance(row, str) and row for row in targets)
            or (asset_root and (len(asset_root.split("/")) != 3 or any(part in {"", ".", ".."} for part in asset_root.split("/"))))
            or (after_verify and not asset_root)
            or plan_guard not in {"update", "install", "wdpa", "usage"}
            or canary_mode not in {"terminal", "deferred"}
            or any(type(value) is not bool for value in (rollout_gate, paused_gate))
            or len(targets) != len(set(targets))
            or (plan_guard == "usage") != (not targets)
            or (canary_mode == "deferred") != (plan_guard == "wdpa")
            or (plan_guard == "wdpa" and (asset_root or not rollout_gate or not paused_gate))
            or (plan_guard == "usage" and (asset_root or rollout_gate or paused_gate or after_verify))
            or (plan_guard in {"update", "install"} and not asset_root)
            or (targets and targets[0] != f"module.{name.replace('-', '_')}_job.google_cloud_run_v2_job.this")):
        raise ValueError("Invalid ingestion deployment policy: " + name)
    return dict(workflow=name + "-deploy.yml", image_variable=name.replace("-", "_") + "_image",
                targets=list(targets), selection=selection, asset_root=asset_root,
                asset_slug=asset_root.rsplit("/", 1)[-1], rollout_gate=rollout_gate,
                paused_gate=paused_gate, plan_guard=plan_guard, canary_mode=canary_mode,
                after_verify=after_verify)


DEPLOY_TARGETS = {
    "wdpa-monthly": target("wdpa-monthly", selection="wdpa", plan_guard="wdpa", canary_mode="deferred",
        rollout_gate=True, paused_gate=True, targets=[
            "module.wdpa_monthly_job.google_cloud_run_v2_job.this", "google_iam_deny_policy.canonical_destructive_actions[0]"]),
    "mpatlas-monthly": target("mpatlas-monthly", selection="mpatlas", plan_guard="install",
        asset_root="100-geographic-reference/130-protected-areas/mpatlas-zone-assessments", targets=[
            "module.mpatlas_monthly_job.google_cloud_run_v2_job.this",
            "google_cloud_run_v2_job_iam_member.mpatlas_scheduler_invoker",
            "module.mpatlas_monthly_scheduler.google_cloud_scheduler_job.this", "google_iam_deny_policy.canonical_destructive_actions[0]"]),
    "eamlis-monthly": target("eamlis-monthly", selection="eamlis", paused_gate=True,
        asset_root="300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory",
        targets=["module.eamlis_monthly_job.google_cloud_run_v2_job.this"]),
    "sea-ice-daily": target("sea-ice-daily", selection="sea_ice", rollout_gate=True,
        asset_root="200-imagery-derived/250-weather-climate/ims-sea-ice-extent",
        targets=["module.sea_ice_daily_job.google_cloud_run_v2_job.this"],
        after_verify='uv run --no-sync python scripts/publication_rollout_gate.py --job "$TARGET" --require-published\n'
                     'gcloud scheduler jobs resume "$TARGET" --location="$REGION" --project="$GOOGLE_CLOUD_PROJECT"\n'
                     'gcloud scheduler jobs describe "$TARGET" --location="$REGION" --project="$GOOGLE_CLOUD_PROJECT" --format="value(state,schedule,timeZone)"'),
    "dataset-usage": target("dataset-usage", selection="dataset_usage", plan_guard="usage"),
}
# Telemetry does not acquire publication requirements.
TARGETS = {name: config for name, config in DEPLOY_TARGETS.items() if config["plan_guard"] != "usage"}


def image_variables(target, image):
    return {config["image_variable"]: image if name == target else "unused-by-reconciliation"
            for name, config in DEPLOY_TARGETS.items()}


def plan(name, image, config_digest):
    config = DEPLOY_TARGETS[name]
    targets = config["targets"]
    if config["plan_guard"] == "usage":
        targets = subprocess.check_output(["uv", "run", "--no-sync", "python", "scripts/dataset_usage_deploy.py", "--targets"], text=True).splitlines()
    if not targets:
        raise ValueError("Ingestion plan requires a nonempty resource scope")
    variables = image_variables(name, image)
    if config["plan_guard"] == "wdpa":
        variables[config["image_variable"] + "_config_digest"] = config_digest
    subprocess.run(["bash", str(Path(os.environ["GITHUB_WORKSPACE"]) / "scripts/terraform_retry.sh"),
        "-chdir=terraform/envs/prod", "plan", "-input=false", "-out=" + os.environ["RUNNER_TEMP"] + "/ingestion.tfplan",
        *("-target=" + item for item in targets), *(f"-var={key}={value}" for key, value in variables.items())], check=True)


if __name__ == "__main__":
    name = os.environ["TARGET"]
    config = DEPLOY_TARGETS[name]
    if config["workflow"] != os.environ["WORKFLOW_FILE"]:
        raise ValueError("Ingestion target does not belong to its caller")
    if sys.argv[1:] == ["plan"]:
        plan(name, os.environ["IMAGE"], os.environ.get("TESTED_IMAGE_CONFIG", ""))
    elif not sys.argv[1:]:
        values = {**config, "canary_attempts": 20 if config["canary_mode"] == "deferred" else 125,
                  "canary_timeout_phase": "verification_pending" if config["canary_mode"] == "deferred" else "failed",
                  "canary_run_url": config["canary_mode"] == "terminal"}
        with Path(os.environ["GITHUB_ENV"]).open("a") as stream:
            for key, value in values.items():
                value = str(value).lower() if isinstance(value, bool) else value if isinstance(value, str) else json.dumps(value)
                stream.write(f"{key.upper()}<<INGESTION_POLICY\n{value}\nINGESTION_POLICY\n")
    else:
        raise ValueError("Expected ingestion target resolution or plan")
