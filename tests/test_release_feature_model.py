from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ingestion.common import feature_metadata
from scripts import release_feature_model as model


VALID_HASH_A = "sha256:" + "a" * 64
VALID_HASH_B = "sha256:" + "b" * 64


class ReleaseFeatureModelTests(unittest.TestCase):
    def test_source_field_feature_id_uses_valid_source_value_directly(self):
        self.assertEqual(model.source_field_feature_id(source_field="WDPAID", source_value="123"), "123")
        self.assertEqual(model.source_field_feature_id(source_field="OBJECTID", source_value=0), "0")
        for value in (None, "", "abc-def", "abc_def", "abc.def", "a" * 65):
            with self.subTest(value=value), self.assertRaisesRegex(model.ReleaseFeatureModelError, "alphanumeric"):
                model.source_field_feature_id(source_field="WDPAID", source_value=value)

    def test_source_fields_identity_key_preserves_zero_values(self):
        self.assertEqual(model.source_fields_identity_key({"SITE_PID": 0}, ["SITE_PID"]), ("0",))
        for properties in ({"SITE_PID": None}, {"SITE_PID": ""}, {}):
            with self.subTest(properties=properties), self.assertRaisesRegex(model.ReleaseFeatureModelError, "blank"):
                model.source_fields_identity_key(properties, ["SITE_PID"])

    def test_generated_decimal_ids_reuse_previous_and_skip_retired_ids(self):
        previous = [
            {"feature_id": "7", "identity_key": ["a"], "geometry_hash": VALID_HASH_A, "properties_hash": VALID_HASH_A},
            {"feature_id": "9", "identity_key": ["retired"], "geometry_hash": VALID_HASH_B, "properties_hash": VALID_HASH_B},
        ]

        assigned = model.assign_generated_feature_ids((["b"], ["a"], ["c"]), previous_records=previous)

        self.assertEqual(assigned[("a",)], "7")
        self.assertEqual(assigned[("b",)], "10")
        self.assertEqual(assigned[("c",)], "11")

    def test_generated_sequence_source_fields_accepts_one_or_two_fields(self):
        one_field = model.build_identity_metadata(
            strategy="generated_sequence_source_fields",
            source_fields=["SITE_PID"],
        )
        two_fields = model.build_identity_metadata(
            strategy="generated_sequence_source_fields",
            source_fields=["source_layer", "PRIMKEY"],
        )

        self.assertEqual(one_field["source_fields"], ["SITE_PID"])
        self.assertEqual(one_field["assignment_key"], ["SITE_PID"])
        self.assertEqual(two_fields["source_fields"], ["source_layer", "PRIMKEY"])
        self.assertEqual(two_fields["assignment_key"], ["source_layer", "PRIMKEY"])

    def test_generated_sequence_source_fields_rejects_three_fields(self):
        with self.assertRaisesRegex(model.ReleaseFeatureModelError, "one or two source fields"):
            model.build_identity_metadata(
                strategy="generated_sequence_source_fields",
                source_fields=["week", "region", "country"],
            )

    def test_generated_sequence_uses_single_url_unfriendly_source_field_as_identity_key(self):
        feature = {
            "type": "Feature",
            "properties": {"SITE_PID": "WDPA-123"},
            "geometry": {"type": "Point", "coordinates": [0, 0]},
        }

        enriched, sidecar, ambiguities = feature_metadata.enrich_features_with_generated_ids(
            [feature],
            asset_slug="wdpa-marine",
            release="2026-05-01",
            provenance={},
            source_fields=["SITE_PID"],
        )

        self.assertEqual(ambiguities, ())
        self.assertEqual(enriched[0]["properties"]["feature_id"], "1")
        self.assertEqual(sidecar[0]["identity_key"], ["WDPA-123"])

    def test_generated_ids_collapse_exact_duplicate_rows_with_provenance(self):
        feature = {
            "type": "Feature",
            "properties": {"DN": 3},
            "geometry": {"type": "Point", "coordinates": [0, 0]},
        }

        enriched, sidecar, ambiguities = feature_metadata.enrich_features_with_generated_ids(
            [feature, feature],
            asset_slug="ims-sea-ice-extent",
            release="2026-05-01",
            provenance={},
        )

        self.assertEqual(len(enriched), 1)
        self.assertEqual(len(sidecar), 1)
        self.assertEqual(ambiguities, ())
        self.assertEqual(sidecar[0]["feature_id"], "1")
        self.assertEqual(sidecar[0]["provenance"]["duplicate_source_row_numbers"], [2])

    def test_partial_hash_matches_report_ambiguities(self):
        new = {
            "feature_id": "2",
            "identity_key": ["new"],
            "geometry_hash": VALID_HASH_A,
            "properties_hash": VALID_HASH_B,
            "properties": {},
        }
        previous = [
            {"feature_id": "1", "identity_key": ["old"], "geometry_hash": VALID_HASH_A, "properties_hash": VALID_HASH_A},
        ]

        ambiguities = model.find_identity_ambiguities([new], previous_records=previous)

        self.assertEqual(len(ambiguities), 1)
        self.assertEqual(ambiguities[0].ambiguity_type, "same_geometry_changed_properties")
        self.assertEqual(ambiguities[0].matching_geometry_feature_ids, ("1",))
        self.assertEqual(ambiguities[0].matching_properties_feature_ids, ())

    def test_reviewed_resolution_reuses_previous_feature_id_for_new_identity_key(self):
        previous = [
            {"feature_id": "7", "identity_key": ["old"], "geometry_hash": VALID_HASH_A, "properties_hash": VALID_HASH_A},
        ]
        new = {
            "identity_key": ["new"],
            "geometry_hash": VALID_HASH_A,
            "properties_hash": VALID_HASH_B,
        }
        ambiguity = model.find_identity_ambiguities([new], previous_records=previous)[0]
        resolutions = model.validate_identity_resolutions(
            release="2026-05-01",
            ambiguities=[ambiguity],
            decisions=[
                {
                    "release": "2026-05-01",
                    "action": "reuse_previous_feature_id",
                    "new_identity_key": ["new"],
                    "new_geometry_hash": VALID_HASH_A,
                    "new_properties_hash": VALID_HASH_B,
                    "matching_geometry_feature_ids": ["7"],
                    "matching_properties_feature_ids": [],
                    "matching_geometry_properties_hashes": [VALID_HASH_A],
                    "matching_properties_geometry_hashes": [],
                    "reuse_feature_id": "7",
                    "rationale": "Same footprint; source attributes changed.",
                    "reviewer": "jonaraphael",
                    "pr_reference": "https://github.com/SkyTruth/shared-datasets-1/pull/123",
                }
            ],
        )

        assigned = model.assign_generated_feature_ids(
            [["new"]],
            previous_records=previous,
            feature_id_overrides=model.resolved_feature_id_overrides(resolutions),
        )

        self.assertEqual(assigned[("new",)], "7")
        self.assertEqual(model.unresolved_identity_ambiguities([ambiguity], resolutions), ())

    def test_reviewed_resolution_assigns_new_feature_id_even_for_previous_key(self):
        previous = [
            {"feature_id": "7", "identity_key": ["same"], "geometry_hash": VALID_HASH_A, "properties_hash": VALID_HASH_A},
        ]
        new = {
            "identity_key": ["same"],
            "geometry_hash": VALID_HASH_A,
            "properties_hash": VALID_HASH_B,
        }
        ambiguity = model.find_identity_ambiguities([new], previous_records=previous)[0]
        resolutions = model.validate_identity_resolutions(
            release="2026-05-01",
            ambiguities=[ambiguity],
            decisions=[
                {
                    "release": "2026-05-01",
                    "action": "assign_new_feature_id",
                    "new_identity_key": ["same"],
                    "new_geometry_hash": VALID_HASH_A,
                    "new_properties_hash": VALID_HASH_B,
                    "matching_geometry_feature_ids": ["7"],
                    "matching_properties_feature_ids": [],
                    "matching_geometry_properties_hashes": [VALID_HASH_A],
                    "matching_properties_geometry_hashes": [],
                    "rationale": "Same footprint now represents a different logical feature.",
                    "reviewer": "jonaraphael",
                    "pr_reference": "https://github.com/SkyTruth/shared-datasets-1/pull/123",
                }
            ],
        )

        assigned = model.assign_generated_feature_ids(
            [["same"]],
            previous_records=previous,
            force_new_identity_keys=model.resolved_force_new_identity_keys(resolutions),
        )

        self.assertEqual(assigned[("same",)], "8")

    def test_stale_resolution_is_rejected(self):
        ambiguity = model.IdentityAmbiguity(
            ambiguity_type="same_geometry_changed_properties",
            identity_key=("new",),
            geometry_hash=VALID_HASH_A,
            properties_hash=VALID_HASH_B,
            matching_geometry_feature_ids=("1",),
            matching_properties_feature_ids=(),
            matching_geometry_properties_hashes=(VALID_HASH_A,),
            matching_properties_geometry_hashes=(),
        )

        with self.assertRaisesRegex(model.ReleaseFeatureModelError, "stale"):
            model.validate_identity_resolutions(
                release="2026-05-01",
                ambiguities=[ambiguity],
                decisions=[
                    {
                        "release": "2026-05-01",
                        "action": "assign_new_feature_id",
                        "new_identity_key": ["other"],
                        "new_geometry_hash": VALID_HASH_A,
                        "new_properties_hash": VALID_HASH_B,
                        "matching_geometry_feature_ids": ["1"],
                        "matching_properties_feature_ids": [],
                        "matching_geometry_properties_hashes": [VALID_HASH_A],
                        "matching_properties_geometry_hashes": [],
                        "rationale": "Wrong record.",
                        "reviewer": "jonaraphael",
                        "pr_reference": "https://github.com/SkyTruth/shared-datasets-1/pull/123",
                    }
                ],
            )

    def test_unresolved_ambiguities_notify_then_raise(self):
        ambiguity = model.IdentityAmbiguity(
            ambiguity_type="same_geometry_changed_properties",
            identity_key=("new",),
            geometry_hash=VALID_HASH_A,
            properties_hash=VALID_HASH_B,
            matching_geometry_feature_ids=("1",),
            matching_properties_feature_ids=(),
            matching_geometry_properties_hashes=(VALID_HASH_A,),
            matching_properties_geometry_hashes=(),
        )

        with mock.patch("scripts.slack_notify.notify", return_value=True) as notify:
            with self.assertRaisesRegex(RuntimeError, "unresolved partial identity hash"):
                feature_metadata.raise_unresolved_identity_ambiguities(
                    asset_slug="example",
                    release="2026-05-01",
                    ambiguities=[ambiguity],
                )

        notify.assert_called_once()
        self.assertIn("catalog/feature-identity-resolutions/example.json", notify.call_args.kwargs["body"])

    def test_sidecar_round_trip_uses_split_hashes(self):
        feature = model.FeatureRecord(
            feature_id="1",
            geometry_hash=VALID_HASH_A,
            properties_hash=VALID_HASH_B,
            geometry=None,
            properties={"name": "A"},
            provenance={"source": "fixture"},
        )
        record = model.sidecar_record(asset_slug="example", release="2026-05-01", feature=feature, identity_key=("A",))

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "example.metadata.ndjson.gz"
            model.write_metadata_sidecar([record], path)
            rows = list(model.read_metadata_sidecar(path))

        self.assertEqual(rows[0]["feature_id"], "1")
        self.assertEqual(rows[0]["geometry_hash"], VALID_HASH_A)
        self.assertEqual(rows[0]["properties_hash"], VALID_HASH_B)
        self.assertEqual(rows[0]["properties"], {"name": "A"})

    def test_common_sidecar_writer_rejects_missing_hashes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad.metadata.ndjson.gz"

            with self.assertRaisesRegex(RuntimeError, "metadata sidecar validation failed"):
                feature_metadata.write_sidecar(
                    [
                        {
                            "schema_version": model.METADATA_SIDECAR_SCHEMA_VERSION,
                            "asset_slug": "example",
                            "release": "2026-05-01",
                            "feature_id": "1",
                            "properties": {},
                            "provenance": {},
                        }
                    ],
                    path,
                )

    def test_manifest_records_feature_identity(self):
        schema = feature_metadata.schema_from_records(
            asset_slug="example",
            release="2026-05-01",
            records=[],
        )
        identity = model.build_identity_metadata(strategy="source_field", source_fields=["WDPAID"])
        manifest = model.build_release_manifest(
            asset_slug="example",
            release="2026-05-01",
            source_inputs=[{"uri": "gs://bucket/source"}],
            artifacts=[
                {"role": "fgb", "path": "gs://bucket/example.fgb", "sha256": "a" * 64},
                {"role": "pmtiles", "path": "gs://bucket/example.pmtiles", "sha256": "b" * 64},
                {"role": "metadata", "path": "gs://bucket/example.metadata.ndjson.gz", "sha256": "c" * 64},
                {"role": "schema", "path": "gs://bucket/example.schema.json", "sha256": "d" * 64},
                {"role": "manifest", "path": "gs://bucket/example.manifest.json"},
            ],
            schema=schema,
            identity=identity,
            validation={"valid": True},
        )

        self.assertEqual(manifest["schema_version"], model.RELEASE_MANIFEST_SCHEMA_VERSION)
        self.assertEqual(manifest["identity"]["strategy"], "source_field")
        self.assertEqual(manifest["identity"]["source_fields"], ["WDPAID"])
        self.assertEqual(manifest["index_status_policy"]["mode"], "inactive_firestore_serving")
        self.assertIsNone(manifest["index_status_policy"]["path"])

    def test_manifest_rejects_inactive_index_policy_with_path(self):
        schema = feature_metadata.schema_from_records(
            asset_slug="example",
            release="2026-05-01",
            records=[],
        )
        identity = model.build_identity_metadata(strategy="source_field", source_fields=["WDPAID"])
        manifest = model.build_release_manifest(
            asset_slug="example",
            release="2026-05-01",
            source_inputs=[{"uri": "gs://bucket/source"}],
            artifacts=[
                {"role": "fgb", "path": "gs://bucket/example.fgb", "sha256": "a" * 64},
                {"role": "pmtiles", "path": "gs://bucket/example.pmtiles", "sha256": "b" * 64},
                {"role": "metadata", "path": "gs://bucket/example.metadata.ndjson.gz", "sha256": "c" * 64},
                {"role": "schema", "path": "gs://bucket/example.schema.json", "sha256": "d" * 64},
                {"role": "manifest", "path": "gs://bucket/example.manifest.json"},
            ],
            schema=schema,
            identity=identity,
            validation={"valid": True},
        )
        manifest["index_status_policy"]["path"] = "gs://bucket/example/index-loads/2026-05-01/"

        with self.assertRaisesRegex(model.ReleaseFeatureModelError, "null path"):
            model.validate_release_manifest(manifest, expected_asset_slug="example", expected_release="2026-05-01")


if __name__ == "__main__":
    unittest.main()
