---
title: Feature Metadata API
description: Contract and operations for release-oriented vector metadata lookup.
last_updated: 2026-10-01
audience: Shared-datasets maintainers and consuming application backend owners
---

# Feature Metadata API

Release-oriented vector assets publish full feature metadata outside PMTiles.
The durable source is the release feature model, release manifest, canonical
FGB, and `.metadata.ndjson.gz` sidecar in GCS. Current catalog and
feature-preview viewers serve same-origin lookups from GCS sidecars. The
standalone Firestore endpoint and index loaders are retired. See
[retirement scope and infrastructure rollout](metadata-stack-retirement.md).
Canonical FGB artifacts and canonical metadata sidecar records always include
`feature_id`, `geometry_hash`, and `properties_hash`. PMTiles are lightweight
lookup tiles and expose only `feature_id` as a feature property.

## Endpoints

```http
POST /v1/assets/{slug}/releases/{release}:lookup
```

`release` is either `latest` or `YYYY-MM-DD`. The service resolves the release
from `_catalog/releases/{slug}.json`; every successful response includes
`resolved_release`.

These routes are served by the IAP-protected catalog viewer and feature-preview
viewer. Browsers call their viewer on the same origin. Other applications can
read generation-pinned release sidecars through the SDKs; there is no separate
metadata service deployment.

`lookup` is keyed by the `feature_id` values emitted in PMTiles. Use `lookup`
for browser/user URL workflows that carry those public handles.
`feature_id` values must be unique, nonblank, and match `^[A-Za-z0-9]{1,64}$`.
Use `geometry_hash` from found sidecar/API records as the stable
geometry-equivalence key when an application needs to group or de-duplicate
footprints after loading metadata. Do not use `geometry_hash` or
`properties_hash` as URL lookup handles.

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
  "sidecar_uri": "gs://skytruth-shared-datasets-1/.../releases/2026-05-01/example-asset.metadata.ndjson.gz",
  "sidecar_generation": 126,
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
  }
}
```

`sidecar_uri` and `sidecar_generation` describe the identity actually enforced by
the serving backend, not merely the resolver's requested identity. The GCS
sidecar backend reports its pinned/cache identity. An injected backend that
does not enforce source identity returns null for both fields. A successful
lookup alone does not prove a particular same-date sidecar generation. Consumers
joining exact tiles must match asset, concrete date,
sidecar path, and generation before enrichment; an unverified result leaves
compact tile properties available.

Duplicate IDs preserve request order in `items`; the backend lookup is
deduplicated. Missing IDs are item-level `"found": false` results in a `200`
response after the index is confirmed ready. Lookup requests are keyed only by
`feature_id`; found items return that value as the top-level `feature_id`.
Found items also return top-level `geometry_hash` and `properties_hash`.
`geometry_hash` is stable for geometry-equivalent footprints and can be used by
consumers to group or de-duplicate loaded sidecar/API records.
Requested fields absent from a particular record return `null`. The viewer
validates request shape and limits; it does not validate field names against
the release schema.

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
- `409`: release sidecar is absent, malformed, or unavailable at the pinned generation.
- `413`: response would exceed 10 MiB.
- `503`: transient serving backend failure.

## Cache And Validators

The service returns `Cache-Control: no-store` while it is IAP-only. Successful
lookup responses include an ETag, and callers may use `If-None-Match` for
repeat requests.

## Locale-Specific Sidecar Downloads

For API-backed catalog feature inspection, the browser first calls the
same-origin lookup API for the clicked `feature_id` values:

```http
POST /v1/assets/{slug}/releases/{captured_release}:lookup
```

This keeps click inspection bounded for large sidecars such as event feeds.
The browser verifies the returned canonical sidecar identity against the mounted
layer. The API returns canonical values, so the inspector labels them as source
language when a different metadata language is selected. If
that endpoint is not available, the static public catalog viewer falls back to
the release sidecar. Public assets resolve the sidecar from the hydrated release
index and fetch it directly from:

```text
https://tiles.skytruth.org/artifacts/{bucket-object-path}
```

For authorized private sidecar inspection through the IAP-protected catalog
viewer, the browser calls the download resolver for one metadata sidecar URL:

```http
GET /api/download-url?slug={slug}&format=metadata&version={captured_release}&generation={generation}&locale=es
```

The resolver first looks for `{asset-slug}.metadata.es.ndjson.gz` in the
selected release's `files` list. If that materialized localized view is absent,
it falls back to the canonical `{asset-slug}.metadata.ndjson.gz`. Successful
responses include `requested_locale`, `resolved_locale`, and
`metadata_locale_fallback` so clients can log fallback behavior, but the browser
still fetches exactly one metadata sidecar and parses the same record shape.
When the resolver is used for public assets, `download_url` is a public Cloud
CDN artifact URL under
`https://tiles.skytruth.org/artifacts/{bucket-object-path}?generation={generation}`. For private
production assets, the resolver may return one signed Cloud CDN URL under
`https://tiles.skytruth.org/private/{bucket-object-path}`. Local development,
feature-preview buckets, or deployments without metadata CDN signing configured
may still return one signed GCS URL. Clients must treat `download_url` as an
opaque sidecar URL and must not fetch a translation overlay or merge
translations in the browser.

