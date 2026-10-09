"""Saved-plan operations, conditional authority, and provider state contracts."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import deployment_permissions as live
from scripts.terraform_plan_permissions import PROJECT, PROJECT_URL, plan_checks

NUMBER = "123456789"
ACCOUNT = f"catalog-viewer@{PROJECT}.iam.gserviceaccount.com"


def row(kind, actions, value, *, before=None, address=None):
    return {
        "address": address or kind + ".fixture", "mode": "managed", "type": kind,
        "change": {"actions": actions, "before": before if before is not None else value,
                   "after": None if actions == ["delete"] else value, "after_unknown": {}},
    }


def plan(*rows):
    return {"format_version": "1.2", "resource_changes": list(rows)}


def checks(value, target=None):
    return dict(plan_checks(value, project_number=NUMBER, target=target))


def permissions(value, target=None):
    return {permission for actual in checks(value, target).values() for permission in actual}


class SavedPlanPermissionTests(unittest.TestCase):
    def test_existing_url_map_update_requires_actual_map_and_backend_authority(self):
        value = {"project": PROJECT, "name": "shared-datasets-pmtiles-cdn", "default_service":
                 f"https://www.googleapis.com/compute/v1/projects/{PROJECT}/global/backendBuckets/shared-datasets"}
        actual = checks(plan(row("google_compute_url_map", ["update"], value)), "pmtiles-cdn")
        map_url = f"https://compute.googleapis.com/compute/v1/projects/{PROJECT}/global/urlMaps/{value['name']}/testIamPermissions"
        self.assertEqual(set(actual[map_url]), {"compute.urlMaps.update", "compute.urlMaps.get", "compute.urlMaps.invalidateCache"})
        self.assertEqual(actual[map_url.replace("urlMaps/" + value["name"], "backendBuckets/shared-datasets")], ("compute.backendBuckets.use",))
        self.assertNotIn("compute.urlMaps.create", permissions(plan(row("google_compute_url_map", ["update"], value)), "pmtiles-cdn"))
        self.assertNotIn("compute.urlMaps.delete", permissions(plan(row("google_compute_url_map", ["update"], value)), "pmtiles-cdn"))

    def test_noop_cdn_still_requires_cache_invalidation_and_exact_cache_target(self):
        value = {"project": PROJECT, "name": "shared-datasets-pmtiles-cdn"}
        self.assertEqual(permissions(plan(row("google_compute_url_map", ["no-op"], value)), "pmtiles-cdn"), {"compute.urlMaps.invalidateCache"})
        with self.assertRaisesRegex(ValueError, "cache target missing"):
            checks(plan(), "pmtiles-cdn")
        with self.assertRaisesRegex(ValueError, "unexpected CDN"):
            checks(plan(row("google_compute_url_map", ["no-op"], {**value, "name": "other"})), "pmtiles-cdn")

    def test_unknown_or_foreign_backend_does_not_pass_before_apply(self):
        value = {"project": PROJECT, "name": "shared-datasets-pmtiles-cdn", "default_service": "projects/other-project/global/backendBuckets/untrusted"}
        with self.assertRaisesRegex(ValueError, "URL-map backend"):
            checks(plan(row("google_compute_url_map", ["update"], value)), "pmtiles-cdn")
        value["default_service"] = "unresolved-backend"
        with self.assertRaisesRegex(ValueError, "URL-map backend"):
            checks(plan(row("google_compute_url_map", ["update"], value)), "pmtiles-cdn")
        value["default_service"] = None
        change = row("google_compute_url_map", ["update"], value)
        change["change"]["after_unknown"] = {"path_matcher": [{"path_rule": [{"service": True}]}]}
        with self.assertRaisesRegex(ValueError, "dependencies remain unknown"):
            checks(plan(change), "pmtiles-cdn")

    def test_provider_bucket_prefix_and_conditioned_iam_resource_are_preserved(self):
        value = {"bucket": "b/skytruth-shared-datasets-1", "role": "roles/storage.objectViewer", "condition": [{"expression": "resource.name.startsWith('allowed/')"}]}
        actual = checks(plan(row("google_storage_bucket_iam_member", ["update"], value)))
        self.assertEqual(len(actual), 1)
        url, required = next(iter(actual.items()))
        self.assertIn("/b/skytruth-shared-datasets-1/iam/testPermissions?", url)
        self.assertEqual(set(required), {"storage.buckets.getIamPolicy", "storage.buckets.setIamPolicy"})
        self.assertNotIn("storage.buckets.create", required)

    def test_managed_folder_delete_requires_authority_on_the_actual_deleted_folder(self):
        value = {"bucket": "b/skytruth-shared-datasets-1", "name": "old-public/prefix/"}
        actual = checks(plan(row("google_storage_managed_folder", ["delete"], value)))
        url, required = next(iter(actual.items()))
        self.assertIn("/managedFolders/old-public%2Fprefix%2F/iam/testPermissions?", url)
        self.assertEqual(required, ("storage.managedFolders.delete", "storage.managedFolders.get"))
        # The existing bootstrap deliberately has no delete authority. A plan
        # that begins deleting folders cannot silently pass a create-only hint.
        missing = Mock(return_value=["storage.managedFolders.delete"])
        with self.assertRaisesRegex(RuntimeError, "managedFolders.delete"):
            live.verify_checks(list(actual.items()), missing, attempts=1)

    def test_new_folder_policy_is_checked_on_its_known_bucket_parent(self):
        folder = {"bucket": "skytruth-shared-datasets-1", "name": "new-public/"}
        policy = {"bucket": "b/skytruth-shared-datasets-1", "managed_folder": "new-public/"}
        actual = checks(plan(row("google_storage_managed_folder", ["create"], folder), row("google_storage_managed_folder_iam_member", ["create"], policy)))
        self.assertTrue(all("/managedFolders/" not in url for url in actual))
        self.assertEqual(permissions(plan(row("google_storage_managed_folder", ["create"], folder), row("google_storage_managed_folder_iam_member", ["create"], policy))), {"storage.managedFolders.create", "storage.managedFolders.get", "storage.managedFolders.getIamPolicy", "storage.managedFolders.setIamPolicy"})

    def test_bucket_delete_requires_delete_but_force_destroy_is_not_authorized(self):
        value = {"project": PROJECT, "name": "shared-datasets-1-catalog-comparisons", "force_destroy": False}
        self.assertEqual(permissions(plan(row("google_storage_bucket", ["delete"], value))), {"storage.buckets.get", "storage.buckets.delete"})
        with self.assertRaisesRegex(ValueError, "separate explicit object authorization"):
            checks(plan(row("google_storage_bucket", ["delete"], {**value, "force_destroy": True})))

    def test_custom_role_update_does_not_require_creation_or_deletion(self):
        actual = permissions(plan(row("google_project_iam_custom_role", ["update"], {"project": PROJECT, "role_id": "sharedDatasetsPmtilesManagedFolderSync"})))
        self.assertEqual(actual, {"iam.roles.get", "iam.roles.update"})

    def test_logging_exclusion_update_requires_only_its_actual_operations(self):
        actual = permissions(plan(row('google_logging_project_exclusion', ['update'], {'project': PROJECT, 'name': 'dataset-usage-exported-copy'})))
        self.assertEqual(actual, {'logging.exclusions.get', 'logging.exclusions.update'})

    def test_viewer_saved_plan_checks_all_actual_resource_classes(self):
        bucket = "shared-datasets-1-catalog-comparisons"
        rows = [
            row("google_cloud_run_v2_service", ["update"], {"project": PROJECT, "location": "us-central1", "name": "catalog-viewer", "template": [{"service_account": ACCOUNT}]}),
            row("google_cloud_run_v2_service_iam_member", ["update"], {"project": PROJECT, "location": "us-central1", "name": "catalog-viewer"}),
            row("google_project_iam_custom_role", ["update"], {"project": PROJECT, "role_id": "sharedDatasetsCatalogViewerSignBlob"}),
            row("google_service_account", ["update"], {"project": PROJECT, "account_id": "catalog-viewer"}, address="module.catalog_viewer_service_account.google_service_account.this"),
            row("google_service_account_iam_member", ["create"], {"service_account_id": f"projects/{PROJECT}/serviceAccounts/{ACCOUNT}"}),
            row("google_secret_manager_secret_iam_member", ["update"], {"project": PROJECT, "secret_id": "pmtiles-cdn-signed-request-key"}),
            row("google_storage_bucket", ["update"], {"project": PROJECT, "name": bucket}),
            row("google_storage_bucket_iam_member", ["update"], {"bucket": bucket}),
            row("google_iap_web_cloud_run_service_iam_member", ["update"], {"project": PROJECT, "location": "us-central1", "cloud_run_service_name": f"projects/{PROJECT}/iap_web/cloud_run-us-central1/services/catalog-viewer"}),
        ]
        actual = checks(plan(*rows), "catalog-viewer")
        self.assertIn(f"https://iap.googleapis.com/v1/projects/{NUMBER}/iap_web/cloud_run-us-central1/services/catalog-viewer:testIamPermissions", actual)
        sa_url = f"https://iam.googleapis.com/v1/projects/{PROJECT}/serviceAccounts/{ACCOUNT}:testIamPermissions"
        self.assertEqual(set(actual[sa_url]), {"iam.serviceAccounts.actAs", "iam.serviceAccounts.get", "iam.serviceAccounts.update", "iam.serviceAccounts.getIamPolicy", "iam.serviceAccounts.setIamPolicy"})
        self.assertIn(f"https://secretmanager.googleapis.com/v1/projects/{PROJECT}/secrets/pmtiles-cdn-signed-request-key:testIamPermissions", actual)
        all_required = permissions(plan(*rows), "catalog-viewer")
        self.assertNotIn("run.services.create", all_required)
        self.assertNotIn("storage.buckets.delete", all_required)
        self.assertNotIn("iam.serviceAccounts.create", all_required)

    def test_new_comparison_bucket_policy_checks_project_inherited_authority(self):
        bucket = "shared-datasets-1-catalog-comparisons"
        actual = checks(plan(row("google_storage_bucket", ["create"], {"project": PROJECT, "name": bucket}), row("google_storage_bucket_iam_member", ["create"], {"bucket": bucket})))
        self.assertEqual(set(actual[PROJECT_URL]), {"storage.buckets.create", "storage.buckets.get", "storage.buckets.getIamPolicy", "storage.buckets.setIamPolicy"})

    def test_actual_registry_secret_and_project_policy_resource_contracts(self):
        actual = checks(plan(
            row("google_artifact_registry_repository_iam_member", ["create"], {"project": PROJECT, "location": "us-central1", "repository": "shared-datasets-jobs"}),
            row("google_secret_manager_secret_iam_member", ["delete"], {"project": PROJECT, "secret_id": f"projects/{NUMBER}/secrets/shared-datasets-slack-webhook-url"}),
            row("google_project_iam_member", ["update"], {"project": PROJECT}),
        ))
        self.assertEqual(set(actual[PROJECT_URL]), {"resourcemanager.projects.getIamPolicy", "resourcemanager.projects.setIamPolicy"})
        self.assertEqual(actual[f"https://artifactregistry.googleapis.com/v1/projects/{PROJECT}/locations/us-central1/repositories/shared-datasets-jobs:testIamPermissions"], ("artifactregistry.repositories.getIamPolicy", "artifactregistry.repositories.setIamPolicy"))
        self.assertEqual(actual[f"https://secretmanager.googleapis.com/v1/projects/{PROJECT}/secrets/shared-datasets-slack-webhook-url:testIamPermissions"], ("secretmanager.secrets.getIamPolicy", "secretmanager.secrets.setIamPolicy"))

    def test_job_and_scheduler_update_check_actual_runtime_identity(self):
        job = {"project": PROJECT, "location": "us-central1", "name": "wdpa-monthly", "template": [{"template": [{"service_account": ACCOUNT}]}]}
        scheduler = {"project": PROJECT, "region": "us-central1", "name": "wdpa-monthly", "http_target": [{"oauth_token": [{"service_account_email": ACCOUNT}]}]}
        actual = checks(plan(row("google_cloud_run_v2_job", ["update"], job), row("google_cloud_scheduler_job", ["update"], scheduler)))
        self.assertEqual(actual[f"https://run.googleapis.com/v2/projects/{PROJECT}/locations/us-central1/jobs/wdpa-monthly:testIamPermissions"], ("run.jobs.get", "run.jobs.update"))
        self.assertEqual(set(actual[PROJECT_URL]), {"run.operations.get", "cloudscheduler.jobs.update", "cloudscheduler.jobs.get"})
        self.assertEqual(actual[f"https://iam.googleapis.com/v1/projects/{PROJECT}/serviceAccounts/{ACCOUNT}:testIamPermissions"], ("iam.serviceAccounts.actAs",))

    def test_logging_alert_update_checks_both_notification_rule_operations(self):
        value = {"project": PROJECT, "conditions": [{"condition_matched_log": [{"filter": "severity>=ERROR"}]}], "notification_channels": [f"projects/{PROJECT}/notificationChannels/123"]}
        required = permissions(plan(row("google_monitoring_alert_policy", ["update"], value)))
        self.assertEqual(required, {"monitoring.alertPolicies.update", "monitoring.alertPolicies.get", "monitoring.notificationChannels.get", "logging.notificationRules.create", "logging.notificationRules.delete"})
        create = permissions(plan(row("google_monitoring_alert_policy", ["create"], value)))
        self.assertNotIn("logging.notificationRules.delete", create)
        self.assertNotIn("monitoring.alertPolicies.delete", create)

    def test_scheduler_provider_pause_and_resume_require_their_actual_operations(self):
        value = {"project": PROJECT, "region": "us-central1", "name": "wdpa-monthly", "paused": True}
        before = {**value, "paused": False}
        paused = permissions(plan(row("google_cloud_scheduler_job", ["update"], value, before=before)))
        self.assertIn("cloudscheduler.jobs.pause", paused)
        self.assertNotIn("cloudscheduler.jobs.enable", paused)
        resumed = permissions(plan(row("google_cloud_scheduler_job", ["update"], before, before=value)))
        self.assertIn("cloudscheduler.jobs.enable", resumed)
        self.assertNotIn("cloudscheduler.jobs.pause", resumed)
        created = permissions(plan(row("google_cloud_scheduler_job", ["create"], before)))
        self.assertIn("cloudscheduler.jobs.enable", created)

    def test_replacement_checks_both_old_and_new_actual_iam_targets(self):
        before = {"project": PROJECT, "secret_id": "old-secret"}
        after = {"project": PROJECT, "secret_id": "new-secret"}
        actual = checks(plan(row("google_secret_manager_secret_iam_member", ["delete", "create"], after, before=before)))
        self.assertEqual(len(actual), 2)
        self.assertTrue(any("/secrets/old-secret:" in url for url in actual))
        self.assertTrue(any("/secrets/new-secret:" in url for url in actual))

    def test_unknown_future_identity_needs_an_explicit_creation_dependency(self):
        account = row("google_service_account", ["create"], {"project": PROJECT, "account_id": "feature-preview-loader"}, address="module.loader.google_service_account.this")
        policy = row("google_service_account_iam_member", ["create"], {"service_account_id": None}, address="google_service_account_iam_member.loader")
        value = plan(account, policy)
        with self.assertRaisesRegex(ValueError, "explicit creation dependency"):
            checks(value)
        value["configuration"] = {"root_module": {"resources": [{"address": policy["address"], "expressions": {"service_account_id": {"references": ["module.loader.email"]}}}]}}
        self.assertEqual(set(checks(value)[PROJECT_URL]), {"iam.serviceAccounts.create", "iam.serviceAccounts.get", "iam.serviceAccounts.getIamPolicy", "iam.serviceAccounts.setIamPolicy"})
        missing = Mock(return_value=["iam.serviceAccounts.setIamPolicy"])
        with self.assertRaisesRegex(RuntimeError, "setIamPolicy"):
            live.verify_checks(list(checks(value).items()), missing, attempts=1)

    def test_foreign_iap_and_service_account_identities_are_rejected(self):
        values = [
            row("google_service_account_iam_member", ["create"], {"service_account_id": "projects/foreign/serviceAccounts/a@foreign.iam.gserviceaccount.com"}),
            row("google_iap_web_cloud_run_service_iam_member", ["update"], {"project": PROJECT, "location": "us-central1", "cloud_run_service_name": "projects/foreign/iap_web/cloud_run-us-central1/services/catalog-viewer"}),
        ]
        for value in values:
            with self.subTest(kind=value["type"]), self.assertRaisesRegex(ValueError, "foreign"):
                checks(plan(value))

    def test_unknown_changed_classes_actions_and_deferred_plans_fail_closed(self):
        original = plan(row("google_unreviewed_resource", ["create"], {"project": PROJECT}))
        with self.assertRaisesRegex(ValueError, "unsupported mutation class"):
            checks(original)
        original["resource_changes"][0]["change"]["actions"] = ["forget"]
        with self.assertRaisesRegex(ValueError, "action sequence"):
            checks(original)
        for key, value in (("errored", True), ("deferred_changes", [{}])):
            deferred = copy.deepcopy(plan())
            deferred[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "errored or deferred"):
                checks(deferred)

    def test_unchanged_resources_do_not_require_mutation_authority(self):
        self.assertEqual(checks(plan(row("google_unreviewed_resource", ["no-op"], {"name": "untouched"}))), {})


class SavedPlanCliTests(unittest.TestCase):
    def invoke(self, value, *, target="pmtiles-cdn"):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "reviewed.tfplan.json"
        path.write_text(json.dumps(value))
        return ["deployment_permissions.py", "--target", target, "--plan-json", str(path)]

    def test_plan_cli_checks_derived_operations_and_does_not_claim_broad_readiness(self):
        value = {"project": PROJECT, "name": "shared-datasets-pmtiles-cdn"}
        args = self.invoke(plan(row("google_compute_url_map", ["no-op"], value)))
        with patch("sys.argv", args), patch.object(live.subprocess, "check_output", side_effect=[NUMBER, "token"]), patch.object(live, "checks", side_effect=AssertionError("broad hints must not substitute for the plan")), patch.object(live, "request", return_value=[]) as request, patch("builtins.print") as output:
            live.main()
        self.assertEqual(request.call_args.args[1], ("compute.urlMaps.invalidateCache",))
        output.assert_called_once_with("Saved-plan mutation permissions and declared post-apply operations verified.")

    def test_unsupported_plan_never_starts_permission_probes(self):
        args = self.invoke(plan(row("google_unreviewed_resource", ["create"], {"project": PROJECT})), target="catalog-viewer")
        with patch("sys.argv", args), patch.object(live.subprocess, "check_output", return_value=NUMBER) as subprocess, patch.object(live, "request") as request:
            with self.assertRaisesRegex(ValueError, "unsupported mutation class"):
                live.main()
        request.assert_not_called()
        self.assertEqual(subprocess.call_count, 1)  # Project identity read only; no access token yet.


class ImageOperationPermissionTests(unittest.TestCase):
    def test_image_probe_checks_actual_repository_and_image_operations(self):
        [(url, required)] = live.checks("artifact-registry-images")
        self.assertEqual(url, f"https://artifactregistry.googleapis.com/v1/projects/{PROJECT}/locations/us-central1/repositories/shared-datasets-jobs:testIamPermissions")
        self.assertEqual(set(required), {
            "artifactregistry.repositories.get", "artifactregistry.repositories.downloadArtifacts",
            "artifactregistry.repositories.uploadArtifacts", "artifactregistry.dockerimages.get",
            "artifactregistry.tags.get", "artifactregistry.tags.create", "artifactregistry.tags.update",
        })
        self.assertTrue(set(required).isdisjoint(live.checks("artifact-registry")[0][1]))
        self.assertFalse(any(permission.endswith(("delete", "setIamPolicy", "createOnPush")) for permission in required))

    def test_one_missing_upload_or_pull_permission_cannot_be_claimed_ready(self):
        for permission in ("artifactregistry.repositories.uploadArtifacts", "artifactregistry.repositories.downloadArtifacts", "artifactregistry.tags.create"):
            with self.subTest(permission=permission), self.assertRaisesRegex(RuntimeError, permission):
                live.verify("artifact-registry-images", lambda url, required: [permission], attempts=1)

    def test_registry_grant_propagation_is_bounded_and_read_only(self):
        probe = Mock(side_effect=[["artifactregistry.repositories.uploadArtifacts"], []])
        pause = Mock()
        live.verify("artifact-registry-images", probe, attempts=2, pause=pause)
        self.assertEqual(probe.call_count, 2)
        pause.assert_called_once_with(10)
        self.assertTrue(all(call.args[0].endswith(":testIamPermissions") for call in probe.call_args_list))

    def test_image_cli_cannot_replace_operational_probe_with_saved_plan(self):
        args = ["deployment_permissions.py", "--target", "artifact-registry-images", "--plan-json", "reviewed.tfplan.json"]
        with patch("sys.argv", args), patch.object(live.subprocess, "check_output") as subprocess, patch.object(live, "request") as request:
            with self.assertRaises(SystemExit):
                live.main()
        subprocess.assert_not_called()
        request.assert_not_called()

    def test_image_cli_checks_repository_permissions_before_reporting_ready(self):
        args = ["deployment_permissions.py", "--target", "artifact-registry-images"]
        with patch("sys.argv", args), patch.object(live.subprocess, "check_output", return_value="token"), patch.object(live, "request", return_value=[]) as request, patch("builtins.print") as output:
            live.main()
        request.assert_called_once_with(*live.checks("artifact-registry-images")[0], "token")
        output.assert_called_once_with("artifact-registry-images: deployment permission prerequisites verified.")
