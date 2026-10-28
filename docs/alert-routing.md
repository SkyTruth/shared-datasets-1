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
The hourly deployment-completion workflow verifies only pending deployment
records; it does not launch a per-dataset observer while WDPA is idle.

The former `wdpa-execution-observer` schedule is permanently paused and
`wdpa_execution_observer_unhealthy` is disabled. Their definitions, narrow IAM
and old status endpoint remain for an update-only rollout: the existing
protected deployment paths block resource deletion. The catalog no longer
reads or polls that endpoint. Do not resume the observer in routine deployment.
Its exact-name exclusions do not exclude `wdpa-monthly` or any other worker.

When both changes deploy, the main CI graph disables the health policy before
the WDPA deployment pauses the schedule. Verify the live policy is disabled
and the schedule is `PAUSED` before declaring the retirement deployed. A
separately dispatched WDPA deployment must follow the same order. A later
infrastructure teardown needs a reviewed, constrained deletion path.

## Editable Monitoring messages

Monitoring sends events through Pub/Sub to the authenticated `monitoring-slack`
Cloud Run service. The existing Slack bot posts one parent per native incident;
closure edits that exact parent to **Closed by Monitoring**, with no recovery
reply or new parent. Duplicate events are quiet, closure is permanent for that
incident ID, and an out-of-order open cannot turn the message red again.
Google's original Slack app messages cannot be adopted by a different bot.

Messages include impact, mutation state, owner and required action. Monitoring
cannot establish consumer impact or mutation outcome, so both remain explicitly
unknown, including after closure. `jonaraphael` owns triage while the operation
owner is unknown. The action directs triage to the linked incident and affected
execution; closure still requires separate recovery verification. Existing
Monitoring parents receive these fields on their next closure update; duplicate
open events remain quiet. Receipt state and delivery safeguards are unchanged.

