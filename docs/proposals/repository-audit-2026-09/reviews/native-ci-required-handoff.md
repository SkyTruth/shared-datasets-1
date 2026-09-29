# Required native CI implementation handoff

Status: implementation complete and source frozen for independent supervisor
review on 2026-09-24. This is not merge approval and does not claim a native
GHA execution for this branch.

Branch: `codex/fix-required-native-ci`. Starting commit:
`da2f61b28d5d7e1ae10d9aa32cb416769cf90be1`.

## Changes

- `.github/workflows/ci.yml`: native selection now includes the shared release
  model, the result checker and its tests, and the Python project/lock files.
  The native command uses strict xfail behavior and chains the result checker
  after successful pytest; its existing failure artifact upload is retained.
- `scripts/check_geospatial_test_results.py`: one dependency-free checker
  requires exactly one passing result for each of the four existing native
  fixtures. Skipped, failed, errored, missing, duplicate, unreadable, and
  malformed results fail clearly. Summary counts do not establish success.
- `tests/test_geospatial_ci.py`: filter coverage, exact-result and CLI refusal
  cases, six actual miniature pytest runs (pass, skip, xfail, xpass, failure,
  setup error), and execution of the real shell chain with controlled exit
  codes. The shell probe confirms pytest failure prevents checker execution
  and checker failure fails the step.
- This handoff and the approved plan are preserved under
  `docs/proposals/repository-audit-2026-09/`.

## Evidence and validation

Before the workflow edit, the new filter assertion failed specifically for
`scripts/release_feature_model.py`. The original failure output is retained in
the task evidence directory as `missing-filter-before.txt`.

Commands below used the repository's existing uv environment without syncing
or installing dependencies. Root's fallback Git executable was used for
checks that shell out to Git; the first static-guardrail attempt encountered
the host's Xcode-license Git block, and the corrected PATH run passed.

- `uv run --no-sync pytest tests/test_geospatial_ci.py`: **9 passed**.
  This includes six real pytest-to-JUnit probes and the shell failure probes.
- `uv run --no-sync ruff check scripts/check_geospatial_test_results.py tests/test_geospatial_ci.py`:
  passed.
- `actionlint .github/workflows/ci.yml`: passed.
- `uv run --no-sync python scripts/repo_guardrails.py check-static`: passed.
- `git diff --check`: passed.
- The actual six-file native-suite selection was run with the native opt-in
  unset: **90 passed, 4 skipped**. The resulting JUnit XML confirmed the exact
  repository classname identities below. Running the checker against this
  report exited **1**, naming all four skipped fixtures. Portable test skips
  therefore remain available but no longer satisfy the native job contract.

Verified repository JUnit identities:

| Classname | Test name |
|---|---|
| `tests.test_raster_standards.RasterCogIntegrationTests` | `test_tiny_cog_validates_with_metadata` |
| `tests.test_wdpa_monthly.WdpaMonthlyIntegrationTests` | `test_fixture_zip_builds_mixed_geometry_outputs` |
| `tests.test_sea_ice_daily.SeaIceDailyIntegrationTests` | `test_synthetic_raster_builds_fgb_and_pmtiles` |
| `tests.test_eamlis_monthly.EamlisMonthlyIntegrationTests` | `test_fixture_geojson_builds_stable_fgb_output` |

The real report is retained as `actual-portable-pytest.xml` beneath this task's
`evidence/native-ci-required/` directory. It is proof of names and refusal, not
proof of successful native execution. No native tools were installed or
repaired. The final branch still needs its normal GHA Docker job to pass all
four fixtures and the checker; historical passing #154 runs are not evidence
for this workflow change.

## Invariant-first review

- Invariant enforced: selected native CI cannot succeed unless each required
  fixture has one passing JUnit result and pytest itself succeeds.
- Boundary changed: CI success after pytest, with one exact-result validator.
- Code removed: none; the existing source filter was extended in place.
- Internal handling removed: none.
- Fallbacks added: none.
- Fallbacks rejected: summary-only success, ignored checker errors, optional
  required-test lists, and success on missing/renamed fixtures.
- Deletion candidates left in place: existing fixture skip probes remain
  intentional boundary handling for portable local runs.
- Remaining uncertainty: actual native execution of this branch awaits GHA.

No ingestion, allocator, public API, persisted data, dependency versions,
production infrastructure, remote objects, or repository settings changed.
The implementation agent performed no staging, commit, push, PR, merge,
workflow dispatch, network, or environment installation operation.

## Supervisor review

Read the three source-file changes and rerun the focused test module. Verify
that the first checker refusal preserves the job's failure status and that
all-four-success requires exact names. After any approved commit/PR, inspect
the native job at that exact head and require the checker success line plus
the four fixture passes before calling the recommendation complete.
