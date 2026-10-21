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
| Synchronous EAMLIS/sea-ice/MPAtlas deployment canary or WDPA upload preflight | GitHub |
| Cloud Scheduler dispatch or unmarked Cloud Run execution | Monitoring Slack |
| Five-minute WDPA execution observer or its Scheduler dispatch | Monitoring Slack only after persistent loss of successful completions |
| Detached WDPA worker canary | Monitoring Slack |
| Scheduled GitHub maintenance or automatic catalog/CDN follow-up | GitHub and Slack |
| Explicit notification transport rehearsal | Separate configured test channel only |

The Cloud Run policy retains the project/region-wide failure condition and
`protoPayload.status.code>0`, including OOM/system failures. The frequent WDPA
observer uses the health policy described below. Its only supervision
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

## Observer recovery window

`wdpa-execution-observer` runs every five minutes. Its execution and Scheduler
dispatch failures are excluded by exact job name from the immediate failure
policies and covered by `wdpa_execution_observer_unhealthy`. Other jobs retain
immediate failure coverage, including jobs added later.

The health policy sums `run.googleapis.com/job/completed_execution_count` with
`result="succeeded"` over a rolling 15-minute window. It notifies only when that
sum stays below one for two minutes. Cloud Run metric ingestion and alert
evaluation add latency. A later successful completion clears the condition;
healthy runs and brief failures send no Slack messages. Notification prompts
remain opening-only.

Missing metric data counts as unhealthy. The completion metric also emits zero
samples, so metric absence alone would not establish absence of successful runs.
Repeated startup failures and a stopped Scheduler are both covered without
relying on Python starting or an application log being emitted. Verify a recent
successful metric sample after policy installation; never infer health from
failure logs being absent.

The grace period relies on the observer's five-minute cadence. Daily and monthly
publishers currently have no Cloud Run task retries and need their own recovery
and delivery deadlines before adopting delayed notifications. Do not add retries
or suppress their failure alerts to emulate observer recovery.

Before declaring the observer policy live, inspect its success selector, rolling
window and missing-data behavior, replay historical metrics covering healthy
operation and a brief failure, and verify the protected sync deployed the new
policy together with both exact-job exclusions. Do not deliberately interrupt
the production observer to test an outage notification.

## Execution and delivery verification

WDPA's ten-minute worker watch retains Cloud Monitoring coverage.
Do not suppress it based solely on its creator, branch, or workflow trigger.
Routine deployments do not deliberately fail production jobs to test alerts.
`notification-rehearsal.yml` exercises the Slack API in a separate channel
using `SHARED_DATASETS_SLACK_TEST_CHANNEL_ID` and the dedicated
`SHARED_DATASETS_SLACK_TEST_BOT_TOKEN` secret. It requires the configured production
`SHARED_DATASETS_SLACK_CHANNEL_ID` for comparison and refuses unset destinations,
that channel, and the repository's known shared channel. It has no cloud identity
or execution-start permissions. Its retained permalink proves isolated transport
delivery; it does not prove production Monitoring detection or end-to-end routing.

Verify production detection by replaying deployed filters against authoritative
historical execution/audit evidence and inspecting complete live policy inventory.
The old production failure-injection workflow, runtime override and probe
verifier remain removed. Use genuine incident delivery evidence when available;
otherwise report production delivery as unverified.

The pytest session installs an audit hook before test collection to prohibit real
Slack HTTP/DNS transport and production webhook-secret subprocess resolution.
Python children inherit that hook through a test-only `sitecustomize` path.
Injected offline transports remain available for real HTTP construction tests.
This is a guard for trusted tests, not an operating-system sandbox.

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
4. Exercise notification transport only with the isolated-channel rehearsal.
   Inspect historical production delivery separately; synthetic shared-channel
   messages are not a rollout prerequisite.
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
probe stays quiet only when it completed successfully and same-attempt evidence
proves the publisher was skipped. Recognized read transport and credential-server
outages share a 30-minute budget of at most eight attempts. Authorization,
configuration, malformed snapshots and unknown failures fail immediately;
exhausted outages become actionable. A skipped publisher is never recovery proof.
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

The protected sync owns every declared Monitoring policy, including canonical
object deletion. The former schema-change policy is retained as a disabled,
channel-free resource and declaratively imports
`projects/shared-datasets-1/alertPolicies/10109535963709095250`. Its historical
incidents remain available. The reviewed protected sync must complete before
that live route is considered retired; removing its Terraform declaration alone
does not disable a live policy.

Compare the complete live inventory against the planned values exported from
the reviewed saved plan:

```bash
uv run python scripts/monitoring_routing_audit.py \
  --terraform-json /path/to/reviewed-plan.json --live
```

This command uses authenticated Monitoring GETs and follows every page. It
reports missing policies, disabled notification channels, enabled-state drift,
condition/filter drift, channel drift, and unexpected enabled Slack-bound
policies. It refuses an incomplete plan or inventory and exits nonzero on
findings. The expected plan must include the independently enabled object-write
and deletion protections and the exact disabled schema policy. It never changes
unknown policies. A discrepancy is evidence for review, not permission to turn
off detection. For offline replay, replace `--live` with `--policies-json` and
`--channels-json` containing complete Monitoring inventories.

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
