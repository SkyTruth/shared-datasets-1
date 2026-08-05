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
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/wdpa-monthly-deploy.yml"
DOCKERFILE = REPO_ROOT / "ingestion/wdpa_monthly/Dockerfile"

REQUIRED_SCRIPT_COPIES = (
    "scripts/feature_metadata_localization.py",
    "scripts/pmtiles_zoom.py",
    "scripts/release_feature_model.py",
    "scripts/slack_notify.py",
    "scripts/vector_asset.py",
)


class WdpaMonthlyDeployWorkflowTests(unittest.TestCase):
    def test_wdpa_monthly_deploy_workflow_is_protected_and_digest_pinned(self):
        workflow = load_workflow(DEPLOY_WORKFLOW)
        trigger = workflow_triggers(workflow)
        deploy = workflow["jobs"]["deploy"]
        env = workflow["env"]
        steps = workflow_steps_by_name(workflow, "deploy")
        step_names = list(steps)

        self.assertEqual(workflow["name"], "WDPA monthly deploy")
        self.assertEqual(trigger["push"]["branches"], ["main"])
        self.assertIn("workflow_dispatch", trigger)
        self.assertIn("canary_run_date", trigger["workflow_dispatch"]["inputs"])
        push_paths = set(trigger["push"]["paths"])
        self.assertIn(".github/workflows/wdpa-monthly-deploy.yml", push_paths)
        self.assertIn("catalog/feature-identity-resolutions/**", push_paths)
        self.assertIn("ingestion/common/**", push_paths)
        self.assertIn("ingestion/wdpa_monthly/**", push_paths)
        for script_path in REQUIRED_SCRIPT_COPIES:
            self.assertIn(script_path, push_paths)
        self.assertEqual(deploy["environment"], "shared-datasets-production")
        self.assertEqual(
            deploy["concurrency"],
            {"group": "prod-terraform-state-wdpa-monthly", "cancel-in-progress": False},
        )
        self.assertEqual(steps["Check out repository"]["with"]["ref"], "main")
        self.assertEqual(env["IMAGE_NAME"], "wdpa-monthly")
        self.assertEqual(env["JOB_NAME"], "wdpa-monthly")

        build_run = steps["Build wdpa-monthly image"]["run"]
        self.assertIn("-f ingestion/wdpa_monthly/Dockerfile", build_run)
        self.assertIn("--platform linux/amd64", build_run)
        self.assertIn("WDPA_MONTHLY_IMAGE_TAG=${image_tag}", build_run)

        self.assertIn("tippecanoe --version", steps["Smoke-test native tools in image"]["run"])
        import_run = steps["Smoke-test job import closure in image"]["run"]
        self.assertIn("import ingestion.wdpa_monthly.run", import_run)
        self.assertIn("scripts.feature_metadata_localization", import_run)
        self.assertIn("scripts.vector_asset", import_run)

        push_run = steps["Push wdpa-monthly image"]["run"]
        self.assertIn("docker push", push_run)
        self.assertIn("docker buildx imagetools inspect", push_run)
        self.assertIn("WDPA_MONTHLY_IMAGE=${image_ref}", push_run)

        plan_run = steps["Terraform plan"]["run"]
        self.assertEqual(
            terraform_targets(plan_run),
            {"module.wdpa_monthly_job.google_cloud_run_v2_job.this"},
        )
        self.assertIn("wdpa_monthly_image=${WDPA_MONTHLY_IMAGE}", plan_run)
        self.assertIn("eamlis_monthly_image=unused-by-wdpa-monthly-deploy", plan_run)
        self.assertIn("sea_ice_daily_image=unused-by-wdpa-monthly-deploy", plan_run)
        all_step_runs = "\n".join(str(step.get("run", "")) for step in steps.values())
        self.assertNotIn("gcloud run jobs describe eamlis-monthly", all_step_runs)
        self.assertNotIn("gcloud run jobs describe sea-ice-daily", all_step_runs)

        enforce_run = steps["Enforce wdpa-monthly resource-change allowlist"]["run"]
        self.assertEqual(
            python_literal_string_set(enforce_run, "allowed_exact"),
            {"module.wdpa_monthly_job.google_cloud_run_v2_job.this"},
        )
        self.assertIn('actions != ["update"]', enforce_run)
        self.assertIn("image != expected_image", enforce_run)
        self.assertIn("terraform -chdir=terraform/envs/prod show -json", steps["Export Terraform plan JSON"]["run"])
        self.assertIn("terraform -chdir=terraform/envs/prod apply", steps["Terraform apply"]["run"])
        self.assertLess(
            step_names.index("Enforce wdpa-monthly resource-change allowlist"),
            step_names.index("Terraform apply"),
        )

        self.assertLess(step_names.index("Terraform apply"), step_names.index("Confirm deployed digest"))
        self.assertLess(step_names.index("Confirm deployed digest"), step_names.index("Execute wdpa-monthly canary"))
        self.assertLess(step_names.index("Execute wdpa-monthly canary"), step_names.index("Watch wdpa-monthly canary"))

        canary_run = steps["Execute wdpa-monthly canary"]["run"]
        self.assertIn("--async", canary_run)
        self.assertIn("RUN_DATE=${CANARY_RUN_DATE}", canary_run)
        watch_run = steps["Watch wdpa-monthly canary"]["run"]
        self.assertIn("gcloud run jobs executions describe", watch_run)

    def test_wdpa_monthly_dockerfile_copies_scripts_import_closure(self):
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        for script_path in REQUIRED_SCRIPT_COPIES:
            self.assertIn(
                f"COPY {script_path} ./{script_path}",
                dockerfile,
                f"wdpa-monthly image must copy {script_path}; the job imports it at runtime",
            )
        self.assertIn(
            "COPY catalog/feature-identity-resolutions ./catalog/feature-identity-resolutions",
            dockerfile,
            "wdpa-monthly image must ship reviewed feature-identity resolutions; "
            "the job loads them from catalog/feature-identity-resolutions/ at runtime",
        )


if __name__ == "__main__":
    unittest.main()
