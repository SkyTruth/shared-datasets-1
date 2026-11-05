# Slack incident lifecycle

Operational failures have one searchable incident ID (`SD-` plus twelve hex
characters). The first failure creates an **Open** parent. Further failed attempts
update its count and evidence; verified recovery edits that same parent green.
Healthy runs create no messages, recovery parents or automatic replies.

Triage (`jonaraphael`) assigns the operation owner and checks the exact failed
attempt and authoritative mutation records. Unknown impact stays unknown;
a successful workflow alone never establishes deployment/publication recovery.

## Admission and cadence

`unattended-workflow-alert.yml` receives failed native completions for monitored
crons and deployments. Healthy completions allocate no worker except the terminal
completion observer, which also reconciles recovery evidence. The monitored list
includes the notification worker itself: failures of GITHUB_TOKEN-dispatched
batches, completion checks and catalog deploys are discovered by the safety probe
even when GitHub emits no `workflow_run` event.

`Operations / Safety check` runs every three hours with a rolling 24-hour replay
window. It compares catalog inputs, discovers pending executions, and dispatches
an authenticated incident batch only for actionable transitions. Three consecutive
probes requesting reconciliation fail the health check. Unchanged successful
pending-execution probes back off for six hours; failed checks remain eligible
next time and active checks are not duplicated. Safety failures self-report to the
existing webhook; the safety workflow has no notification `workflow_run`
subscription. Delayed/missed completion events need no fixed parent wait.

