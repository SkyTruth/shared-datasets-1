# Slack incident lifecycle

Operational failures have one searchable incident ID (`SD-` plus twelve hex
characters). The first failure creates an **Open** parent message. Additional
failed attempts go in its thread, with **Also send to channel** always disabled.
Verified recovery edits the original parent to green **Resolved**, retaining the
original failure and recovery evidence. It creates no reply or new channel
message. Healthy runs stay quiet. There is no live synthetic notification test.

## Recovery contract

The scope includes the exact deployment target, or exact workflow and job when
there is no registered deployment contract. IAM bootstrap and dependent sync
are distinct targets. A bootstrap success cannot resolve a failed dependent
sync. Job paths are derived from the checked-in reusable caller graph, including
the original reviewed PR number for dataset mutation jobs. Unknown job paths
remain separate incidents instead of guessing a target.

Deployment recovery requires the existing cryptographically verified terminal
record for that production target, its tested executor SHA and artifact. Runtime
targets require `verified`; an asynchronous start or `applied` without terminal
runtime acceptance is insufficient. Other targets retain their existing
accepted `applied`/`verified` phases. A newer attempted deployment that is pending
or failed prevents an older successful record from closing the incident.

Success must cover every retained failed revision by ancestry. Same-revision
recovery must follow the failed attempt. A signed deployment of a later revision
supersedes an older execution that finishes late. Signed healthy evidence keeps
advancing after recovery without sending more recovery messages. Skips,
validation-only CI success, unrelated deployments and an observer that has no
matching terminal record cannot resolve a deployment incident.

Audit/workflow startup incidents use successful completion of the same trusted
workflow. Other job incidents require the same exact job within that workflow.
Publication apply/reset incidents retain the original PR and phase; manual reset
recovery derives its PR only from the complete same-attempt apply job identity.
Publisher authorization and immutable plan rechecks remain owned by the existing
publisher. The notification registry never grants deployment or publication
authority.

## Persistence and delivery

Notification checkpoints use GitHub deployment records with task
`slack-incident`, environment `slack-incidents`, `production_environment=false`,
`auto_merge=false`, empty required contexts, and `auto_inactive=false`. These
are notification bookkeeping, not production deployments. The application
appends snapshots rather than rewriting them. GitHub records can be deleted by
privileged users; they are not an undeletable audit store.

Each checkpoint is bound to its exact main workflow revision/run/attempt by the
existing Sigstore receipt mechanism. Notification batches permit multiple signed
subjects; production receipt verification keeps its single-subject default.
Unsigned/forged snapshots cannot choose a parent timestamp or suppress alerts.
Signed state retains the workspace, bot, channel, parent timestamp/permalink,
failed attempts, original recovery evidence, latest healthy evidence, deferred
events and delivery claims/acknowledgements. Version 2 also records the hash of
the last acknowledged parent view. A changed view remains pending until Slack
acknowledges its update; an interrupted update safely retries the same parent.
Version 1 snapshots are signature-verified before migration. Existing timestamps
and historical recovery replies remain audit evidence and are never sent again.
Newer observations cannot replace a resolved incident until its original
parent's green update is acknowledged. They remain in the signed deferred queue
and replay on a later reconciliation, preserving both incident parents.

The serialized worker uses `queue: max` and never cancels an active send.
GitHub's queue is bounded and completion order can vary. Completions of the
existing hourly terminal verifier also reconcile the last 24 hours of main runs,
starting no earlier than the explicit activation timestamp. Complete API
enumeration and authoritative source-attempt checks are required. Longer
GitHub outages require reviewed replay of the missing interval; blanket
production retries are never a notification recovery mechanism.

Before posting, the worker persists and signs the claim. After Slack returns a
valid channel and message timestamp, it persists an acknowledgement; confirmed
delivery checkpoints are attested even if a later send fails. Posting is paced
to the channel limit. Updating an acknowledged parent can repeat safely.

