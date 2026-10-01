from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import metadata_retirement_plan as retirement
from workflow_helpers import load_workflow, workflow_steps_by_name, workflow_triggers


def plan(address, actions, before=None, after=None):
    return {
        "resource_changes": [
            {
                "address": address,
                "change": {
                    "actions": actions,
                    "before": before,
                    "after": after,
                },
            }
        ]
    }


class RetirementPlanTests(unittest.TestCase):
    def test_only_retired_resources_can_be_deleted(self):
        for address in retirement.DELETE_ADDRESSES | {
            'google_iap_web_cloud_run_service_iam_member.metadata_service_accessors["user:maintainer@skytruth.org"]',
        }:
            with self.subTest(address=address):
                self.assertFalse(retirement.blocked_changes(plan(address, ["delete"])))
                for actions in (["create"], ["update"], ["delete", "create"]):
                    self.assertTrue(retirement.blocked_changes(plan(address, actions)))
        for address in (
            "google_storage_bucket.shared",
            "google_cloud_run_v2_service.catalog_viewer",
            "module.feature_preview_loader_service_account.google_service_account.this",
            'google_project_service.required["firestore.googleapis.com"]',
        ):
            self.assertTrue(retirement.blocked_changes(plan(address, ["delete"])))

    def test_both_databases_can_only_be_forgotten_without_destruction(self):
        for database, (address, name) in retirement.DATABASES.items():
            with self.subTest(address=address):
                self.assertFalse(
                    retirement.blocked_database_changes(
                        plan(address, ["forget"], {"name": name}), database
                    )
                )
                self.assertTrue(
                    retirement.blocked_changes(
                        plan(address, ["forget"], {"name": name})
                    )
                )
                self.assertTrue(
                    retirement.blocked_database_changes(
                        plan(address, ["forget"], {"name": "other"}), database
                    )
                )
                for actions in (
                    ["no-op"],
                    ["delete"],
                    ["create"],
                    ["update"],
                    ["delete", "create"],
                ):
                    self.assertTrue(
                        retirement.blocked_database_changes(
                            plan(address, actions, {"name": name}), database
                        )
                    )
                self.assertTrue(
                    retirement.blocked_database_changes(
                        plan("google_storage_bucket.preview_bucket", ["delete"]),
                        database,
                    )
                )
                other_database = "preview" if database == "production" else "production"
                other_address, other_name = retirement.DATABASES[other_database]
                self.assertTrue(
                    retirement.blocked_database_changes(
                        plan(other_address, ["forget"], {"name": other_name}), database
                    )
                )

    def test_preview_role_can_only_lose_datastore_permissions(self):
        address = "google_project_iam_custom_role.preview_terraform"
        before = {
            "project": "shared-datasets-1",
            "permissions": ["run.services.get", "datastore.entities.get"],
        }
        after = {"project": "shared-datasets-1", "permissions": ["run.services.get"]}
        self.assertFalse(
            retirement.blocked_changes(plan(address, ["update"], before, after))
        )
        for invalid in (
            {**after, "permissions": []},
            {**after, "permissions": ["run.services.get", "storage.objects.delete"]},
            {**after, "project": "another-project"},
        ):
            self.assertTrue(
                retirement.blocked_changes(plan(address, ["update"], before, invalid))
            )

    def test_canonical_write_alert_only_loses_loader_exemption(self):
        address = "google_monitoring_alert_policy.dataset_object_written_by_unapproved_principal"
        before = {
            "enabled": True,
            "conditions": [
                {
                    "condition_matched_log": [
                        {
                            "filter": 'resource.type="gcs_bucket" AND protoPayload.authenticationInfo.principalEmail!="metadata-index-loader@shared-datasets-1.iam.gserviceaccount.com"'
                        }
                    ]
                }
            ],
        }
        after = copy.deepcopy(before)
        after["conditions"][0]["condition_matched_log"][0]["filter"] = (
            'resource.type="gcs_bucket"'
        )
        self.assertFalse(
            retirement.blocked_changes(plan(address, ["update"], before, after))
        )
        self.assertTrue(
            retirement.blocked_changes(
                plan(address, ["update"], before, {**after, "enabled": False})
            )
        )

    def test_workflow_is_main_only_opt_in_protected_and_checks_saved_plan(self):
        root = Path(__file__).resolve().parents[1]
        workflow = load_workflow(root / ".github/workflows/metadata-stack-retire.yml")
        trigger = workflow_triggers(workflow)
        self.assertEqual(set(trigger), {"workflow_dispatch"})
        self.assertFalse(
            trigger["workflow_dispatch"]["inputs"]["retire_metadata_stack"]["default"]
        )
        job = workflow["jobs"]["retire"]
        self.assertEqual(job["environment"], "shared-datasets-production")
        self.assertEqual(job["concurrency"]["group"], "prod-terraform-state")
        steps = workflow_steps_by_name(workflow, "retire")
        self.assertIn(
            '"refs/heads/main"',
            steps["Validate main ref and auth configuration"]["run"],
        )
        self.assertEqual(steps["Check out reviewed main"]["with"]["ref"], "main")
        self.assertIn(
            "metadata_retirement_plan.py",
            steps["Export and validate retirement plan"]["run"],
        )
        self.assertIn(
            '"${RUNNER_TEMP}/metadata-retirement.tfplan"',
            steps["Apply approved retirement plan"]["run"],
        )
        self.assertNotIn("-refresh=false", steps["Plan metadata retirement"]["run"])

    def test_production_workflow_executes_both_plans_and_blocks_database_deletion(self):
        root = Path(__file__).resolve().parents[1]
        workflow = load_workflow(root / ".github/workflows/metadata-stack-retire.yml")
        steps = workflow_steps_by_name(workflow, "retire")
        names = list(steps)
        self.assertLess(
            names.index("Preserve retired production database"),
            names.index("Plan metadata retirement"),
        )
        # Terraform requires all root inputs even when the jobs are not targeted.
        required_inputs = set()
        for file in (root / "terraform/envs/prod").glob("*.tf"):
            for name, body in re.findall(
                r'variable "([^"]+)"\s*\{(.*?)^\}', file.read_text(), re.M | re.S
            ):
                if not re.search(r"\bdefault\s*=", body):
                    required_inputs.add(name)
        supplied_inputs = {
            key.removeprefix("TF_VAR_")
            for key in workflow["env"]
            if key.startswith("TF_VAR_")
        }
        for file in (root / "terraform/envs/prod").glob("*.auto.tfvars"):
            supplied_inputs.update(re.findall(r"^(\w+)\s*=", file.read_text(), re.M))
        self.assertFalse(required_inputs - supplied_inputs)

        temp_root = Path(tempfile.gettempdir()) / "shared-datasets-1" / "_scratch"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="metadata-workflow-test-", dir=temp_root
        ) as directory:
            work = Path(directory)
            stub = work / "terraform"
            stub.write_text(
                f"#!{sys.executable}\n"
                + """import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ["TEST_TERRAFORM_LOG"], "a") as handle:
    handle.write(json.dumps(args) + "\\n")
command = args[1]
if command == "plan":
    for name in ("wdpa_monthly_image", "sea_ice_daily_image", "eamlis_monthly_image"):
        if not os.environ.get("TF_VAR_" + name):
            raise SystemExit("Missing required image: " + name)
    output = next(arg.removeprefix("-out=") for arg in args if arg.startswith("-out="))
    database = "-target=google_firestore_database.feature_metadata" in args
    change = {
        "address": "google_firestore_database.feature_metadata" if database else "google_cloud_run_v2_service.metadata_service",
        "change": {"actions": [os.environ.get("TEST_DATABASE_ACTION", "forget") if database else "delete"], "before": {"name": "(default)"}},
    }
    pathlib.Path(output).write_text(json.dumps({"resource_changes": [change]}))
elif command == "show":
    print(pathlib.Path(args[-1]).read_text())
"""
            )
            stub.chmod(0o755)
            log = work / "calls.jsonl"
            env = {
                **os.environ,
                **{
                    key: str(value)
                    for key, value in workflow["env"].items()
                    if "${{" not in str(value)
                },
                "PATH": f"{work}:{Path(sys.executable).parent}:{os.environ['PATH']}",
                "RUNNER_TEMP": directory,
                "TEST_TERRAFORM_LOG": str(log),
            }
            run = "\n".join(
                steps[name]["run"]
                for name in (
                    "Preserve retired production database",
                    "Plan metadata retirement",
                    "Export and validate retirement plan",
                    "Apply approved retirement plan",
                )
            )
            result = subprocess.run(
                ["bash", "-e", "-c", run],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(
                [call[1] for call in calls],
                ["plan", "show", "apply", "plan", "show", "apply"],
            )
            self.assertIn("-refresh=false", calls[0])
            self.assertNotIn("-refresh=false", calls[3])
            self.assertEqual(
                {
                    arg.removeprefix("-target=")
                    for arg in calls[3]
                    if arg.startswith("-target=")
                },
                retirement.DELETE_ADDRESSES
                | retirement.UPDATE_ADDRESSES
                | {
                    "google_iap_web_cloud_run_service_iam_member.metadata_service_accessors",
                },
            )
            self.assertEqual(calls[2][-1], calls[0][-1].removeprefix("-out="))
            self.assertEqual(calls[5][-1], calls[3][3].removeprefix("-out="))

            log.write_text("")
            result = subprocess.run(
                ["bash", "-e", "-c", run],
                cwd=root,
                env={**env, "TEST_DATABASE_ACTION": "delete"},
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Refusing metadata retirement plan", result.stdout)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual([call[1] for call in calls], ["plan", "show"])

    def test_preview_detaches_database_before_destructive_plans(self):
        root = Path(__file__).resolve().parents[1]
        for filename, job, detach_name, destroy_name in (
            (
                "feature-preview-deploy.yml",
                "preview",
                "Preserve retired preview database before reset or deployment",
                "Terraform reset plan",
            ),
            (
                "feature-preview-destroy.yml",
                "destroy",
                "Preserve retired preview database before destroy",
                "Terraform destroy plan",
            ),
        ):
            workflow = load_workflow(root / ".github/workflows" / filename)
            steps = workflow_steps_by_name(workflow, job)
            names = list(steps)
            self.assertLess(names.index(detach_name), names.index(destroy_name))
            self.assertIn("--database-only preview", steps[detach_name]["run"])
            self.assertIn("-refresh=false", steps[detach_name]["run"])
