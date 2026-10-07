# Slack incident lifecycle

Operational failures have one searchable incident ID (`SD-` plus twelve hex
characters). The first failure creates an **Open** parent message. Additional
failed attempts go in its thread. Verified recovery changes the parent to
**Resolved** while retaining the original failure, then posts one threaded
recovery reply with channel broadcast enabled. Healthy runs stay quiet.

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
events and delivery claims/acknowledgements.

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
delivery completion are separate states.

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

Validate the first authorized failure/recovery sequence against actual Slack
delivery: one parent, retry thread, resolved parent, one broadcast recovery,
matching SHA/artifact, and incident permalink in the notification run summary.
This repository's default tests are network-free and do not prove live Slack
app installation or delivery. Avoid deliberate deployment failure just to test
the channel; use the controlled rehearsal below or a real incident.

Disabling incident mode returns future failures to the existing webhook. Existing
incident messages and checkpoints remain available; changing bot/channel/workspace
identity while retaining a registry requires an explicit migration.

## Controlled synthetic lifecycle rehearsal

After the reviewed implementation is merged, the repository owner may dispatch
**Synthetic Slack incident lifecycle rehearsal** from `main`. Both actor and
triggering actor must be the owner. The job uses the existing protected
`shared-datasets-production` environment and exact workflow revision, with only
`contents: read` GitHub permission. It does not request a cloud identity or
deployment/signing permissions.
It shares the real notification worker's serialized queue so the same bot and
channel cannot receive simultaneous posts from these two workflows.

The rehearsal uses the configured bot and channel to create an unmistakably
**SYNTHETIC REHEARSAL** parent tied to its run and attempt, retrieve its permalink,
post a synthetic repeat thread, update the parent to **Resolved**, and send one
synthetic broadcast recovery reply. Every message states that no real workflow
or deployment failure/recovery occurred. It never reads or writes the production
incident ledger and cannot resolve actual incidents.

The workflow retains `result.json` in an attempt-specific artifact, including
the exact revision, Slack identity, acknowledged message timestamps, permalink
and terminal stage/status. A later operation failure leaves earlier
acknowledgements intact. There are no automatic retries; inspect retained
evidence and the synthetic thread before deciding to dispatch another rehearsal.
Each new dispatch creates a new labeled rehearsal. The run summary links the
completed lifecycle. Successful synthetic delivery proves the configured Slack
API methods, not production recovery evidence or deployment readiness.

## Reconcile an uncertain send

Use the notification workflow's main-only dispatch as the repository owner.
Inspect the incident in Slack before confirming its delivery. No history-reading
permission is added just to automate this exceptional decision.

1. Read the latest signed notification checkpoint for the exact incident ID.
2. Choose its first `claimed` operation: `parent`, `attempt:...` or `resolved:...`.
3. If the message exists and matches the incident/operation, choose `delivered`
   and supply its exact string timestamp. A parent timestamp can be obtained
   from its permalink's `p` value by inserting the decimal before its final six
   digits. Do not use a float or another incident's message.
4. If inspection confirms the message was not posted, choose `not-delivered`
   with an empty timestamp. This explicitly authorizes the pending send.

The workflow acknowledges only that exact first uncertain operation before
continuing later unsent operations. New events retained during the pause replay
on a subsequent reconciliation. Real recovery never retries a production mutation.

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
- `slack-incident-rehearsal.yml` and `scripts/slack_incident_rehearsal.py` own
  the separately labeled manual Slack lifecycle check. Its tests execute the
  workflow's main/owner guards and retain failed-operation acknowledgements.

Invariant enforced: only evidence covering the exact affected scope changes an
incident to resolved; non-idempotent posts have signed claims before sending.
Boundaries changed: source-attempt identity, signed registry state and Slack API
acknowledgement. Code removed: duplicate CI delivery-failure classification is
replaced by one shared helper. Internal handling removed: no inferred success
from skips or generic green CI. Fallbacks added: none for enabled-mode delivery.
Fallbacks rejected: webhook resend, guessed targets, unsigned registry rows and
blanket production retries. Deletion candidates retained: the existing webhook
path supports disabled mode and established ingestion notifications. Remaining
uncertainty: bot installation, channel membership and live delivery require the
configured Slack workspace.
