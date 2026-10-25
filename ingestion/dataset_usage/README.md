# Passive dataset usage visibility

The private `/usage` page and `/api/usage` endpoint show native Cloud Storage CSV
observations: successful and partial reads, metadata checks, failures and response
bytes. Referrer-derived application labels are inferred; missing attribution stays
unknown. The 30/90-day views show days with requests, not application active days.

Downstream consumers have no tracking responsibilities. They do not install or
upgrade an SDK, send reports, instrument caches, expose reporting endpoints or
obtain telemetry write permissions. Keep their existing access and caching logic.
No organization audit-policy or Log Router inspection is required.

Native logs are best-effort. CDN cache hits, application-owned caches, downloaded
copies and offline use are outside coverage. Neither silence nor missing evidence
establishes that a dataset is unused. Existing security auditing, signing and CDN
operational logging remain independent.

## Collection and history

The daily Cloud Run job parses generation-pinned native logs, maps dataset paths,
deduplicates request IDs and publishes a bounded SQLite ledger plus a private
report. Known nonce probes are excluded as maintenance. Raw retention remains
seven days and aggregate/snapshot retention remains 460 days. Last-observed
bucket timestamps survive aggregate expiry.

Reconciliation uses the reporting hour in native usage-log names to exclude
expired logs that cannot contain events in the replay window, even when they
were first processed later. Older aggregates and their receipts stay intact.
Missing generations inside the replay window, and unrecognized names that
cannot prove an earlier reporting hour, still stop reconciliation with an
explicit gap. [Google's hourly log contract](https://cloud.google.com/storage/docs/access-logs#download_logs)
allows earlier events in a file, never events from a later hour.

Configuration, report and ledger schema version 3 retire `app_usage`. The worker
accepts existing version-2 ledgers and the older unversioned ledger, preserves
historical tables, totals, timestamps and original source labels, and records a
stable collection cutoff for retired sources. Historical application tables are
retained as archival data under the original 460-day aggregate retention; last
timestamps survive expiry. They are never collected, replayed, reset or shown as
current activity. Prior immutable published reports remain available under their
existing retention policy. New ledgers contain no application-reporting tables.

Only native CSV inputs are collected. Existing `app-usage/` objects are ignored
and retain their existing raw lifecycle; no remote objects are manually deleted.
The dashboard reads report versions 1, 2 and 3 during deployment, renders bucket
observations only and provides no application connection or environment controls.
Deploy this compatible reader before the version-3 writer. The signed reader
byte gate enforces that order. Remove transitional reader compatibility only after
verification; do not discard historical reports to simplify migration.

Ledger and report snapshots remain immutable and checksum-bound. Only the small
manifest is replaced with its observed generation. Failed or concurrent publication
leaves the previous committed snapshot usable.

## Activation and cost

`catalog/dataset-usage.json` configures passive collection and bounded referrer
mapping. There is no producer registry. `catalog/dataset-usage-activation.json`
binds incremental cost evidence to the current configuration/code hash and native
checks: `authenticated_read`, `anonymous_download` and `raw_delivery`.

The budget target remains $25/month. A cost-approved bounded canary can run while
source readiness is unverified; Scheduler stays paused until all native checks
pass. Health checks verify bucket privacy, seven-day retention, logging destination
and the native log-delivery permission. They never query organization policy.
A successful source read alone does not prove delivered logs.

## Removing obsolete reporting and exports

Application reporting is withdrawn for every consumer. The shared deployment
removes the catalog reporter environment and all eight exact previously deployed
application writer grants. Its temporary deletion allowance validates the precise
tracking bucket, principal, create-only role, prefix and condition title; all new
writer grants, unrelated deletions and replacements are refused. Remove that
transitional allowance after the grants are verified absent. No consumer project
resource or dataset access permission changes.

`terraform/envs/prod/dataset_usage_legacy.tf` separately retains the six disabled
old audit/export resources. Their removal still requires fresh error-free native
publication, reviewed activation/cost evidence and two healthy native UTC days.
Application receipts and consumer deployments are no longer prerequisites. Keep
both buckets, historical aggregates and recovery evidence. Use the reviewed,
serialized protected workflow for every infrastructure mutation; never apply
production Terraform locally.

See [the rollout plan](../../docs/dataset-usage-rollout.md) and
[CI preflight](../../docs/ci-preflight.md) for integration and validation boundaries.
