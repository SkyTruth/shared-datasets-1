# Catalog browser supervisor review

Reviewed 2026-09-24 against main `da2f61b28d5d7e1ae10d9aa32cb416769cf90be1`.
The implementation plan was approved before source edits. Accepted for a
committed branch and user PR decision; Linux GitHub Actions execution remains
pending and no deployment is approved by this review.

The supervisor read the fixture producer, strict HTTP routes, real canvas
interaction and inspector assertions, report gate, workflow, locked package
versions, fixture provenance and documentation. A separate reviewer found no
blocking issue. This branch adds test infrastructure without product hooks,
fake map objects or changes to production behavior.

Independent validation:

- Node 22 with Chromium: all four browser scenarios passed in 11.8 seconds,
  with zero skips, unexpected results or retries. The final report gate passed.
- Generated `app.js`, `map-preview.js`, `release-reference.js` and `styles.css`
  were byte-identical to their production sources.
- The supervisor inspected screenshots of real current/historical points and
  reviewed generation-specific requests and report attachments.
- The stale-result negative control was repeated three times using the pinned
  Playwright CLI directly. All three failed at the intended final assertion:
  expected New footprint 2, received a2 / Old footprint 2 / 2026-01-01. The
  failures were not installation, setup, request-routing or report-count errors.
- The implementation's independent wrong-tiles control failed on actual b2
  versus required a2 and was also checked by the second reviewer.
- Existing catalog build/browser/reference tests: 18 passed, 95 subtests.
- Full-repository Ruff and workflow actionlint passed.

The original race assertion only waited for response headers. Supervisor review
required body completion, browser processing and a demonstrated guard-removal
failure before accepting it. The resulting test is evidence for this concrete
regression, not a general guarantee about arbitrary asynchronous decompression.
Normal CI requires exactly four passes. Targeted negative controls bypass that
count gate, ensuring their nonzero result reflects the actual broken behavior.

Tradeoff: maintaining pinned Chromium/library versions and tiny HTTP fixtures
costs more than source-only tests. It earns coverage of actual loading,
rendering, picking and metadata hydration that map mocks cannot provide. Live
cloud tests would add credentials, production dependencies and nondeterminism;
those remain separate operational checks. First Linux execution is still
required before declaring the workflow green in GitHub Actions.

See the separate dependency review for the known production MapLibre advisory.
No affected untrusted-attribution route was identified, but a reviewed runtime
upgrade remains follow-up work; dependency audit cleanliness is not claimed.

Plans, implementation handoff, cross-review and this decision are preserved in
the branch. Detailed reports/screenshots and three negative-control traces are
retained under the named remediation evidence directory. This test-only change
does not warrant a repo-alert block.
