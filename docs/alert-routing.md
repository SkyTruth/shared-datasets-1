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
| EAMLIS or MPAtlas source-quality hold | One persisted decision-alert attempt; delivery failure retains the hold and existing job-failure coverage |

The [EAMLIS source-quality guard](eamlis-source-quality.md) pauses automatic
publication when more than 5% of baseline feature IDs are lost and retains exact
candidate bytes for staging review. MPAtlas uses the same lifecycle and persisted
decision flow for an empty source or more lost published zone IDs than its limit
permits (5% by default). Additions never offset losses in either job. New loss
holds use `objects_lost`; Slack requests name the lost count, published count and
limit with the asset-specific review command. Older `geometry_count_drop` holds
retain their reason and remain reviewable. [MPAtlas hold review](../ingestion/mpatlas_monthly/README.md#review-a-held-release)
uses the shared preview workflow and immutable approval PR. A held run with a
confirmed notification exits zero. `sending`/`unknown` notification state makes
each later scheduled check fail without reposting, using existing job-failure
coverage until reviewed promotion clears the hold. Investigate with `status`,
inspect Slack, and follow `prepare-plan` → immutable reviewed PR → protected
`resolve` for a retained candidate. Scheduler checks continue. A hold is cleared
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
**Closed by Monitoring** as a warning after authoritative closure. Duplicate/out-of-order opens
cannot reopen it. Other apps' historical messages cannot be adopted. Closure
requires separate verification of the affected execution or resource outcome; triage belongs to
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

Execution failures also reconcile automatically through **Alerts / Legacy workflow
resolution** on trusted main in `shared-datasets-production`, using the existing
reconciler identity. Ingestion verification and **Deployments / Completion check**
request a sweep only after signing a verified outcome; **Operations / Safety check**
requests one daily after its independent health report. No incident or deployment
IDs are accepted in automatic mode: the same verifier discovers the latest signed
proof once per managed job per sweep. The daily sweep covers Monitoring's one-hour
closure timer and a dispatch that never started; no runner waits for closure.
Request failures are tolerated, preserving the deployment and safety job outcomes;
the next daily request provides another opportunity.

Each stateless sweep lists `executions/` once with update times (listing may
paginate), then reads at most 200 parents, newest first. It evaluates at most 100
failures, makes at most 100 Monitoring GETs and 400 direct GitHub GETs, and attempts
at most 25 parent edits. Failure/edit budget exhaustion defers remaining work
quietly. Parents outside the 200-parent window remain available to the owner path.
At most two attestation-verifier invocations per managed job are additional to
the direct GET budget. Discovery/application uses at most 325 receipt reads
(200 parents, 100 aliases, 25 generation-checked reporter reloads), each up to two
GCS metadata/download RPCs; listing pages depend on historical object count.

Open or expired (Monitoring 404) incidents, older/nonterminal evidence and pending
identity requests wait quietly; jobs outside managed ingestion targets are ignored.
Every new failure must pass the shared verifier. A resolved receipt acknowledges
wording drift or interrupted delivery using its first recorded proof, without
Monitoring/GitHub reads; new failures never replace that proof. A failure the owner closes
with Monitoring-only reconciliation is not re-verified, so beside a recorded proof
it renders resolved. Acknowledged views
make no Slack call. Corruption, other read errors, generation conflicts and Slack
errors fail the resolver through the existing workflow-health observer, without
retriggering it. Only the `automatic` job is unattended: a failed owner-dispatched
resolver job stays in GitHub, and only a successful `automatic` job resolves a
resolver incident. Legacy/nonexecution parents and recovery on a later scheduled
execution without a redeploy are outside this proof type. The manual owner path
still rejects unmanaged jobs and unavailable incidents.

To resolve an execution failure after a verified replacement, supply both
`monitoring_incident_id` and `recovery_deployment_id` to that owner-only workflow.
The incident must be `CLOSED`, match its original project/job/execution receipt,
and have a later deployment for the same managed ingestion job. The shared
terminal verifier requires the latest signed `verified` outcome; a pending,
failed, applied-only, unsigned, older or unrelated deployment cannot resolve it.
The command edits the original parent green with the original error and recovery
link. Unresolved identity requests remain visible and prevent a green parent.
Deploy the updated Monitoring Slack receiver before using this option: recovered
execution receipts use `execution-slack-v2`; the reader retains v1 support, and a
rollback must retain the v2 reader once a recovery is recorded. A push or merge
alone never establishes recovery. No resource, data or Monitoring state is mutated.

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

The daily operational safety report also checks [dataset stewardship](standards/dataset-stewardship.md).
Active assets require a named contact. Manual, annual and other cadences without
daily/monthly freshness automation require an explicit review deadline. An
overdue review names the steward; it does not claim that unchanged historical
data has become invalid. Regenerating metadata cannot extend a deadline.

Every daily (16:17 UTC, after the 15:00 UTC sea-ice publish) probe reads generation-pinned release indexes and known Scheduler jobs. Daily assets need a successful release or meaningful unchanged-source check within two days;
monthly assets within 38 days (31-day cadence plus seven days of grace). Monthly jobs run on day 2; WDPA checks days 1–10. An old release with a recent confirmed unchanged check is healthy.
Missing-source/decision skips and metadata-only rewrites cannot reset freshness. The weekly bucket audit uses the same rule.
Both `bucket-hygiene-audit.yml` and `scratch-cleanup-audit.yml` require authenticated successful main completion within nine days (weekly cadence plus two days); missing/failed audits cannot reset that clock.
API failures and unverified records remain findings. Pre-start failures remain covered by workflow failures and weekly audit freshness.

An ingestion target trailing its latest signed accepted main image for over 72 hours raises a finding unless its rollout hold is active.
Recovery readiness requires signed successful manual identity provisioning; missing/failed provisioning or the latest recovery authentication failure remains a health finding. These reads grant no IAM authority.

Scheduler states other than `ENABLED` for >=72 hours since `userUpdateTime` fail health unless `.github/scheduler-pauses.json` names a reviewed CODEOWNER, reason and future timezone-aware `expires_at`.
Deployment holds in `.github/ingestion-rollout-holds.json` require the same fields but cannot authorize pauses. Missing jobs/failed reads fail visibly.
Approved pauses suppress freshness until expiry, including telemetry's pre-activation pause while `gcs_usage.verified_at` is null. Daily probes start warning at 96 hours remaining, guaranteeing >=72 hours before expiry under on-time sampling and alert afterward.
Expired holds still block deployment while permitting selection and `ci-ready`; lint rejects malformed entries and changed holds that are already expired.

Disabled incidents or missing activation timestamp/bot token skip incident discovery; catalog/execution checks continue and health reports include configuration findings.
Setup, probe and reader failures share one retained report with change-plus-daily reminders.
When previously notified findings clear, one success post names
`health:operational-safety-check` and links the resolving run. Clean runs afterward
stay quiet. Safety uses an incoming webhook, which returns no message timestamp,
so resolution is a top-level post rather than a threaded reply; old cached reports
need no migration. Delivery failures fail the run before retaining the transition. Health reporting works without probe dependencies or the pinned verifier.
A first-attempt setup failure directly after an authenticated successful safety run sends no Slack message: the run stays red, logs the cause as a warning annotation and carries the previous report forward, so a second consecutive failure or a re-run that fails is a new finding and alerts. Probe and reader failures alert at once. The quiet run skips health readers, so other new findings and due reminders wait one run (24 h). A previous success older than 25 hours cannot qualify for quiet setup handling, so a missing run followed by a setup failure alerts.
The safety job restores only the checksum-verified `gh` verifier archive from a main-scoped Actions cache. Python and uv still download from GitHub releases, because `setup-python` and `setup-uv` trust tool-cache hits without verification and this job holds production credentials; release-download 504s on 2026-11-08..09 failed 3 of 194 hourly runs.
Setup/probe failures stay red; authenticated reports suppress duplicate replay.
If the direct safety-failure webhook succeeds, the incident bot does not create
another incident for failures preceding that delivery, even when timeout prevented
health-report upload. A missing or failed webhook, or a later cleanup failure,
still receives normal incident-bot coverage. Reconciliation dispatch counts do not
raise health findings. Failed batches retain normal incident replay; unconfirmed
Slack delivery claims older than 24 hours still require owner reconciliation.

Protected monitoring sync provisions workflow identities and `google_project_iam_custom_role.operational_safety_reader` before cron-sync adds
`google_logging_metric.operational_safety_heartbeat` and `google_monitoring_alert_policy.operational_safety_absent`.
Only `shared-datasets-heartbeat` gets `logging.logEntries.create`; its separate read role permits probes and time-series reads. The publisher cannot emit heartbeats.

After successful discovery and health-report delivery (or a deduplicated no-send), the authenticated `operational-safety-heartbeat` log confirms reporting is alive.
Reader failures remain in the delivered health report; they still permit that heartbeat. Failed discovery or report delivery emits none.
The independent PromQL absence policy checks the last 49 hours (`176400s`): two 24-hour intervals plus one hour of scheduling grace. One missed daily run stays quiet; two consecutive missed runs alert within 49 hours 5 minutes of the last heartbeat, plus metric ingestion and notification delay (300-second evaluation interval). Setup failures on two on-time runs alert on the second run, within 48 hours of the last success plus run time. Poll-only findings normally wait at most 24 hours; the quiet setup path can extend that to 48 hours. GitHub scheduling itself has no hard delivery bound. Failure-triggered workflow alerts remain immediate. The policy uses cron notification channels and follows `cron_alerts_enabled`. The temporary first-heartbeat activation switch has been removed after verified heartbeats on 2026-11-10 and 2026-11-11 (runs 42924597145 and 42964580893).

Standard [metric-absence conditions](https://docs.cloud.google.com/monitoring/alerts/metric-absence) stop at 23.5 hours, so the policy uses [PromQL](https://docs.cloud.google.com/monitoring/alerts/using-promql) `absent_over_time(...[49h])` with a zero retest window and a 300-second evaluation interval. It selects the system `logging.googleapis.com/log_entry_count` metric for `log="operational-safety-heartbeat"` on `global`; user-defined log metrics [cannot alert beyond 25 hours](https://docs.cloud.google.com/monitoring/promql/promql-in-alerting). The protected Cron alert policy sync enables absence detection following verified heartbeat ingestion. Health-series inspection uses the same 49-hour window. Reports and prior-run discovery retain seven days; automatic replay covers 72 hours (three daily intervals), preserving overlap after a missed or quiet setup run. Longer outages need reviewed replay. Reconciliation stall discovery retains three days to find the previous two daily dispatches; three consecutive dispatches still escalate, now across 48 hours.
Health warns when no recent metric series exists: a log write alone cannot prove metric ingestion. Local tests cannot prove deployed IAM or alert delivery.

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
