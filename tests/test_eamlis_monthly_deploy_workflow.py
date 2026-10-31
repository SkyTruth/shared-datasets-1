from __future__ import annotations

import unittest
import os
import subprocess
from pathlib import Path

from workflow_helpers import (
    load_workflow,
    terraform_targets,
    workflow_steps_by_name,
    workflow_triggers,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/eamlis-monthly-deploy.yml"


def test_paused_scheduler_defers_automatic_deploy_canary(tmp_path):
    step = workflow_steps_by_name(load_workflow(DEPLOY_WORKFLOW), "deploy")["Resolve in-flight canary"]
    fake = tmp_path / "gcloud"
    fake.write_text("#!/bin/sh\ncase \"$1 $2 $3\" in\n'scheduler jobs describe') echo \"$TEST_SCHEDULER_STATE\";;\n'run jobs executions') exit 0;;\n*) exit 91;;\nesac\n")
    fake.chmod(0o755)
    for state, date, expected, successful in [
        ("PAUSED", "", "false", True),
        ("PAUSED", "2026-11-01", "true", True),
        ("ENABLED", "", "true", True),
        ("", "", None, False),
        ("UPDATE_FAILED", "", None, False),
    ]:
        output = tmp_path / "outputs"
        output.write_text("")
        result = subprocess.run(["bash", "-c", step["run"]], env={
            **os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "JOB_NAME": "eamlis-monthly", "REGION": "us-central1", "GOOGLE_CLOUD_PROJECT": "shared-datasets-1",
            "CANCEL_RUNNING_CANARY": "false", "CANARY_RUN_DATE": date, "GITHUB_OUTPUT": str(output),
            "TEST_SCHEDULER_STATE": state,
        }, capture_output=True, text=True)
        assert (result.returncode == 0) == successful, result.stderr
        assert output.read_text() == (f"run_canary={expected}\n" if expected is not None else "")


def test_apply_records_configuration_phase_only_after_success(tmp_path):
    step = workflow_steps_by_name(load_workflow(DEPLOY_WORKFLOW), "deploy")["Terraform apply"]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "terraform_retry.sh").write_text('exit "$TEST_APPLY_EXIT"\n')
    for exit_code in (0, 1):
        environment = tmp_path / "environment"
        environment.write_text("")
        result = subprocess.run(["bash", "-c", step["run"]], env={
            **os.environ, "GITHUB_WORKSPACE": str(tmp_path), "RUNNER_TEMP": str(tmp_path),
            "GITHUB_ENV": str(environment), "TEST_APPLY_EXIT": str(exit_code),
        }, capture_output=True, text=True)
        assert result.returncode == exit_code
        assert environment.read_text() == ("DEPLOYMENT_PHASE=applied\n" if exit_code == 0 else "")
    canary = workflow_steps_by_name(load_workflow(DEPLOY_WORKFLOW), "deploy")["Execute eamlis-monthly canary"]["run"]
    assert 'echo "DEPLOYMENT_PHASE=verified" >> "$GITHUB_ENV"' in canary


