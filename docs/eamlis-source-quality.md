# EAMLIS source-quality review

`ingestion/common/source_quality.py` owns the hold/claim, retained candidate,
Slack delivery state machine, inspection and reviewed promotion implementation.
Its registry enables EAMLIS and MPAtlas; `HoldFlow` binds paths and execution
identity and `Guard` serializes a check or promotion with generation preconditions.
`stage_candidate`, `inspection`, `validate_candidate_plan`, `begin_promotion`
and `finish_promotion` retain and verify the exact reviewed bundle.
`lifecycle.run` owns stage ordering, empty-source handling, the lost-object limit
and unresolved-hold gating. EAMLIS supplies its source selection and identity
hooks. Persisted holds and candidates from earlier images remain readable,
including `geometry_count_drop` holds and records without native tool evidence.
[MPAtlas review commands](../ingestion/mpatlas_monthly/README.md#review-a-held-release)
use the same CLI with `--asset mpatlas-zone-assessments`. Omitting `--asset`
selects EAMLIS. The preview workflow accepts either registered asset and retains
its existing workflow name and filename.

EAMLIS automatic publication stops when **more than 5%** of baseline feature IDs
are absent from the new release. Exactly 5% is allowed. Additions never offset
losses, including equal-sized swaps. `maximum_lost_object_percent` in
`catalog/ingestion-quality.json` explicitly sets the limit to 5, read through
`lifecycle.loss_limit`. New loss holds use `objects_lost`; observations name
`lost_objects`, `geometry_count` and `maximum_lost_object_percent`.

Legacy releases compare source-field `OBJECTID` feature IDs. The installed
[permanent-key contract](eamlis-feature-identity.md) compares permanent ledger IDs;
the first keyed release maps the baseline's reviewed keys through that ledger.
Colocated mining problems remain distinct objects. The shared baseline reader
reads the dated manifest pinned by the Guard's captured release-index entry.
The index pins the manifest generation and optionally its size; index hashes
provide no authority and can be stale after reviewed metadata updates. The
validated manifest pins dated canonical metadata and schema by generation, size
and SHA-256. Validated sidecar counts must equal the manifest count and the
index's positive row count. A counted, generation-pinned baseline needs no run
record for authentication; an uncounted manual release fails at the Guard.
Run records and `latest/` bytes provide no baseline authority. Missing or invalid
published objects or references require reviewed repair before acquisition.
The successful run record supplies the stored source fingerprint and check-in
paths. Record validation checks release identity, release path, optional latest
paths, fingerprint hash and fingerprint/input object shapes before acquisition.
A missing record or a record without fingerprint fields forces a changed-source
comparison; a malformed record requires reviewed repair. Identity comparison and
fingerprint computation errors retain their original exception. Completed success
retries preserve an index already naming their release, including reviewed
metadata updates; missing release-index entries retain run-record recovery.
An unchanged upstream source cannot trigger automatic baseline repair.

The scheduled job continues checking the source. A hold stays active even after
a later healthy download; recovery always requires review of an exact candidate.
An empty source, changed selection, or confirmed interrupted execution also
creates a hold. Invalid or null geometries, inconsistent pagination, and build
failures stop the run through existing job-failure reporting; an active hold
survives those failures and still attempts its decision alert.
A missing counted release index cannot authorize replacing existing data.

## Persisted state and alerts

The generation-preconditioned control object is
`gs://skytruth-shared-datasets-1/_catalog/ingestion-holds/eamlis-abandoned-mine-land-inventory.json`.
It records the hold, published baseline, latest observation, candidate reference,
notification state, and active execution claim. The baseline is the index's
`latest_release`, never a failed download or latest check-in. Claims serialize
scheduled checks and reviewed publication through finalization/index rebuilding.
Completed-date retries also respect this claim and any hold.

The first held check attempts one Slack decision alert using the existing job
webhook. An `objects_lost` request names lost objects, baseline objects and the
percentage limit. A hold raised after the attempt's run record was written still
gets its one request on a completed-date retry. Candidate failures add error
details to the hold's observation while retaining its loss counts and limit.
The complete message is formatted before delivery is claimed; formatting errors
leave the request pending. An uncertain response or crashed send remains
`unknown`/`sending`; subsequent checks do not duplicate it. Delivery
failure never releases the hold and propagates as a job failure through existing
monitoring. The control object is the authority for investigation, even if no
Slack post was delivered. This decision alert is separate from editable GitHub
workflow incident notifications.

Only Google's terminal execution evidence permits reclaiming an interrupted
Cloud Run claim. The runtime has viewer access on the EAMLIS job only. A missing,
expired, unauthorized, active, or unrecognized execution cannot be reclaimed;
investigate it through the protected recovery path. A confirmed terminal
execution creates or preserves a hold. There is no elapsed-time lock stealing.
The [Cloud Run execution API](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs.executions)
owns completion evidence.

## Inspect an exact candidate

1. Read the state without changing production:

   ```bash
   uv run python scripts/eamlis_source_loss_review.py status
   ```

2. If a valid build exists, the job saves its source GeoJSON, FGB, PMTiles,
   canonical and Spanish metadata, translation CSV, schema, prepared manifest,
   and run record under
   `_scratch/pending-publishes/eamlis-abandoned-mine-land-inventory/source-loss-{candidate_id}/`.
   All are create-only objects; reuse requires identical bytes. The candidate
   JSON pins source generations, hashes, sizes, release date, hold, and baseline.
   Empty or invalid downloads cannot become approval candidates. A later valid
   check may replace the hold's candidate reference without releasing the hold.

3. From `main`, run **Inspect held EAMLIS candidate** with the exact candidate ID and `asset=eamlis-abandoned-mine-land-inventory`.
   It downloads generation-pinned production inputs with the read-only identity,
   then switches to the preview loader for all writes. It loads both baseline and
   candidate into the private preview bucket and refreshes its catalog. Dataset
   bytes stay identical; only preview manifest paths/generations change.
   The existing preview environment must be deployed first. No production
   dataset, schedule, or hold is changed by inspection.

4. Open the staging catalog linked by the workflow summary, select EAMLIS, and
   compare the two versions. Retain the workflow's inspection artifact with the
   production references and preview generations. Preview contents are shared
   disposable data; the immutable production staging reference remains the
   approval target. If the candidate has changed, repeat inspection.

## Approve publication through a PR

After investigating the loss, a maintainer may direct their agent to prepare
publication of that candidate:

```bash
uv run python scripts/eamlis_source_loss_review.py prepare-plan
```

This reads the current hold and baseline, verifies all candidate bytes, writes an
immutable `.github/dataset-plans/` document, and prints the matching PR fences.
It makes no remote writes. Include the document, fences, investigation rationale,
consumer impact, and staging inspection evidence in a focused approval PR.
Follow the [reviewed plan contract](../.github/dataset-plans/README.md).
Chat or Slack direction alone cannot clear a hold; approval must target the
exact PR head under the existing maintainer review rules.

After merge, the protected publisher rechecks hold/candidate/baseline identity
and every source hash before copying the complete bundle with captured
destination generations. The finalizer binds actual destination generations,
the release index is rebuilt, and the resolver verifies the published dataset
bytes and count before clearing the hold. A changed candidate or baseline fails
closed. There is no force flag or generic control-object promotion/deletion.

A failed promotion retains its claim. Rerun the same approved transaction:
already-copied objects are reused only after verifying exact bytes and metadata,
including the full expected finalized JSON. Other claims cannot take over.
Unexpected destination bytes still fail the original generation preconditions.
If publication has already completed and the resolver succeeded, inspect the
completed workflow rather than preparing another release for the same date.

After identity activation, a reviewed README-only update retains the same
publication ownership safeguards. It does not implicitly rebuild the release
index: README bytes do not change release artifacts, generations or counts.
Data promotions and explicit `release_index_asset_slugs` requests retain their
normal rebuilding and checksum validation.

PR #338's README copy succeeded before an unnecessary historical index rebuild
failed. The approved README generation is `1793242739943691`; its SHA-256 is
`86765354e7db581877ea3eff02dc24e80af092dc85b9993cd92810c7a83c47d5`.
The existing release index and permanent ID ledger were unchanged. The original
claim remains until protected recovery completes; do not clear it locally,
refresh destination preconditions, or create a replacement publication plan.

Once the README-only workflow fix is reviewed, merged and passes main
`ci-ready`, recovery uses the existing owner-only `Data / Approved mutation`
dispatch with **`pr_number=338`**. That path independently verifies tested main,
the original merged PR and its immutable document/current acceptance. EAMLIS
resumes the same proposal owner and reuses the copied README only after exact
byte and metadata verification. It then preserves the release index and lets
the existing resolver release the claim. A changed destination still fails the
original generation condition. Do not rerun the known failing executor before
the fix is installed. This metadata recovery does not resume cron or authorize
a new data release; owned WDPA/sea-ice receipt transactions retain their separate
original-executor requirements.

Held evidence is exempt from the standing age-based scratch cleanup. Successful
promotion uses the existing exact-source cleanup; remaining source/candidate
evidence requires explicit reviewed cleanup. No old canonical release is deleted.

## Rollout and validation

The protected scheduled-ingestion IAM sync first bootstraps policy management
restricted to the existing EAMLIS job, then installs its runtime viewer and
scoped control/staging storage grants. The job image includes the threshold
policy. Merge and protected deployment are required before these controls are
live; opening the implementation PR does not activate them.

Regression tests exercise the real job orchestration, tiny canonical FGB and
metadata generation, immutable plan serialization, staging catalog and release
comparison, interrupted protected promotion, finalization, index rebuilding,
and resolution with fake GCS/Slack transports. The native image/fixture suites
retain responsibility for GDAL/PMTiles generation and validation.
