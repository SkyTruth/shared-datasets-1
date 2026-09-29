# Protected feature-ID reset installation

The installer prepares the first allocation state for the approved pre-launch
contract retirement of `wdpa-marine`, `wdpa-terrestrial`, and
`ims-sea-ice-extent`. It does not publish a dataset release, rebuild translations,
activate an index, or resume a schedule. All three assets remain **NOT READY**
for production cutover. The checked-in writer-fence registry is empty, and the
dedicated reset identity is not provisioned by this change.

The invariant is: **a reviewed reset may create allocation history once; a
retry must never recreate or rewind that history.** Installation requires the
same immutable PR authority as ordinary reviewed publishing, plus separately
reviewed and currently enforced writer restrictions. There is no local install
mode or `writers_stopped` override.

## Authority and writer restrictions

The `Approved dataset mutation` workflow routes a publish plan containing
`identity_reset` to its dedicated `install-approved-identity-reset` job. The
generic promotion and localization paths do not execute it. The job runs in
`shared-datasets-production`, checks out the captured executor SHA, verifies the
authorization artifact, and rechecks effective PR acceptance before every write.
Changing the PR body cannot change the captured plan.

Before cloud authentication, the installer requires the plan's `fence_sha256`
to identify an entry in `catalog/feature-id-reset-fences.json` at that executor
revision. Each entry contains `snapshot`, a timezone-aware `expires_at`, and a
`review_note` identifying the effective-access evidence and reviewed restrictions.
Its key is SHA-256 of the snapshot serialized with
`ingestion.common.publication.canonical`. No entries are approved by default.

An administrator must first supply the missing inherited IAM evidence and review
all effective writer, impersonation, deployment, group, and administrative paths.
Infrastructure changes require a reviewed Terraform PR and a constrained
protected apply workflow. This installer does not modify IAM or job configuration.
The reviewed infrastructure change must provision
`feature-id-reset@shared-datasets-1.iam.gserviceaccount.com`, narrowly scoped object
access and the required read-only control-plane permissions. Bind its federation
to the approved repository, main workflow, and protected environment; configure
`GCP_FEATURE_ID_RESET_WORKLOAD_IDENTITY_PROVIDER`. Do not reuse the old general
publisher identity or grant broad write access to obtain audit evidence.

`GoogleControlReader.collect()` checks that the old WDPA, sea-ice, and general
publisher accounts are disabled, the reset account is enabled, both schedules
are paused, and every listed execution has completed and is not reconciling.
It captures project/organization allow and complete deny policies, bucket and
managed-folder policies, project service-account policies/status, federation
providers, and custom role definitions. Lists are paginated; denied reads fail.
The live snapshot must equal the approved snapshot before every remote write.

Snapshot equality detects drift; it does **not** prove an arbitrary policy safe.
The review must establish that excluded writers stay excluded regardless of
untracked group membership, time-dependent conditions, historical workflows,
or administrative changes. Credential revocation and propagation also need
cutover evidence. This initial collector covers the known direct
project-to-organization ancestry and refuses a changed parent. It cannot replace
the missing organization-level evidence in the
[readiness report](proposals/feature-id-readiness-2026-09-29.md).

The collector follows the documented
[service-account IAM request](https://docs.cloud.google.com/iam/docs/reference/rest/v1/projects.serviceAccounts/getIamPolicy),
[deny-policy resource](https://docs.cloud.google.com/iam/docs/reference/rest/v2/policies),
and [Cloud Run execution](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs.executions)
interfaces. These are read-only checks, not a production permission test by
attempting unapproved canonical writes.

## Prepare one reviewed plan per asset

After restrictions are in force, choose a new release date and capture all
current `latest/` object generations/hashes plus the matching old release
manifest. The new release directory and run record must be absent. Retain the
inventory and generated files in a named directory under the standard local
temp root. The offline preparer can export the exact three canonical JSON files:

```bash
uv run python scripts/feature_id_reset.py \
  --inventory "$WORK_DIR/inventory.json" --output "$WORK_DIR/reset-review.json" \
  --objects-directory "$WORK_DIR/reset-objects"
```

This creates `0.json` (inventory evidence), `1.json` (adoption receipt), and
`2.json` (initial state). Stage them through `scripts/gcs_asset.py` under the
asset/proposal's `_scratch/pending-publishes/` prefix with no-clobber preconditions.
Record the exact returned source generations; scratch existence grants no
publication authority.

Prepare an ordinary publish payload with an `identity_reset` object containing
`inventory` (the complete inventory JSON object from the review envelope) and
`fence_sha256` (the approved snapshot digest). Include exactly three promotions in the envelope's
evidence/adoption/state order. Each names the staged source URI/generation and
the exact destination from `objects[].path`, with `destination_generation: ""`,
`content_type: "application/json"`, and `cache_control: "no-cache"`. Do not include
a deletion, compatibility waiver, or release-index rebuild. The installer checks
each staged object's SHA-256 and size against the candidate's canonical bytes.

Run `reviewed_dataset_plan.py prepare --publish "$WORK_DIR/publish-plan.json"`
and include its immutable document and generated publish fence in an explicit
PR. Obtain approval on the exact head, then use the protected workflow after
merge. The plan also authorizes the installer-owned journal at the deterministic
`{asset-root}/publications/reset.json`; describe this path and its transitions in
the PR. It is operational transaction state, not a fourth source promotion.

## Write order and recovery

| Durable write | Condition |
| --- | --- |
| Create `reset.json` in `prepared` phase | No existing publication namespace; create only |
| Create inventory evidence | Exact staged generation/hash; create only |
| Create adoption receipt | Exact staged generation/hash; create only |
| Advance journal to `activating` | Match the journal generation |
| Create `state.json` | Create only; never replace an allocation state |
| Advance journal to `complete` | Match journal generation; record created state generation |

The journal binds the inventory, writer fence, proposal, and executor contract.
Publishers require its `complete` phase before allocating or writing. Retries
accept only matching bytes and installer metadata; competing proposals or
unrelated publication objects stop the operation. After completion, a retry
validates the existing owned state and receipts without changing its counter.

A crash after `activating` but before a confirmed state leaves an intentionally
bounded stop: **if state is absent, reviewed recovery is required**. Absence
cannot distinguish a write that never happened from a state that was created
and later lost. Do not delete the journal or rerun a different proposal to start
over. A missing state after completion also refuses automatic reset. Keep writer
restrictions in force while reviewing recovery; never restore an old writer.

Tests cover immutable authorization and revocation, simultaneous installers,
changed inventory/permissions, every durable-write crash boundary, completed
retries after subsequent publication, and runtime refusal before completion.
These use controlled stores/API fixtures; they are not live production cutover
evidence. The first complete new-ID release and its native/serving checks remain
separate prerequisites in the [fresh-start plan](proposals/feature-id-fresh-start.md).
