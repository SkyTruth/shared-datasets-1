---
title: Feature Metadata API
description: Contract and operations for release-oriented vector metadata lookup.
last_updated: 2026-06-03
audience: Shared-datasets maintainers and consuming application backend owners
---

# Feature Metadata API

Release-oriented vector assets publish full feature metadata outside PMTiles.
The durable source is the release feature model, release manifest, canonical
FGB, and `.metadata.ndjson.gz` sidecar in GCS. Firestore is a rebuildable
serving index loaded from that sidecar.

## Endpoints

```http
POST /v1/assets/{slug}/releases/{release}:lookup
POST /v1/assets/{slug}/releases/{release}:lookupByExtId
```

`release` is either `latest` or `YYYY-MM-DD`. The service resolves the release
from `_catalog/releases/{slug}.json`; every successful response includes
`resolved_release`.

The service is IAP-protected for all assets at launch. Consuming browser apps
should call their own backend, and that backend should call the metadata
service.

`lookup` is keyed by internal `feature_id` values emitted in PMTiles. Use
`lookupByExtId` for browser/user URL workflows that carry public handles.
`ext_id` values must be unique, nonblank, and match `^[A-Za-z0-9]{1,64}$`.

## Feature ID Request

```json
{
  "ids": ["src:id:1", "src:id:2"],
  "fields": ["name", "source_id"],
  "include_provenance": true
}
```

Rules:

- `ids` is required and accepts up to 500 feature IDs.
- `fields` omitted or `null` returns all properties.
- `fields: []` returns identifiers, hashes, and provenance only.
- `include_provenance` defaults to `true`.
- A request may project up to 500 fields.

## Ext ID Request

```json
{
  "ext_ids": ["1", "2"],
  "fields": ["name", "source_id"],
  "include_provenance": true
}
```

Rules:

- `ext_ids` is required and accepts up to 500 public ext IDs.
- Every `ext_id` must match `^[A-Za-z0-9]{1,64}$`.
- `fields`, `include_provenance`, and projection limits match `lookup`.

## Response

```json
{
  "asset_slug": "example-asset",
  "requested_release": "latest",
  "resolved_release": "2026-05-01",
  "release_index_generation": 123,
  "manifest_generation": 125,
  "schema_generation": 124,
  "index_load_id": "load-1",
  "items": [
    {
      "feature_id": "src:id:1",
      "ext_id": "123",
      "found": true,
      "feature_hash": "sha256:...",
      "properties": {
        "ext_id": "123",
        "name": "Example"
      },
      "provenance": {
        "source": "Provider release"
      }
    },
    {
      "ext_id": "missing",
      "found": false
    }
  ],
  "limits": {
    "max_ids": 500,
    "max_fields": 500,
    "max_response_bytes": 10485760
  },
  "deduplicated_lookup_count": 2
}
```

Duplicate IDs or ext IDs preserve request order in `items`; the backend lookup is
deduplicated. Missing IDs are item-level `"found": false` results in a `200`
response after the index is confirmed ready. Lookup requests are keyed by
`feature_id`; lookup-by-ext-ID requests resolve the public `ext_id` to the
current internal `feature_id` and return both values on found items. When a
found feature-ID lookup document has an `ext_id` property, the response also
mirrors it as top-level `ext_id` for PMTiles clients. Unknown fields are
rejected against the release schema before Firestore lookup, even if every
requested ID is missing. Explicit valid fields that are absent from a particular
document return `null`.

## Errors

Errors use:

```json
{
  "error": {
    "code": "invalid_argument",
    "message": "ids must be a non-empty array",
    "details": {}
  }
}
```

Status codes:

- `400`: invalid JSON, invalid IDs or ext IDs, invalid fields, or request limit exceeded.
- `401`: IAP identity missing.
- `403`: IAP identity is outside the allowed domains.
- `404`: unknown asset, unknown release, or no latest release.
- `409`: Firestore serving index is not ready.
- `413`: response would exceed 10 MiB.
- `503`: transient serving backend failure.

## Cache And Validators

The service returns `Cache-Control: no-store` while it is IAP-only. Successful
lookup responses include an ETag, and callers may use `If-None-Match` for
repeat requests.

## Locale-Specific Sidecar Downloads

