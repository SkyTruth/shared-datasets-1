# Portable workspace snapshot v1

This is the authoritative contract for the catalog, Python SDK, and TypeScript
SDK. `tests/fixtures/workspace-snapshot-v1.json` is the shared cross-language
acceptance/rejection corpus. The browser validator in
`web/catalog/workspace-contract.js` is compiled unchanged into the TypeScript
package at build time. Python implements this specification and runs the same
fixtures. Changes to this contract require coordinated implementation and tests.

A document has exactly `kind: "skytruth-workspace"`, `schema_version: 1`,
`bucket`, `datasets`, and `presentation`. JSON text is at most 1 MiB and 12 levels
deep. Reject duplicate JSON keys, unknown fields, nonfinite numbers, unknown
versions, and unsupported settings. The caller supplies the trusted bucket;
never infer it from an import. There are 1–32 distinct dataset entries, each with
1–8 distinct required artifacts. All strings exclude control characters except
newlines and tabs, and reject unpaired Unicode surrogates. Metadata text is at most 16,384 UTF-16 code units.

## Exact identity

Each dataset has exactly `asset_slug`, `release`, `canonical_format`,
`access_tier`, `artifacts`, and `provenance`. Slugs are lowercase kebab-case,
at most 128 characters. Access tiers are `public`, `private`, or `internal`.
Release is a valid `YYYY-MM-DD` or explicitly unknown (`null`); never `latest`.
Capture latest once using the existing release-reference resolver. A release
date labels a release; only the URI and generation identify its bytes.

Each artifact has exactly `format`, `role`, `gs_uri`, `generation`, `size`,
`sha256`, `requested_locale`, and `resolved_locale`. Exactly one is `canonical`
and agrees with `canonical_format`. The other roles are `tiles` (PMTiles),
`metadata` (gzip NDJSON), and `schema` (JSON). Data formats supported by v1:
FGB, CSV, GeoJSON, NDGeoJSON, COG, and PMTiles. Zarr collections require a future
collection/manifest contract; v1 does not pretend one object archives a store.

URIs must name a shallow artifact in the trusted bucket, beneath
`{category}/{subcategory}/{asset_slug}/releases/{date}/` or, only when the
release is unknown, `.../{asset_slug}/latest/`. All artifacts share one asset
root. Reject empty, dot, traversal, encoded, wildcard, query, fragment,
backslash, and whitespace path components. Enforce each format's extension:
`.fgb`, `.csv`, `.geojson`, `.ndgeojson`/`.geojsonl`, `.tif`/`.tiff`,
`.pmtiles`, `.metadata[.{locale}].ndjson.gz`, and `.schema.json`.

Generation is a decimal **string**, matching `[1-9][0-9]{0,19}` and no greater
than `18446744073709551615`. No numeric coercion is allowed on import. Size is
an integer from 0 through `9007199254740991`, or unknown (`null`). Published
SHA-256 is 64 lowercase hexadecimal characters or unknown (`null`). These are
published expectations, not proof of local verification. Python's fetched
lineage records published and verified hashes/sizes separately. Portable
snapshots never include local cache paths or verified-cache authority.

Locales are `null` or lowercase field-safe codes such as `es` or `pt_br`, at
most 64 characters. Only metadata artifacts have locales. Requested and
resolved locales differ on canonical fallback. Include canonical metadata and
the selected localized companion when distinct; never join captured tiles to
new metadata. URI and role/resolved-locale pairs must be unique.

## Captured provenance

Provenance has exactly `title`, `citation`, `source`, `source_url`, `license`,
`notes`, `status`, `lifecycle_reason`, `lifecycle_date`, `successor_asset_slug`,
`consumer_guidance`, `source_version`, `release_index_uri`, `release_index_generation`,
`release_index_updated_at`, and `identity_contract`. Values are bounded strings
or `null`, except identity_contract is an object or `null`. Its allowed keys are
`strategy`, `source_fields`, `generated_id_type`, `assignment_key`,
`feature_id_column`, `geometry_hash_column`, and `properties_hash_column`;
values are strings (128 characters) or at most 32 such strings. Capture only
published identity fields. The optional release-index URI must equal
`gs://{bucket}/_catalog/releases/{asset_slug}.json`; its generation follows the
artifact generation rule. It is provenance, not a resolution dependency.

Source URLs are HTTP(S) publication links without credential/token/signature
query parameters or URL user credentials. Query keys are decoded before checking
common token/signature keys and GCS/AWS signing parameters. No signed access URLs, cookies, credentials, tokens, or local
absolute cache paths are fields of this contract. Preserve source caveats and
terms verbatim. Capturing a license does not reinterpret permission.

## Presentation

`presentation: null` is a dataset lockfile. A workspace presentation has exactly
`basemap` (`map` or `satellite`), `viewport` (object or `null`), `locale`, and
`layers`. Viewport has `center: [longitude, latitude]`, `zoom`, `bearing`, and
`pitch`; ranges are ±180, ±85.051129, 0–22, ±180, and 0–85 respectively.
Each dataset appears exactly once in `layers`, in display order, with exactly
`asset_slug`, `visible: true`, `source_layer`, and `color_field`. All selected
layers are visible in the current product; hidden layers/opacity/arbitrary
styles are unsupported and rejected. Source-layer and color-field selections
are strings of at most 128 characters or `null`, and supported only for a
single selected dataset. The viewer restores only these actual controls after
compatible layers are ready. Legend focus and selected features are not saved.

## Trust and restore

Validate the complete import before any remote request. Recheck each slug,
root, access tier, and entitlement against the current authorized catalog.
Imported tier/provenance is historical context, never an authorization grant.
Public exact artifacts can remain readable after replacement. Restricted viewer
and app routes authorize only the exact artifacts still named by the current
release index; an older replaced generation must fail without signing or
upgrading it. App routes use `authorizeSnapshotArtifact` from the server SDK.
They authenticate with the application's own session and enforce request-size
limits before invoking the helper. No unrestricted signer endpoint is added.

Establish access and availability for all required artifacts before mounting.
Failure names the exact artifact and generation; no automatic partial restore,
latest substitution, or tile/metadata mixing is permitted. Then restore supported
presentation settings. Python fetches captured generations directly with ADC
and the existing verified cache without reading the current release index.
A successfully verified cache can still provide captured bytes after remote
retention ends. Ordinary browsing of legacy assets remains available; unknown
identity does not qualify for the catalog's exact export. The viewer requires
dated indexed references for restore. Python can consume manually constructed
valid observed-generation snapshots with unknown release dates.

Exports are references to remote bytes, not archives or retention guarantees.
A preflight HEAD checks availability, exposed generation, and published size;
full metadata reads and Python downloads additionally verify published checksums.
PMTiles range rendering does not claim a full-archive checksum verification.
