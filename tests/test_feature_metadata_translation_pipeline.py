from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
