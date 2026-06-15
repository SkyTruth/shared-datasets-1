from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import feature_metadata_translation_pipeline


class FeatureMetadataTranslationPipelineTests(unittest.TestCase):
    def test_publish_plan_translation_sources_are_detected_from_destinations(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "publish-plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "asset_slug": "example-asset",
                        "proposal_id": "pr-123",
                        "promotions": [
                            {
                                "source_uri": "gs://skytruth-shared-datasets-1/_scratch/pending-publishes/example-asset/pr-123/example-asset.metadata-translations.csv",
                                "source_generation": "111",
                                "destination_uri": "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.metadata-translations.csv",
                                "destination_generation": "222",
                                "content_type": "text/csv",
                                "cache_control": "",
                            },
                            {
                                "source_uri": "gs://skytruth-shared-datasets-1/_scratch/pending-publishes/example-asset/pr-123/example-asset.metadata.ndjson.gz",
                                "source_generation": "333",
                                "destination_uri": "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.metadata.ndjson.gz",
                                "destination_generation": "444",
                                "content_type": "application/x-ndjson",
                                "cache_control": "",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            uris = feature_metadata_translation_pipeline.translation_source_uris_from_publish_plan(
                plan,
                bucket="skytruth-shared-datasets-1",
            )

        self.assertEqual(
            uris,
            [
                "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.metadata-translations.csv"
            ],
        )

    def test_publish_plan_detects_release_and_latest_translation_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "publish-plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "asset_slug": "example-asset",
                        "proposal_id": "pr-123",
                        "promotions": [
                            {
                                "source_uri": "gs://skytruth-shared-datasets-1/_scratch/pending-publishes/example-asset/pr-123/example-asset.metadata-translations.csv",
                                "source_generation": "111",
                                "destination_uri": "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.metadata-translations.csv",
                                "destination_generation": "222",
                                "content_type": "text/csv",
                                "cache_control": "",
                            },
                            {
                                "source_uri": "gs://skytruth-shared-datasets-1/_scratch/pending-publishes/example-asset/pr-123/example-asset.metadata-translations.csv",
                                "source_generation": "111",
                                "destination_uri": "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.metadata-translations.csv",
                                "destination_generation": "333",
                                "content_type": "text/csv",
                                "cache_control": "no-cache, max-age=0, must-revalidate",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            uris = feature_metadata_translation_pipeline.translation_source_uris_from_publish_plan(
                plan,
                bucket="skytruth-shared-datasets-1",
            )

        self.assertEqual(
            uris,
            [
                "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.metadata-translations.csv",
                "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.metadata-translations.csv",
            ],
        )

    def test_non_translation_publish_plan_is_noop_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "publish-plan.json"
            report = root / "summary.json"
            plan.write_text(
                json.dumps(
                    {
                        "asset_slug": "example-asset",
                        "proposal_id": "pr-123",
                        "promotions": [
                            {
                                "source_uri": "gs://skytruth-shared-datasets-1/_scratch/pending-publishes/example-asset/pr-123/example-asset.fgb",
                                "source_generation": "111",
                                "destination_uri": "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb",
                                "destination_generation": "222",
                                "content_type": "application/octet-stream",
                                "cache_control": "",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code = feature_metadata_translation_pipeline.main(
                [
                    "--publish-plan",
                    str(plan),
                    "--work-dir",
                    str(root / "work"),
                    "--report",
                    str(report),
                ]
            )

            payload = json.loads(report.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(payload["translation_source_count"], 0)
        self.assertEqual(payload["translation_sources"], [])

    def test_sibling_and_localized_uris_follow_release_metadata_naming(self):
        translation_uri = (
            "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/"
            "example-asset/releases/2026-05-01/example-asset.metadata-translations.csv"
        )
        canonical_uri = feature_metadata_translation_pipeline.sibling_uri(
            translation_uri,
            feature_metadata_translation_pipeline.CANONICAL_METADATA_SUFFIX,
        )

        self.assertEqual(
            canonical_uri,
            "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.metadata.ndjson.gz",
        )
        self.assertEqual(
            feature_metadata_translation_pipeline.localized_destination_uri(canonical_uri, "pt-BR"),
            "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.metadata.pt_br.ndjson.gz",
        )
        self.assertEqual(feature_metadata_translation_pipeline.release_from_uri(translation_uri), "2026-05-01")

    def test_materialize_upload_rebuilds_release_index(self):
        class Report:
            locale = "es"
            output_sidecar = "/tmp/example-asset.metadata.es.ndjson.gz"

            def to_dict(self):
                return {"locale": self.locale}

        translation_uri = (
            "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/"
            "example-asset/releases/2026-05-01/example-asset.metadata-translations.csv"
        )

        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(
                    feature_metadata_translation_pipeline,
                    "download_object",
                    return_value={"generation": "1"},
                ),
                mock.patch.object(
                    feature_metadata_translation_pipeline.feature_metadata_localization,
                    "resolved_translatable_fields",
                    return_value={"name"},
                ),
                mock.patch.object(
                    feature_metadata_translation_pipeline.feature_metadata_localization,
                    "materialize_locale_sidecars",
                    return_value=[Report()],
                ),
                mock.patch.object(
                    feature_metadata_translation_pipeline,
                    "upload_object_with_current_generation",
                    return_value={"uri": "localized", "generation": "2"},
                ),
                mock.patch.object(
                    feature_metadata_translation_pipeline,
                    "rebuild_release_index_for_asset",
                    return_value={"path": "release-index", "generation": 3},
                ) as rebuild,
            ):
                result = feature_metadata_translation_pipeline.materialize_translation_source(
                    translation_source_uri=translation_uri,
                    work_dir=Path(tmp),
                    asset_slug=None,
                    release=None,
                    upload=True,
                    fail_on_stale=False,
                )

        rebuild.assert_called_once_with("example-asset")
        self.assertEqual(result["release_index"], {"path": "release-index", "generation": 3})
        self.assertEqual(result["uploads"], [{"uri": "localized", "generation": "2"}])

    def test_materialize_skips_sidecars_already_promoted_by_plan(self):
        class Report:
            locale = "es"
            output_sidecar = "/tmp/example-asset.metadata.es.ndjson.gz"

            def to_dict(self):
                return {"locale": self.locale}

        translation_uri = (
            "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/"
            "example-asset/releases/2026-05-01/example-asset.metadata-translations.csv"
        )
        localized_uri = (
            "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/"
            "example-asset/releases/2026-05-01/example-asset.metadata.es.ndjson.gz"
        )

        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(
                    feature_metadata_translation_pipeline,
                    "download_object",
                    return_value={"generation": "1"},
                ),
                mock.patch.object(
                    feature_metadata_translation_pipeline.feature_metadata_localization,
                    "resolved_translatable_fields",
                    return_value={"name"},
                ),
                mock.patch.object(
                    feature_metadata_translation_pipeline.feature_metadata_localization,
                    "materialize_locale_sidecars",
                    return_value=[Report()],
                ),
                mock.patch.object(
                    feature_metadata_translation_pipeline,
                    "upload_object_with_current_generation",
                ) as upload,
                mock.patch.object(
                    feature_metadata_translation_pipeline,
                    "rebuild_release_index_for_asset",
                ) as rebuild,
            ):
                result = feature_metadata_translation_pipeline.materialize_translation_source(
                    translation_source_uri=translation_uri,
                    work_dir=Path(tmp),
                    asset_slug=None,
                    release=None,
                    upload=True,
                    fail_on_stale=False,
                    skip_upload_uris={localized_uri},
                )

        upload.assert_not_called()
        rebuild.assert_not_called()
        self.assertEqual(
            result["uploads"],
            [
                {
                    "uri": localized_uri,
                    "skipped": True,
                    "reason": "already_promoted_in_publish_plan",
                }
            ],
        )
        self.assertIsNone(result["release_index"])


if __name__ == "__main__":
    unittest.main()
