from __future__ import annotations

import json
import contextlib
import io
import tempfile
import unittest
import hashlib
from pathlib import Path

from scripts import validate_feature_metadata as validator
from scripts import release_feature_model


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
        identity=release_feature_model.build_identity_metadata(
            strategy="source_field", source_fields=["id"]
        ),
        validation={"valid": True, "feature_count": count},
    )
    manifest.write_text(json.dumps(manifest_payload, sort_keys=True) + "\n")
    return sidecar, schema, manifest


class MetadataValidationTests(unittest.TestCase):
    def validate(self, paths, **kwargs):
        sidecar, schema, manifest = paths
        return validator.validate_bundle(
            sidecar_path=sidecar,
            schema_path=schema,
            manifest_path=manifest,
            asset_slug="example-asset",
            release="2026-05-01",
            **kwargs,
        )

    def update_manifest(self, paths, **updates):
        manifest = paths[2]
        payload = json.loads(manifest.read_text())
        payload.update(updates)
        for artifact in payload["artifacts"]:
            if artifact["role"] in {"metadata", "schema"}:
                artifact["sha256"] = file_sha(
                    paths[0 if artifact["role"] == "metadata" else 1]
                )
        manifest.write_text(json.dumps(payload))

    def test_valid_bundle_pins_generation_and_never_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_bundle(Path(tmp))
            before = {path: path.read_bytes() for path in paths}
            result = self.validate(paths, sidecar_generation=12, schema_generation=13)
            self.assertEqual(result.feature_count, 3)
            self.assertEqual(before, {path: path.read_bytes() for path in paths})
            with self.assertRaisesRegex(
                validator.MetadataValidationError, "generation"
            ):
                self.validate(paths, sidecar_generation=99)
            with self.assertRaisesRegex(validator.MetadataValidationError, "path"):
                self.validate(paths, sidecar_uri="gs://bucket/wrong.metadata.ndjson.gz")

    def test_local_checksums_and_declared_count_are_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_bundle(Path(tmp))
            paths[1].write_text(paths[1].read_text() + "\n")
            with self.assertRaisesRegex(validator.MetadataValidationError, "checksum"):
                self.validate(paths)
            self.update_manifest(paths, validation={"valid": True, "feature_count": 99})
            with self.assertRaisesRegex(
                validator.MetadataValidationError, "feature_count"
            ):
                self.validate(paths)

    def test_rejects_invalid_rows_and_schema_projection(self):
        cases = (
            ("duplicate", lambda rows: rows.append(rows[0]), "duplicate"),
            (
                "invalid ID",
                lambda rows: rows[0].update(feature_id="bad-id"),
                "feature_id",
            ),
            (
                "invalid hash",
                lambda rows: rows[0].update(geometry_hash="bad"),
                "geometry_hash",
            ),
            (
                "wrong release",
                lambda rows: rows[0].update(release="2026-05-02"),
                "release",
            ),
            (
                "extra property",
                lambda rows: rows[0]["properties"].update(secret="unexpected"),
                "outside the release schema",
            ),
            ("empty", lambda rows: rows.clear(), "at least one"),
        )
        for label, mutate, error in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                paths = write_bundle(Path(tmp))
                rows = list(release_feature_model.read_metadata_sidecar(paths[0]))
                mutate(rows)
                release_feature_model.write_metadata_sidecar(rows, paths[0])
                self.update_manifest(paths, validation={"valid": True})
                with self.assertRaisesRegex(validator.MetadataValidationError, error):
                    self.validate(paths)

    def test_cli_reports_validation_without_cloud_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_bundle(Path(tmp))
            args = ["--asset-slug", "example-asset", "--release", "2026-05-01"]
            for role, path in zip(("sidecar", "schema", "manifest"), paths):
                args.extend([f"--{role}", str(path)])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(validator.main(args), 0)
            self.assertEqual(json.loads(output.getvalue())["feature_count"], 3)
            with (
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                validator.main(args + ["--project", "shared-datasets-1"])
