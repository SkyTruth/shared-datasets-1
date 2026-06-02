from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from scripts import feature_metadata_index


def write_sidecar(path: Path, records: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


class FakeWriter:
    def __init__(self) -> None:
        self.batches = []

    def write_batch(self, asset_slug, release, documents):
        self.batches.append((asset_slug, release, list(documents)))
        return len(documents)


class FeatureMetadataIndexTests(unittest.TestCase):
    def test_load_sidecar_validates_and_batches_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            sidecar = Path(tmp) / "asset.metadata.ndjson.gz"
            write_sidecar(
                sidecar,
                [
                    {
                        "asset_slug": "wdpa-marine",
                        "release": "2026-06-01",
                        "feature_id": "src:id:1",
                        "feature_hash": "sha256:a",
                        "properties": {"name": "A"},
                    },
                    {
                        "asset_slug": "wdpa-marine",
                        "release": "2026-06-01",
                        "feature_id": "src:id:2",
                        "feature_hash": "sha256:b",
                        "properties": {"name": "B"},
                    },
                ],
            )
            writer = FakeWriter()

            result = feature_metadata_index.load_sidecar_to_index(
                sidecar_path=sidecar,
                asset_slug="wdpa-marine",
                release="2026-06-01",
                writer=writer,
                batch_size=1,
            )

        self.assertEqual(result.document_count, 2)
        self.assertEqual(result.batch_count, 2)
        self.assertEqual(len(writer.batches), 2)
        self.assertEqual(writer.batches[0][2][0]["feature_id"], "src:id:1")

    def test_duplicate_feature_id_blocks_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            sidecar = Path(tmp) / "asset.metadata.ndjson.gz"
            write_sidecar(
                sidecar,
                [
                    {"feature_id": "src:id:1", "feature_hash": "sha256:a", "properties": {}},
                    {"feature_id": "src:id:1", "feature_hash": "sha256:b", "properties": {}},
                ],
            )

            with self.assertRaisesRegex(feature_metadata_index.FeatureMetadataIndexError, "duplicate feature_id"):
                feature_metadata_index.load_sidecar_to_index(
                    sidecar_path=sidecar,
                    asset_slug="wdpa-marine",
                    release="2026-06-01",
                    writer=FakeWriter(),
                )


if __name__ == "__main__":
    unittest.main()
