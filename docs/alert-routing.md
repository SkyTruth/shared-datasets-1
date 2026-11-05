# Operational alert routing

Use [CI/deployment recovery](ci-recovery.md) for ordered incident handling.
`workflow_failure_alert.WORKFLOW_EVENTS` owns producer/event routing for alerts
and replay. Supervised runs stay quiet only while GitHub reports their failures.

GitHub incident recovery follows the [Slack incident lifecycle](slack-incidents.md):
additional failed attempts update the parent without automatic replies;
verified recovery edits the original parent to **Resolved** and creates no reply
or new channel message. That document owns configuration and reconciliation;
the bot is the sole workflow incident writer. The incoming webhook remains for
safety findings and operational summaries.

| Execution | Failure reporting |
| --- | --- |
| PR CI, build checks, manual GitHub checks | GitHub |
| Synchronous EAMLIS/sea-ice/MPAtlas/dataset-usage deployment canary | GitHub |
| Cloud Scheduler dispatch or unmarked Cloud Run execution | Monitoring Slack |
| Detached WDPA worker canary | Monitoring Slack |
| Scheduled GitHub maintenance or automatic SDK/catalog/CDN follow-up | GitHub and Slack |
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
The creator must equal the registered runtime service account. The marker is
`SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL`; its value links the owning GitHub run.
Matching the name avoids incorrectly pairing values from different env entries.
Marker presence is the contract: an empty or false-looking value is not an opt-out.
Missing metadata or a different creator leaves the event alertable.

Only callers that supervise terminal completion may set the marker. This
includes `gcloud run jobs execute --wait` and the shared executor's captured
async launch followed by polling beyond the short job's task timeout. It is
never a persistent job setting. Detached WDPA uses no marker because its initial
watch can end before the task. A caller that loses connectivity, times out or
is cancelled must fail GitHub and leave the execution for inspection; successful
workflows cannot abandon marked executions or swallow their failure.

## WDPA execution-driven monitoring

WDPA retains Cloud Run execution/Scheduler failure coverage, generation-pinned
check-ins and on-demand completion verification. Idle jobs need no separate observer.
The retired `wdpa-execution-observer` has no alert/writer exception; retain historical
objects/receipts and verify teardown through the protected retirement contract.

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
Monitoring publisher grant before switching the configured policy routes. Startup
failure leaves native routes intact; retained channel config supports reviewed
rollback. No failure conditions, grace windows or publication authority change.

## Identity decision threads

Private `identity-decision-events` bind project/execution/asset/release, full hashes
and case sets. Validate configured publisher/asset/job pairs and generation-claim
every post/reply. Local diagnostics never post. Private operation receipts retain
approved decisions and `identity/events/<event-id>.json` links.

