---
schema_version: 1
asset_slug: cerulean-s1-envelope
title: Cerulean S1 Envelope
category: 200-imagery-derived
subcategory: 210-satellite-indexes
status: active
access_tier: public
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/cerulean-s1-envelope.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
- runs/YYYY-MM-DD.json
- latest/cerulean-s1-envelope.metadata.ndjson.gz
- latest/cerulean-s1-envelope.schema.json
- latest/cerulean-s1-envelope.manifest.json
source: SkyTruth internal derived Cerulean Sentinel-1 envelope WKT extract
license: SkyTruth internal use; upstream source and redistribution terms need confirmation
citation: SkyTruth (2026). Cerulean S1 Envelope. Internal derived Sentinel-1 envelope extract; upstream source citation needs
  confirmation.
notes: Named as a Cerulean envelope to avoid implying complete Sentinel-1 footprint coverage; the legacy remote prefix sentinel-1-footprints
  is a deprecated pre-rename location and is intentionally not an active catalog slug; release 2026-05-01; source features
  1; fgb sha256 4fd635807aa544d8a0019f54ff663a639816cc7b2726d7a935fb7d8780924b11; pmtiles sha256 33f080e73a6ea2f5dc78b7174abbaf61c6d3c52165615f69ff1d8510ac225e6d;
  PMTiles rebuilt 2026-05-04 with zooms 0-6, no simplification, and synthetic source_layer property for catalog inspection;
  corrective metadata-contract release 2026-06-05 reuses latest FGB generation 1777669295802653, adds generated feature_id,
  geometry_hash, properties_hash, metadata sidecar, schema, manifest, and metadata-lookup PMTiles with only feature_id; fgb
  sha256 94520e1987bc4f84e253218c6467b1ddeb1b5ad4b59b17fba2ac037a935966ac; pmtiles sha256 825cb5d5142ce63752d71ec43e76098506c3cf07c683a4032d4c0fe00dd52fab;
  metadata sha256 af34ee531b4404337b773107376b7d8c8cee4662b4b08992c59abdebddb492d6; schema sha256 c0a8f1b1f6916ff8488d5e1802cfafb1ef44ff3d63d4113f07a80df6eab6ea18;
  manifest sha256 ab2dd57bb04704512d5493d6f734487c78a5805d6d9d70beebdf8e6654504dd7; canonical FGB preserves the source WKT
  geometry as an envelope only
row_count: 1
data_profile:
  field_count: 3
  identity_candidates: []
  notes: No source attribute fields or source field ID candidate; metadata-contract releases use a generated geometry-digest
    feature_id.
feature_identity:
  strategy: generated_sequence_content_hash
  source_fields: []
  generated_id_type: monotonic_integer_string
  assignment_key:
  - geometry_hash
  - properties_hash
feature_metadata:
  storage: metadata_sidecar_v1
  index_backend: firestore
  feature_id_column: feature_id
  geometry_hash_column: geometry_hash
  properties_hash_column: properties_hash
  sidecar_file: latest/cerulean-s1-envelope.metadata.ndjson.gz
  schema_file: latest/cerulean-s1-envelope.schema.json
  manifest_file: latest/cerulean-s1-envelope.manifest.json
  provenance_default: true
pmtiles_detail_hint: coarse
files:
- path: latest/cerulean-s1-envelope.fgb
  format: fgb
  role: canonical
  purpose: Canonical WGS84 multipolygon envelope dataset with release metadata fields
- path: latest/cerulean-s1-envelope.pmtiles
  format: pmtiles
  role: companion
  purpose: Metadata-lookup tiles generated from the same envelope geometry with only feature_id properties
- path: latest/cerulean-s1-envelope.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Canonical feature metadata sidecar keyed by feature_id
- path: latest/cerulean-s1-envelope.schema.json
  format: json
  role: metadata
  purpose: Release feature metadata schema for field projection
- path: latest/cerulean-s1-envelope.manifest.json
  format: json
  role: metadata
  purpose: Release manifest tying source input, artifacts, checksums, IDs, validation, and index-load policy
- path: releases/2026-05-01/cerulean-s1-envelope.fgb
  format: fgb
  role: release
  purpose: Dated legacy pre-metadata-contract canonical release
