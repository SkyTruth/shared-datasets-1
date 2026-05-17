from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import publishing_concierge


CATEGORIES_YAML = """categories:
  "100-geographic-reference":
    subcategories:
      "110-boundaries": "Boundaries."
  "300-infrastructure-industrial":
    subcategories:
      "330-offshore-platforms": "Offshore platforms."
"""


class PublishingConciergeTests(unittest.TestCase):
    def test_plan_infers_vector_build_and_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "source.shp"
            source.write_text("placeholder")

            plan = publishing_concierge.build_plan(
                source=source,
                asset_slug="example-asset",
                title="Example Asset",
                category="100-geographic-reference",
                subcategory="110-boundaries",
                owner="SkyTruth",
                source_name="Example source",
                license_text="Example license",
                citation="Example citation",
                update_cadence="manual",
                canonical_format=None,
                access_tier="public",
                bucket="example-bucket",
                release_date="2026-05-01",
                with_pmtiles=False,
                categories_path=categories,
                docs_dir=root / "docs/assets",
            )

        self.assertEqual(plan.canonical_format, "fgb")
        self.assertEqual(plan.available_formats, ["fgb", "pmtiles"])
        self.assertEqual(plan.asset_root, "100-geographic-reference/110-boundaries/example-asset")
        self.assertEqual(
            plan.canonical_path,
            "gs://example-bucket/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb",
        )
        self.assertTrue(any("vector_asset.py build" in command for command in plan.suggested_commands))
        self.assertTrue(any("--maxzoom auto" in command for command in plan.suggested_commands))
        self.assertTrue(any("publish-release" in command for command in plan.remote_write_commands))
        self.assertEqual(plan.blocking_questions, [])
        self.assertFalse(any("PMTiles is automatic" in note for note in plan.notes))
        self.assertTrue(any("require a PMTiles companion" in note for note in plan.notes))
        self.assertTrue(any("resolved after the canonical FGB" in note for note in plan.notes))
        self.assertTrue(any("shared_datasets_group_id" in note for note in plan.notes))

    def test_missing_source_and_license_are_blocking_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "example.csv"
            source.write_text("id,name\n1,A\n")

            plan = publishing_concierge.build_plan(
                source=source,
                asset_slug=None,
                title=None,
                category="300-infrastructure-industrial",
                subcategory="330-offshore-platforms",
                owner="SkyTruth",
                source_name=None,
                license_text=None,
                citation=None,
                update_cadence="manual",
                canonical_format=None,
                access_tier="public",
                bucket="example-bucket",
                release_date=None,
                with_pmtiles=False,
                categories_path=categories,
                docs_dir=root / "docs/assets",
            )

        self.assertEqual(plan.asset_slug, "example")
        self.assertEqual(plan.canonical_format, "csv")
        self.assertEqual(plan.available_formats, ["csv"])
        self.assertIn("Confirm source name or URL.", plan.blocking_questions)
        self.assertIn("Confirm license or terms.", plan.blocking_questions)
        self.assertIn("Confirm citation for the original source publication.", plan.blocking_questions)
        self.assertTrue(any("geometry-free" in note for note in plan.notes))

    def test_curator_field_options_profile_csv_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "example.csv"
            source.write_text(
                "source_id,NAME,GIS_AREA_K\n"
                "A1,North Reef,10\n"
                "A2,North Reef,11\n"
                "A3,South Reef,12\n"
            )

            plan = publishing_concierge.build_plan(
                source=source,
                asset_slug="example",
                title="Example",
                category="300-infrastructure-industrial",
                subcategory="330-offshore-platforms",
                owner="SkyTruth",
                source_name="Example source",
                license_text="Example license",
                citation="Example citation",
                update_cadence="manual",
                canonical_format=None,
                access_tier="public",
                bucket="example-bucket",
                release_date=None,
                with_pmtiles=False,
                categories_path=categories,
                docs_dir=root / "docs/assets",
            )

        self.assertEqual(plan.curator_field_options.id_field_candidates[0].field, "source_id")
        self.assertEqual(plan.curator_field_options.id_field_candidates[0].confidence, "high")
        self.assertEqual(plan.curator_field_options.group_field_candidates[0].field, "NAME")
        self.assertEqual(plan.curator_field_options.group_field_candidates[0].distinct_values, 2)
        self.assertFalse(plan.curator_field_options.generated_row_id_option.available)
        self.assertFalse(any(candidate.field == "GIS_AREA_K" for candidate in plan.curator_field_options.group_field_candidates))

    def test_field_profile_reports_decision_table_statistics(self):
        options = publishing_concierge.profile_rows(
            [
                {"source_id": "A1", "NAME": "North Reef", "DISC": "-9999"},
                {"source_id": "A2", "NAME": "North Reef", "DISC": "1999"},
                {"source_id": "A3", "NAME": "South Reef", "DISC": ""},
            ]
        )

        self.assertEqual(options.total_rows, 3)
        self.assertEqual(options.total_columns, 3)
        provider = options.id_field_candidates[0]
        self.assertEqual(provider.field, "source_id")
        self.assertEqual(provider.datatype, "string")
        self.assertEqual(provider.distinction_percent, 100.0)
        self.assertEqual(provider.emptiness_percent, 0.0)
        self.assertEqual(provider.domination_percent, 33.33)
        self.assertEqual(provider.skew_ratio, 1.0)
        self.assertEqual(provider.top_examples[0].value, "A1")

        name = options.group_field_candidates[0]
        self.assertEqual(name.field, "NAME")
        self.assertEqual(name.distinction_percent, 66.67)
        self.assertEqual(name.domination_percent, 66.67)
        self.assertEqual(name.skew_ratio, 1.33)
        self.assertTrue(any("top value" in concern for concern in name.concerns))

        disc_profile = next(profile for profile in options.all_fields_profile if profile.name == "DISC")
        self.assertEqual(disc_profile.datatype, "integer")
        self.assertEqual(disc_profile.empty_values, 1)
        self.assertEqual(disc_profile.sentinel_value_count, 1)

    def test_petrodata_like_recommendations_keep_table_compact(self):
        rows = []
        for index in range(102):
            rows.append(
                {
                    "PRIMKEY": "AL001PET" if index in {0, 1, 2} else f"PET{index:03d}",
                    "NAME": "West Siberian Basin" if index < 20 else f"Basin {index % 12}",
                    "COUNTRY": "Russia" if index < 30 else ["United States", "Brazil", "Canada"][index % 3],
                    "RESINFO": ["oil and gas", "gas", "oil"][index % 3],
                    "source_layer": "onshore" if index < 80 else "offshore",
                    "LAT": str(1.0 + index),
                    "LONG": str(2.0 + index),
                    "SOURCEINFO": f"Long reference text {index % 5}",
                }
            )

        options = publishing_concierge.profile_rows(rows)

        provider = options.id_field_candidates[0]
        self.assertEqual(provider.field, "PRIMKEY")
        self.assertEqual(provider.confidence, "high")
        self.assertTrue(any("duplicate value" in concern for concern in provider.concerns))
        group_fields = [candidate.field for candidate in options.group_field_candidates]
        self.assertIn("NAME", group_fields)
        self.assertIn("COUNTRY", group_fields)
        self.assertIn("RESINFO", group_fields)
        self.assertIn("source_layer", group_fields)
        self.assertNotIn("LAT", group_fields)
        self.assertNotIn("LONG", group_fields)
        self.assertNotIn("SOURCEINFO", group_fields)

    def test_coral_like_name_fields_surface_domination_warning(self):
        rows = (
            [{"NAME": "Not Reported", "ORIG_NAME": "Not Reported"} for _ in range(900)]
            + [{"NAME": f"Reef {index}", "ORIG_NAME": f"Original Reef {index}"} for index in range(100)]
        )

        options = publishing_concierge.profile_rows(rows)

        by_field = {candidate.field: candidate for candidate in options.group_field_candidates}
        self.assertIn("NAME", by_field)
        self.assertIn("ORIG_NAME", by_field)
        self.assertGreaterEqual(by_field["NAME"].domination_percent or 0, 80)
        self.assertGreaterEqual(by_field["NAME"].skew_ratio or 0, 25)
        self.assertTrue(any("top value" in concern for concern in by_field["NAME"].concerns))
        self.assertTrue(any(example.value == "Not Reported" and example.is_sentinel for example in by_field["NAME"].top_examples))

    def test_curator_field_options_profiles_full_csv_under_sample_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "example.csv"
            source.write_text("source_id,NAME\nA1,North Reef\nA2,North Reef\n")

            plan = publishing_concierge.build_plan(
                source=source,
                asset_slug="example",
                title="Example",
                category="300-infrastructure-industrial",
                subcategory="330-offshore-platforms",
                owner="SkyTruth",
                source_name="Example source",
                license_text="Example license",
                citation="Example citation",
                update_cadence="manual",
                canonical_format=None,
                access_tier="public",
                bucket="example-bucket",
                release_date=None,
                with_pmtiles=False,
                categories_path=categories,
                docs_dir=root / "docs/assets",
            )

        self.assertEqual(plan.curator_field_options.profile_scope, "full")
        self.assertEqual(plan.curator_field_options.total_rows, 2)
        self.assertEqual(plan.curator_field_options.profiled_row_count, 2)
        self.assertEqual(plan.curator_field_options.id_field_candidates[0].field, "source_id")

    def test_profile_row_iter_uses_deterministic_random_sample_not_first_rows(self):
        rows = [{"source_id": f"A{index}", "NAME": f"Name {index}"} for index in range(25)]

        sample, total_rows, profile_scope = publishing_concierge.profile_row_iter(rows, sample_size=10, random_seed=7)

        self.assertEqual(total_rows, 25)
        self.assertEqual(profile_scope, "random_sample")
        self.assertEqual(len(sample), 10)
        self.assertNotEqual([row["source_id"] for row in sample], [f"A{index}" for index in range(10)])

    def test_curator_field_options_profile_ogr_vector_source_before_group_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "example.fgb"
            source.write_text("placeholder")

            with mock.patch.dict(publishing_concierge.os.environ, {"SHARED_DATASETS_PROFILE_WITH_GDAL": "1"}), mock.patch.object(
                publishing_concierge.shutil,
                "which",
                return_value="/usr/bin/ogr2ogr",
            ), mock.patch.object(
                publishing_concierge.subprocess,
                "run",
                return_value=mock.Mock(
                    returncode=0,
                    stdout="source_id,NAME,GIS_AREA_K\nA1,North Reef,10\nA2,North Reef,11\nA3,South Reef,12\n",
                    stderr="",
                ),
            ) as run:
                plan = publishing_concierge.build_plan(
                    source=source,
                    asset_slug="example",
                    title="Example",
                    category="300-infrastructure-industrial",
                    subcategory="330-offshore-platforms",
                    owner="SkyTruth",
                    source_name="Example source",
                    license_text="Example license",
                    citation="Example citation",
                    update_cadence="manual",
                    canonical_format=None,
                    access_tier="public",
                    bucket="example-bucket",
                    release_date=None,
                    with_pmtiles=False,
                    categories_path=categories,
                    docs_dir=root / "docs/assets",
                )

        self.assertEqual(run.call_args.args[0][:3], ["ogr2ogr", "-f", "CSV"])
        self.assertNotIn("-limit", run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["timeout"], publishing_concierge.OGR_PROFILE_TIMEOUT_SECONDS)
        self.assertEqual(plan.curator_field_options.profile_scope, "full")
        self.assertTrue(plan.curator_field_options.generated_row_id_option.available)
        self.assertEqual(plan.curator_field_options.id_field_candidates[0].field, "source_id")
        self.assertEqual(plan.curator_field_options.group_field_candidates[0].field, "NAME")
        self.assertTrue(any("Curator must choose grouping fields" in note for note in plan.curator_field_options.notes))

    def test_curator_field_options_do_not_profile_ogr_without_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "example.fgb"
            source.write_text("placeholder")

            with mock.patch.dict(publishing_concierge.os.environ, {}, clear=True), mock.patch.object(
                publishing_concierge.subprocess,
                "run",
            ) as run:
                plan = publishing_concierge.build_plan(
                    source=source,
                    asset_slug="example",
                    title="Example",
                    category="300-infrastructure-industrial",
                    subcategory="330-offshore-platforms",
                    owner="SkyTruth",
                    source_name="Example source",
                    license_text="Example license",
                    citation="Example citation",
                    update_cadence="manual",
                    canonical_format=None,
                    access_tier="public",
                    bucket="example-bucket",
                    release_date=None,
                    with_pmtiles=False,
                    categories_path=categories,
                    docs_dir=root / "docs/assets",
                )

        run.assert_not_called()
        self.assertEqual(plan.curator_field_options.profile_scope, "unavailable")
        self.assertTrue(plan.curator_field_options.generated_row_id_option.available)
        self.assertTrue(any("not profiled with GDAL" in note for note in plan.curator_field_options.notes))

    def test_curator_field_options_skip_large_geojson_feature_collection_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "example.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}' + (" " * 32))

            with mock.patch.object(publishing_concierge, "MAX_IN_MEMORY_GEOJSON_BYTES", 16), mock.patch.object(
                publishing_concierge,
                "read_geojson_rows",
            ) as read_rows:
                plan = publishing_concierge.build_plan(
                    source=source,
                    asset_slug="example",
                    title="Example",
                    category="300-infrastructure-industrial",
                    subcategory="330-offshore-platforms",
                    owner="SkyTruth",
                    source_name="Example source",
                    license_text="Example license",
                    citation="Example citation",
                    update_cadence="manual",
                    canonical_format=None,
                    access_tier="public",
                    bucket="example-bucket",
                    release_date=None,
                    with_pmtiles=False,
                    categories_path=categories,
                    docs_dir=root / "docs/assets",
                )

        read_rows.assert_not_called()
        self.assertTrue(any("too large for in-memory" in note for note in plan.curator_field_options.notes))

    def test_existing_fgb_still_uses_vector_build_for_pmtiles_companion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "source.fgb"
            source.write_text("placeholder")

            plan = publishing_concierge.build_plan(
                source=source,
                asset_slug="example-asset",
                title="Example Asset",
                category="100-geographic-reference",
                subcategory="110-boundaries",
                owner="SkyTruth",
                source_name="Example source",
                license_text="Example license",
                citation="Example citation",
                update_cadence="manual",
                canonical_format=None,
                access_tier="public",
                bucket="example-bucket",
                release_date=None,
                with_pmtiles=False,
                categories_path=categories,
                docs_dir=root / "docs/assets",
            )

        self.assertEqual(plan.available_formats, ["fgb", "pmtiles"])
        self.assertTrue(any("vector_asset.py build" in command for command in plan.suggested_commands))
        self.assertTrue(any("--maxzoom auto" in command for command in plan.suggested_commands))
        self.assertFalse(any(" cp " in command for command in plan.suggested_commands))

    def test_write_draft_doc_refuses_existing_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "docs/assets/example.md"
            path.parent.mkdir(parents=True)
            path.write_text("existing")

            with self.assertRaisesRegex(publishing_concierge.ConciergeError, "refusing to overwrite"):
                publishing_concierge.write_draft_doc(path, "draft", overwrite=False)

    def test_main_outputs_json_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "source.shp"
            source.write_text("placeholder")

            exit_code = publishing_concierge.main(
                [
                    str(source),
                    "--asset-slug",
                    "example-asset",
                    "--category",
                    "100-geographic-reference",
                    "--subcategory",
                    "110-boundaries",
                    "--source-name",
                    "Example source",
                    "--license",
                    "Example license",
                    "--citation",
                    "Example citation",
                    "--categories",
                    str(categories),
                    "--docs-dir",
                    str(root / "docs/assets"),
                    "--with-pmtiles",
                    "--release-date",
                    "2026-05-01",
                ]
            )

        self.assertEqual(exit_code, 0)

    def test_draft_asset_doc_contains_access_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "source.shp"
            source.write_text("placeholder")
            plan = publishing_concierge.build_plan(
                source=source,
                asset_slug="example-asset",
                title="Example Asset",
                category="100-geographic-reference",
                subcategory="110-boundaries",
                owner="SkyTruth",
                source_name="Example source",
                license_text="Example license",
                citation="Example citation",
                update_cadence="manual",
                canonical_format=None,
                access_tier="public",
                bucket="example-bucket",
                release_date="2026-05-01",
                with_pmtiles=True,
                categories_path=categories,
                docs_dir=root / "docs/assets",
            )

        text = publishing_concierge.draft_asset_doc(
            plan,
            owner="SkyTruth",
            source_name="Example source",
            license_text="Example license",
            citation="Example citation",
            update_cadence="manual",
            access_tier="public",
        )
        self.assertIn("access_tier: public", text)
        self.assertIn("citation: Example citation", text)
        self.assertIn("latest/example-asset.pmtiles", text)

    def test_pmtiles_hints_are_included_in_vector_command_and_draft_doc(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "source.shp"
            source.write_text("placeholder")
            plan = publishing_concierge.build_plan(
                source=source,
                asset_slug="example-asset",
                title="Example Asset",
                category="100-geographic-reference",
                subcategory="110-boundaries",
                owner="SkyTruth",
                source_name="Example source",
                license_text="Example license",
                citation="Example citation",
                update_cadence="manual",
                canonical_format=None,
                access_tier="public",
                bucket="example-bucket",
                release_date=None,
                with_pmtiles=False,
                source_scale_denominator=10_000_000,
                pmtiles_detail_hint="medium",
                categories_path=categories,
                docs_dir=root / "docs/assets",
            )

        command = next(command for command in plan.suggested_commands if "vector_asset.py build" in command)
        self.assertIn("--source-scale-denominator 10000000", command)
        self.assertIn("--pmtiles-detail-hint medium", command)
        self.assertTrue(any("source/detail hints" in note for note in plan.notes))

        text = publishing_concierge.draft_asset_doc(
            plan,
            owner="SkyTruth",
            source_name="Example source",
            license_text="Example license",
            citation="Example citation",
            update_cadence="manual",
            access_tier="public",
        )
        self.assertIn("source_scale_denominator: 10000000", text)
        self.assertIn("pmtiles_detail_hint: medium", text)


if __name__ == "__main__":
    unittest.main()
