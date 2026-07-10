from __future__ import annotations

import csv
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


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
        shared_bucket_tf = (REPO_ROOT / "terraform/envs/prod/shared_bucket_public.tf").read_text()
        variables_tf = (REPO_ROOT / "terraform/envs/prod/variables.tf").read_text()
        outputs_tf = (REPO_ROOT / "terraform/envs/prod/outputs.tf").read_text()

        self.assertIn('resource "google_storage_managed_folder" "shared_bucket_public_prefixes"', shared_bucket_tf)
        self.assertIn('resource "google_storage_managed_folder_iam_member" "shared_bucket_public_object_viewers"', shared_bucket_tf)
        self.assertIn("shared_bucket_managed_folder_names", shared_bucket_tf)
        self.assertIn('["_catalog/"]', shared_bucket_tf)
        self.assertIn('lower(row.access_tier) == "public"', shared_bucket_tf)
        self.assertIn('startswith(row.canonical_path, "gs://${var.bucket_name}/")', shared_bucket_tf)
        self.assertIn("if contains(local.shared_bucket_public_managed_folder_names, name)", shared_bucket_tf)
        self.assertIn('roles/storage.objectViewer', shared_bucket_tf)
        self.assertIn('member         = "allUsers"', shared_bucket_tf)
        self.assertIn('variable "shared_bucket_public_object_viewer_enabled"', variables_tf)
        self.assertIn('count = var.shared_bucket_public_object_viewer_enabled ? 1 : 0', shared_bucket_tf)
        self.assertIn('to   = google_storage_bucket_iam_member.shared_bucket_public_object_viewer[0]', shared_bucket_tf)
        self.assertIn("origin          = local.pmtiles_browser_allowed_origins", shared_bucket_tf)
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
        folders = public_catalog_folder_names()

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


if __name__ == "__main__":
    unittest.main()