For static catalog feature inspection, the browser does not call the lookup API
or fetch a translation overlay. Public assets resolve the sidecar from the
hydrated release index and fetch it directly from:

```text
https://tiles.skytruth.org/artifacts/{bucket-object-path}
```

For authorized private inspection through the IAP-protected catalog viewer, the
browser calls the download resolver for one metadata sidecar URL:

```http
GET /api/download-url?slug={slug}&format=metadata&version={release_or_latest}&locale=es
```

The resolver first looks for `{asset-slug}.metadata.es.ndjson.gz` in the
selected release's `files` list. If that materialized localized view is absent,
it falls back to the canonical `{asset-slug}.metadata.ndjson.gz`. Successful
responses include `requested_locale`, `resolved_locale`, and
`metadata_locale_fallback` so clients can log fallback behavior, but the browser
still fetches exactly one metadata sidecar and parses the same record shape.
When the resolver is used for public assets, `download_url` is a public Cloud
CDN artifact URL under
`https://tiles.skytruth.org/artifacts/{bucket-object-path}`. For private
production assets, the resolver may return one signed Cloud CDN URL under
`https://tiles.skytruth.org/private/{bucket-object-path}`. Local development,
feature-preview buckets, or deployments without metadata CDN signing configured
may still return one signed GCS URL. Clients must treat `download_url` as an
opaque sidecar URL and must not fetch a translation overlay or merge
translations in the browser.

Localized sidecars are generated during publish/build/index preparation from
the canonical sidecar and `{asset-slug}.metadata-translations.csv`. Translation
rows are keyed by `feature_id`, property field, locale, and source-value hash.
Rows whose hash no longer matches the canonical property value are stale; the
generator reports and skips them so the localized view falls back to canonical
properties for that field. After an approved publish plan promotes a new
translation source CSV, `.github/workflows/metadata-localization.yml` reruns
the materialization pipeline for that source and writes generated localized
sidecars with generation preconditions from the approved publisher environment.

## Operations

Index load status is written only under:

```text
{asset-root}/index-loads/YYYY-MM-DD/{load-id}.json
```

Do not rewrite release manifests to update index load status. To rebuild an
index, read the canonical sidecar for the release, write a new immutable
Firestore load under
`feature_metadata/{asset_slug}/releases/{release}/loads/{load_id}/features/`,
validate counts and sample lookups, write the `ext_id -> feature_id` mapping
for public-handle lookup, then write a new index-load record through the
protected `Feature metadata index load` workflow. The service reads only the
`load_id` selected from the newest successful matching index-load record.

Dispatch `.github/workflows/feature-metadata-index-load.yml` in the
`shared-datasets-production` environment with:

- Exact `asset_slug`.
- Concrete `release` date in `YYYY-MM-DD` form.
- Canonical release `sidecar_uri`, `schema_uri`, and `manifest_uri`.
- Exact object generations for those three objects.
- Optional `load_id`; when omitted the workflow uses
  `github-{run_id}-{run_attempt}`.

The workflow authenticates as `metadata-index-loader`, downloads each canonical
object with the supplied generation precondition, runs
`scripts/feature_metadata_index.py` without `--dry-run`, writes a local
index-load record, and uploads that record no-clobber to:

```text
{asset-root}/index-loads/{release}/{load-id}.json
```

For local preflight only, maintainers may run
`scripts/feature_metadata_index.py --dry-run` against downloaded artifacts to
validate the sidecar/schema/manifest bundle and count the sidecar without
writing Firestore or publishing an index-load record.

Operational checks:

- Sidecar row count equals Firestore document count for the selected load.
- Sample feature IDs from PMTiles exist in Firestore.
- Sample public `ext_id` values resolve through `lookupByExtId` to the expected
  feature IDs.
- Firestore documents preserve `feature_id`, `feature_hash`, `properties`, and
  provenance.
- The latest release index resolves to the same release used during index load.
- Cloud Run metadata service error logs and Firestore lookup failures are
  monitored.

The production `Feature metadata service deploy` workflow is deferred by
default while Firestore serving remains disabled. It exits green after a
no-op gate and skips the Docker build and protected Terraform deploy job unless
repository variable `ENABLE_METADATA_SERVICE_DEPLOY` is set to `true`. Manual
dispatches also require `deploy_metadata_service=true`.
