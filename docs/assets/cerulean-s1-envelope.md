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
source: SkyTruth internal derived Cerulean Sentinel-1 envelope WKT extract
license: SkyTruth internal use; upstream source and redistribution terms need confirmation
citation: SkyTruth (2026). Cerulean S1 Envelope. Internal derived Sentinel-1 envelope extract; upstream source citation needs
  confirmation.
notes: Named as a Cerulean envelope to avoid implying complete Sentinel-1 footprint coverage. Release 2026-05-01 is the initial
  upload (source features 1; fgb sha256 4fd635807aa544d8a0019f54ff663a639816cc7b2726d7a935fb7d8780924b11). Release 2026-05-04
  is a canonical-file repair and PMTiles rebuild with zooms 0-6, no simplification, and a synthetic source_layer property
  (pmtiles sha256 33f080e73a6ea2f5dc78b7174abbaf61c6d3c52165615f69ff1d8510ac225e6d). A 2026-06-05 v1 metadata-contract release
  was documented here but never promoted; no sidecar, schema, manifest, or releases/2026-06-05/ objects exist in the bucket
  and the hashes previously recorded for it are retracted. The 2026-06-10 corrective schema-contract release publishes the
  asset's first promoted metadata contract directly under release feature identity v2, built from the unchanged latest FGB
  generation 1777669295802653, with generated monotonic decimal feature_id assigned from the geometry_hash+properties_hash
  identity key, geometry_hash and properties_hash columns, schema_version 2 metadata/schema/manifest artifacts, and metadata-lookup
  PMTiles rebuilt at maxzoom 6 with only feature_id. Hashes for the 2026-06-10 candidates are fgb d658c6ee78c9c81510baf3b479f471c936f494bfc4ee06f83c57e30eec3ccf33;
  pmtiles 014d5bed6a28e15dade63d5b64fc620e479f380d3be8e7876b95d9e3994737c2; metadata ad1631c356a2bb71edeb75233185520fc678da683a20985b85610cd8ea95936b;
  schema 7901582ecaadf2d260646be9e46a8694be6e423c9c494e163e64f5b643f01185; manifest a4995e460e781cb915db747c5140f8d6cda6c923b398048d38aad0efaee9626f.
  Canonical FGB preserves the source WKT geometry as an envelope only
row_count: 1
data_profile:
  field_count: 3
  identity_candidates: []
  notes: No source attribute fields or source field ID candidate; releases use generated monotonic decimal feature_id values
    assigned from the geometry_hash+properties_hash identity key.
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
- path: releases/YYYY-MM-DD/cerulean-s1-envelope.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/YYYY-MM-DD/cerulean-s1-envelope.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
- path: releases/YYYY-MM-DD/cerulean-s1-envelope.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Dated canonical feature metadata sidecar keyed by feature_id
- path: releases/YYYY-MM-DD/cerulean-s1-envelope.schema.json
  format: json
  role: metadata
  purpose: Dated release feature metadata schema for field projection
- path: releases/YYYY-MM-DD/cerulean-s1-envelope.manifest.json
  format: json
  role: metadata
  purpose: Dated release manifest tying source input, artifacts, checksums, IDs, validation, and index-load policy
- path: runs/YYYY-MM-DD.json
  format: json
  role: run-record
  purpose: Manual release run record
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
| `releases/YYYY-MM-DD/cerulean-s1-envelope.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/YYYY-MM-DD/cerulean-s1-envelope.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
| `releases/YYYY-MM-DD/cerulean-s1-envelope.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Dated canonical feature metadata sidecar keyed by feature_id |
| `releases/YYYY-MM-DD/cerulean-s1-envelope.schema.json` | `json` | `metadata` | Dated release feature metadata schema for field projection |
| `releases/YYYY-MM-DD/cerulean-s1-envelope.manifest.json` | `json` | `metadata` | Dated release manifest tying source input, artifacts, checksums, IDs, validation, and index-load policy |
| `runs/YYYY-MM-DD.json` | `json` | `run-record` | Manual release run record |
<!-- END GENERATED files-table -->

