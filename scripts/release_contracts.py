#!/usr/bin/env python3
"""Offline release contracts shared by preflight and PR CI.

Checks reviewed evidence and declared dependencies. Live installed reset state
and IAM propagation are separately checked by trusted production code.
"""
from __future__ import annotations

import argparse
import csv
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
from scripts.deployment_permissions import MONITORING_PERMISSIONS, OPERATIONAL_LOG_SETUP_PERMISSIONS, OPERATIONAL_METRIC_SETUP_PERMISSIONS, PROJECT_PERMISSIONS, SECRET_PERMISSIONS
from scripts.deployment_revision import TERRAFORM_SYNCS, TARGET_WORKFLOWS
from scripts import deployment_receipt_contracts as receipts
from scripts import cdn_plan_readiness as cdn
from scripts import logging_setup_permissions as logging_setup
from scripts import wif_setup_permissions as wif_setup
from scripts import readonly_wif_trust as readonly_trust

DEPLOYS = {"mpatlas": "mpatlas-monthly", "wdpa": "wdpa-monthly", "eamlis": "eamlis-monthly", "sea-ice": "sea-ice-daily"}
RESET_ASSETS = {"wdpa": ("wdpa-marine", "wdpa-terrestrial"), "sea-ice": ("ims-sea-ice-extent",)}
PLAN_PROBE_WORKFLOWS = (
    "prod-terraform-target-apply.yml", "wdpa-monthly-deploy.yml", "mpatlas-monthly-deploy.yml", "eamlis-monthly-deploy.yml",
    "dataset-usage-deploy.yml", "sea-ice-daily-deploy.yml",
    "pmtiles-cdn-sync.yml", "catalog-viewer-deploy.yml",
)
IMAGE_PROBE_WORKFLOWS = (
    "wdpa-monthly-deploy.yml", "mpatlas-monthly-deploy.yml", "eamlis-monthly-deploy.yml", "sea-ice-daily-deploy.yml",
    "catalog-viewer-deploy.yml", "dataset-usage-deploy.yml",
)


def workflow(root, name):
    return yaml.safe_load((root / ".github/workflows" / name).read_text())


def role_permissions(text, name):
    match = re.search(r'resource "google_project_iam_custom_role" "' + re.escape(name) + r'"\s*\{(.*?)\n\}', text, re.S)
    if not match:
        return set()
    permissions = re.search(r'permissions\s*=\s*\[(.*?)\]', match[1], re.S)
    return set(re.findall(r'"([a-zA-Z0-9.]+)"', permissions[1])) if permissions else set()


def readonly_trust_contract(root):
    """Bind the new proposal to its exact role, existing provider and owner."""
    errors = []
    text = (root / "terraform/envs/prod/github_readonly_iam.tf").read_text()
    role = re.search(r'resource "google_project_iam_custom_role" "readonly_provider_updater"\s*\{(.*?)\n\}', text, re.S)
    if (role is None or not re.search(r'role_id\s*=\s*"' + readonly_trust.ROLE.rsplit('/', 1)[1] + '"', role[1])
            or role_permissions(text, "readonly_provider_updater") != {readonly_trust.UPDATE}):
        errors.append("read-only trust repair requires its separate update-only role")
    binding = re.search(r'resource "google_project_iam_member" "github_actions_readonly_provider_updater"\s*\{(.*?)\n\}', text, re.S)
    expected = 'project = var.project_id\nrole = google_project_iam_custom_role.readonly_provider_updater.name\nmember = "serviceAccount:${var.github_actions_terraform_service_account_email}"\ncondition {\n'
    expected += '\n'.join(key + ' = "' + value + '"' for key, value in readonly_trust.condition().items()) + '\n}'
    if binding is None or re.sub(r'\s+', '', binding[1]) != re.sub(r'\s+', '', expected):
        errors.append("read-only trust repair requires the exact protected principal and fixed-expiry binding")
    provider = re.search(r'resource "google_iam_workload_identity_pool_provider" "github_readonly"\s*\{(.*?)\n\}', text, re.S)
    expected = '''project = var.project_id
      workload_identity_pool_id = google_iam_workload_identity_pool.github.workload_identity_pool_id
      workload_identity_pool_provider_id = var.github_readonly_workload_identity_pool_provider_id
      display_name = "GitHub Actions read-only"
      description = "Restricts GitHub OIDC tokens to read-only shared-datasets bucket checks."
      disabled = false
      attribute_mapping = {
        "google.subject" = "assertion.sub"
        "attribute.actor" = "assertion.actor"
        "attribute.repository" = "assertion.repository"
        "attribute.repository_owner" = "assertion.repository_owner"
        "attribute.workflow" = "assertion.workflow"
      }
      attribute_condition = join(" && ", [
        "assertion.repository == '${var.github_repository}'",
        "''' + '(' + ' || '.join("assertion.workflow == '" + name + "'" for name in readonly_trust.WORKFLOWS) + ')' + '''",
      ])
      oidc { issuer_uri = "https://token.actions.githubusercontent.com" }'''
    if provider is None or re.sub(r'\s+', '', provider[1]) != re.sub(r'\s+', '', expected):
        errors.append("read-only trust repair must retain the exact declared provider configuration and workflow addition")
    jobs = workflow(root, "cron-alert-policy-sync.yml")["jobs"]
    for name, predecessor, readiness, targets in (
        ("readonly-bootstrap", "bootstrap", "iam-bootstrap", {"google_project_iam_custom_role.readonly_provider_updater", "google_project_iam_member.github_actions_readonly_provider_updater"}),
        ("readonly-trust", "readonly-bootstrap", "readonly-wif-trust", {readonly_trust.ADDRESS}),
    ):
        job = jobs.get(name, {})
        inputs = job.get("with", {})
        if (job.get("needs") != predecessor or inputs.get("readiness_target") != readiness
                or inputs.get("block_deletes") is not True
                or any(set(inputs.get(field, "").split()) != targets for field in ("targets", "allowed_exact"))):
            errors.append("read-only trust repair requires its ordered two-target bootstrap and exact one-target sync")
    return errors


