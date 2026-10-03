from __future__ import annotations

import argparse
import unittest
from unittest import mock

from ingestion.common.identity_reset import ASSET_ROOTS
from ingestion.common.publication import PublicationError
from scripts import gcs_asset, publish_workflow


class PublicationBypassTests(unittest.TestCase):


    def test_retired_index_reports_cannot_bypass_owned_publication(self):
        with mock.patch.dict("os.environ", {gcs_asset.ALLOW_CANONICAL_MUTATION_ENV: "1"}):
            for root in ASSET_ROOTS.values():
                uri = f"gs://bucket/{root}/index-loads/2026-10-01/github-123-1.json"
                for operation in ("upload", "copy", "delete"):
                    with self.subTest(uri=uri, operation=operation), self.assertRaises(PublicationError):
                        gcs_asset.require_mutation_allowed(uri, operation=operation)

    def test_generic_permission_flag_cannot_bypass_managed_roots_or_catalog_state(self):
        with mock.patch.dict("os.environ", {gcs_asset.ALLOW_CANONICAL_MUTATION_ENV: "1"}):
            for slug, root in ASSET_ROOTS.items():
                targets = [f"{root}/latest/{slug}.fgb", f"{root}/releases/2026-10-01/{slug}.manifest.json",
                           f"{root}/publications/state.json", f"_catalog/releases/{slug}.json", f"_catalog/schema-snapshots/{slug}.json"]
                for name in targets:
                    for operation in ("upload", "copy", "delete", "release-index rebuild"):
                        with self.subTest(name=name, operation=operation), self.assertRaisesRegex(PublicationError, "requires owned publication"):
                            gcs_asset.require_mutation_allowed(f"gs://bucket/{name}", operation=operation)
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
                self.assertRaisesRegex(PublicationError, "requires owned publication|does not support owned translation updates"),
            ):
                try:
                    command(argparse.Namespace(plan_json="fixture.json"))
                finally:
                    process.assert_not_called()
                    client.assert_not_called()