## Schema notes

Geometry is WGS84 multipolygon geometry. The source CSV contains one valid
`MultiPolygon` with 64 polygon parts and bounds `(-179.366759, -79.420040) -
(179.536787, 89.565949)`. The published FlatGeobuf has one feature in layer
`cerulean_s1_envelope`.

The source CSV is not published as a canonical format because shared-datasets
CSV assets must not contain geometry columns. The corrective 2026-06-10 release
starts from the existing `latest/cerulean-s1-envelope.fgb` object, generation
`1777669295802653`, and does not reacquire upstream source material.

An earlier 2026-06-05 v1 metadata-contract release was documented for this
asset but never promoted: the bucket has no sidecar, schema, manifest, or
`releases/2026-06-05/` objects, so there is no published v1 `feature_id` to
preserve. The 2026-06-10 release is the asset's first promoted metadata
contract and uses release feature identity v2 directly. Because the source has
no attribute fields, `feature_id` is a generated monotonic decimal string
(`1`) assigned from the pair of `geometry_hash` and `properties_hash` identity
keys. `geometry_hash` is computed from canonical geometry, and
`properties_hash` is computed from the projected non-geometry sidecar
properties, which form an empty record for this asset. Adding the three
identity columns is an append-compatible schema change; no prior columns were
removed or retyped.

The PMTiles artifact is a metadata-lookup derivative with zooms 0 through 6,
keeping the accepted coarse envelope display from the 2026-05-04 rebuild.
PMTiles feature properties are intentionally limited to `feature_id`.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `geometry` | MultiPolygon | Cerulean Sentinel-1 analysis envelope geometry in WGS84. |
| `feature_id` | string | Public URL-safe lookup handle; generated monotonic decimal string assigned from the geometry/properties hash identity key. |
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

A 2026-06-05 v1 metadata-contract release was previously described here with
artifact hashes, but it was never promoted to the bucket and those hashes are
retracted; the 2026-05-01 and 2026-05-04 releases remained the only published
state until the 2026-06-10 release.

The corrective 2026-06-10 release publishes the asset's first promoted
metadata contract directly under release feature identity v2. It starts from
`latest/cerulean-s1-envelope.fgb` generation `1777669295802653`, assigns the
generated monotonic decimal `feature_id` `1` from the
`geometry_hash`+`properties_hash` identity key, and publishes
`geometry_hash =
sha256:56a7d9027b6f8d84265cb0be1fb22ef0f7d9b564590f4c7cba98b34dee57c939` and
`properties_hash =
sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a` (the
hash of the empty properties record). No translations are generated because
the source has no human-readable metadata fields to localize.

2026-06-10 artifact summary:

- FGB SHA-256: `d658c6ee78c9c81510baf3b479f471c936f494bfc4ee06f83c57e30eec3ccf33`
- PMTiles SHA-256: `014d5bed6a28e15dade63d5b64fc620e479f380d3be8e7876b95d9e3994737c2`
- Metadata sidecar SHA-256: `ad1631c356a2bb71edeb75233185520fc678da683a20985b85610cd8ea95936b`
- Schema SHA-256: `7901582ecaadf2d260646be9e46a8694be6e423c9c494e163e64f5b643f01185`
- Manifest SHA-256: `a4995e460e781cb915db747c5140f8d6cda6c923b398048d38aad0efaee9626f`
- PMTiles maxzoom: 6
- PMTiles zoom 0 decoded feature properties: `feature_id`
- PMTiles build path: WGS84 GeoJSONSeq to Tippecanoe MBTiles to PMTiles

## Known caveats

The input file did not include source metadata, acquisition fields, timestamps,
repeat intensity, polarization, orbit direction, relative orbit, platform, or
license text. Treat this as an internal derived Cerulean envelope until the
upstream source, generation method, and redistribution terms are confirmed.
