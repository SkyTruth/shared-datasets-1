> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# A — Scratch cleanup handoff

**Status: READY FOR SUPERVISOR REVIEW of the approved bounded defect. Not merged or deployed.**

- Branch: `codex/audit-scratch-cleanup`
- Worktree: `${REMEDIATION_WORKDIR}/worktrees/scratch-cleanup`
- Base/HEAD unchanged: `1bf095d861d921e2378203495bd9a0da0bdf650c`
- Approval: supervisor `PLAN APPROVED` authorized the content-inference removal, obsolete `--catalog` retirement, and canonical-scan removal. A subsequent explicit approval authorized strict non-authorizing malformed marker handling and generation refusal tests.

## Behavior delivered

The periodic auditor can no longer treat matching historical bytes as publication completion. It no longer reads the catalog, lists canonical release roots, checks file extensions/checksums, or passes a content-match decision into classification. Fresh mixed/partial proposals remain; old proposals without a current valid warning get a warning rather than immediate deletion.

The existing age-based abandonment policy remains: warn when the newest object is at least 60 days old; select for deletion at 90 days only if that object's name/generation/update time still match a warning. A first warning after day 90 can be eligible on the next audit. This does not protect open PRs or establish publication completion. No receipt or lease protocol was invented.

Observed object generations are required to be positive ASCII decimal values before mutations. Marker downloads explicitly use their observed generation precondition. Marker JSON must be an object whose three observed-state fields are nonempty strings before it can authorize age-based deletion. Invalid JSON, non-object JSON, missing fields, or boolean/numeric fields remain non-authorizing; the marker's exact generation is retained so an eligible new warning can replace malformed evidence by CAS. Read races or invalid observed generations abort visibly rather than becoming permission to delete.

Markdown reports now distinguish warning/deletion candidates from actual successfully deleted objects; dry run reports zero actual deletion. Existing JSON output keys and persisted warning-marker fields are unchanged.

## Changed files

1. `scripts/scratch_cleanup.py`: remove content inference and catalog/canonical scans; validate observed generations and warning evidence; make candidate/actual summary wording accurate. Source diff is 29 additions / 95 deletions (net -66).
2. `tests/test_scratch_cleanup.py`: remove the test blessing unsafe matching; add fake-GCS behavioral regression, generation/race, malformed marker, age-policy, and CLI tests.
3. `README.md`: accurately document retained age policy and removed content inference.
4. `docs/gcp-asset-operations.md`: same operational deletion boundary, including exact-generation and prefix-atomicity limits.
5. `scripts/README.md`: remaining-prefix policy and dry-run semantics.

No workflow, IAM, Terraform, catalog output, dataset schema, publication plan, or receipt change.

## Baseline failure evidence

Before source changes, added the two required `run_cleanup` regressions and ran:

```text
python -m pytest tests/test_scratch_cleanup.py -q -k 'fresh_mixed or old_unwarned'
```

Result: 4 failing apply/dry-run subtests across two test methods. Both a fresh proposal (age 0) and a 95-day proposal without a warning were classified `delete/matching-release` because their staged FGB matched a historical FGB while their README was unpublished. Output retained in:

`${REMEDIATION_WORKDIR}/reviews/scratch-cleanup-baseline-regressions.txt`

The original unchanged suite had passed (5 tests), establishing the original coverage gap. The independently imported module path was verified to be this worktree's `scripts/scratch_cleanup.py`.

## Final validation

All Python commands ran from the assigned worktree, reusing the root runtime without sync:

```text
UV_PROJECT_ENVIRONMENT=${REPO_ROOT}/.venv
UV_CACHE_DIR=${REPO_ROOT}/.uv-cache
uv run --no-sync ...
```

| Command | Result |
|---|---|
| `python -m pytest tests/test_scratch_cleanup.py tests/test_catalog_drift_guard.py tests/test_publish_workflow.py -q` | **62 passed, 136 subtests passed** |
| `ruff check scripts/scratch_cleanup.py tests/test_scratch_cleanup.py` | Passed |
| `python scripts/scratch_cleanup.py --help` | Defaults 60/90 and dry-run intact; obsolete catalog option absent; age policy explicit |
| `python scripts/catalog_docs.py check` | Passed, current for 24 assets; existing source-confirmation placeholders warned for six unrelated asset docs |
| Fallback Git `diff --check` | Passed |
| Fallback Git `status --short --branch` | Correct branch; precisely the five intended modified files |
| Fallback Git `diff --cached --stat` | Empty index diff |
| `rg` for removed helpers, match reason/parameter, catalog scan caller | No source/test/workflow/docs consumers remain |

Key tests exercise:

