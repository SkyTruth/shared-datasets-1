#!/usr/bin/env python3
"""Offline release contracts shared by preflight and PR CI.

Checks reviewed evidence and declared dependencies. Live installed reset state
and IAM propagation are separately checked by trusted production code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml
from scripts import reviewed_dataset_plan as plans
from scripts import wdpa_processing_gate as wdpa
from scripts.deployment_permissions import MONITORING_PERMISSIONS, PROJECT_PERMISSIONS, SECRET_PERMISSIONS
from scripts.deployment_revision import TERRAFORM_SYNCS

DEPLOYS = {"wdpa": "wdpa-monthly", "eamlis": "eamlis-monthly", "sea-ice": "sea-ice-daily"}
RESET_ASSETS = {"wdpa": ("wdpa-marine", "wdpa-terrestrial"), "sea-ice": ("ims-sea-ice-extent",)}
PLAN_PROBE_WORKFLOWS = (
    "prod-terraform-target-apply.yml", "wdpa-monthly-deploy.yml", "eamlis-monthly-deploy.yml",
    "sea-ice-daily-deploy.yml", "wdpa-processing-validation-deploy.yml",
    "pmtiles-cdn-sync.yml", "catalog-viewer-deploy.yml",
)
IMAGE_PROBE_WORKFLOWS = (
    "wdpa-monthly-deploy.yml", "eamlis-monthly-deploy.yml", "sea-ice-daily-deploy.yml",
    "wdpa-processing-validation-deploy.yml", "catalog-viewer-deploy.yml",
)


def workflow(root, name):
    return yaml.safe_load((root / ".github/workflows" / name).read_text())


def role_permissions(text, name):
    match = re.search(r'resource "google_project_iam_custom_role" "' + re.escape(name) + r'"\s*\{(.*?)\n\}', text, re.S)
    if not match:
        return set()
    permissions = re.search(r'permissions\s*=\s*\[(.*?)\]', match[1], re.S)
    return set(re.findall(r'"([a-zA-Z0-9.]+)"', permissions[1])) if permissions else set()


def saved_plan_permission_contract(root):
    """Every automatic protected apply must check that saved plan's operations."""
    errors = []
    path = r'''["']?([^\s"']+\.%s)["']?'''
    show = re.compile(r"show\s+-json\s+" + path % "tfplan" + r"\s*>\s*" + path % "json")
    probe = re.compile(r"deployment_permissions\.py\s+.*--plan-json\s+" + path % "json")
    apply = re.compile(r"(?:terraform_retry\.sh|\bterraform\b).*\sapply(?:\s|$)")
    binary = re.compile(path % "tfplan")
    target = re.compile(r'''--target\s+["']?([^\s"']+)["']?''')
    for filename in PLAN_PROBE_WORKFLOWS:
        applies = 0
        for job in workflow(root, filename)["jobs"].values():
            saved, checked = {}, set()
            for step in job.get("steps", []):
                run = re.sub(r"\$\{([A-Z_]+)\}", r"$\1", step.get("run", "").replace("\\\n", " "))
                for line in run.splitlines():
                    rendered = show.search(line)
                    if rendered:
                        saved[rendered[1]] = rendered[2]
                        checked.discard(rendered[2])  # A newly rendered plan invalidates an earlier probe.
                    tested = probe.search(line)
                    if tested and target.search(line) and str(step.get("if", "")).strip().casefold() not in {"false", "${{ false }}"}:
                        selected = target.search(line)[1]
                        # Cache invalidation is a post-apply operation and must
                        # remain in the CDN plan contract, even on no-change plans.
                        if filename != "pmtiles-cdn-sync.yml" or not tested[1].endswith("/pmtiles-cdn-sync.tfplan.json") or selected == "pmtiles-cdn":
                            checked.add(tested[1])
                    if apply.search(line):
                        applies += 1
                        candidate = binary.search(line)
                        if not candidate or saved.get(candidate[1]) not in checked:
                            errors.append(f"{filename}/{step.get('name')}: saved-plan permission probe must precede each apply of its exact JSON")
        if not applies:
            errors.append(f"{filename}: saved-plan apply boundary is missing or unrecognized")
    return errors


def image_permission_contract(root):
    """Operational repository authority must be proven before image mutation."""
    errors = []
    probe = re.compile(r"^\s*(?:uv\s+run(?:\s+--no-sync)?\s+)?python(?:3)?\s+scripts/deployment_permissions\.py\s+--target\s+artifact-registry-images\s*$")
    for filename in IMAGE_PROBE_WORKFLOWS:
        pushes = 0
        for job in workflow(root, filename)["jobs"].values():
            checked = False
            for step in job.get("steps", []):
                enabled = str(step.get("if", "")).strip().casefold() not in {"false", "${{ false }}"}
                for line in step.get("run", "").replace("\\\n", " ").splitlines():
                    if enabled and probe.fullmatch(line):
                        checked = True
                    if re.search(r"\bdocker\s+push\s", line) and not line.lstrip().startswith("#"):
                        pushes += 1
                        if not checked:
                            errors.append(f"{filename}/{step.get('name')}: operational image permission probe must precede every Docker push")
                    # Pulling the accepted producer image is also a repository
                    # operation; project-policy hints cannot establish access.
                    if re.search(r"\bdocker\s+pull\s", line) and not line.lstrip().startswith("#") and not checked:
                        errors.append(f"{filename}/{step.get('name')}: operational image permission probe must precede the retained image pull")
        if not pushes:
            errors.append(f"{filename}: Docker push boundary is missing or unrecognized")
    return errors


