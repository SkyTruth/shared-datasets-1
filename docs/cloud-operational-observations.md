# Cloud operational observation shadow

Cloud collection is a shadow input to the signed incident reconciler. Existing
Cloud Monitoring operational and security delivery stays in place during this
stage. A runtime fact is evidence, never permission to replay publication,
start an execution, suppress a public incident, or transfer an owner's authority.

## Runtime protocol

`ingestion/common/operational_events.py` emits one JSON line to stderr with
schema `shared-datasets-runtime-operation-v1`. Cloud Run captures it as a
structured log entry. The existing stdout run-record format remains intact.
The supported facts are:

| Kind | Outcomes | Evidence |
| --- | --- | --- |
| `job_outcome` | success, skipped, blocked, failed | Execution context and record count; generic failures have uncertain mutation and unknown published-data state. |
| `identity_decision` | required | Asset/release, case count and canonical case-set digest. Changed cases enrich the same asset/release episode. |
| `translation_maintenance` | required, already_claimed, delivered, delivery_unknown, failed | Coverage counts and exact manifest/legacy receipt references, without source text. |

The identity and translation helpers retain their existing delivery paths until
reviewed cutover. In particular, an existing translation claim does not prove
delivery and must never cause an automatic resend. Publication remains complete
when its later notification or receipt fails.

## Collection and persistence contract

`scripts/cloud_operational_observations.py` scans the two filtered views on the
global `_Default` and `_Required` Logging buckets. It covers all regional Cloud
Run jobs and Scheduler failures, including newly added jobs. Each pass scans up
to 24 hours, overlaps the previous checkpoint by ten minutes, and leaves a
two-minute ingestion lag. Delayed ingestion beyond that overlap is a remaining
coverage limitation; shadow evidence must assess it before cutover.

Every page and its authoritative execution/receipt metadata must be read before
the candidate checkpoint is complete. Safe read retries share the operation's
single budget and discard a partial snapshot before restarting enumeration.
The deduplication key binds the authenticated log name and insert ID. Reusing
that identity with different log bytes is an error. A replay gap beyond the
bounded horizon is an explicit failure, never permission to skip history.

The checkpoint contains `covered_through`, bounded `seen` source identities,
pending facts, pending execution watches, and coverage evidence. Pending
executions are checked again even when no new log event arrives. A terminal
transition adds a distinct evidence identity; a running observation does not
establish recovery.

The integration entry point is:

```python
reconcile_cycle(
    previous_checkpoint, reader, now=utc_clock,
    reconcile=evaluate_verified_facts,
    persist=save_signed_checkpoint_and_evaluation,
    heartbeat=reader.heartbeat,
)
```

`reconcile` must be idempotent by `source_id` and return an object with
`complete: true`. `persist` must save both the candidate checkpoint and its
evaluation in one authenticated durable transaction, verify its main
signature/provenance and delivery state, and return the freshly verified durable
object. A write acknowledgement alone is insufficient. A failed or uncertain
save produces no heartbeat; reload authoritative state before retrying. The
signed v3 notification ledger owns production persistence and supervision.

The production workflow can use separate trusted phases: `collect(...)` returns
a proposal without persistence or heartbeat; `evaluated_collection(proposal,
policy_result)` returns exact checkpoint/evaluation bytes for signing. After
persistence, attestation and delivery verification,
`confirm_heartbeat(expected, verified_durable, delivery_verified=True,
heartbeat=reader.heartbeat)` compares those exact bytes and refuses unresolved
gaps. Its metric sample retains the cycle's original timestamp, so replaying old
verified evidence cannot refresh health.

Runtime context is checked against the log resource and exact authoritative
execution. Repository revision or owner claims in a payload are not trusted.
Resolved execution image digests may identify a policy binding. An unresolved
tag remains an explicit provenance gap, preserving the authoritative execution
resource and UID. A canonical execution-UID anchor can only support the policy's
immediate unresolved-provenance action; it cannot permit owner suppression or
retry. Legacy non-UUID identifiers remain raw evidence. A gap stays pending and
inhibits the heartbeat until the evaluator returns its exact `source_id` in
`resolved_source_ids` after authoritative reconciliation.

To correlate a cloud execution with a GitHub attempt, verify the signed target
receipt and complete same-attempt job evidence first. One runtime success cannot
resolve every failed job in a workflow or silently drop coarse uncertainty.

The local collection CLI writes atomic shadow evidence only:

```bash
uv run python scripts/cloud_operational_observations.py \
  --checkpoint "${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}/_scratch/cloud-shadow/checkpoint.json"
```

It does not evaluate policy, send Slack, or emit a heartbeat. Its file is not a
replacement for authenticated production persistence.

## Access and protected installation

Terraform output `operational_observer_workload_identity_provider` configures
`GCP_OPERATIONAL_OBSERVER_WORKLOAD_IDENTITY_PROVIDER`, and
`operational_observer_service_account` configures
`GCP_OPERATIONAL_OBSERVER_SERVICE_ACCOUNT` for the trusted notification worker.
The provider permits only this repository's exact main notification workflow.
Its unique `observer_repository` attribute prevents other providers in the
shared pool from conferring observer impersonation through a generic repository
binding.

The observer receives view-local `roles/logging.viewAccessor`, execution get/list,
translation receipt get, and time-series create. It receives no project-wide log
viewer, dataset write/list, execution start, secret-read, or infrastructure
mutation permissions. Receipt IAM is restricted to registered asset `runs/`
prefixes and the `.translation-notice.json` suffix. The client requests metadata
only; Cloud Storage's `objects.get` permission also allows the contents of those
specific receipt objects. Google IAM does not constrain time-series create to
one metric; the observer client emits only the fixed heartbeat metric.

The protected cron policy workflow runs bootstrap, exact allowlisted observation
setup, then policy sync. Setup blocks deletion/replacement and probes the exact
saved plan's resource operations. New Terraform-identity grants are limited to
two-bucket log-view setup and metric-descriptor create/get/list. Existing
protected identity/project/bucket IAM authority remains a prerequisite. Missing
WIF-provider creation authority must be resolved through a reviewed protected
setup change; it is not silently added to the observer role.

## Heartbeat and cutover evidence

The independent native absence policy opens one notification after 30 minutes
without a successful reconciliation heartbeat. A complete scan, successful
policy evaluation and acknowledged durable persistence precede emission.
Unresolved provenance gaps are recorded but do not emit this heartbeat.

Metric absence detection requires a first sample after policy creation or
update. The first successful shadow reconciliation must seed the metric only
after the protected policy sync completes; retain evidence of both times and
confirm Monitoring received the sample. See [Google's absence-policy
contract](https://docs.cloud.google.com/monitoring/alerts/metric-absence).

Before removing direct operational Slack routes, require 24 continuous hours of
successful shadow reconciliation and verified heartbeat coverage, with no
unresolved scan/persistence/provenance gaps. Retain source identities, decisions,
checkpoint and policy timestamps, and the routing audit as operational evidence.
Synthetic tests use offline transports or an explicitly isolated destination.
Independent security and pipeline-health delivery remain available throughout
cutover and rollback. No routine shadow outcome is posted to the shared channel.
