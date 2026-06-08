from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts import catalog_site


REPO_ROOT = Path(__file__).resolve().parents[1]


DOC = """---
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
metadata_paths:
- README.md
source: Example source
license: Example license
citation: Example citation
feature_identity:
  strategy: source_field
  source_fields:
  - WDPAID
feature_metadata:
  storage: metadata_sidecar_v1
  feature_id_column: feature_id
  geometry_hash_column: geometry_hash
  properties_hash_column: properties_hash
  sidecar_file: latest/example-asset.metadata.ndjson.gz
  schema_file: latest/example-asset.schema.json
  manifest_file: latest/example-asset.manifest.json
  provenance_default: true
files:
- path: latest/example-asset.fgb
  format: fgb
  role: canonical
  purpose: Canonical vector dataset
---
# Example Asset

## What this is
Example.
"""


class CatalogSiteTests(unittest.TestCase):
    def _write_categories(self, path: Path) -> None:
        path.write_text(
            "categories:\n  100-geographic-reference:\n    subcategories:\n      110-boundaries: Boundaries\n"
        )

    def _write_empty_catalog(self, path: Path) -> None:
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=catalog_site.REQUIRED_FIELDS)
            writer.writeheader()

    def _write_preview_release_index(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "asset_slug": "world-eez-high-seas",
                    "updated_at": "2026-06-08T03:22:53Z",
                    "latest_release": {
                        "date": "2024-10-10",
                        "rows": 286,
                        "source_version": "World_EEZ_v12_20231025 + World_High_Seas_v2_20241010",
                        "release_path": (
                            "gs://skytruth-shared-datasets-1-preview/100-geographic-reference/110-boundaries/"
                            "world-eez-high-seas/releases/2024-10-10/"
                        ),
                        "files": [
                            {
                                "format": "fgb",
                                "path": (
                                    "gs://skytruth-shared-datasets-1-preview/100-geographic-reference/110-boundaries/"
                                    "world-eez-high-seas/releases/2024-10-10/world-eez-high-seas.fgb"
                                ),
                                "sha256": "a" * 64,
                            },
                            {
                                "format": "pmtiles",
                                "path": (
                                    "gs://skytruth-shared-datasets-1-preview/100-geographic-reference/110-boundaries/"
                                    "world-eez-high-seas/releases/2024-10-10/world-eez-high-seas.pmtiles"
                                ),
                                "sha256": "b" * 64,
                            },
                            {
                                "format": "metadata",
                                "role": "metadata",
                                "path": (
                                    "gs://skytruth-shared-datasets-1-preview/100-geographic-reference/110-boundaries/"
                                    "world-eez-high-seas/releases/2024-10-10/world-eez-high-seas.metadata.ndjson.gz"
                                ),
                            },
                            {
                                "format": "schema",
                                "path": (
                                    "gs://skytruth-shared-datasets-1-preview/100-geographic-reference/110-boundaries/"
                                    "world-eez-high-seas/releases/2024-10-10/world-eez-high-seas.schema.json"
                                ),
                            },
                            {
                                "format": "manifest",
                                "path": (
                                    "gs://skytruth-shared-datasets-1-preview/100-geographic-reference/110-boundaries/"
                                    "world-eez-high-seas/releases/2024-10-10/world-eez-high-seas.manifest.json"
                                ),
                            },
                        ],
                    },
                    "latest_run": {"date": "2024-10-10", "status": "success"},
                    "releases": [
                        {
                            "date": "2024-10-10",
                            "rows": 286,
                            "source_version": "World_EEZ_v12_20231025 + World_High_Seas_v2_20241010",
                            "release_path": (
                                "gs://skytruth-shared-datasets-1-preview/100-geographic-reference/110-boundaries/"
                                "world-eez-high-seas/releases/2024-10-10/"
                            ),
                            "files": [
                                {
                                    "format": "fgb",
                                    "path": (
                                        "gs://skytruth-shared-datasets-1-preview/100-geographic-reference/110-boundaries/"
                                        "world-eez-high-seas/releases/2024-10-10/world-eez-high-seas.fgb"
                                    ),
                                    "sha256": "a" * 64,
                                },
                                {
                                    "format": "pmtiles",
                                    "path": (
                                        "gs://skytruth-shared-datasets-1-preview/100-geographic-reference/110-boundaries/"
                                        "world-eez-high-seas/releases/2024-10-10/world-eez-high-seas.pmtiles"
                                    ),
                                    "sha256": "b" * 64,
                                },
                                {
                                    "format": "metadata",
                                    "role": "metadata",
                                    "path": (
                                        "gs://skytruth-shared-datasets-1-preview/100-geographic-reference/110-boundaries/"
                                        "world-eez-high-seas/releases/2024-10-10/world-eez-high-seas.metadata.ndjson.gz"
                                    ),
                                },
                                {
                                    "format": "schema",
                                    "path": (
                                        "gs://skytruth-shared-datasets-1-preview/100-geographic-reference/110-boundaries/"
                                        "world-eez-high-seas/releases/2024-10-10/world-eez-high-seas.schema.json"
                                    ),
                                },
                                {
                                    "format": "manifest",
                                    "path": (
                                        "gs://skytruth-shared-datasets-1-preview/100-geographic-reference/110-boundaries/"
                                        "world-eez-high-seas/releases/2024-10-10/world-eez-high-seas.manifest.json"
                                    ),
                                },
                            ],
                        }
                    ],
                }
            )
        )

    def test_build_catalog_payload_includes_feature_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs_dir = root / "docs/assets"
            docs_dir.mkdir(parents=True)
            (docs_dir / "example-asset.md").write_text(DOC)
            categories_path = root / "categories.yaml"
            self._write_categories(categories_path)
            catalog_path = root / "catalog.csv"
            with catalog_path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=catalog_site.REQUIRED_FIELDS + ["feature_identity"])
                writer.writeheader()
                writer.writerow(
                    {
                        "asset_slug": "example-asset",
                        "title": "Example Asset",
                        "category": "100-geographic-reference",
                        "subcategory": "110-boundaries",
                        "status": "active",
                        "access_tier": "public",
                        "owner": "SkyTruth",
                        "update_cadence": "manual",
                        "canonical_path": "gs://example-bucket/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb",
                        "canonical_format": "fgb",
                        "available_formats": "fgb",
                        "metadata_paths": "README.md",
                        "source": "Example source",
                        "license": "Example license",
                        "citation": "Example citation",
                        "feature_identity": "source_field:WDPAID",
                    }
                )

            payload = catalog_site.build_catalog_payload(
                catalog_path=catalog_path,
                categories_path=categories_path,
                docs_dir=docs_dir,
                bucket="example-bucket",
                site_prefix="_catalog/web",
                generated_at="2026-05-01T00:00:00Z",
            )

        self.assertEqual(payload["assets"][0]["feature_identity"]["strategy"], "source_field")
        self.assertEqual(payload["assets"][0]["feature_identity"]["source_fields"], ["WDPAID"])

    def test_release_index_only_preview_asset_requires_explicit_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs_dir = root / "docs/assets"
            docs_dir.mkdir(parents=True)
            categories_path = root / "categories.yaml"
            self._write_categories(categories_path)
            catalog_path = root / "catalog.csv"
            self._write_empty_catalog(catalog_path)
            release_index_dir = root / "_catalog/releases"
            self._write_preview_release_index(release_index_dir / "world-eez-high-seas.json")

            payload = catalog_site.build_catalog_payload(
                catalog_path=catalog_path,
                categories_path=categories_path,
                docs_dir=docs_dir,
                bucket="skytruth-shared-datasets-1-preview",
                site_prefix="_catalog/web",
                release_index_dir=release_index_dir,
                release_index_assets_only=True,
                latest_from_release_index=True,
                force_access_tier="private",
                generated_at="2026-06-08T00:00:00Z",
            )
            self.assertEqual(payload["assets"], [])

            payload = catalog_site.build_catalog_payload(
                catalog_path=catalog_path,
                categories_path=categories_path,
                docs_dir=docs_dir,
                bucket="skytruth-shared-datasets-1-preview",
                site_prefix="_catalog/web",
                release_index_dir=release_index_dir,
                release_index_assets_only=True,
                latest_from_release_index=True,
                allow_release_index_only_assets=True,
                force_access_tier="private",
                generated_at="2026-06-08T00:00:00Z",
            )

        asset = payload["assets"][0]
        version = asset["versions"][0]
        self.assertEqual(asset["slug"], "world-eez-high-seas")
        self.assertEqual(asset["category"], "100-geographic-reference")
        self.assertEqual(asset["subcategory"], "110-boundaries")
        self.assertEqual(asset["access_tier"], "private")
        self.assertEqual(asset["canonical_format"], "fgb")
        self.assertTrue(asset["has_pmtiles"])
        self.assertEqual(asset["row_count"], 286)
        self.assertEqual(version["date"], "2024-10-10")
        self.assertEqual(version["rows"], 286)
        self.assertEqual(len(version["files"]), 5)
        self.assertTrue(any(file["path"].endswith(".metadata.ndjson.gz") for file in version["files"]))

    def test_release_index_only_preview_flag_requires_assets_only_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs_dir = root / "docs/assets"
            docs_dir.mkdir(parents=True)
            categories_path = root / "categories.yaml"
            self._write_categories(categories_path)
            catalog_path = root / "catalog.csv"
            self._write_empty_catalog(catalog_path)

            with self.assertRaisesRegex(catalog_site.CatalogSiteError, "requires release_index_assets_only"):
                catalog_site.build_catalog_payload(
                    catalog_path=catalog_path,
                    categories_path=categories_path,
                    docs_dir=docs_dir,
                    bucket="skytruth-shared-datasets-1-preview",
                    site_prefix="_catalog/web",
                    release_index_assets_only=False,
                    allow_release_index_only_assets=True,
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

        self.assertGreater(len(payload["assets"]), 0)


if __name__ == "__main__":
    unittest.main()
