#!/usr/bin/env python3
"""Explicitly acknowledged local break-glass Terraform production apply."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.slack_notify import notify


PROD_DIR = REPO_ROOT / "terraform" / "envs" / "prod"
PROJECT_ID = "shared-datasets-1"
AUDIT_LOG_NAME = "shared-datasets-breakglass"
SLACK_CHANNEL = os.environ.get("SHARED_DATASETS_TERRAFORM_NOTIFICATION_CHANNEL", "")
Runner = Callable[..., subprocess.CompletedProcess[str]]


class StageFailure(RuntimeError):
    def __init__(self, stage: str, command: list[str], returncode: int, output: str) -> None:
        super().__init__(f"{stage} failed with exit code {returncode}")
        self.stage = stage
        self.command = command
        self.returncode = returncode
        self.output = output


def run_command(command: list[str], *, runner: Runner = subprocess.run) -> subprocess.CompletedProcess[str]:
    return runner(command, capture_output=True, text=True)


def ensure_success(stage: str, result: subprocess.CompletedProcess[str], command: list[str]) -> None:
    if result.returncode != 0:
        raise StageFailure(stage, command, result.returncode, (result.stdout or "") + (result.stderr or ""))


def tail_text(text: str, *, lines: int = 20) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def resolve_terraform_binary(candidate: str | None) -> str:
    """Resolve a non-writable Terraform executable outside unsafe directories."""
    raw = candidate or shutil.which("terraform")
    if not raw:
        raise ValueError("Terraform was not found on PATH; pass --terraform-bin with an absolute path")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        resolved = shutil.which(str(path))
        if not resolved:
            raise ValueError(f"Terraform executable was not found: {path}")
        path = Path(resolved)
    path = path.resolve(strict=True)
    mode = path.stat().st_mode
    if not stat.S_ISREG(mode) or not os.access(path, os.X_OK):
        raise ValueError(f"Terraform binary is not an executable regular file: {path}")
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError(f"Terraform binary must not be group- or world-writable: {path}")
    for parent in (path, *path.parents):
        if parent == Path("/tmp") or parent == Path("/private/tmp"):
            raise ValueError(f"Terraform binary must not resolve beneath a shared temporary directory: {path}")
    return str(path)


def current_cloud_run_image(job_name: str, *, runner: Runner = subprocess.run) -> str:
    command = [
        "gcloud",
        "run",
        "jobs",
        "describe",
        job_name,
        "--region=us-central1",
        f"--project={PROJECT_ID}",
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


def default_vars(existing: dict[str, str], *, runner: Runner = subprocess.run) -> dict[str, str]:
    values = dict(existing)
    if SLACK_CHANNEL:
        values.setdefault("cron_alert_notification_channels", f'["{SLACK_CHANNEL}"]')
    for variable, job_name in (
        ("wdpa_monthly_image", "wdpa-monthly"),
        ("sea_ice_daily_image", "sea-ice-daily"),
        ("eamlis_monthly_image", "eamlis-monthly"),
    ):
        if variable not in values:
            values[variable] = current_cloud_run_image(job_name, runner=runner)
    return values


def var_flags(values: Mapping[str, str]) -> list[str]:
    flags: list[str] = []
    for key, value in values.items():
        flags.extend(["-var", f"{key}={value}"])
    return flags


def plan_command(terraform_bin: str, plan_file: Path, values: Mapping[str, str]) -> list[str]:
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


def show_command(terraform_bin: str, plan_file: Path) -> list[str]:
    return [terraform_bin, f"-chdir={PROD_DIR}", "show", "-json", str(plan_file)]


def resource_change_summary(plan: Mapping[str, Any]) -> list[str]:
    summary = []
    for change in plan.get("resource_changes", []):
        actions = change.get("change", {}).get("actions", [])
        if actions in ([], ["no-op"], ["read"]):
            continue
        summary.append(f"{'/'.join(actions)} {change.get('address')}")
    return summary


def has_destructive_changes(plan: Mapping[str, Any]) -> bool:
    return any(
        "delete" in change.get("change", {}).get("actions", [])
        for change in plan.get("resource_changes", [])
    )


def read_command_value(stage: str, command: list[str], *, runner: Runner) -> str:
    result = run_command(command, runner=runner)
    ensure_success(stage, result, command)
    value = result.stdout.strip()
    if not value:
        raise StageFailure(stage, command, 1, "command returned an empty value")
    return value


def operator_identity(*, runner: Runner) -> str:
    return read_command_value(
        "read active gcloud operator",
        ["gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
        runner=runner,
    )


def git_sha(*, runner: Runner) -> str:
    return read_command_value("read Git SHA", ["git", "rev-parse", "HEAD"], runner=runner)


def terraform_version(terraform_bin: str, *, runner: Runner) -> str:
    raw = read_command_value("read Terraform version", [terraform_bin, "version", "-json"], runner=runner)
    try:
        return str(json.loads(raw)["terraform_version"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise StageFailure("read Terraform version", [terraform_bin, "version", "-json"], 1, raw) from exc


def plan_sha256(plan_file: Path) -> str:
    return hashlib.sha256(plan_file.read_bytes()).hexdigest()


def write_audit_event(payload: Mapping[str, Any], *, runner: Runner) -> None:
    command = [
        "gcloud",
        "logging",
        "write",
        AUDIT_LOG_NAME,
        json.dumps(dict(payload), sort_keys=True, separators=(",", ":")),
        "--payload-type=json",
        f"--project={PROJECT_ID}",
        "--severity=NOTICE",
    ]
    result = run_command(command, runner=runner)
    ensure_success("write break-glass audit event", result, command)


def send_summary(
    *,
    title: str,
    status: str,
    operator: str,
    reason: str,
    plan_hash: str,
    actions: Sequence[str],
    dry_run: bool,
) -> None:
    notify(
        title=title,
        body=(
            f"*Operator:* `{operator}`\n"
            f"*Reason:* {reason}\n"
            f"*Plan SHA-256:* `{plan_hash}`\n"
            f"*Actions:*\n```{chr(10).join(actions) or 'no changes'}```"
        ),
        status=status,
        dry_run=dry_run,
    )


def send_failure_summary(exc: StageFailure, *, dry_run: bool) -> None:
    notify(
        title="Terraform prod break-glass apply failed",
        body=(
            f"*Stage:* `{exc.stage}`\n"
            f"*Exit code:* `{exc.returncode}`\n"
            f"*Command:* `{' '.join(exc.command)}`\n"
            f"*Output tail:*\n```{tail_text(exc.output)}```"
        ),
        status="error",
        dry_run=dry_run,
    )


def run_apply(
    argv: Sequence[str],
    *,
    runner: Runner = subprocess.run,
    binary_resolver: Callable[[str | None], str] = resolve_terraform_binary,
    input_fn: Callable[[str], str] = input,
    stdin_isatty: Callable[[], bool] = sys.stdin.isatty,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--break-glass", action="store_true", help="Acknowledge this is an approved emergency.")
    parser.add_argument("--reason", required=True, help="Human-readable emergency rationale for the audit record.")
    parser.add_argument("--allow-destroy", action="store_true", help="Allow plans containing delete or replace actions.")
    parser.add_argument("--terraform-bin", default=os.environ.get("TERRAFORM_BIN"))
    parser.add_argument("--var", action="append", default=[], help="Terraform variable in key=value form.")
    parser.add_argument("--slack-dry-run", action="store_true")
    args = parser.parse_args(list(argv))

    if not args.break_glass:
        parser.error("--break-glass is required; normal production applies must use protected GitHub Actions")
    if not args.reason.strip():
        parser.error("--reason must not be empty")
    if not stdin_isatty():
        parser.error("break-glass apply requires an interactive TTY")

    try:
        terraform_bin = binary_resolver(args.terraform_bin)
        values = default_vars(parse_var_args(args.var), runner=runner)
        with tempfile.TemporaryDirectory(prefix="shared-datasets-prod-breakglass-") as temp_dir:
            os.chmod(temp_dir, 0o700)
            plan_file = Path(temp_dir) / "prod.tfplan"
            previous_umask = os.umask(0o077)
            try:
                plan = plan_command(terraform_bin, plan_file, values)
                plan_result = run_command(plan, runner=runner)
                ensure_success("plan", plan_result, plan)
            finally:
                os.umask(previous_umask)
            if not plan_file.is_file():
                raise StageFailure("plan", plan, 1, "Terraform did not create the saved plan file")
            os.chmod(plan_file, 0o600)

            show = show_command(terraform_bin, plan_file)
            show_result = run_command(show, runner=runner)
            ensure_success("show plan JSON", show_result, show)
            try:
                plan_json = json.loads(show_result.stdout)
            except json.JSONDecodeError as exc:
                raise StageFailure("show plan JSON", show, 1, show_result.stdout) from exc
            actions = resource_change_summary(plan_json)
            if has_destructive_changes(plan_json) and not args.allow_destroy:
                raise StageFailure(
                    "destructive plan authorization",
                    plan,
                    2,
                    "plan contains delete or replace actions; pass --allow-destroy after reviewing them",
                )

            plan_hash = plan_sha256(plan_file)
            operator = operator_identity(runner=runner)
            revision = git_sha(runner=runner)
            version = terraform_version(terraform_bin, runner=runner)
            print("\n".join(actions) or "No resource changes.")
            confirmation = f"apply {PROJECT_ID} {plan_hash}"
            if input_fn(f"Type {confirmation!r} to apply this exact plan: ").strip() != confirmation:
                raise StageFailure("operator confirmation", plan, 2, "confirmation did not match")
            if plan_sha256(plan_file) != plan_hash:
                raise StageFailure("plan integrity", plan, 2, "saved plan changed after confirmation")

            audit_base = {
                "operator": operator,
                "reason": args.reason.strip(),
                "git_sha": revision,
                "terraform_version": version,
                "plan_sha256": plan_hash,
                "resource_actions": actions,
            }
            write_audit_event({**audit_base, "event": "apply_started"}, runner=runner)
            apply = apply_command(terraform_bin, plan_file)
            apply_result = run_command(apply, runner=runner)
            ensure_success("apply", apply_result, apply)
            write_audit_event({**audit_base, "event": "apply_succeeded"}, runner=runner)
            send_summary(
                title="Terraform prod break-glass apply succeeded",
                status="success",
                operator=operator,
                reason=args.reason.strip(),
                plan_hash=plan_hash,
                actions=actions,
                dry_run=args.slack_dry_run,
            )
            print(apply_result.stdout)
            return 0
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except StageFailure as exc:
        send_failure_summary(exc, dry_run=args.slack_dry_run)
        print(exc.output, file=sys.stderr)
        return exc.returncode


def main() -> int:
    return run_apply(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