Delivery rereads each prepared deployment claim and requires the same worker
invocation and exact record fields except GitHub's `updated_at`, which can
advance when its checkpoint status is posted. The current record still needs
full ledger identity and signed-receipt verification before any Slack write.
Payload, timestamps within incident state and all other API fields must match.

Permalink lookup uses Slack's documented `chat.getPermalink` GET request with
the exact string timestamp and fixed channel in query parameters. Posting and
updating retain JSON POST requests. A rejected permalink lookup fails the worker
after preserving acknowledged posts. A later worker signs a metadata-only
prepared record and resumes the lookup without posting the parent or acknowledged
thread replies again. Permalinks always identify the top-level incident parent;
this client does not request links to threaded replies.

An HTTP 200 without Slack `ok:true`, missing message identity, timeout or other
unconfirmed delivery cannot acknowledge a post. A claimed post is never blindly
resent, and the worker never falls back to a webhook after an uncertain bot send.
Known blocked scopes retain new observations for later replay; they do not
prevent unrelated scopes from posting. The first failed send remains failed in
GitHub. Subsequent read-only observations report **Action required** in their
summaries without attempting that send again. Operational recovery and Slack
delivery completion are separate states. A known open parent continues tracking
failed attempts while a retry's delivery is uncertain. Recovery must cover all
recorded and deferred failures before that parent can turn green; the uncertain
retry remains blocked and is never resent automatically. An unconfirmed original
parent still requires owner inspection before any parent update or new send.

## Configure and activate

Use the existing Shared Dataset Alerts app if appropriate, or a dedicated bot.
Add the bot scope `chat:write`, install/reinstall it, and invite the bot into the
alert channel. Normal operation needs no channel-history, search or public-channel
posting scopes. The bot can update only its own messages. Cloud Monitoring and
older webhook messages are not automatically adopted or rewritten.

Configure these repository settings before activation:

| Setting | Type | Value |
| --- | --- | --- |
| `SHARED_DATASETS_SLACK_BOT_TOKEN` | Actions secret | Installed app's bot token |
| `SHARED_DATASETS_SLACK_CHANNEL_ID` | Actions variable | Exact `C...` or `G...` channel ID |
| `SHARED_DATASETS_SLACK_INCIDENTS_SINCE` | Actions variable | Activation time, UTC `YYYY-MM-DDTHH:MM:SSZ` |
| `SHARED_DATASETS_SLACK_INCIDENTS_ENABLED` | Actions variable | `true`, set last |

Use the normal secret-setting interface; never commit, log or place the token in
a command argument. The incoming webhook secret remains in place for disabled
mode and other existing notifications. Enabled mode fails visibly on missing or
invalid configuration rather than silently choosing another transport.

Observe the next real failure/recovery sequence: one parent, thread-only retry
if another attempt fails, that same parent edited green, matching SHA/artifact,
and incident permalink in the notification run summary. Tests exercise the
actual HTTP request construction offline, including negative controls for
broadcasts, duplicate posts and false recovery. Do not create synthetic channel
messages or deliberately fail a deployment to validate notification delivery.

Disabling incident mode returns future failures to the existing webhook. Existing
incident messages and checkpoints remain available; changing bot/channel/workspace
identity while retaining a registry requires an explicit migration.

For the v1-to-v2 rollout, pause admission by disabling
`unattended-workflow-alert.yml` immediately before the approved merge. Completely
enumerate its existing nonterminal runs and let them finish without cancelling
an active send. Merge only after they have drained, verify the new main revision,
then re-enable the workflow. Already-created workers check out their original
workflow revision; a newer merge cannot upgrade them. Keep the pause within
24 hours so the next hourly terminal-verification completion reconciles missed
main events. A longer pause requires the reviewed missing-interval replay
described above. Changing the incident-mode variable alone does not prevent
old workflow admissions and is insufficient for this schema transition.
Do not rerun old v1 notification workers after v2 checkpoints have been written;
use the current main worker's owner reconciliation for uncertain delivery.