- Fresh and old mixed proposals in apply/dry-run; unchanged data with changed README/schema/manifest/sidecar; zero, partial, or full historical data matches.
- No canonical prefix listing; opaque/PR-looking proposal identifiers have identical age behavior.
- 60/90-day boundaries, valid existing warnings, late initial warning behavior, valid multi-object age cleanup.
- Invalid JSON; non-object JSON; missing state fields; boolean/numeric/empty state fields; exact marker-generation CAS replacement.
- Invalid observed data/marker generations (including missing, bool, zero, negative, float, whitespace, non-ASCII decimal) abort before mutations.
- Marker replacement between list/read aborts with no writes; subsequent retry re-warns using the newly observed generation.
- Warning create/replacement collisions abort before any deletion; replaced data survives while only successful deletions count; subsequent retry sees fresh replacement state.
- A newly added object after listing is outside the deletion set; marker replacement before deletion survives CAS; already-deleted marker is tolerated.
- Dry-run candidate vs actual summary and CLI default/invalid-policy refusal.

No native geospatial, live GCS, or live GitHub integration checks were run. They are unnecessary for the local policy fix and are not claimed.

## Compatibility and existing data

- No migration, receipt backfill, production audit, or remote object mutation.
- Persisted valid warning markers keep working; their fields and age semantics are unchanged.
- Malformed marker evidence no longer gains authority through string coercion; it can only receive a fresh warning via exact-generation replacement.
- JSON audit shape is preserved. `warnings`/`deletions` and counts remain decisions; `deleted_object_count` counts successful data-object delete calls.
- `--catalog` and `run_cleanup(catalog_path=...)` were intentionally removed with supervisor approval. No repository caller used the option outside the removed wiring. External callers of this repo-owned CLI must drop `--catalog`; no silently ignored compatibility shim was left.
- Matching published content can remain in scratch until producer cleanup or the age policy handles it. This retention is intentional.

## Invariant-first removal pass

- **Invariant enforced:** Artifact matches never establish publication completion or periodic delete eligibility. Age policy is the sole periodic eligibility rule; generation conditions protect every mutation.
- **Boundary changed:** Warning-marker JSON and observed GCS generations; malformed/unverified state never authorizes deletion.
- **Code removed:** Four matching/catalog/release helpers; `has_matching_release` state/branch; release-list wiring; `--catalog`/`catalog_path`; cleanup CRC32C representation; `csv` and extension imports; unsafe matching-success test.
- **Internal handling removed:** Boolean completion inference and downstream branch that consumed it; no fallback remains for missing catalog or release data.
- **Fallbacks added:** Only the approved external malformed-warning boundary: retain the object's known generation but discard invalid authorization fields, permitting a fresh CAS warning under age policy. No broad exception handler or retry was added.
- **Fallbacks rejected:** All-file content matching, age-gated content matching, inferred PR status, manifest-as-receipt, and a second receipt schema.
- **Deletion candidates retained:** Exact-generation `PreconditionFailed` skip for changed data and `NotFound` tolerance for an already-removed marker; current newest-object warning format for persisted compatibility; separate producer-owned post-promotion cleanup untouched.
- **Remaining uncertainty:** No production state inspected. Real GCS race behavior was modeled in-memory and explicit generation arguments were asserted.

## Limits and dependencies

This branch fixes O1's content-inference deletion only. The retained age policy may age out an old unchanged proposal even while a PR is open. It does not inspect PRs or publish leases. Object CAS does not make deletion atomic across a prefix: another writer can add an object after listing while older listed objects are deleted; a generation conflict can leave a partially cleaned prefix. Tests and docs state these limits rather than hiding them.

No dependency on E/F for this fix; it can be accepted first. Future completion-based cleanup must consume the publication producer's reviewed-plan/generation receipt and needs separately approved design. E/F may touch the same narrow README paragraphs, so review doc conflict resolution deliberately. Existing direct publisher source cleanup remains owned by E/F and unchanged.

## Supervisor review instructions

1. Read the baseline failure output, then inspect the five-file diff in this worktree.
2. Confirm there is no path from artifact matching or catalog/release metadata into `classify_proposal`.
3. Challenge incomplete/non-object/numeric warning evidence and list/read/write/delete races with the fake client; verify all mutations retain exact generation arguments.
4. Run the focused 62-test command and Ruff above independently.
5. Verify docs do not promise active-PR protection, transaction completion proof, a new grace period, or prefix atomicity.
6. Decide acceptance. No commit/merge authorization is inferred from supervisor acceptance.

## Prohibited-action and artifact record

No Git index/history mutation, stage, commit, push, PR, merge, remote object mutation, workflow dispatch, account/repository setting change, environment install/sync, or change to main's source tree occurred. All source commands explicitly used the assigned worktree.

Retained task artifacts: this handoff, the approved plan in `plans/scratch-cleanup.md`, and `reviews/scratch-cleanup-baseline-regressions.txt`, all under `${REMEDIATION_WORKDIR}/`. No stale-directory cleanup was performed.
