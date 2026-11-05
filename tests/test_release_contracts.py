from __future__ import annotations

import json
from pathlib import Path
import re
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

    def test_release_barriers_do_not_depend_on_step_display_names(self):
        for filename in ("ingestion-deploy.yml", "pmtiles-cdn-sync.yml"):
            path = self.root / ".github/workflows" / filename
            path.write_text(re.sub(r"(?m)^(\s*)- name: .*", r"\1- name: Renamed step", path.read_text()))
        self.assertEqual(contracts.check(self.root, set(contracts.DEPLOYS)), [])

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

    def test_monitoring_private_resources_and_authority_must_precede_cutover(self):
        path = self.root / ".github/workflows/cron-alert-policy-sync.yml"
        original = path.read_text()
        self.assertEqual(contracts.iam_contract(self.root), [])
        for child, needs in (("resources", []), ("sync", "bootstrap"), ("sync", "resources")):
            with self.subTest(child=child, needs=needs):
                value = yaml.safe_load(original)
                value["jobs"][child]["needs"] = needs
                path.write_text(yaml.safe_dump(value))
                self.assertTrue(any("bootstrap authority" in item for item in contracts.iam_contract(self.root)))
        path.write_text(original)

    def test_missing_generic_proof_and_direct_policy_push_cannot_pass(self):
        path = self.root / ".github/workflows/prod-terraform-target-apply.yml"
        value = yaml.safe_load(path.read_text())
        value[True]['workflow_call']['inputs']['source_run_id']['required'] = False
        path.write_text(yaml.safe_dump(value))
        caller = self.root / ".github/workflows/cron-alert-policy-sync.yml"
        caller.write_text(caller.read_text().replace("  workflow_call:\n", "  push:\n    branches: [main]\n  workflow_call:\n", 1))
        errors = contracts.iam_contract(self.root)
        self.assertTrue(any("must require source_run_id" in item for item in errors))
        self.assertTrue(any("ci-ready" in item for item in errors))

    def test_terraform_queue_and_propagation_cannot_bypass_signed_admission(self):
        path = self.root / '.github/workflows/prod-terraform-target-apply.yml'
        original = path.read_text()
        mutations = {
            'preparation under queue': lambda jobs: jobs['prepare'].update(concurrency=jobs['sync']['concurrency']),
            'disconnected preparation': lambda jobs: jobs['sync'].update(needs=[]),
            'bypass preparation': lambda jobs: jobs['sync'].update({'if': 'always()'}),
            'unprotected apply': lambda jobs: jobs['sync'].pop('environment'),
            'cancelling state queue': lambda jobs: jobs['sync']['concurrency'].update({'cancel-in-progress': True}),
            'wait under queue': lambda jobs: jobs['propagation'].update(concurrency=jobs['sync']['concurrency']),
            'privileged wait': lambda jobs: jobs['propagation'].update(environment='shared-datasets-production'),
            'wait writer': lambda jobs: jobs['propagation'].update(permissions={'deployments': 'write'}),
            'unsigned wait': lambda jobs: jobs['propagation'].update({'if': 'always()'}),
            'unbound apply output': lambda jobs: jobs['sync']['outputs'].update(applied='true'),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                value = yaml.safe_load(original)
                mutate(value['jobs'])
                path.write_text(yaml.safe_dump(value))
                self.assertTrue(any('Terraform' in error for error in contracts.iam_contract(self.root)))
        path.write_text(original)

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
        for mutation in ('removed', 'disabled', 'late', 'foreign-plan', 'missing-target', 'foreign-export', 're-export'):
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
                elif mutation == 're-export':
                    exported = next(step for step in steps if 'show -json' in step.get('run', ''))
                    steps.insert(steps.index(guard) + 1, exported.copy())
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
        path = self.root / ".github/workflows/ingestion-deploy.yml"
        original = path.read_text()
        path.write_text(original.replace('--plan-json "$RUNNER_TEMP/ingestion.tfplan.json"', '--plan-json "$RUNNER_TEMP/other-plan.json"'))
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
                        if "uses" in job or not any("docker push" in step.get("run", "") for step in job.get("steps", [])):
                            continue  # Readiness and reusable IAM jobs publish no image.
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

    def test_trusted_bootstrap_bindings_and_checkout_cannot_be_disabled(self):
        path = self.root / ".github/workflows/ingestion-deploy.yml"
        original = yaml.safe_load(path.read_text())
        for mutation in ("checkout", "removed", "disabled", "candidate-sha", "late"):
            with self.subTest(mutation=mutation):
                value = json.loads(json.dumps(original))
                steps = value["jobs"]["prepare"]["steps"]
                bootstrap = next(step for step in steps if step.get("uses") == contracts.receipts.BOOTSTRAP)
                if mutation == "checkout":
                    steps[0]["with"]["ref"] = "${{ inputs.executor_sha }}"
                elif mutation == "removed":
                    steps.remove(bootstrap)
                elif mutation == "disabled":
                    bootstrap["if"] = False
                elif mutation == "late":
                    steps.remove(bootstrap)
                    steps.append(bootstrap)
                else:
                    bootstrap["with"]["executor-sha"] = "main"
                path.write_text(yaml.safe_dump(value))
                self.assertTrue(contracts.deployment_contract(self.root, "wdpa"))

    def test_shared_action_declares_unconditional_exact_checkouts_and_restore(self):
        source = ROOT / ".github/actions/deployment-bootstrap/action.yml"
        steps = yaml.safe_load(source.read_text())["runs"]["steps"]
        checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
        self.assertEqual(checkout["with"], {"ref": "${{ inputs.executor-sha }}", "fetch-depth": 0, "persist-credentials": False})
        restore = next(step for step in steps if "TRUSTED_WORKFLOW_SHA" in step.get("env", {}))
        self.assertEqual(restore["env"]["TRUSTED_WORKFLOW_SHA"], "${{ github.workflow_sha }}")
        self.assertTrue(all("if" not in step and not step.get("continue-on-error") for step in steps))

    def test_each_wrapper_must_resolve_its_registered_target_and_exact_source(self):
        for target, slug in contracts.DEPLOYS.items():
            path = self.root / ".github/workflows" / (slug + "-deploy.yml")
            original = path.read_text()
            for key, value in (("uses", "./.github/workflows/arbitrary.yml"), ("target", "other-target"), ("executor_sha", "main")):
                with self.subTest(target=target, key=key):
                    caller = yaml.safe_load(original)
                    job = caller["jobs"]["deploy"]
                    (job if key == "uses" else job["with"])[key] = value
                    path.write_text(yaml.safe_dump(caller))
                    self.assertTrue(contracts.deployment_contract(self.root, target))
            path.write_text(original)

    def test_scheduler_retains_bundle_without_manual_overrides(self):
        path = self.root / "terraform/envs/prod/wdpa_monthly.tf"
        path.write_text(path.read_text().replace("WDPA_PROMOTION_BUNDLE", "OLD_BUNDLE") + '\n  RUN_DATE = "2026-10-01"\n')
        errors = contracts.deployment_contract(self.root, "wdpa")
        self.assertTrue(any("pin a monthly bundle" in item for item in errors))
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
