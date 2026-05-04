from __future__ import annotations

import csv
import json
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

CATALOG = """asset_slug,title,category,subcategory,status,access_tier,owner,update_cadence,canonical_path,canonical_format,available_formats,metadata_paths,has_pmtiles,has_geojson,has_csv,source,license,notes
example-asset,Example Asset,100-geographic-reference,110-boundaries,active,public,SkyTruth,manual,gs://example-bucket/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb,fgb,fgb;pmtiles,README.md,true,false,false,Example source,CC BY-NC 4.0,Example notes
"""

MIXED_ACCESS_CATALOG = CATALOG + (
    "private-asset,Private Asset,100-geographic-reference,110-boundaries,active,private,SkyTruth,manual,"
    "gs://example-bucket/100-geographic-reference/110-boundaries/private-asset/latest/private-asset.fgb,"
    "fgb,fgb;pmtiles,README.md,true,false,false,Private source,Internal terms,Private notes\n"
)

DOC = """---
asset_slug: example-asset
source_url: https://example.test/source
license_flags:
- attribution-required
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
    write_release_index(root, "example-asset")
    return catalog_path, categories_path, docs_dir


def write_release_index(root: Path, slug: str, *, latest_date: str = "2026-05-02", previous_date: str = "2026-04-30") -> None:
    release_dir = root / "_catalog/releases"
    release_dir.mkdir(parents=True, exist_ok=True)
    root_path = f"gs://example-bucket/100-geographic-reference/110-boundaries/{slug}"
    releases = []
    for date in (latest_date, previous_date):
        releases.append(
            {
                "date": date,
                "release_path": f"{root_path}/releases/{date}/",
                "run_record_path": f"{root_path}/runs/{date}.json",
                "source_version": f"source-{date}",
                "rows": 12345,
                "files": [
                    {
                        "format": "fgb",
                        "path": f"{root_path}/releases/{date}/{slug}.fgb",
                        "sha256": "a" * 64,
                    },
                    {
                        "format": "pmtiles",
                        "path": f"{root_path}/releases/{date}/{slug}.pmtiles",
                        "sha256": "b" * 64,
                    },
                ],
            }
        )
    payload = {
        "schema_version": 1,
        "asset_slug": slug,
        "updated_at": "2026-05-02T12:00:00Z",
        "latest_release": releases[0],
        "latest_run": {
            "date": latest_date,
            "status": "success",
            "source_version": f"source-{latest_date}",
            "release_path": f"{root_path}/releases/{latest_date}/",
            "run_record_path": f"{root_path}/runs/{latest_date}.json",
            "rows": 12345,
        },
        "releases": releases,
    }
    (release_dir / f"{slug}.json").write_text(json.dumps(payload))


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
        self.assertEqual(asset["access_tier"], "public")
        self.assertEqual(asset["public_url"], "https://storage.googleapis.com/example-bucket/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb")
        self.assertEqual(asset["pmtiles_path"], "gs://example-bucket/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.pmtiles")
        self.assertEqual(asset["pmtiles_url"], "https://tiles.skytruth.org/pmtiles/public/example-asset.pmtiles")
        self.assertEqual(asset["docs_url"], "docs/assets/example-asset.md")
        self.assertEqual(asset["release_index_url"], "../releases/example-asset.json")
        self.assertEqual(asset["last_updated"], "2026-05-02")
        self.assertEqual(asset["latest_release"]["date"], "2026-05-02")
        self.assertEqual(asset["latest_run"]["status"], "success")
        self.assertEqual(asset["pmtiles_sha256"], "b" * 64)
        self.assertEqual([version["date"] for version in asset["versions"]], ["2026-05-02", "2026-04-30"])
        self.assertEqual(asset["versions"][0]["canonical_path"], "gs://example-bucket/100-geographic-reference/110-boundaries/example-asset/releases/2026-05-02/example-asset.fgb")
        self.assertEqual(asset["versions"][0]["pmtiles_url"], "https://storage.googleapis.com/example-bucket/100-geographic-reference/110-boundaries/example-asset/releases/2026-05-02/example-asset.pmtiles")
        self.assertEqual(asset["versions"][0]["pmtiles_sha256"], "b" * 64)
        self.assertIn("non-commercial", asset["license_flags"])
        self.assertIn("attribution-required", asset["license_flags"])
        self.assertEqual(asset["bounds"], [-10.5, 20.25, 30.75, 40.125])
        self.assertEqual(asset["geometry_type"], "Polygon")
        self.assertEqual(asset["row_count"], 12345)
        self.assertEqual(asset["source_url"], "https://example.test/source")
        self.assertIn("Reusable example boundary dataset", asset["description"])

    def test_build_catalog_payload_keeps_public_and_private_assets_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog_path, categories_path, docs_dir = write_fixture(Path(tmp), MIXED_ACCESS_CATALOG)
            (docs_dir / "private-asset.md").write_text(DOC.replace("example-asset", "private-asset"))

            payload = catalog_site.build_catalog_payload(
                catalog_path=catalog_path,
                categories_path=categories_path,
                docs_dir=docs_dir,
                bucket="example-bucket",
                site_prefix="_catalog/web",
                generated_at="2026-05-01T00:00:00Z",
            )

        assets_by_slug = {asset["slug"]: asset for asset in payload["assets"]}
        self.assertEqual(set(assets_by_slug), {"example-asset", "private-asset"})
        self.assertEqual(assets_by_slug["example-asset"]["access_tier"], "public")
        self.assertEqual(assets_by_slug["private-asset"]["access_tier"], "private")
        self.assertEqual(
            assets_by_slug["example-asset"]["pmtiles_url"],
            "https://tiles.skytruth.org/pmtiles/public/example-asset.pmtiles",
        )
        self.assertEqual(
            assets_by_slug["private-asset"]["pmtiles_url"],
            "https://tiles.skytruth.org/pmtiles/private/private-asset.pmtiles",
        )

    def test_optional_discovery_metadata_validation(self):
        bad_doc = DOC.replace("row_count: 12345", "row_count: many")
        with tempfile.TemporaryDirectory() as tmp:
            catalog_path, categories_path, docs_dir = write_fixture(Path(tmp))
            (docs_dir / "example-asset.md").write_text(bad_doc)

            with self.assertRaisesRegex(catalog_site.CatalogSiteError, "row_count must be an integer"):
                catalog_site.build_catalog_payload(
                    catalog_path=catalog_path,
                    categories_path=categories_path,
                    docs_dir=docs_dir,
                    bucket="example-bucket",
                    site_prefix="_catalog/web",
                    generated_at="2026-05-01T00:00:00Z",
                )

    def test_license_flags_mark_referential_terms_without_flagging_explicit_terms_of_use(self):
        for license_text in (
            "See Marine Regions terms",
            "See Protected Planet WDPA terms",
            "See source terms",
        ):
            with self.subTest(license_text=license_text):
                self.assertIn("confirm-license", catalog_site.license_flags(license_text))

        gfw_flags = catalog_site.license_flags(
            "Global Fishing Watch API non-commercial use only and subject to Global Fishing Watch Terms of Use"
        )
        self.assertIn("non-commercial", gfw_flags)
        self.assertNotIn("confirm-license", gfw_flags)

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

    def test_invalid_access_tier_fails(self):
        bad_tier = CATALOG.replace(",public,", ",internal,", 1)
        with tempfile.TemporaryDirectory() as tmp:
            catalog_path, categories_path, docs_dir = write_fixture(Path(tmp), bad_tier)
            with self.assertRaisesRegex(catalog_site.CatalogSiteError, "access_tier"):
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
        with (REPO_ROOT / "catalog/shared-datasets-catalog.csv").open(newline="") as catalog_file:
            expected_active_assets = sum(1 for row in csv.DictReader(catalog_file) if row["status"] == "active")
        self.assertEqual(len(active_assets), expected_active_assets)
        self.assertTrue(all(asset["canonical_path"].startswith("gs://") for asset in active_assets))
        self.assertTrue(all(asset["release_index_url"].endswith(f"/{asset['slug']}.json") for asset in active_assets))
        self.assertTrue(any(asset["versions"] for asset in active_assets))
        self.assertTrue(any(asset["pmtiles_url"] for asset in active_assets))


if __name__ == "__main__":
    unittest.main()
