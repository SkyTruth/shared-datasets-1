from __future__ import annotations

import argparse
from pathlib import Path
import unittest
from unittest import mock

from ingestion.common.identity_reset import ASSET_ROOTS
from ingestion.common.publication import PublicationError
from scripts import gcs_asset, publish_workflow
from test_publication import publication_temp_directory


class PublicationBypassTests(unittest.TestCase):
    def test_index_report_upload_preserves_existing_status_with_storage_precondition(self):
        root = ASSET_ROOTS["wdpa-marine"]
        uri = f"gs://bucket/{root}/index-loads/2026-10-01/github-123-1.json"
        with publication_temp_directory() as temporary:
            source = Path(temporary) / "report.json"
            source.write_text("{}\n")
            blob = mock.Mock(generation=20, size=3, content_type="application/json", cache_control="no-cache", metadata={})
            with (
                mock.patch.dict("os.environ", {gcs_asset.ALLOW_CANONICAL_MUTATION_ENV: "1"}),
                mock.patch.object(gcs_asset, "get_blob", return_value=blob) as get_blob,
                mock.patch.object(gcs_asset, "print"),
            ):
                gcs_asset.upload(source, uri, replace_generation=None, unsafe_overwrite=False,
                                 content_type="application/json", cache_control="no-cache", metadata_json=None)
                blob.upload_from_filename.assert_called_once_with(source, content_type="application/json", if_generation_match=0)
                get_blob.reset_mock()
                with self.assertRaises(PublicationError):
                    gcs_asset.upload(source, uri, replace_generation=20, unsafe_overwrite=False,
                                     content_type="application/json", cache_control="no-cache", metadata_json=None)
                get_blob.assert_not_called()

    def test_independent_index_load_reports_allow_only_no_clobber_uploads(self):
        with mock.patch.dict("os.environ", {gcs_asset.ALLOW_CANONICAL_MUTATION_ENV: "1"}):
            for slug, root in ASSET_ROOTS.items():
                uri = f"gs://bucket/{root}/index-loads/2026-10-01/github-123-1.json"
                gcs_asset.require_mutation_allowed(uri, operation="upload", expected_generation=0)
                for operation, generation, unsafe in (("upload", 12, False), ("upload", None, False),
                                                      ("upload", 0, True), ("delete", 0, False), ("copy", 0, False)):
                    with self.subTest(slug=slug, operation=operation, generation=generation, unsafe=unsafe), self.assertRaises(PublicationError):
                        gcs_asset.require_mutation_allowed(uri, operation=operation, expected_generation=generation, unsafe_overwrite=unsafe)
                for tail in ("2026-99-99/report.json", "2026-10-01/report.fgb", "2026-10-01/nested/report.json", "../state.json"):
                    with self.subTest(tail=tail), self.assertRaises(PublicationError):
                        gcs_asset.require_mutation_allowed(f"gs://bucket/{root}/index-loads/{tail}", operation="upload", expected_generation=0)

    def test_generic_permission_flag_cannot_bypass_managed_roots_or_catalog_state(self):
        with mock.patch.dict("os.environ", {gcs_asset.ALLOW_CANONICAL_MUTATION_ENV: "1"}):
            for slug, root in ASSET_ROOTS.items():
                targets = [f"{root}/latest/{slug}.fgb", f"{root}/releases/2026-10-01/{slug}.manifest.json",
                           f"{root}/publications/state.json", f"_catalog/releases/{slug}.json", f"_catalog/schema-snapshots/{slug}.json"]
                for name in targets:
                    for operation in ("upload", "copy", "delete", "release-index rebuild"):
                        with self.subTest(name=name, operation=operation), self.assertRaisesRegex(PublicationError, "requires owned publication"):
                            gcs_asset.require_mutation_allowed(f"gs://bucket/{name}", operation=operation, expected_generation=0)
            gcs_asset.require_mutation_allowed("gs://bucket/_scratch/pending-publishes/reset/evidence.json", operation="upload")
            gcs_asset.require_mutation_allowed("gs://bucket/300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/latest/data.fgb", operation="upload")

    def test_mixed_publish_or_delete_plan_refuses_before_touching_first_allowed_object(self):
        managed = f"gs://bucket/{ASSET_ROOTS['wdpa-marine']}/latest/wdpa-marine.fgb"
        allowed = "gs://bucket/_catalog/web/catalog.json"
        cases = [
            (publish_workflow.command_promote, {"asset_slug": "wdpa-marine", "promotions": [{"destination_uri": allowed}, {"destination_uri": managed}]}),
            (publish_workflow.command_delete_canonical_objects, {"deletions": [{"uri": allowed}, {"uri": managed}]}),
        ]
        for command, plan in cases:
            with (
                self.subTest(command=command.__name__),
                mock.patch.dict("os.environ", {"SHARED_DATASETS_BUCKET": "bucket"}),
                mock.patch.object(publish_workflow, "load_plan", return_value=plan),
                mock.patch.object(publish_workflow, "catalog_row", return_value={}),
                mock.patch.object(publish_workflow.subprocess, "run") as process,
                mock.patch.object(gcs_asset, "get_client") as client,
                self.assertRaisesRegex(PublicationError, "requires owned publication"),
            ):
                try:
                    command(argparse.Namespace(plan_json="fixture.json"))
                finally:
                    process.assert_not_called()
                    client.assert_not_called()
