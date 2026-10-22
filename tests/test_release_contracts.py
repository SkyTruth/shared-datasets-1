from __future__ import annotations

from copy import deepcopy
import json
import csv
from pathlib import Path
import shutil
import tempfile
import unittest

import yaml

from scripts import release_contracts as contracts
from scripts.production_image_contracts import commands, INTERPRETER_PROBE

ROOT = Path(__file__).resolve().parents[1]


class ReleaseContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in (".github", "catalog", "terraform", "docs/wdpa-processing-evidence"):
            shutil.copytree(ROOT / directory, self.root / directory)

    def test_current_reviewed_contracts_pass_without_live_credentials(self):
        self.assertEqual(contracts.check(self.root, {"wdpa", "eamlis", "sea-ice", "wdpa-processing"}), [])

    def test_runtime_ci_callers_cannot_inherit_optional_skips_or_bypass_prerequisites(self):
        path = self.root / ".github/workflows/ci.yml"
        original = yaml.safe_load(path.read_text())
        for target in contracts.DEPLOYS:
            condition = original["jobs"][target]["if"]
            for unsafe in (
                condition.replace("!cancelled() && ", ""),
                condition.replace("!cancelled()", "always()"),
                condition.replace("needs.ingestion-iam.result == 'success' && ", ""),
                condition.replace("needs.artifact-registry-iam.result == 'success' && ", ""),
                condition.replace("needs.ci-ready.result == 'success'", "needs.ci-ready.result != 'failure'"),
            ):
                with self.subTest(target=target, condition=unsafe):
                    current = deepcopy(original)
                    current["jobs"][target]["if"] = unsafe
                    path.write_text(yaml.safe_dump(current))
                    errors = contracts.deployment_contract(self.root, target)
                    self.assertTrue(any("CI caller must bypass skipped ancestors" in item for item in errors))
            for needs in (
                [name for name in original["jobs"][target]["needs"] if name != "ingestion-iam"],
                original["jobs"][target]["needs"] + ["preview-terraform-iam"],
            ):
                current = deepcopy(original)
                current["jobs"][target]["needs"] = needs
                path.write_text(yaml.safe_dump(current))
                self.assertTrue(any("CI caller must bypass skipped ancestors" in item
                                    for item in contracts.deployment_contract(self.root, target)))
        path.write_text(yaml.safe_dump(original))

    def test_logging_identity_proof_wiring_cannot_change_principal_scope_or_delegate(self):
        path = self.root / ".github/workflows/prod-terraform-target-apply.yml"
        original = yaml.safe_load(path.read_text())
        for field, changed in (
            ("service_account", "other@shared-datasets-1.iam.gserviceaccount.com"),
            ("token_format", "access_token"),
            ("access_token_scopes", "https://www.googleapis.com/auth/cloud-platform"),
            ("access_token_subject", "delegate@example.com"),
            ("delegates", "other@shared-datasets-1.iam.gserviceaccount.com"),
            ("create_credentials_file", False),
        ):
            current = deepcopy(original)
            auth = next(step for step in current["jobs"]["sync"]["steps"] if step.get("id") == "auth")
            auth["with"][field] = changed
            path.write_text(yaml.safe_dump(current))
            self.assertTrue(any("explicit scoped identity token" in item for item in contracts.iam_contract(self.root)))
        for name in ("Verify prerequisite deployment permissions", "Verify complete saved-plan mutation permissions"):
            current = deepcopy(original)
            step = next(step for step in current["jobs"]["sync"]["steps"] if step.get("name") == name)
            step["env"].pop("LOGGING_SETUP_ACCESS_TOKEN")
            path.write_text(yaml.safe_dump(current))
            self.assertTrue(any("masked scoped auth token" in item for item in contracts.iam_contract(self.root)))
        current = deepcopy(original)
        current["env"]["TERRAFORM_SERVICE_ACCOUNT"] = "different-principal"
        path.write_text(yaml.safe_dump(current))
        self.assertTrue(any("explicit scoped identity token" in item for item in contracts.iam_contract(self.root)))

    def test_logging_installed_grant_proof_tracks_exact_terraform_scope(self):
        path = self.root / "terraform/envs/prod/operational_observer.tf"
        original = path.read_text()
        for before, after in (
            ('toset(["_Default", "_Required"])', 'toset(["_Default", "_Required", "other"])'),
            ('role_id     = "sharedDatasetsOperationalLogViewManager"', 'role_id     = "otherLogRole"'),
            ('title       = "operational-view-buckets-only"', 'title       = "different-scope"'),
            ("resource.name.startsWith('projects/${var.project_id}/locations/global/buckets/${bucket}/views/')", "resource.name.startsWith('projects/${var.project_id}/')"),
        ):
            self.assertIn(before, original)
            path.write_text(original.replace(before, after, 1))
            self.assertTrue(any("Logging installed-grant proof" in item for item in contracts.iam_contract(self.root)))
        path.write_text(original)

    def test_provider_setup_proposal_has_fixed_expiry_exact_roles_and_bootstrap(self):
        path = self.root / "terraform/envs/prod/operational_observer.tf"
        original = path.read_text()
        for before, after in (
            ("request.time < timestamp('2026-10-22T02:00:00Z')", "request.time < timestamp('2099-01-01T00:00:00Z')"),
            ('"sharedDatasetsOperationalProviderCreator"', '"otherCreator"'),
            ('permissions = ["iam.workloadIdentityPoolProviders.create"]', 'permissions = ["iam.workloadIdentityPoolProviders.create", "iam.workloadIdentityPoolProviders.update"]'),
            ('title       = "operational-provider-create-expires"', 'title       = "changed"'),
        ):
            self.assertIn(before, original)
            path.write_text(original.replace(before, after, 1))
            self.assertTrue(any("operational provider" in item or "operational_provider_creator" in item for item in contracts.iam_contract(self.root)))
        path.write_text(original)
        workflow_path = self.root / ".github/workflows/cron-alert-policy-sync.yml"
        workflow = yaml.safe_load(workflow_path.read_text())
        workflow["jobs"]["bootstrap"]["with"]["allowed_exact"] = workflow["jobs"]["bootstrap"]["with"]["allowed_exact"].replace("google_project_iam_member.github_actions_operational_provider_creator", "")
        workflow_path.write_text(yaml.safe_dump(workflow))
        self.assertTrue(any("exact protected bootstrap allowed_exact" in item for item in contracts.iam_contract(self.root)))

    def test_readonly_trust_repair_preserves_exact_provider_and_separate_update_scope(self):
        path = self.root / "terraform/envs/prod/github_readonly_iam.tf"
        original = path.read_text()
        for before, after in (
            ('permissions = ["iam.workloadIdentityPoolProviders.update"]',
             'permissions = ["iam.workloadIdentityPoolProviders.update", "iam.workloadIdentityPoolProviders.delete"]'),
            ("2026-10-22T06:00:00Z", "2099-01-01T00:00:00Z"),
            ('"sharedDatasetsReadonlyTrustUpdater"', '"otherRole"'),
            ("assertion.workflow == 'Dataset breaking change alert'", "assertion.workflow == 'Other workflow'"),
            ('issuer_uri = "https://token.actions.githubusercontent.com"', 'issuer_uri = "https://other.invalid"'),
            ('"google.subject"             = "assertion.sub"', '"google.subject"             = "assertion.actor"'),
        ):
            with self.subTest(before=before):
                self.assertIn(before, original)
                path.write_text(original.replace(before, after, 1))
                self.assertTrue(contracts.readonly_trust_contract(self.root))
        path.write_text(original)
        workflow_path = self.root / ".github/workflows/cron-alert-policy-sync.yml"
        original_workflow = yaml.safe_load(workflow_path.read_text())
        for job, field, value in (
            ("readonly-bootstrap", "needs", "readonly-trust"),
            ("readonly-trust", "needs", "bootstrap"),
            ("readonly-trust", "readiness_target", "operational-observer-setup"),
            ("readonly-trust", "allowed_exact", "google_iam_workload_identity_pool.github"),
            ("readonly-bootstrap", "targets", "google_iam_workload_identity_pool_provider.github_readonly"),
        ):
            with self.subTest(job=job, field=field):
                value_workflow = deepcopy(original_workflow)
                current = value_workflow["jobs"][job]
                (current if field == "needs" else current["with"])[field] = value
                workflow_path.write_text(yaml.safe_dump(value_workflow))
                self.assertTrue(contracts.readonly_trust_contract(self.root))
        workflow_path.write_text(yaml.safe_dump(original_workflow))
        changed = deepcopy(original_workflow)
        changed["jobs"]["observations"]["needs"] = "bootstrap"
        workflow_path.write_text(yaml.safe_dump(changed))
        self.assertTrue(any("operational setup requires ordered bootstrap" in item
                            for item in contracts.iam_contract(self.root)))
        workflow_path.write_text(yaml.safe_dump(original_workflow))

    def test_missing_retained_evidence_or_resource_approval_blocks_merge(self):
        path = self.root / "catalog/wdpa-processing-acceptance.json"
        evidence = json.loads(path.read_text())
        evidence["disk_quota_approved"] = False
        evidence["build"].pop("artifact_bundle")
        path.write_text(json.dumps(evidence))
        errors = contracts.retained_evidence(self.root)
        self.assertTrue(any("quota" in item for item in errors))
        self.assertTrue(any("bundle" in item for item in errors))

    def test_changed_retained_bytes_rejected_without_comparing_current_consumer_to_producer(self):
        path = self.root / "catalog/wdpa-processing-acceptance.json"
        evidence = json.loads(path.read_text())
        report = self.root / evidence["evidence_files"]["report.json"]["path"]
        report.write_bytes(report.read_bytes() + b" ")
        self.assertTrue(any("bytes changed" in item for item in contracts.retained_evidence(self.root)))

    def test_missing_reset_plan_rejected_before_live_deployment(self):
        shutil.rmtree(self.root / ".github/dataset-plans/ims-sea-ice-extent")
        self.assertTrue(any("immutable reset" in item for item in contracts.deployment_contract(self.root, "sea-ice")))

    def test_removed_permission_or_bootstrap_edge_fails(self):
        path = self.root / "terraform/envs/prod/scheduled_ingestion_deploy_iam.tf"
        path.write_text(path.read_text().replace('"run.jobs.runWithOverrides",', ""))
        workflow = self.root / ".github/workflows/scheduled-ingestion-deploy-iam-sync.yml"
        workflow.write_text(workflow.read_text().replace("needs: bootstrap", "needs: wrong"))
        errors = contracts.iam_contract(self.root)
        self.assertTrue(any("runWithOverrides" in item for item in errors))
        self.assertTrue(any("bootstrap" in item for item in errors))

    def test_observer_catalog_growth_cannot_exceed_reviewed_iam_chunk_scope(self):
        path = self.root / "catalog/shared-datasets-catalog.csv"
        with path.open() as stream:
            reader = csv.DictReader(stream)
            fields, rows = reader.fieldnames, list(reader)
        while len(rows) <= 30:
            additional = {**rows[0], "asset_slug": "additional-asset-" + str(len(rows))}
            rows.append(additional)
        with path.open("w") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        caller = contracts.workflow(self.root, "cron-alert-policy-sync.yml")["jobs"]["observations"]["with"]
        text = (self.root / "terraform/envs/prod/operational_observer.tf").read_text()
        errors = contracts.operational_storage_contract(self.root, text, caller)
        self.assertTrue(any("exceeds the reviewed five IAM chunks" in item for item in errors))

    def test_missing_generic_proof_and_direct_policy_push_cannot_pass(self):
        path = self.root / ".github/workflows/prod-terraform-target-apply.yml"
        text = path.read_text().replace("      source_run_id:\n        description: Main-push CI run that tested executor_sha\n        required: true", "      source_run_id:\n        description: Main-push CI run that tested executor_sha\n        required: false")
        path.write_text(text)
        caller = self.root / ".github/workflows/cron-alert-policy-sync.yml"
        caller.write_text(caller.read_text().replace("  workflow_call:\n", "  push:\n    branches: [main]\n  workflow_call:\n", 1))
        errors = contracts.iam_contract(self.root)
        self.assertTrue(any("must require source_run_id" in item for item in errors))
        self.assertTrue(any("ci-ready" in item for item in errors))

    def test_every_policy_including_deletion_and_retired_schema_has_protected_coverage(self):
        path = self.root / ".github/workflows/cron-alert-policy-sync.yml"
        original = path.read_text()
        for name in ("dataset_object_deleted", "dataset_schema_changed"):
            with self.subTest(name=name):
                path.write_text(original.replace("        google_monitoring_alert_policy." + name + "\n", "", 1))
                self.assertTrue(any("every declared alert policy" in error for error in contracts.iam_contract(self.root)))
        path.write_text(original)
        (self.root / "terraform/envs/prod/new_alert.tf").write_text('resource "google_monitoring_alert_policy" "future_job" {}\n')
        self.assertTrue(any("every declared alert policy" in error for error in contracts.iam_contract(self.root)))

    def test_protected_setup_retains_legacy_and_fenced_heartbeat_descriptors(self):
        path = self.root / ".github/workflows/cron-alert-policy-sync.yml"
        original = path.read_text()
        for suffix in ("", "_v2"):
            with self.subTest(suffix=suffix):
                path.write_text(original.replace("        google_monitoring_metric_descriptor.operational_reconciliation_heartbeat" + suffix + "\n", "", 1))
                self.assertTrue(any("retain the legacy heartbeat descriptor" in error for error in contracts.iam_contract(self.root)))

    def test_owned_dependent_roles_cannot_drift_from_permission_contracts(self):
        for filename, permission in (("artifact_registry_iam.tf", "artifactregistry.repositories.setIamPolicy"), ("preview_terraform_iam.tf", "iam.serviceAccounts.actAs"), ("shared_bucket_public.tf", "storage.managedFolders.setIamPolicy")):
            with self.subTest(filename=filename):
                path = self.root / "terraform/envs/prod" / filename
                original = path.read_text()
                path.write_text(original.replace('"' + permission + '",', ""))
                self.assertTrue(any(permission in error for error in contracts.iam_contract(self.root)))
                path.write_text(original)

    def test_exact_existing_cdn_role_adoption_cannot_broaden_or_drop_permissions(self):
        path = self.root / 'terraform/envs/prod/shared_bucket_public.tf'
        original = path.read_text()
        for before, after in (
            ('"compute.urlMaps.list",', ''),
            ('"compute.backendBuckets.list",', ''),
            ('"compute.urlMaps.list",', '"compute.urlMaps.list", "compute.urlMaps.delete",'),
            ('"compute.urlMaps.invalidateCache",', ''),
            ('stage       = "GA"', 'stage       = "BETA"'),
            ('Shared Datasets PMTiles URL Map Sync', 'Different title'),
            ('roles/sharedDatasetsPmtilesUrlMapSync"', 'roles/OtherRole"'),
        ):
            with self.subTest(before=before, after=after):
                path.write_text(original.replace(before, after))
                self.assertTrue(any('URL-map role' in error for error in contracts.iam_contract(self.root)))
        path.write_text(original)

    def test_removed_disabled_late_or_foreign_plan_role_guard_is_not_ready(self):
        path = self.root / '.github/workflows/pmtiles-cdn-sync.yml'
        original = path.read_text()
        for mutation in ('removed', 'disabled', 'late', 'foreign-plan', 'missing-target', 'foreign-export'):
            with self.subTest(mutation=mutation):
                value = yaml.safe_load(original)
                steps = value['jobs']['sync']['steps']
                guard = next(step for step in steps if step.get('name') == 'Enforce PMTiles managed-folder IAM bootstrap allowlist')
                if mutation == 'removed':
                    steps.remove(guard)
                elif mutation == 'disabled':
                    guard['if'] = False
                elif mutation == 'late':
                    steps.remove(guard)
                    steps.append(guard)
                elif mutation == 'foreign-plan':
                    guard['run'] = guard['run'].replace('pmtiles-managed-folder-bootstrap', 'unrelated')
                elif mutation == 'foreign-export':
                    export = next(step for step in steps if step.get('name') == 'Export PMTiles managed-folder IAM bootstrap plan JSON')
                    export['run'] = export['run'].replace('show -json "${RUNNER_TEMP}/pmtiles-managed-folder-bootstrap.tfplan"', 'show -json "${RUNNER_TEMP}/unrelated.tfplan"')
                else:
                    plan = next(step for step in steps if step.get('name') == 'Terraform plan PMTiles managed-folder IAM bootstrap')
                    plan['run'] = plan['run'].replace('-target=google_project_iam_custom_role.pmtiles_url_map_sync', '')
                path.write_text(yaml.safe_dump(value))
                self.assertTrue(any('CDN' in error for error in contracts.iam_contract(self.root)))
        path.write_text(original)

    def test_redirect_mode_or_new_backend_service_cannot_inherit_bucket_only_readiness(self):
        for filename, before, after in (
            ('terraform/envs/prod/production.auto.tfvars', 'pmtiles_serving_mode                   = "cdn"', 'pmtiles_serving_mode                   = "redirect"'),
            ('.github/workflows/pmtiles-cdn-sync.yml', '-refresh=false', '-refresh=false -var="pmtiles_serving_mode=redirect"'),
            ('terraform/envs/prod/pmtiles_cdn.tf', 'pmtiles_redirector_enabled = var.pmtiles_serving_mode == "redirect"', 'pmtiles_redirector_enabled = true'),
            ('terraform/envs/prod/pmtiles_cdn.tf', 'service = google_compute_backend_bucket.pmtiles_cdn.self_link', 'service = google_compute_backend_service.pmtiles_redirector[0].self_link'),
            ('terraform/envs/prod/pmtiles_cdn.tf', 'name        = "shared-datasets-pmtiles-cdn"', 'name        = "other-backend"'),
        ):
            with self.subTest(filename=filename, before=before):
                path = self.root / filename
                original = path.read_text()
                self.assertIn(before, original)
                path.write_text(original.replace(before, after))
                self.assertTrue(any('CDN' in error for error in contracts.iam_contract(self.root)))
                path.write_text(original)

    def test_alternate_auto_variables_and_unmodeled_cli_overrides_are_not_ready(self):
        prod = self.root / 'terraform/envs/prod'
        for filename in ('z-override.auto.tfvars', 'z-override.auto.tfvars.json', 'terraform.tfvars', 'terraform.tfvars.json'):
            with self.subTest(filename=filename):
                path = prod / filename
                path.write_text('{"pmtiles_serving_mode":"redirect"}' if filename.endswith('.json') else 'pmtiles_serving_mode = "redirect"')
                self.assertTrue(any('automatic variable files' in error for error in contracts.iam_contract(self.root)))
                path.unlink()
        path = self.root / '.github/workflows/pmtiles-cdn-sync.yml'
        original = path.read_text()
        for override in ('-var-file="other.tfvars"', '-var="unknown_mode_var=redirect"', 'TF_CLI_ARGS_plan=-var-file=other.tfvars', 'TF_VAR_dynamic_mode=redirect'):
            with self.subTest(override=override):
                path.write_text(original.replace('-refresh=false', '-refresh=false ' + override))
                self.assertTrue(any('unmodeled CLI or environment' in error for error in contracts.iam_contract(self.root)))
        path.write_text(original)

    def test_every_automatic_saved_plan_apply_requires_its_exact_permission_probe(self):
        self.assertEqual(contracts.saved_plan_permission_contract(self.root), [])
        for filename in contracts.PLAN_PROBE_WORKFLOWS:
            with self.subTest(filename=filename):
                path = self.root / ".github/workflows" / filename
                original = path.read_text()
                path.write_text(original.replace("deployment_permissions.py", "other_check.py"))
                self.assertTrue(any("saved-plan permission probe" in error for error in contracts.check(self.root, {"iam"})))
                path.write_text(original)

    def test_probe_of_another_plan_cannot_satisfy_monthly_apply(self):
        path = self.root / ".github/workflows/wdpa-monthly-deploy.yml"
        original = path.read_text()
        path.write_text(original.replace('--plan-json "$RUNNER_TEMP/wdpa-monthly.tfplan.json"', '--plan-json "$RUNNER_TEMP/other-plan.json"'))
        self.assertTrue(any("Terraform apply" in error for error in contracts.saved_plan_permission_contract(self.root)))

    def test_disabled_probe_and_removed_cdn_post_apply_authority_fail(self):
        path = self.root / ".github/workflows/pmtiles-cdn-sync.yml"
        original = path.read_text()
        value = contracts.workflow(self.root, path.name)
        for job in value["jobs"].values():
            for step in job.get("steps", []):
                if "deployment_permissions.py" in step.get("run", "") and "--plan-json" in step["run"]:
                    step["if"] = False
        path.write_text(yaml.safe_dump(value))
        self.assertTrue(contracts.saved_plan_permission_contract(self.root))
        path.write_text(original.replace("--target pmtiles-cdn --plan-json", "--target iam-bootstrap --plan-json"))
        self.assertTrue(any("saved-plan permission probe" in error for error in contracts.saved_plan_permission_contract(self.root)))

    def test_every_image_push_requires_live_repository_operation_permissions(self):
        self.assertEqual(contracts.image_permission_contract(self.root), [])
        for filename in contracts.IMAGE_PROBE_WORKFLOWS:
            path = self.root / ".github/workflows" / filename
            original = path.read_text()
            for replacement in ("artifact-registry", "iam-bootstrap", "artifact-registry-images || true", "artifact-registry-images --plan-json other.tfplan.json"):
                with self.subTest(filename=filename, replacement=replacement):
                    path.write_text(original.replace("--target artifact-registry-images", "--target " + replacement))
                    self.assertTrue(any("operational image permission probe" in error for error in contracts.image_permission_contract(self.root)))
            path.write_text(original)

    def test_removed_disabled_and_late_image_probe_fail_before_publication(self):
        for filename in contracts.IMAGE_PROBE_WORKFLOWS:
            path = self.root / ".github/workflows" / filename
            original = path.read_text()
            for mutation in ("removed", "disabled", "late"):
                with self.subTest(filename=filename, mutation=mutation):
                    value = yaml.safe_load(original)
                    for job in value["jobs"].values():
                        if "uses" in job:
                            continue  # Reusable IAM bootstrap has no image publication.
                        steps = job.get("steps", [])
                        selected = next(step for step in steps if "--target artifact-registry-images" in step.get("run", ""))
                        if mutation == "disabled":
                            selected["if"] = False
                        else:
                            steps.remove(selected)
                            if mutation == "late":
                                steps.append(selected)
                    path.write_text(yaml.safe_dump(value))
                    self.assertTrue(any("operational image permission probe" in error for error in contracts.check(self.root, {"iam"})))
            path.write_text(original)

    def test_shallow_mutable_checkout_rejected(self):
        path = self.root / ".github/workflows/wdpa-monthly-deploy.yml"
        path.write_text(path.read_text().replace("fetch-depth: 0", "fetch-depth: 1").replace("ref: ${{ inputs.executor_sha }}", "ref: main"))
        self.assertTrue(any("complete history" in item for item in contracts.deployment_contract(self.root, "wdpa")))

    def test_candidate_cannot_replace_the_trusted_bootstrap(self):
        path = self.root / ".github/workflows/wdpa-monthly-deploy.yml"
        path.write_text(path.read_text().replace("ref: ${{ github.workflow_sha }}", "ref: ${{ inputs.executor_sha }}"))
        self.assertTrue(any("trusted main workflow" in item for item in contracts.deployment_contract(self.root, "wdpa")))

    def test_scheduler_retains_bundle_without_manual_overrides(self):
        path = self.root / "terraform/envs/prod/wdpa_monthly.tf"
        path.write_text(path.read_text().replace("WDPA_PROMOTION_BUNDLE", "OLD_BUNDLE") + '\n  RUN_DATE = "2026-10-01"\n')
        errors = contracts.retained_evidence(self.root)
        self.assertTrue(any("retained bundle" in item for item in errors))
        self.assertTrue(any("calendar date" in item for item in errors))


class ActualImageContractTests(unittest.TestCase):
    def test_real_recipe_and_actual_gdal_interpreter_are_checked(self):
        checks = commands("wdpa-monthly", "a" * 40)
        self.assertIn("ingestion/wdpa_monthly/Dockerfile", checks[0])
        self.assertTrue(any("scripts/wdpa_input_memory_probe.py" in command for command in checks))
        self.assertTrue(any("scripts/local_ingestion_smoke.py" in command for command in checks))
        self.assertIn('script.open().readline()', INTERPRETER_PROBE)
        self.assertIn('import numpy; from osgeo import gdal_array', INTERPRETER_PROBE)
        self.assertTrue(all("--platform" in command and "linux/amd64" in command for command in checks))