The retired WDPA metric policy retains `OPENED` and `CLOSED` for its existing
incident history. Google supports only
`OPENED` for log-based policies ([API contract](https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.alertPolicies#AlertStrategy)).
Those alerts are bot-owned too. After investigating recovery, the owner can use
**Alerts / Legacy workflow resolution**, supplying only `monitoring_incident_id`.
That protected, owner-only main workflow reads Monitoring once, requires its
exact incident to be `CLOSED`, and edits the recorded parent. It does not poll,
accept arbitrary Slack timestamps, create parents, or treat log expiry as proof
of dataset/deployment recovery. Keep genuine identity decisions and consumer
breaking-change notifications intact.

Receipts live at
`gs://skytruth-shared-datasets-1-monitoring-slack/incidents/<incident-id>.json`.
Generation preconditions arbitrate delivery claims; receipts bind the bot user,
workspace, channel and string message timestamp. A claimed post with no confirmed
timestamp remains an error. Inspect the service's `Monitoring Slack receipt` log
and the original Slack parent; an owner must repair that exact receipt with its
current generation before retrying. Never delete a claim or repost to escape an
uncertain delivery. Pub/Sub retains failed deliveries for seven days. Inspect
subscription backlog and receiver errors during incident recovery.

The existing protected cron policy sync provisions private resources, loads
only exact CI-tested image bytes, and copies the existing GitHub bot secret to
Secret Manager through stdin. The token never enters Terraform state. The bot
must authenticate and verify its private receipt permissions before the service
starts listening. Terraform waits for that service, authenticated subscription
and Monitoring publisher grant before changing any of the six policy routes;
startup failure leaves the existing native routes in place. Native channel
configuration is retained for reviewed rollback. No alert conditions, grace
windows, publication authority or dataset permissions are relaxed.

## Execution and delivery verification

WDPA's ten-minute worker watch retains Cloud Monitoring coverage.
Do not suppress it based solely on its creator, branch, or workflow trigger.
Verify delivery using an actual failed execution's Monitoring incident, transport
receipt and matching Slack message. Record the exact execution and incident IDs
with that evidence. Policy configuration and local tests alone do not prove live
delivery; report it as unverified when matching failure evidence is unavailable.
Do not deliberately fail production jobs to test routing.

The unapproved-writer policy retains the dormant WDPA observer exception only for the exact
`_catalog/wdpa-monthly-execution.json` object. It grants no permissions and does
not suppress other writes or delete alerts. Its protected sync watches
`canonical_mutation_iam.tf` and `wdpa_execution_observer.tf` as policy inputs.

## Deployment verification

Use a reviewed PR, merge to `main`, and the protected cron alert policy sync.
Review its saved plan: only the six alert policies and the explicitly allowlisted
Monitoring transport resources may change. Ingestion jobs, Scheduler
configuration, canonical bucket permissions and dataset objects must not.
The workflow changes take effect on subsequent executions; existing executions
do not gain the marker retroactively.

Before declaring live routing verified:

1. Inspect the deployed policy filters and notification channels. Confirm the
   configured Terraform account matches the creator of a synchronous canary.
2. Read terminal `/Jobs.RunJob` audit entries from a scheduled execution, a
   synchronous workflow execution, and a detached WDPA execution. Verify the
   actual response fields above. Replay the deployed filter against those logs:
   only the trusted marked synchronous failure should be excluded. A filter
   matching no historical failures is not proof of correctness.
3. Confirm a synchronous command's failure fails its GitHub step. Check that
   scheduled executions have no supervision marker in their execution template.
4. For an actual unattended failure, confirm its Monitoring incident, transport
   receipt and matching Slack notification. Record delivery as unverified if no
   matching evidence exists. Delivery evidence does not prove supervision
   suppression.
5. Verify the observer's expected status write no longer matches; writes by a
   different principal or to a different canonical object must still match.
6. Check the first scheduled maintenance and automatic follow-up failures route
   through `unattended-workflow-alert.yml`. That privileged workflow checks out
   only `main`, reads event JSON as data, and never downloads source-run artifacts.

Successful/skipped upstream outcomes are filtered before allocating the legacy
failure worker. When incident tracking is enabled, its separate reconciliation
worker also inspects successful completions for matching recovery evidence;
routine healthy completions produce no Slack messages. CI now includes reusable dataset publication, catalog refresh and
deployment jobs. For a failed main-push CI run, the alert worker verifies the
workflow identity and exact current run attempt, then checks its actual jobs.
Publication failures and failed deployment jobs after passing `ci-ready` are
announced; ordinary PR and validation-only
failures stay in GitHub. Failed terminal deployment verification and explicit
read-only reconciliation also remain visible in Slack. A scheduled catalog input
probe stays in Actions when same-attempt job evidence proves the publisher was
skipped and no other job failed. Actual catalog deployment failures still alert.
Every subscribed source
workflow is checked against its authoritative API identity; completion events
superseded by a newer attempt become no-ops. Repository announcements are
selected from every commit in the complete main-push range, using existing CI
detection, before allocating their worker. Dataset publication refreshes catalog data without
redeploying the catalog viewer service.

Local tests exercise routing decisions, real workflow shell failure propagation,
marker placement, policy boundaries and protected-sync coverage. They do not
verify Google Cloud's audit payloads or Slack delivery; record those separately.

## Monitoring configuration

Prefer an existing Cloud Monitoring Slack notification channel. Include its
resource name in the local review plan with `cron_alert_notification_channels`;
the protected `cron-alert-policy-sync.yml` applies reviewed changes after merge.
Alternatively, Terraform can create a channel from `cron_alert_slack_channel_name`
and sensitive `cron_alert_slack_auth_token`, which can enter Terraform state.

The alert-policy deployer needs both `logging.notificationRules.create` and
`logging.notificationRules.delete`: updating a log-based policy replaces its
internal notification rule. Repair missing permissions through the existing
protected bootstrap and policy-sync workflows before declaring routing deployed.

## Dataset and repository notifications

The approved publisher sends a new-dataset upload summary only when the canonical
`latest/` object did not exist before publication; it derives this from the plan's
`destination_generation`. `dataset_alerts.py upload-summary` posts only with
`--new-dataset`; existing-asset refreshes print a local skip message.
Announcements remain operational notifications, not commit gates, as specified
in [AGENTS.md](../AGENTS.md#non-negotiable-rules).

Canonical vector/table publication validates schema compatibility before writes.
Schema snapshots live under `_catalog/schema-snapshots/`; the approved publisher
advances them after compatible or waived publication. A schema delta emits a
structured Cloud Logging diagnostic. Schema-change Slack monitoring stays quiet;
consumer-impacting changes use the reviewed `breaking_changes` plan contract in
[dataset plans](../.github/dataset-plans/README.md#consumer-impact).

Repository functionality notices use fenced commit messages under the
[repo-alert workflow](../.claude/skills/repo-alert-commit-messages/SKILL.md).
GitHub webhook notifications use the `SHARED_DATASETS_SLACK_WEBHOOK_URL` Actions
secret. Runtime FYI notifications use the Secret Manager secret
`shared-datasets-slack-webhook-url` by default. To set or rotate it, supply the
webhook bytes through a local file:

```bash
gcloud secrets versions add shared-datasets-slack-webhook-url \
  --project=shared-datasets-1 \
  --data-file=/path/to/webhook-url.txt
```

This webhook configuration is separate from the bot settings and delivery
reconciliation owned by [Slack incidents](slack-incidents.md).