Localized sidecars are generated during publish/build preparation from
the canonical sidecar and `{asset-slug}.metadata-translations.csv`. Translation
rows are keyed by `feature_id`, property field, locale, and source-value hash.
Rows whose hash no longer matches the canonical property value are stale; the
generator reports and skips them so the localized view falls back to canonical
properties for that field. Prepare the CSV, every maintained locale sidecar, and
the coverage manifest together before staging a reviewed publish plan. The local
materializer's `--manifest` option records their hashes and coverage. The approved
mutation workflow rejects incomplete bundles; catalog deployment follows that
workflow directly. WDPA uses its existing publication owner for current-release
translation edits, preserving base artifact snapshots and the feature-ID counter.
The committed manifest supplies the next scheduled build's translation inputs.

Asset documents can declare maintained `translation_locales` and
`translation_fields` together. The generated catalog exposes both lists; CSV
columns use semicolon-separated values. Explicitly requesting those locales in
the local materializer produces a sidecar even when the translation CSV has no
rows for a locale. Only nonblank string values in the approved fields enter
coverage. Existing explicit non-string translations remain supported, but do not
enter this denominator.

New localized records include an optional `translation` block with `locale`,
`state` (`complete`, `partial`, or `fallback`), `translated_fields`,
`fallback_fields`, `machine_fields`, and `human_reviewed_fields`. The canonical
release, identity, hashes, and provenance remain unchanged. Consumers of older
sidecars must tolerate the block being absent.

The materialization report includes a `translations` block (`schema_version: 1`)
keyed by locale. Each locale reports `translatable_values`, `current`, `stale`,
`missing`, `orphan`, `removed_fields`, `coverage`, and `review_states`. Current,
stale, and missing partition eligible current values; old CSV history never
increases the denominator. Usable AI output is current without expert approval;
failed current rows are missing. Coverage is `current / translatable_values`, or
`null` when no values are eligible. The WDPA publisher writes this same block
into its final manifest alongside the CSV and locale artifact generations.

The materializer also writes a local `*.translation-debt.{locale}.csv` containing
unresolved `feature_id`, `field`, `locale`, `source_value_hash`, and `source_value`
columns. These files support subsequent review; this build step does not send
notifications or publish debt files.

## Operations

Both viewers construct `GcsSidecarFeatureIndex`. A lookup resolves a concrete
release and opens its sidecar with a generation precondition. The browser also
checks the returned path and generation against the mounted layer before joining
metadata. Retiring Firestore does not change this route, the download resolver,
the SDK sidecar readers, or localization materialization.

Release manifests and release indexes retain these persisted compatibility
fields:

```json
{
  "index_load_status": "Firestore metadata serving is inactive",
  "index_status_policy": {
    "mode": "inactive_firestore_serving",
    "path": null
  }
}
```

The words identify the established serialized format. They do not enable a
Firestore backend or prevent sidecar lookups. Do not rename them or rewrite
historical release objects as part of retirement.

