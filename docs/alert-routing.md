# Operational alert routing

Use [CI/deployment recovery](ci-recovery.md) for ordered incident handling.
This reference owns alert selection, delivery paths and health policy.
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
| Synchronous EAMLIS/sea-ice/MPAtlas/dataset-usage deployment canary | GitHub |
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
bucket audit uses the same freshness rule.

A Scheduler state other than `ENABLED` for at least 72 hours since
`userUpdateTime` fails health unless `.github/scheduler-pauses.json` contains a
reviewed CODEOWNER, reason and future timezone-aware `expires_at` for that
paused job. Deployment rollout holds in `.github/ingestion-rollout-holds.json`
use the same `owner`, `reason`, timezone-qualified `expires_at` fields and a
nonempty CODEOWNER/reason; they do not authorize Scheduler pauses. Missing
jobs and failed reads fail visibly. Pauses suppress the associated absence checks
only through that expiry. The paused internal dataset-usage collector is checked
even though it has no catalog release index. Its registry entry records the deliberate
pre-activation pause while `gcs_usage.verified_at` remains null. Holds and pauses
warn at least 72 hours before expiry and alert after expiry; expired holds continue
blocking deployments without failing CI selection or `ci-ready`. Lint rejects malformed
holds and changed holds that are already expired.

Health findings and external health-read exceptions produce a scoped report
without making the workflow red when reporting succeeds. The report deduplicates
findings and sends on change plus daily reminders; identical findings neither
dispatch incident replay nor count toward its three-dispatch escalation. Report
upload/notification and infrastructure failures remain visible workflow failures.

Before relying on the checks, use the protected monitoring sync to provision
`google_project_iam_custom_role.operational_safety_reader` and its publisher
binding. The role grants exactly `cloudscheduler.jobs.get`, `run.executions.get`
and `logging.logEntries.create`; the safety job retains its existing service
account and protected environment. The bootstrap scope includes both IAM
resources; the cron-sync scope adds `google_logging_metric.operational_safety_heartbeat`
and `google_monitoring_alert_policy.operational_safety_absent`.

A completed health read writes the authenticated `operational-safety-heartbeat`
log. Its log-based metric feeds an independent Cloud Monitoring absence policy
with a seven-hour (`25200s`) window, covering missed/disabled GitHub safety runs.
Verify the first heartbeat has established its time series before relying on
absence detection; a policy with no initial series cannot prove the job is alive.
The policy follows `cron_alerts_enabled` and the configured cron notification
channels. A health-read failure remains in the deduplicated report and emits no
success heartbeat. Local tests cannot prove deployed IAM or alert delivery.

Read-only Monitoring evidence (2026-10-06 through 2026-11-05) bounded the highest
occupied utilization histogram bucket, multiplied by live 4-vCPU/16-GiB limits:

| Job | Samples | CPU peak upper bound | Memory peak upper bound | Proposed CPU / memory |
| --- | ---: | ---: | ---: | --- |
| EAMLIS | 28 | 2.60 vCPU | 0.96 GiB | 6 / 4 GiB |
| MPAtlas | 40 | 2.32 vCPU | 3.36 GiB | 6 / 8 GiB |
| Sea ice | 35 | 0.92 vCPU | 0.16 GiB | 2 / 512 MiB |

Limits retain twice observed headroom; EAMLIS/MPAtlas CPU increased, without CPU
cost savings. [Cloud Run](https://docs.cloud.google.com/run/docs/configuring/jobs/cpu)
requires 4 GiB for 6 vCPU. Samples cannot bound future growth; compare peaks after
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
checks plan, PR snapshot and schema; body edits remain covered.

[Repository notices](../.claude/skills/repo-alert-commit-messages/SKILL.md) use
fenced commit messages. GitHub webhooks use Actions secret
`SHARED_DATASETS_SLACK_WEBHOOK_URL`; runtime FYIs use Secret Manager
`shared-datasets-slack-webhook-url`. Rotate through `gcloud secrets versions add`
with project `shared-datasets-1` and `--data-file` pointing to a protected local
file, never token bytes in arguments. Bot configuration/reconciliation belongs to
[Slack incidents](slack-incidents.md).
