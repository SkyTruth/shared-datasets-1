# Catalog browser smoke independent cross-review

Date: 2026-09-24. Reviewer: SDK release repair subagent, independently assigned
by the supervisor. Scope: read-only source/workflow/evidence inspection on
`codex/test-catalog-browser-smoke`; no source edits, installs, network requests,
Git mutations, or browser reruns by this reviewer.

Disposition: **No blocking findings.** This is meaningful rendered integration
coverage, with bounded claims and substantive negative controls. Supervisor must
still finish its independent run and normal branch/PR review.

## Evidence checked

- Fixture builder invokes the production `scripts/catalog_site.py` CLI with
  synthetic input docs/CSV/indexes. It does not replace the normal generated
  catalog or product app/module implementations. Only the explicitly requested
  negative-control build removes one stale-result guard in disposable output.
- Exact locked MapLibre 5.9.0 and PMTiles 4.3.0 bytes are served from npm packages;
  real verified PMTiles bytes go through HTTP range handling and the real reader
  and WebGL canvas. Click coordinates come from fixture geometry/fit bounds,
  not a fake map instance or a test-only product export.
- Distinct historical/current IDs a2/b2 and labels prevent the sidecar fixture
  from manufacturing a pass when the wrong archive is rendered. Latest and
  historical artifact requests assert exact generations; historical FGB control
  is explicitly a URL-only assertion rather than a claimed valid FGB read.
- Restricted signers require exact slug/date/format/generation before returning
  fixture URLs. Refusal tests require old canvas removal, an empty hidden
  inspector, and no subsequent artifact fetch, then verify recovery.
- A browser-context route rejects unrecognized external requests; service
  workers are blocked. Normal catalog/module bytes come from the loopback
  production build. Unexpected browser errors/warnings fail, with narrowly
  classified expected HTTP 403/409 errors and SwiftShader readback warnings.
- Normal runner now requires all four scenarios to pass, with no skip, flaky
  result, or retry. Targeted negative commands use the pinned Playwright CLI
  directly, so the four-test count gate cannot falsely certify a mutation.
- Workflow is read-only, runs on every PR/main push, locks package dependencies
  and Chromium through exact Playwright, limits execution to ten minutes, and
  retains synthetic evidence for seven days. No auth/deploy or production data
  access is present.

## Inspected execution records

I independently read the existing JSON reports and their failure details:

| Record | Result and significance |
|---|---|
| `evidence/catalog-browser-smoke/report.json` | 4 passed; zero skipped/unexpected/flaky, approximately 11.6 seconds |
| `evidence/browser-negative-identity/catalog-browser-smoke/report.json` | Expected failure: historical canvas click produced b2 while assertion required a2 |
| `evidence/browser-negative-race/catalog-browser-smoke/report.json` | Expected failure at final metadata assertion: actual old-release a2 / Old footprint 2 replaced required New footprint 2 |

The delayed-response test waits for complete response body transfer and browser
frames after releasing the held historical response. Those waits alone should
not be described as a generic proof that arbitrary asynchronous decompression
has finished. The checked guard-removal control is the concrete acceptance
evidence here: it reached the final assertion and exposed the intended stale
inspector regression, rather than failing setup or network transport.

I also ran `actionlint .github/workflows/catalog-browser-smoke.yml` and
`git diff --check`; both passed. I did not independently run Chromium or native
fixture verification; those remain the implementation/supervisor evidence.

## Limits and follow-up

- Synthetic HTTP boundaries do not prove deployed IAP/CDN/CORS/IAM behavior,
  retention of live object generations, cross-browser behavior, or mobile UX.
  The branch documents these limits accurately.
- The known MapLibre attribution-sanitization advisory is disclosed in the
  separate dependency review. This suite deliberately matches production; it
  does not claim a clean dependency audit or silently change runtime versions.
  Review an upgrade of product and test pins together as separate work.
- Local concurrent executions need separate work roots and cannot both own the
  fixed loopback port. The config rejects an already-running server rather than
  quietly testing an unrelated instance.

Invariant-first assessment: this adds an executable boundary proving real
rendered release identity and refusal behavior. No product fallback, shared
state representation, or production compatibility layer was added; none is
warranted for this coverage gap.
