# Operational alert routing

Slack reports unattended failures. A supervised execution can be quiet only
when its GitHub workflow waits for its result and reports failure itself.

GitHub incident recovery follows the [Slack incident lifecycle](slack-incidents.md):
additional failed attempts update the parent without automatic replies;
verified recovery edits the original parent to **Resolved** and creates no reply
or new channel message. That document owns configuration and reconciliation;
the existing failure-only webhook remains active until incident mode is enabled.

| Execution | Failure reporting |
| --- | --- |
| PR CI, build checks, manual GitHub checks | GitHub |
| Synchronous EAMLIS/sea-ice/MPAtlas deployment canary or WDPA upload preflight | GitHub |
| Cloud Scheduler dispatch or unmarked Cloud Run execution | Monitoring Slack |
| Detached WDPA worker canary | Monitoring Slack |
| Scheduled GitHub maintenance or automatic catalog/CDN follow-up | GitHub and Slack |
| Explicit monitoring delivery test | Monitoring Slack |
| EAMLIS source-quality hold | One persisted decision-alert attempt; delivery failure retains the hold and existing job-failure coverage |

The [EAMLIS source-quality guard](eamlis-source-quality.md) pauses automatic
publication after a greater-than-5% geometry-record loss and retains exact
candidate bytes for staging review. Scheduler checks continue. A hold is cleared
only after the immutable approved candidate is published and verified; Slack
delivery or a healthy subsequent download cannot clear it.

The Cloud Run policy retains the project/region-wide failure condition and
`protoPayload.status.code>0`, including OOM/system failures. Its supervision
exception requires both the authenticated execution creator in
`protoPayload.response.metadata.annotations."run.googleapis.com/creator"` and the
execution marker name in `protoPayload.response.spec.template.spec.containers.env`.
The creator must equal the configured Terraform deployer. The marker is
`SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL`; its value links the owning GitHub run.
Matching the name avoids incorrectly pairing values from different env entries.
Marker presence is the contract: an empty or false-looking value is not an opt-out.
Missing metadata or a different creator leaves the event alertable.

Only `gcloud run jobs execute --wait --update-env-vars=...` callers set the marker.
It is never a persistent job setting and must not be used with `--async`, a
bounded watch window, or a caller that swallows the failure. If the waiting
command loses connectivity, times out, or its runner is cancelled, GitHub reports
an unsuccessful workflow; the execution may still need manual inspection. A
successful workflow is not permitted to abandon a marked execution.

## WDPA execution-driven monitoring

WDPA uses the same Cloud Run execution-failure and Scheduler dispatch-failure
policies as the other ingestion jobs. Successes and meaningful skips update
each asset's release index, which supplies the catalog's **Last check-in**.
An old check-in while no update is scheduled is not an observer outage.
The three-hour operational safety check discovers pending deployment records and
requests the existing completion workflow only when work is needed; it does not
launch a per-dataset observer while WDPA is idle.

The retired `wdpa-execution-observer` infrastructure has no alert/writer
exception and cannot be recreated by routine WDPA deployment. Its historical
status object and receipts remain; the catalog no longer polls that object.
Verify teardown through the protected retirement contract.

## Editable Monitoring messages

Monitoring sends events through Pub/Sub to private authenticated `monitoring-slack`.
Its bot creates one parent per native incident and edits that same parent to
**Closed by Monitoring** after authoritative closure. Duplicate/out-of-order opens
cannot reopen it. Other apps' historical messages cannot be adopted. Impact and
mutation outcome remain unknown, including after closure; triage belongs to
`jonaraphael` and assigns the operation owner.

Execution failures and identity reviews share a parent keyed by exact project,
job and execution, never by time. Failure-policy extraction uses
`protoPayload.response.metadata.name`; typed reviews carry the same execution key.
`executions/<context-hash>.json` claims the post first; incident receipts alias its
confirmed string timestamp. Either event order enriches the same parent.
The receiver reads at most twenty error entries, prefers escaped application
errors, and otherwise retains the audit reason. Failed reads remain queued.
The reader gains only `logging.logEntries.list`, no data writes. Historical
uncorrelated parents keep their original reconciliation paths.

