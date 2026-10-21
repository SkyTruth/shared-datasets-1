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
| `job_outcome` | success, skipped, blocked, failed | Execution context, per-asset/release outcome and generation-pinned run receipt when available; generic failures have uncertain mutation and unknown published-data state. |
| `identity_decision` | required | Asset/release, case count and canonical case-set digest. Changed cases enrich the same asset/release episode. |
| `translation_maintenance` | required, already_claimed, delivered, delivery_unknown, failed | Coverage counts and exact manifest/legacy receipt references, without source text. |

The identity and translation helpers retain their existing delivery paths until
reviewed cutover. In particular, an existing translation claim does not prove
delivery and must never cause an automatic resend. Publication remains complete
when its later notification or receipt fails.

The auxiliary WDPA execution observer is an explicit resource edge of the
protected WDPA deployment target and shares its immutable image. Signed image
provenance does not require WDPA's multi-hour data verification to finish and
never establishes dataset or deployment recovery. Its 120-second worker uses a
shared 60-second, three-attempt read budget, buffers complete execution pages,
and emits typed failures only before status writes. A later verified observer
execution resolves only observer health, including after a legitimate image
repair. Status-write failures are never reclassified as pre-write read failures
or replayed. The signed cycle retains proven terminal metadata beyond the lagged
log cutoff as a watch buffer; only unresolved observations within coverage block
its completeness heartbeat. Unknown images, jobs and unproven buffers remain
visible provenance gaps.

## Collection and persistence contract

`scripts/cloud_operational_observations.py` scans the two filtered views on the
global `_Default` and `_Required` Logging buckets. It covers all regional Cloud
Run jobs and Scheduler failures, including newly added jobs. Each pass scans up
to 24 hours, overlaps the previous checkpoint by ten minutes, and leaves a
two-minute ingestion lag. Delayed ingestion beyond that overlap is a remaining
coverage limitation; shadow evidence must assess it before cutover.

