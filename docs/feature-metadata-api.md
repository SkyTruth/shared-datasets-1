---
title: Feature Metadata API
description: Contract and operations for release-oriented vector metadata lookup.
last_updated: 2026-06-03
audience: Shared-datasets maintainers and consuming application backend owners
---

# Feature Metadata API

Release-oriented vector assets publish full feature metadata outside PMTiles.
The durable source is the release feature model, release manifest, canonical
FGB, and `.metadata.ndjson.gz` sidecar in GCS. When serving is enabled,
Firestore is a rebuildable serving index loaded from that sidecar.

## Endpoints

```http
POST /v1/assets/{slug}/releases/{release}:lookup
```

`release` is either `latest` or `YYYY-MM-DD`. The service resolves the release
from `_catalog/releases/{slug}.json`; every successful response includes
`resolved_release`.

The service is IAP-protected for all assets at launch. Consuming browser apps
should call their own backend, and that backend should call the metadata
service.

`lookup` is keyed by the `feature_id` values emitted in PMTiles. Use `lookup`
for browser/user URL workflows that carry those public handles.
`feature_id` values must be unique, nonblank, and match `^[A-Za-z0-9]{1,64}$`.

## Feature ID Request

```json
{
  "ids": ["1", "2"],
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
      "feature_id": "1",
      "found": true,
      "geometry_hash": "sha256:...",
      "properties_hash": "sha256:...",
      "properties": {
        "feature_id": "1",
        "name": "Example"
      },
      "provenance": {
        "source": "Provider release"
      }
    },
    {
      "feature_id": "missing",
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

Duplicate IDs preserve request order in `items`; the backend lookup is
deduplicated. Missing IDs are item-level `"found": false` results in a `200`
response after the index is confirmed ready. Lookup requests are keyed only by
`feature_id`; found items return that value as the top-level `feature_id`.
Unknown fields are rejected against the release schema before index lookup, even
if every requested ID is missing. Explicit valid fields that are absent from a
particular document return `null`.

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

- `400`: invalid JSON, invalid IDs, invalid fields, or request limit exceeded.
- `401`: IAP identity missing.
- `403`: IAP identity is outside the allowed domains.
- `404`: unknown asset, unknown release, or no latest release.
- `409`: Firestore serving index is not ready, including while the release
  carries the `inactive_firestore_serving` policy.
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

Firestore metadata serving is inactive for this refactor. Release manifests and
release indexes should record:

```json
{
  "index_load_status": "Firestore metadata serving is inactive",
  "index_status_policy": {
    "mode": "inactive_firestore_serving",
    "path": null
  }
}
```

While this policy is present, valid lookup requests return
`409 index_not_ready` before any Firestore lookup.

Do not dispatch `.github/workflows/feature-metadata-index-load.yml`, rebuild a
Firestore database, or load production/preview indexes as part of this contract
change. `scripts/feature_metadata_index.py` and the workflow files remain as
dormant implementation plumbing only. Local dry-run validation may still inspect
sidecar/schema/manifest bundles, but it must not write Firestore or publish
serving index records.

Operational checks while serving is inactive:

- Sidecar row count matches the release schema and manifest.
- PMTiles lookup properties contain `feature_id` only.
- Canonical metadata sidecars preserve `feature_id`, `geometry_hash`,
  `properties_hash`, `properties`, and provenance.
- Release manifests and release indexes both carry the inactive Firestore
  serving policy.

The production `Feature metadata service deploy` workflow is deferred by
default while Firestore serving remains disabled. It exits green after a
no-op gate and skips the Docker build and protected Terraform deploy job unless
repository variable `ENABLE_METADATA_SERVICE_DEPLOY` is set to `true`. Manual
dispatches also require `deploy_metadata_service=true`.