`feature-identity-review-ready-v1` attaches generation/size/checksum-bound WDPA
review evidence verified by the authenticated catalog. Freeze origin/pinned links;
late events cannot downgrade review, and historical events stay readable. See
[review evidence](wdpa-identity-review.md#review-from-the-alert).

After curation merges, the protected reconciler verifies exact accepted final-head
PR bytes and runtime-legal choices. Preserve the original **Decision needed** body,
cases, context and links; record choice, decision maker (`jonaraphael`), rationale
and reviewed PR in its original thread without broadcast. Only complete case coverage
permits **Decision recorded**; partial decisions stay open. Status is separate.
Frozen events can restore shortened parents without another decision reply.

Shared/historical parents settle only on matching execution/asset/release identity.
Approval cannot establish Monitoring closure or publication/execution recovery;
late duplicates cannot reopen parents. Wait for protected transport sync, freeze
complete cases before rendering and reuse confirmed timestamps after interruptions.
Uncertain claims require owner inspection of the receipt log and original thread
before generation-safe repair. Existing `chat:write` suffices. Reviewed
`.github/identity-decision-adoptions/` may bind inspected legacy parents/replies
without duplication; they grant bookkeeping only and preserve delivery safeguards.

## Execution and delivery verification

Use reviewed protected cron sync and inspect its saved allowlist plan. Compare live
filters/channels with scheduled, synchronous and detached audit logs. Only the exact
trusted marked synchronous failure may be excluded; missing metadata stays alertable.
Waiting failures must fail GitHub, and persistent templates must lack the marker.
Existing executions gain no marker retroactively. Match real incidents to transport
receipts and Slack messages; local tests do not prove live delivery/suppression.
Never fail production deliberately to test routing. See [incident admission and
recovery](slack-incidents.md) for polling, signing and uncertain-delivery contracts.

## Scheduled freshness and capacity

Every three-hour safety probe reads generation-pinned release indexes and each
known ingestion Scheduler job. Daily assets need a successful release or meaningful
unchanged-source check-in within two days; monthly assets within 45 days (monthly jobs run on day 2;
WDPA checks days 1–10, allowing 31 days plus source/retry grace). Missing-source/decision skips do not reset data
freshness. An old publication with a recent confirmed unchanged-source skip is
healthy. Metadata-only index rewrites cannot reset the check-in date. The weekly
bucket audit uses the same freshness rule. The safety health report also requires
a successful main completion of both `bucket-hygiene-audit.yml` and
`scratch-cleanup-audit.yml` within nine days (weekly cadence plus two days of grace).
Missing/failed audits cannot reset that clock; API failures remain visible.
An ingestion target's live image differing from its latest signed, accepted main
deployment for over 72 hours raises a finding unless its rollout hold is active.
Failed reads/unverified records fail health. Pre-start failures remain covered by
workflow failures and weekly audit freshness.

A Scheduler state other than `ENABLED` for at least 72 hours since
`userUpdateTime` fails health unless `.github/scheduler-pauses.json` contains a
reviewed CODEOWNER, reason and future timezone-aware `expires_at`. Deployment
holds in `.github/ingestion-rollout-holds.json` use those same fields and do not
authorize Scheduler pauses. Missing jobs/failed reads fail visibly. Approved
pauses suppress absence checks until expiry, including the internal dataset-usage
collector's pre-activation pause while `gcs_usage.verified_at` is null. Holds/pauses
warn at least 72 hours before expiry and alert afterward. Expired holds still block
deployment, allowing CI selection and `ci-ready`; lint rejects malformed entries
and changed holds that are already expired.

Disabled incidents or a missing activation timestamp/bot token skip incident
discovery and appear in the health report; catalog and execution checks continue.
Setup/probe failures and health-read exceptions share one retained report with
change-plus-daily reminders. The health reporter runs without installed probe
dependencies or the pinned verifier. Native setup/probe failures remain red;
an authenticated report suppresses duplicate replay. Failed delivery, upload or
dispatch remains visible. Identical findings cannot advance stall escalation.

Before relying on the checks, use the protected monitoring sync to provision
the workflow identities and `google_project_iam_custom_role.operational_safety_reader`.
Only `shared-datasets-heartbeat` receives its `logging.logEntries.create` grant;
its separate read role permits the safety probes, including heartbeat series reads. The publisher cannot emit
heartbeats. The resources scope provisions these identities before cron-sync adds
`google_logging_metric.operational_safety_heartbeat`
and `google_monitoring_alert_policy.operational_safety_absent`.

A completed health read writes the authenticated `operational-safety-heartbeat`
log only after successful health-report delivery (or its deduplicated no-send).
A webhook failure emits no heartbeat. Its metric feeds an independent absence policy
with a seven-hour (`25200s`) window, covering missed/disabled GitHub safety runs.
Health queries the metric's recent time series and warns when none exists;
a log write alone cannot establish absence detection before the first series.
The policy follows `cron_alerts_enabled` and the configured cron notification
channels. A health-read failure remains in the deduplicated report and emits no
success heartbeat. Local tests cannot prove deployed IAM or alert delivery.

Read-only Monitoring evidence (2026-10-06 through 2026-11-05) bounded the highest
occupied utilization histogram bucket, multiplied by live 4-vCPU/16-GiB limits:

| Job | Samples | CPU peak upper bound | Memory peak upper bound | Proposed CPU / memory |
| --- | ---: | ---: | ---: | --- |
| EAMLIS | 28 | 2.60 vCPU | 0.96 GiB | 4 / 4 GiB |
| MPAtlas | 40 | 2.32 vCPU | 3.36 GiB | 4 / 8 GiB |
| Sea ice | 35 | 0.92 vCPU | 0.16 GiB | 2 / 512 MiB |

Memory retains twice observed headroom; EAMLIS/MPAtlas keep at least 1.5 times
observed CPU peaks. Samples cannot bound future growth; compare peaks after
protected deployment. Evidence collection mutated no production resources.

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
checks plan, PR snapshot and schema; body edits remain covered. Its read-only
WIF route accepts only that exact workflow at a numeric PR merge ref; the
trusted-base workflow gates same-repository PRs before requesting credentials.

[Repository notices](../.claude/skills/repo-alert-commit-messages/SKILL.md) use
fenced commit messages. GitHub webhooks use Actions secret
`SHARED_DATASETS_SLACK_WEBHOOK_URL`; runtime FYIs use Secret Manager
`shared-datasets-slack-webhook-url`. Rotate through `gcloud secrets versions add`
with project `shared-datasets-1` and `--data-file` pointing to a protected local
file, never token bytes in arguments. Bot configuration/reconciliation belongs to
[Slack incidents](slack-incidents.md).
