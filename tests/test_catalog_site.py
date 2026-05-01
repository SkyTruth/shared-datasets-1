from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import catalog_site


REPO_ROOT = Path(__file__).resolve().parents[1]

CATEGORIES = """categories:
  "100-geographic-reference":
    subcategories:
      "110-boundaries": "Boundaries."
"""

CATALOG = """asset_slug,title,category,subcategory,status,owner,update_cadence,canonical_path,canonical_format,available_formats,metadata_paths,has_pmtiles,has_geojson,has_csv,last_updated,source,license,notes
example-asset,Example Asset,100-geographic-reference,110-boundaries,active,SkyTruth,manual,gs://example-bucket/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb,fgb,fgb;pmtiles,README.md,true,false,false,2026-04-30,Example source,CC BY-NC 4.0,Example notes
"""

DOC = """---
asset_slug: example-asset
files:
- path: latest/example-asset.fgb
  format: fgb
  role: canonical
  purpose: Canonical file
- path: latest/example-asset.pmtiles
  format: pmtiles
  role: companion
  purpose: Map tiles
- path: releases/YYYY-MM-DD/example-asset.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/YYYY-MM-DD/example-asset.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
---

# Example Asset

## What this is

Reusable example boundary dataset for catalog preview tests.
"""


def write_fixture(root: Path, catalog_text: str = CATALOG) -> tuple[Path, Path, Path]:
    catalog_path = root / "catalog/shared-datasets-catalog.csv"
    categories_path = root / "catalog/categories.yaml"
    docs_dir = root / "docs/assets"
    catalog_path.parent.mkdir(parents=True)
    docs_dir.mkdir(parents=True)
    catalog_path.write_text(catalog_text)
    categories_path.write_text(CATEGORIES)
    (docs_dir / "example-asset.md").write_text(DOC)
    return catalog_path, categories_path, docs_dir


class CatalogSiteTests(unittest.TestCase):
    def test_build_catalog_payload_derives_urls_and_license_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog_path, categories_path, docs_dir = write_fixture(Path(tmp))

            payload = catalog_site.build_catalog_payload(
                catalog_path=catalog_path,
                categories_path=categories_path,
                docs_dir=docs_dir,
                bucket="example-bucket",
                site_prefix="_catalog/web",
                generated_at="2026-05-01T00:00:00Z",
            )

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["generated_at"], "2026-05-01T00:00:00Z")
        asset = payload["assets"][0]
        self.assertEqual(asset["slug"], "example-asset")
        self.assertEqual(asset["public_url"], "https://storage.googleapis.com/example-bucket/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb")
        self.assertEqual(asset["pmtiles_path"], "gs://example-bucket/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.pmtiles")
        self.assertEqual(asset["docs_url"], "docs/assets/example-asset.md")
        self.assertEqual(asset["versions"][0]["date"], "2026-04-30")
        self.assertEqual(asset["versions"][0]["canonical_path"], "gs://example-bucket/100-geographic-reference/110-boundaries/example-asset/releases/2026-04-30/example-asset.fgb")
        self.assertEqual(asset["versions"][0]["pmtiles_url"], "https://storage.googleapis.com/example-bucket/100-geographic-reference/110-boundaries/example-asset/releases/2026-04-30/example-asset.pmtiles")
        self.assertIn("non-commercial", asset["license_flags"])
        self.assertIn("Reusable example boundary dataset", asset["description"])

    def test_build_site_writes_static_bundle_and_docs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog_path, categories_path, docs_dir = write_fixture(root)
            out_dir = root / "out"

            written = catalog_site.build_site(
                catalog_path=catalog_path,
                categories_path=categories_path,
                docs_dir=docs_dir,
                static_dir=REPO_ROOT / "web/catalog",
                out_dir=out_dir,
                bucket="example-bucket",
                site_prefix="_catalog/web",
                generated_at="2026-05-01T00:00:00Z",
            )

            written_names = {path.relative_to(out_dir).as_posix() for path in written}

        self.assertIn("index.html", written_names)
        self.assertIn("catalog.json", written_names)
        self.assertIn("docs/assets/example-asset.md", written_names)

    def test_duplicate_slugs_fail(self):
        duplicate = CATALOG + CATALOG.splitlines()[1] + "\n"
        with tempfile.TemporaryDirectory() as tmp:
            catalog_path, categories_path, docs_dir = write_fixture(Path(tmp), duplicate)

            with self.assertRaisesRegex(catalog_site.CatalogSiteError, "duplicate asset_slug"):
                catalog_site.build_catalog_payload(
                    catalog_path=catalog_path,
                    categories_path=categories_path,
                    docs_dir=docs_dir,
                    bucket="example-bucket",
                    site_prefix="_catalog/web",
                    generated_at="2026-05-01T00:00:00Z",
                )

    def test_invalid_taxonomy_and_bad_gs_paths_fail(self):
        bad_category = CATALOG.replace("100-geographic-reference", "900-unknown", 1)
        with tempfile.TemporaryDirectory() as tmp:
            catalog_path, categories_path, docs_dir = write_fixture(Path(tmp), bad_category)
            with self.assertRaisesRegex(catalog_site.CatalogSiteError, "unknown category"):
                catalog_site.build_catalog_payload(
                    catalog_path=catalog_path,
                    categories_path=categories_path,
                    docs_dir=docs_dir,
                    bucket="example-bucket",
                    site_prefix="_catalog/web",
                    generated_at="2026-05-01T00:00:00Z",
                )

        bad_uri = CATALOG.replace("gs://example-bucket/", "https://example.test/")
        with tempfile.TemporaryDirectory() as tmp:
            catalog_path, categories_path, docs_dir = write_fixture(Path(tmp), bad_uri)
            with self.assertRaisesRegex(catalog_site.CatalogSiteError, "expected gs://"):
                catalog_site.build_catalog_payload(
                    catalog_path=catalog_path,
                    categories_path=categories_path,
                    docs_dir=docs_dir,
                    bucket="example-bucket",
                    site_prefix="_catalog/web",
                    generated_at="2026-05-01T00:00:00Z",
                )

    def test_current_repo_catalog_builds_all_assets(self):
        payload = catalog_site.build_catalog_payload(
            catalog_path=REPO_ROOT / "catalog/shared-datasets-catalog.csv",
            categories_path=REPO_ROOT / "catalog/categories.yaml",
            docs_dir=REPO_ROOT / "docs/assets",
            bucket="skytruth-shared-datasets-1",
            site_prefix="_catalog/web",
            generated_at="2026-05-01T00:00:00Z",
        )

        active_assets = [asset for asset in payload["assets"] if asset["status"] == "active"]
        self.assertEqual(len(active_assets), 11)
        self.assertTrue(all(asset["canonical_path"].startswith("gs://") for asset in active_assets))
        self.assertTrue(all(asset["versions"] for asset in active_assets))
        self.assertTrue(any(asset["pmtiles_url"] for asset in active_assets))


if __name__ == "__main__":
    unittest.main()
