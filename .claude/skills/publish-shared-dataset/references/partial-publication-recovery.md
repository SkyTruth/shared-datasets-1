# Partial-publication recovery

Read only after partial execution. The entry point owns helper commands; the
shared [reviewed proposal](reviewed-proposal.md) owns a new proposal's Git path.
Initialize its workspace variables first. Load `ci-incident-recovery` when the
request also includes workflow incident/Slack reconciliation.

Inspect the failed step and exact current source/destination generations. Keep the
original checked-in plan and authorization artifact unchanged. Do not edit a
merged PR body, replace artifact bytes, or relax preconditions during redispatch.
A body-only merged plan requires a new reviewed PR. Open legacy proposals can use
`reviewed_dataset_plan.py prepare --body pr-body.md`, then add the document and
obtain review on the new head.

`publishing_concierge.py refresh-retry-plan` may prepare an **unapproved**
replacement payload from verified observations. Changed generation expectations,
waivers or operations require a new checked-in digest document and reviewed PR.
An unchanged-plan retry preserves every original expectation. Per-object CAS can
refuse a partial replay until durable transaction recovery is available. A future
receipt consumer must refuse a changed executor contract for the existing
proposal; it must not create a new transaction to evade old expectations.
See `.github/dataset-plans/README.md` for the shared identity and rollout limits.

The helper's `--stat-gcs` observes each staged source at its pinned generation and
each live destination, saving `source` / `destination` stats per promotion.
It refuses missing or changed source generations and missing/mismatched CRC32C
for existing destinations. Still-absent destinations retain no-clobber creates.
The summary reports `promotion_count`, `refreshed_destination_count`,
`destinations_still_absent`, `compatibility_waiver_count`, and `crc_mismatch_count`.
Inspect those results, not a guessed "safe to retry" assertion.

Use `--remove-waivers` only after confirming
that a partial run already advanced the schema snapshot and the protected schema
preflight no longer reports blocked schema changes.
Do not use `--allow-crc-mismatch` to turn a conflict into success. Conflicting
bytes require a human decision and a new proposal; preserve the original evidence.

For WDPA/sea-ice `OwnedGeneratedPublisher` transactions, stop generic recovery:
retain claim, reservation, captured inputs, checkpoints and pinned executor.
A new execution/date or reset counter cannot abandon ownership. Consult
`deploy-scheduled-ingestion` and the owning job's runtime contract; report a
specific blocker if the original execution cannot resume.

## MPAtlas after both task attempts fail

One task retry runs automatically in the original Cloud Run execution. Once both
attempts are terminal, stop scheduling new attempts as a recovery technique.
Load `deploy-scheduled-ingestion` and `gcp-shared-datasets` before inspection.
Retain the failed execution name, both attempts' logs, image digest,
`SHARED_DATASETS_EXECUTOR_SHA`, `RUN_DATE` and original `MPATLAS_*` configuration.

Read (do not edit) the following generation-pinned evidence:

- `_catalog/ingestion-holds/mpatlas-zone-assessments.json` (claim and hold).
  `uv run python scripts/eamlis_source_loss_review.py --asset mpatlas-zone-assessments status`
  reports the hold; it does not repair a publication.
- `100-geographic-reference/130-protected-areas/mpatlas-zone-assessments/publications/state.json`.
  Its `active` value names the original transaction; retain the referenced
  `publications/receipts/{proposal_key}.json`, intent, checkpoint input generations,
  operation results and their current destination generations.
- The dated/latest manifests, run record and `_catalog/releases/mpatlas-zone-assessments.json`.

If no intent/active transaction exists, resolve the original extraction or hold
failure before a normal new execution. An interrupted source-quality claim may
still block that execution; use the hold-review procedure rather than deleting
the control object. A `sending` or `unknown` hold notification remains a failure
on retry and on every later scheduled check until reviewed promotion clears the
hold. Inspect the original Slack delivery; never blindly resend or relabel it
as `sent`. If delivery is confirmed missing, an authorized operator may post the
decision request manually. For a retained candidate, use the asset-specific
`status` → `prepare-plan` → immutable reviewed PR → protected `resolve` procedure.
A missing approvable candidate is a recovery blocker, not permission to clear
the control object. Source-quality holds are separate from workflow-incident receipts.

If a receipt exists, compare each recorded result with the exact live object
generation and checksum. A completed receipt with `active: null` is evidence to
verify, not permission to republish. An incomplete receipt must resume with its
original context, executor, captured input generations and destination
preconditions. `SourceFieldPublication.resume()` is the supported transaction
operation; a new Cloud Run execution deliberately cannot adopt that context.

There is currently **no protected operator entry point for resuming a terminal
MPAtlas execution**. Report that specific blocker and prepare a reviewed,
constrained recovery PR using the original receipt; include the original context,
remaining operations, current generations and synthetic recovery proof. The
protected deployment procedure and exact-head owner acceptance still apply.
Do not dispatch the generic publish-plan workflow over an active transaction,
forge `CLOUD_RUN_EXECUTION`, delete `state.json`, change the date, or refresh
preconditions. Conflicting generations require a new human decision.

A replacement plan's document and fence must be reviewed at their new exact head.
Do not edit the original merged document or authorization artifact. Report the
original run/attempt, partial paths/generations, proposed remaining operations,
CRC observations, schema-waiver decisions, and what has not been executed.
