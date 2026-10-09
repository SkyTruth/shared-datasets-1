# Operational alert routing

Slack reports unattended failures. A supervised execution can be quiet only
when its GitHub workflow waits for its result and reports failure itself.

GitHub incident recovery follows the [Slack incident lifecycle](slack-incidents.md):
verified recovery edits the original parent to **Resolved** and creates no reply
or new channel message. That document owns configuration and reconciliation;
the existing failure-only webhook remains active until incident mode is enabled.

| Execution | Failure reporting |
| --- | --- |
| PR CI, build checks, manual GitHub checks | GitHub |
| Synchronous EAMLIS/sea-ice deployment canary or WDPA upload preflight | GitHub |
| Cloud Scheduler dispatch or unmarked Cloud Run execution | Monitoring Slack |
| Detached WDPA canary, validation, or runtime inspection | Monitoring Slack |
| Scheduled GitHub maintenance or automatic catalog/CDN follow-up | GitHub and Slack |
| Explicit monitoring delivery test | Monitoring Slack |

The Cloud Run policy retains the project/region-wide failure condition and
`protoPayload.status.code>0`, including OOM/system failures. Its only supervision
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

WDPA's ten-minute watch and detached validation retain Cloud Monitoring coverage.
Do not suppress them based solely on their creator, branch, or workflow trigger.
Routine deployments no longer require deliberate failure probes. The explicit
`cron-alert-delivery-test.yml` retains both existing fail-before-write modes and
refuses to start while the selected job has an active execution. Its `start`
action verifies the terminal failure, exact execution override and controlled
pre-write log marker; it records Monitoring delivery as **pending**. A failed
count alone cannot satisfy the negative control: OOM, IAM or image failures
must still fail verification.

After seeing the matching Monitoring Slack message, dispatch `verify-delivery`
with the same job, exact execution name and message permalink. That action
rechecks the execution and negative control, then records explicit delivery
confirmation. The permalink is maintainer confirmation of the matching message,
not an automated read of Slack. The two runs retain their evidence separately;
a successful `start` run alone does not establish alert delivery. The probe
execution is never retried automatically, and log ingestion polling is read-only.

The unapproved-writer policy exempts the WDPA observer only for the exact
`_catalog/wdpa-monthly-execution.json` object. It grants no permissions and does
not suppress other writes or delete alerts. Its protected sync watches
`canonical_mutation_iam.tf` and `wdpa_execution_observer.tf` as policy inputs.

## Deployment verification

Use a reviewed PR, merge to `main`, and the protected cron alert policy sync.
Review its saved plan: only the allowlisted alert policies should change; job
images, Scheduler configuration, bucket permissions and dataset objects must not.
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
4. Dispatch the explicit delivery test's `start` action when no execution is
   active. Confirm its controlled terminal failure and matching Slack
   notification, then run `verify-delivery` with that execution and permalink.
   This checks the unattended path; it does not prove supervision suppression.
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
read-only reconciliation also remain visible in Slack. Every subscribed source
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
