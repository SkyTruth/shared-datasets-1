from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import catalog_docs


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
- pmtiles
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
  purpose: Canonical vector dataset with feature_id identity
- path: latest/example-asset.pmtiles
  format: pmtiles
  role: companion
  purpose: Metadata lookup tiles with feature_id only
- path: latest/example-asset.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Feature metadata sidecar
- path: latest/example-asset.schema.json
  format: json
  role: metadata
  purpose: Feature metadata schema
- path: latest/example-asset.manifest.json
  format: json
  role: metadata
  purpose: Feature metadata manifest
---
# Example Asset

## What this is
Example.

## Files

## Schema notes
Feature identity follows the source field.

## Properties / columns
| Field | Description |
|---|---|
| `feature_id` | Canonical feature identity. |

## Update notes
Manual.
"""


class CatalogDocsTests(unittest.TestCase):
    def test_feature_identity_catalog_row_and_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs_dir = root / "docs/assets"
            docs_dir.mkdir(parents=True)
            (docs_dir / "example-asset.md").write_text(DOC)
            categories = {"100-geographic-reference": {"110-boundaries"}}

            docs = catalog_docs.read_asset_docs(docs_dir=docs_dir, categories=categories)

        row = catalog_docs.catalog_row(docs[0].metadata, "example-bucket")
        rendered = catalog_docs.render_asset_doc(docs[0])
        self.assertEqual(row["feature_identity"], "source_field:WDPAID")
        self.assertIn("geometry_hash_column: geometry_hash", rendered)

    def test_feature_metadata_requires_split_hash_columns(self):
        bad = DOC.replace("  geometry_hash_column: geometry_hash\n", "")
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir = Path(tmp) / "docs/assets"
            docs_dir.mkdir(parents=True)
            path = docs_dir / "example-asset.md"
            path.write_text(bad)

            with self.assertRaisesRegex(catalog_docs.CatalogDocsError, "geometry_hash_column"):
                catalog_docs.read_asset_docs(
                    docs_dir=docs_dir,
                    categories={"100-geographic-reference": {"110-boundaries"}},
                )


if __name__ == "__main__":
    unittest.main()
