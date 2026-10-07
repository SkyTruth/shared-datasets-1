# Passive dataset usage

The repo observes current consumption without changing consumers, SDKs, dataset
URLs, or download authentication. `/usage` and `GET /api/usage` belong to the
existing IAP-protected catalog viewer. No report is published in `_catalog/web`.
There are no automatic retirements, deletions, refresh shutdowns, or retirement
notifications.

A qualified retirement-review candidate is an **active** dataset with 365 fully
elapsed observation days, no downstream reads or dataset-specific interest in
that window, current reporting, and no known unresolved collection gaps. Its
observation begins with the next complete UTC day after both registration and
verified monitoring. Delivery is allowed 48 hours after each day ends. A new registration or mapping/parser
policy starts its own observation period. Candidates remain unavailable during
this first year.

Logs are observational. Public GCS usage logging is best-effort; successful
probes establish pipeline operation, not completeness. Downloaded copies may
remain in use. Stewards must inspect catalog activity and failures, consult the
application groups identified in the report, and review consumer guidance before
proposing lifecycle changes through the normal reviewed metadata workflow.

## Evidence and classification

| Source | Observations | Count semantics |
| --- | --- | --- |
| `gcs_audit` | Authenticated `storage.objects.get` | Audit operations; conservative interest because a get does not prove a completed bytes download |
| `cdn` | GET/HEAD requests, including cache hits | HTTP requests; response bytes when supplied |
| `gcs_usage` | Native bucket usage CSV records, including public access | HTTP requests; response bytes when supplied |
| `catalog` | Successful repo-owned cached lookups and URL issuance | Catalog activity / access intent, never completed downloads |

Sources overlap and are never added together as downloads or users. HEAD,
revalidation/304, object metadata, and asset-specific `_catalog/releases/{slug}.json`
reads count as interest. In particular, the Python SDK fetches that release index
before returning a locally cached dataset. General catalog listings, README
browsing, run records, source archives and telemetry-storage requests do not
count as dataset interest.

`catalog/dataset-usage.json` maps existing reader service accounts to display
names for application groups. A shared account cannot identify an individual
application within its group. Exact known referrer hosts give inferred
attribution; the initial hosts come from the existing CDN browser-origin list.
Unrecognized traffic is Unknown and blocks candidacy. Full
referrers, IPs, user agents, account emails, signed URLs and query strings never
enter the report. Display names cannot contain account emails.

Maintenance/catalog exclusion requires a known authenticated principal, trusted
Cloud Run platform provenance, or a random nonce recorded by this worker for its
own source/asset probe. User-agent or referrer text alone never excludes activity.
An origin request caused by a known CDN probe can be excluded from native usage
counts, but it cannot satisfy that source's independent anonymous probe. Unknown
origin fills remain conservative evidence of interest.

Each day preserves classification-policy and catalog-mapping hashes. Version 1
retains catalog snapshots and last-observed timestamps in the ledger. The policy hash binds the four processor source files and configuration, so
code or attribution changes cannot silently reuse old coverage. Policy changes
restart observation and replay retained inputs when their generations still exist.
Never reinterpret expired history as zero. There is no historical backfill.

## Storage and processing

Terraform creates two uniform-access buckets with public-access prevention:

- `gs://skytruth-shared-datasets-1-usage-raw/`: audit/CDN/catalog export and
  native usage logs under `storage-usage`; 30-day lifecycle.
- `gs://skytruth-shared-datasets-1-usage-state/`: immutable SQLite ledgers,
  precomputed JSON reports, and `published/manifest.json`; 460-day lifecycle.

Only the processor and authorized infrastructure administrators read raw logs.
The viewer can read objects below `published/` only, not ledgers or raw evidence.
The worker has no shared-dataset write permission; authenticated probe reads are
limited to two catalog-selected sentinel objects and the published catalog CSV.
The worker reads that generation-pinned current CSV each day; missing/oversized
catalogs fail collection, and removed or non-active assets cannot become candidates. Logs from telemetry buckets
cannot match the usage sink's source-bucket filter, avoiding a feedback loop.

