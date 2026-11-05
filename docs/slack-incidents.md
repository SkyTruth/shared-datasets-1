# Slack incident lifecycle

Follow [CI/deployment recovery](ci-recovery.md) for the ordered response. This
reference owns incident identity, persistence, verification and Slack configuration.

Operational failures share a stable `SD-` plus twelve hex characters incident ID.
The first failure creates an **Open** parent; further attempts update its count and
evidence. Verified recovery edits that parent green, without recovery parents or
automatic replies. Triage (`jonaraphael`) assigns an owner and checks the exact
attempt and mutation records. Unknown impact remains unknown.

## Admission and cadence

`unattended-workflow-alert.yml` receives failed native completions. Healthy events
allocate no worker except the terminal observer, which also reconciles recovery.
The three-hour safety probe discovers GITHUB_TOKEN-dispatched failures, including
notification batches, for which GitHub emits no `workflow_run` event. Its rolling
24-hour replay window dispatches only actionable transitions. Three actual
consecutive reconciliation dispatches fail health; unchanged health findings never
enter replay or that counter. The [health/heartbeat contract](alert-routing.md#scheduled-freshness-and-capacity)
owns scoped non-red reports, deduplication, reminders and missing-run detection.
Infrastructure failures report and request completion verification without checkout.

Each safety probe cheaply inspects the exact pending WDPA execution for terminal
status before completion-observer backoff. Pending verification backs off at most
three hours after an unchanged successful observer; failed checks stay eligible
and active observers are not duplicated. This live hint only requests protected
verification; it cannot authorize a terminal result. Freshness/hold/pause checks and
72-hour expiry warnings follow [alert routing](alert-routing.md#scheduled-freshness-and-capacity).
Failed reads or evidence never certify idle. Catalog refresh rechecks live inputs
and exact passing main CI; pending CI defers it. A failed input probe with a proven
skipped publisher stays in Actions; publisher failures alert.

WIF observers retain `shared-datasets-production` and their service accounts with
normal environment references. GitHub documents [deployment opt-out](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/control-deployments#using-environments-without-deployments)
and [environment OIDC claims](https://docs.github.com/en/actions/reference/security/oidc#custom-claims-provided-by-github)
but does not explicitly confirm their combination. Signed receipts remain separate
from automatic deployment records. Batches bind the exact successful safety discovery
attempt/revision and cannot acknowledge sends or authorize mutation. Later main
consumers accept ancestral successful hints only after rechecking live inputs.

## Recovery contract

Scopes identify exact deployment targets, workflow/jobs or publication PR/phases.
Job paths come from the immutable source `head_sha` caller graph via YAML `git show`,
never executed source code. IAM bootstrap and dependent sync remain distinct;
unknown jobs keep exact scopes. Missing history fails visibly; use full history.

Deployment recovery needs the latest signed terminal record, exact tested artifact
and executor, ancestry covering every failure and later completion. Runtime targets
require `verified`; other registered targets allow `applied`/`verified`. Newer
pending/failed attempts block older healthy evidence. Skips or validation success
cannot recover deployments. Workflow/startup recovery requires the same workflow;
job recovery requires the same job. Historical completion-worker recovery instead
requires its exact signed failed-worker proof and newer signed success for that
runtime target. Legacy resolution uses the same verifier. Invalid signatures,
unknown outcomes and mixed unresolved failures retain alerts.

SDK `candidate`/retired WDPA `readiness` require the actual read-only verification
step. Migration verifies all historical failures and skipped mutating siblings while
preserving ID, parent, claims and history. A completed publication with failed PR
receipt uses protected owner-only recovery with original PR/run/attempt, tested main
and rehearsal false. It rechecks immutable authority/completion and repairs only
comment/deduplication state, without Slack/GCP mutation. Proof never covers another
PR/attempt or reset.

## Persistence and delivery

Append-only signed deployment checkpoints use task `slack-incident`, environment
`slack-incidents`, `production_environment=false`, `auto_merge=false`, empty required
contexts and `auto_inactive=false`. They bind exact main revision/run/attempt and
are bookkeeping, not production authority or undeletable audit storage. Notification
batches may have several subjects; production retains its single-subject policy.

Persist/sign claims before non-idempotent posts. Acknowledge/sign valid channel and
string timestamp responses even if a later send fails; timeouts, invalid responses
and HTTP 200 without `ok:true` never acknowledge. Pace posts; acknowledged updates
may repeat. Permalink retries reuse the parent timestamp. Changed views remain
pending until acknowledged; new episodes wait for the original green update.

Never blindly resend or replace uncertain claims through webhooks. Retain new
observations without blocking other scopes. Claims cannot create idle snapshots;
unchanged claims fail health after 24 hours from the latest signed checkpoint.
Owner inspection is required; recovery does not imply confirmed delivery.

Verify v1/v2/v3 signatures before v4 migration; rollback retains those readers.
Retired v3 IDs must precede retirement and contain complete incident cores. Reject
unknown/merged scopes, active empty registrations and omitted failures. Preserve
parents/replies/claims; clear old acknowledgements/fingerprints. Before incompatible
writers pause admission, drain without cancelling sends, merge and resume within
24 hours. Activation changes do not drain workers; longer gaps need reviewed replay.

## Incremental verification

Snapshots require authenticated successful same-main producers, identical policy/
Slack identity, unchanged generations and live identical API transcripts. Pending
executions/claims cannot create them. Artifacts bind exact producer, successful
job/upload, creation interval, archive digest and sole bounded member; export only
verified receipts. Terminal fingerprints cover complete status history and verifier.
Missing proof invokes full verification; malformed evidence/failed reads fail visibly.
Caches grant no authority; final signing/reconciliation use fresh checks. Only
repository/workflow identities cache within an invocation. GET transport/5xx and
recognized attestation 503 failures retry at most three times after 5 then 15 seconds
with identical bytes/signer. Authentication, authorization, rate limits and invalid
proofs fail immediately; this mechanism never retries writes or uncertain sends.

## Configure and reconcile

Install/invite a bot with `chat:write`; no history/search/public-post scope is needed.
Set secret `SHARED_DATASETS_SLACK_BOT_TOKEN`, exact channel variable
`SHARED_DATASETS_SLACK_CHANNEL_ID`, UTC activation `SHARED_DATASETS_SLACK_INCIDENTS_SINCE`
and finally `SHARED_DATASETS_SLACK_INCIDENTS_ENABLED=true`. Never log/commit tokens
or put them in arguments. Retain the incoming webhook for disabled mode and safety.
Enabled mode fails on missing configuration; transport never silently changes.
Workspace/bot/channel changes require registry migration. Observe real recovery;
never fail production or post synthetic production messages to test delivery.

For an uncertain send, inspect Slack and the latest signed checkpoint/first claimed
operation. Owner-only main dispatch confirms `delivered` with the exact matching
string timestamp, or `not-delivered` with empty timestamp only after proving absence.
Derive timestamps from permalinks by inserting a decimal before the final six digits;
never use floats or another incident's timestamp. Confirm each operation separately.
A recovered undelivered parent retires quietly; old replies are never replaced.
Workers recheck proof and retained observations without retrying production.

Unsigned interrupted checkpoints use reviewed `seal-checkpoint`, incident ID and
`checkpoint:RECORD_ID:PAYLOAD_SHA256` over canonical bytes. Recheck historical
recovery/status IDs, signatures and ancestry; sign a new checkpoint without sending.
Claims still require inspection. Catch-up uses `replay-window`, incident
`workflow-bot-cutover`, `replay:START..END` and no timestamp. Enumerate closed past
UTC windows daily, at most seven days; never rerun source workflows.

For legacy webhook closure, owner main dispatch supplies original failed run ID
and inspected parent timestamp. Every failure requires registered signed recovery
or exact historical worker proof; runtime targets require `verified`. Update only
that parent, without replacement/reply/activation/mutation. Managed parents retain
normal signed closure. Native incidents supply only `monitoring_incident_id` after
[authoritative closure](alert-routing.md#editable-monitoring-messages).
Feature decisions follow [their typed lifecycle](alert-routing.md#identity-decision-threads):
preserve requests and reply in the original thread without broadcast. Acceptance
cannot establish publication, execution or Monitoring recovery.
