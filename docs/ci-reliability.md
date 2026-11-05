# CI and production execution contracts

Current validation and rerun behavior is documented in [CI preflight](ci-preflight.md).
The [historical failure-family mapping](history/ci-failure-guardrails.md) retains
the original audit counts, run identities and diagnostic limits.

## Reviewed dataset publication

Automatic publication runs inside the main-push CI pipeline after `ci-ready`.
The detector enumerates the complete local Git range and paginated associated
PR metadata. Only same-repository PRs merged into main with checked-in immutable
mutation documents become publisher calls. Ordinary code merges finish detection
without allocating publisher or catalog jobs. Fork PRs and merges into feature
branches cannot provide mutation authority.

The publisher checks the caller workflow, exact run attempt, successful
`ci-ready`, original PR head and merge, original acceptance, immutable document
bytes, executor revision and generation preconditions. PR-body edits cannot
change authority. Consumers recheck the API provenance and acceptance before
authenticating and immediately before mutation.

Version 2 authorization envelopes identify the CI caller explicitly. Their
artifacts include the run, attempt, PR and plan digest. Version 1 envelopes remain
readable for persisted transactions; proposal and executor-contract hashes retain
their existing definitions. Changing an executor never creates permission to
resume an incompatible transaction.

Catalog refresh receives the exact authorization artifact and executor. It
independently verifies the successful mutation-completion step in the exact PR
job. A later notification failure remains a failure but does not suppress a
catalog refresh for a completed mutation. Standalone index rebuilds check out
their event SHA; catalog refresh verifies that source workflow and attempt.

## Protected authorization rehearsal

From main, the owner may dispatch `publish-dataset.yml` with an explicit merged
PR number and `authorization_rehearsal=true`. The protected job verifies the
immutable handoff and live GitHub acceptance without GCP credentials or object
mutation. Its executor must have successful main `ci-ready` evidence. This proves
the authorization path; it does not waive live object-generation prerequisites.

## Recovery during migration

Before switching orchestration, inventory active publication claims and failed
eligible main-dispatch runs. Keep immutable plans, receipts and authorization
artifacts. PR-ref runs refused by the production environment did not acquire
production credentials; they cannot become recovery authority.

For an unfinished transaction, reconcile source and destination generations and
the persisted receipt first. If recovery is compatible, rerun the **entire
original eligible main workflow**, preserving its executor and reviewed plan.
Rerunning only an apply job can reuse an envelope from the wrong run attempt.
GitHub preserves the original SHA/ref during a rerun, but limits reruns to 30 days
after the initial run and 50 attempts. If that route is unavailable, stop and add
a reviewed, constrained recovery path preserving the original contract. Retain
the existing reset installer bridge: a newer executor may verify an already
activated installation and finish its journal, but cannot continue creating reset
objects under a changed executor. Never
dispatch the newest executor to evade an existing transaction.

## Validation and cancellation

The always-evaluated `ci-ready` gate requires every selected suite and both SDK
runtime results. Missing, failed, cancelled, unexpectedly skipped or mismatched
revision evidence fails the gate. Production-image selection covers every
selected image-consuming deployment, including IaC-only edits. Local and PR
checks use no production credentials.

A newer PR run cancels the entire obsolete PR workflow graph, including selection,
suites and gate. Main-push and manual runs have unique groups and are never
cancelled by that policy. Protected production queues retain their existing
non-cancelling mutation locks.
