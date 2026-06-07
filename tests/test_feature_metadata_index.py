from __future__ import annotations

import json
import tempfile
import unittest
import hashlib
from pathlib import Path

from scripts import feature_metadata_index
from scripts import release_feature_model

VALID_HASH = "sha256:" + "a" * 64


class FakeWriter:
    def __init__(self) -> None:
        self.batches = []

    def write_batch(self, *, asset_slug: str, release: str, load_id: str, documents):
        self.batches.append((asset_slug, release, load_id, list(documents)))
        return len(documents)


def write_sidecar(path: Path, count: int = 3) -> None:
    records = []
    for index in range(count):
        feature = release_feature_model.FeatureRecord(
            feature_id=str(index + 1),
            geometry_hash="sha256:" + f"{index + 1:064x}",
            properties_hash="sha256:" + f"{index:064x}",
            geometry=None,
            properties={"name": f"Feature {index}"},
            provenance={"source": "fixture"},
        )
        records.append(
            release_feature_model.sidecar_record(
                asset_slug="example-asset",
                release="2026-05-01",
                feature=feature,
            )
        )
    release_feature_model.write_metadata_sidecar(records, path)


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_bundle(tmp_path: Path, *, count: int = 3):
    sidecar = tmp_path / "example-asset.metadata.ndjson.gz"
    schema = tmp_path / "example-asset.schema.json"
    manifest = tmp_path / "example-asset.manifest.json"
    write_sidecar(sidecar, count=count)
    schema_payload = release_feature_model.build_release_schema(
        asset_slug="example-asset",
        release="2026-05-01",
        fields=[
            release_feature_model.ReleaseSchemaField("feature_id", "String"),
            release_feature_model.ReleaseSchemaField("name", "String"),
        ],
    )
    schema.write_text(json.dumps(schema_payload, sort_keys=True) + "\n")
    artifacts = [
        {
            "role": "fgb",
            "path": "gs://bucket/root/releases/2026-05-01/example-asset.fgb",
            "generation": 10,
            "sha256": "0" * 64,
        },
        {
            "role": "pmtiles",
            "path": "gs://bucket/root/releases/2026-05-01/example-asset.pmtiles",
            "generation": 11,
            "sha256": "1" * 64,
        },
        {
            "role": "metadata",
            "path": "gs://bucket/root/releases/2026-05-01/example-asset.metadata.ndjson.gz",
            "generation": 12,
            "sha256": file_sha(sidecar),
        },
        {
            "role": "schema",
            "path": "gs://bucket/root/releases/2026-05-01/example-asset.schema.json",
            "generation": 13,
            "sha256": file_sha(schema),
        },
        {
            "role": "manifest",
            "path": "gs://bucket/root/releases/2026-05-01/example-asset.manifest.json",
        },
    ]
    manifest_payload = release_feature_model.build_release_manifest(
        asset_slug="example-asset",
        release="2026-05-01",
        source_inputs=[],
        artifacts=artifacts,
        schema=schema_payload,
        identity=release_feature_model.build_identity_metadata(strategy="source_field", source_fields=["id"]),
        validation={"valid": True, "feature_count": count},
    )
    manifest.write_text(json.dumps(manifest_payload, sort_keys=True) + "\n")
    return sidecar, schema, manifest


class FeatureMetadataIndexTests(unittest.TestCase):
    def test_load_sidecar_batches_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            sidecar, schema, manifest = write_bundle(Path(tmp), count=3)
            writer = FakeWriter()

            result = feature_metadata_index.load_sidecar_to_index(
                sidecar_path=sidecar,
                schema_path=schema,
                manifest_path=manifest,
                asset_slug="example-asset",
                release="2026-05-01",
                writer=writer,
                batch_size=2,
                load_id="load-1",
                sidecar_uri="gs://bucket/root/releases/2026-05-01/example-asset.metadata.ndjson.gz",
                sidecar_generation=12,
                schema_uri="gs://bucket/root/releases/2026-05-01/example-asset.schema.json",
                schema_generation=13,
                manifest_uri="gs://bucket/root/releases/2026-05-01/example-asset.manifest.json",
            )

        self.assertEqual(result.document_count, 3)
        self.assertEqual(result.batch_count, 2)
        self.assertEqual(result.deleted_document_count, 0)
        self.assertEqual(len(writer.batches), 2)
        self.assertEqual(writer.batches[0][2], "load-1")
        self.assertEqual(writer.batches[0][3][0]["feature_id"], "1")

    def test_non_dry_run_requires_load_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            sidecar, schema, manifest = write_bundle(Path(tmp), count=1)

            with self.assertRaisesRegex(feature_metadata_index.FeatureMetadataIndexError, "load_id"):
                feature_metadata_index.load_sidecar_to_index(
                    sidecar_path=sidecar,
                    schema_path=schema,
                    manifest_path=manifest,
                    asset_slug="example-asset",
                    release="2026-05-01",
                    writer=FakeWriter(),
                )

    def test_dry_run_does_not_require_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            sidecar, schema, manifest = write_bundle(Path(tmp), count=1)

            result = feature_metadata_index.load_sidecar_to_index(
                sidecar_path=sidecar,
                schema_path=schema,
                manifest_path=manifest,
                asset_slug="example-asset",
                release="2026-05-01",
                writer=None,
                dry_run=True,
            )

        self.assertTrue(result.dry_run)
        self.assertEqual(result.document_count, 1)

    def test_index_load_record_is_json_serializable(self):
        result = feature_metadata_index.LoadResult(
            asset_slug="example-asset",
            release="2026-05-01",
            sidecar_path="/tmp/example.metadata.ndjson.gz",
            schema_path="/tmp/example.schema.json",
            manifest_path="/tmp/example.manifest.json",
            document_count=3,
            batch_count=1,
            dry_run=False,
        )

        record = feature_metadata_index.build_index_load_record(
            result,
            load_id="load-1",
            sidecar_uri="gs://bucket/path/example.metadata.ndjson.gz",
            sidecar_generation=123,
            schema_uri="gs://bucket/path/example.schema.json",
            schema_generation=124,
            manifest_uri="gs://bucket/path/example.manifest.json",
            manifest_generation=125,
            started_at="2026-05-01T00:00:00+00:00",
            completed_at="2026-05-01T00:00:01+00:00",
        )

        self.assertEqual(record["status"], "success")
        self.assertEqual(record["sidecar_generation"], 123)
        self.assertEqual(record["schema_generation"], 124)
        self.assertEqual(record["manifest_generation"], 125)
        json.dumps(record)

    def test_sidecar_asset_release_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            sidecar, schema, manifest = write_bundle(Path(tmp), count=1)

            with self.assertRaisesRegex(feature_metadata_index.FeatureMetadataIndexError, "asset_slug"):
                feature_metadata_index.load_sidecar_to_index(
                    sidecar_path=sidecar,
                    schema_path=schema,
                    manifest_path=manifest,
                    asset_slug="other-asset",
                    release="2026-05-01",
                    writer=None,
                    dry_run=True,
                )


if __name__ == "__main__":
    unittest.main()
