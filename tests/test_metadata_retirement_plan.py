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
from unittest import mock

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
        for address in (
            retirement.DELETE_ADDRESSES
            - retirement.LEGACY_DELETE_IDENTITIES.keys()
            - retirement.DELETE_ACCOUNT_IDENTITIES.keys()
            | {
                'google_iap_web_cloud_run_service_iam_member.metadata_service_accessors["user:maintainer@skytruth.org"]',
            }
        ):
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

    def test_legacy_removal_requires_exact_observed_identity(self):
        for address, identity in {
            **retirement.LEGACY_DELETE_IDENTITIES,
            **retirement.DELETE_ACCOUNT_IDENTITIES,
        }.items():
            with self.subTest(address=address):
                self.assertFalse(
                    retirement.blocked_changes(plan(address, ["delete"], identity))
                )
                for key in identity:
                    invalid = {**identity, key: "different-active-resource"}
                    self.assertTrue(
                        retirement.blocked_changes(plan(address, ["delete"], invalid))
                    )
                self.assertTrue(
                    retirement.blocked_changes(plan(address, ["delete"], {}))
                )
                for actions in (["create"], ["update"], ["delete", "create"]):
                    self.assertTrue(
                        retirement.blocked_changes(plan(address, actions, identity))
                    )

        account = "module.preview_metadata_index_loader_service_account.google_service_account.this"
        self.assertTrue(
            retirement.blocked_changes(
                plan(
                    account,
                    ["delete"],
                    {
                        "project": "shared-datasets-1",
                        "account_id": "feature-preview-loader",
                        "email": "feature-preview-loader@shared-datasets-1.iam.gserviceaccount.com",
                    },
                )
            )
        )
        binding = (
            "google_project_iam_member.preview_metadata_index_loader_firestore_user"
        )
        self.assertTrue(
            retirement.blocked_changes(
                plan(
                    binding,
                    ["delete"],
                    {
                        "project": "shared-datasets-1",
                        "role": "roles/datastore.user",
                        "member": "serviceAccount:feature-preview-loader@shared-datasets-1.iam.gserviceaccount.com",
                        "condition": [],
                    },
                )
            )
        )

    def test_deletion_authority_uses_immutable_ids_from_validated_plan(self):
        for address, identity in retirement.DELETE_ACCOUNT_IDENTITIES.items():
            with self.subTest(address=address):
                self.assertEqual(
                    retirement.retirement_account_ids(
                        plan(address, ["delete"], identity)
                    ),
                    [identity["unique_id"]],
                )
                with self.assertRaises(ValueError):
                    retirement.retirement_account_ids(
                        plan(
                            address,
                            ["delete"],
                            {**identity, "unique_id": "recreated-account"},
                        )
                    )
        self.assertEqual(retirement.retirement_account_ids({}), [])
        with self.assertRaises(ValueError):
            retirement.retirement_account_ids(
                plan(
                    "module.feature_preview_loader_service_account.google_service_account.this",
                    ["delete"],
                )
            )

    def test_temporary_iam_only_grants_planned_account_deletions_and_cleans_up(self):
        for account_id in retirement.DELETE_ACCOUNT_IDS:
            address = (
                f'google_service_account_iam_member.retirement_deleter["{account_id}"]'
            )
            identity = {
                "service_account_id": f"projects/shared-datasets-1/serviceAccounts/{retirement.DELETE_ACCOUNT_EMAILS[account_id]}",
                "role": retirement.DELETE_ROLE,
                "member": retirement.TERRAFORM_MEMBER,
                "condition": [],
            }
            with self.subTest(account_id=account_id):
                self.assertFalse(
                    retirement.blocked_delete_iam_changes(
                        plan(address, ["create"], after=identity), {account_id}
                    )
                )
                self.assertFalse(
                    retirement.blocked_delete_iam_changes(
                        plan(address, ["delete"], before=identity), set()
                    )
                )
                self.assertTrue(
                    retirement.blocked_delete_iam_changes(
                        plan(address, ["create"], after=identity), set()
                    )
                )
                for key in identity:
                    invalid = {**identity, key: "another-resource-or-authority"}
                    for actions, before, after in (
                        (["create"], None, invalid),
                        (["delete"], invalid, None),
                    ):
                        self.assertTrue(
                            retirement.blocked_delete_iam_changes(
                                plan(address, actions, before, after), {account_id}
                            )
                        )
                for actions in (["update"], ["delete", "create"]):
                    self.assertTrue(
                        retirement.blocked_delete_iam_changes(
                            plan(address, actions, identity, identity), {account_id}
                        )
                    )
        for address in (
            'google_service_account_iam_member.retirement_deleter["100846506355649701710"]',
            "google_project_iam_member.project_wide_deletion",
            "google_project_iam_custom_role.preview_terraform",
        ):
            self.assertTrue(
                retirement.blocked_delete_iam_changes(
                    plan(address, ["create"], after={}),
                    set(retirement.DELETE_ACCOUNT_IDS),
                )
            )

    def test_iam_root_matches_plan_authority_and_defaults_to_no_grants(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "terraform/envs/metadata-retirement-iam/main.tf").read_text()
        self.assertEqual(
            set(re.findall(r'"(\d{21})"', source)), retirement.DELETE_ACCOUNT_IDS
        )
        self.assertIn('role               = "roles/iam.serviceAccountDeleter"', source)
        self.assertIn(
            'member             = "' + retirement.TERRAFORM_MEMBER + '"', source
        )
        self.assertIn("default     = []", source)
        self.assertIn("condition     = self.unique_id == each.key", source)
        self.assertIn(
            "data.google_service_account.retirement_target[each.key].name", source
        )
        self.assertIn(
            'prefix = "000-system/terraform/state/metadata-retirement-iam"', source
        )
        from scripts.ci_preflight import suite_commands
        commands = [args for args, _ in suite_commands('lint', root, {'base':'base','tested_sha':'head'}, root)]
        self.assertIn(['terraform', '-chdir=terraform/envs/metadata-retirement-iam', 'validate'], commands)
        self.assertIn(['terraform', '-chdir=terraform/envs/metadata-retirement-iam', 'test'], commands)

    def test_delete_permission_wait_is_bounded_and_only_checks_planned_ids(self):
        address, identity = next(iter(retirement.DELETE_ACCOUNT_IDENTITIES.items()))
        retire_plan = plan(address, ["delete"], identity)
        with (
            mock.patch.object(
                retirement.subprocess, "check_output", return_value="token\n"
            ) as token,
            mock.patch.object(
                retirement, "test_delete_permission", side_effect=[False, True]
            ) as check,
            mock.patch.object(retirement.time, "monotonic", side_effect=[0, 1]),
            mock.patch.object(retirement.time, "sleep") as sleep,
        ):
            retirement.wait_for_delete_permissions(retire_plan)
            self.assertEqual(
                check.call_args_list, [mock.call(identity["unique_id"], "token")] * 2
            )
            sleep.assert_called_once_with(10)
            token.assert_called_once_with(
                ["gcloud", "auth", "print-access-token"], text=True
            )
        with (
            mock.patch.object(
                retirement.subprocess, "check_output", return_value="token"
            ),
            mock.patch.object(retirement, "test_delete_permission", return_value=False),
            mock.patch.object(retirement.time, "monotonic", side_effect=[0, 300]),
            mock.patch.object(retirement.time, "sleep") as sleep,
        ):
            with self.assertRaises(TimeoutError):
                retirement.wait_for_delete_permissions(retire_plan)
            sleep.assert_not_called()
        with mock.patch.object(retirement.subprocess, "check_output") as token:
            retirement.wait_for_delete_permissions({})
            token.assert_not_called()

    def test_permission_api_checks_only_delete_without_exposing_token(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = (
            b'{"permissions":["iam.serviceAccounts.delete"]}'
        )
        with mock.patch.object(
            retirement.urllib.request, "urlopen", return_value=response
        ) as open_url:
            self.assertTrue(
                retirement.test_delete_permission(
                    "117696104962177306505", "private-token"
                )
            )
            request = open_url.call_args.args[0]
            self.assertTrue(
                request.full_url.endswith("/117696104962177306505:testIamPermissions")
            )
            self.assertEqual(
                json.loads(request.data),
                {"permissions": ["iam.serviceAccounts.delete"]},
            )
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(open_url.call_args.kwargs, {"timeout": 30})
        with mock.patch.object(
            retirement.urllib.request, "urlopen", side_effect=OSError("API failure")
        ):
            with self.assertRaisesRegex(OSError, "API failure"):
                retirement.test_delete_permission(
                    "117696104962177306505", "private-token"
                )

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
    if "metadata-retirement-iam" in args[0]:
        pathlib.Path(output).write_text(json.dumps({"resource_changes": []}))
        raise SystemExit(0)
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
                    "Authorize deletion of remaining retired accounts",
                    "Wait for account deletion authority",
                    "Apply approved retirement plan",
                    "Remove temporary deletion authority",
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
                [
                    "plan",
                    "show",
                    "apply",
                    "plan",
                    "show",
                    "init",
                    "plan",
                    "show",
                    "apply",
                    "apply",
                    "plan",
                    "show",
                    "apply",
                ],
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
            self.assertEqual(calls[9][-1], calls[3][3].removeprefix("-out="))
            self.assertEqual(calls[8][-1], calls[6][-1].removeprefix("-out="))
            self.assertEqual(calls[12][-1], calls[10][-1].removeprefix("-out="))
            self.assertIn("metadata-retirement-iam", calls[6][0])
            self.assertNotIn("-var-file=", " ".join(calls[10]))

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
