from __future__ import annotations

import json
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

    def test_missing_generic_proof_and_direct_policy_push_cannot_pass(self):
        path = self.root / ".github/workflows/prod-terraform-target-apply.yml"
        text = path.read_text().replace("      source_run_id:\n        description: Main-push CI run that tested executor_sha\n        required: true", "      source_run_id:\n        description: Main-push CI run that tested executor_sha\n        required: false")
        path.write_text(text)
        caller = self.root / ".github/workflows/cron-alert-policy-sync.yml"
        caller.write_text(caller.read_text().replace("  workflow_call:\n", "  push:\n    branches: [main]\n  workflow_call:\n", 1))
        errors = contracts.iam_contract(self.root)
        self.assertTrue(any("must require source_run_id" in item for item in errors))
        self.assertTrue(any("ci-ready" in item for item in errors))

    def test_owned_dependent_roles_cannot_drift_from_permission_contracts(self):
        for filename, permission in (("artifact_registry_iam.tf", "artifactregistry.repositories.setIamPolicy"), ("preview_terraform_iam.tf", "iam.serviceAccounts.actAs"), ("shared_bucket_public.tf", "storage.managedFolders.setIamPolicy")):
            with self.subTest(filename=filename):
                path = self.root / "terraform/envs/prod" / filename
                original = path.read_text()
                path.write_text(original.replace('"' + permission + '",', ""))
                self.assertTrue(any(permission in error for error in contracts.iam_contract(self.root)))
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

    def test_probe_of_another_plan_cannot_satisfy_restore_apply(self):
        path = self.root / ".github/workflows/wdpa-processing-validation-deploy.yml"
        original = path.read_text()
        path.write_text(original.replace('--plan-json "$RUNNER_TEMP/restore-processing-plan.json"', '--plan-json "$RUNNER_TEMP/staging-probe-plan.json"'))
        self.assertTrue(any("Restore the processing command" in error for error in contracts.saved_plan_permission_contract(self.root)))

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
