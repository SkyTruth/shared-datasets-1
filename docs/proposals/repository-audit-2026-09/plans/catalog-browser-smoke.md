# Catalog rendered browser smoke test — approval plan

Status: supervisor `PLAN APPROVED` on 2026-09-24; implemented and awaiting independent review. See ../reviews/catalog-browser-smoke-handoff.md.
Base: main `da2f61b28d5d7e1ae10d9aa32cb416769cf90be1`.
Branch: `codex/test-catalog-browser-smoke` (supervisor creates worktree).

## Evidence and invariant

The historical consumer fix has merged, but ordinary CI exercises JavaScript
through source checks and fake DOM/map orchestration. Those tests cannot prove
that the generated static bundle loads its modules and pinned CDN libraries,
creates a real WebGL map, renders actual PMTiles, and joins a clicked feature to
the selected release's metadata. Prior manual browser QA demonstrated these
behaviors, but lives only in a temporary fixture server.

Invariant: CI success requires the real generated catalog shell, app modules,
MapLibre, PMTiles reader, canvas and DOM to work together. A displayed feature's
identity and hydrated label must belong to the same selected immutable release;
a refused selection must remove the previously displayed map and inspector.
Bad states: green smoke results with fake map objects, blank/unloaded canvas,
latest tiles plus historical metadata, stale inspector content after refusal,
unexpected external network access, silently skipped or retried browser tests.
Boundary owner: the test harness owns deterministic HTTP fixtures, never product
behavior. Production code owns rendering, selection and identity checks.

Failure class: test coverage gap. There is no claim of a new product defect.
Existing unit tests remain useful; this does not replace their broad edge matrix.

## Smallest complete implementation

1. Add an isolated private npm test package at `tests/browser/`, not a frontend
   build framework or a dependency of the published SDK. Lock exact direct
   dependencies: `@playwright/test` 1.62.1 (matches the available bundled runtime),
   `maplibre-gl` 5.9.0 and `pmtiles` 4.3.0 (match production URL constants).
   The npm lock records transitive versions/integrities. Chromium is installed
   by that exact Playwright package; workflow uses Node 22, existing Python3.12
   and uv0.11.8. Browser/package downloads happen during setup, never at test time.
2. Build the real production bundle with `scripts/catalog_site.py`, using
   generated tiny fixture catalog CSV/docs and release-index inputs in the
   standard temp root. Use the CLI options already present; serve its copied
   index/styles/app/map-preview/release-reference files exactly as generated.
   Do not handcraft/patch output catalog.json or inject scripts into index.html.
3. Reuse the historical QA's two tiny synthetic PMTiles archives (`a.pmtiles`,
   `b.pmtiles`) and source GeoJSON. They contain two Points with distinct IDs
   a1/a2 versus b1/b2 in layer `mapdata`, zoom0–4. No upstream licensed data.
   Provenance README records SHA256, size, exact original commands and honest
   tools: Tippecanoe2.79.0; PMTiles CLI reported dev/no commit/unknown build.
   Retain the verified archives rather than claim byte reproducibility from an
   unidentified converter. During implementation rerun magic, `pmtiles verify`,
   `pmtiles show`, and representative tile decode with resolved local versions.
   CI validates fixture SHA256 and uses the actual reader/rendering path; it does
   not need to install native geospatial tools to regenerate 2.4 KiB of fixtures.
4. Test-only HTTP transport serves exactly pinned archive generations, metadata
   gzip NDJSON and small schema responses, plus simulated signer results. Range
   requests receive correct206/Content-Range/length and absent generations fail.
   Production artifact URLs are intercepted by Playwright routing; no replacement
   fetch function, globals, test export, fake map, or product code changes.
   Same routes fulfill the exact unpkg JS/CSS URLs from npm-installed library
   bytes and raster basemap URLs from a tiny deterministic PNG. All other
   non-loopback traffic fails and is recorded as an assertion failure. Do not
   intercept app modules or core catalog-build output. Browser context disables
   service workers and uses fresh storage per test.
5. Use Chromium headless with its software WebGL support and a fixed desktop
   viewport. A missing browser/WebGL support is a failure, not a skip. Click
   real rendered points using fixture coordinates and the existing fitBounds
   geometry at known viewport size; any helper computes screen coordinates from
   fixture bounds, never consults or exposes the app's map object. Canvas must
   be visible, nonzero and receive actual PMTiles206 reads. Click success is
   asserted by exact inspector DOM IDs/labels/release; capture canvas screenshots
   and Playwright traces as evidence. Avoid platform-sensitive golden images.

## Required scenarios and assertions

A. Public asset: real generated shell lists fixture; select Latest and click the
latest point -> b2/New footprint2/current date. Select the old date, verify old
PMTiles/metadata generation requests, click -> a2/Old footprint2/old date and
assert no current label remains. Select Latest again and verify the inverse.
Check displayed/download artifact URL generation as well as actual request log.

