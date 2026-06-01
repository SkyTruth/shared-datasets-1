from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ingestion.common import feature_metadata
from scripts import release_feature_model as model

VALID_HASH = "sha256:" + "a" * 64
VALID_HEX_HASH = "a" * 64


class ReleaseFeatureModelTests(unittest.TestCase):
    def test_provider_id_and_feature_hash_are_separate(self):
        feature_id = model.provider_feature_id(source_field="SITE_PID", source_value=" WDPA 123 ")
        feature_hash = model.content_feature_hash(
            geometry={"type": "Point", "coordinates": [1, 2]},
            properties={"SITE_PID": "WDPA 123", "name": "Example"},
        )

        self.assertEqual(feature_id, "src:SITE_PID:WDPA-123")
        self.assertTrue(feature_hash.startswith("sha256:"))
        self.assertNotEqual(feature_id, feature_hash)

    def test_common_provider_id_rejects_null_and_blank_values(self):
        for value in (None, "", " \t"):
            with self.assertRaisesRegex(RuntimeError, "feature ID token"):
                feature_metadata.provider_feature_id("SITE_PID", value)

    def test_common_provider_id_rejects_oversized_final_feature_id(self):
        with self.assertRaisesRegex(RuntimeError, "feature_id must be 1-256 chars"):
            feature_metadata.provider_feature_id("F" * 256, "V" * 256)

    def test_common_final_manifest_records_generations_without_manifest_self_generation(self):
        asset_slug = "example"
        release = "2026-05-01"
        asset_root = "100/reference/example"
        release_base = f"gs://bucket/{asset_root}/releases/{release}/{asset_slug}"
        latest_base = f"gs://bucket/{asset_root}/latest/{asset_slug}"
        release_info = {
            "fgb": {"path": f"{release_base}.fgb", "generation": 10, "size": 3},
            "pmtiles": {"path": f"{release_base}.pmtiles", "generation": 11, "size": 4},
            "metadata": {"path": f"{release_base}.metadata.ndjson.gz", "generation": 12, "size": 5},
            "schema": {"path": f"{release_base}.schema.json", "generation": 13, "size": 6},
        }
        latest_info = {
            "fgb": {"path": f"{latest_base}.fgb", "generation": 20},
            "pmtiles": {"path": f"{latest_base}.pmtiles", "generation": 21},
            "metadata": {"path": f"{latest_base}.metadata.ndjson.gz", "generation": 22},
            "schema": {"path": f"{latest_base}.schema.json", "generation": 23},
        }

        manifest = feature_metadata.final_manifest_payload(
            asset_slug=asset_slug,
            release=release,
            bucket_name="bucket",
            asset_root=asset_root,
            sha256_by_role={
                "fgb": VALID_HEX_HASH,
                "pmtiles": "b" * 64,
                "metadata": "c" * 64,
                "schema": "d" * 64,
            },
            schema={
                "schema_version": 1,
                "asset_slug": asset_slug,
                "release": release,
                "fields": [],
            },
            source_inputs=[{"uri": "https://example.test/source"}],
            id_strategy={"strategy": "provider", "field": "SITE_PID"},
            feature_count=1,
            release_blob_info_by_role=release_info,
            latest_blob_info_by_role=latest_info,
            manifest_release_path=f"{release_base}.manifest.json",
            manifest_latest_path=f"{latest_base}.manifest.json",
        )

        artifacts = {artifact["role"]: artifact for artifact in manifest["artifacts"]}
        for role in ("fgb", "pmtiles", "metadata", "schema"):
            self.assertEqual(artifacts[role]["generation"], release_info[role]["generation"])
            self.assertEqual(artifacts[role]["latest_generation"], latest_info[role]["generation"])
        self.assertNotIn("generation", artifacts["manifest"])
        self.assertNotIn("latest_generation", artifacts["manifest"])

    def test_generated_feature_id_requires_curated_preimage(self):
        with self.assertRaisesRegex(model.ReleaseFeatureModelError, "preimage"):
            model.generated_feature_id(asset_slug="example", preimage={})

        feature_id = model.generated_feature_id(
            asset_slug="example",
            preimage={"source_fields": {"name": "A"}, "geometry_digest": "abc"},
        )

        self.assertTrue(feature_id.startswith("gen:"))

    def test_common_generated_ids_are_geometry_stable_across_releases(self):
        feature = {
            "type": "Feature",
            "properties": {"DN": 3},
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
        }

        first, _first_sidecar = feature_metadata.enrich_features_with_generated_ids(
            [feature],
            asset_slug="ims-sea-ice-extent",
            release="2026-05-01",
            provenance={},
        )
        second, _second_sidecar = feature_metadata.enrich_features_with_generated_ids(
            [feature],
            asset_slug="ims-sea-ice-extent",
            release="2026-05-02",
            provenance={},
        )

        self.assertEqual(first[0]["properties"]["feature_id"], second[0]["properties"]["feature_id"])

    def test_common_generated_ids_reject_duplicate_geometry(self):
        feature = {
            "type": "Feature",
            "properties": {"DN": 3},
            "geometry": {"type": "Point", "coordinates": [0, 0]},
        }

        with self.assertRaisesRegex(RuntimeError, "duplicate generated feature_id"):
            feature_metadata.enrich_features_with_generated_ids(
                [feature, feature],
                asset_slug="ims-sea-ice-extent",
                release="2026-05-01",
                provenance={},
            )

    def test_sidecar_validation_rejects_duplicates_and_oversized_records(self):
        feature = model.FeatureRecord(
            feature_id="src:id:1",
            feature_hash=VALID_HASH,
            geometry={"type": "Point", "coordinates": [0, 0]},
            properties={"name": "A"},
            provenance={"source": "fixture"},
        )
        record = model.sidecar_record(asset_slug="example", release="2026-05-01", feature=feature)

        result = model.validate_sidecar_records([record, record], max_record_bytes=100)

        self.assertFalse(result.valid)
        self.assertEqual(result.duplicate_feature_ids, ("src:id:1",))
        self.assertIn("duplicate feature_id", " ".join(result.errors))

    def test_sidecar_round_trip_uses_gzip_ndjson(self):
        feature = model.FeatureRecord(
            feature_id="src:id:1",
            feature_hash=VALID_HASH,
            geometry=None,
            properties={"name": "A"},
            provenance={"source": "fixture"},
        )
        record = model.sidecar_record(asset_slug="example", release="2026-05-01", feature=feature)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "example.metadata.ndjson.gz"
            model.write_metadata_sidecar([record], path)
            rows = list(model.read_metadata_sidecar(path))

        self.assertEqual(rows[0]["feature_id"], "src:id:1")
        self.assertEqual(rows[0]["properties"], {"name": "A"})

    def test_sidecar_writers_are_deterministic(self):
        feature = model.FeatureRecord(
            feature_id="src:id:1",
            feature_hash=VALID_HASH,
            geometry=None,
            properties={"name": "A"},
            provenance={"source": "fixture"},
        )
        record = model.sidecar_record(asset_slug="example", release="2026-05-01", feature=feature)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            first = tmp_path / "first.metadata.ndjson.gz"
            second = tmp_path / "second.metadata.ndjson.gz"
            common_first = tmp_path / "common-first.metadata.ndjson.gz"
            common_second = tmp_path / "common-second.metadata.ndjson.gz"

            model.write_metadata_sidecar([record], first)
            model.write_metadata_sidecar([record], second)
            feature_metadata.write_sidecar([record], common_first)
            feature_metadata.write_sidecar([record], common_second)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(common_first.read_bytes(), common_second.read_bytes())

    def test_sidecar_validation_rejects_wrong_asset_release_and_bad_hash(self):
        record = {
            "schema_version": model.METADATA_SIDECAR_SCHEMA_VERSION,
            "asset_slug": "wrong-asset",
            "release": "2026-05-02",
            "feature_id": "src:id:1",
            "feature_hash": "sha256:abc",
            "properties": {"name": "A"},
            "provenance": {},
        }

        result = model.validate_sidecar_records(
            [record],
            expected_asset_slug="example",
            expected_release="2026-05-01",
        )

        self.assertFalse(result.valid)
        self.assertIn("invalid feature_hash", " ".join(result.errors))
        self.assertIn("asset_slug does not match", " ".join(result.errors))
        self.assertIn("release does not match", " ".join(result.errors))

    def test_release_schema_validates_projectable_allowlist(self):
        schema = model.build_release_schema(
            asset_slug="example",
            release="2026-05-01",
            fields=[
                model.ReleaseSchemaField("name", "String"),
                {"name": "internal", "type": "String", "projectable": False},
            ],
        )

        fields = model.validate_release_schema(schema, expected_asset_slug="example", expected_release="2026-05-01")

        self.assertEqual(tuple(fields), ("name",))

    def test_release_manifest_points_index_status_to_index_loads(self):
        manifest = model.build_release_manifest(
            asset_slug="example",
            release="2026-05-01",
            source_inputs=[{"uri": "gs://bucket/source"}],
            artifacts=[{"role": "metadata", "path": "gs://bucket/example.metadata.ndjson.gz"}],
            schema={"fields": []},
            id_strategy={"strategy": "provider", "field": "id"},
            validation={"valid": True},
        )

        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["index_load_status"], "tracked in index-loads/")
        self.assertEqual(manifest["index_status_policy"]["mode"], "external_index_load_records")
        json.dumps(manifest)

    def test_artifact_and_index_load_names(self):
        self.assertEqual(model.release_artifact_name("example", "metadata"), "example.metadata.ndjson.gz")
        self.assertEqual(
            model.index_load_record_name("100/ref/example", "2026-05-01", "load 1"),
            "100/ref/example/index-loads/2026-05-01/load-1.json",
        )


if __name__ == "__main__":
    unittest.main()
