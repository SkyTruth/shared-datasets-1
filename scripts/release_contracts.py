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

from scripts import reviewed_dataset_plan as plans
from scripts import wdpa_processing_gate as wdpa
from scripts.deployment_permissions import MONITORING_PERMISSIONS, PROJECT_PERMISSIONS, SECRET_PERMISSIONS
from scripts.deployment_revision import TARGET_WORKFLOWS
from scripts import deployment_receipt_contracts as receipts
from scripts.ingestion_targets import DEPLOY_TARGETS as INGESTION_CONFIGS
from scripts import cdn_plan_readiness as cdn
from scripts import terraform_target_contracts as terraform_targets
from scripts.terraform_target_contracts import workflow

DEPLOYS = {config["selection"].replace("_", "-"): target for target, config in INGESTION_CONFIGS.items()}
RESET_ASSETS = {"wdpa": ("wdpa-marine", "wdpa-terrestrial"), "sea-ice": ("ims-sea-ice-extent",)}
PLAN_PROBE_WORKFLOWS = (
    "prod-terraform-target-apply.yml", "ingestion-deploy.yml",
    "pmtiles-cdn-sync.yml", "catalog-viewer-deploy.yml",
)
IMAGE_PROBE_WORKFLOWS = (
    "ingestion-deploy.yml",
    "catalog-viewer-deploy.yml",
)


def role_permissions(root, filename, name, *, revision=None):
    return set(terraform_targets.resource_index(root, revision)['terraform/envs/prod/' + filename].get(
        'google_project_iam_custom_role.' + name, {}).get('permissions', []))


def saved_plan_permission_contract(root, filenames=PLAN_PROBE_WORKFLOWS):
    """Every automatic protected apply must check that saved plan's operations."""
    errors = []
    path = r'''["']?([^\s"']+\.%s)["']?'''
    show = re.compile(r"show\s+-json\s+" + path % "tfplan" + r"\s*>\s*" + path % "json")
    probe = re.compile(r"deployment_permissions\.py\s+.*--plan-json\s+" + path % "json")
    apply = re.compile(r"(?:terraform_retry\.sh|\bterraform\b).*\sapply(?:\s|$)")
    binary = re.compile(path % "tfplan")
    target = re.compile(r'''--target\s+["']?([^\s"']+)["']?''')
    bootstrap_guard = re.compile(r"cdn_plan_readiness\.py\s+check-bootstrap\s+--plan-json\s+" + path % "json")
    for filename in filenames:
        applies = 0
        for job in workflow(root, filename)["jobs"].values():
            saved, checked, role_plans, guarded = {}, set(), set(), set()
            for step in receipts.expanded_steps(root, job):
                run = re.sub(r"\$\{([A-Z_]+)\}", r"$\1", step.get("run", "").replace("\\\n", " "))
                for line in run.splitlines():
                    if '-target=' + cdn.ROLE_ADDRESS in line:
                        role_plans.update(binary.findall(run))
                    rendered = show.search(line)
                    if rendered:
                        saved[rendered[1]] = rendered[2]
                        checked.discard(rendered[2])  # A newly rendered plan invalidates an earlier probe.
                        guarded.discard(rendered[2])
                    tested = probe.search(line)
                    guard = bootstrap_guard.search(line)
                    if guard and receipts.enabled(step) and not step.get('continue-on-error'):
                        guarded.add(guard[1])
                    if tested and target.search(line) and receipts.enabled(step) and not step.get("continue-on-error"):
                        selected = target.search(line)[1]
                        # Cache invalidation is a post-apply operation and must
                        # remain in the CDN plan contract, even on no-change plans.
                        if (filename != "pmtiles-cdn-sync.yml" or selected == "pmtiles-cdn"
                                or tested[1] in guarded and tested[1] in {saved.get(plan) for plan in role_plans}):
                            checked.add(tested[1])
                    if filename == "pmtiles-cdn-sync.yml" and receipts.command({'run': line}, 'start'):
                        if len(role_plans) != 1 or not any(saved.get(plan) in guarded & checked for plan in role_plans):
                            errors.append('CDN exact role guard and saved-plan permissions must precede the signed claim')
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
            for step in receipts.expanded_steps(root, job):
                enabled = receipts.enabled(step) and not step.get("continue-on-error")
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
    terraform_targets.resource_index.cache_clear()
    errors = []
    syncs = {name: item['workflow'] for name, item in terraform_targets.registry(root).items()}
    errors += cdn.bootstrap_definition_errors((root / 'terraform/envs/prod/shared_bucket_public.tf').read_text())
    errors += cdn.routing_contract_errors(root)
    errors += saved_plan_permission_contract(root, ('pmtiles-cdn-sync.yml',))
    for path in (root / '.github/workflows').glob('*.yml'):
        if path.name != 'pmtiles-cdn-sync.yml' and cdn.ROLE_ADDRESS in path.read_text():
            errors.append(f'{path.name}: URL-map role adoption belongs only to the constrained CDN bootstrap')
    for role, expected in (("scheduled_ingestion_deployer", PROJECT_PERMISSIONS), ("translation_notice_iam_manager", SECRET_PERMISSIONS)):
        missing = set(expected) - role_permissions(root, "scheduled_ingestion_deploy_iam.tf", role)
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
    missing = set(MONITORING_PERMISSIONS) - role_permissions(root, "monitoring_alert_policy_iam.tf", "monitoring_alert_policy_manager")
    if missing:
        errors.append(f"monitoring alert policy manager: missing declared permissions {sorted(missing)}")
    for filename, role, expected in (
        ("artifact_registry_iam.tf", "artifact_registry_iam_policy_manager", {"artifactregistry.repositories.getIamPolicy", "artifactregistry.repositories.setIamPolicy"}),
        ("preview_terraform_iam.tf", "preview_terraform", {"iam.serviceAccounts.actAs", "iam.serviceAccounts.create", "iam.serviceAccounts.get", "iam.serviceAccounts.update", "iam.serviceAccounts.getIamPolicy", "iam.serviceAccounts.setIamPolicy"}),
        ("shared_bucket_public.tf", "pmtiles_managed_folder_sync", {"storage.managedFolders.create", "storage.managedFolders.get", "storage.managedFolders.getIamPolicy", "storage.managedFolders.setIamPolicy"}),
    ):
        missing = expected - role_permissions(root, filename, role)
        if missing:
            errors.append(f"{role}: missing declared permissions {sorted(missing)}")
    generic = workflow(root, "prod-terraform-target-apply.yml")
    errors += receipts.terraform_boundary(generic)
    required = generic.get("on", generic.get(True))["workflow_call"]["inputs"]
    for key in ("executor_sha", "source_run_id", "source_run_attempt", "caller_workflow", "sync_name"):
        if not required[key].get("required"):
            errors.append(f"generic Terraform apply must require {key}")
    for filename in sorted(set(syncs.values())):
        caller = workflow(root, filename)
        triggers = caller.get("on", caller.get(True))
        if set(triggers) != {"workflow_call", "workflow_dispatch"}:
            errors.append(f"{filename}: automatic mutation must be called after ci-ready")
        for name, job in caller["jobs"].items():
            if job.get("uses") != "./.github/workflows/prod-terraform-target-apply.yml":
                if filename not in TARGET_WORKFLOWS.values():
                    errors.append(f"{filename}/{name}: unregistered inline deployment")
                continue
            inputs = job["with"]
            if inputs.get("caller_workflow") != filename or syncs.get(inputs["sync_name"]) != filename:
                errors.append(f"{filename}/{name}: target does not belong to its trusted caller")
            if not inputs.get("readiness_target"):
                errors.append(f"{filename}/{name}: live permission prerequisite is missing")
            for key in ("executor_sha", "source_run_id", "source_run_attempt"):
                if inputs.get(key) != "${{ inputs." + key + " }}" or not all(triggers[event]["inputs"][key].get("required") for event in ("workflow_call", "workflow_dispatch")):
                    errors.append(f"{filename}/{name}: exact tested {key} must be required and forwarded")
    for filename, child, parents in (
        ("artifact-registry-iam-sync.yml", "writer", "bootstrap"),
        ("cron-alert-policy-sync.yml", "resources", "bootstrap"),
        ("cron-alert-policy-sync.yml", "sync", ["bootstrap", "resources"]),
        ("preview-terraform-iam-sync.yml", "sync", "bootstrap"),
    ):
        if workflow(root, filename)["jobs"][child].get("needs") != parents:
            errors.append(f"{filename}: bootstrap authority must precede dependent mutation")
    return errors


