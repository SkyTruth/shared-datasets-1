"""Tests for the streaming generated-identity release writer.

The writer exists so peak memory tracks the identity data (one key and two
hashes per feature) instead of the release payload. These tests pin that
property directly, not just the output contents, because a regression to
list-materialization would still produce correct files — it would only fail in
production, on the largest asset, after a 30-minute build.
"""

from __future__ import annotations

import datetime as dt
import gc
import gzip
import json
import tempfile
import unittest
import weakref
from pathlib import Path
from typing import Any
from unittest import mock

from ingestion.common import feature_metadata
from release_streaming_helpers import write_generated_release
from scripts import release_feature_model as model


class TrackedGeometry(dict):
    """A dict that supports weak references, so tests can observe retention."""


def geojson_feature(index: int) -> dict:
    return {
        "type": "Feature",
        "properties": {"SITE_PID": str(index), "NAME": f"Site {index}"},
        "geometry": TrackedGeometry({"type": "Point", "coordinates": [index, 0]}),
    }


class ReleaseStreamingTests(unittest.TestCase):
    def test_writer_retains_no_feature_payloads(self):
        geometry_refs: list[weakref.ref] = []

        def open_features():
            for index in range(1, 26):
                feature = geojson_feature(index)
                geometry_refs.append(weakref.ref(feature["geometry"]))
                yield feature

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = feature_metadata.write_generated_id_release(
                open_features=open_features,
                asset_slug="wdpa-marine",
                release="2026-08-01",
                provenance={},
                source_fields=["SITE_PID"],
                enriched_features_path=tmp_path / "enriched.geojsonseq",
                sidecar_path=tmp_path / "metadata.ndjson.gz",
            )

        self.assertEqual(result.feature_count, 25)
        # Two passes yield two generations of geometry objects.
        self.assertEqual(len(geometry_refs), 50)
        gc.collect()
        live = [ref for ref in geometry_refs if ref() is not None]
        self.assertEqual(
            live,
            [],
            f"{len(live)} feature geometries are still reachable after writing; "
            "the writer must not retain payloads",
        )

    def test_writer_makes_exactly_two_passes(self):
        calls = 0
        features = [geojson_feature(index) for index in range(1, 6)]

        def open_features():
            nonlocal calls
            calls += 1
            return iter(features)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            feature_metadata.write_generated_id_release(
                open_features=open_features,
                asset_slug="wdpa-marine",
                release="2026-08-01",
                provenance={},
                source_fields=["SITE_PID"],
                enriched_features_path=tmp_path / "enriched.geojsonseq",
                sidecar_path=tmp_path / "metadata.ndjson.gz",
            )

        self.assertEqual(calls, 2)

    def test_written_artifacts_agree_and_preserve_source_order(self):
        features = [geojson_feature(index) for index in range(1, 8)]

        enriched, sidecar, result = write_generated_release(
            features,
            asset_slug="wdpa-marine",
            release="2026-08-01",
            provenance={"source": "test"},
            source_fields=["SITE_PID"],
        )

        self.assertEqual(result.feature_count, 7)
        self.assertEqual(len(enriched), 7)
        self.assertEqual(len(sidecar), 7)
        self.assertEqual(
            [record["feature_id"] for record in sidecar],
            [str(index) for index in range(1, 8)],
        )
        for index, (enriched_feature, record) in enumerate(zip(enriched, sidecar), start=1):
            self.assertEqual(enriched_feature["properties"]["SITE_PID"], str(index))
            self.assertEqual(enriched_feature["properties"]["feature_id"], record["feature_id"])
            self.assertEqual(enriched_feature["properties"]["geometry_hash"], record["geometry_hash"])
            self.assertEqual(enriched_feature["properties"]["properties_hash"], record["properties_hash"])
            self.assertEqual(record["provenance"]["source_row_number"], index)
            self.assertNotIn("feature_id", record["properties"])
        validation = model.validate_sidecar_records(
            sidecar,
            expected_asset_slug="wdpa-marine",
            expected_release="2026-08-01",
        )
        self.assertTrue(validation.valid, validation.errors)
        self.assertEqual(result.next_generated_feature_id, 8)

    def test_schema_payload_matches_a_full_pass_over_the_written_sidecar(self):
        features = [geojson_feature(index) for index in range(1, 5)]
        features.append(
            {
                "type": "Feature",
                "properties": {"SITE_PID": "9", "NAME": None, "EXTRA": 1.5},
                "geometry": TrackedGeometry({"type": "Point", "coordinates": [9, 0]}),
            }
        )

        _enriched, sidecar, result = write_generated_release(
            features,
            asset_slug="wdpa-marine",
            release="2026-08-01",
            provenance={},
            source_fields=["SITE_PID"],
        )

        # Fields keep first-observation (source) order, as they did when the
        # schema was derived from an in-memory record list.
        self.assertEqual(
            result.schema_payload["fields"],
            [
                {"name": "SITE_PID", "type": "String", "nullable": False, "projectable": True},
                {"name": "NAME", "type": "String", "nullable": True, "projectable": True},
                {"name": "EXTRA", "type": "Real", "nullable": False, "projectable": True},
            ],
        )
        # Streaming accumulation must agree field-for-field with a full pass.
        # Compared by name because the written sidecar canonicalizes (sorts)
        # property keys, which changes only first-observation order.
        full_pass = feature_metadata.schema_from_records(
            asset_slug="wdpa-marine",
            release="2026-08-01",
            records=sidecar,
        )
        self.assertEqual(
            {field["name"]: field for field in result.schema_payload["fields"]},
            {field["name"]: field for field in full_pass["fields"]},
        )

    def test_unresolved_ambiguity_writes_no_artifacts(self):
        geometry = TrackedGeometry({"type": "Point", "coordinates": [0, 0]})
        geometry_hash, properties_hash = feature_metadata.content_hashes(
            geometry=geometry,
            properties={"SITE_PID": "1", "NAME": "Old"},
            exclude_properties=(),
        )
        previous_records = [
            {
                "feature_id": "7",
                "geometry_hash": geometry_hash,
                "properties_hash": properties_hash,
                "identity_key": ["7"],
                "properties": {"SITE_PID": "1", "NAME": "Old"},
            }
        ]
        feature = {
            "type": "Feature",
            "properties": {"SITE_PID": "2", "NAME": "Renamed"},
            "geometry": geometry,
        }

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            enriched_path = tmp_path / "enriched.geojsonseq"
            sidecar_path = tmp_path / "metadata.ndjson.gz"
            with mock.patch("scripts.slack_notify.notify", return_value=True):
                with self.assertRaisesRegex(RuntimeError, "unresolved partial identity hash"):
                    feature_metadata.write_generated_id_release(
                        open_features=lambda: iter([feature]),
                        asset_slug="wdpa-marine",
                        release="2026-08-01",
                        provenance={},
                        source_fields=["SITE_PID"],
                        enriched_features_path=enriched_path,
                        sidecar_path=sidecar_path,
                        previous_records=previous_records,
                    )

            self.assertFalse(enriched_path.exists(), "no enriched output may exist for a blocked release")
            self.assertFalse(sidecar_path.exists(), "no sidecar may exist for a blocked release")

    def test_reviewed_decision_unblocks_and_reuses_the_previous_feature_id(self):
        geometry = TrackedGeometry({"type": "Point", "coordinates": [0, 0]})
        previous_geometry_hash, previous_properties_hash = feature_metadata.content_hashes(
            geometry=geometry,
            properties={"SITE_PID": "1", "NAME": "Old"},
            exclude_properties=(),
        )
        previous_records = [
            {
                "feature_id": "7",
                "geometry_hash": previous_geometry_hash,
                "properties_hash": previous_properties_hash,
                "identity_key": ["1"],
                "properties": {"SITE_PID": "1", "NAME": "Old"},
            }
        ]
        feature = {
            "type": "Feature",
            "properties": {"SITE_PID": "2", "NAME": "Renamed"},
            "geometry": geometry,
        }
        new_geometry_hash, new_properties_hash = feature_metadata.content_hashes(
            geometry=geometry,
            properties={"SITE_PID": "2", "NAME": "Renamed"},
            exclude_properties=(),
        )

        _enriched, sidecar, _result = write_generated_release(
            [feature],
            asset_slug="wdpa-marine",
            release="2026-08-01",
            provenance={},
            source_fields=["SITE_PID"],
            previous_records=previous_records,
            identity_resolution_decisions=[
                {
                    "release": "2026-08-01",
                    "action": "reuse_previous_feature_id",
                    "new_identity_key": ["2"],
                    "new_geometry_hash": new_geometry_hash,
                    "new_properties_hash": new_properties_hash,
                    "matching_geometry_feature_ids": ["7"],
                    "matching_properties_feature_ids": [],
                    "matching_geometry_properties_hashes": [previous_properties_hash],
                    "matching_properties_geometry_hashes": [],
                    "reuse_feature_id": "7",
                    "rationale": "Same footprint; upstream renamed the site.",
                    "reviewer": "jonaraphael",
                    "pr_reference": "https://github.com/SkyTruth/shared-datasets-1/pull/133",
                }
            ],
        )

        self.assertEqual(sidecar[0]["feature_id"], "7")

    def test_duplicate_identity_key_with_different_content_fails_in_the_identity_pass(self):
        features = [
            {
                "type": "Feature",
                "properties": {"SITE_PID": "1", "NAME": "First"},
                "geometry": TrackedGeometry({"type": "Point", "coordinates": [0, 0]}),
            },
            {
                "type": "Feature",
                "properties": {"SITE_PID": "1", "NAME": "Conflicting"},
                "geometry": TrackedGeometry({"type": "Point", "coordinates": [1, 1]}),
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            enriched_path = tmp_path / "enriched.geojsonseq"
            with self.assertRaisesRegex(RuntimeError, "duplicate generated identity key"):
                feature_metadata.write_generated_id_release(
                    open_features=lambda: iter(features),
                    asset_slug="wdpa-marine",
                    release="2026-08-01",
                    provenance={},
                    source_fields=["SITE_PID"],
                    enriched_features_path=enriched_path,
                    sidecar_path=tmp_path / "metadata.ndjson.gz",
                )
            self.assertFalse(enriched_path.exists())

    def test_duplicate_rows_collapse_and_record_every_duplicate_ordinal(self):
        feature = {
            "type": "Feature",
            "properties": {"SITE_PID": "1", "NAME": "Repeated"},
            "geometry": TrackedGeometry({"type": "Point", "coordinates": [0, 0]}),
        }
        other = geojson_feature(2)

        enriched, sidecar, result = write_generated_release(
            [feature, other, feature, feature],
            asset_slug="wdpa-marine",
            release="2026-08-01",
            provenance={},
            source_fields=["SITE_PID"],
        )

        self.assertEqual(result.feature_count, 2)
        self.assertEqual(len(enriched), 2)
        self.assertEqual(sidecar[0]["provenance"]["duplicate_source_row_numbers"], [3, 4])
        self.assertNotIn("duplicate_source_row_numbers", sidecar[1]["provenance"])

    def test_empty_source_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "metadata sidecar would be empty"):
                feature_metadata.write_generated_id_release(
                    open_features=lambda: iter([]),
                    asset_slug="wdpa-marine",
                    release="2026-08-01",
                    provenance={},
                    source_fields=["SITE_PID"],
                    enriched_features_path=tmp_path / "enriched.geojsonseq",
                    sidecar_path=tmp_path / "metadata.ndjson.gz",
                )

    def test_source_shrinking_between_passes_fails_loudly(self):
        features = [geojson_feature(index) for index in range(1, 5)]
        passes = 0

        def open_features():
            nonlocal passes
            passes += 1
            if passes == 1:
                return iter(features)
            return iter(features[:2])

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "source changed between identity and write passes"):
                feature_metadata.write_generated_id_release(
                    open_features=open_features,
                    asset_slug="wdpa-marine",
                    release="2026-08-01",
                    provenance={},
                    source_fields=["SITE_PID"],
                    enriched_features_path=tmp_path / "enriched.geojsonseq",
                    sidecar_path=tmp_path / "metadata.ndjson.gz",
                )