[Freshness/pause checks](alert-routing.md#scheduled-freshness-and-capacity) bound
poll-only detection by three hours. Failed reads/evidence never certify idle.

Catalog refresh and completion have no separate timer. Catalog refresh rechecks
live inputs and exact passing main CI before publishing. Pending CI defers it to
the next probe. A failed input probe whose same-attempt publisher was provably
skipped stays in Actions; allocated publisher failures alert.

Safety and read-only observers retain `shared-datasets-production`, their existing
service accounts and `deployment: false`. Signed production and incident receipts
remain distinct from automatic environment deployment records. An authenticated
batch binds the exact safety attempt, revision and successful discovery step;
it cannot acknowledge a Slack send or authorize a production mutation. A later
main consumer accepts an ancestral successful probe hint and rechecks live inputs.

## Recovery contract

A scope identifies an exact deployment target, workflow/job, or publication PR and
phase. Registered job paths come from each verified source run's immutable
`head_sha` caller graph, read as YAML data with `git show`; source code is never
executed. IAM bootstrap and dependent sync remain distinct. Unknown paths keep
exact job scopes. Missing source history fails visibly; use a full-history checkout.

Deployment recovery requires the latest signed terminal record, tested executor
SHA/artifact, revision ancestry covering every retained failure, and a later
completion time. Runtime targets require `verified`; other targets accept their
registered `applied`/`verified` phases. A newer pending/failed deployment blocks
older healthy evidence. Skips and validation-only success cannot recover a deploy.

Workflow/startup failures recover only from the same trusted workflow; job failures
require the same exact job. Historical completion jobs may instead recover through
the original job's signed failed-worker proof and a newer signed runtime success
for that exact target. Legacy resolution uses the same verifier. Unknown outcomes,
invalid signatures and mixed unresolved failures retain their alerts.

SDK `candidate` and retired WDPA `readiness` are read-only job scopes. Recovery
requires their actual verification step to succeed. Existing candidate/readiness
incidents migrate only after verifying all historical failures and proving the
mutating sibling was skipped; ID, parent, claims and failure history survive.

### A completed publication with a failed PR receipt

Do not rerun a mutation whose promotion, metadata and index rebuild already
succeeded. The owner-only protected `Data / Approved mutation` receipt recovery
accepts the original `pr_number`, `receipt_recovery_run_id` and
`receipt_recovery_attempt` on tested main, with `authorization_rehearsal=false`.
It rechecks immutable authorization, exact-head acceptance and original completion
steps, and repairs only the failed breaking-change comment/deduplication marker.
It has no GCP auth or Slack send. The observer requires its repair step to succeed
and covers only the named PR/apply attempt, never another attempt or feature-ID
reset. Verify the original Slack parent after completion.

## Persistence and delivery

Signed checkpoints are append-only GitHub deployment records with task
`slack-incident`, environment `slack-incidents`, `production_environment=false`,
`auto_merge=false`, empty required contexts and `auto_inactive=false`. They are
notification bookkeeping, never deployment authority or an undeletable audit store.
Each is bound to its exact main workflow revision/run/attempt. Notification batches
allow several signed subjects; production verification keeps its independent
single-subject policy.

Claims are persisted and signed before non-idempotent posts. Valid Slack channel
and string timestamp responses are acknowledged and signed even if a later send
fails. Invalid responses, timeouts and HTTP 200 without `ok:true` never acknowledge
a post. Posts are paced; acknowledged parent updates can repeat safely. Permalink
retries use the exact parent timestamp and do not resend it. A changed view stays
pending until Slack acknowledges its update. New episodes defer until the old
parent's green update is acknowledged.

An uncertain claim is never blindly resent or replaced via webhook. It retains
new observations and does not block unrelated scopes. Unchanged claims are checked
every probe, cannot receive an idle snapshot, and fail safety health after 24 hours
since the latest signed checkpoint, requiring owner inspection. Operational
recovery does not imply confirmed delivery.

Readers verify v1/v2/v3 historical signatures before migration to v4. Retired v3
IDs must precede the checked retirement boundary and represent one complete
incident core; unknown/merged scopes, active empty registrations and omitted
failures fail closed. Original parents, replies and claims remain audit evidence;
old view acknowledgements and terminal reuse fingerprints are cleared. New writes
use v4. Rollback must retain its reader. Before an incompatible writer rollout,
pause notification admission, drain workers without cancelling sends, merge, and
resume within 24 hours. Changing the activation variable alone does not drain old
workers. Longer gaps need the owner-reviewed replay below.

## Incremental verification

Idle snapshots require an authenticated successful same-main producer, identical
code/verifier/activation/Slack identity, unchanged catalog generations and fresh
identical API transcripts. Pending executions/claims cannot create them.
Notification artifacts last one day and authenticate the exact producer, successful
job/upload, creation interval, archive digest and sole bounded member. Only exactly
verified receipts are exported. Terminal fingerprints bind complete record/status
history and verifier contract; cold verification rereads status history. Missing
proof runs full verification; changed/malformed evidence and failed reads fail
visibly. Caches grant no production authority. Owner reconciliation/final signing
retain fresh checks; only repository/workflow identities cache within an invocation.

GitHub GET transport/500/502/503/504 failures and the recognized attestation 503
retry at most three times, waiting 5 then 15 seconds, with identical request bytes
and signer policy. Authentication, authorization, rate limits and invalid proof
fail immediately. Writes and uncertain Slack sends are never retried this way.

## Configure and activate

Install a bot with `chat:write` and invite it into the channel; normal operation
needs no channel history/search/public-posting scope. Configure:

| Setting | Type | Value |
| --- | --- | --- |
| `SHARED_DATASETS_SLACK_BOT_TOKEN` | Secret | Installed bot token |
| `SHARED_DATASETS_SLACK_CHANNEL_ID` | Variable | Exact `C...` / `G...` channel |
| `SHARED_DATASETS_SLACK_INCIDENTS_SINCE` | Variable | UTC `YYYY-MM-DDTHH:MM:SSZ` activation |
| `SHARED_DATASETS_SLACK_INCIDENTS_ENABLED` | Variable | `true`, set last |

Never commit/log tokens or pass them in command arguments. Keep the existing
incoming webhook for disabled mode and safety self-reporting. Enabled bot mode
fails on missing configuration; it cannot silently select another transport.
Changing workspace/bot/channel requires explicit registry migration. Observe a
real failure/recovery sequence and original permalink; do not deliberately fail a
production job or send synthetic production messages to test delivery.

## Reconcile an uncertain send

Use the notification workflow's owner-only main dispatch after inspecting Slack:

1. Read the latest signed checkpoint and first active `claimed` operation
   (`parent` or historical `attempt:...`).
2. If that message exists and matches, choose `delivered` with its exact string
   timestamp. Derive it from the permalink by inserting a decimal before its final
   six digits; never use a float or another incident's timestamp.
3. If inspection proves it absent, choose `not-delivered` with an empty timestamp.
   A recovered undelivered parent retires quietly; an old reply is never replaced.

Confirm each uncertain operation separately. The worker then rechecks terminal
proof and retained observations; this never retries production mutation. An
unsigned interrupted checkpoint requires reviewed `seal-checkpoint` with the
incident ID and `checkpoint:RECORD_ID:PAYLOAD_SHA256` (canonical payload SHA-256).
Sealing rechecks exact historical recovery/status IDs, signatures and ancestry,
creates a new signed checkpoint, and sends nothing. Claims still require inspection.

For catch-up beyond the ordinary window, dispatch `replay-window`, incident
`workflow-bot-cutover`, operation `replay:START..END`, and no timestamp. The closed
past interval is at most seven days and enumerated daily; source workflows are
never rerun. Source provenance, signed checkpoints and delivery gates still apply.

### Closing a legacy webhook parent

Dispatch `Alerts / Legacy workflow resolution` as owner on main with original
failed run ID and inspected parent `message_ts`. Every failed job needs registered
signed deployment recovery or the exact historical completion-worker proof above.
Runtime targets require `verified`. This only updates the supplied original
parent: no replacement/reply, activation change or mutation. Unsupported failures
stay unresolved. Managed parents use normal reconciliation to retain signed closure.
For native Monitoring messages supply only `monitoring_incident_id`; see
[Monitoring closure](alert-routing.md#editable-monitoring-messages).

## Feature identity decision replies

These follow their [separate typed lifecycle](alert-routing.md#identity-decision-threads).
Preserve the original **Decision needed** body, cases, context and review links.
Record accepted choice, decision maker, rationale and exact reviewed PR in that
same thread with channel broadcast disabled. Status is a separate marker.
Partial decisions leave uncovered cases open; uncertain replies keep their claims.
The protected reconciler checks accepted final-head PR bytes and runtime legality;
late events use the private approved index. Review acceptance cannot establish
publication, execution or Monitoring recovery.
