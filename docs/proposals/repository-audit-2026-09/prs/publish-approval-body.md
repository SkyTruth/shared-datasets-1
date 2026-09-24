> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

## Problem and resulting behavior

Publication authority currently depends on mutable PR prose and can accept an older approval after a later request for changes. A reviewer can approve plan A while a downstream job subsequently reads plan B. This PR binds canonical mutation authorization to an immutable, checked-in plan and effective acceptance of its exact PR head.

The same plan bytes must exist at the reviewed head and merge revision. Publication and automatic localization consume a captured authorization artifact, verify its provenance and current acceptance, and run the captured executor revision. Editing a PR description cannot change the executed plan. Ordinary code PRs carry an explicit `no_mutation` result; missing authorization evidence is an error.

## Implementation

- Add a versioned canonical plan document at `.github/dataset-plans/{asset}/{proposal}/{sha256}.json`, with strict parsing, immutable history, target uniqueness, and matching head/merge content.
- Require the latest effective `jonaraphael` acceptance on that exact head. Preserve the documented merged, self-authored `jonaraphael` exception.
- Capture the normalized plan, acceptance, source run/attempt, trusted executor SHA, and execution contract in a hashed artifact. Revalidate before authentication, publication, and deletion.
- Bind automatic localization to the exact successful upstream repository, workflow, run/attempt, and authorization artifact.
- Have the concierge generate and verify the committed plan. Changed evidence requires a fresh proposal and review.
- Remove historical-any-approval, runtime PR-body extraction, moving-main executor, heuristic PR lookup, and catalog fallback paths. Update operating guidance and the PR template.

Primary code: `scripts/dataset_mutation_authorization.py`, `scripts/reviewed_dataset_plan.py`, `scripts/publish_workflow.py`, and `scripts/publishing_concierge.py`. Workflow changes: publication, metadata localization, and breaking-change alerts. The remaining changes are focused tests, a shared authorization fixture, plan documentation, and maintainer instructions (21 files total).

## Why draft / rollout decisions before merge

Source review is complete, but live rollout is not approved by this PR's draft status.

- [ ] Establish the intended branch/environment enforcement policy, including how it coexists with the retained self-authored exception. A read-only settings check on 2026-09-23 found main required zero approving reviews, did not require CODEOWNER approval or stale-review dismissal, and the production environment had no reviewer gate. This PR does not change those settings.
- [ ] Inventory pending body-only publication proposals. Add a canonical document and obtain acceptance of the new head; already-merged body-only proposals require a new reviewed PR.
- [ ] Control reruns of historical workflow definitions so they cannot bypass the new boundary.
- [ ] Validate the live workflow handoff and artifact-retention/retry procedure in the intended deployment process. Missing or expired artifacts have no fallback to mutable prose.

GitHub review revocation and GCS writes are not atomic. This work does not implement publication receipts, crash recovery, all-writer adoption, rollback, or distributed localization-output concurrency. Those remain separate work; the shared authorization fixture defines the interface for publication recovery. A change to the executor contract cannot silently resume an existing transaction.

## Validation

Previously completed against the accepted source, whose committed bytes are being checked before opening this PR:

- Independent supervisor run of eight affected suites: **174 passed, 140 subtests passed**.
- Independent adversarial review: **17 cases passed** (15 refusals and 2 positive controls), including edited plan bytes with recomputed digests, revoked/forged acceptance, concealed mutation, wrong run/attempt/repository/workflow, duplicate JSON/archive members, and PR-body replacement after capture.
- Independent authorization/parser/workflow suites: **45 passed, 53 subtests passed**.
- Full combined remediation checkout: **1007 tests and 827 subtests passed**, with four unavailable-native-tool skips and one documented unchanged temporary-path-sensitive Terraform fixture deselected. This is combined-branch evidence, not a test of this PR against today's main.
- Committed diff whitespace check passed. No live publisher workflow, cloud mutation, or protection-settings change was performed.

The focused independent command used the existing repo environment, without installing dependencies:

```sh
uv run --no-sync pytest -q -p no:cacheprovider \
  tests/test_dataset_mutation_authorization.py \
  tests/test_reviewed_dataset_plan.py \
  tests/test_publish_dataset_workflow.py \
  tests/test_metadata_localization_workflow.py
```

## Scope and review

This is a code/workflow PR, not a dataset publication request. Remote GCS paths changed: **none**. No merge, deployment, or repository-settings changes were performed while preparing it.

This PR is authored by `jonaraphael`, the sole configured CODEOWNER. GitHub does not allow an author to request their own review; no self-review request is made. That limitation is separate from the runtime self-authored acceptance policy documented above.
