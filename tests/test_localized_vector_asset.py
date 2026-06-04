from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import localized_vector_asset


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str] | None = None) -> None:
    fields = fieldnames or list(rows[0])
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def feature(properties: dict[str, str]) -> dict[str, object]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [0, 0]},
        "properties": properties,
    }


ASSET_DOC = """---
schema_version: 1
asset_slug: example-asset
title: Example Asset
category: 100-geographic-reference
subcategory: 110-boundaries
status: active
access_tier: public
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/example-asset.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
source: Example source
license: Example license
citation: Example citation
localized_names:
  storage: localization_csv_v1
  join_key: ext_id
  localization_file: latest/example-asset-localizations.csv
  property_template: name_{locale_code}
  locale_code_format: bcp47_field_safe
  fallback_field: name
  translations:
  - locale_code: es
    field: name_es
    review_state_field: name_es_review_state
    label: Spanish
    review_state: mixed
files:
- path: latest/example-asset.fgb
  format: fgb
  role: canonical
  purpose: Canonical vector file
- path: latest/example-asset.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map tiles
- path: latest/example-asset-localizations.csv
  format: csv
  role: localization
  purpose: Feature display-name localizations keyed by ext_id for metadata/API use
---

# Example Asset

## What this is

Example.

## Files

Files.

## Schema notes

Schema.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `ext_id` | string | Stable source identifier. |

## Update notes

Manual.
"""