class EamlisMonthlyDeployWorkflowTests(unittest.TestCase):
    def test_eamlis_monthly_deploy_workflow_is_protected_and_digest_pinned(self):
        workflow = load_workflow(DEPLOY_WORKFLOW)
        trigger = workflow_triggers(workflow)
        deploy = workflow["jobs"]["deploy"]
        env = workflow["env"]
        steps = workflow_steps_by_name(workflow, "deploy")
        step_names = list(steps)

        self.assertEqual(workflow["name"], "Ingestion / EAMLIS monthly deploy")
        self.assertNotIn("push", trigger)
        self.assertIn("workflow_call", trigger)
        for name in ("executor_sha", "source_run_id", "source_run_attempt"):
            self.assertTrue(trigger["workflow_call"]["inputs"][name]["required"])
        self.assertIn("workflow_dispatch", trigger)
        self.assertIn("canary_run_date", trigger["workflow_dispatch"]["inputs"])
        self.assertEqual(deploy["environment"], "shared-datasets-production")
        self.assertEqual(
            deploy["concurrency"],
            {"group": "prod-terraform-state", "queue": "max", "cancel-in-progress": False},
        )
        self.assertEqual(steps["Check out repository"]["with"]["ref"], "${{ inputs.executor_sha }}")
        self.assertEqual(env["IMAGE_NAME"], "eamlis-monthly")
        self.assertEqual(env["JOB_NAME"], "eamlis-monthly")
        self.assertEqual(env["ASSET_SLUG"], "eamlis-abandoned-mine-land-inventory")

        build_run = steps["Build eamlis-monthly image"]["run"]
        self.assertIn("tested_image_authorization.py --workflow eamlis-monthly-deploy.yml --target eamlis-monthly", build_run)
        self.assertNotIn("docker build", build_run)
        self.assertEqual(steps["Build eamlis-monthly image"]["id"], "tested-image")
        tag = steps["Tag the exact tested eamlis-monthly image"]
        self.assertEqual(tag["env"]["TESTED_IMAGE_ID"], "${{ steps.tested-image.outputs.image_id }}")
        self.assertIn('docker tag "$TESTED_IMAGE_ID"', tag["run"])
        self.assertIn("EAMLIS_MONTHLY_IMAGE_TAG=${image_tag}", tag["run"])

        tools_run = steps["Smoke-test native tools in image"]["run"]
        self.assertIn("ogr2ogr --version", tools_run)
        self.assertIn("pmtiles version", tools_run)
        self.assertIn("tippecanoe-decode", tools_run)
        import_run = steps["Smoke-test job import closure in image"]["run"]
        self.assertIn("import ingestion.eamlis_monthly.run", import_run)
        self.assertIn("scripts.vector_asset", import_run)

        push_run = steps["Push eamlis-monthly image"]["run"]
        self.assertIn("docker push", push_run)
        self.assertIn("docker buildx imagetools inspect", push_run)
        self.assertIn("EAMLIS_MONTHLY_IMAGE=${image_ref}", push_run)
        self.assertIn("assert actual == expected", push_run)

        plan_run = steps["Terraform plan"]["run"]
        self.assertEqual(
            terraform_targets(plan_run),
            {"module.eamlis_monthly_job.google_cloud_run_v2_job.this"},
        )
        self.assertIn("eamlis_monthly_image=${EAMLIS_MONTHLY_IMAGE}", plan_run)
        self.assertIn("wdpa_monthly_image=unused-by-eamlis-monthly-deploy", plan_run)
        self.assertIn("sea_ice_daily_image=unused-by-eamlis-monthly-deploy", plan_run)
        all_step_runs = "\n".join(str(step.get("run", "")) for step in steps.values())
        self.assertNotIn("gcloud run jobs describe wdpa-monthly", all_step_runs)
        self.assertNotIn("gcloud run jobs describe sea-ice-daily", all_step_runs)

        enforce_run = steps["Enforce eamlis-monthly resource-change allowlist"]["run"]
        self.assertIn('python scripts/terraform_plan_allowlist.py "${RUNNER_TEMP}/eamlis-monthly.tfplan.json"', enforce_run)
        self.assertIn('--allowed-exact="module.eamlis_monthly_job.google_cloud_run_v2_job.this"', enforce_run)
        self.assertIn('--job-image-target=eamlis-monthly --expected-image="${EAMLIS_MONTHLY_IMAGE}"', enforce_run)
        self.assertNotIn("python - ", enforce_run)
        self.assertNotIn("uv ", enforce_run)
        self.assertEqual(steps["Enforce eamlis-monthly resource-change allowlist"]["if"], steps["Terraform plan"]["if"])
        self.assertIn("terraform -chdir=terraform/envs/prod show -json", steps["Export Terraform plan JSON"]["run"])
        self.assertIn("terraform_retry.sh\" -chdir=terraform/envs/prod apply", steps["Terraform apply"]["run"])
        self.assertLess(
            step_names.index("Export Terraform plan JSON"),
            step_names.index("Enforce eamlis-monthly resource-change allowlist"),
        )
        self.assertLess(
            step_names.index("Enforce eamlis-monthly resource-change allowlist"),
            step_names.index("Claim tested deployment revision"),
        )
        self.assertLess(
            step_names.index("Enforce eamlis-monthly resource-change allowlist"),
            step_names.index("Terraform apply"),
        )
        self.assertLess(
            step_names.index("Enforce eamlis-monthly resource-change allowlist"),
            step_names.index("Sync Python dependencies"),
        )

        self.assertLess(step_names.index("Terraform apply"), step_names.index("Confirm deployed digest"))
        self.assertLess(step_names.index("Confirm deployed digest"), step_names.index("Execute eamlis-monthly canary"))
        self.assertLess(
            step_names.index("Execute eamlis-monthly canary"),
            step_names.index("Validate latest EAMLIS release contract"),
        )
        self.assertLess(
            step_names.index("Validate latest EAMLIS release contract"),
            step_names.index("Run EAMLIS bucket hygiene audit"),
        )

        canary_run = steps["Execute eamlis-monthly canary"]["run"]
        self.assertIn("--wait", canary_run)
        self.assertIn("RUN_DATE=${CANARY_RUN_DATE}", canary_run)
        validate_run = steps["Validate latest EAMLIS release contract"]["run"]
        self.assertIn("validate_release_manifest", validate_run)
        self.assertIn("eamlis-abandoned-mine-land-inventory", validate_run)
        audit_run = steps["Run EAMLIS bucket hygiene audit"]["run"]
        self.assertIn("audit_shared_datasets.py", audit_run)
        self.assertIn("eamlis-abandoned-mine-land-inventory", audit_run)


if __name__ == "__main__":
    unittest.main()