A daily worker streams JSON arrays/NDJSON and header-based CSV in bounded chunks.
Each record is limited to 512 KiB, the disk ledger to 128 MiB, SQLite's cache to
8 MiB, and published JSON to 8 MiB. SQL groups daily evidence without loading all
raw records or event IDs into memory. At 1 CPU / 1 GiB, it has a 30-minute timeout
and no task or Scheduler retries. Schedule: **09:00 UTC**.

Input receipts use object path plus generation. Per-source request identifiers
and event time deduplicate overlap within 30 days. Late shards update affected
daily aggregates transactionally; the report is recomputed from those partitions.
Daily aggregates, source coverage, policy evidence, probe receipts and processing
receipts stay for 460 days. Last read/interest/catalog timestamps are copied forward into each
new ledger snapshot even after those daily partitions expire.

The worker uploads an immutable ledger, then an immutable report, then replaces
one manifest with a generation precondition. The manifest is the only commit
point. A crash before publication changes neither visible progress nor results;
a lost response after publication is recovered by loading the manifest. A
concurrent publisher fails its compare-and-swap and cannot overwrite newer
results. Orphan uploads are invisible and expire by lifecycle.

Parsing, record-budget and disappeared-generation failures roll back that input
and publish an explicit source gap with its receipt still uncommitted. A shard
with unknowable timestamps suspends retained coverage for that source. Expired
late inputs and outages longer than raw retention also make coverage unknown.
Policy/mapping corrections replace retained totals and receipts only after the
whole replay succeeds. Failed or unavailable-generation reconciliations preserve
the original totals and evidence versions, remain pending for retry, and record
the previous ledger's checksum alongside the explicit gap.
The viewer independently marks reports older than 48 hours stale, including when
a dead worker cannot write a failure marker. Missing summaries return 503 rather
than an empty report.

## Reviewed rollout and cost gate

Initial settings deliberately leave collection disabled and the schedule paused.
Absent bucket access logging is an expected disabled configuration: the worker
publishes unverified coverage gaps and untrusted probe receipts without enabling
collection. Malformed configuration, denied reads and incompatible saved state
still fail. The production-image check runs a small cold-start fixture through
the installed entrypoint code, Health checks, SQLite collector and Storage
publication boundary with in-memory transport, no credentials and no network.
All infrastructure changes use `Dataset usage deploy`, through reviewed `main`,
exact passing CI and retained tested-image evidence, the production environment,
durable deployment receipts, and the existing production-state queue. Its plan
checker refuses unrelated resources, deletes/replacements, public telemetry,
write-audit removal, and shared-bucket changes outside logging. Existing IAP and
production failure alerts apply. Never apply production Terraform locally.

