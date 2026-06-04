from __future__ import annotations

import contextlib
import io
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
    def _write_json(self, path: Path, payload: dict) -> Path:
        path.write_text(json.dumps(payload))
        return path

    def _start_workflow(self, root: Path, *, canonical_format: str | None = "csv", release_date: str | None = None) -> Path:
        categories = root / "categories.yaml"
        categories.write_text(CATEGORIES_YAML)
        source = root / "example.csv"
        source.write_text("source_id,NAME\nA1,North Reef\n")
        args = [
            "start",
            str(source),
            "--asset-slug",
            "example",
            "--title",
            "Example",
            "--category",
            "300-infrastructure-industrial",
            "--subcategory",
            "330-offshore-platforms",
            "--source-name",
            "Example source",
            "--license",
            "Example license",
            "--citation",
            "Example citation",
            "--access-tier",
            "private",
            "--request-classification",
            "canonical-publish",
            "--proposal-id",
            "pr-123",
            "--categories",
            str(categories),
            "--docs-dir",
            str(root / "docs/assets"),
        ]
        if canonical_format:
            args.extend(["--canonical-format", canonical_format])
        if release_date:
            args.extend(["--release-date", release_date])
        with mock.patch.dict(publishing_concierge.os.environ, {"SHARED_DATASETS_WORKDIR": str(root / "work")}):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = publishing_concierge.main(args)
        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        return Path(payload["state_file"])

    def _confirm(self, root: Path, state_file: Path, step: str, payload: dict) -> int:
        evidence = self._write_json(root / f"{step}.json", payload)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return publishing_concierge.main(
                [
                    "confirm",
                    "--state-file",
                    str(state_file),
                    "--step",
                    step,
                    "--evidence-json",
                    str(evidence),
                ]
            )

    def _complete_first_csv_workflow_through_pr_ready(self, root: Path, state_file: Path) -> None:
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "resolve-metadata",
                {
                    "source_name": "Example source",
                    "license": "Example license",
                    "citation": "Example citation",
                    "steward": "Data Steward",
                    "source_version_date": "2026-05-01",
                    "update_cadence": "manual",
                    "intended_consumers": ["test"],
                    "shared_datasets_rationale": "Reusable reference table for multiple projects.",
                    "alternatives_considered": "Project storage and direct upstream access.",
                    "deprecation_exit_policy": "Deprecate with a successor if source support ends.",
                    "estimated_published_footprint": "1 MB",
                },
            ),
            0,
        )
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "settle-contract",
                {
                    "confirmed_asset_slug": "example",
                    "confirmed_category": "300-infrastructure-industrial",
                    "confirmed_subcategory": "330-offshore-platforms",
                    "confirmed_canonical_format": "csv",
                    "release_layout": "latest-only",
                    "access_tier": "private",
                    "exception_flags": {
                        "public_access_approved": False,
                        "new_top_level_category_approved": False,
                        "new_canonical_format_approved": False,
                        "large_data_exception_approved": False,
                        "incompatible_schema_change_approved": False,
                        "move_or_delete_releases_approved": False,
                        "unsafe_overwrite_approved": False,
                        "infrastructure_mutation_approved": False,
                    },
                },
            ),
            0,
        )
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "profile-fields",
                {
                    "decision_table_present": True,
                    "profile_scope": "full",
                    "provider_id_decision": "use-provider-id",
                    "provider_id_fields": ["source_id"],
                    "generated_group_id_decision": "not-needed",
                    "group_id_fields": [],
                    "generated_row_id_decision": "not-needed",
                    "search_fields": ["NAME"],
                },
            ),
            0,
        )
        artifact = root / "work/vector-assets/example/publish/example.csv"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("source_id,NAME\nA1,North Reef\n")
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "build-artifacts",
                {"artifacts": [{"path": str(artifact), "format": "csv", "role": "canonical"}]},
            ),
            0,
        )
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "validate-artifacts",
                {
                    "commands_run": ["csv validation"],
                    "validation_summary": "CSV is geometry-free and row count matches.",
                    "all_passed": True,
                    "tool_versions": {"csv-validator": "not applicable; inspected with Python csv"},
                },
            ),
            0,
        )
        doc = root / "docs/assets/example.md"
        doc.parent.mkdir(parents=True)
        doc.write_text(
            "---\n"
            "asset_slug: example\n"
            "title: Example\n"
            "category: 300-infrastructure-industrial\n"
            "subcategory: 330-offshore-platforms\n"
            "status: active\n"
            "access_tier: private\n"
            "owner: SkyTruth\n"
            "update_cadence: manual\n"
            "canonical_format: csv\n"
            "canonical_file: latest/example.csv\n"
            "available_formats: [csv]\n"
            "metadata_paths: [README.md]\n"
            "source: Example source\n"
            "license: Example license\n"
            "citation: Example citation\n"
            "---\n\n# Example\n"
        )
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "document-asset",
                {
                    "asset_doc_path": str(doc),
                    "admission_complete": True,
                    "source_license_citation_complete": True,
                    "schema_or_properties_complete": True,
                    "data_profile_complete": True,
                },
            ),
            0,
        )
        readmes = root / "work/readmes"
        readmes.mkdir(parents=True)
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "catalog-outputs",
                {
                    "generate_ran": True,
                    "check_passed": True,
                    "readmes_exported": True,
                    "readmes_dir": str(readmes),
                },
            ),
            0,
        )
        catalog_json = root / "work/catalog-web/catalog.json"
        catalog_json.parent.mkdir(parents=True)
        catalog_json.write_text("{}")
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "catalog-web",
                {
                    "built": True,
                    "catalog_json_path": str(catalog_json),
                    "content_type": "application/json",
                    "cache_control": "no-cache, max-age=0, must-revalidate",
                },
            ),
            0,
        )
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "stage-scratch",
                {
                    "staged_objects": [
                        {
                            "source_uri": "gs://skytruth-shared-datasets-1/_scratch/pending-publishes/example/pr-123/example.csv",
                            "source_generation": "111",
                            "destination_uri": "gs://skytruth-shared-datasets-1/300-infrastructure-industrial/330-offshore-platforms/example/latest/example.csv",
                            "content_type": "text/csv",
                            "cache_control": "",
                        },
                        {
                            "source_uri": "gs://skytruth-shared-datasets-1/_scratch/pending-publishes/example/pr-123/catalog.json",
                            "source_generation": "112",
                            "destination_uri": "gs://skytruth-shared-datasets-1/_catalog/web/catalog.json",
                            "content_type": "application/json",
                            "cache_control": "no-cache, max-age=0, must-revalidate",
                        },
                    ]
                },
            ),
            0,
        )
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "stat-destinations",
                {
                    "destinations": [
                        {
                            "destination_uri": "gs://skytruth-shared-datasets-1/300-infrastructure-industrial/330-offshore-platforms/example/latest/example.csv",
                            "destination_generation": "",
                            "status": "absent",
                        },
                        {
                            "destination_uri": "gs://skytruth-shared-datasets-1/_catalog/web/catalog.json",
                            "destination_generation": "222",
                            "status": "exists",
                        },
                    ]
                },
            ),
            0,
        )

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

    def test_start_creates_default_state_file_under_temp_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(root)

            self.assertTrue(state_file.exists())
            self.assertTrue(str(state_file).startswith(str(root / "work")))
            state = json.loads(state_file.read_text())
            self.assertEqual(state["schema_version"], publishing_concierge.WORKFLOW_SCHEMA_VERSION)
            self.assertEqual(state["workflow_type"], "first-upload")
            self.assertEqual(state["plan"]["asset_slug"], "example")
            self.assertEqual(state["steps"]["resolve-metadata"]["status"], "pending")

    def test_start_requires_canonical_publish_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "example.csv"
            source.write_text("id,name\n1,A\n")

            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = publishing_concierge.main(
                    [
                        "start",
                        str(source),
                        "--asset-slug",
                        "example",
                        "--category",
                        "300-infrastructure-industrial",
                        "--subcategory",
                        "330-offshore-platforms",
                        "--request-classification",
                        "preview-only",
                        "--proposal-id",
                        "pr-123",
                        "--categories",
                        str(categories),
                        "--docs-dir",
                        str(root / "docs/assets"),
                    ]
                )

            self.assertEqual(code, 2)

    def test_start_blocks_duplicate_first_upload_asset_doc(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "example.csv"
            source.write_text("id,name\n1,A\n")
            docs_dir = root / "docs/assets"
            docs_dir.mkdir(parents=True)
            (docs_dir / "example.md").write_text("existing")

            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = publishing_concierge.main(
                    [
                        "start",
                        str(source),
                        "--asset-slug",
                        "example",
                        "--category",
                        "300-infrastructure-industrial",
                        "--subcategory",
                        "330-offshore-platforms",
                        "--request-classification",
                        "canonical-publish",
                        "--proposal-id",
                        "pr-123",
                        "--categories",
                        str(categories),
                        "--docs-dir",
                        str(docs_dir),
                    ]
                )

            self.assertEqual(code, 2)

    def test_next_waits_on_same_step_until_valid_evidence_is_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(root)

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = publishing_concierge.main(["next", "--state-file", str(state_file), "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["step_id"], "resolve-metadata")

            bad_evidence = self._write_json(root / "bad.json", {"source_name": "Only source"})
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                bad_code = publishing_concierge.main(
                    ["confirm", "--state-file", str(state_file), "--step", "resolve-metadata", "--evidence-json", str(bad_evidence)]
                )
            self.assertEqual(bad_code, 2)

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = publishing_concierge.main(["next", "--state-file", str(state_file), "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["step_id"], "resolve-metadata")

    def test_cannot_confirm_later_step_before_current_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(root)
            evidence = self._write_json(root / "contract.json", {})

            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = publishing_concierge.main(
                    ["confirm", "--state-file", str(state_file), "--step", "settle-contract", "--evidence-json", str(evidence)]
                )

            self.assertEqual(code, 2)
            state = json.loads(state_file.read_text())
            self.assertEqual(state["steps"]["settle-contract"]["status"], "pending")

    def test_generated_id_decisions_require_explicit_profile_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(root)
            self.assertEqual(
                self._confirm(
                    root,
                    state_file,
                    "resolve-metadata",
                    {
                        "source_name": "Example source",
                        "license": "Example license",
                        "citation": "Example citation",
                        "steward": "Data Steward",
                        "source_version_date": "2026-05-01",
                        "update_cadence": "manual",
                        "intended_consumers": ["test"],
                        "shared_datasets_rationale": "Reusable reference table for multiple projects.",
                        "alternatives_considered": "Project storage.",
                        "deprecation_exit_policy": "Deprecate with a successor.",
                        "estimated_published_footprint": "1 MB",
                    },
                ),
                0,
            )
            self.assertEqual(
                self._confirm(
                    root,
                    state_file,
                    "settle-contract",
                    {
                        "confirmed_asset_slug": "example",
                        "confirmed_category": "300-infrastructure-industrial",
                        "confirmed_subcategory": "330-offshore-platforms",
                        "confirmed_canonical_format": "csv",
                        "release_layout": "latest-only",
                        "access_tier": "private",
                        "exception_flags": {
                            "public_access_approved": False,
                            "new_top_level_category_approved": False,
                            "new_canonical_format_approved": False,
                            "large_data_exception_approved": False,
                            "incompatible_schema_change_approved": False,
                            "move_or_delete_releases_approved": False,
                            "unsafe_overwrite_approved": False,
                            "infrastructure_mutation_approved": False,
                        },
                    },
                ),
                0,
            )

            code = self._confirm(
                root,
                state_file,
                "profile-fields",
                {
                    "decision_table_present": True,
                    "profile_scope": "full",
                    "provider_id_decision": "use-provider-id",
                    "provider_id_fields": ["source_id"],
                    "generated_group_id_decision": "approved",
                    "group_id_fields": ["NAME"],
                    "generated_row_id_decision": "not-needed",
                    "search_fields": ["NAME"],
                },
            )

            self.assertEqual(code, 2)

    def test_release_vector_requires_translation_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(root, canonical_format="fgb", release_date="2026-05-01")
            state = json.loads(state_file.read_text())
            self.assertTrue(publishing_concierge.translation_decision_required(state))

    def test_large_data_exception_is_required_when_footprint_is_at_least_10gb(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(root)
            self.assertEqual(
                self._confirm(
                    root,
                    state_file,
                    "resolve-metadata",
                    {
                        "source_name": "Example source",
                        "license": "Example license",
                        "citation": "Example citation",
                        "steward": "Data Steward",
                        "source_version_date": "2026-05-01",
                        "update_cadence": "manual",
                        "intended_consumers": ["test"],
                        "shared_datasets_rationale": "Reusable reference table for multiple projects.",
                        "alternatives_considered": "Project storage.",
                        "deprecation_exit_policy": "Deprecate with a successor.",
                        "estimated_published_footprint": "12 GB",
                    },
                ),
                0,
            )

            code = self._confirm(
                root,
                state_file,
                "settle-contract",
                {
                    "confirmed_asset_slug": "example",
                    "confirmed_category": "300-infrastructure-industrial",
                    "confirmed_subcategory": "330-offshore-platforms",
                    "confirmed_canonical_format": "csv",
                    "release_layout": "latest-only",
                    "access_tier": "private",
                    "exception_flags": {
                        "public_access_approved": False,
                        "new_top_level_category_approved": False,
                        "new_canonical_format_approved": False,
                        "large_data_exception_approved": False,
                        "incompatible_schema_change_approved": False,
                        "move_or_delete_releases_approved": False,
                        "unsafe_overwrite_approved": False,
                        "infrastructure_mutation_approved": False,
                    },
                },
            )

            self.assertEqual(code, 2)

    def test_artifact_validation_failure_blocks_advancement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(root)

            # Move to build-artifacts.
            self.assertEqual(
                self._confirm(
                    root,
                    state_file,
                    "resolve-metadata",
                    {
                        "source_name": "Example source",
                        "license": "Example license",
                        "citation": "Example citation",
                        "steward": "Data Steward",
                        "source_version_date": "2026-05-01",
                        "update_cadence": "manual",
                        "intended_consumers": ["test"],
                        "shared_datasets_rationale": "Reusable reference table.",
                        "alternatives_considered": "Project storage.",
                        "deprecation_exit_policy": "Deprecate with a successor.",
                        "estimated_published_footprint": "1 MB",
                    },
                ),
                0,
            )
            self.assertEqual(
                self._confirm(
                    root,
                    state_file,
                    "settle-contract",
                    {
                        "confirmed_asset_slug": "example",
                        "confirmed_category": "300-infrastructure-industrial",
                        "confirmed_subcategory": "330-offshore-platforms",
                        "confirmed_canonical_format": "csv",
                        "release_layout": "latest-only",
                        "access_tier": "private",
                        "exception_flags": {
                            "public_access_approved": False,
                            "new_top_level_category_approved": False,
                            "new_canonical_format_approved": False,
                            "large_data_exception_approved": False,
                            "incompatible_schema_change_approved": False,
                            "move_or_delete_releases_approved": False,
                            "unsafe_overwrite_approved": False,
                            "infrastructure_mutation_approved": False,
                        },
                    },
                ),
                0,
            )
            self.assertEqual(
                self._confirm(
                    root,
                    state_file,
                    "profile-fields",
                    {
                        "decision_table_present": True,
                        "profile_scope": "full",
                        "provider_id_decision": "use-provider-id",
                        "provider_id_fields": ["source_id"],
                        "generated_group_id_decision": "not-needed",
                        "group_id_fields": [],
                        "generated_row_id_decision": "not-needed",
                        "search_fields": ["NAME"],
                    },
                ),
                0,
            )

            code = self._confirm(
                root,
                state_file,
                "build-artifacts",
                {"artifacts": [{"path": str(root / "missing.csv"), "format": "csv", "role": "canonical"}]},
            )

            self.assertEqual(code, 2)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                next_code = publishing_concierge.main(["next", "--state-file", str(state_file), "--json"])
            self.assertEqual(next_code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["step_id"], "build-artifacts")

    def test_render_pr_uses_reviewed_publish_plan_validator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(root)
            self._complete_first_csv_workflow_through_pr_ready(root, state_file)

            self.assertEqual(
                self._confirm(root, state_file, "pr-ready", {"reviewed_pr_body": True}),
                0,
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = publishing_concierge.main(["render-pr", "--state-file", str(state_file)])

            self.assertEqual(code, 0)
            body = stdout.getvalue()
            self.assertIn("```shared-datasets-publish-plan", body)
            self.assertIn("_catalog/web/catalog.json", body)

    def test_render_report_outputs_completion_scaffold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(root)
            self._complete_first_csv_workflow_through_pr_ready(root, state_file)
            self.assertEqual(
                self._confirm(root, state_file, "pr-ready", {"reviewed_pr_body": True}),
                0,
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = publishing_concierge.main(["render-report", "--state-file", str(state_file)])

            self.assertEqual(code, 0)
            report = stdout.getvalue()
            self.assertIn("## Completion Report", report)
            self.assertIn("## Remote Paths", report)

    def test_yes_cannot_skip_evidence_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(root)

            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = publishing_concierge.main(
                    ["confirm", "--state-file", str(state_file), "--step", "resolve-metadata", "--yes"]
                )

            self.assertEqual(code, 2)

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