Private receipts live under
`gs://skytruth-shared-datasets-1-monitoring-slack/incidents/<incident-id>.json`.
Generation preconditions arbitrate claims and bind workspace/bot/channel/timestamp.
An unconfirmed claim blocks every producer; inspect the `Monitoring Slack receipt`
log and original parent before owner repair of that exact generation. Never delete
claims or repost to escape uncertainty. Pub/Sub retains failures for seven days;
inspect backlog and receiver errors during recovery.

Log policies support only `OPENED` ([API contract](https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.alertPolicies#AlertStrategy)).
After investigating recovery, the owner may dispatch **Alerts / Legacy workflow
resolution** on main with only `monitoring_incident_id`. It requires that exact
incident to be `CLOSED` and updates its recorded parent; it neither polls nor
accepts arbitrary timestamps. Closure/log expiry cannot establish deployment or
publication recovery. Historical WDPA metric incidents retain their receipts.

The protected cron sync uses exact CI-tested image bytes, passes the bot secret
through stdin (never Terraform state), and validates bot auth/receipt permissions
before listening. Terraform waits for service, authenticated subscription and
Monitoring publisher grant before switching the five policy routes. Startup
failure leaves native routes intact; retained channel config supports reviewed
rollback. No failure conditions, grace windows or publication authority change.

## Identity decision threads

Typed events on private `identity-decision-events` bind project, execution, asset,
release, full captured hashes and case sets. Publishers must match configured
asset/job pairs. Generation preconditions claim every parent/reply before posting;
local diagnostics never post production messages. The private operations bucket
retains approved decisions and `identity/events/<event-id>.json` receipts/links.

`feature-identity-review-ready-v1` attaches generation/size/checksum-bound WDPA
review evidence to the same context. The authenticated catalog verifies the bundle
before rendering. Receipts freeze the configured origin and pinned links, so late
original events cannot downgrade the linked review. Old events/receipts remain
readable. See [WDPA review evidence](wdpa-identity-review.md#review-from-the-alert).

After curation merges, **Alerts / Identity decision reconciliation** verifies the
exact accepted final PR head and runtime-legal choices. It replies in the original
thread with choice, decision maker (`jonaraphael`), rationale and reviewed PR,
without channel broadcast. Every case must be covered before **Decision recorded**;
partial decisions leave other cases open. Preserve the original **Decision needed**
body, cases, context and links; status is separate. Older shortened bot parents can
be restored from their frozen event without another decision reply.

Shared execution parents and historical decision-policy parents settle only when
execution/asset/release identities match. Review acceptance establishes neither
native Monitoring closure nor publication/execution recovery; late duplicates
cannot reopen it. New workers wait for the selected protected transport sync.
Shared decision receipts freeze complete cases before execution rendering;
interrupted timestamp association reuses the already confirmed post.

Uncertain legacy parent/reply claims require owner inspection of the `Identity
decision Slack receipt` log and original thread before generation-safe repair.
Existing `chat:write` suffices; no history/search access is added. Reviewed
`.github/identity-decision-adoptions/` documents may bind inspected webhook parents
and confirmed replies without duplication, carrying notification bookkeeping
only. Legacy parents and delivery claims retain their existing safeguards.

## Execution and delivery verification

WDPA's bounded watch retains Monitoring coverage. Do not suppress failures solely
by creator, branch or trigger. Match a real failure's incident, transport receipt
and Slack message; report delivery unverified without that evidence. Never fail
production deliberately to test routing. The retired observer has no writer exception.

## Deployment verification

Use a reviewed PR and protected cron policy sync; inspect its saved allowlist plan.
Compare deployed filters/channels with actual scheduled, synchronous and detached
execution logs. Only the exact trusted marked synchronous failure may be excluded;
missing metadata stays alertable. Waiting-command failures must fail GitHub;
persistent templates must lack the supervision marker. Local tests do not prove
live delivery/suppression. Existing executions gain no marker retroactively.

Healthy events and recovery evidence are batched by the three-hour safety probe.
Failures of native monitored completions retain immediate coverage; GITHUB_TOKEN
follow-ups are also discovered by polling. Safety failures self-report without a
`workflow_run` subscription. See [incident admission, signed recovery and uncertain
delivery](slack-incidents.md) for the single authoritative lifecycle contract.
Local routing tests do not establish live audit-payload or Slack delivery behavior.

## Scheduled freshness and capacity

Every three-hour safety probe reads generation-pinned release indexes and each
known ingestion Scheduler job. Daily assets need a successful release or meaningful
unchanged-source check-in within two days; monthly assets within 62 days (twice the
maximum calendar-month interval). Missing-source/decision skips do not reset data
freshness. An old publication with a recent confirmed unchanged-source skip is
healthy. Metadata-only index rewrites cannot reset the check-in date. The weekly
bucket audit uses the same freshness rule.

A Scheduler state other than `ENABLED` for at least 72 hours since
`userUpdateTime` fails health unless `APPROVED_PAUSES` in
`scripts/operational_safety_check.py` contains a reviewed owner, reason and future
UTC `until`. Deployment rollout holds do not authorize Scheduler pauses. Missing
jobs and failed reads fail visibly. Pauses suppress the associated absence checks
only through that expiry. The paused internal dataset-usage collector is checked
even though it has no catalog release index.

Provision `google_project_iam_custom_role.operational_safety_reader` and its
publisher binding through the protected monitoring sync **before enabling these
checks**. It grants only `cloudscheduler.jobs.get`; the safety job retains its
existing service account and protected environment. The sync target/allowlist must
include both resources. No local production apply is permitted.

The cadence change reduces scheduled safety runs from 24 to 8/day and removes
24 skipped safety notification runs/day. At the baseline 148.5 runner-minutes for
69 safety runs, safety alone projects from 51.7 to 17.2 runner-minutes/day before
the additional small health reads. Healthy idle workers remain zero; incident
batches and completion checks add demand-dependent work. These are projections,
not measured post-deployment savings. Poll-only failures add <=3 h detection
latency, within the freshness bounds; three consecutive reconciliation demands
escalate by the third probe. Unchanged uncertain Slack claims escalate after 24 h.

Read-only Monitoring evidence (2026-10-06 through 2026-11-05) bounded the highest
occupied utilization histogram bucket, multiplied by live 4-vCPU/16-GiB limits:

| Job | Samples | CPU peak upper bound | Memory peak upper bound | Proposed CPU / memory |
| --- | ---: | ---: | ---: | --- |
| EAMLIS | 28 | 2.60 vCPU | 0.96 GiB | 6 / 4 GiB |
| MPAtlas | 40 | 2.32 vCPU | 3.36 GiB | 6 / 8 GiB |
| Sea ice | 35 | 0.92 vCPU | 0.16 GiB | 2 / 512 MiB |

All proposed limits exceed twice those observed bounds. The EAMLIS/MPAtlas CPU
increase is required by that headroom rule; it is not a CPU-cost saving.
[Cloud Run job limits](https://docs.cloud.google.com/run/docs/configuring/jobs/cpu)
require at least 4 GiB for 6 vCPU. The samples cannot bound unobserved spikes or
future source growth; compare new execution peaks after protected deployment.
These are Terraform-only changes; no job, Scheduler, IAM or dataset was mutated
while gathering this evidence.

## Monitoring configuration

Use an existing `cron_alert_notification_channels` resource or reviewed Terraform
channel inputs `cron_alert_slack_channel_name` / `cron_alert_slack_auth_token`
(the latter may enter state). Protected cron policy sync owns changes. Its
bootstrap requires both `logging.notificationRules.create` and `.delete` because
policy updates replace the internal notification rule; provision before syncing.

## Dataset and repository notifications

`dataset_alerts.py upload-summary --new-dataset` announces a new canonical latest
object according to the reviewed plan's `destination_generation`; refreshes skip
it. Announcements are operational notifications, never commit gates.
Schema snapshots under `_catalog/schema-snapshots/` advance only after compatible
or waived publication. Deltas log diagnostics; consumer advisories use the reviewed
[breaking-change contract](../.github/dataset-plans/README.md#consumer-impact).
The advisory runs only for PRs containing immutable dataset plans and independently
checks plan, PR snapshot and schema; body edits remain covered.

[Repository notices](../.claude/skills/repo-alert-commit-messages/SKILL.md) use
fenced commit messages. GitHub webhooks use Actions secret
`SHARED_DATASETS_SLACK_WEBHOOK_URL`; runtime FYIs use Secret Manager
`shared-datasets-slack-webhook-url`. Rotate through `gcloud secrets versions add`
with project `shared-datasets-1` and `--data-file` pointing to a protected local
file, never token bytes in arguments. Bot configuration/reconciliation belongs to
[Slack incidents](slack-incidents.md).
