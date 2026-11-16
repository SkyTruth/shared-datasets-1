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

Use the constrained `mpatlas-source-field-v1` recovery plan after pausing and
draining the job through `ingestion-schedule-control.yml`. This pause is a
separate protected operation; preparation and recovery never pause, cancel or
resume a schedule themselves. Inspect all original evidence listed above first.
Then prepare an **unapproved**, read-only proposal in the task workspace:

```bash
uv run python scripts/mpatlas_publication_recovery.py prepare \
  --execution "$EXECUTION" --proposal-id "$PROPOSAL_ID" \
  --output "$WORK_DIR/recovery-publish.json"
uv run python scripts/reviewed_dataset_plan.py prepare \
  --publish "$WORK_DIR/recovery-publish.json" > "$WORK_DIR/pr-body.md"
```

Preparation checks the original terminal unsuccessful execution and immutable
image. The receipt's executor SHA must match that image's signed, tested GitHub
deployment record; Cloud Run's execution environment omits image-baked defaults
and cannot supply this proof. Preparation requires the repository-pinned GitHub
receipt verifier (`gh 2.96.0`); the protected installer sets it up automatically.
For local preparation, download the official v2.96.0 release for your platform,
verify its SHA-256 against the release checksums, and put that binary first on
`PATH`. The Linux Actions installer does not install workstation binaries.
Outside Actions, preparation creates its verifier workspace under the repository
temporary root; an existing `RUNNER_TEMP` is preserved.
It captures the exact state/receipt/control generations and receipt contents,
and inventories discoverable missing inputs and foreign destination generations
together. Missing or expired execution evidence, a source-quality hold, missing
inputs, another owner or conflicting generations block recovery. Resolve that
specific problem; never refresh the original destination expectations.

Review the captured intent's complete operation list, not just its remaining
files. This special plan has `promotions: []`: it adds no replacement artifact
bytes or independent effects. Only the existing captured operations may resume.
Preparation refuses a rendered plan body above 60,000 characters, leaving room
for review text below GitHub's PR body limit. The complete captured receipt
includes historical release-index entries and recorded results, which can make
it too large. In that case, retain the original evidence and stop: a separate
reviewed code PR must introduce a bounded-reference recovery contract before a
recovery plan can be prepared. Do not truncate the receipt, prune release history,
omit the matching fence, or use a generic promotion as a fallback.
Commit its immutable document and matching publish fence in a PR and obtain
exact-head owner acceptance. After merge, the existing protected reviewed-control
installer in `publish-dataset.yml` executes it using the approved publisher and
read-only observer. No new IAM grant or standalone replay dispatch is needed.

Before importing original code, the worker verifies the signed image/executor
binding with trusted current code. It archives the **original** executor commit, uses its frozen dependency
lock and Python 3.12.12, and runs its publication engine with the reviewed recovery
adapter. It retains the original context, transaction, captured inputs and CAS
expectations. A separate trusted current-code check revalidates the immutable
authorization and paused/drained job before every mutation. No global execution
environment variable is forged. Recovery itself can resume after interrupted
writes; already recorded results cannot be replaced. The source-quality owner
is cleared by CAS only after the original receipt and publication state are
complete; a later run's claim is never cleared.
The current validator must still accept the original receipt format; archiving
the old executor does not bypass that authorization boundary.

Verify the terminal worker result and release/latest/index contracts before
using the protected schedule-control workflow to resume normal operation.
No ingestion execution is relabelled successful and no Slack request is resent.
Reconcile any original incident through its own documented route. Do not delete
`state.json`, change the date, launch a new transaction or use generic promotions
over this active receipt. Conflicting bytes require a new human decision.

A replacement plan's document and fence must be reviewed at their new exact head.
Do not edit the original merged document or authorization artifact. Report the
original run/attempt, partial paths/generations, proposed remaining operations,
CRC observations, schema-waiver decisions, and what has not been executed.
