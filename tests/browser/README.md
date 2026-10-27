# Catalog browser smoke tests

This private test package builds the catalog through `scripts/catalog_site.py`
and runs the generated shell, modules and real MapLibre/PMTiles libraries in
Chromium. The tests click rendered vector points and inspect their release-bound
metadata, then exercise denied selections and a delayed historical response.
No production source is replaced and no map objects are mocked.

From the repository root, after following the repository Python environment
instructions:

```sh
uv sync --locked
npm ci --ignore-scripts --prefix api/typescript
npm run build --prefix api/typescript
npm ci --ignore-scripts --prefix tests/browser
cd tests/browser
npx --no-install playwright install --with-deps chromium
npm test
```

The standalone setup above provisions system dependencies when needed. Shared
validation uses the prepared Ubuntu runner or local preflight image and runs
`playwright install chromium` without reinstalling OS packages. Browser launch
and rendering failures still fail validation.

The exact Playwright version pins Chromium. CI runs Node22 and Python3.12.
MapLibre and PMTiles versions match `web/catalog/map-preview.js`; update both
pins and the exact transport allowlist together when upgrading the product.
Missing Chromium, software WebGL support, unexpected network calls, test skips,
and browser errors fail the check. There are no test retries or visual golden
files. Native geospatial tools are not needed for ordinary test runs.
Optional Playwright Git metadata capture is disabled because its CI diff
collector performs a depth-one fetch. The shared validation plan records the
exact tested revision, and browser execution must preserve full checkout history.

The loopback server uses port4179. Output is retained beneath
`${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}/catalog-browser-smoke/`:
the generated site, inputs, tiny fixture objects, JSON report, screenshots,
request logs and failure traces. CI uploads this directory for seven days.
These are synthetic data; there are no credentials or live cloud responses.
Local concurrent runs should use separate work roots and cannot share port4179.

Only HTTP boundaries are simulated: the exact pinned CDN libraries are served
from locked npm packages, basemap tiles use a deterministic image, and the
release indexes, generation-pinned artifacts and restricted signer responses
come from tiny fixtures. Any other external request fails. The main catalog
JSON and app resources are served unchanged from the production generator.

To prove that real wrong tiles cannot pass as historical features, run the
acceptance-only negative control (expected nonzero exit):

```sh
CATALOG_BROWSER_NEGATIVE_CONTROL=wrong-old-tiles npx --no-install playwright test --grep 'public latest'
CATALOG_BROWSER_NEGATIVE_CONTROL=stale-inspector npx --no-install playwright test --grep 'late historical'
CATALOG_BROWSER_NEGATIVE_CONTROL=large-color-filter npx --no-install playwright test --grep 'color labels filter'
```

The second control removes the stale-result guard only in the disposable
generated app, proving the delayed-response test catches that regression. Normal
builds remain byte-identical to production static source. SwiftShader may emit
an explicitly classified `GPU stall due to ReadPixels` performance warning
during screenshots; warnings are retained in evidence, not silently discarded.

The color-filter control restores the old 8,000-ID cutoff only in the disposable
generated map module. The test must fail because the other category remains
visible. Pixel checks omit map controls so antialiased attribution text cannot
be mistaken for data features.

The production-matching MapLibre5.9.0 dependency has a known sanitizer advisory;
see the [dependency review](DEPENDENCIES.md). This test package does not claim a clean
dependency audit or silently upgrade the production runtime.

For these targeted controls use the pinned Playwright CLI directly, as shown,
so the full-suite count gate cannot supply a misleading failure.
Inspect the report: the intended identity or stale-inspector assertion must
fail. A setup error or an unrelated failure does not validate the control.

This coverage does not prove live IAP, CDN/CORS or GCS retention configuration;
it does not cover Firefox/Safari or replace the broader unit-test matrix.
FlatGeobuf download controls are URL-checked only; their fixture bytes are not a
valid FGB. See [fixture provenance](fixtures/README.md) for the real PMTiles.

