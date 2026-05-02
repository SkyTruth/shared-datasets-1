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
last_updated: '2026-04-30'
source: Example source
source_url: https://example.test/source
license: Example license
license_flags:
- attribution-required
notes: Example notes
bounds:
- -10.5
- 20.25
- 30.75
- 40.125
geometry_type: Polygon
row_count: 12345
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
last_updated: '2026-04-30'
source: Example source
license: Example license
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


CATALOG_CSV = """asset_slug,title,category,subcategory,status,access_tier,owner,update_cadence,canonical_path,canonical_format,available_formats,metadata_paths,has_pmtiles,has_geojson,has_csv,last_updated,source,license,notes
example-asset,Example Asset,100-geographic-reference,110-boundaries,active,public,SkyTruth,manual,gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb,fgb,fgb;pmtiles,README.md,true,false,false,2026-04-30,Example source,Example license,Example notes
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
        self.assertEqual(row["has_pmtiles"], "true")
        self.assertIn("<!-- BEGIN GENERATED asset-summary -->", rendered)
        self.assertIn("- **Access tier:** public", rendered)
        self.assertIn("source_url: https://example.test/source", rendered)
        self.assertIn("geometry_type: Polygon", rendered)
        self.assertIn("row_count: 12345", rendered)
        self.assertIn("| `latest/example-asset.pmtiles` | `pmtiles` | `companion` | Web map tiles |", rendered)

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
