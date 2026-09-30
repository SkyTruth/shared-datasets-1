from __future__ import annotations

import unittest
import os
import subprocess
from pathlib import Path

from workflow_helpers import (
    load_workflow,
    python_literal_string_set,
    terraform_targets,
    workflow_steps_by_name,
    workflow_triggers,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/sea-ice-daily-deploy.yml"


class SeaIceDailyDeployWorkflowTests(unittest.TestCase):
    def test_canary_passes_reviewed_date_as_one_argument_and_rejects_invalid_input(self):
        steps = workflow_steps_by_name(load_workflow(DEPLOY_WORKFLOW), "deploy")
        # Execute the real workflow shell with a harmless command recorder.
        script = "gcloud() { printf '%s\\n' \"$@\"; }\n" + steps["Execute sea-ice-daily canary"]["run"]
        for date in ("", "2026-10-01", "invalid; exit 0"):
            with self.subTest(date=date):
                result = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                                        env={**os.environ, "JOB_NAME": "sea-ice-daily", "REGION": "us-central1",
                                             "GOOGLE_CLOUD_PROJECT": "shared-datasets-1", "CANARY_RUN_DATE": date})
                if date.startswith("invalid"):
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual("--update-env-vars=RUN_DATE=2026-10-01" in result.stdout.splitlines(), bool(date))

    def test_sea_ice_daily_deploy_workflow_is_protected_and_digest_pinned(self):
        workflow = load_workflow(DEPLOY_WORKFLOW)
        trigger = workflow_triggers(workflow)
        deploy = workflow["jobs"]["deploy"]
        env = workflow["env"]
        steps = workflow_steps_by_name(workflow, "deploy")
        step_names = list(steps)

        self.assertEqual(workflow["name"], "Sea ice daily deploy")
        self.assertEqual(trigger["push"]["branches"], ["main"])
        self.assertIn("catalog/feature-identity-resolutions/**", set(trigger["push"]["paths"]))
        self.assertIn("workflow_dispatch", trigger)
        self.assertIn("resume_scheduler", trigger["workflow_dispatch"]["inputs"])
        self.assertEqual(deploy["environment"], "shared-datasets-production")
        self.assertEqual(
            deploy["concurrency"],
            {"group": "prod-terraform-state", "queue": "max", "cancel-in-progress": False},
        )
        self.assertEqual(steps["Check out repository"]["with"]["ref"], "main")
        self.assertEqual(env["IMAGE_NAME"], "sea-ice-daily")
        self.assertEqual(env["JOB_NAME"], "sea-ice-daily")

        build_run = steps["Build sea-ice-daily image"]["run"]
        self.assertIn("-f ingestion/sea_ice_daily/Dockerfile", build_run)
        self.assertIn("--platform linux/amd64", build_run)
        self.assertIn("SEA_ICE_DAILY_IMAGE_TAG=${image_tag}", build_run)

        self.assertIn("gdal_calc.py --help", steps["Smoke-test native tools in image"]["run"])
        synthetic_run = steps["Smoke-test synthetic sea-ice build path in image"]["run"]
        self.assertIn("docker run --platform linux/amd64 --rm -i", synthetic_run)
        self.assertIn("sea_ice.build_outputs", synthetic_run)

        push_run = steps["Push sea-ice-daily image"]["run"]
        self.assertIn("docker push", push_run)
        self.assertIn("docker buildx imagetools inspect", push_run)
        self.assertIn("SEA_ICE_DAILY_IMAGE=${image_ref}", push_run)

        plan_run = steps["Terraform plan"]["run"]
        self.assertEqual(
            terraform_targets(plan_run),
            {"module.sea_ice_daily_job.google_cloud_run_v2_job.this"},
        )
        self.assertIn("sea_ice_daily_image=${SEA_ICE_DAILY_IMAGE}", plan_run)
        self.assertIn("wdpa_monthly_image=unused-by-sea-ice-daily-deploy", plan_run)
        self.assertIn("eamlis_monthly_image=unused-by-sea-ice-daily-deploy", plan_run)
        all_step_runs = "\n".join(str(step.get("run", "")) for step in steps.values())
        self.assertNotIn("gcloud run jobs describe wdpa-monthly", all_step_runs)
        self.assertNotIn("gcloud run jobs describe eamlis-monthly", all_step_runs)

        dockerfile = (REPO_ROOT / "ingestion/sea_ice_daily/Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            "COPY catalog/feature-identity-resolutions ./catalog/feature-identity-resolutions",
            dockerfile,
            "sea-ice-daily image must ship reviewed feature-identity resolutions; "
            "the job loads them from catalog/feature-identity-resolutions/ at runtime",
        )

        enforce_run = steps["Enforce sea-ice-daily resource-change allowlist"]["run"]
        self.assertEqual(
            python_literal_string_set(enforce_run, "allowed_exact"),
            {"module.sea_ice_daily_job.google_cloud_run_v2_job.this"},
        )
        self.assertIn('actions != ["update"]', enforce_run)
        self.assertIn("image != expected_image", enforce_run)
        self.assertIn("terraform -chdir=terraform/envs/prod show -json", steps["Export Terraform plan JSON"]["run"])
        self.assertIn("terraform_retry.sh\" -chdir=terraform/envs/prod apply", steps["Terraform apply"]["run"])
        self.assertLess(
            step_names.index("Enforce sea-ice-daily resource-change allowlist"),
            step_names.index("Terraform apply"),
        )

        self.assertLess(step_names.index("Terraform apply"), step_names.index("Confirm deployed digest"))
        self.assertLess(step_names.index("Confirm deployed digest"), step_names.index("Execute sea-ice-daily canary"))
        self.assertLess(step_names.index("Execute sea-ice-daily canary"), step_names.index("Validate latest IMS release contract"))
        self.assertLess(step_names.index("Validate latest IMS release contract"), step_names.index("Run IMS bucket hygiene audit"))
        self.assertLess(step_names.index("Run IMS bucket hygiene audit"), step_names.index("Resume sea-ice-daily scheduler"))
        self.assertIn("gcloud scheduler jobs resume", steps["Resume sea-ice-daily scheduler"]["run"])
        resume_run = steps["Resume sea-ice-daily scheduler"]["run"]
        self.assertLess(resume_run.index("--require-published"), resume_run.index("gcloud scheduler jobs resume"))


if __name__ == "__main__":
    unittest.main()