def operational_storage_contract(root, text, inputs):
    """Keep registry-derived observer reads inside IAM's fixed condition limits."""
    errors = []
    with (root / "catalog/shared-datasets-catalog.csv").open() as stream:
        assets = [row for row in csv.DictReader(stream) if row["canonical_path"].startswith("gs://skytruth-shared-datasets-1/")
                  and ("/latest/" in row["canonical_path"] or "/releases/" in row["canonical_path"])]
    groups = (len(assets) + 5) // 6
    if not 1 <= groups <= 5:
        errors.append("observer asset registry exceeds the reviewed five IAM chunks; update exact protected targets before deployment")
    expected = {f'google_storage_bucket_iam_member.operational_observer_{surface}["{index}"]' for surface in ("receipts", "manifests") for index in range(groups)}
    for field in ("targets", "allowed_exact"):
        actual = {address for address in inputs.get(field, "").split() if address.startswith("google_storage_bucket_iam_member.operational_observer_")}
        if actual != expected:
            errors.append(f"operational {field} must cover every exact quota-safe receipt and manifest IAM chunk")
    # Exact expression templates prevent replacing paired asset boundaries with
    # independent prefix/suffix sets, or introducing an unconditional alternative.
    templates = {
        "operational_observer_receipt_conditions": """{ for index, group in local.operational_observer_asset_groups : index => join(" && ", [
            "resource.type == 'storage.googleapis.com/Object'",
            "(${join(" || ", [for scope in group : "(resource.name.startsWith('${local.shared_bucket_object_resource_prefix}${scope.root}/runs/') && resource.name.endsWith('.json'))"])})",
        ]) }""",
        "operational_observer_manifest_conditions": """{ for index, group in local.operational_observer_asset_groups : index => join(" && ", [
            "resource.type == 'storage.googleapis.com/Object'",
            "(${join(" || ", [for scope in group : "(resource.name.startsWith('${local.shared_bucket_object_resource_prefix}${scope.root}/releases/') && resource.name.endsWith('/${scope.slug}.manifest.json'))"])})",
        ]) }""",
    }
    def compact(value):
        return re.sub(r"\s+", "", value)
    for name, expected_template in templates.items():
        match = re.search(r'\n  ' + name + r'\s*=\s*(.*?)(?=\n  [a-zA-Z_]|\n\})', text, re.S)
        if match is None or compact(match[1]) != compact(expected_template):
            errors.append(f"{name}: observer reads require exact Object type and paired registered asset prefix/suffix boundaries")
    expected_groups = """{ for index, group in chunklist([
        for row in local.shared_catalog_rows : {
            slug = row.asset_slug
            root = split("/releases/", split("/latest/", replace(row.canonical_path, "gs://${var.bucket_name}/", ""))[0])[0]
        } if startswith(row.canonical_path, "gs://${var.bucket_name}/") && (strcontains(row.canonical_path, "/latest/") || strcontains(row.canonical_path, "/releases/"))
    ], 6) : tostring(index) => group }"""
    scope_match = re.search(r'\n  operational_observer_asset_groups\s*=\s*(.*?)(?=\n  [a-zA-Z_]|\n\})', text, re.S)
    if scope_match is None or compact(scope_match[1]) != compact(expected_groups):
        errors.append("observer reads must derive six-asset IAM chunks from the catalog registry")
    for surface in ("receipts", "manifests"):
        block = re.search(r'resource "google_storage_bucket_iam_member" "operational_observer_' + surface + r'"\s*\{(.*?)\n\}', text, re.S)
        conditions = "receipt" if surface == "receipts" else "manifest"
        assignments = {"for_each": f"local.operational_observer_{conditions}_conditions", "bucket": "var.bucket_name",
                       "role": "google_project_iam_custom_role.operational_observer_receipts.name", "member": "module.operational_observer_service_account.member", "expression": "each.value"}
        if block is None or any(re.search(r'(?m)^\s*' + key + r'\s*=\s*' + re.escape(value) + r'\s*$', block[1]) is None for key, value in assignments.items()):
            errors.append(f"operational_observer_{surface}: observer bindings must retain the exact read-only identity/role/bucket/condition")
    return errors


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
    errors += cdn.bootstrap_definition_errors((root / 'terraform/envs/prod/shared_bucket_public.tf').read_text())
    cdn_workflow = workflow(root, 'pmtiles-cdn-sync.yml')
    errors += cdn.routing_contract_errors(root)
    cdn_steps = {step.get('name'): step for step in cdn_workflow['jobs']['sync']['steps']}
    bootstrap = cdn_steps.get('Terraform plan PMTiles managed-folder IAM bootstrap', {}).get('run', '')
    if '-target=' + cdn.ROLE_ADDRESS not in bootstrap:
        errors.append('CDN bootstrap must target the exact existing URL-map role')
    guard = cdn_steps.get('Enforce PMTiles managed-folder IAM bootstrap allowlist', {})
    expected = 'python scripts/cdn_plan_readiness.py check-bootstrap --plan-json "${RUNNER_TEMP}/pmtiles-managed-folder-bootstrap.tfplan.json"'
    if guard.get('run') != expected or guard.get('if') != "steps.replay.outputs.proceed == 'true'":
        errors.append('CDN bootstrap must enforce exact role adoption on its saved plan')
    ordered = [
        'Export PMTiles managed-folder IAM bootstrap plan JSON',
        'Enforce PMTiles managed-folder IAM bootstrap allowlist',
        'Verify permissions required by the saved CDN bootstrap plan',
        'Record serialized CDN deployment attempt',
        'Attest serialized deployment claim before mutation',
        'Terraform apply PMTiles managed-folder IAM bootstrap',
    ]
    names = [step.get('name') for step in cdn_workflow['jobs']['sync']['steps']]
    if any(names.count(name) != 1 for name in ordered) or sorted(names.index(name) for name in ordered if name in names) != [names.index(name) for name in ordered if name in names]:
        errors.append('CDN exact role guard must follow saved-plan export and precede permissions, signed claim and bootstrap apply')
    exported = cdn_steps.get(ordered[0], {}).get('run')
    if exported != 'terraform -chdir=terraform/envs/prod show -json "${RUNNER_TEMP}/pmtiles-managed-folder-bootstrap.tfplan" > "${RUNNER_TEMP}/pmtiles-managed-folder-bootstrap.tfplan.json"':
        errors.append('CDN role guard must consume JSON exported from the exact saved bootstrap plan')
    for path in (root / '.github/workflows').glob('*.yml'):
        if path.name != 'pmtiles-cdn-sync.yml' and cdn.ROLE_ADDRESS in path.read_text():
            errors.append(f'{path.name}: URL-map role adoption belongs only to the constrained CDN bootstrap')
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
    operational = (root / "terraform/envs/prod/operational_observer.tf").read_text()
    for role, expected in (("operational_log_view_manager", set(OPERATIONAL_LOG_SETUP_PERMISSIONS)), ("operational_metric_descriptor_manager", set(OPERATIONAL_METRIC_SETUP_PERMISSIONS)),
                           ("operational_provider_creator", {wif_setup.CREATE}), ("operational_provider_reader", {wif_setup.GET}),
                           ("operational_observer_reader", {"run.executions.get", "run.executions.list", "monitoring.timeSeries.create"}), ("operational_observer_receipts", {"storage.objects.get"})):
        if role_permissions(operational, role) != expected:
            errors.append(f"{role}: operational authority must match its exact permission contract")
    log_role = re.search(r'resource "google_project_iam_custom_role" "operational_log_view_manager"\s*\{(.*?)\n\}', operational, re.S)
    if log_role is None or not re.search(r'role_id\s*=\s*"' + re.escape(logging_setup.ROLE.rsplit("/", 1)[1]) + '"', log_role[1]):
        errors.append("Logging installed-grant proof requires the exact active role identity")
    bucket_scope = re.search(r'operational_observer_log_buckets\s*=\s*toset\(\[(.*?)\]\)', operational, re.S)
    binding = re.search(r'resource "google_project_iam_member" "github_actions_operational_log_view_manager"\s*\{(.*?)\n\}', operational, re.S)
    expected_binding = """project = var.project_id
      role = google_project_iam_custom_role.operational_log_view_manager.name
      member = "serviceAccount:${var.github_actions_terraform_service_account_email}"
      condition {
        title = "operational-view-buckets-only"
        description = "No access to views in other Logging buckets."
        expression = join(" || ", [for bucket in local.operational_observer_log_buckets :
          "(resource.name == 'projects/${var.project_id}/locations/global/buckets/${bucket}' || resource.name.startsWith('projects/${var.project_id}/locations/global/buckets/${bucket}/views/'))"
        ])
      }"""
    if (bucket_scope is None or set(re.findall(r'"([^"]+)"', bucket_scope[1])) != set(logging_setup.BUCKETS)
            or binding is None or re.sub(r"\s+", "", binding[1]) != re.sub(r"\s+", "", expected_binding)):
        errors.append("Logging installed-grant proof requires the exact reviewed two-bucket Terraform binding")
    for kind, role_name, condition in (("creator", wif_setup.CREATOR_ROLE, wif_setup.condition()), ("reader", wif_setup.READER_ROLE, None)):
        label = "operational_provider_" + kind
        role = re.search(r'resource "google_project_iam_custom_role" "' + label + r'"\s*\{(.*?)\n\}', operational, re.S)
        binding = re.search(r'resource "google_project_iam_member" "github_actions_' + label + r'"\s*\{(.*?)\n\}', operational, re.S)
        expected_binding = 'project = var.project_id\nrole = google_project_iam_custom_role.' + label + '.name\nmember = "serviceAccount:${var.github_actions_terraform_service_account_email}"'
        if condition is not None:
            expected_binding += '\ncondition {\n' + '\n'.join(key + ' = "' + value + '"' for key, value in condition.items()) + '\n}'
        if (role is None or not re.search(r'role_id\s*=\s*"' + role_name.rsplit('/', 1)[1] + '"', role[1])
                or binding is None or re.sub(r'\s+', '', binding[1]) != re.sub(r'\s+', '', expected_binding)):
            errors.append("operational provider " + kind + " requires the exact protected principal, role and fixed expiry contract")
        bootstrap = workflow(root, "cron-alert-policy-sync.yml")["jobs"]["bootstrap"]["with"]
        for field in ("targets", "allowed_exact"):
            if not {"google_project_iam_custom_role." + label, "google_project_iam_member.github_actions_" + label} <= set(bootstrap.get(field, "").split()):
                errors.append("operational provider " + kind + " has no exact protected bootstrap " + field)
    errors += readonly_trust_contract(root)
    observations = workflow(root, "cron-alert-policy-sync.yml")["jobs"]["observations"]
    errors += operational_storage_contract(root, operational, observations["with"])
    if observations.get("needs") != ["bootstrap", "readonly-trust"] or observations["with"].get("readiness_target") != "operational-observer-setup" or observations["with"].get("block_deletes") is not True:
        errors.append("operational setup requires ordered bootstrap, permission readiness and blocked deletion")
    audit_address = "google_project_iam_audit_config.operational_run_data_write"
    audit_match = re.search(r'resource\s+"google_project_iam_audit_config"\s+"operational_run_data_write"\s*\{(.*?)(?=\nresource |\Z)', operational, re.S)
    if (audit_match is None or not re.search(r'service\s*=\s*"run.googleapis.com"', audit_match[1])
            or re.findall(r'log_type\s*=\s*"([^"]+)"', audit_match[1]) != ["DATA_WRITE"] or "exempted_members" in audit_match[1]):
        errors.append("operational setup must enable exact unexempted Run DATA_WRITE capture")
    for field in ("targets", "allowed_exact"):
        if audit_address not in observations["with"].get(field, "").split():
            errors.append(f"operational {field} omits required Run DATA_WRITE capture")
        metrics = {"google_monitoring_metric_descriptor.operational_reconciliation_heartbeat" + suffix for suffix in ("", "_v2")}
        actual_metrics = {address for address in observations["with"].get(field, "").split() if address.startswith("google_monitoring_metric_descriptor.")}
        if actual_metrics != metrics:
            errors.append(f"operational {field} must retain the legacy heartbeat descriptor and exact fenced v2 descriptor")
    # Derive coverage from the declarations so adding a new policy cannot leave
    # it outside the protected saved-plan/permission/receipt boundary.
    declared_policies = {
        "google_monitoring_alert_policy." + name
        for path in (root / "terraform/envs/prod").glob("*.tf")
        for name in re.findall(r'resource\s+"google_monitoring_alert_policy"\s+"([^"]+)"', path.read_text())
    }
    policy_sync = workflow(root, "cron-alert-policy-sync.yml")["jobs"]["sync"]["with"]
    for field in ("targets", "allowed_exact"):
        if set(policy_sync[field].split()) != declared_policies:
            errors.append(f"Monitoring {field} must cover exactly every declared alert policy")
    for filename, role, expected in (
        ("artifact_registry_iam.tf", "artifact_registry_iam_policy_manager", {"artifactregistry.repositories.getIamPolicy", "artifactregistry.repositories.setIamPolicy"}),
        ("preview_terraform_iam.tf", "preview_terraform", {"iam.serviceAccounts.actAs", "iam.serviceAccounts.create", "iam.serviceAccounts.get", "iam.serviceAccounts.update", "iam.serviceAccounts.getIamPolicy", "iam.serviceAccounts.setIamPolicy"}),
        ("shared_bucket_public.tf", "pmtiles_managed_folder_sync", {"storage.managedFolders.create", "storage.managedFolders.get", "storage.managedFolders.getIamPolicy", "storage.managedFolders.setIamPolicy"}),
    ):
        missing = expected - role_permissions((root / "terraform/envs/prod" / filename).read_text(), role)
        if missing:
            errors.append(f"{role}: missing declared permissions {sorted(missing)}")
    generic = workflow(root, "prod-terraform-target-apply.yml")
    steps = {step.get("name"): step for step in generic["jobs"]["sync"]["steps"]}
    auth = steps.get("Authenticate to Google Cloud", {})
    expected_auth = {
        "workload_identity_provider": "${{ env.TERRAFORM_WORKLOAD_IDENTITY_PROVIDER }}",
        "service_account": "${{ env.TERRAFORM_SERVICE_ACCOUNT }}",
        "token_format": "${{ (inputs.readiness_target == 'operational-observer-setup' || inputs.readiness_target == 'readonly-wif-trust') && 'access_token' || '' }}",
        "access_token_scopes": "https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/userinfo.email",
    }
    if (generic.get("env", {}).get("TERRAFORM_SERVICE_ACCOUNT") != "${{ vars.GCP_TERRAFORM_SERVICE_ACCOUNT }}"
            or auth.get("id") != "auth" or auth.get("uses") != "google-github-actions/auth@v2"
            or auth.get("with") != expected_auth):
        errors.append("Logging grant proof requires the protected service account's explicit scoped identity token without delegation")
    for name in ("Verify prerequisite deployment permissions", "Verify complete saved-plan mutation permissions"):
        if steps.get(name, {}).get("env", {}).get("LOGGING_SETUP_ACCESS_TOKEN") != "${{ steps.auth.outputs.access_token }}":
            errors.append(f"{name}: Logging grant proof must receive the masked scoped auth token")
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
            if job.get("uses") != "./.github/workflows/prod-terraform-target-apply.yml":
                if filename not in TARGET_WORKFLOWS.values():
                    errors.append(f"{filename}/{name}: unregistered inline deployment")
                continue
            inputs = job["with"]
            if inputs.get("caller_workflow") != filename or TERRAFORM_SYNCS.get(inputs["sync_name"]) != filename:
                errors.append(f"{filename}/{name}: target does not belong to its trusted caller")
            if not inputs.get("readiness_target"):
                errors.append(f"{filename}/{name}: live permission prerequisite is missing")
            for key in ("executor_sha", "source_run_id", "source_run_attempt"):
                if inputs.get(key) != "${{ inputs." + key + " }}" or not all(triggers[event]["inputs"][key].get("required") for event in ("workflow_call", "workflow_dispatch")):
                    errors.append(f"{filename}/{name}: exact tested {key} must be required and forwarded")
    for filename, child in (("artifact-registry-iam-sync.yml", "writer"), ("cron-alert-policy-sync.yml", "sync"), ("preview-terraform-iam-sync.yml", "sync")):
        expected = ["bootstrap", "observations"] if filename == "cron-alert-policy-sync.yml" else "bootstrap"
        if workflow(root, filename)["jobs"][child].get("needs") != expected:
            errors.append(f"{filename}: bootstrap authority must precede dependent mutation")
    return errors


