from __future__ import annotations

import json
import unittest
from unittest import mock

from google.api_core.exceptions import PreconditionFailed

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
        identity=release_feature_model.build_identity_metadata(
            strategy="source_field",
            source_fields=["OBJECTID"],
        ),
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


class JsonStore:
    """Distinct blob handles model generation snapshots, rather than live aliases."""

    def __init__(self):
        self.generation = 10
        self.payload = manifest_payload()
        self.before_download = lambda: None
        self.after_upload = lambda: None
        self.uploads = []
        self.client = mock.Mock()
        self.client.bucket.return_value.blob.side_effect = self.blob

    def blob(self, _name):
        store = self

        class Blob:
            def reload(self):
                self.generation = store.generation
                self.content_type = "application/json"
                self.size = len(json.dumps(store.payload))

            def download_as_text(self, *, if_generation_match=None):
                store.before_download()
                if if_generation_match is not None and if_generation_match != store.generation:
                    raise PreconditionFailed("generation changed")
                return json.dumps(store.payload)

            def upload_from_string(self, text, *, content_type, if_generation_match):
                if if_generation_match != store.generation:
                    raise PreconditionFailed("generation changed")
                store.uploads.append(if_generation_match)
                store.generation += 1
                store.payload = json.loads(text)
                self.generation = store.generation
                self.content_type = content_type
                self.size = len(text.encode())
                store.after_upload()

        return Blob()

    def replace_concurrently(self):
        self.generation += 1
        self.payload = {"concurrent": True}


class FinalizePromotedReleaseMetadataTests(unittest.TestCase):
    def test_finalizer_refuses_replacement_after_original_read_changes(self):
        store = JsonStore()

        def derive(payload, **_kwargs):
            store.replace_concurrently()
            return payload

        with (
            mock.patch.object(finalizer, "finalized_manifest_payload", side_effect=derive),
            mock.patch.object(finalizer.gcs_asset, "require_mutation_allowed"),
            self.assertRaises(finalizer.FinalizeReleaseMetadataError),
        ):
            finalizer.finalize_promoted_release_metadata(
                {"promotions": [{"destination_uri": uri(".manifest.json")}]}, client=store.client,
            )
        self.assertEqual(store.payload, {"concurrent": True})
        self.assertEqual(store.uploads, [])

    def test_finalizer_pins_json_download_to_observed_generation(self):
        store = JsonStore()
        store.before_download = store.replace_concurrently
        with self.assertRaises(finalizer.FinalizeReleaseMetadataError):
            finalizer.load_json_object(store.client, uri(".manifest.json"))
        self.assertEqual(store.uploads, [])

    def test_finalizer_reports_upload_generation_without_later_reload(self):
        store = JsonStore()
        store.after_upload = store.replace_concurrently
        with (
            mock.patch.object(finalizer, "finalized_manifest_payload", side_effect=lambda payload, **_kwargs: payload),
            mock.patch.object(finalizer.gcs_asset, "require_mutation_allowed"),
        ):
            result = finalizer.finalize_promoted_release_metadata(
                {"promotions": [{"destination_uri": uri(".manifest.json")}]}, client=store.client,
            )
        self.assertEqual(result["finalized_manifests"][0]["generation"], 11)
        self.assertEqual(store.generation, 12)
        self.assertEqual(store.payload, {"concurrent": True})

    def test_finalizer_run_record_also_uses_original_generation(self):
        manifest_store = JsonStore()
        run_store = JsonStore()
        run_store.payload = {"release_paths": [], "latest_paths": []}
        client = mock.Mock()
        client.bucket.return_value.blob.side_effect = lambda name: (
            manifest_store if name.endswith(".manifest.json") else run_store
        ).blob(name)

        def derive_record(payload, **_kwargs):
            run_store.replace_concurrently()
            return payload

        with (
            mock.patch.object(finalizer, "finalized_manifest_payload", side_effect=lambda payload, **_kwargs: payload),
            mock.patch.object(finalizer, "finalized_run_record_payload", side_effect=derive_record),
            mock.patch.object(finalizer.gcs_asset, "require_mutation_allowed"),
            self.assertRaises(finalizer.FinalizeReleaseMetadataError),
        ):
            finalizer.finalize_promoted_release_metadata(
                {"promotions": [
                    {"destination_uri": uri(".manifest.json")},
                    {"destination_uri": f"{ROOT}/runs/{RELEASE}.json"},
                ]}, client=client,
            )
        self.assertEqual(manifest_store.uploads, [10])
        self.assertEqual(run_store.uploads, [])
        self.assertEqual(run_store.payload, {"concurrent": True})

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