## Reconcile an uncertain send

Use the notification workflow's main-only dispatch as the repository owner.
Inspect the incident in Slack before confirming its delivery. No history-reading
permission is added just to automate this exceptional decision.

1. Read the latest signed notification checkpoint for the exact incident ID.
2. Choose its first active `claimed` operation: `parent` or `attempt:...`.
   Historical recovery claims are audit-only and cannot send a message.
3. If the message exists and matches the incident/operation, choose `delivered`
   and supply its exact string timestamp. A parent timestamp can be obtained
   from its permalink's `p` value by inserting the decimal before its final six
   digits. Do not use a float or another incident's message.
4. If inspection confirms the message was not posted, choose `not-delivered`
   with an empty timestamp. This explicitly authorizes the pending failure send.
   If verified recovery already covers an undelivered parent, it is retired
   quietly rather than posted as a new green message.

The workflow confirms only that exact first uncertain operation. Other uncertain
operations still require separate inspection. Once they are cleared, it rechecks
terminal evidence and replays retained observations before preparing any new
failure send. Events waiting for a green parent update replay after that update
is acknowledged. Real recovery never retries a production mutation.

If a runner stopped after a checkpoint write but before attestation, the latest
checkpoint fails signature verification. Do not skip it and resend. Inspect the
exact deployment record and all recorded Slack acknowledgements, compute
SHA-256 of its payload using `deployment_emission.canonical`, then dispatch
`seal-checkpoint` with the exact incident ID and operation
`checkpoint:RECORD_ID:PAYLOAD_SHA256`. This is explicit owner acceptance of the
reviewed notification bookkeeping. It creates a new signed checkpoint and sends
no Slack messages; claimed posts still need the separate confirmation above.
Deployment recovery evidence is independently rechecked before sealing.
Original recovery and newer healthy evidence are reverified by their exact
deployment/status IDs, including signatures and revision ancestry. A newer
healthy deployment does not invalidate the original recovery history.

## Implementation and validation

- `scripts/slack_incident_api.py` owns fixed routing and strict Slack responses.
- `scripts/slack_incidents.py` owns correlation, transitions, signed checkpoints
  and delivery reconciliation.
- `unattended-workflow-alert.yml` owns trusted main execution, serialization,
  claim signing and delivery signing.
- `scripts/deployment_receipt_contracts.py` admits only this main notification
  job's fixed checkout, commands, permissions and signing sequence. Negative
  controls retain the rejection of PR-capable or production mutation writers.
- `tests/test_slack_incidents.py` covers the actual preview/CDN job identities,
  wrong targets/revisions, stale events, skips, forged state, uncertain delivery,
  deferred events, owner reconciliation, production-registry separation,
  method-specific HTTP request contracts and permalink-only resumption after
  acknowledged delivery.
- `tests/test_slack_incident_api.py` exercises a complete real-client request
  sequence offline: parent, permalink, thread-only failure and green parent
  update. No production channel test is needed.

Invariant enforced: real open incidents may create messages; verified recovery
only updates their original parent. Exact scope/revision evidence and signed
claims remain required. Boundary changed: fixed thread routing and a versioned,
signed acknowledgement of the parent view. Code removed: the live synthetic
workflow, script and tests, recovery reply generation and broadcast option.
Internal handling removed: recovery posts no longer participate in the active
outbox. Fallbacks added: none. Fallbacks rejected: broadcast replies, new green
messages, blind reposts and inferred recovery. Deletion candidates left in place:
historical v1 snapshots and recovery posts retain persisted audit evidence; the
existing webhook supports disabled mode and established ingestion notifications.
Remaining uncertainty: actual delivery depends on Slack availability and bot
configuration; ambiguous non-idempotent sends still require owner inspection.
