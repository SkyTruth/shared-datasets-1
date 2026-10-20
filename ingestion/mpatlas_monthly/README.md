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
   source SHA-256 in the published manifest, skip conversion and publication.
2. Otherwise validate source IDs and properties, exclude null geometries, repair
   geometry with GDAL MakeValid, and promote Polygon/Point to MultiPolygon/MultiPoint.
   Serialize the existing three JSON-valued fields as canonical JSON text.
3. Compute `mpatlas-canonical-features-v1`: SHA-256 of canonical JSON containing
   the version and the sorted `(feature_id, geometry_hash, properties_hash)`
   tuples. Compare against the published sidecar's same fingerprint. This ignores
   response whitespace, object-key order, feature order, retrieval provenance and
   release dates. Coordinate ordering and array ordering remain meaningful.
4. On a match, write only `runs/YYYY-MM-DD.json` and the release-index check-in.
   `latest_release`, `releases/` and `latest/` artifacts stay unchanged. The raw
   hash in a semantically equivalent response can differ from the published raw
   hash, so a later check may need normalization again.
5. On a change, build and validate the complete FGB/PMTiles/metadata/schema bundle,
   then publish with shared generation-preconditioned helpers. Dated artifacts
   precede `latest/`; the final manifest and successful run record complete the
   publication and release-index update.

The first monthly run compares against the existing manually published sidecar;
it does not require an earlier monthly-job run record. Same-day completed runs
are idempotent. `RUN_DATE` overrides must be newer than the latest release.
An incomplete same-date release blocks publication and requires reviewed repair;
do not delete objects or choose another date merely to evade the failed release.

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
`RUN_DATE`, and optional `SHARED_DATASETS_WORKDIR` for temporary files. The
production job uses 4 CPUs, 16 GiB RAM, a four-hour timeout and no task retries.
The pinned container includes GDAL 3.6.2, Tippecanoe 2.52.0 and PMTiles 1.30.1.

After review and merge, main CI routes through the existing IAM and registry
prerequisites and cron-alert-policy sync, then
`.github/workflows/mpatlas-monthly-deploy.yml`. Deployment verifies the exact
tested-main CI image and signed deployment claims, pushes the retained image
without rebuilding, and applies a saved plan constrained to the MPAtlas Cloud
Run job, its scheduler invoker binding, scheduler and the optional canonical
destructive-action deny policy (adding the approved MPAtlas writer exception).
The IAM sync owns the two MPAtlas service accounts, their scoped actAs grants and dataset-root storage
binding. First-time service-account and scheduler creation uses the existing
`dataset_usage_deployer` authority on the shared Terraform identity; its owner
is an explicit graph prerequisite. The job adds only its scoped storage and
actAs grants.
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
