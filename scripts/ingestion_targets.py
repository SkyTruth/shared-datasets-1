"""Closed ingestion deployment policies; validate combinations before authentication."""
from __future__ import annotations

from dataclasses import dataclass, asdict
import os
from pathlib import Path
import re
import subprocess
import sys

def target(name, *, selection, targets=(), asset_root="", rollout_gate=False,
           paused_gate=False, plan_guard="update", canary_mode="terminal", after_verify=""):
    def require(condition, message):
        if not condition:
            raise ValueError(f"{name}: {message}")
    require(bool(re.fullmatch(r"[a-z][a-z0-9-]+", name)) and bool(re.fullmatch(r"[a-z][a-z0-9_]+", selection)), "invalid target or selection name")
    require(isinstance(targets, (list, tuple)) and all(isinstance(row, str) and row for row in targets)
            and len(targets) == len(set(targets)), "resource targets must be distinct nonempty strings")
    require(not asset_root or len(asset_root.split("/")) == 3 and not any(part in {"", ".", ".."} for part in asset_root.split("/")), "invalid asset root")
    require(plan_guard in {"update", "install", "wdpa", "usage"}, "unknown plan guard")
    require(canary_mode in {"terminal", "deferred"}, "unknown canary mode")
    require(all(type(value) is bool for value in (rollout_gate, paused_gate)), "gates must be booleans")
    require((plan_guard == "usage") == (not targets), "telemetry owns computed scope; publishers require targets")
    require((canary_mode == "deferred") == (plan_guard == "wdpa"), "only WDPA uses deferred verification")
    require(plan_guard != "wdpa" or not asset_root and rollout_gate and paused_gate, "WDPA requires rollout and pause gates without a single asset")
    require(plan_guard != "usage" or not (asset_root or rollout_gate or paused_gate or after_verify), "telemetry cannot acquire publication policies")
    require(plan_guard not in {"update", "install"} or bool(asset_root), "publication requires an asset root")
    require(not targets or targets[0] == f"module.{name.replace('-', '_')}_job.google_cloud_run_v2_job.this", "first target must be the owned Cloud Run job")
    require(after_verify in {"", "resume_published_scheduler"} and (not after_verify or bool(asset_root)), "unknown verification hook or missing asset")
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
        after_verify="resume_published_scheduler"),
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


@dataclass(frozen=True)
class ExecutorEnvironment:
    ROLLOUT_GATE: bool
    ASSET_ROOT: str
    PLAN_GUARD: str
    AFTER_VERIFY: str
    CANARY_ATTEMPTS: int
    CANARY_TIMEOUT_PHASE: str
    CANARY_RUN_URL: bool


def environment(config):
    deferred = config["canary_mode"] == "deferred"
    return asdict(ExecutorEnvironment(config["rollout_gate"], config["asset_root"], config["plan_guard"],
        config["after_verify"], 20 if deferred else 125, "verification_pending" if deferred else "failed", not deferred))


def after_verify(name):
    if DEPLOY_TARGETS[name]["after_verify"] != "resume_published_scheduler":
        raise ValueError("No registered verification hook for " + name)
    subprocess.run(["uv", "run", "--no-sync", "python", "scripts/publication_rollout_gate.py", "--job", name, "--require-published"], check=True)
    for operation in ("resume", "describe"):
        subprocess.run(["gcloud", "scheduler", "jobs", operation, name, "--location=" + os.environ["REGION"],
            "--project=" + os.environ["GOOGLE_CLOUD_PROJECT"], *(["--format=value(state,schedule,timeZone)"] if operation == "describe" else [])], check=True)


if __name__ == "__main__":
    # Placeholder variables are generated for every registered ingestion image.
    if sys.argv[1:] == ["image-vars"]:
        print("\n".join(f"-var={key}={value}" for key, value in image_variables("", "unused-by-reconciliation").items()))
        sys.exit(0)
    name = os.environ["TARGET"]
    stage = os.environ.get("STAGE", "all")
    if stage not in {"all", "apply", "verify"} or stage != "all" and name != "dataset-usage":
        raise ValueError("Split apply/verify is restricted to usage telemetry")
    identifier, image = os.environ.get("VERIFY_DEPLOYMENT_ID", ""), os.environ.get("VERIFY_IMAGE", "")
    if (stage == "verify" and (not identifier.isdecimal() or not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", image))
            or stage != "verify" and (identifier or image)):
        raise ValueError("Only split verification accepts a required deployment ID and immutable image")
    config = DEPLOY_TARGETS[name]
    if config["workflow"] != os.environ["WORKFLOW_FILE"]:
        raise ValueError("Ingestion target does not belong to its caller")
    if sys.argv[1:] == ["plan"]:
        plan(name, os.environ["IMAGE"], os.environ.get("TESTED_IMAGE_CONFIG", ""))
    elif sys.argv[1:] == ["after-verify"]:
        after_verify(name)
    else:
        values = environment(config)
        if sys.argv[1:] != list(values):
            raise ValueError("Executor must declare the exact registered environment keys")
        with Path(os.environ["GITHUB_ENV"]).open("a") as stream:
            for key, value in values.items():
                value = str(value).lower() if isinstance(value, bool) else str(value)
                stream.write(f"{key}<<INGESTION_POLICY\n{value}\nINGESTION_POLICY\n")
