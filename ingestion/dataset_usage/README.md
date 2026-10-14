# Dataset usage visibility

The private `/usage` page and `/api/usage` endpoint show two distinct observations:

- `gcs_usage`: native Cloud Storage CSV logs provide observed requests and response
  bytes, including anonymous requests. Referrer application labels are inferred.
- `app_usage`: opted-in application backends create daily receipts using managed
  runtime identities. These show application–dataset active days and last reported
  activity, including successful cache returns. They are application reports,
  not download counts or independent proof of a user's behavior.

Neither source establishes non-use. Native logs are best-effort and exclude CDN
cache hits. Uninstrumented applications and offline use remain unobserved. No
organization audit-policy or sink read is required, and the tracker never enables
project-wide `DATA_READ` auditing. Existing security auditing and CDN operational
logging remain independent.

## Producer registration and permissions

`catalog/dataset-usage.json` is the producer registry. Each producer has a stable
identifier, application display name, `production` or `development` environment,
and either `registered` status or `configured` status with its verified runtime
service account. A reserved reader account is not evidence of an application's
actual runtime identity. The report calls a producer `not_connected` until a valid
receipt has been observed; this never means zero use.

Configured writers get only `roles/storage.objectCreator` on their exact prefix:

```text
app-usage/{producer}/v1/{UTC-date}/{random-uuid}.json
```

The raw bucket stays private, with uniform bucket-level access. Writers cannot
read tracking records or overwrite/delete receipts. No dataset write grant is
added. Browser code reports through its own application's authorized backend;
credentials, application names, producer identifiers and observation timestamps
come from the backend, never the browser body. Validate the dataset slug and its
current access tier with the application's existing access policy before recording.

The receipt body contains exactly `schema_version: 1`, `asset_slug`, `activity`
(`use` or `interest`), and `observed_at` (UTC). It contains no user identifier,
fingerprint, feature selection, full URL or query contents. The collector derives
application and environment from the IAM-bound prefix and rejects unregistered
prefixes, extra fields and mismatched days.

## SDK and catalog reporting

Python `UsageReporter` and TypeScript `createUsageReporter` expose `recordUsage`
and `flush`. Reporting is opt-in. Record `use` after successfully consuming data,
including a verified cache return, and `interest` after resolving a URL. Fetch
failures do not produce use receipts. Python `Catalog.fetch`, `fetch_dataset` and
`fetch_snapshot_artifact` accept `usage_reporter`. TypeScript snapshot layer
resolution accepts `usageReporter` and records interest; the map helper records
use after the map loads successfully. Application-owned caches need an explicit
record at their successful consumption boundary.

Flush before short-lived workers or requests exit. Reporter uploads are bounded,
create-only, and retry a lost acknowledgement using the same immutable object
name. Upload errors are diagnosed without throwing into dataset operations.
There is no persistent/offline queue. Use an ADC-backed TypeScript Storage client
with automatic retries disabled; the reporter owns its bounded retry policy.
The catalog viewer produces backend receipts only when its producer environment
variable is enabled by the reviewed collection deployment.

## Aggregation and history

The existing daily Cloud Run job consumes generation-pinned raw inputs into a
bounded SQLite ledger. Raw evidence expires after seven days. Daily aggregates,
coverage, receipt inventory and catalog versions are retained for 460 days;
last-observed timestamps survive aggregate expiry.

The report schema is version 2. Its immutable manifest envelope remains version 1
because the publication format has not changed. Application active days are in
`application_windows` and `last_application_activity`; request counts remain in
`windows`. Production is the dashboard default. Historical audit/CDN/catalog
observations retain their original labels in `windows`, and `retired_sources`
records their collection cutoff. No historical counts are reinterpreted as new
application receipts.

The first version-2 publication upgrades an existing version-1 ledger in place,
without resetting totals or replaying retired-source inputs. Later active-source
policy reconciliation still requires complete retained generation evidence;
missing input generations preserve old totals and publish an explicit gap.
Ledger/report bytes are immutable and checksum-bound. Only the manifest is
replaced, using its observed generation. A failed upload or concurrent writer
leaves the previous committed publication usable.

## Activation and controlled trial

Collection starts disabled. `catalog/dataset-usage-activation.json` records measured
incremental cost evidence (at least one measured day, target at most $25/month)
and independent source checks bound to the current configuration/code hash.
Costs cover raw/state storage, operations, worker, viewer and incremental logging
caused by tracker operations; unrelated project or organization costs are not
represented as tracking costs. Use measured traffic to forecast trial costs,
then update the estimate with observed delivery and runtime volume.

Native readiness checks are `authenticated_read`, `anonymous_download` and
`raw_delivery`. Receipt readiness checks are `receipt_delivery`, `cached_use`,
`writer_isolation` and `bounded_failure`. A source can be verified independently.
Collection can be enabled for a bounded canary once cost evidence passes; the
scheduler remains paused until at least one source is verified. The worker also
checks live bucket privacy, retention, native delivery configuration and exact
configured-writer prefix bindings. It never queries organization policy or sinks.

The daily native HEAD probe uses a ledger-recorded nonce. Only the matching
successful request is excluded as maintenance. Disabled collection sends no
probes. Application coverage is explicitly partial, even when valid receipts
are observed; an absent application's report does not establish its inactivity.

All infrastructure changes use reviewed PRs and the protected dataset-usage
workflow. Use `uv run python scripts/ci_preflight.py --base <current-main-sha>
--head <head-sha>` from the frozen clean checkout before pushing. The tested
worker image, queued saved-plan validation/apply and signed deployment receipts
remain required. No local production apply or canonical object mutation is part
of activation.

## Staged retirement

`terraform/envs/prod/dataset_usage_legacy.tf` temporarily retains disabled old
exports, their unused permissions and the previous multi-source probe grant.
Its exact six resources are the only
permitted deletion scope. After replacement proof, a focused retirement PR
removes that file, drops obsolete Logging deployment permissions and removes
version-1 viewer compatibility. Preserve both buckets and all existing evidence.

The cleanup validator reads the current checksum-bound private report and requires
both source activation checks, a fresh error-free version-2 publication, at least
two healthy UTC days for each new source, and observed receipts from each configured
production producer. Only then may `--retire-legacy` admit exact legacy deletes;
replacements and unrelated deletions remain refused. The protected workflow
selects this verification when the legacy file is removed. Add only the temporary
sink/exclusion delete permissions needed for that reviewed cleanup and remove
them after successful retirement.

Monitor and SkyTruthTech registrations remain visible while their actual runtime
or shared-data consumption boundary is unverified. Existing consumer integration
PRs are prerequisites, not permission to take over their branches or merge them.
Claim all-consumer completion only after each actual consumer's rollout evidence
is retained. Pause a failed source/reporter and preserve data; do not restore the
organization-dependent tracking design.

The verified runtime inventory, consumer prerequisites and outstanding identity
work are recorded in [the rollout inventory](../../docs/dataset-usage-rollout.md).
