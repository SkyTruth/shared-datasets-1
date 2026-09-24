> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# A — Scratch cleanup remediation plan (approval required)

## 1. Baseline and reproduced defect

- Worktree: `${REMEDIATION_WORKDIR}/worktrees/scratch-cleanup`.
- Branch: `codex/audit-scratch-cleanup`; clean baseline `1bf095d861d921e2378203495bd9a0da0bdf650c`.
- Read CHARTER, AGENTS, every repo skill frontmatter, invariant-first-engineering, sync-docs-with-code, local-temp-workspaces, and operations-review O1. No remote data or settings were inspected.
- Defect remains in `scripts/scratch_cleanup.py`: `proposal_has_matching_release` accepts any one staged approved-format file matching a historical basename, size, and CRC32C; `classify_proposal` immediately returns `delete/matching-release`; `run_cleanup` then attempts deletion of every staged object.
- Independent local reproduction imported the module from this worktree and constructed a matching FGB plus an unpublished README. With **no warning marker**, both age 0 and age 95 returned `action=delete`, `reason=matching-release`, `object_count=2`.
- Baseline targeted tests: `tests/test_scratch_cleanup.py`: 5 passed. The tests currently bless the unsafe matching helper and never exercise `run_cleanup`.

Current paths verified:

1. Weekly workflow invokes `--apply`; manual dispatch defaults to dry run.
2. Matching-release rule overrides all age/warning checks. The script reads the catalog and lists all historical release objects for each relevant slug.
3. Abandonment policy: newest object age >=60 days and no matching warning -> warn; age >=90 days plus matching warning -> delete. Matching warning records newest name/generation/updated time. A first audit at 95 days warns, and a later audit may delete. The policy does not promise a full 30-day grace after a late warning.
4. There is no PR-status lookup, publish receipt, in-progress lease, or completed-transaction signal in this cleanup implementation. A proposal identifier resembling a PR number does not establish PR state.
5. The separate approved publisher workflow invokes exact-source cleanup only after promotion/finalization/index and notification steps succeed. `command_delete_scratch_sources` deletes only unique source URI/generation pairs from its plan. That path is owned by E/F, not changed here.
6. Scratch data and warning-marker deletes use `if_generation_match`. Changed data generations raise `PreconditionFailed`, are skipped, and the remaining records can still be deleted. The warning marker is then generation-preconditioned for deletion. Missing warning markers are tolerated; unexpected data delete errors abort. The operation is not atomic across a prefix.

## 2. Invariant and ownership

**Invariant:** The periodic auditor never derives publication completion from artifact content. It may delete listed scratch objects only under the existing explicit age-plus-warning abandonment policy, and every object mutation remains conditioned on its observed generation.

**Bad states to remove:** A pending, active, mixed, partially published, metadata-only, or otherwise unverified proposal becomes eligible merely because some or all bytes resemble an earlier artifact. A dry run is presented as completed deletion.

**Established at:** `classify_proposal`, with the content-match input and branch removed entirely. `run_cleanup` has no canonical release data dependency.

**Consumed at:** Existing exact-object-generation deletion loop; summary output.

**Trust boundaries:** GCS list and warning-marker data enter the auditor. The warning proves only the existing age policy's observed newest-object state, not successful publication or PR approval. Published-completion proof remains owned by the publication producer, where any future receipt must identify reviewed plan and exact source/destination generations.

Failure classification: consumer misuse of content metadata as a transaction-completion signal. Fix by deleting the unsupported state instead of making the heuristic stricter.

## 3. Smallest complete fix and removal analysis