The view uses only [supported flexible-view
fields](https://docs.cloud.google.com/logging/docs/logs-views#filters_for_log_views):
project/region resource labels, resource type and `log_id`. Its access boundary
contains regional job `run.googleapis.com/stderr`, Cloud Run Admin Activity and
Data Access audit families, and Scheduler's execution-log family. It excludes
stdout, services, unrelated resource types, regions and log families. This
unavoidably permits other stderr text and other methods/severities within those
bounded families; payload fields and severity are not supported view predicates.
The collector's `entries:list` query additionally requires the runtime schema,
exact legacy/v1/v2 RunJob methods or Scheduler errors before retrieving entries.
Those query checks select observations; they are not an IAM boundary.

RunJob uses `DATA_WRITE` in the [current Run audit
contract](https://docs.cloud.google.com/run/docs/audit-logging#googlecloudrunv2Jobs).
The protected observation setup enables `run.googleapis.com` DATA_WRITE capture
without exempted members. This is required for a job that fails before Python
can emit stderr. It grants the observer no additional permissions and does not
enable Run DATA_READ. Keep the native route until real shadow evidence includes
these cold-start audit observations. The legacy Admin Activity family remains
in the bounded view for historical/internal `/Jobs.RunJob` entries.

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

The production entry point is `scripts/operational_reconcile.py`, called by the
existing main-only notification workflow every five minutes. Collection remains
opt-in through `SHARED_DATASETS_CLOUD_OBSERVATIONS_ENABLED=true`, after installing
the reviewed observer resources and configuring the two identity variables below.
The rollout defaults to shadow; GitHub incident escalation and recovery continue
while cloud-only facts cannot acquire a public delivery claim. Dormant unified
delivery is present in this same tested protocol, so activation does not replace
the code whose 24-hour shadow history was reviewed.

The collector also exposes a transaction helper for offline callers:

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

The production workflow uses separate trusted phases: `collect(...)` returns
a proposal without persistence or heartbeat; `evaluated_collection(proposal,
policy_result)` returns exact checkpoint/evaluation bytes for signing. After
persistence, attestation and delivery verification,
`confirm_heartbeat(expected, verified_durable, delivery_verified=True,
heartbeat=reader.heartbeat)` compares those exact bytes and refuses unresolved
gaps. Its metric sample retains the cycle's original timestamp, so replaying old
verified evidence cannot refresh health.

`cloud_observation_ledger.py` persists the evaluated bundle in bounded compressed
parts and a final manifest using nonproduction GitHub deployment records. Each
part and manifest requires exactly one applied status and the exact main
notification workflow's signed receipt. The manifest binds the actual worker
revision, policy and collector contract hashes, predecessor, coverage times,
ordered part identities and content hashes. An unsigned latest manifest blocks
progress; the reader never silently chooses an older cursor.

The workflow signs incident claims, delivers eligible GitHub changes, signs any
outcomes, and re-verifies incident state before saving a complete cycle. It then
signs that cycle and freshly re-reads all its durable bytes before emitting the
heartbeat. A failed scan, delivery, signature or persistence step cannot advance
coverage or freshen health. `public_delivery_count` in shadow evidence counts
cloud operational delivery; GitHub incidents retain their existing independent
accounting. Local proposal files and deployment write acknowledgements alone
are never heartbeat evidence.

`catalog/operational-slack-rollout.json` defaults to native routing with no
cutover or retirement evidence. A config merge alone cannot activate unified
delivery. The selector requires the latest exact signed successful protected
policy deployment, its complete managed policy scope, unchanged evidence-gate
protocol and reviewed immutable evidence. Failed, unsigned or incompatible newer
attempts cannot be replaced with an older successful receipt. The worker checks
the same activation before delivery, final persistence and heartbeat.

Cloud action episodes may inherit an active deployment owner only through the
same authoritative execution, immutable image and signed deployment proof.
Payload owner names and similar job names confer no ownership. The inherited
record retains the original activity and fixed deadline; it cannot create a
fresh two-hour window. Recovery remains specific to the affected target.

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
operational receipt/manifest get, and time-series create. It receives no project-wide
log viewer, dataset write/list, execution start, secret-read, or infrastructure
mutation permissions. Receipt IAM is restricted to registered asset `runs/` JSON
objects. Separate conditional bindings of the same `storage.objects.get` role
permit only filenames ending in `/{asset_slug}.manifest.json` inside that asset's
canonical `releases/` prefix. Every branch retains the explicit object resource
type and its own prefix/suffix conjunction; dataset row files and metadata
sidecars do not match. The client accepts only exact translation notices, dated
run receipts, or known dated canonical manifest URIs. It never enumerates the
bucket. Run recovery reads the
generation-pinned receipt with bounded bytes and validates its asset, release
and successful publication state; a healthy execution alone proves no recovery.
Cloud Storage's `objects.get` permission permits the contents of those scoped
operational receipts and manifest metadata documents. Google IAM does not constrain time-series create to
one metric; the observer client emits only the fixed heartbeat metric.

The 25 registered assets occupy five receipt and five manifest IAM bindings.
Each condition has at most six asset branches, using at most 12 logical
operators; ten bindings remain below the limit of 20 different conditions for
one principal and role. These are [fixed IAM
limits](https://docs.cloud.google.com/iam/quotas). Exact indexed targets remain
in the protected setup allowlist, with deletion blocked. Catalog growth beyond
the reviewed five chunks fails the release contract and requires an explicit
scope update instead of silently omitting an asset or broadening its boundary.

For an unresolved translation episode, the scheduled reconciler reads only its
already known canonical manifest URI: current object metadata followed by the
exact observed generation's bounded document bytes. A newer generation proving
a complete maintained-locale inventory with valid counts and every locale below
the existing one-percent maintenance threshold can resolve that translation episode.
Final verification re-reads current metadata and refuses stale generations.
This proves translation recovery only; delivery receipts do not establish locale
coverage, dataset publication recovery, or permission to resend a notification.

An existing signed owner of the exact same manifest issue can cover its verified
canonical observation without changing the responsible human, originating chat,
activity, clarification wait, or original deadline. Observation alone never creates
ownership. An authenticated supervision request may also name an already retained
canonical binding: its revision is the manifest's actual SHA256 and its PR is
unset. It cannot invent a PR association or grant deployment or retry authority.

The protected cron policy workflow runs bootstrap, exact allowlisted observation
setup, then policy sync. Setup blocks deletion/replacement and probes the exact
saved plan's resource operations, including the exact Run DATA_WRITE audit
configuration under existing protected project IAM authority. New
Terraform-identity grants are limited to
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
The immutable cutover document names `audit_setup_deployment_id`: the signed
successful protected observation setup containing the Run audit resource and
the exact current observation Terraform boundary. Every eligible shadow sample
must follow that setup receipt and installation of the current absence policy.
Later no-op setup runs do not restart this clock. At cutover the gate reads
only `auditConfigs` through the protected identity's existing get-IAM authority
and rejects missing Run DATA_WRITE capture, Run exemptions or relevant
`allServices` exemptions. The observer receives no project IAM read permission.
Synthetic tests use offline transports or an explicitly isolated destination.
Independent security and pipeline-health delivery remain available throughout
cutover and rollback. No routine shadow outcome is posted to the shared channel.

The reviewed document records an existing signed shadow day, with exact cycle
IDs and manifest hashes. Those review references do not expire while the PR is
reviewed and tested. At apply time the gate completely enumerates the cycle
inventory, verifies its actual latest signed record, and follows at most 400
predecessors to prove a fresh rolling 24-hour window under the same reviewed
collector and policy hashes and main ancestry. Every cycle in that current
window needs complete observation and provenance, continuous coverage, zero
cloud public deliveries, and an observed heartbeat after the audit setup and
current absence-policy installation. A failed, unsigned, stale or incompatible
latest record cannot be replaced by the historical reviewed day. The runner's
proof records both the reviewed tail and the current verified window IDs/hash;
heartbeat inventory and eligibility time are read after signature enumeration,
so a cycle completing during verification is compared with the actual clock.
It rechecks wall-clock freshness immediately before applying the saved plan.

`runtime_deployments` names four signed deployment source anchors with exact
reviewed runtime hashes. An anchor may come from native-mode bridge staging.
The gate resolves the latest signed successful attempt for each authoritative
current job image, requires its source to descend from that anchor on tested
main ancestry with the same runtime producer/bridge hash, and requires its
source configuration to select unified mode with the same cutover reference.
It checks the current job and every active execution page for that immutable
image and unified environment. The proof retains anchor/current deployment IDs,
latest status IDs, source revisions and image digests. A failed or unsigned
newer deployment attempt blocks the gate instead of reviving an older success.

After the reviewed unified configuration merges, stage all four protected job
deployments while native Monitoring delivery remains installed. The first policy
sync may correctly refuse until these deployments and old-image drain finish.
CI's MPAtlas deployment depends on policy sync, so the owning agent must dispatch
`mpatlas-monthly-deploy.yml` on main with the exact tested `executor_sha`,
`source_run_id` and `source_run_attempt` for passing `ci-ready` evidence, then
wait for its terminal receipt. Re-dispatch `cron-alert-policy-sync.yml` with those
same inputs once all four current receipts and the fresh shadow window verify.
This uses the existing protected dispatch path and preserves the CI dependency.
Configuration alone leaves the observer in shadow until the signed successful
policy apply exists; no native channel is removed on a refused gate.

Runtime webhook secret retirement is a later reviewed change after that signed
policy activation. Its immutable document binds the original cutover hash and
may name fresh runtime source anchors; current compatible receipts and live
drain are resolved again. It cannot be combined with initial channel removal.
A subsequent policy sync with no channel or secret-access removal retains the
original signed activation, fresh heartbeat and live routing/view/audit checks,
without requiring historical runtime image pins. Routine compatible redeploys
therefore do not block policy maintenance or later secret retirement. No step
replays publication, adopts a legacy webhook parent, or resends an uncertain
notification.