def iam_contract(root):
    errors = []
    text = (root / "terraform/envs/prod/scheduled_ingestion_deploy_iam.tf").read_text()
    for role, expected in (("scheduled_ingestion_deployer", PROJECT_PERMISSIONS), ("translation_notice_iam_manager", SECRET_PERMISSIONS)):
        missing = set(expected) - role_permissions(text, role)
        if missing:
            errors.append(f"{role}: missing declared permissions {sorted(missing)}")
    iam = workflow(root, "scheduled-ingestion-deploy-iam-sync.yml")["jobs"]
    if iam["sync"].get("needs") != "bootstrap":
        errors.append("translation secret bootstrap must precede runtime grants")
    bootstrap_targets = iam["bootstrap"].get("with", {}).get("targets", "")
    if "google_project_iam_member.github_actions_translation_notice_iam_manager" not in bootstrap_targets:
        errors.append("translation secret authority has no explicit bootstrap target")
    if iam["sync"].get("with", {}).get("readiness_target") != "ingestion-iam":
        errors.append("runtime grants must verify live secret permission readiness after bootstrap")
    monitoring = (root / "terraform/envs/prod/monitoring_alert_policy_iam.tf").read_text()
    missing = set(MONITORING_PERMISSIONS) - role_permissions(monitoring, "monitoring_alert_policy_manager")
    if missing:
        errors.append(f"monitoring alert policy manager: missing declared permissions {sorted(missing)}")
    for filename, role, expected in (
        ("artifact_registry_iam.tf", "artifact_registry_iam_policy_manager", {"artifactregistry.repositories.getIamPolicy", "artifactregistry.repositories.setIamPolicy"}),
        ("preview_terraform_iam.tf", "preview_terraform", {"iam.serviceAccounts.actAs", "iam.serviceAccounts.create", "iam.serviceAccounts.get", "iam.serviceAccounts.update", "iam.serviceAccounts.getIamPolicy", "iam.serviceAccounts.setIamPolicy"}),
        ("shared_bucket_public.tf", "pmtiles_managed_folder_sync", {"storage.managedFolders.create", "storage.managedFolders.get", "storage.managedFolders.getIamPolicy", "storage.managedFolders.setIamPolicy"}),
    ):
        missing = expected - role_permissions((root / "terraform/envs/prod" / filename).read_text(), role)
        if missing:
            errors.append(f"{role}: missing declared permissions {sorted(missing)}")
    generic = workflow(root, "prod-terraform-target-apply.yml")
    required = generic.get("on", generic.get(True))["workflow_call"]["inputs"]
    for key in ("executor_sha", "source_run_id", "source_run_attempt", "caller_workflow", "readiness_target"):
        if not required[key].get("required"):
            errors.append(f"generic Terraform apply must require {key}")
    for filename in sorted(set(TERRAFORM_SYNCS.values())):
        caller = workflow(root, filename)
        triggers = caller.get("on", caller.get(True))
        if set(triggers) != {"workflow_call", "workflow_dispatch"}:
            errors.append(f"{filename}: automatic mutation must be called after ci-ready")
        for name, job in caller["jobs"].items():
            inputs = job["with"]
            if inputs.get("caller_workflow") != filename or TERRAFORM_SYNCS.get(inputs["sync_name"]) != filename:
                errors.append(f"{filename}/{name}: target does not belong to its trusted caller")
            if not inputs.get("readiness_target"):
                errors.append(f"{filename}/{name}: live permission prerequisite is missing")
            for key in ("executor_sha", "source_run_id", "source_run_attempt"):
                if inputs.get(key) != "${{ inputs." + key + " }}" or not all(triggers[event]["inputs"][key].get("required") for event in ("workflow_call", "workflow_dispatch")):
                    errors.append(f"{filename}/{name}: exact tested {key} must be required and forwarded")
    for filename, child in (("artifact-registry-iam-sync.yml", "writer"), ("cron-alert-policy-sync.yml", "sync"), ("preview-terraform-iam-sync.yml", "sync")):
        if workflow(root, filename)["jobs"][child].get("needs") != "bootstrap":
            errors.append(f"{filename}: bootstrap authority must precede dependent mutation")
    return errors


