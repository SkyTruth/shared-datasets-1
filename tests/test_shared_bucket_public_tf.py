from __future__ import annotations

import csv
import unittest
from pathlib import Path

from terraform_helpers import evaluate, load_terraform, resources

REPO_ROOT = Path(__file__).resolve().parents[1]


def folder_policy(rows: list[dict], bucket: str) -> dict:
    document = load_terraform(REPO_ROOT / "terraform/envs/prod/shared_bucket_public.tf")
    declared = {(kind, name): values for kind, name, values in resources(document)}
    folders = declared["google_storage_managed_folder", "shared_bucket_public_prefixes"]
    grants = declared["google_storage_managed_folder_iam_member", "shared_bucket_public_object_viewers"]
    assert grants["role"] == "roles/storage.objectViewer"
    assert grants["member"] == "allUsers"
    locals_ = {key: value for group in document["locals"] for key, value in group.items()}
    locals_.update({
        "shared_catalog_rows": rows,
        "policy_fixture_managed_names": folders["for_each"],
        "policy_fixture_folders": '${{for name in local.policy_fixture_managed_names : name => {bucket = var.bucket_name, name = name}}}',
        "policy_fixture_public_grants": grants["for_each"].replace(
            "google_storage_managed_folder.shared_bucket_public_prefixes", "local.policy_fixture_folders",
        ),
    })
    return evaluate(locals_, "{all = local.policy_fixture_managed_names, public = keys(local.policy_fixture_public_grants)}",
                    variables={"bucket_name": bucket})


def public_catalog_folder_names() -> set[str]:
    names = {"_catalog/"}
    with (REPO_ROOT / "catalog/shared-datasets-catalog.csv").open(newline="") as catalog_file:
        for row in csv.DictReader(catalog_file):
            if row["access_tier"].lower() != "public":
                continue
            canonical_path = row["canonical_path"]
            marker = f"/{row['asset_slug']}/latest/"
            prefix, _, _ = canonical_path.partition(marker)
            names.add(f"{prefix.removeprefix('gs://skytruth-shared-datasets-1/')}/{row['asset_slug']}/")
    return names


