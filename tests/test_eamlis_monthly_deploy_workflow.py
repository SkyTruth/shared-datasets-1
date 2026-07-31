from __future__ import annotations

import unittest
from pathlib import Path

from workflow_helpers import (
    load_workflow,
    python_literal_string_set,
    terraform_targets,
    workflow_steps_by_name,
    workflow_triggers,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/eamlis-monthly-deploy.yml"
DOCKERFILE = REPO_ROOT / "ingestion/eamlis_monthly/Dockerfile"

REQUIRED_SCRIPT_COPIES = (
    "scripts/pmtiles_zoom.py",
    "scripts/release_feature_model.py",
    "scripts/slack_notify.py",
    "scripts/vector_asset.py",
)


class EamlisMonthlyDeployWorkflowTests(unittest.TestCase):
    def test_eamlis_monthly_deploy_workflow_is_protected_and_digest_pinned(self):
        workflow = load_workflow(DEPLOY_WORKFLOW)
        trigger = workflow_triggers(workflow)
        deploy = workflow["jobs"]["deploy"]
        env = workflow["env"]
        steps = workflow_steps_by_name(workflow, "deploy")
        step_names = list(steps)

        self.assertEqual(workflow["name"], "EAMLIS monthly deploy")
        self.assertEqual(trigger["push"]["branches"], ["main"])
        self.assertIn("workflow_dispatch", trigger)
        self.assertIn("canary_run_date", trigger["workflow_dispatch"]["inputs"])
        push_paths = set(trigger["push"]["paths"])
        self.assertIn(".github/workflows/eamlis-monthly-deploy.yml", push_paths)
        self.assertIn("ingestion/common/**", push_paths)
        self.assertIn("ingestion/eamlis_monthly/**", push_paths)
        for script_path in REQUIRED_SCRIPT_COPIES:
            self.assertIn(script_path, push_paths)
        self.assertEqual(deploy["environment"], "shared-datasets-production")
        self.assertEqual(
            deploy["concurrency"],
            {"group": "prod-terraform-state", "cancel-in-progress": False},
        )
        self.assertEqual(steps["Check out repository"]["with"]["ref"], "main")
        self.assertEqual(env["IMAGE_NAME"], "eamlis-monthly")
        self.assertEqual(env["JOB_NAME"], "eamlis-monthly")
        self.assertEqual(env["ASSET_SLUG"], "eamlis-abandoned-mine-land-inventory")

        build_run = steps["Build eamlis-monthly image"]["run"]
        self.assertIn("-f ingestion/eamlis_monthly/Dockerfile", build_run)
        self.assertIn("--platform linux/amd64", build_run)
        self.assertIn("EAMLIS_MONTHLY_IMAGE_TAG=${image_tag}", build_run)

        tools_run = steps["Smoke-test native tools in image"]["run"]
        self.assertIn("ogr2ogr --version", tools_run)
        self.assertIn("pmtiles version", tools_run)
        import_run = steps["Smoke-test job import closure in image"]["run"]
        self.assertIn("import ingestion.eamlis_monthly.run", import_run)
        self.assertIn("scripts.vector_asset", import_run)

        push_run = steps["Push eamlis-monthly image"]["run"]
        self.assertIn("docker push", push_run)
        self.assertIn("docker buildx imagetools inspect", push_run)
        self.assertIn("EAMLIS_MONTHLY_IMAGE=${image_ref}", push_run)

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
        self.assertEqual(
            python_literal_string_set(enforce_run, "allowed_exact"),
            {"module.eamlis_monthly_job.google_cloud_run_v2_job.this"},
        )
        self.assertIn('actions != ["update"]', enforce_run)
        self.assertIn("image != expected_image", enforce_run)
        self.assertIn("terraform -chdir=terraform/envs/prod show -json", steps["Export Terraform plan JSON"]["run"])
        self.assertIn("terraform -chdir=terraform/envs/prod apply", steps["Terraform apply"]["run"])
        self.assertLess(
            step_names.index("Enforce eamlis-monthly resource-change allowlist"),
            step_names.index("Terraform apply"),
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

    def test_eamlis_monthly_dockerfile_copies_scripts_import_closure(self):
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        for script_path in REQUIRED_SCRIPT_COPIES:
            self.assertIn(
                f"COPY {script_path} ./{script_path}",
                dockerfile,
                f"eamlis-monthly image must copy {script_path}; the job imports it at runtime",
            )


if __name__ == "__main__":
    unittest.main()
