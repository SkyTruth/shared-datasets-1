# Required native geospatial CI checks

Status: approved by the supervising agent on 2026-09-24; implementation
completed for independent review. This records the approved plan, not merge
approval. The original plan was submitted and held before implementation.

## Baseline and defect

Implementation branch: `codex/fix-required-native-ci`, starting from main
`da2f61b28d5d7e1ae10d9aa32cb416769cf90be1`. Root creates the worktree and owns
Git operations. PR #154 is reference context, not a dependency of this fix.

`.github/workflows/ci.yml` currently omits `scripts/release_feature_model.py`
from the geospatial change regex. A change solely to this shared model can
therefore avoid the native job. The four required native fixtures use unittest
skip conditions when their toolchain is absent or unrunnable, even inside the
native CI job. Pytest exits successfully if these tests skip, so the CI job can
report green without proving the COG, WDPA, sea-ice, or EAMLIS native paths.
Existing `tests/test_geospatial_ci.py` exercises the actual regex but does not
include the shared release model in its expectations.

## Invariant and boundaries

- Invariant: a successful selected native CI job has executed and passed each
  of the four named native fixtures exactly once. Changes to the shared release
  model, native result contract, and relevant tests select this job.
- Impossible success states: missing/renamed/deselected required tests,
  unexpected skip or expected failure, setup error, assertion failure, duplicate
  required result, missing or malformed result file.
- Owner: CI's native-job success boundary, not every portable test's tool probe.
- Boundary: pytest produces JUnit results; a small checker consumes exact test
  outcomes. Pytest's exit status still rejects all other test failures.
- Ordinary local/full-suite runs keep existing optional native skips.
- Failure classification: missing validation at the CI success boundary.

## Smallest complete change

1. Extend the existing workflow regex with `release_feature_model` and the new
   result-checker script, plus `pyproject.toml` and `uv.lock` (dependency coverage
   explicitly approved by the supervisor on 2026-09-24). Add `test_geospatial_ci`
   to the matching test list so
   changes to the contract tests select the native job too. Keep the existing
   dispatch, push, and pull-request change-detection behavior.
2. Add `scripts/check_geospatial_test_results.py`, a dependency-free XML result
   checker with a single positional JUnit-report argument. Its fixed required
   identities are the following JUnit classname/name pairs, corresponding to
   existing nodes:

   - `tests.test_raster_standards.RasterCogIntegrationTests` /
     `test_tiny_cog_validates_with_metadata`
   - `tests.test_wdpa_monthly.WdpaMonthlyIntegrationTests` /
     `test_fixture_zip_builds_mixed_geometry_outputs`
   - `tests.test_sea_ice_daily.SeaIceDailyIntegrationTests` /
     `test_synthetic_raster_builds_fgb_and_pmtiles`
   - `tests.test_eamlis_monthly.EamlisMonthlyIntegrationTests` /
     `test_fixture_geojson_builds_stable_fgb_output`

   Each must have exactly one testcase and no skipped/failure/error child. Do
   not infer success from total counts. Missing/unreadable/invalid XML and
   wrong/missing class or method names fail clearly. No thresholds, optional
   lists, override flags, automatic discovery, or generic plugin framework.
3. Chain this checker after successful pytest inside the existing Docker
   command using `&&`, so a checker refusal fails the same step and uploads
   its existing JUnit artifact. Set pytest `xfail_strict=true` for this native
   command, so ordinary unexpected xpasses are failures too. The required
   fixtures currently have no xfail marks. Never overwrite pytest's nonzero
   exit code or use `continue-on-error`.
4. Extend `tests/test_geospatial_ci.py` with meaningful report and CLI tests,
   retaining its existing workflow assertions. Test the actual workflow regex
   against the newly covered sources and unrelated negative examples. Test
   the workflow command actually invokes the checker after pytest in the same
   failure-propagating command.

## Exact intended files

- `.github/workflows/ci.yml`
- `scripts/check_geospatial_test_results.py` (new)
- `tests/test_geospatial_ci.py`
- `docs/proposals/repository-audit-2026-09/plans/native-ci-required.md`
  (approved plan copy; root coordinates preservation)

No ingestion code, allocator behavior, native test internals, dependencies,
Docker tool versions, deploy workflows, data formats, or repository settings
change. No production credentials or data are used.

## Validation and original-defect proof

- Before editing the regex, add/run the source-filter regression and retain its
  failure for `scripts/release_feature_model.py`; it must pass after the fix.
- Checker unit tests cover all-four-pass, each individual missing node,
  renamed class/method, duplicate required node, skipped, xfailed-as-skipped,
  failed, setup error, empty report, malformed/missing file. Unrelated passing
  cases do not satisfy required identities.
- Produce actual JUnit XML with disposable miniature pytest suites under the
  named task temp directory using the same four module/class/method names.
  Exercise a clean pass and a required test skip (pytest exits zero but checker
  must fail), plus expected failure and a test failure. This validates the
  pytest-to-XML boundary instead of relying exclusively on handmade XML.
- Verify the command/checker exits nonzero on rejection, and strict-xfail
  command behavior. Retain generated result evidence outside the repository.
- Run focused `tests/test_geospatial_ci.py`, Ruff for touched Python files,
  static repo guardrails, and `git diff --check`; root independently reviews.
- Run the existing selected native-suite command locally without setting the
  native opt-in flag to confirm portable skip behavior remains available.
  This is not native execution evidence.
- Local GDAL probes previously hung and Docker is unavailable. Do not install,
  start, or repair toolchains or claim a native pass locally. After root's
  normal PR workflow authorization, the GHA native image must execute all
  four fixture nodes and the checker successfully on the final branch head.
  Existing passing #154 results do not validate this new workflow edit.

## Alternatives and removal pass

Changing every skip helper to inspect a CI environment variable would scatter
this policy across fixtures and still allow accidentally omitted tests to go
undetected. A full pytest plugin can inspect every phase but is unnecessary
for these four fixed unittest fixtures. Checking summary totals or grepping
console text is weaker and more brittle than exact JUnit testcase outcomes.
Running only four explicitly named nodes still permits pytest skips and loses
surrounding integration regressions.

Delete no existing portable skip behavior: it is useful boundary handling for
optional local toolchains. Reuse the existing filter and test module. Reject
new fallback success paths or a general test-configuration abstraction.
No public/persisted compatibility migration is needed.

## Acceptance and handoff

The supervisor approved this plan before source edits. Completion requires
focused changes, original-failure evidence, passing positive and negative
checker probes, targeted tests/lint/guardrails, a removal review, and a handoff
in `reviews/native-ci-required-handoff.md`. Keep the plan in the branch archive
so it is not lost with the local temp directory. Root stages, commits, pushes,
opens any approved PR, and verifies GHA; this agent performs none of those Git
or remote operations. The independent SDK and browser work use separate
workflows and should not conflict with this edit.
