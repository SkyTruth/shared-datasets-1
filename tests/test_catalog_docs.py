from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import catalog_docs


CATEGORIES_YAML = """categories:
  "100-geographic-reference":
    subcategories:
      "110-boundaries": "Boundaries."
  "700-non-geographic-reference":
    subcategories:
      "730-units-codes-lookups": "Lookups."
"""


STRICT_DOC = """---
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
source_url: https://example.test/source
license: Example license
citation: Example citation
license_flags:
- attribution-required
notes: Example notes
admission:
  intended_consumers:
  - Monitor
  shared_rationale: Shared reference layer for repeatable analysis.
  steward: SkyTruth data team
  update_expectations: Manual refresh when the source publishes a material update.
  estimated_published_size_gb: 1.5
  large_data_exception: ""
  alternatives_considered: Project bucket and direct upstream access.
  deprecation_policy: Keep existing releases and point users to any successor asset.
bounds:
- -10.5
- 20.25
- 30.75
- 40.125
geometry_type: Polygon
row_count: 12345
data_profile:
  field_count: 8
  identity_candidates:
  - field: source_id
    distinct_values: 12345
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: unique
search_fields:
- field: source_name
  distinct_values: 345
  notes: Useful human-readable filter
localized_names:
  property_template: name_{locale_code}
  locale_code_format: bcp47_field_safe
  fallback_locale: en
  fallback_field: name_en
  translations:
  - locale_code: en
    field: name_en
    label: English
    review_state: source_provided
  - locale_code: es
    field: name_es
    label: Spanish
    review_state: machine_translated
generated_group_id:
  column: shared_datasets_group_id
  algorithm: shared-datasets-group-id:v1
  grouping_fields:
  - source_name
  token_length: 8
  group_count: 345
  blank_group_count: 0
  stability: Geometry-addressed ID; source-name changes do not change IDs, but material geometry changes do.
files:
- path: latest/example-asset.fgb
  format: fgb
  role: canonical
  purpose: Canonical vector file
- path: latest/example-asset.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map tiles
---

# Example Asset

Legacy summary.

## What this is

Example.

## Files

Legacy files.

## Schema notes

Example schema.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `id` | integer | Identifier. |
| `name_en` | string | English display name. |
| `name_es` | string | Spanish display name. |

## Update notes

Manual.
"""


LEGACY_DOC = """---
asset_slug: example-asset
title: Example Asset
category: 100-geographic-reference
subcategory: 110-boundaries
status: active
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
source: Example source
license: Example license
citation: Example citation
---

# Example Asset

**Status:** active

## What this is

Example.

## Files

| File | Purpose |
|---|---|
| `latest/example-asset.fgb` | Canonical vector file |
| `latest/example-asset.pmtiles` | Web map tiles |

## Schema notes

Example schema.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `id` | integer | Identifier. |

## Update notes

Manual.
"""


CATALOG_CSV = """asset_slug,title,category,subcategory,status,lifecycle_reason,lifecycle_date,successor_asset_slug,consumer_guidance,access_tier,owner,update_cadence,canonical_path,canonical_format,available_formats,metadata_paths,localized_name_locales,localized_name_review_states,has_pmtiles,has_geojson,has_csv,source,license,citation,notes
example-asset,Example Asset,100-geographic-reference,110-boundaries,active,,,,,public,SkyTruth,manual,gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb,fgb,fgb;pmtiles,README.md,en;es,en:source_provided;es:machine_translated,true,false,false,Example source,Example license,Example citation,Example notes
"""


def write_fixture_tree(root: Path, doc_text: str = STRICT_DOC) -> tuple[Path, Path, Path, Path]:
    docs_dir = root / "docs/assets"
    docs_dir.mkdir(parents=True)
    (docs_dir / "example-asset.md").write_text(doc_text)
    catalog_path = root / "catalog/shared-datasets-catalog.csv"
    catalog_path.parent.mkdir()
    catalog_path.write_text(CATALOG_CSV)
    categories_path = root / "catalog/categories.yaml"
    categories_path.write_text(CATEGORIES_YAML)
    index_path = docs_dir / "index.md"
    return docs_dir, catalog_path, categories_path, index_path