The existing CDN uses backend buckets, whose request logging is enabled by
Google. Inspect the effective Logging sinks and exclusions during rollout;
the export must retain requests including cache hits. See Google's
[CDN logging documentation](https://docs.cloud.google.com/cdn/docs/cdn-logging-monitoring).

1. Before enabling read auditing, inspect project/folder/organization audit
   settings, exemptions, existing sinks and Logging bucket retention/exclusions.
   Read auditing affects **all GCS buckets in the project**. A narrow export
   filter does not remove that upstream volume or cost. Preserve pre-existing
   audit configuration; if the adoption guard reports drift, reconcile it by
   reviewed code rather than bypassing the guard.
2. Record at least one measured day of project-wide volume and pricing evidence
   (a representative week is preferred). Project monthly incremental Logging,
   raw storage, state storage, operations, worker and viewer costs separately.
   Include the steady 30-day raw / 460-day state footprint and current billed
   Logging retention/exclusions. Account for the whole project and effective
   billing/free allowance, not just exported shared-bucket logs. Do not silently
   sample or exclude low-frequency traffic to meet the target.
3. Put reviewed preliminary cost evidence in
   `catalog/dataset-usage-activation.json`, then set `collection_enabled: true`
   in `catalog/dataset-usage.json` through a PR. Leave `verified_at: null`.
   The deploy checker blocks collection if measured evidence is absent or the
   incremental estimate exceeds **$25/month**. A higher estimate requires a
   revised explicit decision, not activation under this policy.
4. Deploy collection with the schedule paused. The workflow runs one bounded
   worker canary. After the 48-hour delivery allowance, dispatch the same protected
   workflow from a newly tested reviewed revision when another canary is needed;
   reconcile a previous failed/unknown deployment before retrying its revision.
   Verify representative authenticated reads, anonymous downloads, CDN cache
   hits, cached SDK release-index interest, and cached catalog lookups in exported
   records **and** their classified report results. Confirm native CSV/log
   fields and origin-fill behavior; synthetic probes cannot replace these checks.
5. Compare measured rollout volume to the preliminary cost projection. Record
   all source and traffic checks, updated project-wide cost, verification time,
   and the policy hash printed by
   `uv run python -m ingestion.dataset_usage.run --print-policy-hash`.
   Merge that activation evidence. Only a matching verified policy at or below
   the target lets Terraform enable the daily schedule.

The activation evidence has this shape (replace numbers and checks with measured
reviewed evidence; this is not an activation record):

```json
{
  "schema_version": 1,
  "verified_at": "2026-10-07T09:00:00Z",
  "configuration_sha256": "<printed policy hash>",
  "verified_sources": ["gcs_audit", "cdn", "gcs_usage", "catalog"],
  "estimated_monthly_cost_usd": 12,
  "evidence": {
    "inherited_audit_settings_reviewed": true,
    "existing_sinks_and_exemptions_reviewed": true,
    "traffic_checks": {
      "authenticated_read": true,
      "anonymous_download": true,
      "cdn_cache_hit": true,
      "cached_sdk_interest": true,
      "catalog_cache_lookup": true
    },
    "cost": {
      "measurement_days": 7,
      "project_wide_audit_volume_reviewed": true,
      "logging_retention_and_exclusions_reviewed": true,
      "monthly_usd": {
        "project_logging": 4,
        "raw_storage": 2,
        "state_storage": 2,
        "operations": 1,
        "worker": 2,
        "viewer": 1
      }
    },
    "references": ["reviewed PR with private measurement evidence"]
  }
}
```

The component total must match the estimate. Collection health checks the live
bucket logging destination/prefix, exact sink filter, project audit configuration
and sink-export errors (all metric pages), plus independently delivered daily
probes. Missing probes, configuration/cost gates and identifiable failures keep
coverage unavailable. The report says **no known gaps**, never complete coverage.
Inherited audit policy review is part of activation evidence; its correctness
cannot be inferred from successful probes.

## Recovery and steward review

Preserve the current manifest and its generation first. Inspect the referenced
immutable ledger, input receipts, source gaps, policy hashes and failed execution.
Never independently edit a checkpoint or report, reset the ledger to invent zero
history, or retry an old manifest write without its original generation condition.
A later worker loads the committed ledger and retries uncommitted inputs. Partial
uploads need no promotion or deletion; they cannot become visible by themselves.

If the raw generation still exists, correct the parser/mapping in reviewed code
and replay retained inputs with the new policy. Existing daily totals remain
explicit historical evidence; policy changes restart the observation clock and
cannot certify unavailable older periods. If evidence has expired, preserve the
unknown coverage and begin a fresh verified observation period. A source gap
remains unresolved in the current policy; it is never erased by a new successful
probe. The gap will leave the rolling window only after trustworthy future days
have replaced it. If the manifest/ledger expired during an exceptionally long
outage, recover a retained integrity-checked snapshot if one exists; otherwise
start fresh and disclose loss of observation history.

Review a candidate's observation dates, unknown traffic, application groups,
per-source requests/bytes, catalog intents and failed access attempts. Confirm
with stewards that ongoing cached copies and infrequent use are understood.
Any eventual retirement is a separate reviewed lifecycle metadata decision;
published releases remain readable and citable under the existing repository
rules.

Authoritative limitations and formats:
[usage logs](https://docs.cloud.google.com/storage/docs/access-logs),
[audit/custom fields](https://docs.cloud.google.com/storage/docs/audit-logging),
[GCS export limits](https://docs.cloud.google.com/logging/docs/export/storage),
[sink errors](https://docs.cloud.google.com/logging/docs/export/troubleshoot).