class LocalizedVectorAssetTests(unittest.TestCase):
    def test_validate_localization_csv_accepts_required_and_locale_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "example-asset-localizations.csv"
            write_csv(
                path,
                [
                    {
                        "ext_id": "1",
                        "name": "One",
                        "name_review_state": "source_provided",
                        "name_es": "Uno",
                        "name_es_review_state": "machine_translated",
                    },
                    {
                        "ext_id": "2",
                        "name": "Two",
                        "name_review_state": "human_reviewed",
                        "name_es": "Dos",
                        "name_es_review_state": "human_reviewed",
                    },
                ],
            )

            profile, rows = localized_vector_asset.validate_localization_csv(path)

        self.assertTrue(profile.valid)
        self.assertEqual(len(rows), 2)
        self.assertEqual(profile.locale_fields, ("name_es",))
        self.assertEqual(profile.aggregate_review_states, {"es": "mixed"})

    def test_validate_localization_csv_rejects_bad_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad-localizations.csv"
            write_csv(
                path,
                [
                    {
                        "ext_id": "1",
                        "name": "One",
                        "name_review_state": "draft",
                        "name_es": "",
                        "name_es_review_state": "machine_translated",
                    },
                    {
                        "ext_id": "1",
                        "name": "",
                        "name_review_state": "source_provided",
                        "name_es": "Uno",
                        "name_es_review_state": "",
                    },
                ],
            )

            profile, _rows = localized_vector_asset.validate_localization_csv(path)

        self.assertFalse(profile.valid)
        self.assertIn("duplicate ext_id", "\n".join(profile.errors))
        self.assertIn("name_review_state must be one of", "\n".join(profile.errors))
        self.assertIn("must be blank when name_es is blank", "\n".join(profile.errors))
        self.assertIn("name_es_review_state is required", "\n".join(profile.errors))

    def test_seed_localizations_appends_missing_rows_and_reports_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "example-asset-localizations.csv"
            write_csv(
                path,
                [
                    {
                        "ext_id": "a",
                        "name": "Old Name",
                        "name_review_state": "human_reviewed",
                    }
                ],
            )
            features = [
                feature({"provider_id": "a", "source_name": "New Name"}),
                feature({"provider_id": "b", "source_name": "Second"}),
            ]

            with mock.patch.object(localized_vector_asset, "iter_ogr_features", return_value=iter(features)):
                result = localized_vector_asset.seed_localizations(
                    fgb=Path(tmp) / "asset.fgb",
                    localizations=path,
                    ext_id_field="provider_id",
                    fallback_name_field="source_name",
                )

            with path.open(newline="") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(result["appended_rows"], 1)
        self.assertEqual(result["fallback_drift_count"], 1)
        self.assertEqual(rows[0]["name"], "Old Name")
        self.assertEqual(rows[1]["ext_id"], "b")
        self.assertEqual(rows[1]["name"], "Second")
        self.assertEqual(rows[1]["name_review_state"], "source_provided")

    def test_validate_localizations_checks_asset_doc_and_fgb_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = root / "example-asset.md"
            doc.write_text(ASSET_DOC)
            localizations = root / "example-asset-localizations.csv"
            write_csv(
                localizations,
                [
                    {
                        "ext_id": "a",
                        "name": "One",
                        "name_review_state": "source_provided",
                        "name_es": "Uno",
                        "name_es_review_state": "machine_translated",
                    },
                    {
                        "ext_id": "b",
                        "name": "Two",
                        "name_review_state": "source_provided",
                        "name_es": "Dos",
                        "name_es_review_state": "human_reviewed",
                    },
                ],
            )
            features = [feature({"ext_id": "a"}), feature({"ext_id": "b"})]

            with mock.patch.object(localized_vector_asset, "iter_ogr_features", return_value=iter(features)):
                result = localized_vector_asset.validate_localizations(
                    fgb=root / "example-asset.fgb",
                    localizations=localizations,
                    asset_doc=doc,
                )

        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["missing_ext_id_count"], 0)
        self.assertEqual(result["orphan_ext_id_count"], 0)

    def test_build_pmtiles_plan_uses_tippecanoe_mbtiles_conversion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fgb = root / "example-asset.fgb"
            fgb.write_bytes(b"not a real fgb")
            localizations = root / "example-asset-localizations.csv"
            write_csv(
                localizations,
                [
                    {
                        "ext_id": "a",
                        "name": "One",
                        "name_review_state": "source_provided",
                    }
                ],
            )

            plan = localized_vector_asset.build_pmtiles_plan(
                fgb=fgb,
                localizations=localizations,
                asset_slug="example-asset",
                output=root / "example-asset.pmtiles",
            )

        self.assertEqual(plan.localized_property_fields, ("name",))
        self.assertEqual([command["kind"] for command in plan.commands], ["metadata_lookup_geojsonseq", "tippecanoe_mbtiles", "pmtiles_convert"])
        tippecanoe_argv = plan.commands[1]["argv"]
        self.assertEqual(tippecanoe_argv[0], "tippecanoe")
        self.assertEqual(
            [tippecanoe_argv[index + 1] for index, value in enumerate(tippecanoe_argv) if value == "-y"],
            ["feature_id", "ext_id"],
        )
        self.assertEqual(tippecanoe_argv[-1], plan.lookup_geojsonseq_path)

    def test_pmtiles_validation_requires_decode_for_required_properties(self):
        with tempfile.TemporaryDirectory() as tmp:
            pmtiles = Path(tmp) / "example-asset.pmtiles"
            pmtiles.write_bytes(b"placeholder")

            with (
                mock.patch.object(localized_vector_asset.shutil, "which", return_value=None),
                mock.patch.object(localized_vector_asset.vector_asset, "decoded_pmtiles_property_summary", return_value=None),
            ):
                result = localized_vector_asset.validate_pmtiles_properties(
                    pmtiles_path=pmtiles,
                    required_properties=("feature_id", "ext_id"),
                    pmtiles_bin="pmtiles",
                    profile=None,
                    decode_zoom=0,
                )

        self.assertFalse(result["valid"])
        self.assertIn("could not verify required PMTiles properties", "\n".join(result["errors"]))

    def test_metadata_lookup_feature_keeps_only_lookup_ids(self):
        result = localized_vector_asset.metadata_lookup_feature(
            feature({"feature_id": "src:id:a", "ext_id": "a", "name": "Alpha", "name_es": "Alfa"})
        )

        self.assertEqual(result["properties"], {"feature_id": "src:id:a", "ext_id": "a"})

    def test_build_pmtiles_warns_but_allows_orphaned_localization_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            localizations = root / "example-asset-localizations.csv"
            write_csv(
                localizations,
                [
                    {"ext_id": "a", "name": "Current", "name_review_state": "source_provided"},
                    {"ext_id": "old", "name": "Previous", "name_review_state": "source_provided"},
                ],
            )
            plan = localized_vector_asset.LocalizedPmtilesPlan(
                asset_slug="example-asset",
                layer_name="example_asset",
                fgb_path=str(root / "example-asset.fgb"),
                localization_path=str(localizations),
                output_path=str(root / "example-asset.pmtiles"),
                lookup_geojsonseq_path=str(root / "example-asset-metadata-lookup.ndgeojson"),
                mbtiles_path=str(root / "example-asset.mbtiles"),
                work_dir=str(root),
                profile_path=str(root / "localized-pmtiles-profile.json"),
                minzoom=0,
                maxzoom_mode="manual",
                maxzoom=8,
                source_resolution_meters=None,
                source_scale_denominator=None,
                pmtiles_maxzoom=None,
                pmtiles_maxzoom_reason=None,
                pmtiles_detail_hint=None,
                localized_property_fields=("name",),
                ogr2ogr_bin="ogr2ogr",
                tippecanoe_bin="tippecanoe",
                pmtiles_bin="pmtiles",
                tool_paths={},
                tool_versions={},
                commands=(),
            )
            fgb_profile = localized_vector_asset.FgbKeyProfile(
                path=plan.fgb_path,
                ext_id_field="ext_id",
                row_count=1,
                ext_ids=("a",),
                property_keys=("ext_id",),
                fallback_names={},
                errors=(),
            )

            with (
                mock.patch.object(localized_vector_asset.vector_asset, "require_executable"),
                mock.patch.object(localized_vector_asset, "load_fgb_key_profile", return_value=fgb_profile),
                mock.patch.object(localized_vector_asset, "profile_fgb", return_value=mock.Mock(bounds=None)),
                mock.patch.object(localized_vector_asset, "profile_payload", return_value={"recommendation": {"maxzoom": 8}}),
                mock.patch.object(localized_vector_asset, "run_metadata_lookup_conversion"),
                mock.patch.object(
                    localized_vector_asset,
                    "validate_pmtiles_properties",
                    return_value={"valid": True, "errors": [], "warnings": []},
                ),
            ):
                result = localized_vector_asset.build_pmtiles(plan)

        self.assertTrue(result["validation"]["valid"])
        self.assertIn("not present in the FGB; ignored", "\n".join(result["validation"]["warnings"]))

    def test_draft_publish_plan_supports_translation_only_release_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog.csv"
            catalog.write_text(
                "asset_slug,canonical_path\n"
                "example-asset,gs://example-bucket/100-geographic-reference/110-boundaries/"
                "example-asset/latest/example-asset.fgb\n"
            )

            plan = localized_vector_asset.draft_publish_plan(
                asset_slug="example-asset",
                release_date="2026-05-29",
                proposal_id="pr-123",
                catalog_path=catalog,
                bucket_name="example-bucket",
                translation_only=True,
                source_generation="123",
            )

        destinations = [promotion["destination_uri"] for promotion in plan["promotions"]]
        self.assertNotIn(
            "gs://example-bucket/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb",
            destinations,
        )
        self.assertIn(
            "gs://example-bucket/100-geographic-reference/110-boundaries/example-asset/releases/2026-05-29/example-asset.fgb",
            destinations,
        )
        self.assertIn(
            "gs://example-bucket/100-geographic-reference/110-boundaries/example-asset/latest/example-asset-localizations.csv",
            destinations,
        )
        promotions_by_destination = {promotion["destination_uri"]: promotion for promotion in plan["promotions"]}
        self.assertEqual(
            promotions_by_destination[
                "gs://example-bucket/100-geographic-reference/110-boundaries/example-asset/latest/example-asset-localizations.csv"
            ]["destination_generation"],
            localized_vector_asset.LATEST_DESTINATION_GENERATION_PLACEHOLDER,
        )
        self.assertEqual(
            promotions_by_destination[
                "gs://example-bucket/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.pmtiles"
            ]["destination_generation"],
            localized_vector_asset.LATEST_DESTINATION_GENERATION_PLACEHOLDER,
        )
        self.assertEqual(
            promotions_by_destination[
                "gs://example-bucket/100-geographic-reference/110-boundaries/example-asset/releases/2026-05-29/example-asset.fgb"
            ]["destination_generation"],
            "",
        )

    def test_draft_publish_plan_accepts_latest_destination_generations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog.csv"
            catalog.write_text(
                "asset_slug,canonical_path\n"
                "example-asset,gs://example-bucket/100-geographic-reference/110-boundaries/"
                "example-asset/latest/example-asset.fgb\n"
            )

            plan = localized_vector_asset.draft_publish_plan(
                asset_slug="example-asset",
                release_date="2026-05-29",
                proposal_id="pr-123",
                catalog_path=catalog,
                bucket_name="example-bucket",
                translation_only=False,
                source_generation="123",
                latest_fgb_destination_generation="456",
                latest_localization_destination_generation="789",
                latest_pmtiles_destination_generation="999",
            )

        generations = {promotion["destination_uri"]: promotion["destination_generation"] for promotion in plan["promotions"]}
        self.assertEqual(
            generations["gs://example-bucket/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb"],
            "456",
        )
        self.assertEqual(
            generations["gs://example-bucket/100-geographic-reference/110-boundaries/example-asset/latest/example-asset-localizations.csv"],
            "789",
        )
        self.assertEqual(
            generations["gs://example-bucket/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.pmtiles"],
            "999",
        )

    @unittest.skipUnless(
        os.environ.get("RUN_GDAL_INTEGRATION_TESTS") == "1"
        and shutil.which("ogr2ogr")
        and shutil.which("tippecanoe")
        and shutil.which("tippecanoe-decode")
        and shutil.which("pmtiles"),
        "requires RUN_GDAL_INTEGRATION_TESTS=1, GDAL, Tippecanoe, tippecanoe-decode, and PMTiles binaries",
    )
    def test_build_pmtiles_integration_with_tiny_fgb(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.geojson"
            fgb = root / "example-asset.fgb"
            pmtiles = root / "example-asset.pmtiles"
            localizations = root / "example-asset-localizations.csv"
            source.write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [
                            feature({"feature_id": "src:id:a", "ext_id": "a", "source_name": "Alpha"}),
                            feature({"feature_id": "src:id:b", "ext_id": "b", "source_name": "Beta"}),
                        ],
                    }
                )
            )
            subprocess.run(["ogr2ogr", "-f", "FlatGeobuf", str(fgb), str(source)], check=True)
            write_csv(
                localizations,
                [
                    {
                        "ext_id": "a",
                        "name": "Alpha",
                        "name_review_state": "source_provided",
                        "name_es": "Alfa",
                        "name_es_review_state": "human_reviewed",
                    },
                    {
                        "ext_id": "b",
                        "name": "Beta",
                        "name_review_state": "source_provided",
                        "name_es": "Beta",
                        "name_es_review_state": "machine_translated",
                    },
                ],
            )
            plan = localized_vector_asset.build_pmtiles_plan(
                fgb=fgb,
                localizations=localizations,
                asset_slug="example-asset",
                output=pmtiles,
                work_dir=root / "work",
                pmtiles_detail_hint="coarse",
            )

            result = localized_vector_asset.build_pmtiles(plan)

        self.assertTrue(pmtiles.exists())
        self.assertEqual(result["validation"]["valid"], True)
        self.assertEqual(result["localized_property_fields"], ["name", "name_es"])


if __name__ == "__main__":
    unittest.main()
