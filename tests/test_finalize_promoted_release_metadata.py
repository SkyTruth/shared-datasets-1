from __future__ import annotations

import unittest

from scripts import finalize_promoted_release_metadata as finalizer
from scripts import release_feature_model


ASSET = "example-asset"
RELEASE = "2026-06-05"
ROOT = f"gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/{ASSET}"


def uri(suffix: str) -> str:
    return f"{ROOT}/releases/{RELEASE}/{ASSET}{suffix}"


def manifest_payload() -> dict:
    schema = release_feature_model.build_release_schema(
        asset_slug=ASSET,
        release=RELEASE,
        fields=[release_feature_model.ReleaseSchemaField("name", "String")],
    )
    return release_feature_model.build_release_manifest(
        asset_slug=ASSET,
        release=RELEASE,
        source_inputs=[{"uri": "https://example.test/source"}],
        schema=schema,
        id_strategy={"strategy": "provider", "field": "OBJECTID"},
        validation={"valid": True, "feature_count": 2},
        artifacts=[
            {"role": "fgb", "format": "fgb", "path": uri(".fgb"), "sha256": "a" * 64},
            {"role": "pmtiles", "format": "pmtiles", "path": uri(".pmtiles"), "sha256": "b" * 64},
            {
                "role": "metadata",
                "format": "metadata",
                "path": uri(".metadata.ndjson.gz"),
                "sha256": "c" * 64,
            },
            {"role": "schema", "format": "schema", "path": uri(".schema.json"), "sha256": "d" * 64},
            {"role": "manifest", "format": "manifest", "path": uri(".manifest.json")},
        ],
    )