def deployment_contract(root, target):
    name = INGESTION_CONFIGS[DEPLOYS[target]]["workflow"]
    value = workflow(root, name)
    triggers = value.get("on", value.get(True, {}))
    errors = []
    if "push" in triggers or "pull_request" in triggers or "workflow_call" not in triggers:
        errors.append(f"{name}: automatic deployment must be called after ci-ready")
    caller = value["jobs"]["deploy"]
    if caller.get("uses") != "./.github/workflows/ingestion-deploy.yml" or caller.get("with", {}).get("target") != DEPLOYS[target]:
        return [f"{name}: registered target must call the shared ingestion executor"]
    for key in ("executor_sha", "source_run_id", "source_run_attempt"):
        if caller.get("with", {}).get(key) != "${{ inputs." + key + " }}" or not all(triggers[event]["inputs"][key].get("required") for event in ("workflow_call", "workflow_dispatch")):
            errors.append(f"{name}: exact tested {key} must be required and forwarded")
    shared = workflow(root, "ingestion-deploy.yml")
    jobs = shared["jobs"]
    if INGESTION_CONFIGS[DEPLOYS[target]]["plan_guard"] == "usage":
        guards = [step for step in jobs["apply"]["steps"] if "scripts/dataset_usage_deploy.py --plan-json" in step.get("run", "")]
        if (len(guards) != 1 or guards[0].get("continue-on-error")
                or guards[0].get("if") != "steps.window.outputs.ready == 'true' && env.PLAN_GUARD == 'usage'"):
            errors.append(f"{name}: usage telemetry saved-plan guard must gate deployment")
    if jobs["apply"].get("concurrency") != {"group": "prod-terraform-state", "queue": "max", "cancel-in-progress": False}:
        errors.append(f"{name}: protected serialized production job required")
    for job in jobs.values():
        if job.get("environment") != "shared-datasets-production":
            errors.append(f"{name}: protected production job required")
        bootstraps = [step for step in job.get("steps", []) if step.get("uses") == receipts.BOOTSTRAP]
        if len(bootstraps) != 1 or any(key in bootstraps[0] for key in ("if", "continue-on-error")) or bootstraps[0].get("with") != {
            "workflow": "${{ env.WORKFLOW_FILE }}", "executor-sha": "${{ inputs.executor_sha }}",
            "source-run-id": "${{ inputs.source_run_id }}", "source-run-attempt": "${{ inputs.source_run_attempt }}",
        }:
            errors.append(f"{name}: exact unconditional trusted bootstrap required")
        steps = job.get("steps", [])
        checkouts = [step for step in steps if step.get("uses", "").startswith("actions/checkout@")]
        checkout = checkouts[0] if len(checkouts) == 1 else {}
        if (not checkout.get("uses", "").startswith("actions/checkout@")
                or checkout.get("with", {}).get("ref") != "${{ github.workflow_sha }}"
                or checkout.get("with", {}).get("fetch-depth") != 0
                or "if" in checkout or checkout.get("continue-on-error")
                or bootstraps and steps.index(checkout) >= steps.index(bootstraps[0])):
            errors.append(f"{name}: unconditional trusted main workflow checkout with complete history required")
        if bootstraps and any(receipts.mutations(step)[1] or step.get("uses", "").startswith("google-github-actions/auth@")
                              for step in receipts.expanded_steps(root, {"steps": steps[:steps.index(bootstraps[0])]})):
            errors.append(f"{name}: trusted bootstrap must precede authentication and mutation")
    if target == "wdpa":
        tf = (root / "terraform/envs/prod/wdpa_monthly.tf").read_text()
        if "WDPA_PROMOTION_BUNDLE" in tf or re.search(r"(?m)^\s*RUN_DATE\s*=", tf):
            errors.append("WDPA scheduler must not pin a monthly bundle or calendar date")
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


