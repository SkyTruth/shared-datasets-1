> Preserved planning/review record. See the [archive index](README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# Remediation supervision contract

Base: `1bf095d861d921e2378203495bd9a0da0bdf650c` (main after PR147).
Supervisor: root agent. User review is required before any merge.
Source review: ${INITIAL_AUDIT_WORKDIR}/repository-review.md

## Gate 1: plan approval, before implementation

The first assignment is PLAN ONLY. Read source, instructions, and tests. Reproduce with disposable external fixtures if useful; do not modify tracked files, install dependencies, or mutate remote state. Submit a plan to plans/{task}.md and notify the supervisor. Stop. Only an explicit supervisor message beginning `PLAN APPROVED` authorizes implementation. Scope changes that alter persisted/public contracts or widen ownership return to this gate.

Required plan:
1. Current baseline and whether the defect still exists; exact failure evidence.
2. Invariant, impossible states, owner/producer, trust boundaries.
3. Smallest complete fix; why simpler alternatives fail; removal candidates.
4. Exact intended files and public/persisted compatibility implications.
5. Existing-data strategy; no invented historic correctness or silent migration.
6. Tests that fail on baseline and pass with fix, including adverse/retry/concurrency paths where relevant.
7. Dependencies/conflicts with other branches and explicit non-goals.
8. Validation commands, limitations, and completion checklist.

## Gate 2: implementation acceptance

Work ONLY in your assigned worktree; set workdir explicitly on every command. Do not alter the user's main checkout or other worktrees. Do not stage, commit, amend, push, open PRs, merge, deploy, dispatch workflows, change account/repository settings, or mutate remote objects. The user authorized branches; the supervisor created them. Leave focused reviewable working-tree changes for final review.

Read AGENTS.md and matching repo skills. Invariant-first-engineering is mandatory. Read sync-docs-with-code before docs updates, align-virtual-environment before environment changes, and other focused skills as triggered. Avoid repeating prose rules in new places when executable enforcement is possible.

Completion requires:
- Independent reproducer/regression evidence for the original defect.
- Focused final diff with all producer/consumer call sites updated; no split semantics or unchecked fallback.
- Meaningful tests for valid behavior AND refusal/recovery paths. No merely textual tests where behavior can be exercised.
- Appropriate tests/lint/types pass; skipped native/integration checks stated explicitly.
- Compatibility/migration evidence and no silent data/ID/path/access-policy changes.
- Removal pass identifying code deleted or retained with reasons.
- No stale TODO masquerading as the claimed fix; unresolved safety concern means NOT READY.
- reviews/{task}-handoff.md with summary, files, commands/results, residual risks, and exact review instructions.
- git diff --check and source status, plus confirmation no prohibited action was taken.

## Gate 3: demanding supervisor review

Supervisor reads final diff, independently runs targeted regression checks, challenges invariants and adverse paths, and returns revisions until satisfied. Where useful, another agent reviews the finished implementation independently. Supervisor approval is not user merge approval. Final user handoff identifies branch/worktree, behavior change, proof, remaining risks/dependencies, and suggested merge order. No merge occurs in this assignment.

## Runtime

Use uv. Existing root runtime can be reused without resync via UV_PROJECT_ENVIRONMENT=${REPO_ROOT}/.venv and UV_CACHE_DIR=${REPO_ROOT}/.uv-cache with `uv run --no-sync ...`, invoked FROM YOUR WORKTREE so imports/test root are correct. Verify imported module paths. Do not mutate/sync that shared environment. For isolated installs, follow align-virtual-environment and ask supervisor to coordinate; no ad hoc pip environments.

System /usr/bin/git is Xcode-license blocked. Use ${GIT_BIN}, or prepend its containing directory to PATH per command. Temporary artifacts stay within this named remediation directory or the repo-standard temp root, never main's source tree.

## Work packages and hard acceptance criteria

A. scratch-cleanup: pending/active/unverified proposals must never become delete-eligible because any subset matches prior artifacts. Eliminate the content-match-as-completion rule. Preserve exact-generation deletion, explicit age/abandonment policy, and dry-run transparency. Test fresh and old mixed proposals, partial publishes, unchanged bundles with new metadata, absent/incomplete proof, and legitimate explicitly eligible cleanup. Coordinate future receipt support with publication task rather than inventing a second receipt schema.

B. feature-id-highwater: newly allocated generated IDs must exceed all previously allocated IDs, even when high IDs disappeared or current release is empty. Consume and monotonically carry persisted sequence state through actual ingestion builders. Cover three-plus release deletion/new/return sequences, explicit reused-ID decisions, corrupt/bool/nonpositive/regressing sequence values, and legacy baselines. Never guess a historical high-water value from only current live records. Preserve source-field IDs and hash canonicalization unless separately approved.

C. python-release-cache: a successful fetch of latest must identify/cache the exact resolved release artifact; an unchanged CSV date must not freeze updates or falsify lineage. Use available release index generation/checksum metadata coherently, pin downloads where possible, fail explicit missing versions. Preserve documented legacy/latest-only assets explicitly. Cover unchanged CSV refresh, same-date replacement, tampered/truncated cache, offline/error behavior, private ADC/public paths, historical version, and no index. Avoid introducing another release schema.

D. historical-consumer-bundle: browser selected release and TS layer version must select tiles+sidecar from that exact same release; missing explicit versions cannot silently fall back to latest. Propagate version/generation through restricted signer authorization. Cover public/private/internal, two releases with different IDs, missing sidecar vs missing release, expired access, and latest changing between requests. Do not widen access or sign arbitrary user-selected URIs.

E. immutable-publish-approval: executed mutation bytes must be the same bytes authorized for a concrete reviewed commit/plan digest. Eliminate approval by mutable PR-body alone; preserve human-readable fenced plan and self-authored restriction. Cover edited post-approval body, between-job edits, stale/revoked reviews, wrong SHA/repo, duplicate/conflicting destinations, self-authored case, and redispatch. Prepare code/docs and tests only; no GitHub protection mutation.

F. publication-recovery: approved publication must distinguish prepared/partial/committed and resume only operations proven byte-identical to the approved plan. Prevent mixed consumer activation where current contracts permit, late-generation finalizer lost updates, and older backfill clobbering current latest. Strong preflight before mutation, exact-generation receipt, manifest-last/activation semantics, schema snapshot only at appropriate commit, independent notification retry. Plan must explicitly state remaining multi-object atomicity limits and avoid claiming a full guarantee from per-object CAS. Coordinate interface with A/E/B; don't introduce a format migration or pointer protocol without supervisor approval.

G. translation-integrity: first focus on local destructive aliasing, transactional output, strict workbook row/shard identity, and failed task retry semantics; treat distributed materialization race as a separately approved extension if needed. Preserve human translations and canonical input on every failure. Cover same path/symlink alias, malformed output midstream, reordered/extra/missing workbooks, provider failure then success, source changes, and stale input/destination generations. Respect spec-only PR145 rather than implementing all product features opportunistically.

Deferred from implementation in this pass: live GitHub protection changes, production data migration/republish, bulk historical audits, a new metadata serving backend, broad architecture rewrites, and unrelated low-impact bugs. Prepare concrete recommendations where these are prerequisites; do not silently mark dependent defects resolved.

## User-authorized commit checkpoint — 2026-09-23

After source review, the user explicitly requested all edits be committed to their branches. Root staged only frozen reviewed files and committed each focused package; the review-only integration composition was committed separately. This supersedes the earlier no-stage/no-commit limit for those exact changes. It does not authorize pushes, PRs, merges, deployments, remote operations or implementation of held F3b/c. See commits/README.md for exact SHAs and history. All source worktrees are clean.