Comparison acceptance scenarios run the real catalog-viewer routes and SQLite
engine against local pinned fixture files. The union fixtures exercise green
new geometry, red removed geometry, yellow metadata changes, and faint unchanged
geometry. The polygon scenario reuses numeric IDs across generated-contract resets
and checks actual screenshot interior pixels, with no map instances mocked. The Before polygon bundle uses a real canonical historical v1 FGB and combined
hash sidecar, covering the coral failure. Tests cover collapsed details, pagination, property absence/null, keyboard
operation, narrow layout, cancellation and a delayed start. They also hold a real
classification stream to prove filters remain disabled until colors are ready,
truncate its footer to prove incomplete results fail visibly, and verify two
streams are reused when panning or switching basemaps. Polygon clicks inspect
both pinned releases across ID resets, preserve identical overlapping hits, and
discard delayed metadata after a selection change. `comparison_server.py`
uses synthetic IAP headers at the HTTP test boundary and never contacts GCS.
The overlapping-point scenario checks actual red, green and yellow pixels above
unchanged gray objects at exactly the same coordinates, verifies only matched
edited IDs receive field highlights, and preserves all overlapping hits. It clicks all four category buttons, verifies filtered
hits and table membership, verifies toggles preserve the camera and the separate
extents button zooms, and checks basemap persistence and release
reset. Geometry-only polygon inspection withholds field highlights across identity
resets. Comparable point inspection and paired table inspection assert yellow
source-property cells, including absent versus explicit null.

Each scenario has its own real comparison job store, retaining the production
job limits without accumulating retained jobs from earlier scenarios.
Set `CATALOG_BROWSER_PORT` to use another loopback port for a concurrent local run.

The static-catalog scenario removes the comparison endpoint, checks the visible
availability and CLI guidance, then verifies keyboard close and historical
browsing/inspection still work.

Historical comparison-map denial stays visible while the table is searched and
inspected; changing Before/After selections automatically retries using their pinned inputs.

The original catalog scenarios include generated TypeScript
execution, inline copying/layout, removal of workspace import controls and WDPA execution status. The WDPA scenario
preserves each realm's published release alongside overall execution failures,
newer running executions, cancellation and stale observations. The SDK build in the setup above is
required for the generated integration test.

The fixture builder copies compiled browser SDK modules into the disposable
site solely for integration-code execution. The production catalog shell and
modules remain unchanged. Tests preserve synthetic captured artifact storage
independently from mutable index pointers to exercise replacement/retention.

The three `usage.spec.mjs` scenarios also exercise the private usage endpoint's
authentication/missing-summary behavior, report sorting/filters and safe DOM
rendering, and independent stale-worker detection. The complete browser contract
requires all twenty-eight scenarios, with no skips or retries. Colorizer coverage checks
that a category with 8,001 metadata IDs hides other categories, reports complete
dataset counts, and toggles without moving the camera. Tile-backed color fields
report loaded tile counts and filter both rendering and feature inspection.

Eight additional scenarios replace Python source-text pins with fixture-site
behavior: docs and localized metadata controls, private/internal signed FGB
downloads and PMTiles sessions, identifier versus measurement coloring and
focused zoom, oversized sidecar refusal, tile-only/no-field coloring, direct
GCS hosting, and bounded API lookup before public streaming. The color fixture uses five display points plus four off-tile
records; numeric legends follow visible geometry and identifiers keep discrete
colors across basemap changes. Existing release/signing/cache/cancellation unit
tests and all original browser scenarios remain in place.

The legacy declaration check for restricted `FetchSource` credentials remains
in Python: PMTiles4.3.0 ignores its third argument. These scenarios prove
signer/session cookies and pinned Range requests, but do not claim tile-cookie delivery.

The identifier negative control deliberately changes only the disposable map
module to color identifiers as measurements. This must fail the basemap color
assertion, rather than setup or the full-suite count gate:

```sh
CATALOG_BROWSER_NEGATIVE_CONTROL=identifier-gradient npx --no-install playwright test --grep 'sidecar colorization'
```
