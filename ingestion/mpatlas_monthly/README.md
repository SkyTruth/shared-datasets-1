# Monthly MPAtlas refresh

Checks the existing private `mpatlas-zone-assessments` asset on the second day
of each month at **11:00 UTC** (`0 11 2 * *`). The source is the
[MPAtlas public v4 zone GeoJSON API](https://guide.mpatlas.org/api/public/v4/zone/geojson/).
The [asset documentation](../../docs/assets/mpatlas-zone-assessments.md) records
license permission, attribution, admission evidence and the full feature-identity
decision table. Steward: Jonathan Raphael (@jonaraphael), SkyTruth. The existing
30x30 analysis is the intended consumer; shared snapshots avoid repeated
downloads and normalization of the mutable API. Published releases remain
readable if this job is paused or retired.

## Change detection

The comparison baseline is the published `latest/*.manifest.json`, read with its
observed generation, and the manifest's hash-verified, generation-bound dated
metadata sidecar. A missing or corrupt baseline fails before any publication;
this refresh job does not initialize a dataset or repair its publication state.

1. Download the complete API response and compute SHA-256. If it matches the
   source SHA-256 in the published manifest and its normalized fingerprint uses
   the current definition and no hold is open, skip conversion and publication.
2. Otherwise validate source IDs and properties, exclude null geometries, repair
   geometry with GDAL MakeValid, and promote Polygon/Point to MultiPolygon/MultiPoint.
   Serialize the existing three JSON-valued fields as canonical JSON text.
3. Compute the common versioned `mpatlas-content-v1` fingerprint from the source
   URL and the multiset of `(feature_id, geometry_hash, properties_hash)` records.
   Compare with the last publication's fingerprint. For legacy manual releases,
   derive it from the verified published sidecar. This ignores
   response whitespace, object-key order, feature order, retrieval provenance and
   release dates. Coordinate ordering and array ordering remain meaningful.
4. On a match without an open hold, write only `runs/YYYY-MM-DD.json` and the release-index check-in.
   The record's `run_date` is the day of the check and its `release_date` is the
   published release that stays current. `latest_release`, `releases/` and
   `latest/` artifacts stay unchanged. The raw hash in a semantically equivalent
   response can differ from the published raw hash, so a later check may need
   normalization again.
5. Build and validate changed content, then count the published zones whose
   `zone_id` is absent from the source. Additions never offset losses. More than
   `lifecycle.loss_limit(slug)` (5% by default, configured with
   `maximum_lost_object_percent` in `catalog/ingestion-quality.json`) creates a
   persistent hold. An empty source is held without building an empty bundle.
6. A held run retains valid candidate bytes, attempts one Slack decision request,
   returns `status: held` and exits zero. It writes no canonical dataset artifacts
   or check-in record. While held, subsequent checks normalize and rebuild even
   when the source matches the baseline; they restage the candidate and refresh
   the observation. A healthy check cannot release the hold.
7. Otherwise publish the complete bundle with generation preconditions. Dated
   artifacts precede `latest/`; the final manifest and successful run record
   complete publication and the release-index update. The record carries
   `lost_objects`.

The stage order and the rules between stages are those of
[`ingestion/common/lifecycle.py`](../common/lifecycle.py); `run.py` supplies the
MPAtlas stages.

The first monthly run compares against the existing manually published sidecar;
it does not require an earlier monthly-job run record. Same-day completed runs
are idempotent. `RUN_DATE` overrides must be newer than the latest release.
An incomplete same-date release blocks publication and requires reviewed repair;
do not delete objects or choose another date merely to evade the failed release.

The [shared fingerprint contract](../../docs/source-fingerprints.md#mpatlas-mpatlas-content-v1)
defines persisted hash fields and source-specific limitations.

## Review a held release

The shared [hold flow](../common/source_quality.py) persists MPAtlas state at
`gs://skytruth-shared-datasets-1/_catalog/ingestion-holds/mpatlas-zone-assessments.json`.
Candidates use
`gs://skytruth-shared-datasets-1/_scratch/pending-publishes/mpatlas-zone-assessments/source-loss-{candidate_id}/`.
The hold and candidate formats and protected promotion are shared with
[EAMLIS](../../docs/eamlis-source-quality.md); MPAtlas retains source-field
`zone_id` identity and has no EAMLIS identity hooks or net-count threshold.

1. Inspect the hold:

   ```bash
   uv run python scripts/eamlis_source_loss_review.py --asset mpatlas-zone-assessments status
   ```

2. Run the existing **Inspect held EAMLIS candidate** workflow from `main`, with
   `asset=mpatlas-zone-assessments` and the exact candidate ID. Compare the
   candidate and baseline in the private preview catalog linked by its summary.
   The workflow downloads retained bytes; it does not rebuild dataset artifacts.

3. Prepare the immutable plan and printed PR fences:

   ```bash
   uv run python scripts/eamlis_source_loss_review.py --asset mpatlas-zone-assessments prepare-plan
   ```

4. Include the generated `.github/dataset-plans/` document, matching fences,
   investigation rationale, consumer impact and preview evidence in a focused
   PR. Obtain `jonaraphael` review of that exact head (or the existing restricted
   self-authored acceptance), then merge. The protected approved-mutation workflow
   promotes only those retained bytes with their captured source/destination
   generations. Finalization, release-index rebuilding and byte verification
   precede clearing the hold. If a later check changes the candidate reference,
   prepare and review a new plan. No local resolve command can grant authority.

## Artifact contract

Canonical root:
`100-geographic-reference/130-protected-areas/mpatlas-zone-assessments/`.
Five artifacts retain the established names and formats under both `releases/DATE/`
and `latest/`: `.fgb`, `.pmtiles`, `.metadata.ndjson.gz`, `.schema.json`, and
`.manifest.json`. `feature_id` remains the unique, non-null source `zone_id` as
text; invalid or duplicate IDs fail. FGB and sidecar retain source attributes and
stored geometry/properties hashes. Display tiles contain only geometry and
`feature_id`, at zooms 0–10; exact spatial analysis uses the unsimplified FGB.
Native tool paths and versions are captured in the manifest source evidence and
run record. PMTiles is converted explicitly from MBTiles and validated with
magic bytes, `pmtiles verify`, metadata inspection and representative tile decode.

Current counts, source versions and hashes belong to
`_catalog/releases/mpatlas-zone-assessments.json` and run records. The asset doc's
counts describe the initial October 2026 snapshot. Normal refreshes need no Git
commits, tracked catalog edits or generated feature IDs. This asset has no locale
sidecars or translation-source contract.

## Runtime and protected deployment

Entrypoint: `python -m ingestion.mpatlas_monthly.run`. Configuration:
`GOOGLE_CLOUD_PROJECT`, `SHARED_DATASETS_BUCKET`, optional execution-only
`RUN_DATE`, `SHARED_DATASETS_SLACK_WEBHOOK_URL` from the existing Slack webhook secret, and optional `SHARED_DATASETS_WORKDIR` for temporary files. The
production job uses 4 CPUs, 8 GiB RAM, a one-hour timeout and no task retries.
The one-hour limit leaves over six times the observed 8.6-minute maximum across
MPAtlas, EAMLIS and sea-ice executions (October–November 2026), while bounding
stalled executions. WDPA retains its separate multi-hour processing limit.
The pinned container includes GDAL 3.6.2, Tippecanoe 2.52.0 and PMTiles 1.30.1.
Its PMTiles build retains Go's checksum-verified module cache and a
target-specific compiler cache, and embeds the CLI version in one compilation.
Download failures still fail the build; no checksum or image checks are skipped.

After review and merge, main CI routes through the existing IAM and registry
prerequisites and cron-alert-policy sync, then
`.github/workflows/mpatlas-monthly-deploy.yml`. Deployment verifies the exact
tested-main CI image and signed deployment claims, pushes the retained image
without rebuilding, and applies a saved plan constrained to the MPAtlas Cloud
Run job, its scheduler invoker binding, scheduler and the optional canonical
destructive-action deny policy (adding the approved MPAtlas writer exception).
The IAM sync owns the two MPAtlas service accounts, their scoped actAs grants and dataset-root storage
binding, its exact hold object and candidate-prefix storage grants, Slack secret
access, and viewer access on the existing `mpatlas-monthly` job. The existing
**Translation notice secret IAM bootstrap** scope first grants policy management
restricted to that job, then **Scheduled ingestion deploy IAM sync** installs the
runtime grants. The Slack secret needs an enabled version before image rollout.
MPAtlas uses no translation-debt storage permissions. First-time service-account and scheduler creation uses the existing
`dataset_usage_deployer` authority on the shared Terraform identity; its owner
is an explicit graph prerequisite. These grants precede the protected exact-image deployment; merge alone does not
activate the runtime flow.
Other production resources are not in the deployment allowlist.

The deployment runs a waited canary and verifies the latest metadata contract.
Existing project/region Cloud Run and Scheduler failure policies cover this job;
the writer's principal is also registered for canonical-write monitoring. Manual
dispatch from main requires `executor_sha`, `source_run_id` and
`source_run_attempt`; `canary_run_date` is an execution-only override. A running
execution defers deployment unless cancellation is explicitly selected. Follow
the [scheduled-ingestion skill](../../.claude/skills/deploy-scheduled-ingestion/SKILL.md)
for terminal execution, release-index, metadata and alert verification. No local
Terraform apply or canonical GCS writes are part of PR preparation.

## Local validation

```bash
uv run pytest tests/test_mpatlas_monthly.py
RUN_GDAL_INTEGRATION_TESTS=1 uv run pytest tests/test_mpatlas_monthly.py
```

The native fixture exercises mixed point/polygon geometry, a repaired
self-intersection, stable source IDs, FGB hashes and a verified compact tile
archive. Production-source fractional tests must use `prepare_metadata` and
`build_outputs` directly in a named temporary workspace; they never call
`ingest` or publish sampled data.
