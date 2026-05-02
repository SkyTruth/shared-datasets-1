#!/usr/bin/env python3
"""Preferred local Terraform prod apply wrapper with Slack summaries."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.slack_notify import notify


PROD_DIR = REPO_ROOT / "terraform" / "envs" / "prod"
SLACK_CHANNEL = "projects/shared-datasets-1/notificationChannels/6831586092945135667"
DEFAULT_TERRAFORM = "/tmp/terraform-1.8.5/terraform" if Path("/tmp/terraform-1.8.5/terraform").exists() else "terraform"


class StageFailure(RuntimeError):
    def __init__(self, stage: str, command: list[str], returncode: int, output: str) -> None:
        super().__init__(f"{stage} failed with exit code {returncode}")
        self.stage = stage
        self.command = command
        self.returncode = returncode
        self.output = output


def run_command(command: list[str], *, runner: Any = subprocess.run) -> subprocess.CompletedProcess[str]:
    return runner(command, capture_output=True, text=True)


def ensure_success(stage: str, result: subprocess.CompletedProcess[str], command: list[str]) -> None:
    if result.returncode != 0:
        raise StageFailure(stage, command, result.returncode, (result.stdout or "") + (result.stderr or ""))


def tail_text(text: str, *, lines: int = 20) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def current_cloud_run_image(job_name: str, *, runner: Any = subprocess.run) -> str:
    command = [
        "gcloud",
        "run",
        "jobs",
        "describe",
        job_name,
        "--region=us-central1",
        "--project=shared-datasets-1",
        "--format=value(spec.template.spec.template.spec.containers[0].image)",
    ]
    result = run_command(command, runner=runner)
    ensure_success(f"read {job_name} image", result, command)
    image = result.stdout.strip()
    if not image:
        raise StageFailure(f"read {job_name} image", command, 1, "empty image URI")
    return image


def parse_var_args(var_args: Sequence[str]) -> dict[str, str]:
    parsed = {}
    for item in var_args:
        if "=" not in item:
            raise ValueError(f"--var expects key=value, got {item}")
        key, value = item.split("=", 1)
        parsed[key] = value
    return parsed


def default_vars(existing: dict[str, str], *, runner: Any = subprocess.run) -> dict[str, str]:
    values = dict(existing)
    values.setdefault("cron_alert_notification_channels", f'["{SLACK_CHANNEL}"]')
    if "wdpa_monthly_image" not in values:
        values["wdpa_monthly_image"] = current_cloud_run_image("wdpa-monthly", runner=runner)
    if "sea_ice_daily_image" not in values:
        values["sea_ice_daily_image"] = current_cloud_run_image("sea-ice-daily", runner=runner)
    if "eamlis_monthly_image" not in values:
        values["eamlis_monthly_image"] = current_cloud_run_image("eamlis-monthly", runner=runner)
    return values


def var_flags(values: dict[str, str]) -> list[str]:
    flags: list[str] = []
    for key, value in values.items():
        flags.extend(["-var", f"{key}={value}"])
    return flags


def plan_command(terraform_bin: str, plan_file: Path, values: dict[str, str]) -> list[str]:
    return [
        terraform_bin,
        f"-chdir={PROD_DIR}",
        "plan",
        "-input=false",
        f"-out={plan_file}",
        *var_flags(values),
    ]


def apply_command(terraform_bin: str, plan_file: Path) -> list[str]:
    return [terraform_bin, f"-chdir={PROD_DIR}", "apply", "-input=false", str(plan_file)]


def show_plan(terraform_bin: str, plan_file: Path, *, runner: Any = subprocess.run) -> dict[str, Any]:
    command = [terraform_bin, f"-chdir={PROD_DIR}", "show", "-json", str(plan_file)]
    result = run_command(command, runner=runner)
    ensure_success("show plan", result, command)
    return json.loads(result.stdout or "{}")


def output_json(terraform_bin: str, *, runner: Any = subprocess.run) -> dict[str, Any]:
    command = [terraform_bin, f"-chdir={PROD_DIR}", "output", "-json"]
    result = run_command(command, runner=runner)
    ensure_success("read outputs", result, command)
    return json.loads(result.stdout or "{}")


def resource_change_summary(plan: dict[str, Any]) -> list[str]:
    summary = []
    for change in plan.get("resource_changes", []):
        actions = change.get("change", {}).get("actions", [])
        if actions == ["no-op"]:
            continue
        summary.append(f"{'/'.join(actions)} {change.get('address')}")
    return summary


def selected_outputs(outputs: dict[str, Any]) -> dict[str, str]:
    keys = [
        "cron_alert_policy_names",
        "monitoring_alert_policy_names",
        "slack_webhook_secret_id",
        "wdpa_monthly_scheduler_id",
        "sea_ice_daily_scheduler_id",
    ]
    selected = {}
    for key in keys:
        if key in outputs:
            selected[key] = json.dumps(outputs[key].get("value"))
    return selected


def send_success_summary(changes: list[str], outputs: dict[str, Any], *, dry_run: bool) -> None:
    body = "\n".join(
        [
            f"*Operator:* `{getpass.getuser()}`",
            f"*Result:* `{'no-op' if not changes else 'applied'}`",
            "*Changes:*",
            *(f"- `{change}`" for change in (changes or ["none"])),
        ]
    )
    notify(
        title="Terraform prod apply succeeded",
        body=body,
        status="success",
        fields=selected_outputs(outputs),
        dry_run=dry_run,
    )


def send_failure_summary(exc: StageFailure, *, dry_run: bool) -> None:
    notify(
        title="Terraform prod apply failed",
        body=(
            f"*Stage:* `{exc.stage}`\n"
            f"*Exit code:* `{exc.returncode}`\n"
            f"*Command:* `{' '.join(exc.command)}`\n"
            f"*Output tail:*\n```{tail_text(exc.output)}```"
        ),
        status="error",
        dry_run=dry_run,
    )


def run_apply(argv: Sequence[str], *, runner: Any = subprocess.run) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terraform-bin", default=os.environ.get("TERRAFORM_BIN", DEFAULT_TERRAFORM))
    parser.add_argument("--var", action="append", default=[], help="Terraform variable in key=value form.")
    parser.add_argument("--plan-file", default="")
    parser.add_argument("--slack-dry-run", action="store_true")
    args = parser.parse_args(list(argv))

    plan_file = Path(args.plan_file) if args.plan_file else Path(tempfile.gettempdir()) / f"shared-datasets-prod-{int(time.time())}.tfplan"
    try:
        values = default_vars(parse_var_args(args.var), runner=runner)
        plan = plan_command(args.terraform_bin, plan_file, values)
        plan_result = run_command(plan, runner=runner)
        ensure_success("plan", plan_result, plan)
        plan_payload = show_plan(args.terraform_bin, plan_file, runner=runner)
        changes = resource_change_summary(plan_payload)

        apply = apply_command(args.terraform_bin, plan_file)
        apply_result = run_command(apply, runner=runner)
        ensure_success("apply", apply_result, apply)
        outputs = output_json(args.terraform_bin, runner=runner)
        send_success_summary(changes, outputs, dry_run=args.slack_dry_run)
        print(apply_result.stdout)
        return 0
    except StageFailure as exc:
        send_failure_summary(exc, dry_run=args.slack_dry_run)
        print(exc.output, file=sys.stderr)
        return exc.returncode


def main() -> int:
    return run_apply(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