class CatalogDocsTests(unittest.TestCase):
    def test_strict_doc_generates_catalog_row_and_managed_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir, catalog_path, categories_path, _ = write_fixture_tree(Path(tmp))
            categories = catalog_docs.load_categories(categories_path)
            rows = catalog_docs.load_catalog_rows(catalog_path)

            docs = catalog_docs.read_asset_docs(
                docs_dir=docs_dir,
                categories=categories,
                catalog_rows=rows,
                allow_legacy=False,
            )

        row = catalog_docs.catalog_row(docs[0].metadata, "example-bucket")
        rendered = catalog_docs.render_asset_doc(docs[0])
        self.assertEqual(row["canonical_path"], "gs://example-bucket/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb")
        self.assertEqual(row["access_tier"], "public")
        self.assertEqual(row["citation"], "Example citation")
        self.assertEqual(row["lifecycle_reason"], "")
        self.assertEqual(row["localized_name_locales"], "en;es")
        self.assertEqual(row["localized_name_review_states"], "en:source_provided;es:machine_translated")
        self.assertEqual(row["has_pmtiles"], "true")
        self.assertNotIn("admission", row)
        self.assertEqual(docs[0].metadata["admission"]["steward"], "SkyTruth data team")
        self.assertIn("<!-- BEGIN GENERATED asset-summary -->", rendered)
        self.assertIn("- **Access tier:** public", rendered)
        self.assertIn("- **Citation:** Example citation", rendered)
        self.assertIn("admission:", rendered)
        self.assertIn("shared_rationale: Shared reference layer for repeatable analysis.", rendered)
        self.assertIn("source_url: https://example.test/source", rendered)
        self.assertIn("geometry_type: Polygon", rendered)
        self.assertIn("row_count: 12345", rendered)
        self.assertIn("search_fields:", rendered)
        self.assertIn("localized_names:", rendered)
        self.assertIn("fallback_field: name_en", rendered)
        self.assertIn("generated_group_id:", rendered)
        self.assertIn("| `latest/example-asset.pmtiles` | `pmtiles` | `companion` | Web map tiles |", rendered)

    def test_localized_names_metadata_is_validated(self):
        metadata = {
            "property_template": "name_{locale_code}",
            "locale_code_format": "bcp47_field_safe",
            "fallback_locale": "en",
            "fallback_field": "name_en",
            "translations": [
                {"locale_code": "en", "field": "name_en", "label": "English", "review_state": "source_provided"},
                {
                    "locale_code": "pt_br",
                    "field": "name_pt_br",
                    "label": "Brazilian Portuguese",
                    "review_state": "human_reviewed",
                },
            ],
        }

        normalized = catalog_docs.normalize_localized_names(metadata, path=Path("docs/assets/example.md"))

        self.assertEqual(catalog_docs.localized_name_locales({"localized_names": normalized}), ["en", "pt_br"])
        self.assertEqual(
            catalog_docs.localized_name_review_states({"localized_names": normalized}),
            ["en:source_provided", "pt_br:human_reviewed"],
        )
        bad_locale = dict(metadata)
        bad_locale["translations"] = [{"locale_code": "pt-BR", "field": "name_pt_br", "review_state": "machine_translated"}]
        with self.assertRaisesRegex(catalog_docs.CatalogDocsError, "field-safe BCP 47"):
            catalog_docs.normalize_localized_names(bad_locale, path=Path("docs/assets/example.md"))
        bad_field = dict(metadata)
        bad_field["translations"] = [{"locale_code": "es", "field": "spanish_name", "review_state": "machine_translated"}]
        with self.assertRaisesRegex(catalog_docs.CatalogDocsError, "name_es"):
            catalog_docs.normalize_localized_names(bad_field, path=Path("docs/assets/example.md"))
        duplicate_locale = dict(metadata)
        duplicate_locale["translations"] = [
            {"locale_code": "en", "field": "name_en", "review_state": "source_provided"},
            {"locale_code": "en", "field": "name_en", "review_state": "machine_translated"},
        ]
        with self.assertRaisesRegex(catalog_docs.CatalogDocsError, "duplicated"):
            catalog_docs.normalize_localized_names(duplicate_locale, path=Path("docs/assets/example.md"))
        bad_fallback = dict(metadata)
        bad_fallback["fallback_field"] = "name_fr"
        with self.assertRaisesRegex(catalog_docs.CatalogDocsError, "fallback_field"):
            catalog_docs.normalize_localized_names(bad_fallback, path=Path("docs/assets/example.md"))
        missing_review_state = dict(metadata)
        missing_review_state["translations"] = [{"locale_code": "fr", "field": "name_fr"}]
        with self.assertRaisesRegex(catalog_docs.CatalogDocsError, "review_state is required"):
            catalog_docs.normalize_localized_names(missing_review_state, path=Path("docs/assets/example.md"))
        bad_review_state = dict(metadata)
        bad_review_state["translations"] = [{"locale_code": "fr", "field": "name_fr", "review_state": "draft"}]
        with self.assertRaisesRegex(catalog_docs.CatalogDocsError, "review_state must be one of"):
            catalog_docs.normalize_localized_names(bad_review_state, path=Path("docs/assets/example.md"))

    def test_generated_row_id_metadata_is_validated(self):
        metadata = {
            "column": "shared_datasets_row_id",
            "algorithm": "shared-datasets-row-id:v1",
            "token_length": 8,
            "row_count": 123,
            "duplicate_geometry_digest_count": 2,
            "duplicate_geometry_row_count": 4,
            "stability": "Stable while canonical geometry and duplicate-geometry source order stay unchanged.",
            "warning": "Last-resort row address; not a provider/entity/group ID.",
        }

        catalog_docs.validate_generated_row_id(metadata, path=Path("docs/assets/example.md"), row_count=123)

        bad = dict(metadata)
        bad["warning"] = ""
        with self.assertRaisesRegex(catalog_docs.CatalogDocsError, "generated_row_id.warning"):
            catalog_docs.validate_generated_row_id(bad, path=Path("docs/assets/example.md"), row_count=123)

    def test_generated_group_and_row_id_metadata_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = STRICT_DOC.replace(
                "files:\n",
                """generated_row_id:
  column: shared_datasets_row_id
  algorithm: shared-datasets-row-id:v1
  token_length: 8
  row_count: 12345
  stability: Stable while canonical geometry and duplicate-geometry source order stay unchanged.
  warning: Last-resort row address; not a provider/entity/group ID.
files:\n""",
            )
            docs_dir, catalog_path, categories_path, _ = write_fixture_tree(Path(tmp), doc)
            categories = catalog_docs.load_categories(categories_path)
            rows = catalog_docs.load_catalog_rows(catalog_path)

            with self.assertRaisesRegex(catalog_docs.CatalogDocsError, "mutually exclusive"):
                catalog_docs.read_asset_docs(
                    docs_dir=docs_dir,
                    categories=categories,
                    catalog_rows=rows,
                    allow_legacy=False,
                )

    def test_legacy_generate_backfills_frontmatter_from_catalog_and_files_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir, catalog_path, categories_path, _ = write_fixture_tree(Path(tmp), LEGACY_DOC)
            categories = catalog_docs.load_categories(categories_path)
            rows = catalog_docs.load_catalog_rows(catalog_path)

            docs = catalog_docs.read_asset_docs(
                docs_dir=docs_dir,
                categories=categories,
                catalog_rows=rows,
                allow_legacy=True,
            )

        metadata = docs[0].metadata
        self.assertEqual(metadata["schema_version"], 1)
        self.assertEqual(metadata["access_tier"], "public")
        self.assertEqual(metadata["canonical_file"], "latest/example-asset.fgb")
        self.assertEqual(metadata["available_formats"], ["fgb", "pmtiles"])
        self.assertEqual(metadata["files"][1]["role"], "companion")

    def test_accepts_deprecated_asset_with_lifecycle_guidance(self):
        doc_text = STRICT_DOC.replace("status: active\n", "status: deprecated\n").replace(
            "notes: Example notes\n",
            (
                "notes: Example notes\n"
                "lifecycle_reason: Source is no longer maintained.\n"
                "lifecycle_date: 2026-05-08\n"
                "consumer_guidance: Prefer a maintained replacement for new work.\n"
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir, catalog_path, categories_path, _ = write_fixture_tree(Path(tmp), doc_text)
            categories = catalog_docs.load_categories(categories_path)
            rows = catalog_docs.load_catalog_rows(catalog_path)

            docs = catalog_docs.read_asset_docs(
                docs_dir=docs_dir,
                categories=categories,
                catalog_rows=rows,
                allow_legacy=False,
            )

        row = catalog_docs.catalog_row(docs[0].metadata, "example-bucket")
        rendered = catalog_docs.render_asset_doc(docs[0])
        self.assertEqual(row["status"], "deprecated")
        self.assertEqual(row["lifecycle_date"], "2026-05-08")
        self.assertIn("- **Lifecycle reason:** Source is no longer maintained.", rendered)

    def test_accepts_all_lifecycle_statuses(self):
        lifecycle_fields = (
            "lifecycle_reason: Governance status changed.\n"
            "lifecycle_date: 2026-05-08\n"
            "consumer_guidance: Check status before new use.\n"
        )
        cases = {
            "active": "",
            "deprecated": lifecycle_fields,
            "retired": lifecycle_fields,
            "superseded": lifecycle_fields + "successor_asset_slug: replacement-asset\n",
        }
        for status, extra_fields in cases.items():
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                doc_text = STRICT_DOC.replace("status: active\n", f"status: {status}\n")
                if extra_fields:
                    doc_text = doc_text.replace("notes: Example notes\n", "notes: Example notes\n" + extra_fields)
                docs_dir, catalog_path, categories_path, _ = write_fixture_tree(Path(tmp), doc_text)
                categories = catalog_docs.load_categories(categories_path)
                rows = catalog_docs.load_catalog_rows(catalog_path)

                docs = catalog_docs.read_asset_docs(
                    docs_dir=docs_dir,
                    categories=categories,
                    catalog_rows=rows,
                    allow_legacy=False,
                )

                self.assertEqual(docs[0].metadata["status"], status)

    def test_rejects_unknown_lifecycle_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir, catalog_path, categories_path, _ = write_fixture_tree(
                Path(tmp),
                STRICT_DOC.replace("status: active\n", "status: scratch\n"),
            )
            categories = catalog_docs.load_categories(categories_path)
            rows = catalog_docs.load_catalog_rows(catalog_path)

            with self.assertRaisesRegex(catalog_docs.CatalogDocsError, "status must be one of"):
                catalog_docs.read_asset_docs(
                    docs_dir=docs_dir,
                    categories=categories,
                    catalog_rows=rows,
                    allow_legacy=False,
                )

    def test_non_active_assets_require_lifecycle_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir, catalog_path, categories_path, _ = write_fixture_tree(
                Path(tmp),
                STRICT_DOC.replace("status: active\n", "status: retired\n"),
            )
            categories = catalog_docs.load_categories(categories_path)
            rows = catalog_docs.load_catalog_rows(catalog_path)

            with self.assertRaisesRegex(catalog_docs.CatalogDocsError, "non-active assets require"):
                catalog_docs.read_asset_docs(
                    docs_dir=docs_dir,
                    categories=categories,
                    catalog_rows=rows,
                    allow_legacy=False,
                )

    def test_superseded_assets_require_successor_slug(self):
        doc_text = STRICT_DOC.replace("status: active\n", "status: superseded\n").replace(
            "notes: Example notes\n",
            (
                "notes: Example notes\n"
                "lifecycle_reason: Replaced by normalized successor.\n"
                "lifecycle_date: 2026-05-08\n"
                "consumer_guidance: Use the successor for new work.\n"
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir, catalog_path, categories_path, _ = write_fixture_tree(Path(tmp), doc_text)
            categories = catalog_docs.load_categories(categories_path)
            rows = catalog_docs.load_catalog_rows(catalog_path)

            with self.assertRaisesRegex(catalog_docs.CatalogDocsError, "successor_asset_slug"):
                catalog_docs.read_asset_docs(
                    docs_dir=docs_dir,
                    categories=categories,
                    catalog_rows=rows,
                    allow_legacy=False,
                )

    def test_check_detects_stale_generated_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir, catalog_path, categories_path, index_path = write_fixture_tree(Path(tmp))
            categories = catalog_docs.load_categories(categories_path)
            rows = catalog_docs.load_catalog_rows(catalog_path)
            docs = catalog_docs.read_asset_docs(
                docs_dir=docs_dir,
                categories=categories,
                catalog_rows=rows,
                allow_legacy=False,
            )
            catalog_docs.generate_outputs(docs=docs, catalog_path=catalog_path, index_path=index_path, bucket="skytruth-shared-datasets-1")
            catalog_path.write_text(catalog_path.read_text().replace("Example Asset", "Stale Asset", 1))

            errors, warnings = catalog_docs.check_outputs(
                docs=docs,
                catalog_path=catalog_path,
                index_path=index_path,
                bucket="skytruth-shared-datasets-1",
            )

        self.assertEqual(warnings, [])
        self.assertTrue(any("generated content is stale" in error for error in errors))

    def test_invalid_taxonomy_fails(self):
        bad_doc = STRICT_DOC.replace("subcategory: 110-boundaries", "subcategory: 999-missing")
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir, catalog_path, categories_path, _ = write_fixture_tree(Path(tmp), bad_doc)
            categories = catalog_docs.load_categories(categories_path)
            rows = catalog_docs.load_catalog_rows(catalog_path)

            with self.assertRaises(catalog_docs.CatalogDocsError):
                catalog_docs.read_asset_docs(
                    docs_dir=docs_dir,
                    categories=categories,
                    catalog_rows=rows,
                    allow_legacy=False,
                )

    def test_invalid_access_tier_fails(self):
        bad_doc = STRICT_DOC.replace("access_tier: public", "access_tier: internal")
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir, catalog_path, categories_path, _ = write_fixture_tree(Path(tmp), bad_doc)
            categories = catalog_docs.load_categories(categories_path)
            rows = catalog_docs.load_catalog_rows(catalog_path)

            with self.assertRaisesRegex(catalog_docs.CatalogDocsError, "access_tier"):
                catalog_docs.read_asset_docs(
                    docs_dir=docs_dir,
                    categories=categories,
                    catalog_rows=rows,
                    allow_legacy=False,
                )

    def test_invalid_discovery_bounds_fail(self):
        bad_doc = STRICT_DOC.replace("- 40.125", "- 200.0", 1)
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir, catalog_path, categories_path, _ = write_fixture_tree(Path(tmp), bad_doc)
            categories = catalog_docs.load_categories(categories_path)
            rows = catalog_docs.load_catalog_rows(catalog_path)

            with self.assertRaisesRegex(catalog_docs.CatalogDocsError, "bounds"):
                catalog_docs.read_asset_docs(
                    docs_dir=docs_dir,
                    categories=categories,
                    catalog_rows=rows,
                    allow_legacy=False,
                )

    def test_data_profile_requires_field_count(self):
        profile = (
            "data_profile:\n"
            "  identity_candidates: []\n"
            "  notes: No documented ext_id candidate\n"
        )
        bad_doc = STRICT_DOC.replace(
            "data_profile:\n"
            "  field_count: 8\n"
            "  identity_candidates:\n"
            "  - field: source_id\n"
            "    distinct_values: 12345\n"
            "    duplicate_value_count: 0\n"
            "    duplicate_row_count: 0\n"
            "    status: unique\n",
            profile,
            1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir, catalog_path, categories_path, _ = write_fixture_tree(Path(tmp), bad_doc)
            categories = catalog_docs.load_categories(categories_path)
            rows = catalog_docs.load_catalog_rows(catalog_path)

            with self.assertRaisesRegex(catalog_docs.CatalogDocsError, "data_profile.field_count is required"):
                catalog_docs.read_asset_docs(
                    docs_dir=docs_dir,
                    categories=categories,
                    catalog_rows=rows,
                    allow_legacy=False,
                )

    def test_update_cadence_rejects_unchanged_skip_detail(self):
        bad_doc = STRICT_DOC.replace("update_cadence: manual", "update_cadence: monthly, skipped when unchanged")
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir, catalog_path, categories_path, _ = write_fixture_tree(Path(tmp), bad_doc)
            categories = catalog_docs.load_categories(categories_path)
            rows = catalog_docs.load_catalog_rows(catalog_path)

            with self.assertRaisesRegex(catalog_docs.CatalogDocsError, "schedule only"):
                catalog_docs.read_asset_docs(
                    docs_dir=docs_dir,
                    categories=categories,
                    catalog_rows=rows,
                    allow_legacy=False,
                )

    def test_export_readmes_mirrors_asset_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs_dir, catalog_path, categories_path, _ = write_fixture_tree(root)
            categories = catalog_docs.load_categories(categories_path)
            rows = catalog_docs.load_catalog_rows(catalog_path)
            docs = catalog_docs.read_asset_docs(
                docs_dir=docs_dir,
                categories=categories,
                catalog_rows=rows,
                allow_legacy=False,
            )

            changed = catalog_docs.export_readmes(docs, root / "export")

        self.assertEqual(
            changed,
            [root / "export/100-geographic-reference/110-boundaries/example-asset/README.md"],
        )


if __name__ == "__main__":
    unittest.main()
