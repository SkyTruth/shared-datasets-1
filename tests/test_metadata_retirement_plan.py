from __future__ import annotations

import copy
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
        for address, name, check in (
            (retirement.DATABASE_ADDRESS, "(default)", retirement.blocked_changes),
            (
                retirement.PREVIEW_DATABASE_ADDRESS,
                "feature-preview",
                retirement.blocked_preview_database_changes,
            ),
        ):
            with self.subTest(address=address):
                self.assertFalse(check(plan(address, ["forget"], {"name": name})))
                self.assertTrue(check(plan(address, ["forget"], {"name": "other"})))
                for actions in (
                    ["delete"],
                    ["create"],
                    ["update"],
                    ["delete", "create"],
                ):
                    self.assertTrue(check(plan(address, actions, {"name": name})))
        self.assertTrue(
            retirement.blocked_preview_database_changes(
                plan("google_storage_bucket.preview_bucket", ["delete"])
            )
        )
        self.assertTrue(
            retirement.blocked_preview_database_changes(
                plan(
                    retirement.PREVIEW_DATABASE_ADDRESS,
                    ["no-op"],
                    {"name": "feature-preview"},
                )
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
            self.assertIn("--preview-database-only", steps[detach_name]["run"])
            self.assertIn("-refresh=false", steps[detach_name]["run"])
