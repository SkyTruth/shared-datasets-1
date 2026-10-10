from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

import pytest

from scripts import metadata_retirement_plan as retirement
from workflow_helpers import load_workflow, workflow_steps_by_name


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
    def test_preview_database_can_only_be_forgotten_without_destruction(self):
        for database, (address, name) in retirement.DATABASES.items():
            with self.subTest(address=address):
                self.assertFalse(
                    retirement.blocked_database_changes(
                        plan(address, ["forget"], {"name": name}), database
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
                other_address = "google_firestore_database.feature_metadata"
                other_name = "(default)"
                self.assertTrue(
                    retirement.blocked_database_changes(
                        plan(other_address, ["forget"], {"name": other_name}), database
                    )
                )

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


@pytest.mark.parametrize("document", [
    {},
    plan("google_firestore_database.feature_preview", ["forget"], {"name": "feature-preview"}),
    {"resource_changes": [
        *plan("google_firestore_database.feature_preview", ["forget"], {"name": "feature-preview"})["resource_changes"],
        *plan("google_storage_bucket.preview_bucket", ["no-op"])["resource_changes"],
        *plan("data.google_service_account.preview", ["read"])["resource_changes"],
    ]},
])
def test_cli_validates_saved_preview_detachment_plans(tmp_path, document):
    saved = tmp_path / "plan.json"
    saved.write_text(json.dumps(document))
    script = Path(retirement.__file__)
    result = subprocess.run(
        [sys.executable, str(script), str(saved), "--database-only", "preview"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("arguments,exit_code", [
    ([], 2),
    (["--database-only", "production"], 2),
    (["--service-account-ids"], 2),
    (["--delete-iam-grants-from", "other.json"], 2),
    (["--delete-iam-cleanup"], 2),
    (["--wait-for-delete-permission"], 2),
    (["--database-only", "preview"], 1),
])
def test_cli_refuses_preview_database_deletion_in_every_invocation(tmp_path, arguments, exit_code):
    saved = tmp_path / "plan.json"
    saved.write_text(json.dumps(plan(
        "google_firestore_database.feature_preview", ["delete"], {"name": "feature-preview"},
    )))
    result = subprocess.run(
        [sys.executable, retirement.__file__, str(saved), *arguments],
        capture_output=True, text=True,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    if exit_code == 1:
        assert "delete google_firestore_database.feature_preview" in result.stdout
