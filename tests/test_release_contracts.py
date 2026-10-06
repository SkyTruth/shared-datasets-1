from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

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