- path: releases/2026-06-05/cerulean-s1-envelope.fgb
  format: fgb
  role: release
  purpose: Dated metadata-contract canonical release
- path: releases/2026-06-05/cerulean-s1-envelope.pmtiles
  format: pmtiles
  role: release
  purpose: Dated metadata-lookup map-tile release
- path: releases/2026-06-05/cerulean-s1-envelope.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Dated canonical feature metadata sidecar keyed by feature_id
- path: releases/2026-06-05/cerulean-s1-envelope.schema.json
  format: json
  role: metadata
  purpose: Dated release feature metadata schema for field projection
- path: releases/2026-06-05/cerulean-s1-envelope.manifest.json
  format: json
  role: metadata
  purpose: Dated release manifest tying source input, artifacts, checksums, IDs, validation, and index-load policy
- path: runs/2026-05-01.json
  format: json
  role: run-record
  purpose: Manual publish record
---

# Cerulean S1 Envelope

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Update cadence:** manual
- **Canonical file:** `latest/cerulean-s1-envelope.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** SkyTruth internal derived Cerulean Sentinel-1 envelope WKT extract
- **License / terms:** SkyTruth internal use; upstream source and redistribution terms need confirmation
- **Citation:** SkyTruth (2026). Cerulean S1 Envelope. Internal derived Sentinel-1 envelope extract; upstream source citation needs confirmation.
<!-- END GENERATED asset-summary -->

## What this is

This asset contains a SkyTruth internal derived Cerulean Sentinel-1 analysis
envelope from the local source file `s1 footprint wkt.csv`. The source CSV has
one `wkt` column and one valid WGS84 `MultiPolygon` feature.

This is an envelope or coverage mask used for Cerulean context, not a complete
Sentinel-1 scene-footprint catalog. The canonical FlatGeobuf preserves the
source geometry as a single multipolygon feature with generated release metadata
fields. The PMTiles artifact is generated from the same geometry for web-map
display and metadata lookup only.

## When to use it

- Use this as a broad Cerulean Sentinel-1 analysis envelope for map display,
  coarse filtering, and context.
- Use the FlatGeobuf file for analytical workflows.
- Use the PMTiles file for web-map display and feature metadata lookup by
  `feature_id`.
- Do not use this as a scene-level Sentinel-1 catalog, acquisition metadata
  table, repeat-intensity surface, polarization inventory, orbit list, or
  public redistribution source without confirming the upstream source and
  license terms.
- Do not use the PMTiles artifact as the analytical source.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/cerulean-s1-envelope.fgb` | `fgb` | `canonical` | Canonical WGS84 multipolygon envelope dataset with release metadata fields |
| `latest/cerulean-s1-envelope.pmtiles` | `pmtiles` | `companion` | Metadata-lookup tiles generated from the same envelope geometry with only feature_id properties |
| `latest/cerulean-s1-envelope.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Canonical feature metadata sidecar keyed by feature_id |
| `latest/cerulean-s1-envelope.schema.json` | `json` | `metadata` | Release feature metadata schema for field projection |
| `latest/cerulean-s1-envelope.manifest.json` | `json` | `metadata` | Release manifest tying source input, artifacts, checksums, IDs, validation, and index-load policy |
| `releases/2026-05-01/cerulean-s1-envelope.fgb` | `fgb` | `release` | Dated legacy pre-metadata-contract canonical release |
| `releases/2026-06-05/cerulean-s1-envelope.fgb` | `fgb` | `release` | Dated metadata-contract canonical release |
| `releases/2026-06-05/cerulean-s1-envelope.pmtiles` | `pmtiles` | `release` | Dated metadata-lookup map-tile release |
| `releases/2026-06-05/cerulean-s1-envelope.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Dated canonical feature metadata sidecar keyed by feature_id |
| `releases/2026-06-05/cerulean-s1-envelope.schema.json` | `json` | `metadata` | Dated release feature metadata schema for field projection |
| `releases/2026-06-05/cerulean-s1-envelope.manifest.json` | `json` | `metadata` | Dated release manifest tying source input, artifacts, checksums, IDs, validation, and index-load policy |
| `runs/2026-05-01.json` | `json` | `run-record` | Manual publish record |
<!-- END GENERATED files-table -->

## Schema notes

