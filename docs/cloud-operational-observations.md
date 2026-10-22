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

`scripts/cloud_operational_observations.py` scans eight family views across the
global `_Default` and `_Required` Logging buckets. It covers all regional Cloud
Run jobs and Scheduler failures, including newly added jobs. Each pass scans up
to 24 hours, overlaps the previous checkpoint by ten minutes, and leaves a
two-minute ingestion lag. Delayed ingestion beyond that overlap is a remaining
coverage limitation; shadow evidence must assess it before cutover.

Each view uses only [supported flexible-view
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

The four family selectors live in `catalog/operational-log-views.json`. Terraform
creates a separate view for each family in each bucket: eight views and eight
scoped accessors. IDs are `shared-datasets-operations-` followed by `run-stderr`,
`run-activity`, `run-data-access` or `scheduler-executions`. Each filter is one
flat conjunction of project, region, resource type and one log ID. The collector
reads their exact union; cross-family resource/log combinations remain excluded.

Protected setup rejected the previous OR-of-AND filter with HTTP 400 even though
its parenthesis depth was one. Parenthesis counting does not establish service
acceptance. The lint gate now evaluates the actual Terraform constructor using
the pinned offline console and validates its serialized eight-view payload.
Offline scope tests reject missing views, Boolean branches, cross-project,
cross-region, cross-resource and unrelated-family combinations. Protected
creation and exact live filter/IAM readback are still required before collection;
offline checks do not claim that the service has accepted a filter.

RunJob uses `DATA_WRITE` in the [current Run audit
contract](https://docs.cloud.google.com/run/docs/audit-logging#googlecloudrunv2Jobs).
The protected observation setup enables `run.googleapis.com` DATA_WRITE capture
without exempted members. This is required for a job that fails before Python
can emit stderr. It grants the observer no additional permissions and does not
enable Run DATA_READ. Keep the native route until real shadow evidence includes
these cold-start audit observations. The legacy Admin Activity family remains
in the bounded view for historical/internal `/Jobs.RunJob` entries.

RunJob audit records prove the API request outcome, separately from an execution
outcome. A present empty protobuf `status: {}` or explicit code `0`, without
contradictory error details, is an accepted request fact. It creates no incident
or recovery and grants no retry, supervision or publication authority. Missing,
malformed or contradictory status is not acceptance. Response names, request
counts and nearby timestamps cannot establish which execution was created.

Each snapshot independently lists every page of executions for all six currently
registered jobs, with `showDeleted=true`, using the existing read-only metadata
role. The registered workflow-to-target graph owns this set. All returned rows
are validated and counted before an inventory digest is saved; any incomplete or
conflicting inventory aborts the snapshot. An empty complete inventory proves
only the API visibility observed at that time, never request completion or
nonexecution. Unknown jobs seen in logs remain coverage gaps.

Execution facts have exact API resource and UID evidence rather than invented log
identities. To bound routine healthy evidence, the snapshot materializes every
active execution regardless of creation date, failures completed within the
covered window, and each job's latest successful terminal execution at or before
the cutoff. An older-created execution that failed inside the window is included.
Execution-failure facts observed through logs and the API retain one attempt
identity in the job episode. A domain decision does not explain an independently
failed execution's unknown mutation outcome; both action requirements remain
visible. A blocked identity decision normally returns a successful execution and
therefore creates no additional execution-failure action.
Successful observer executions prove only observer health. Other healthy job
executions still require the existing generation-pinned publication or domain
proof before resolving a publication, identity or translation episode.

Every page and its authoritative execution/receipt metadata must be read before
the candidate checkpoint is complete. Safe read retries share the operation's
single budget and discard a partial snapshot before restarting enumeration.
The deduplication key binds the authenticated log name and insert ID. Reusing
that identity with different log bytes is an error. A replay gap beyond the
bounded horizon is an explicit failure, never permission to skip history.

The v2 checkpoint contains complete per-job execution inventory counts and digests,
`covered_through`, bounded `seen` source identities,
pending facts, pending execution watches, and coverage evidence. Pending
executions are checked again even when no new log event arrives. A terminal
transition adds a distinct evidence identity; a running observation does not
establish recovery.

The production entry point is `scripts/operational_reconcile.py`, called by the
existing main-only notification workflow every five minutes. Workflow-level
concurrency coalesces only redundant, not-started scheduled ticks; each source
event and supervision request retains a distinct workflow group. The existing
job-level `queue: max` still serializes every signed write and Slack send, with
active cancellation disabled at both levels. A replaced tick advances no
checkpoint; the next complete pass resumes durable coverage and reconsiders
absolute episode clocks. This reduces tick backlog without establishing a live
latency guarantee; old queued workers still need to drain. Collection remains
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
`CycleLedger.save` requires the caller's installed-policy `before_commit` fence
after predecessor verification and before the final manifest write. Partial
parts remain audit evidence without advancing coverage when that fence fails.

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

The installed protocol file set is defined once in
`scripts/operational_protocol.py`. Activation and CI deployment selection share
that set, including the collector, adapter, routing verifier and registry itself.
Changing any member requires a fresh protected policy receipt. The broader cycle
contract also hashes every installed-protocol member, so an older shadow window
cannot qualify a changed request or execution interpretation. Collector-only
repairs select policy sync without launching dataset jobs; cutover configuration
and runtime changes retain their broader deployment dependencies.

Native mode also requires a supported signed installed policy receipt. An older
native worker cannot bypass that check after a newer unified policy is installed.
The writer repeats the check after predecessor verification and after part
writes, immediately before the coverage manifest; heartbeat emission checks again
after durable signature verification. Only identical freshly enumerated record
and status bytes can reuse in-process signature verification. Missing, changed,
unsigned, failed or incompatible latest policy evidence pauses cloud coverage and
health. GitHub incident handling remains independent while cloud collection is off.

Cloud action episodes may inherit an active deployment owner only through the
same authoritative execution, immutable image and signed deployment proof.
Payload owner names and similar job names confer no ownership. The inherited
record retains the original activity and fixed deadline; it cannot create a
fresh two-hour window. Recovery remains specific to the affected target.

Within one CLI phase, repeated observer records may reuse an already verified
image-only auxiliary receipt. The phase owns this in-memory proof context; a
later phase starts fresh. Every recovery still fetches and compares the exact
execution, and publication/translation receipt reads remain current. Saved
record, status, revision or artifact differences require their own exact proof;
missing provenance and failed verification are never memoized. This prevents
routine healthy records from repeatedly verifying the same immutable image
without granting execution or dataset recovery authority.

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

The protected setup verifies Logging permissions at their supported resources.
Logging's view permission-test API rejects bucket lookup, view creation and view
listing permissions. The setup therefore reads each exact bucket's metadata and
enumerates all pages of view names, and tests view get/update/IAM permissions on
the existing view. Before view creation, the same readiness and saved-plan helper
also verifies the active custom role's exact seven permissions and the live v3
project binding's exact protected principal and two-bucket condition. A project
permission test cannot substitute for that bucket-conditioned grant.

For this check only, the protected authentication step supplies a masked access
token with `cloud-platform` and `userinfo.email` scopes. Google token introspection
must return the configured service account's verified email and its authoritative
IAM unique ID as audience. Audience alone also occurs on delegated user tokens;
missing email or another principal fails closed. The token is sent only in an
HTTPS POST body and never appears in a URL or diagnostic. Other deployment probes
retain their existing token path, and no IAM grant is added. This is evidence of
the installed allow grant and successful metadata reads, not a guarantee against
independent deny policies or later authorization changes. The actual protected
Terraform operation still enforces authorization. See Google's
[Logging view creation contract](https://docs.cloud.google.com/logging/docs/reference/v2/rest/v2/projects.locations.buckets.views/create),
[resource-based conditions](https://docs.cloud.google.com/iam/docs/configuring-resource-based-access)
and [token identities](https://docs.cloud.google.com/docs/authentication/token-types).

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
configuration under existing protected project IAM authority. Log-view setup
remains limited to two buckets; metric setup permits descriptor create/get/list.
Observer runtime permissions remain execution metadata reads, scoped operational
object reads and the fixed heartbeat write.

The protected setup in CI run `39646971194/1` at revision
`34374accba86b8978e770ef29643427fe37835bb` stopped before plan/claim/apply because
the Terraform identity lacked provider create/get authority. The proposed repair adds a permanent
get-only provider metadata role and a separate create-only project role bound
only to the protected Terraform identity. The creator binding has the fixed
condition `request.time < timestamp('2026-10-22T02:00:00Z')`; it does not renew
itself. **This proposal requires explicit human approval before merge/apply.**
It grants creation across existing project pools during that window, rather
than an IAM-enforced provider-ID restriction. The protected main workflow's
exact saved-plan allowlist still permits only the existing `github` pool's
`github-operational-observer` provider. No provider update/delete, pool mutation,
pool policy setter, or observer runtime mutation permission is added.

Google checks [provider creation on its parent pool](https://docs.cloud.google.com/iam/docs/reference/rest/v1/projects.locations.workloadIdentityPools.providers/create)
and [provider reads on the child](https://docs.cloud.google.com/iam/docs/reference/rest/v1/projects.locations.workloadIdentityPools.providers/get).
IAM pools/providers are absent from the supported
[resource-name condition types](https://docs.cloud.google.com/iam/docs/conditions-resource-attributes),
so the proposal does not use a misleading `resource.name` condition. A pool-local
grant would first require pool get/set-IAM-policy authority, which this deployer
lacks; it would add policy mutation authority solely to bootstrap creation.
The temporary project create-only grant avoids that additional write class.

Readiness derives the canonical pool name from authoritative project/pool
metadata, then GETs the exact child. Only a structured `404 NOT_FOUND` enters
initial-creation checks: strict scoped-token identity, exact installed reader
and expiring creator roles/bindings, and the supported parent create-permission
test. Other errors remain failures. Existing providers need only their real
metadata GET after expiry; a saved plan requests create authority only for an
actual create. Updates/deletes require a new separately reviewed authority plan.
Installed allow-grant proof does not claim to defeat independent deny policies
or authorize another target, and it is never treated as proof of deployment.

An independent live audit found that the existing `github-actions-readonly`
provider still allows only `Catalog drift guard` and `Bucket hygiene audit`,
while Terraform also declares `Dataset breaking change alert`. A generation-pinned
backend read confirmed this exact provider is already managed (state serial 759,
generation 1792616426547848). No failed consumer run or earlier drift date is
inferred from that snapshot. A separate proposed protected repair adds a
project-wide update-only role for the Terraform identity with a new fixed
`2026-10-22T06:00:00Z` expiry (02:00 EDT). **It requires separate explicit human approval
before merge/apply; approval of the observer create grant does not cover it.**
The exact two-resource bootstrap precedes a one-resource trust sync. A strict
saved-plan guard accepts only the old two-workflow condition becoming the
declared three-workflow condition, with no other field changes, replacement,
creation or deletion. Readiness GETs the real child, verifies its unchanged
issuer/mapping/configuration and exact installed update-only allow grant; it
does not call a nonexistent provider permission-test method. A later verified
no-op does not require update permission after expiry.

The new UPDATE window is a concrete proposal, not an extension authorized by the
earlier approval ending at 02:00 UTC. Main CI `39706344866/1` failed its validation
gate before any protected IAM caller ran; neither updater resource was created.
The unchanged two-resource bootstrap therefore still creates the role and
binding, with no installed conditional binding to replace. The observer CREATE
grant remains capped at `2026-10-22T02:00:00Z`, and the permanent metadata reader
is unchanged. Old 02:00 UTC UPDATE-grant evidence cannot satisfy the new proposal's
exact installed-grant proof. The source guards, target allowlists and sole
permitted trust-condition change are unchanged.

Successful main5 shadow cycles may seed the independent heartbeat after its
protected setup and absence policy are ready. They remain audit evidence for
that protocol. The final trust-repair protocol must establish its own complete
24-hour qualification window after compatible protected setup/policy; earlier
cycles do not start or satisfy that final cutover clock.

## Heartbeat and cutover evidence

The independent native absence policy opens one notification after 30 minutes
without a successful reconciliation heartbeat. A complete scan, successful
policy evaluation and acknowledged durable persistence precede emission.
Unresolved provenance gaps are recorded but do not emit this heartbeat.

The current series is
`custom.googleapis.com/shared_datasets/operational_reconciliation_heartbeat_v2`.
The original descriptor remains managed with destruction blocked, but its samples
cannot satisfy the current absence policy. This separates old unfenced workers
from current health without changing series between shadow and unified mode.
Before collection is enabled, the final protected observation setup must create
the v2 descriptor and the final policy sync must install its absence filter and
the matching fenced worker protocol. An earlier native setup or heartbeat is not
proof of this installation. No heartbeat is fabricated for migration.

Metric absence detection requires a first sample after policy creation or
update. The first successful shadow reconciliation must seed the metric only
after the protected policy sync completes; retain evidence of both times and
confirm Monitoring received the sample. See [Google's absence-policy
contract](https://docs.cloud.google.com/monitoring/alerts/metric-absence).

Before removing direct operational Slack routes, require 24 continuous hours of
successful shadow reconciliation and verified heartbeat coverage, with no
unresolved scan/persistence/provenance gaps. Retain source identities, decisions,
checkpoint and policy timestamps, and the routing audit as operational evidence.
The fenced protocol requires a fresh 24-hour window; pre-fence cycles and samples
do not count. Queue delays and stale supervision check-ins remain evidence gaps
or expired ownership under their existing deadlines, never permission to relax
the rollout gate.
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