def deployment_contract(root, target):
    name = DEPLOYS[target] + "-deploy.yml"
    value = workflow(root, name)
    triggers = value.get("on", value.get(True, {}))
    errors = []
    if "push" in triggers or "pull_request" in triggers or "workflow_call" not in triggers:
        errors.append(f"{name}: automatic deployment must be called after ci-ready")
    job = value["jobs"]["deploy"]
    if job.get("environment") != "shared-datasets-production" or job.get("concurrency") != {"group": "prod-terraform-state", "queue": "max", "cancel-in-progress": False}:
        errors.append(f"{name}: protected serialized production job required")
    steps = {s.get("name"): s for s in job["steps"]}
    checkout = steps.get("Check out repository", {}).get("with", {})
    if checkout.get("ref") != "${{ inputs.executor_sha }}" or checkout.get("fetch-depth") != 0:
        errors.append(f"{name}: exact executor SHA and complete history required")
    order = list(steps)
    bootstrap = steps.get("Check out trusted verifier", {}).get("with", {})
    if bootstrap.get("ref") != "${{ github.workflow_sha }}" or bootstrap.get("fetch-depth") != 0:
        errors.append(f"{name}: verifier must originate from immutable trusted main workflow code")
    if "--bootstrap" not in steps.get("Verify executor before candidate checkout", {}).get("run", ""):
        errors.append(f"{name}: candidate authorization must execute trusted bootstrap code")
    for prerequisite, consumer in (("Verify executor before candidate checkout", "Check out repository"), ("Verify tested main revision", "Authenticate to Google Cloud"), ("Check prior deployment before rebuilding", "Authenticate to Google Cloud"), ("Check runtime verification window", "Configure Docker for Artifact Registry"), ("Verify live deployment permissions", "Claim tested deployment revision"), ("Claim tested deployment revision", "Terraform apply")):
        if prerequisite not in order or consumer not in order or order.index(prerequisite) >= order.index(consumer):
            errors.append(f"{name}: {prerequisite} must precede {consumer}")
    for slug in RESET_ASSETS.get(target, ()):
        valid = False
        for path in (root / ".github/dataset-plans" / slug).glob("**/*.json"):
            raw = path.read_bytes()
            document = plans.read_document(raw, path=str(path.relative_to(root)))
            if document.get("publish", {}).get("identity_reset"):
                valid = True
        if not valid:
            errors.append(f"{slug}: missing checked-in immutable reset plan; install state before deployment")
    return errors


def retained_evidence(root):
    original_root = wdpa.ROOT
    try:
        wdpa.ROOT = root
        evidence = json.loads((root / "catalog/wdpa-processing-acceptance.json").read_text())
        errors = wdpa.check(evidence)
        files = evidence.get("evidence_files", {})
        if not files:
            errors.append("checked-in retained build evidence is missing")
        for name, item in files.items():
            path = (root / item["path"]).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                errors.append(f"retained evidence bytes changed or missing: {name}")
        # Producer identity is deliberately independent of current consumer code.
        # The live gate checks the accepted registry image's producer fingerprint.
        tf = (root / "terraform/envs/prod/wdpa_monthly.tf").read_text()
        if "WDPA_PROMOTION_BUNDLE" not in tf or ".build.artifact_bundle" not in tf:
            errors.append("scheduler has no retained bundle when manual overrides are absent")
        if re.search(r"(?m)^\s*RUN_DATE\s*=", tf):
            errors.append("scheduler must not freeze the calendar date")
        return errors
    finally:
        wdpa.ROOT = original_root


def check(root, targets):
    errors = iam_contract(root) + saved_plan_permission_contract(root) + image_permission_contract(root)
    for target in sorted(targets & set(DEPLOYS)):
        errors += deployment_contract(root, target)
    if "wdpa" in targets:
        errors += retained_evidence(root)
    if "wdpa-processing" in targets:
        wdpa_workflow = workflow(root, "wdpa-processing-validation-deploy.yml")
        runs = "\n".join(step.get("run", "") for step in wdpa_workflow["jobs"]["deploy"]["steps"])
        if "actions/download-artifact@v4" not in json.dumps(wdpa_workflow) or "--pre-cloud" not in runs:
            errors.append("isolated producer deployment must consume tested bytes and staged evidence")
        if "wdpa-monthly-deploy.yml" in json.dumps(wdpa_workflow):
            errors.append("isolated producer bootstrap must not launch unready production dependents")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append", choices=["all", "wdpa", "eamlis", "sea-ice", "wdpa-processing", "iam"], required=True)
    args = parser.parse_args()
    targets = set(args.target)
    if "all" in targets:
        targets = {"wdpa", "eamlis", "sea-ice", "wdpa-processing", "iam"}
    errors = check(ROOT, targets)
    if errors:
        parser.exit(1, "RELEASE_CONTRACT_NOT_READY:\n" + "\n".join(errors) + "\n")
    print("Offline release contracts passed. Live IAM and installed publication state still require protected verification.")


if __name__ == "__main__":
    main()