Geometry is WGS84 multipolygon geometry. The source CSV contains one valid
`MultiPolygon` with 64 polygon parts and bounds `(-179.366759, -79.420040) -
(179.536787, 89.565949)`. The published FlatGeobuf has one feature in layer
`cerulean_s1_envelope`.

The source CSV is not published as a canonical format because shared-datasets
CSV assets must not contain geometry columns. The corrective 2026-06-05 release
starts from the existing `latest/cerulean-s1-envelope.fgb` object, generation
`1777669295802653`, and does not reacquire upstream source material.

The metadata-contract release generates `feature_id` from the normalized
geometry digest because the source has no source identifier. `geometry_hash` is
computed from canonical geometry, and `properties_hash` is computed from
projected non-geometry sidecar properties.

The PMTiles artifact is a display and metadata-lookup derivative with zooms 0
through 6 and no display simplification. The 2026-06-05 PMTiles archive keeps
maxzoom 6 to preserve the accepted coarse envelope display from the 2026-05-04
rebuild. PMTiles feature properties are intentionally limited to `feature_id`.

The remote prefix `200-imagery-derived/210-satellite-indexes/sentinel-1-footprints/`
was an initial name for this dataset before the framing was corrected. It is
treated as a deprecated legacy prefix for audit purposes, not as a separate
catalog asset and not as the canonical publishing location.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `geometry` | MultiPolygon | Cerulean Sentinel-1 analysis envelope geometry in WGS84. |
| `feature_id` | string | Public URL-safe lookup handle generated from the normalized geometry digest. |
| `geometry_hash` | string | SHA-256 content hash computed from canonical feature geometry. |
| `properties_hash` | string | SHA-256 content hash computed from projected non-geometry metadata properties. |

The PMTiles display tiles publish only `feature_id`; full feature
metadata is served from the metadata sidecar and feature metadata index.

## Update notes

Manually converted from `/Users/jonathanraphael/Downloads/s1 footprint wkt.csv`
on 2026-05-01 using `scripts/vector_asset.py`. The asset name emphasizes that
the geometry is an envelope and should not be interpreted as complete
Sentinel-1 footprint coverage.

Output summary:

- Source rows: 1
- Published features: 1
- CRS: EPSG:4326
- Legacy 2026-05-01 FGB SHA-256: `4fd635807aa544d8a0019f54ff663a639816cc7b2726d7a935fb7d8780924b11`
- Legacy 2026-05-04 PMTiles SHA-256: `33f080e73a6ea2f5dc78b7174abbaf61c6d3c52165615f69ff1d8510ac225e6d`

The corrective 2026-06-05 release adds the release-oriented metadata contract.
It starts from `latest/cerulean-s1-envelope.fgb` generation `1777669295802653`,
generates a URL-safe `feature_id`, and publishes `geometry_hash` and
`properties_hash =
sha256:ec963aca17dbd5389752844ec67376c4841e37990b5034ac611a1c6aa2de454c`.
No translations are generated because the source has no human-readable metadata
fields to localize.

2026-06-05 artifact summary:

- FGB SHA-256: `94520e1987bc4f84e253218c6467b1ddeb1b5ad4b59b17fba2ac037a935966ac`
- PMTiles SHA-256: `825cb5d5142ce63752d71ec43e76098506c3cf07c683a4032d4c0fe00dd52fab`
- Metadata sidecar SHA-256: `af34ee531b4404337b773107376b7d8c8cee4662b4b08992c59abdebddb492d6`
- Schema SHA-256: `c0a8f1b1f6916ff8488d5e1802cfafb1ef44ff3d63d4113f07a80df6eab6ea18`
- Manifest SHA-256: `ab2dd57bb04704512d5493d6f734487c78a5805d6d9d70beebdf8e6654504dd7`
- PMTiles maxzoom: 6
- PMTiles zoom 0 decoded feature properties: `feature_id`
- PMTiles build path: WGS84 GeoJSONSeq to Tippecanoe MBTiles to PMTiles

## Known caveats

The input file did not include source metadata, acquisition fields, timestamps,
repeat intensity, polarization, orbit direction, relative orbit, platform, or
license text. Treat this as an internal derived Cerulean envelope until the
upstream source, generation method, and redistribution terms are confirmed.