class FinalizePromotedReleaseMetadataTests(unittest.TestCase):
    def test_finalized_manifest_records_destination_generations(self):
        stats = {
            uri(".fgb"): finalizer.BlobInfo(uri(".fgb"), generation=10, size=100, content_type="application/octet-stream"),
            uri(".pmtiles"): finalizer.BlobInfo(uri(".pmtiles"), generation=11, size=200, content_type="application/vnd.pmtiles"),
            uri(".metadata.ndjson.gz"): finalizer.BlobInfo(uri(".metadata.ndjson.gz"), generation=12, size=300),
            uri(".schema.json"): finalizer.BlobInfo(uri(".schema.json"), generation=13, size=400, content_type="application/json"),
            uri(".manifest.json"): finalizer.BlobInfo(uri(".manifest.json"), generation=14, size=500, content_type="application/json"),
            f"{ROOT}/latest/{ASSET}.fgb": finalizer.BlobInfo(f"{ROOT}/latest/{ASSET}.fgb", generation=20, size=100),
            f"{ROOT}/latest/{ASSET}.manifest.json": finalizer.BlobInfo(
                f"{ROOT}/latest/{ASSET}.manifest.json",
                generation=24,
                size=500,
            ),
        }

        payload = finalizer.finalized_manifest_payload(
            manifest_payload(),
            stat=lambda path: stats[path],
            maybe_stat=lambda path: stats.get(path),
        )

        artifacts = {artifact["role"]: artifact for artifact in payload["artifacts"]}
        self.assertEqual(artifacts["fgb"]["generation"], 10)
        self.assertEqual(artifacts["fgb"]["latest_generation"], 20)
        self.assertEqual(artifacts["pmtiles"]["generation"], 11)
        self.assertEqual(artifacts["metadata"]["generation"], 12)
        self.assertEqual(artifacts["schema"]["generation"], 13)
        self.assertNotIn("generation", artifacts["manifest"])
        self.assertNotIn("latest_generation", artifacts["manifest"])
        self.assertEqual(artifacts["manifest"]["latest_path"], f"{ROOT}/latest/{ASSET}.manifest.json")

    def test_finalized_run_record_updates_manifest_sha_and_path_metadata(self):
        release_manifest = finalizer.BlobInfo(
            uri(".manifest.json"),
            generation=30,
            size=600,
            content_type="application/json",
            sha256="f" * 64,
        )
        latest_manifest = finalizer.BlobInfo(
            f"{ROOT}/latest/{ASSET}.manifest.json",
            generation=31,
            size=600,
            content_type="application/json",
            sha256="f" * 64,
        )
        fgb = finalizer.BlobInfo(uri(".fgb"), generation=10, size=100)
        record = {
            "sha256": {"fgb": "a" * 64, "manifest": "0" * 64},
            "release_paths": [{"path": uri(".fgb")}, {"path": uri(".manifest.json")}],
            "latest_paths": [{"path": f"{ROOT}/latest/{ASSET}.manifest.json"}],
        }

        payload = finalizer.finalized_run_record_payload(
            record,
            stat=lambda _path: fgb,
            manifest_infos={
                release_manifest.path: release_manifest,
                latest_manifest.path: latest_manifest,
            },
        )

        release_entries = {entry["path"]: entry for entry in payload["release_paths"]}
        latest_entries = {entry["path"]: entry for entry in payload["latest_paths"]}
        self.assertEqual(payload["sha256"]["manifest"], "f" * 64)
        self.assertEqual(release_entries[uri(".manifest.json")]["generation"], 30)
        self.assertEqual(release_entries[uri(".manifest.json")]["sha256"], "f" * 64)
        self.assertEqual(latest_entries[f"{ROOT}/latest/{ASSET}.manifest.json"]["generation"], 31)

    def test_finalized_run_record_converts_string_paths_to_metadata_objects(self):
        release_manifest = finalizer.BlobInfo(
            uri(".manifest.json"),
            generation=30,
            size=600,
            content_type="application/json",
            sha256="f" * 64,
        )
        latest_manifest = finalizer.BlobInfo(
            f"{ROOT}/latest/{ASSET}.manifest.json",
            generation=31,
            size=600,
            content_type="application/json",
            sha256="f" * 64,
        )
        stats = {
            uri(".fgb"): finalizer.BlobInfo(
                uri(".fgb"),
                generation=10,
                size=100,
                content_type="application/octet-stream",
            ),
            f"{ROOT}/latest/{ASSET}.fgb": finalizer.BlobInfo(
                f"{ROOT}/latest/{ASSET}.fgb",
                generation=20,
                size=100,
                content_type="application/octet-stream",
            ),
        }
        record = {
            "sha256": {"fgb": "a" * 64, "manifest": "0" * 64},
            "release_paths": [uri(".fgb"), uri(".manifest.json")],
            "latest_paths": [f"{ROOT}/latest/{ASSET}.fgb", f"{ROOT}/latest/{ASSET}.manifest.json"],
        }

        payload = finalizer.finalized_run_record_payload(
            record,
            stat=lambda path: stats[path],
            manifest_infos={
                release_manifest.path: release_manifest,
                latest_manifest.path: latest_manifest,
            },
        )

        release_entries = {entry["path"]: entry for entry in payload["release_paths"]}
        latest_entries = {entry["path"]: entry for entry in payload["latest_paths"]}
        self.assertEqual(release_entries[uri(".fgb")]["generation"], 10)
        self.assertEqual(release_entries[uri(".manifest.json")]["generation"], 30)
        self.assertEqual(release_entries[uri(".manifest.json")]["sha256"], "f" * 64)
        self.assertEqual(latest_entries[f"{ROOT}/latest/{ASSET}.fgb"]["generation"], 20)
        self.assertEqual(latest_entries[f"{ROOT}/latest/{ASSET}.manifest.json"]["generation"], 31)

    def test_plan_helpers_find_manifest_and_run_record_destinations(self):
        plan = {
            "promotions": [
                {"destination_uri": uri(".fgb")},
                {"destination_uri": uri(".manifest.json")},
                {"destination_uri": f"{ROOT}/runs/{RELEASE}.json"},
            ]
        }

        self.assertEqual(finalizer.manifest_destination_uris(plan), [uri(".manifest.json")])
        self.assertEqual(finalizer.run_record_destination_uris(plan), [f"{ROOT}/runs/{RELEASE}.json"])


if __name__ == "__main__":
    unittest.main()
