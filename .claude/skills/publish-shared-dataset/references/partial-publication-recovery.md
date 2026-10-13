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

A replacement plan's document and fence must be reviewed at their new exact head.
Do not edit the original merged document or authorization artifact. Report the
original run/attempt, partial paths/generations, proposed remaining operations,
CRC observations, schema-waiver decisions, and what has not been executed.
