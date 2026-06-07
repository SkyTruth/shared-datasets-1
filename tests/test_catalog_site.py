from __future__ import annotations

import csv
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
    def test_build_catalog_payload_includes_feature_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs_dir = root / "docs/assets"
            docs_dir.mkdir(parents=True)
            (docs_dir / "example-asset.md").write_text(DOC)
            categories_path = root / "categories.yaml"
            categories_path.write_text(
                "categories:\n  100-geographic-reference:\n    subcategories:\n      110-boundaries: Boundaries\n"
            )
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