class SharedBucketPublicTerraformTests(unittest.TestCase):
    def test_shared_bucket_has_thirty_day_soft_delete(self):
        shared_bucket_tf = (REPO_ROOT / "terraform/envs/prod/shared_bucket_public.tf").read_text()

        self.assertIn("retention_duration_seconds = 2592000", shared_bucket_tf)
        self.assertIn("hierarchical_namespace", shared_bucket_tf)
        self.assertNotIn("versioning {", shared_bucket_tf)

    def test_managed_folder_public_grants_are_catalog_driven(self):
        document = load_terraform(REPO_ROOT / "terraform/envs/prod/shared_bucket_public.tf")
        declared = {(kind, name): attributes for kind, name, attributes in resources(document)}
        variables = {name: value for group in load_terraform(REPO_ROOT / "terraform/envs/prod/variables.tf")["variable"]
                     for name, value in group.items()}
        production = load_terraform(REPO_ROOT / "terraform/envs/prod/production.auto.tfvars")
        enabled = production.get("shared_bucket_public_object_viewer_enabled",
                                 variables["shared_bucket_public_object_viewer_enabled"]["default"])
        self.assertIs(enabled, False)
        public_bucket = declared["google_storage_bucket_iam_member", "shared_bucket_public_object_viewer"]
        self.assertEqual(evaluate({"count": public_bucket["count"]}, "local.count",
                                  variables={"shared_bucket_public_object_viewer_enabled": enabled}), 0)
        self.assertIn({"from": "${google_storage_bucket_iam_member.shared_bucket_public_object_viewer}",
                       "to": "${google_storage_bucket_iam_member.shared_bucket_public_object_viewer[0]}"}, document["moved"])
        outputs_tf = (REPO_ROOT / "terraform/envs/prod/outputs.tf").read_text()
        bucket = declared["google_storage_bucket", "shared_bucket"]
        self.assertIn("local.pmtiles_browser_allowed_origins", bucket["cors"][0]["origin"])
        self.assertIn('shared_bucket_public_managed_folders', outputs_tf)

    def test_pmtiles_managed_folder_sync_role_is_narrow(self):
        shared_bucket_tf = (REPO_ROOT / "terraform/envs/prod/shared_bucket_public.tf").read_text()
        role_start = shared_bucket_tf.index(
            'resource "google_project_iam_custom_role" "pmtiles_managed_folder_sync"'
        )
        role_end = shared_bucket_tf.index(
            'resource "google_storage_bucket_iam_member" "github_actions_pmtiles_managed_folder_sync"'
        )
        role_block = shared_bucket_tf[role_start:role_end]

        self.assertIn('role_id     = "sharedDatasetsPmtilesManagedFolderSync"', role_block)
        for permission in (
            "storage.managedFolders.create",
            "storage.managedFolders.get",
            "storage.managedFolders.getIamPolicy",
            "storage.managedFolders.list",
            "storage.managedFolders.setIamPolicy",
        ):
            self.assertIn(permission, role_block)
        self.assertNotIn("storage.objects", role_block)
        self.assertNotIn("storage.managedFolders.delete", role_block)

        self.assertIn(
            'role   = google_project_iam_custom_role.pmtiles_managed_folder_sync.name',
            shared_bucket_tf,
        )
        self.assertIn(
            'member = "serviceAccount:${var.github_actions_terraform_service_account_email}"',
            shared_bucket_tf,
        )
        self.assertGreaterEqual(
            shared_bucket_tf.count(
                "google_storage_bucket_iam_member.github_actions_pmtiles_managed_folder_sync"
            ),
            2,
        )

    def test_current_catalog_has_private_roots_outside_public_folder_set(self):
        with (REPO_ROOT / "catalog/shared-datasets-catalog.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        folders = set(folder_policy(rows, "skytruth-shared-datasets-1")["public"])
        self.assertEqual(folders, public_catalog_folder_names())

        self.assertIn("_catalog/", folders)
        self.assertIn(
            "100-geographic-reference/130-protected-areas/wdpa-marine/",
            folders,
        )
        self.assertIn(
            "500-conservation-ecosystems/530-habitat-condition/global-coral-reefs/",
            folders,
        )
        self.assertNotIn(
            "500-conservation-ecosystems/530-habitat-condition/iucn-mammal-ranges/",
            folders,
        )
        self.assertNotIn(
            "500-conservation-ecosystems/530-habitat-condition/iucn-reptile-ranges/",
            folders,
        )

    def test_public_folder_filter_excludes_private_internal_and_foreign_bucket_rows(self):
        rows = [
            {"access_tier": tier, "canonical_path": f"gs://{bucket}/category/{slug}/latest/{slug}.fgb"}
            for tier, bucket, slug in [
                ("public", "example-bucket", "public"),
                ("PUBLIC", "example-bucket", "public-uppercase"),
                ("private", "example-bucket", "private"),
                ("internal", "example-bucket", "internal"),
                ("public", "foreign-bucket", "foreign"),
            ]
        ]
        cases = [
            ("empty", [], {"_catalog/"}, {"_catalog/"}),
            ("private-only", [rows[2]], {"_catalog/", "category/private/"}, {"_catalog/"}),
            ("mixed", rows,
             {"_catalog/", "category/public/", "category/public-uppercase/", "category/private/", "category/internal/"},
             {"_catalog/", "category/public/", "category/public-uppercase/"}),
        ]
        for label, catalog_rows, expected_all, expected_public in cases:
            with self.subTest(catalog=label):
                actual = folder_policy(catalog_rows, "example-bucket")
                self.assertEqual(set(actual["all"]), expected_all)
                self.assertEqual(set(actual["public"]), expected_public)


if __name__ == "__main__":
    unittest.main()