class IdentityBaselineTests(unittest.TestCase):
    def test_baseline_drops_properties_but_keeps_identity_fields(self):
        baseline = feature_metadata.identity_baseline_records(
            [
                {
                    "feature_id": "7",
                    "geometry_hash": "sha256:" + "a" * 64,
                    "properties_hash": "sha256:" + "b" * 64,
                    "identity_key": ["7"],
                    "properties": {"NAME": "Site", "blob": "x" * 1000},
                    "provenance": {"source_row_number": 1},
                }
            ],
            exclude_properties=(),
        )

        self.assertEqual(
            baseline,
            [
                {
                    "feature_id": "7",
                    "geometry_hash": "sha256:" + "a" * 64,
                    "properties_hash": "sha256:" + "b" * 64,
                    "identity_key": ["7"],
                }
            ],
        )

    def test_baseline_recomputes_hashes_when_properties_are_excluded(self):
        properties = {"DN": 3, "ice_date": "2026-06-14"}
        geometry_hash = "sha256:" + "c" * 64
        expected_properties_hash = model.properties_hash(properties, exclude_properties=("ice_date",))

        baseline = feature_metadata.identity_baseline_records(
            [
                {
                    "feature_id": "7",
                    "geometry_hash": geometry_hash,
                    "properties_hash": "sha256:" + "d" * 64,
                    "identity_key": ["stale"],
                    "properties": properties,
                }
            ],
            exclude_properties=("ice_date",),
        )

        self.assertEqual(baseline[0]["properties_hash"], expected_properties_hash)
        self.assertEqual(
            baseline[0]["identity_key"],
            [geometry_hash, expected_properties_hash],
        )
        self.assertNotIn("properties", baseline[0])