def deployment_contract(root, target):
    name = DEPLOYS[target] + "-deploy.yml"
    value = workflow(root, name)
    errors = []
    caller = workflow(root, "ci.yml")["jobs"][target]
    required = {"ci-ready", "geospatial-changes", "ingestion-iam", "artifact-registry-iam"}
    conditions = ["!cancelled()", "needs.ingestion-iam.result == 'success'", "needs.artifact-registry-iam.result == 'success'"]
    if target == "mpatlas":
        required.add("cron-alert-policy")
        conditions.append("(needs.geospatial-changes.outputs.cron_alert_policy != 'true' || needs.cron-alert-policy.result == 'success')")
    conditions += ["github.event_name == 'push'", "github.ref == 'refs/heads/main'", "needs.ci-ready.result == 'success'",
                   "needs.geospatial-changes.outputs." + target.replace("-", "_") + " == 'true'"]
    if (required != set(caller.get("needs", []))
            or caller.get("if") != "${{ " + " && ".join(conditions) + " }}"):
        errors.append(f"{name}: CI caller must bypass skipped ancestors, respect cancellation and require exact successful CI/IAM prerequisites")
    triggers = value.get("on", value.get(True, {}))
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
    if target == "wdpa":
        terminal = steps.get("Verify accepted build terminal success", {})
        if (terminal.get("run") != "uv run --no-sync python scripts/release_contracts.py --target wdpa"
                or terminal.get("if") != "${{ steps.replay.outputs.proceed == 'true' && steps.window.outputs.ready == 'true' }}"
                or "Verify accepted build terminal success" not in order
                or "Prepare reviewed WDPA publication image" not in order
                or order.index("Verify accepted build terminal success") >= order.index("Prepare reviewed WDPA publication image")):
            errors.append(f"{name}: retained terminal execution verification must precede image promotion")
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
    name = ("projects/shared-datasets-1/locations/us-central1/jobs/"
            f"wdpa-processing-validation/executions/{build['cloud_execution']}")
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
    errors = iam_contract(root) + saved_plan_permission_contract(root) + image_permission_contract(root) + receipts.boundaries(root) + receipts.permissions(root)
    if "dataset-usage" in targets:
        from scripts.dataset_usage_deploy import validate_rollout
        try:
            validate_rollout(root)
        except ValueError as exc:
            errors.append("dataset-usage: " + str(exc))
    for target in sorted(targets & set(DEPLOYS)):
        errors += deployment_contract(root, target)
    if "wdpa" in targets:
        errors += retained_evidence(root)
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append", choices=["all", "mpatlas", "wdpa", "eamlis", "sea-ice", "iam", "dataset-usage"], required=True)
    args = parser.parse_args()
    targets = set(args.target)
    if "all" in targets:
        targets = {"mpatlas", "wdpa", "eamlis", "sea-ice", "iam", "dataset-usage"}
    errors = check(ROOT, targets)
    if errors:
        parser.exit(1, "RELEASE_CONTRACT_NOT_READY:\n" + "\n".join(errors) + "\n")
    print("Offline release contracts passed. Live IAM and installed publication state still require protected verification.")


if __name__ == "__main__":
    main()
