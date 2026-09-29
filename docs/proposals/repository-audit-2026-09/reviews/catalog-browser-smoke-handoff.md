# Catalog browser smoke — review handoff

Status: source frozen for independent supervisor review on 2026-09-24.
Branch: `codex/test-catalog-browser-smoke`.
Base: main `da2f61b28d5d7e1ae10d9aa32cb416769cf90be1`.
[Plan](../plans/catalog-browser-smoke.md) approved before implementation.

## Result

The new read-only `Catalog browser smoke` workflow runs on every PR, main push,
and manual invocation. It builds the actual catalog with its production CLI and
loads the unmodified generated shell and modules in real Chromium/MapLibre.
There are no application test hooks or fake map objects. The isolated private
npm package has exact pins and a lockfile; it is unrelated to the published SDK.

Four tests click actual PMTiles features and inspect real rendered DOM:

1. Public Latest -> historical -> Latest, with b2/New footprint2 versus
   a2/Old footprint2, exact dates, metadata and PMTiles generations, and the dated
   download URL.
2. Private403 after successful display clears canvas/inspector, fetches no denied
   artifact, then recovers after successful reselection.
3. Internal409 does the equivalent generation-refusal/recovery check.
4. Held historical metadata finishes after a successful current selection; its
   response body and browser paint finish before checking the current inspector.

HTTP fixtures supply only deterministic external boundaries: archive ranges,
sidecars/schema, release indexes and signer responses; CDN library bytes come
from the exact production-matching npm packages and basemap pixels are local.
Unexpected external requests, unknown fixture identities and unexpected browser
errors fail. Known403/409 resource errors are accepted only for the explicit
refused URLs; Chromium's exact SwiftShader readback performance warning is
retained and classified. No tests skip or retry, and the runner rejects empty,
skipped or flaky reports.

## Evidence

Clean `npm ci --ignore-scripts`: passed. Installed direct packages are
`@playwright/test`1.62.1, `maplibre-gl`5.9.0, `pmtiles`4.3.0. Actual Chromium is
151.0.7922.34 (Playwright revision1234), run locally on macOS ARM64. Ubuntu CI
execution remains pending until this branch receives a GitHub Actions run.

Final `npm test`: **4 passed in11.6s, zero skipped/retried**, including the strict
report check. The retained report includes runtime version, generation/range
requests, browser messages and screenshots showing actual vector features.

Acceptance-only negative controls both failed at the actual intended test assertion
(as recorded in the JSON report), not only at process exit or report-count checking.
The maintainer commands use the direct pinned Playwright CLI for targeted controls:

- `wrong-old-tiles`: serving the current archive as historical bytes resulted in
  an actual b2 inspector where the positive test required a2.
- `stale-inspector`: removing the stale completion guard only in the disposable
  generated app replaced the current inspector with a2/Old footprint2; the race
  test rejected it. Normal builds copy production source unchanged.

Existing focused tests: **18 passed,95 subtests** across catalog generator,
browser JavaScript and release-reference suites. Python Ruff, all six JS module
syntax checks, actionlint, static repository guardrails and `git diff --check`
passed. No product files or the main CI workflow were changed.

Both tiny PMTiles archives passed magic, `pmtiles verify`, `pmtiles show` and
representative zoom4 tile decode using native tools with20-second command bounds.
They contain a valid mapdata layer with compact feature IDs. Generated intermediate
MBTiles remain outside Git. [Fixture provenance](../../../../tests/browser/fixtures/README.md)
records hashes, commands and the converter's honest unknown build identity.

Retained evidence beneath the supervisor's standard temporary review workspace:

- `evidence/catalog-browser-smoke/`: successful report, site, input contracts,
  screenshots/request/browser/runtime attachments.
- `evidence/browser-negative-identity/catalog-browser-smoke/`: mixed-identity
  failure report, screenshot and trace.
- `evidence/browser-negative-race/catalog-browser-smoke/`: stale-result failure
  report, screenshot and trace.
- `evidence/catalog-browser-fixtures/validation.json`: native archive validation,
  decoded sample contents, output sizes/hashes and original commands.

## Changed files / review instructions

- `.github/workflows/catalog-browser-smoke.yml`: no credentials, cloud auth,
  protected environment, deployment or remote mutation;10-minute timeout and
  seven-day synthetic report artifacts.
- `tests/browser/`: lockfile/private package, actual builder input producer,
  loopback server, strict runner/config/report check, four browser tests, tiny
  fixtures with provenance and maintainer instructions.
- `docs/catalog-web-preview.md`: discoverable link to the automated browser test.
- This plan/handoff plus the supervisor's
  [dependency review](catalog-browser-dependency-review.md).

Repeat the commands in `tests/browser/README.md`; choose an independent
`SHARED_DATASETS_WORKDIR` and ensure port4179 is free. Review `context.route`
allowlisting, actual canvas clicks and inspector identity assertions. Inspect
successful screenshots and both negative-control failures. Compare generated
static modules to `web/catalog` after a normal run. Inspect workflow actionlint
output and the locked library versions against production constants.

## Limits and removal pass

This is Chromium/synthetic transport coverage, not live IAP/CDN/CORS/retention,
all browser engines or a complete visual/mobile audit. FGB control bytes are
intentionally not claimed as valid FGB. Native ingestion remains separately
covered. The MapLibre pin has a known advisory; see the dependency review rather
than treating `npm ci` success as a clean security audit.

Invariant enforced: green CI requires real product rendering and coherent
selected-release inspection, including stale and refused transitions.
Boundary changed: test infrastructure only.
Code removed: none from production; former temporary QA is now a repeatable
small test harness rather than another frontend framework.
Internal handling removed: none.
Fallbacks added: none to product.
Fallbacks rejected: map mocks, replacing generated catalog output, source globals,
silent browser skips, test retries and live network dependence.
Deletion candidates left in place: unit fake-DOM tests remain because they cover
many branches beyond this smoke suite.
Remaining uncertainty: first Linux GitHub execution and known dependency upgrade
follow-up; no production authorization or deployment readiness claim.

No Git staging/commit/push, merge, workflow dispatch, cloud/data/API write, or
main-checkout mutation was performed by this subagent. Supervisor owns final
review and the user-authorized commit/PR workflow.
