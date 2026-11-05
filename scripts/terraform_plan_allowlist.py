#!/usr/bin/env python3
"""Refuse a Terraform plan that changes resources outside an explicit allowlist.

Reads `terraform show -json` output and exits nonzero when any create, update,
replace, or delete touches a resource address that is not allowlisted. This is
the single owner of the resource-change allowlist rule used by the constrained
prod Terraform apply workflows; keep it stdlib-only so workflow steps can run
it before `uv sync`.

Image targets own a closed resource scope and pin the Cloud Run job to the
expected image. EAMLIS and sea ice permit only job updates; MPAtlas also permits
first installation and its scheduler, invoker and canonical deny-policy update.
Other callers retain their declared address/action policy.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.ingestion_targets import TARGETS

IGNORED_ACTIONS = ([], ["no-op"], ["read"])
JOB_IMAGE_TARGETS = {target: config["targets"][0] for target, config in TARGETS.items()}


def split_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def wdpa_errors(after, expected_image, expected_config):
    task = after.get("template", [{}])[0].get("template", [{}])[0]
    container = task.get("containers", [{}])[0]
    env = {e.get("name"): e.get("value") for e in container.get("env", [])}
    volume = next((v for v in task.get("volumes", []) if v.get("name") == "work"), {})
    disk = volume.get("empty_dir", [{}])[0] if volume.get("empty_dir") else {}
    errors = []
    if container.get("resources", [{}])[0].get("limits") != {"cpu": "4", "memory": "8Gi"} or task.get("timeout") != "86400s":
        errors.append("unexpected WDPA processing limits")
    if env.get("WDPA_BUILD_IMAGE") != expected_image or env.get("WDPA_BUILD_IMAGE_CONFIG_DIGEST") != expected_config:
        errors.append("WDPA scheduled executions require the exact tested producer")
    if "WDPA_PROMOTION_BUNDLE" in env or "RUN_DATE" in env:
        errors.append("WDPA must not pin a monthly bundle or RUN_DATE")
    if disk.get("medium") != "DISK" or disk.get("size_limit") != "100Gi" or not any(
        m.get("name") == "work" and m.get("mount_path") == "/work" for m in container.get("volume_mounts", [])
    ):
        errors.append("WDPA requires the 100 GiB disk at /work")
    if env.get("TMPDIR") != "/work/tmp" or env.get("SHARED_DATASETS_WORKDIR") != "/work/shared-datasets-1":
        errors.append("WDPA scratch paths must use /work")
    return errors


def blocked_changes(
    plan: dict,
    *,
    allowed_exact: set[str],
    allowed_patterns: list[re.Pattern[str]],
    block_deletes: bool,
    job_image: tuple[str, str] | None = None,
    delete_only: bool = False,
    expected_config: str | None = None,
) -> list[str]:
    blocked = []
    for resource in plan.get("resource_changes", []):
        actions = resource.get("change", {}).get("actions", [])
        if actions in IGNORED_ACTIONS:
            continue
        address = resource.get("address", "")
        if (delete_only and actions != ["delete"]
                or block_deletes and "delete" in actions
                or address not in allowed_exact and not any(pattern.match(address) for pattern in allowed_patterns)
                or job_image is not None and job_image[0] == JOB_IMAGE_TARGETS["wdpa-monthly"] and actions != ["update"]):
            blocked.append(f"{'/'.join(actions)} {address}")
            continue
        if job_image is not None and address == job_image[0]:
            if actions != ["update"] and not (address == JOB_IMAGE_TARGETS["mpatlas-monthly"] and actions == ["create"]):
                blocked.append(f"{'/'.join(actions)} {address}")
                continue
            after = resource.get("change", {}).get("after", {})
            containers = after.get("template", [{}])[0].get("template", [{}])[0].get("containers", [{}])
            image = containers[0].get("image") if containers else None
            if image != job_image[1]:
                blocked.append(f"unexpected image for {address}: {image!r}")
            if address == JOB_IMAGE_TARGETS["wdpa-monthly"]:
                blocked.extend(wdpa_errors(after, job_image[1], expected_config))
    return blocked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan_json", help="Path to `terraform show -json` output for the plan.")
    parser.add_argument("--allowed-exact", help="Newline-separated resource addresses; image targets supply their closed scope.")
    parser.add_argument("--allowed-patterns", default="", help="Newline-separated allowed address regexes.")
    action_policy = parser.add_mutually_exclusive_group()
    action_policy.add_argument("--block-deletes", action="store_true", help="Refuse deletes and replacements, even for allowlisted addresses.")
    action_policy.add_argument("--delete-only", action="store_true", help="Permit only deletion of allowlisted retired resources.")
    parser.add_argument("--job-image-target", choices=sorted(JOB_IMAGE_TARGETS), help="Require the closed job scope and exact image.")
    parser.add_argument("--expected-image", help="Exact planned container image, required with --job-image-target.")
    parser.add_argument("--expected-config", help="Exact CI-tested config digest, required for WDPA.")
    parser.add_argument("--refusal-prefix", required=True, help="Message prefix printed before blocked resources.")
    args = parser.parse_args(argv)

    allowed_exact = set(split_lines(args.allowed_exact or ""))
    allowed_patterns = [re.compile(pattern) for pattern in split_lines(args.allowed_patterns)]
    job_image = None
    if args.job_image_target is not None:
        if not args.expected_image:
            parser.error("--job-image-target requires a nonempty --expected-image")
        if args.job_image_target == "wdpa-monthly" and not re.fullmatch(r"sha256:[0-9a-f]{64}", args.expected_config or ""):
            parser.error("WDPA requires --expected-config with its tested config digest")
        address = JOB_IMAGE_TARGETS[args.job_image_target]
        scope = set(TARGETS[args.job_image_target]["targets"])
        if (args.allowed_exact is not None and allowed_exact != scope) or allowed_patterns:
            parser.error("--job-image-target requires its exact resource scope and no allowed patterns")
        if args.job_image_target == "mpatlas-monthly" and not args.block_deletes:
            parser.error("the MPAtlas image target requires --block-deletes")
        allowed_exact = scope
        job_image = (address, args.expected_image)
    elif args.expected_image is not None:
        parser.error("--expected-image requires --job-image-target")
    elif args.allowed_exact is None:
        parser.error("--allowed-exact is required without --job-image-target")

    with open(args.plan_json) as file_obj:
        plan = json.load(file_obj)

    blocked = blocked_changes(
        plan,
        allowed_exact=allowed_exact,
        allowed_patterns=allowed_patterns,
        block_deletes=args.block_deletes,
        job_image=job_image,
        delete_only=args.delete_only,
        expected_config=args.expected_config,
    )
    if blocked:
        print(f"{args.refusal_prefix} because the Terraform plan changes non-allowlisted resources:")
        for item in blocked:
            print(f"- {item}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
