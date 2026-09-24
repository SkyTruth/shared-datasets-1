> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# E — Immutable publish approval (plan only)

Worktree: `${REMEDIATION_WORKDIR}/worktrees/publish-approval`
Branch/base: `codex/audit-publish-approval` / `1bf095d861d921e2378203495bd9a0da0bdf650c`
Governed by [CHARTER](../CHARTER.md) and [execution board](../EXECUTION-BOARD.md). Plan approved by supervisor; implementation handed off for independent review.

## Baseline and evidence

The defects remain at this base. Independent local results (historical artifact: `../evidence/publish-approval-baseline-results.json`) confirm: revoked/old-head approval accepted; open dispatch accepted; duplicate destination accepted twice; multiple plan fences silently select the first. The supervisor's original evidence (historical artifact: `../evidence/approval-baseline-results.json`) and review fixture (historical artifact: `../evidence/baseline-stale-reviews.json`) independently confirm the first two.

Source evidence:

- `scripts/publish_workflow.py:122-133` accepts any historical APPROVED review; no commit binding or effective-decision calculation.
- `.github/workflows/publish-dataset.yml:66-69,151-154` checks out moving default branch twice; `:109-128` skips the review check entirely for dispatch; `:171-210` refetches current body in the apply job. Gate outputs only presence flags.
- `scripts/reviewed_dataset_plan.py:63-84` accepts open PRs with `allow_merged=True`; `:44-61` accepts duplicate fences/JSON keys; `:412-532` permits duplicate/conflicting targets and drops unknown fields.
- `scripts/publishing_concierge.py:1974-2002,2560-2602,2885-2894` produces the actual plan and renders only a PR-body fence. Its retry helper generates altered generation preconditions for a merged-body retry.
- `.github/workflows/metadata-localization.yml:88-197` heuristically resolves a PR and reparses its current body before canonical derived writes. The planned-breaking-alert workflow also extracts body-only input.