Validate downloaded local sidecar/schema/manifest bytes without credentials:

```bash
uv run python scripts/validate_feature_metadata.py \
  --asset-slug example-asset --release 2026-05-01 \
  --sidecar "$WORK_ROOT/example-asset.metadata.ndjson.gz" \
  --schema "$WORK_ROOT/example-asset.schema.json" \
  --manifest "$WORK_ROOT/example-asset.manifest.json"
```

Use a named workspace from [local temp workspaces](standards/local-temp-workspaces.md).
The validator checks release identity, row IDs and hashes, uniqueness, schema
projection, manifest artifact generations, sidecar/schema checksums, and declared
feature count. Optional `--sidecar-uri`, `--sidecar-generation`, `--schema-uri`,
`--schema-generation`, and `--manifest-uri` cross-check download evidence. Pin
the manifest generation when downloading; a manifest cannot embed its own
generation. This CLI has no remote write path.

Keep the release-model, publisher, ingestion, localization, PMTiles, catalog,
and viewer validation suites. PMTiles lookup properties remain `feature_id`
only; canonical FGB and sidecar records retain both hashes, full properties,
and provenance. No Firestore index-load or standalone service-deploy workflow
remains.

## Release comparison

The authenticated catalog viewer compares generation-pinned canonical release
sidecars, schemas and manifests through `scripts/compare_releases.py` and the
same-origin `/api/comparisons` job routes. The catalog offers release selection,
complete change counts, a searchable table, source property inspection, a union
map (green novel geometry, red removed geometry, yellow identical geometry with
altered metadata, faint unchanged geometry) in the primary map and complete
report export. Before/After selections rerun automatically; Close comparison
restores ordinary browsing. Summaries use compact tables. Geometry colors remain
available when feature-ID contracts differ. Localization
is excluded from source changes. Identity incompatibility withholds authoritative
feature classification; static catalogs retain visual inspection and a local CLI
route. See [comparison semantics, authorization, budgets and CLI](compare-releases.md).

### Translation maintenance notices

After a successful publication, scheduled WDPA/e-AMLIS jobs and reviewed manual
publishes use one shared completion hook. A notice is due when any maintained
locale has a positive denominator and
`100 * (stale + missing) >= translatable_values`. The comparison is inclusive and
uses integer counts. One message combines all locales; orphan/removed-field
history and pending expert review do not trigger it.

The hook reads generation-pinned localized sidecars from the committed manifest.
Their `fallback_fields` retain current source values, which are streamed into
`gs://{bucket}/_scratch/translation-debt/{asset}/{release}/{locale}.csv` with
`feature_id,field,locale,source_value_hash,source_value`. Only unresolved values
are exported; these files are noncanonical maintenance evidence. Public notices
include a copyable prompt with the full worklist references and up to 25 complete
CSV sample rows, bounded by Slack's message limit. The prompt requests the
existing seven-column translation CSV, preserves IDs and source hashes, and
marks generated values `machine_placeholder` with `awaiting human review` notes.
Proper names use known official target-language forms or remain unchanged.
Oversized rows and rows containing a code fence stay in the full export; source
values are never truncated. Internal/private notices show counts and GCS
references only.

A create-only `{asset-root}/runs/{release}.translation-notice.json` claim prevents
repeat attempts, including concurrent jobs and reviewed edits. Its status starts
as `claimed`, becomes `delivered` only after a confirmed webhook response, or
`delivery_unknown` after an unconfirmed attempt. A crash/export failure can leave
`claimed`; neither state is a delivery receipt. The hook intentionally does not
retry a claimed attempt because Slack webhooks do not offer exactly-once delivery.
Notification failures never roll back or fail a published release. Unconfirmed
delivery is logged as a warning for operators, with automatic retry suppressed.

Runtime credentials need access to the existing Slack secret and create-only
access to their own debt-export prefixes. The protected Scheduled ingestion
deploy IAM sync owns those narrow grants. After its reviewed main changes apply,
use the existing protected WDPA/e-AMLIS deploy workflows to update job images and
secret references. No local production apply is part of this rollout.
