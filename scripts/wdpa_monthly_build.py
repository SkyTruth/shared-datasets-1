#!/usr/bin/env python3
"""Inspect WDPA readiness or collect a completed monthly build for review."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingestion.common import publication as p
from ingestion.wdpa_monthly import artifact_bundle as bundles
from scripts import wdpa_processing_gate as gate
from scripts.release_contracts import retained_execution_errors
from scripts.terraform_plan_allowlist import blocked_changes

PROJECT = "shared-datasets-1"
LOCATION = f"projects/{PROJECT}/locations/us-central1"
BUILD_JOB = "module.wdpa_monthly_build_job.google_cloud_run_v2_job.this"


def check_plan(plan, image, config_digest):
    p.require(re.fullmatch(r"us-central1-docker\.pkg\.dev/shared-datasets-1/shared-datasets-jobs/wdpa-monthly-build@sha256:[0-9a-f]{64}", image)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", config_digest), "builder image and configuration must be immutable")
    errors = blocked_changes(plan, allowed_exact={BUILD_JOB}, allowed_patterns=[], block_deletes=True)
    resources = [item for item in plan.get("resource_changes", []) if item.get("address") == BUILD_JOB]
    p.require(len(resources) == 1, "plan must contain the monthly build job")
    job = resources[0]["change"]["after"]
    task = job["template"][0]["template"][0]
    container = task["containers"][0]
    env = {item["name"]: item.get("value") for item in container.get("env", [])}
    p.require(not errors and job["name"] == "wdpa-monthly-build"
        and job["template"][0]["task_count"] == 1 and job["template"][0]["parallelism"] == 1
        and task["service_account"] == f"wdpa-monthly-build@{PROJECT}.iam.gserviceaccount.com"
        and len(task["containers"]) == 1 and container["image"] == image
        and container["command"] == ["python", "-m", "ingestion.wdpa_monthly.build"]
        and container["resources"][0]["limits"] == {"cpu": "4", "memory": "8Gi"}
        and task["timeout"] == "86400s" and task["max_retries"] == 0
        and len(task["volumes"]) == 1 and task["volumes"][0]["name"] == "work"
        and task["volumes"][0]["empty_dir"][0] == {"medium": "DISK", "size_limit": "100Gi"}
        and container["volume_mounts"] == [{"name": "work", "mount_path": "/work"}]
        and env == {"GOOGLE_CLOUD_PROJECT": PROJECT, "TMPDIR": "/work/tmp",
            "SHARED_DATASETS_WORKDIR": "/work/shared-datasets-1", "WDPA_BUILD_IMAGE": image,
            "WDPA_BUILD_IMAGE_CONFIG_DIGEST": config_digest},
        "monthly builder plan changes scope, identity, image or resource contract: " + "; ".join(errors))


def cloud_get(service, path):
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    with AuthorizedSession(credentials) as session:
        response = session.get(f"https://{service}.googleapis.com/{path}", timeout=60)
        response.raise_for_status()
        return response.json()


def next_window(now):
    """Next invocation of the fixed 09:00 UTC, days 1–10 schedule."""
    now = now.astimezone(dt.UTC)
    candidate = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += dt.timedelta(days=1)
    if candidate.day > 10:
        candidate = (candidate.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return candidate.date().replace(day=1)


def readiness(evidence, job, scheduler, month):
    errors = gate.check(evidence)
    accepted = evidence.get("build", {}).get("run_date")
    if accepted != month.isoformat():
        errors.append("prepare, approve and deploy the required month's retained build")
    containers = job.get("template", {}).get("template", {}).get("containers", [])
    env = {item["name"]: item.get("value") for item in containers[0].get("env", [])} if len(containers) == 1 else {}
    try:
        deployed = json.loads(env.get("WDPA_PROMOTION_BUNDLE", "null"))
    except (TypeError, ValueError):
        deployed = None
    if deployed != evidence.get("build", {}).get("artifact_bundle"):
        errors.append("deployed bundle differs from acceptance")
    if "RUN_DATE" in env:
        errors.append("RUN_DATE must remain execution-only")
    if scheduler.get("schedule") != "0 9 1-10 * *" or scheduler.get("timeZone") != "UTC":
        errors.append("unexpected WDPA scheduling window")
    if scheduler.get("state") not in {"ENABLED", "PAUSED"}:
        errors.append("unknown scheduler state")
    return {"required_month": month.strftime("%Y-%m"), "accepted_date": accepted,
        "scheduler_state": scheduler.get("state"), "ready": not errors, "reasons": errors}


def collect(execution, output):
    from google.cloud import storage
    p.require(gate.build_job(execution) == "wdpa-monthly-build", "collect requires a monthly builder execution")
    facts = {"execution": cloud_get("run", f"v2/{LOCATION}/jobs/wdpa-monthly-build/executions/{execution}")}
    client = storage.Client(project=PROJECT)
    blob = client.bucket(bundles.BUCKET).blob(f"{bundles.PREFIX}{execution}/build-bundle.json")
    blob.reload()
    p.require(0 < blob.size <= 1024 * 1024, "invalid build descriptor size")
    raw = blob.download_as_bytes(if_generation_match=int(blob.generation), checksum="crc32c")
    bundle = p.strict_json(raw)
    bundles.check_bundle(bundle, producer_source=gate.source_digest())
    report = bundle["report"]
    p.require(report["cloud_execution"] == execution, "descriptor belongs to another execution")
    reference = {"uri": f"gs://{bundles.BUCKET}/{blob.name}", "generation": int(blob.generation),
        "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    build = {**report, "artifact_bundle": reference}
    p.require(not retained_execution_errors({"build": build}, facts), "builder did not reach verified terminal success")
    evidence = {"schema_version": 3, "source_tree_sha256": report["source_tree_sha256"],
        "disk_quota_approved": True, "build": build, "compatibility_sample": report["compatibility_sample"],
        "promotion_pr": None, "promotion_plan": f".github/dataset-plans/wdpa-build-{reference['sha256']}.json"}
    errors = gate.check_build(build) + gate.check_compatibility(evidence, [build])
    p.require(not errors, "; ".join(errors))
    plan = {"schema_version": 1, "kind": "wdpa_owned_build_promotion",
        **{key: build[key] for key in ("artifact_bundle", "cloud_execution", "cloud_image", "image_digest", "source_tree_sha256", "run_date", "assets")}}
    evidence["evidence_files"] = {}
    for name, payload in (("build-bundle.json", bundle), ("cloud-facts.json", facts)):
        relative = f"docs/wdpa-processing-evidence/{execution}/{name}"
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw if name == "build-bundle.json" else p.canonical(payload))
        evidence["evidence_files"][name] = {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    for relative, payload in ((evidence["promotion_plan"], plan), ("catalog/wdpa-processing-acceptance.json", evidence)):
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    (output / "promotion-pr.md").write_text("Set promotion_pr to this PR's number before review.\n\n```shared-datasets-publish-plan\n" + json.dumps(plan, indent=2, sort_keys=True) + "\n```\n")
    print(f"Prepared unapproved evidence in {output}; set promotion_pr and obtain exact-head review.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("readiness")
    check.add_argument("--run-date", type=gate.monthly_date)
    check.add_argument("--allow-paused", action="store_true", help="Report a paused schedule without failing the monitoring check")
    gather = commands.add_parser("collect")
    gather.add_argument("--execution", required=True)
    gather.add_argument("--out", type=Path, required=True)
    plan = commands.add_parser("check-plan")
    plan.add_argument("--plan-json", required=True, type=Path)
    plan.add_argument("--image", required=True)
    plan.add_argument("--config-digest", required=True)
    args = parser.parse_args()
    if args.command == "check-plan":
        check_plan(json.loads(args.plan_json.read_text()), args.image, args.config_digest)
        return 0
    if args.command == "collect":
        args.out.mkdir(parents=True, exist_ok=False)
        collect(args.execution, args.out)
        return 0
    evidence = json.loads(gate.EVIDENCE.read_text())
    result = readiness(evidence, cloud_get("run", f"v2/{LOCATION}/jobs/wdpa-monthly"),
        cloud_get("cloudscheduler", f"v1/{LOCATION}/jobs/wdpa-monthly"),
        args.run_date or next_window(dt.datetime.now(dt.UTC)))
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write("### WDPA month readiness\n\n```json\n" + rendered + "\n```\n")
    return 0 if result["ready"] or (args.allow_paused and result["scheduler_state"] == "PAUSED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