class SidecarWriterTests(unittest.TestCase):
    def test_write_sidecar_streams_a_generator_and_returns_the_count(self):
        def records():
            for index in range(1, 4):
                yield feature_metadata.sidecar_record(
                    asset_slug="wdpa-marine",
                    release="2026-08-01",
                    feature_id=str(index),
                    geometry_hash="sha256:" + f"{index}".rjust(64, "a"),
                    properties_hash="sha256:" + f"{index}".rjust(64, "b"),
                    properties={"NAME": f"Site {index}"},
                    provenance={},
                    identity_key=[str(index)],
                )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metadata.ndjson.gz"
            written = feature_metadata.write_sidecar(records(), path)
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                lines = [json.loads(line) for line in handle if line.strip()]

        self.assertEqual(written, 3)
        self.assertEqual([record["feature_id"] for record in lines], ["1", "2", "3"])

    def test_write_sidecar_raises_on_duplicate_feature_ids(self):
        record = feature_metadata.sidecar_record(
            asset_slug="wdpa-marine",
            release="2026-08-01",
            feature_id="1",
            geometry_hash="sha256:" + "a" * 64,
            properties_hash="sha256:" + "b" * 64,
            properties={},
            provenance={},
            identity_key=["1"],
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metadata.ndjson.gz"
            with self.assertRaisesRegex(RuntimeError, "metadata sidecar validation failed"):
                feature_metadata.write_sidecar([record, dict(record)], path)


if __name__ == "__main__":
    unittest.main()


class WdpaBuildPipelineTests(unittest.TestCase):
    """Integration coverage for the wdpa-monthly call site of the writer."""

    def _run_build(self, tmp_path: Path) -> tuple[Any, list[str]]:
        from ingestion.wdpa_monthly import run as wdpa

        events: list[str] = []
        asset = wdpa.ASSETS[0]
        source_fields = (
            wdpa.FieldSpec(name="SITE_PID", type="String"),
            wdpa.FieldSpec(name=wdpa.TRANSLATION_FIELD, type="String"),
        )
        source_layers = [
            wdpa.SourceLayer(name="polygons", fields=source_fields, geometry_type="Polygon", source="source.shp")
        ]

        def fake_build_filtered_gpkg(**kwargs):
            events.append("build_gpkg")
            kwargs["output"].write_text("gpkg", encoding="utf-8")

        def fake_convert_gpkg_to_geojsonseq(gpkg, _asset, output):
            events.append("gpkg->geojsonseq")
            lines = [
                json.dumps(
                    {
                        "type": "Feature",
                        "properties": {"SITE_PID": str(index), wdpa.TRANSLATION_FIELD: f"Site {index}"},
                        "geometry": {"type": "Point", "coordinates": [index, 0]},
                    }
                )
                for index in (1, 2)
            ]
            output.write_text("\n".join(lines) + "\n", encoding="utf-8")

        def fake_remove_if_exists(path):
            events.append(f"remove:{Path(path).name}")
            Path(path).unlink(missing_ok=True)

        def fake_build_pmtiles(_geojsonseq, _asset, output):
            events.append("build_pmtiles")
            output.write_text("pmtiles", encoding="utf-8")

        def fake_convert_geojsonseq_to_fgb(_geojsonseq, _asset, output):
            events.append("geojsonseq->fgb")
            output.write_text("fgb", encoding="utf-8")

        with (
            mock.patch.object(wdpa, "expected_feature_count", return_value=2),
            mock.patch.object(wdpa, "build_filtered_gpkg", fake_build_filtered_gpkg),
            mock.patch.object(wdpa, "convert_gpkg_to_geojsonseq", fake_convert_gpkg_to_geojsonseq),
            mock.patch.object(wdpa, "remove_if_exists", fake_remove_if_exists),
            mock.patch.object(wdpa, "build_pmtiles", fake_build_pmtiles),
            mock.patch.object(wdpa, "convert_geojsonseq_to_fgb", fake_convert_geojsonseq_to_fgb),
            mock.patch.object(wdpa, "feature_count", return_value=2),
            mock.patch.object(wdpa, "layer_fields", return_value=source_fields + (
                wdpa.FieldSpec(name="feature_id", type="String"),
                wdpa.FieldSpec(name="geometry_hash", type="String"),
                wdpa.FieldSpec(name="properties_hash", type="String"),
            )),
            mock.patch.object(wdpa, "validate_pmtiles", return_value=None),
            mock.patch.object(wdpa, "sha256_file", return_value="deadbeef"),
            mock.patch.object(
                wdpa,
                "materialize_localized_metadata",
                return_value={"applied_translation_count": 2},
            ),
            mock.patch.object(wdpa.feature_metadata, "validate_release_vector_contract", return_value=None),
        ):
            outputs = wdpa.build_asset_outputs(
                source="source.shp",
                source_layers=source_layers,
                source_fields=source_fields,
                asset=asset,
                where="REALM = 'Marine'",
                workdir=tmp_path,
                run_date=dt.date(2026, 8, 1),
            )
        return outputs, events

    def test_gpkg_is_deleted_before_the_fgb_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            outputs, events = self._run_build(Path(tmp))

        gpkg_removal = events.index(f"remove:{outputs.fgb.stem}.gpkg")
        self.assertLess(events.index("gpkg->geojsonseq"), gpkg_removal)
        self.assertLess(gpkg_removal, events.index("geojsonseq->fgb"))
        self.assertLess(gpkg_removal, events.index("build_pmtiles"))

    def test_build_writes_metadata_translations_and_next_feature_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            outputs, _events = self._run_build(tmp_path)

            self.assertEqual(outputs.row_count, 2)
            self.assertEqual(outputs.next_generated_feature_id, 3)
            with gzip.open(outputs.metadata, "rt", encoding="utf-8") as handle:
                sidecar = [json.loads(line) for line in handle if line.strip()]
            self.assertEqual([record["feature_id"] for record in sidecar], ["1", "2"])
            translation_rows = outputs.metadata_translations.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(translation_rows), 3)  # header + one row per feature
            self.assertIn("feature_id", translation_rows[0])