1. Remove `has_matching_release` from classification and the unconditional `matching-release` deletion branch.
2. Remove `proposal_has_matching_release`, `is_release_data_match_candidate`, `release_records_for_proposals`, `asset_roots_by_slug`, and the release/catalog scanning plumbing; delete now-unused `csv` and `APPROVED_DATA_EXTENSIONS` imports. `BlobRecord.crc32c` becomes unused and will be removed with its adapters/fixtures; checksums no longer participate in cleanup eligibility.
3. Remove the unused `catalog_path` parameter and obsolete `--catalog` CLI option. Repository search finds no caller passing the option beyond the script's own wiring; the scheduled workflow already passes only bucket and summary. This is an explicit retirement of an obsolete repo-tool option, not a silently ignored compatibility shim. The plan requests approval for that narrow CLI change.
4. Preserve current warn/delete age defaults, existing warning marker format, generation-preconditioned marker writes, exact-generation data and marker deletes, non-recursive behavior, and dry-run default. Keep schema boundary handling for absent/incomplete markers; never add a receipt or PR-state fallback.
5. Correct Markdown summary labels to distinguish warning/deletion candidates from actual deleted object count, so dry-run or a generation mismatch does not report all candidate prefixes as completed deletions. Keep existing JSON keys/meanings (`warnings`, `deletions`, counts are decisions; `deleted_object_count` is actual successful object deletes; `dry_run` remains explicit).
6. Replace the old matching-helper success test with behavioral audit regressions and add generation/refusal tests. Keep grouping and age-policy tests, adapting them to the smaller interface.
7. Update the three focused operations documentation passages to describe the retained age policy and explicitly state that historical byte matches do not establish completed publication.

Why simpler-looking alternatives fail:

- Requiring every data artifact to match still deletes unchanged data with new README, schema, catalog, manifest, or sidecar intent; even all matching bytes do not prove approval/completion of this proposal.
- Adding only an age guard preserves unsupported completion inference and skips the required warning.
- Guessing PR state from a prefix, inventing a receipt in this consumer, or interpreting a release manifest as a proposal receipt would fabricate authority absent from the producer.

Removal candidates retained:

- Newest-object warning fields: persisted age-policy contract retained; no marker migration in this scope.
- `PreconditionFailed` skip and `NotFound` marker handling: real external concurrency boundaries, retained and tested.
- Direct post-promotion scratch deletion: separate producer-owned path, not duplicated into the auditor.

## 4. Exact intended files and compatibility

- `scripts/scratch_cleanup.py`: remove inference/scanning; summary wording.
- `tests/test_scratch_cleanup.py`: regression and fake-client mutation tests.
- `README.md`: remove historical-content completion claim.
- `docs/gcp-asset-operations.md`: same authoritative deletion policy correction.
- `scripts/README.md`: clarify remaining prefixes use warning/age policy, not inferred completion.

No workflow, Terraform, IAM, catalog, dataset schema, source data, publication plan, or receipt files change. JSON audit shape and warning marker persisted format stay compatible. The only intentional callable/CLI removals are the internal match helpers/parameters and now-useless `--catalog` option. Existing warning markers remain valid under the documented unchanged age policy. Completion inference disappears, so some previously immediately deleted scratch bytes are retained until explicitly cleaned by the producer or eligible under age policy; this is the intended safety behavior.

## 5. Existing data strategy

No remote mutation or local migration. Existing scratch objects remain untouched by this task. Historic matching artifacts, manifests, missing receipts, and partially completed proposals are not retroactively declared completed. Existing warning markers retain their established meaning. There is no need to enumerate production objects or backfill receipts to ship this bounded fix.

## 6. Tests and evidence

Use an in-memory fake GCS client that supplies pending records, warning payloads, and old canonical records; records list/upload/delete calls, enforces `if_generation_match`, and can simulate generation changes. Regressions exercise `run_cleanup` rather than only textual assertions:

- Fresh matching FGB + new README: keep in apply and dry-run; no upload/delete calls.
- Fresh unchanged full data bundle + changed sidecar/schema/manifest/README: keep.
- Fresh partial publish (some historical/current destinations matching): keep.
- Old mixed/partially published proposal without warning: warn, never delete; dry-run writes nothing.
- Old proposal with incomplete or changed warning fields: warn again, not delete.
- Nonmatching, matching, or absent historical content yields the same age decision; canonical roots are never listed. Absent catalog and opaque/PR-looking proposal IDs do not affect outcomes.
- Exactly 60-day and 90-day boundaries; valid warning before 90 days retains, valid warning at 90 days deletes; existing markers work unchanged.
- Apply creates a warning with generation 0 or updates the prior marker generation; marker write CAS failure propagates before deletion.
- Eligible age-policy deletion passes exact scanned generations for every object and marker; no prefix delete occurs.
- Object replaced between list and delete survives via `PreconditionFailed`; only successful deletes count. A subsequent audit sees the new state and cannot inherit the old warning.
- Added objects after listing are never accidentally included in deletion calls. This verifies limited list-snapshot behavior, not a prefix-atomic claim.
- Warning marker replacement during cleanup is protected by its generation CAS (failure visible); no replacement marker is deleted.
- Dry-run JSON and Markdown distinguish proposed candidates and actual zero deletions; no warning-marker changes.

At least the fresh mixed and old-without-warning regressions must fail against baseline with observed unsafe deletion, then pass after the fix. Preserve the external reproduction details above and show regression failure output before source edits.

## 7. Dependencies, conflicts, and non-goals

This branch is independently useful and can merge before E/F. It does not require receipt support. Supervisor has been notified that any future publication-completion cleanup must consume the publication task's producer-owned receipt, never create a second schema. Potential documentation merge conflict: root README or scripts README may also be touched by E/F; changes are narrow paragraphs.

Non-goals: new PR API or lease integration, receipt schema, publication transaction changes, age-policy redesign, warning-marker migration, global prefix locks, production audit/cleanup, workflow dispatch, account/repository settings, IAM, commits/PRs/merge.

Limitations to state explicitly: age policy can still classify an old unchanged proposal as abandoned without checking whether its PR is open; object generation preconditions are not a prefix transaction and cannot stop all races with another writer/publisher after the list snapshot. This fix eliminates content-based deletion, not every possible concurrency or retention-policy issue. Completing a stronger lease/receipt protocol requires E/F design and another approved scope.

## 8. Validation and completion checklist

Every command runs from the assigned worktree. Reuse the existing runtime without sync:

`UV_PROJECT_ENVIRONMENT=${REPO_ROOT}/.venv UV_CACHE_DIR=${REPO_ROOT}/.uv-cache uv run --no-sync ...`

Planned commands:

- `python -m pytest tests/test_scratch_cleanup.py -q` (baseline 5 passed already; new regressions first fail, then pass).
- `python -m pytest tests/test_scratch_cleanup.py tests/test_catalog_drift_guard.py tests/test_publish_workflow.py -q` (script behavior and adjacent unchanged workflow/publication contracts).
- `ruff check scripts/scratch_cleanup.py tests/test_scratch_cleanup.py`.
- `python scripts/scratch_cleanup.py --help` (CLI matches planned removal, defaults unchanged).
- `python scripts/catalog_docs.py check` if the focused doc checker covers modified prose without remote access.
- Fallback Git `diff --check`, `status --short --branch`, and final diff inspection.

Verify imported module path remains this worktree. No native geospatial/integration or live GCS/GitHub check is necessary for this pure control-plane cleanup policy; none will be claimed.

Completion: provide independent baseline failure and final tests; inspect every removed helper caller; verify no canonical list requests or metadata inference survive; complete removal pass; write `reviews/scratch-cleanup-handoff.md` with commands/results, compatibility/removal table, residual risks, review instructions, and no-prohibited-action confirmation. No staging, commits, pushes, PRs, merges, remote mutations, environment sync, or settings changes.

**Gate status: PLAN ONLY. No tracked files or Git state changed. Await supervisor message beginning `PLAN APPROVED`.**
