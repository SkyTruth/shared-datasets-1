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

The exact Playwright version pins Chromium. CI runs Node22 and Python3.12.
MapLibre and PMTiles versions match `web/catalog/map-preview.js`; update both
pins and the exact transport allowlist together when upgrading the product.
Missing Chromium, software WebGL support, unexpected network calls, test skips,
and browser errors fail the check. There are no test retries or visual golden
files. Native geospatial tools are not needed for ordinary test runs.

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
```

The second control removes the stale-result guard only in the disposable
generated app, proving the delayed-response test catches that regression. Normal
builds remain byte-identical to production static source. SwiftShader may emit
an explicitly classified `GPU stall due to ReadPixels` performance warning
during screenshots; warnings are retained in evidence, not silently discarded.

The production-matching MapLibre5.9.0 dependency has a known sanitizer advisory;
see the [dependency review](DEPENDENCIES.md). This test package does not claim a clean
dependency audit or silently upgrade the production runtime.

For these targeted controls use the pinned Playwright CLI directly, as shown,
so the normal thirteen-scenario count gate cannot supply a misleading failure.
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
and checks actual screenshot interior pixels, with no map instances mocked. Tests cover pagination, full export, property absence/null, keyboard
operation, narrow layout, cancellation and a delayed start. `comparison_server.py`
uses synthetic IAP headers at the HTTP test boundary and never contacts GCS.
Set `CATALOG_BROWSER_PORT` to use another loopback port for a concurrent local run.

The static-catalog scenario removes the comparison endpoint, checks the visible
availability and CLI guidance, then verifies keyboard close and historical
browsing/inspection still work.

Historical comparison-map denial stays visible while the table is searched and
inspected; changing Before/After selections automatically retries using their pinned inputs.

The suite has fourteen required scenarios, including generated TypeScript
execution, inline copying/layout, workspace imports and WDPA execution status. The WDPA scenario
preserves each realm's published release alongside overall execution failures,
newer running executions, cancellation and stale observations. The SDK build in the setup above is
required for the generated integration test.

The fixture builder copies compiled browser SDK modules into the disposable
site solely for integration-code execution. The production catalog shell and
modules remain unchanged. Tests preserve synthetic captured artifact storage
independently from mutable index pointers to exercise replacement/retention.