Source review R2/R4 and operations cross-confirmation were read. No live GitHub or bucket requests were made. Official GitHub documentation was checked only for [review state/commit fields and pagination](https://docs.github.com/en/rest/pulls/reviews), [workflow SHA](https://docs.github.com/en/actions/reference/workflows-and-actions/variables), and [merge event semantics](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).

## Invariant and ownership

A canonical mutation may consume only a strictly validated plan whose exact bytes exist in one reviewed head and its actual merge revision, with effective acceptance of that head. All execution jobs consume the same captured bytes under one immutable executor revision. A body edit, stale approval, artifact replacement, ambiguous target, or another run cannot create authority.

The plan serializer/parser owns plan identity; the GitHub authorization boundary owns repository/PR/review/revision provenance; workflow handoff owns byte integrity. F owns actual mutation, durable receipts, replay reconciliation and deterministic metadata finalization. Trust boundaries are GitHub API responses and event context, checked-in data blobs, cross-job artifacts, and staged GCS object generations. GitHub/GCS do not offer an atomic review-revocation-plus-object-write transaction: recheck acceptance immediately before mutation, and state that finite race limitation.

## Smallest complete architecture

### 1. One checked-in canonical plan document

Add a versioned document containing `plan_version: 1`, optional `publish` and/or `delete` payloads (at least one), and an explicit named finalization/derivation contract. Existing publish/delete payload concepts remain: exact sources/generations, destination expectations, metadata, waivers, breaking changes, and explicit release-index targets. Preserve mixed asset-plus-catalog proposals; do not require F's proposed operation modes before their separate approval.

Use deterministic UTF-8 JSON (sorted keys, stable indentation, one trailing newline). SHA-256 is over those exact canonical bytes. Store it at `.github/dataset-plans/{asset_slug}/{proposal_id}/{sha256}.json`. A changed plan creates a new digest file; it cannot replace an already-merged plan. This keeps existing proposal IDs/staged prefixes usable without republishing sources solely to change review metadata.

The PR must add exactly one such document. Discover it from the fully paginated PR file list, not an arbitrary path in prose; reject modified/deleted/renamed existing plan records, multiple candidate files, symlinks, path traversal, or incomplete/truncated file enumeration. Fetch as data at the full head SHA and actual merge SHA. Require identical blob bytes, correct content-addressed path, and canonical serialization. Do not execute scripts checked out from a PR head. Verify merge is in the expected main lineage using immutable event/executor revision anchors; do not mistake head, synthetic PR merge, or another repository's commit for the actual accepted merge.

Strictly reject duplicate JSON keys/non-finite values, unknown version/fields, ambiguous coercions, bool/zero/negative generations, duplicate destinations/deletions, and overlap between promotion and deletion. Keep all legitimate declared metadata in the digest. One publish and one delete fence may coexist only for this same asset/proposal and disjoint destinations. Reject multiple fences of either kind, including identical duplicates. Avoid changing generic read-only open-PR helpers into authorization helpers: execution uses an explicit merged-only contract.

The readable fences remain generated projections, with checked-in path and digest above them. At the gate, require the rendered payloads to agree with the document; a post-review body edit cannot substitute operations. After capture, downstream jobs never consult body text. A between-job body edit therefore cannot alter executed bytes.

### 2. One authorization decision, current at execution

For merged events and restricted dispatch alike, fetch the PR by explicit positive number and validate repository identity, same-repo head/base, default base branch, non-draft closed-and-merged state, full head SHA, actual merge SHA, and event/dispatch anchors. Dispatch remains restricted to `jonaraphael` on main but does not bypass acceptance. No open/closed-unmerged dispatch.

For a non-self-authored PR, fetch every review page. Validate review association with the exact repository/PR and stable ordering metadata. Determine the required reviewer's latest effective decisive review across all pages: APPROVED or CHANGES_REQUESTED; a dismissed approval cannot authorize; pending/comment-only entries do not invent approval or erase an existing decisive review. An approved review must target the exact final head. Later changes-requested, dismissal of the effective approval, unknown/malformed decisive state, or contradictory duplicate IDs fail closed. New approval after changes requested may authorize only the exact final head. Document/test conservative handling of a later DISMISSED decisive record.

Preserve the explicit exception only when API author login is exactly `jonaraphael` and the PR is merged into the required repository/main. Record `acceptance_kind: self_authored_merge`; a caller-supplied reviewer string/PR-body claim cannot invoke it. The exception skips external review only, never document/revision/merge checks.

Record acceptance identity in the envelope. Revalidate the PR and all effective reviews after environment/queue delay and immediately before first mutation, without extracting new plan bytes; changed head/merge/acceptance requires a new gate run. No cached approval fallback on API failure.

### 3. Immutable envelope and job handoff

The envelope carries `authorization_version: 1`, repository identity, PR number, head SHA, merge SHA, plan path/blob SHA, `normalized_plan_sha256`, the normalized document, acceptance kind/review ID/reviewed commit, `trusted_executor_sha`, named `finalization_version`, and source workflow/run/attempt identity. Derivation versions are explicit allowlisted identifiers, never arbitrary commands or transforms. `finalize-promoted-release-v1` binds the approved implementation version; F supplies its deterministic allowed fields and receipt inputs. E does not claim current finalizer races are fixed.

Checkout the exact event `GITHUB_WORKFLOW_SHA` for gate and apply code; capture it in the envelope and gate outputs, and verify checked-out HEAD. Never checkout moving `main` for mutation code. Event workflow/ref identity must name this repository's expected production workflow/main path. Read catalog context from exact captured head/merge/parent revisions, with no fallback to a different local/current catalog when retrieval fails.

Upload one immutable v4 artifact named for run ID/attempt. Gate outputs include artifact ID, envelope hash, plan hash, executor SHA and presence flags. Apply downloads that exact artifact from the same run, rejects missing/extra files, verifies outer hash and inner canonical digest/context against gate outputs, and rechecks original head/merge blobs and current acceptance before GCP authentication. It extracts individual publish/delete files only from those verified bytes. No refetch/re-extraction of PR bodies or local hand-edited plan fallback. Artifact upload digest warnings alone are insufficient; validation failure must stop execution.

F receives this envelope unchanged and keys its transaction on immutable execution identity, not mutable PR prose. E adds no GCS receipt. A same-plan redispatch keeps the semantic plan/review identity while recording the new run attempt; it never refreshes destination expectations. Until F's receipt-based retry is integrated, already-mutated expectations fail safely. F alone decides proven-complete no-op/resume; changed plans require fresh reviewed bytes.

### 4. Automatic localization and review preview use the same authority

Replace the automatic localization workflow's PR/branch heuristics with the successful upstream publication run's exact envelope artifact. Validate source repository ID/name, run ID/attempt, allowed event, expected workflow file path and numeric workflow ID, head identity, successful conclusion, exact artifact ID/name/digest, and inner envelope provenance. A matching workflow display name is not proof. Missing/expired/tampered artifacts stop before publisher auth; no fallback to current PR prose or branch guessing. Verify immutable plan/review context and pin the downstream executor to the captured trusted revision. Only the verified publish payload feeds existing translation materialization. Manual materialization inputs and its distributed stale-output race stay unchanged/out of E.

Align planned-breaking-change input with the checked-in head document; this is preview validation, not acceptance. A body-only legacy plan reports a migration requirement and cannot become execution authority. Keep schema/impact checks read-only. If required evidence cannot be fetched completely, do not silently present an empty plan.

### 5. Make plan creation and migration usable

Extend concierge `render-pr` to write the canonical document under the content-addressed repo path and render its path/digest plus the existing readable fences. Add one reusable local `reviewed_dataset_plan.py prepare`/`render` path for delete-only, combined and existing manually prepared plans. Local commands validate/canonicalize only; they do not stage/commit/open PRs or assert review. Readiness verifies the on-disk document still equals assembled evidence.

Open legacy PRs: run the local preparation command, add the plan document to that PR, regenerate its fence, and obtain review on the resulting new head. Existing approvals do not carry forward. Merged body-only plans cannot be retroactively authorized: prepare a new reviewed PR with exact still-existing source generations and freshly reviewed destination expectations. No automatic claim that prior mutations were approved by new bytes. The existing retry helper may prepare an explicitly unapproved replacement proposal, but cannot edit/re-authorize merged plans or automatically relax CAS; document that F receipts replace routine unchanged-plan recovery. Historical merged content remains readable for audit.

## Files and shared boundaries

E code:

- `scripts/reviewed_dataset_plan.py`: canonical document parsing/rendering/digest; strict ambiguity rejection; exact immutable plan discovery/extraction; envelope validation.
- New `scripts/dataset_mutation_authorization.py`: one stdlib-only GitHub boundary and pure acceptance/provenance logic, used by both publishing and localization. Keep network transport thin/testable; no second schema owner.
- `scripts/publish_workflow.py`: replace obsolete review helper with delegation/removal; exact catalog revision input. Do not edit F's promote/finalizer/receipt logic.
- `scripts/publishing_concierge.py`: document generation and readiness, practical legacy preparation/retry wording.
- `.github/workflows/publish-dataset.yml`: gate, fixed checkout, artifacts, acceptance recheck and handoff only; coordinate execution-step interface with F.
- `.github/workflows/metadata-localization.yml`: automatic envelope provenance/extraction and fixed executor only.
- `.github/workflows/dataset-breaking-change-alert.yml`: immutable-head preview input.

Tests: `tests/test_reviewed_dataset_plan.py`, new `tests/test_dataset_mutation_authorization.py`, `tests/test_publish_workflow.py`, `tests/test_publish_dataset_workflow.py`, `tests/test_metadata_localization_workflow.py`, `tests/test_publishing_concierge.py`; adjust direct consumer fixtures only when the strict document contract requires it. Test CLI behavior and mocked API pages/artifact bytes; retain a small number of wiring assertions for permission/check ordering.

Docs: `AGENTS.md`, `README.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `.claude/skills/publish-shared-dataset/SKILL.md`, `.claude/skills/gcp-shared-datasets/SKILL.md`, and a short `.github/dataset-plans/README.md` format/command contract. Update any direct localization docs that promise current-body extraction. No generated asset catalog changes.

F owns `command_promote`, canonical mutations, receipts/state, deterministic finalizer, activation/preflight and execution-step ordering. Share envelope test fixtures/interface; do not independently introduce F modes, receipt paths or replay semantics. Parent explicitly authorized inclusion of the automatic localization handoff and planned-alert alignment in this plan. G retains translation-output/concurrency work.

## Regression and adversarial matrix

| Boundary | Required checks |
|---|---|
| Baseline failures | Old APPROVED then CHANGES_REQUESTED denied; approved wrong head denied; open dispatch denied; duplicate destination and multiple fences denied. Preserve independent baseline evidence. |
| Effective review | Approved exact head; old head/new commits; approved then dismissed; changes requested then reapproved; comments/pending; more than 100 reviews with decisive entry on later page; malformed/incomplete pages, duplicate IDs and API failure. |
| PR identity | Wrong head/base repo, repo ID/name mismatch, wrong base, draft/open/closed-unmerged, wrong event SHA, missing/incorrect merge, synthetic merge SHA, non-main dispatch, wrong actor; self-authored exact exception plus negative variants. |
| Document | Head/merge bytes differ, wrong digest path, altered merge conflict result, modified historical plan, multiple files, missing file, duplicate JSON keys/NaN/unknown fields, malformed generations, duplicate/overlapping publish-delete targets, traversal/symlink and pagination limits. |
| Body changes | Post-review replacement cannot execute; mismatch/duplicate fences fail gate; between-job edit leaves captured bytes unchanged; deletion of prose cannot remove a checked-in mutation from review discovery. |
| Jobs/artifacts | Tampered payload/envelope hash/repo/head/merge/executor/run/attempt/artifact ID, missing/extra artifact files, expired artifact, changed review while queued, advancing main after gate, code checkout mismatch. Assert publisher auth/mutation callback never reached. |
| Localization | Same workflow name from wrong repository or workflow ID/path; wrong run/attempt; unsuccessful upstream; mismatched head; body-only/no artifact; correct immutable upstream artifact. No heuristic or body fallback. |
| Producers/compatibility | Concierge generates byte-identical file/fence/digest; file modified after readiness; publish/delete/combined; existing mixed asset/catalog plan; preparation from legacy body; old approval invalidated by new commit; merged legacy rejects with actionable new-PR guidance. |
| Replay/F seam | Same semantic plan yields stable digest across run attempts; changed preconditions/version alter digest; no rewritten source/destination generation on redispatch. Test envelope fixture contract with F; F must prove partial/complete receipt behavior separately. |

Validation after implementation, from this worktree with the already-installed root runtime and `uv run --no-sync`:

1. Targeted tests for reviewed plan, authorization, publish workflow/YAML, concierge and localization workflow/pipeline; record exact totals and regressions failing against baseline.
2. `ruff check` on changed Python; `pytest tests/test_repo_guardrails.py`; Git `diff --check` and final focused status.
3. Combined E/F integration tests before either recommends operational use; no live dispatch/remote mutation to test this branch.

## Removal pass and limits

Delete historical-any-approval authority, duplicate body extraction/event preparation, moving-main mutation checkout, heuristic localization PR resolution, and catalog-context fallback when exact evidence is required. Preserve legacy body parsing only in clearly local migration/render/preview code if a real caller needs it; never behind an execution fallback. Replace tests asserting unsafe fallback with refusal/identity behavior.

This cannot make arbitrary repository writers or modified Actions jobs trustworthy: the workflow/runtime and GitHub API are trusted. Current GitHub protections and old historical workflow reruns remain an operational rollout prerequisite; E changes neither settings nor historical workflow code. F's durable replay/preflight/finalizer and G's distributed locale race remain separate. No artifact retention expiry fallback. No new dependencies, live data migration, rollout, branch protection/settings, Git history, PR or bucket mutations.

Completion checklist: baseline proof retained; canonical-byte producer and every authority consumer updated; full pagination/review/revision semantics tested; post-review/job tampering denied; self-authored exception preserved; legacy/open-plan commands documented; F interface agreed and combined validation stated; removal pass complete; checks pass; focused handoff written; stop for supervisor review, without merge.

## Approved implementation refinements

- Stable approval/proposal identity excludes source run/attempt IDs. The execution contract additionally binds the exact executor SHA and named finalization version. F must look up the proposal before planning; a changed executor refuses the existing transaction rather than creating another or refreshing expectations. Shared fixture: `tests/fixtures/dataset-mutation-authorization-v1.json`.
- Every successful gate emits an artifact, including a typed `no_mutation` envelope for ordinary merged code/docs PRs. Missing/expired artifacts cannot mean no mutation.
- Read-only live evidence confirmed merged-PR REST runs report the source branch/head (run 35806651282 / PR147), distinct from the runner merge/main context. Authorization binds both independently.
- Unused legacy extract/detect/event/heuristic CLI paths were removed; `prepare --body` is the explicit local migration route.
