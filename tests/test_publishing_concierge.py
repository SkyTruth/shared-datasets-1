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
        self.assertFalse(any(candidate.field == "GIS_AREA_K" for candidate in plan.curator_field_options.group_field_candidates))

    def test_curator_field_options_limits_csv_profile_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "example.csv"
            source.write_text("source_id,NAME\nA1,North Reef\n")

            with mock.patch.object(
                publishing_concierge,
                "read_csv_rows",
                return_value=[
                    {"source_id": "A1", "NAME": "North Reef"},
                    {"source_id": "A2", "NAME": "North Reef"},
                ],
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

        read_rows.assert_called_once_with(source, limit=publishing_concierge.PROFILE_ROW_LIMIT)
        self.assertEqual(plan.curator_field_options.id_field_candidates[0].field, "source_id")

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