def retained_execution_errors(evidence, facts):
    """Bind the immutable terminal execution snapshot to the accepted image."""
    build = evidence["build"]
    execution = facts.get("execution", {})
    try:
        job = wdpa.build_job(build.get("cloud_execution"))
    except ValueError:
        return ["retained cloud facts have an invalid build execution"]
    name = ("projects/shared-datasets-1/locations/us-central1/jobs/"
            f"{job}/executions/{build['cloud_execution']}")
    containers = execution.get("template", {}).get("containers", [])
    completed = [condition.get("state") for condition in execution.get("conditions", [])
                 if condition.get("type") == "Completed"]
    if (execution.get("name") != name or not execution.get("completionTime")
            or execution.get("taskCount") != 1 or execution.get("succeededCount") != 1
            or execution.get("failedCount", 0) != 0
            or completed != ["CONDITION_SUCCEEDED"]
            or [container.get("image") for container in containers] != [build["cloud_image"]]):
        return ["retained cloud facts must prove terminal success of the exact accepted execution and image"]
    return []


def retained_evidence(root):
    original_root = wdpa.ROOT
    try:
        wdpa.ROOT = root
        evidence = json.loads((root / "catalog/wdpa-processing-acceptance.json").read_text())
        errors = wdpa.check(evidence)
        files = evidence.get("evidence_files", {})
        if not files:
            errors.append("checked-in retained build evidence is missing")
        if "cloud-facts.json" not in files:
            errors.append("checked-in retained terminal execution evidence is missing")
        for name, item in files.items():
            path = (root / item["path"]).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                errors.append(f"retained evidence bytes changed or missing: {name}")
            elif name == "cloud-facts.json":
                errors += retained_execution_errors(evidence, json.loads(path.read_text()))
        return errors
    finally:
        wdpa.ROOT = original_root


def check(root, targets):
    errors = iam_contract(root) + saved_plan_permission_contract(root) + image_permission_contract(root) + receipts.boundaries(root) + receipts.permissions(root)
    if "dataset-usage" in targets:
        from scripts.dataset_usage_deploy import validate_rollout
        try:
            validate_rollout(root)
        except ValueError as exc:
            errors.append("dataset-usage: " + str(exc))
    for target in sorted(targets & set(DEPLOYS)):
        errors += deployment_contract(root, target)
    if "wdpa-history" in targets:
        errors += retained_evidence(root)
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append", choices=["all", *DEPLOYS, "wdpa-history", "iam", "dataset-usage"], required=True)
    args = parser.parse_args()
    targets = set(args.target)
    if "all" in targets:
        targets = {*DEPLOYS, "iam", "dataset-usage"}
    errors = check(ROOT, targets)
    if errors:
        parser.exit(1, "RELEASE_CONTRACT_NOT_READY:\n" + "\n".join(errors) + "\n")
    print("Offline release contracts passed. Live IAM and installed publication state still require protected verification.")


if __name__ == "__main__":
    main()