B. Restricted selection: parameterize private and internal assets. Render a
successful exact selection first. On changing release return403 from the signer
(private) or409 generation mismatch (internal). Assert visible refusal, old
canvas removed, inspector hidden/cleared and no unauthorized artifact request.
Restore fixture response and select again to prove the app recovers normally.
Signer fixture must require date and exact expected generation, never default
missing parameters to current values. These are UI boundary tests, not live IAM.

C. Delayed old metadata: hold the old sidecar response after an actual point
click, change back to Latest and successfully inspect the current point, then
release the old response. The current inspector must remain current. Bounded
waits synchronize explicit requests/DOM conditions; no arbitrary sleeps.

At least one deliberately broken copied-bundle control must fail the positive
scenario: serve a missing map-preview module, or swap historical archive bytes
while retaining old metadata. Run this separately during acceptance and retain
its expected nonzero report. It is not a product fallback or a permanently
failing CI job. This demonstrates that mock responses cannot manufacture a pass.

## Files and CI

- `.github/workflows/catalog-browser-smoke.yml`: PRs, main pushes, manual run;
  contents:read only, no secrets/environments/cloud auth; one bounded Chromium
  job, pinned dependency installs, `uv sync --locked`, production build, npm test.
  Run on every PR initially to avoid path-filter blind spots and required-check
  pending states. timeout10minutes; one worker, retries0. Upload traces,
  screenshots, fixture request log and reports with short retention on failure
  (and concise success report). No production deployment/dispatch.
- `tests/browser/package.json`, `package-lock.json`, `playwright.config.mjs`.
- `tests/browser/catalog.spec.mjs`: scenarios and transport boundaries.
- `tests/browser/build_fixture.py`: tiny deterministic input generation and
  invocation of the real builder; no independent catalog schema implementation.
- `tests/browser/serve.mjs`: loopback static server only if Playwright webServer
  cannot use existing Python http.server; prefer existing server where possible.
- `tests/browser/fixtures/{old,new}.{geojson,pmtiles}` plus provenance README
  (PNG may be a small inline constant instead of another tracked binary).
- `tests/browser/README.md`: commands, support limits, temp outputs/fixture upkeep.
- focused `docs/catalog-web-preview.md` Validate addition linking the runner.
- `docs/proposals/repository-audit-2026-09/plans/catalog-browser-smoke.md`: approved
  plan preserved with branch, not only in temp; no need to import the whole B
  archive or update other agents' files.
- `.gitignore` only if package-local outputs/node_modules are not already ignored;
  actual browser output and catalog build go under standard temp root.

No change to `ci.yml`, SDK, production browser source, static URLs, publisher,
release/index format, auth policy, infrastructure, or feature-ID allocation.
Main includes the merged D consumer behavior. Unmerged B is not a prerequisite.

## Completion gates / validation

- Supervisor approves this plan before edits/install; all work in assigned tree.
- Record locked package versions and resolved Chromium version; `npm ci` clean.
- Fixture provenance/hash/native format checks complete; archives tiny.
- Run full new browser suite; zero skips/retries, real screenshot/request evidence.
- Run broken-resource/identity negative control and show expected failed test.
- Run focused existing catalog build/JS/reference tests, Python Ruff on harness,
  JS syntax checks, workflow parse/guardrails, `git diff --check`.
- Independent supervisor review repeats suite from clean package install where
  practical and reviews no network escapes, no fake map and no source mutation.
- Handoff includes exact files, commands, versions, count/results, screenshots,
  negative control, runtime limits, cleanup paths and removal pass. Stop for
  final review; supervisor owns staging/commit/push/PR decision.

Remaining limits: Chromium only; synthetic transport and data do not prove live
GCS retention, real IAP/CDN policy, CORS deployment, every locale, mobile layout,
Firefox/Safari, or native ingestion correctness. Downloads/installs may need
network authorization; unavailable local browser support is reported and must
be resolved in CI before readiness is claimed. No live dataset/network access.
No production code is removed because the missing invariant is in CI coverage;
no new product fallback or compatibility layer is justified.


## Implementation amendments accepted during supervision

The original archives embedded old temporary paths. Equivalent tiny fixtures
were regenerated using relative filenames and explicit names, then verified and
decoded again. Their actual converter version is disclosed, not presented as a
pinned reproducible native build. A valid neutral256x256 raster PNG is checked in
instead of the proposed inline pixel. The test clicks the upper-right a2/b2 point.

The suite also includes an acceptance-only stale-inspector negative control that
removes the guard from disposable generated output. It fails by visibly replacing
current metadata with historical metadata. The normal late-response scenario
waits for complete body transfer and browser paint before checking current state.
Latest as well as historical generation requests are explicitly asserted.

The package runner rejects skipped/flaky/empty results, even when Playwright
would normally exit successfully. Known SwiftShader readback performance warnings
are retained and narrowly classified; unexpected browser errors remain failures.

The MapLibre production-matching pin has a disclosed advisory. See the separate
[dependency review](../reviews/catalog-browser-dependency-review.md); no runtime
upgrade or live-cloud security claim is included in this test-only branch.
