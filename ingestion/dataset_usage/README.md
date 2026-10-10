# Passive dataset usage visibility

Purpose: **visibility** into observed dataset access, metadata interest,
application groups and collection health. Consumers and download access need no
changes. `/usage` and `GET /api/usage` use the catalog viewer's IAP/domain policy.
Public catalog artifacts contain no usage data; dataset files/lifecycle are unchanged.

Logs cannot prove non-use of downloaded copies. Public usage logging is best-effort;
probes establish pipeline operation, not complete coverage.
Missing information never means zero use. Activity is visible immediately,
with separate 30/90-day request groups and persistent last-observed timestamps.

## Evidence and application groups

| Source | Observations and count semantics |
| --- | --- |
| `gcs_audit` | Authenticated `storage.objects.get`; interest because audit operations do not prove completed downloads |
| `cdn` | HTTP GET/HEAD requests including cache hits; response bytes when supplied |
| `gcs_usage` | Native bucket usage CSV including public access; HTTP requests and supplied response bytes |
| `catalog` | Successful cached lookups and URL issuance; catalog intent, never completed downloads |

Sources overlap and must not be summed as downloads or users. HEAD, 304,
object metadata and asset-specific `_catalog/releases/{slug}.json` reads are
interest, including a Python SDK release-index check before returning cached
data. General listings, READMEs, run records, source archives and telemetry
storage requests are excluded. Failures remain separate from successful reads.

`catalog/dataset-usage.json` maps reader principals to application-group display
names. Shared accounts identify groups, not individual applications. Exact known
referrer hosts provide inferred attribution; unmatched activity is **Unknown**.
Reports omit full referrers, IPs, user agents, account emails, signed URLs and
query strings. Display names cannot contain account emails.

Maintenance/catalog exclusion requires a known authenticated principal, trusted
Cloud Run provenance or a worker-recorded source/asset probe nonce. Referrer or
user-agent text alone cannot exclude activity. CDN probe origin fills cannot
satisfy the independent anonymous usage probe. Other origin fills remain visible.

## Collection, storage and health

The worker reads the generation-pinned catalog daily and retains lifecycle status.
Policy hashes bind config/code; catalog hashes/snapshots preserve historical interpretation.

Private uniform-access buckets enforce public-access prevention:

- `gs://skytruth-shared-datasets-1-usage-raw/`: audit/CDN/catalog export and
  native `storage-usage` logs; reviewed `raw_retention_days` defaults to 7.
- `gs://skytruth-shared-datasets-1-usage-state/`: immutable ledgers, reports and
  `published/manifest.json`; 460-day lifecycle retains bounded historical evidence.

Only the processor and authorized administrators read raw logs. The viewer reads
`published/` only. The worker cannot write shared datasets; its authenticated
reads are limited to catalog-selected sentinels and the published CSV. Telemetry
bucket requests cannot match the source-bucket sink filter.

At 09:00 UTC the 1 CPU / 1 GiB worker streams JSON/NDJSON and header-based CSV,
with a 30-minute timeout and no task/Scheduler retries. Budgets: 512 KiB per
record, 128 MiB ledger, 8 MiB SQLite cache and 8 MiB report. Input path/generation
receipts and per-source request IDs deduplicate within the raw-retention window.
Daily groups, coverage, policy evidence and receipts remain for 460 days;
last-read/interest/catalog timestamps survive partition expiry.

Delivery has 48 hours after each UTC day ends. Independently delivered trusted
probes establish source health; missing probe days are pending, pre-collection
days unknown. The health summary includes all 460 retained days and recent daily
reasons. Missing probes, invalid configuration/cost evidence, parsing or budget
failures, expired late inputs and outages at least as long as raw retention are
disclosed. Healthy means **no known gaps**. Existing gaps survive later probes.
The viewer and page independently mark reports older than 48 hours stale;
missing or corrupt summaries return 503, never an empty successful report.

## Reviewed rollout and cost gate

Collection starts disabled, activation unverified and the schedule paused.
The disabled cold-start fixture exercises the installed CLI, Health, SQLite and
Storage publication with in-memory transport, no credentials or network.
`Dataset usage deploy` consumes reviewed main, exact passing CI/tested images,
protected production environment, deployment receipts and the production-state
queue. Its allowlist rejects unrelated/destructive changes, public telemetry,
write-audit removal and shared-bucket changes outside logging. Existing IAP and
production failure alerts remain in force; never apply production Terraform locally.

1. Review inherited/project audit settings, exemptions, sinks, Logging retention
   and exclusions. Read auditing affects every GCS bucket in the project.
   Preserve existing audit policy; reconcile adoption drift through reviewed code.
2. Record at least one measured day (prefer a representative week) and project
   incremental monthly Logging, raw/state storage, operations, worker and viewer
   costs. Review all sinks and allowances. The scoped `_Default` exclusion avoids
   a duplicate stored copy; the independent GCS export remains unsampled.
3. Put preliminary evidence in `catalog/dataset-usage-activation.json`, enable
   `collection_enabled` by PR and leave `verified_at` null. Collection requires
   component totals matching an estimate at or below **$25/month**.
4. Deploy with the schedule paused. After the delivery allowance verify exported
   records and classified results for authenticated reads, anonymous downloads,
   CDN cache hits, cached SDK interest and cached catalog lookups. Synthetic probes
   alone cannot establish these checks. Reconcile failed deployments before retry.
5. Update measured cost, all source/traffic checks, verification time and the hash
   from `uv run python -m ingestion.dataset_usage.run --print-policy-hash`.
   Only complete matching activation evidence enables the daily schedule.

Live Health checks verify bucket logging destination/prefix, unsampled sink and
matching duplicate exclusion, raw retention, project audit settings and every
page of sink-export errors. The activation file and `activation_valid` /
`cost_evidence_valid` in `run.py` own the evidence contract.

## Publication and recovery

Immutable ledger and report uploads precede a generation-preconditioned manifest
swap, the sole commit point. Crashes/orphan uploads cannot advance visible
checkpoints; competing writers fail compare-and-swap. Preserve the manifest,
generation, ledger checksum, receipts and gap reasons when investigating failures.
Never edit checkpoints/reports independently or reset history to invent zero use.

Corrected parser/mapping policies replay retained original generations as one
transaction. Failed/unavailable replays preserve totals and evidence versions,
publish gaps and remain pending. Expired history cannot be reclassified as zero.
If an exceptionally long outage loses the ledger, recover an integrity-checked
snapshot or disclose lost history when starting fresh. Orphans expire by lifecycle.
Source formats: [usage logs](https://docs.cloud.google.com/storage/docs/access-logs), [audit fields](https://docs.cloud.google.com/storage/docs/audit-logging),
[export limits](https://docs.cloud.google.com/logging/docs/export/storage), [sink errors](https://docs.cloud.google.com/logging/docs/export/troubleshoot).
