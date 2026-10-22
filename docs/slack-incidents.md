# Slack incident lifecycle

Operational failures have one searchable incident ID (`SD-` plus twelve hex
characters). An actionable escalation creates one **Open** parent message.
Recognized safe transient recovery stays private for thirty minutes; an active,
authenticated owner can retain a safe issue within the fixed supervision limit
below. Failed attempts stay in GitHub evidence and update the parent's count and
latest evidence without automatic thread replies or broadcasts.
Verified recovery edits the original parent to green **Resolved**, retaining the
original failure and recovery evidence. It creates no reply or new channel
message. Healthy runs and failures recovered before escalation stay quiet. Retain
an episode through thirty minutes of stable recovery so brief flapping reuses its
parent. Synthetic tests must use offline transports or a separate test channel.

The `Workflow health and Slack reconciliation` workflow runs on failed workflow
completions, observer completions and explicit owner requests. Ordinary healthy
completions allocate no reconciliation worker. Every five-minute scheduled tick
first replays the last 24 hours of verified main workflow observations and
reconsiders pending episodes, including recovery and owner deadlines. Schedule
delivery can be delayed or dropped; a deadline establishes
eligibility, not a guaranteed message timestamp. Its presence in Actions does not mean a failure
occurred or a Slack message was sent. `Deployment completion check` runs hourly;
its completion also retains an independent incident replay. Historical
runs under its former name, `Deployment terminal verification`, remain recognized
under the same workflow and incident identity.

The hourly catalog input probe is read-only. An unchanged comparison skips the
catalog deployment job. Only a successful idle probe stays quiet. Recognized safe
read outages receive bounded thirty-minute source-worker recovery; exhausted outages and
permanent or unknown failures become actionable. Probe recovery covers only its
exact read-only job, and a skipped publisher never proves deployment recovery.
An allocated publisher failure still alerts. Historical catalog runs
without this probe retain their original failure and recovery evidence.

## Policy and accountable supervision

`catalog/alert-routing.json` is the versioned policy registry;
`scripts/operational_policy.py` makes pure decisions from verified facts and an
explicit clock. Supported failure reasons are enums. Free-text similarity never
decides suppression. Unknown impact and published-data state are stated plainly.
The public parent includes impact, data state, responsible owner, required action,
recovery already attempted and evidence.

An agent acts for its responsible human in the originating chat. Register before
overseeing deployment, then explicitly check in; registration alone cannot hold
an issue privately. For example, after identifying the exact tested revision:

```bash
uv run python scripts/slack_supervision.py claim --target catalog-web \
  --revision "$TESTED_MAIN_SHA" --pr "$ORIGINAL_PR" --human "$RESPONSIBLE_HUMAN" \
  --conversation "$ORIGINATING_CHAT_URL" --current-action "Checking deployment prerequisites"
uv run python scripts/slack_supervision.py check-in --target catalog-web \
  --revision "$TESTED_MAIN_SHA" --pr "$ORIGINAL_PR" --human "$RESPONSIBLE_HUMAN" \
  --conversation "$ORIGINATING_CHAT_URL" --current-action "Verifying the exact terminal receipt"
```

The CLI sends JSON through stdin to the existing workflow on `main`. It waits
for its unique request run and successful signed-checkpoint verification; a
skipped worker or unrelated green run does not prove registration. `--dry-run`
is offline; `--no-wait` requests only and requires later exact-run verification.
The workflow derives authority from its authenticated actor and run, never CLI
inputs. The human must match that actor. Supplied deployment PRs must match the
authoritative merged revision. Exact existing workflow/job/publication scopes can
use `--operation-id`; guessed job scopes cannot establish prospective ownership.

Check in at least every ten minutes with the current action. The first verified
failure binds a fixed two-hour deadline. New commits, claims, repeated failures
and changed repair PRs cannot restart it. A stale check-in, explicit release or
expired deadline makes the unresolved episode eligible for escalation once.

Ask clarification in the originating chat, then use `clarification` with
`--question-ref "$ORIGINATING_CHAT_URL#MESSAGE_ID"` and the same binding. This
pauses check-ins until the original deadline; it never proves recovery. Before
stopping unresolved work, record clarification, `release`, or `supersede` with
an explicit handoff reference. There is no automatic cross-chat message, agent
creation or transfer of deployment authority.

For supervision commands, only an explicit `claim` submitted after 30 minutes of
verified stable recovery retires ownership bound to the old failure. Repeated
commands for a prospective binding preserve its registration, human and chat
constraints; queue delay cannot move an earlier claim into a new ownership episode.

After explicitly releasing an operation that never owned a failure, the same
authenticated human may register a new deployment PR and revision from a new
originating chat. The worker verifies the exact merged PR and requires no open
incident; a retained resolved incident must have 30 minutes of stable recovery
at dispatch creation time. This creates inactive registration with empty source
coverage and no check-in or failure clock. Previous signed checkpoints and all
incident, delivery and recovery evidence remain intact.

Security and demonstrated integrity risks bypass holds through independent
Monitoring. Uncertain mutation outcomes freeze replay and require authoritative
inspection. The notification engine starts no production recovery. Source
workers retain their operation-specific read, publication, receipt and apply
safety rules, including lock-acquisition-only Terraform retry before writes.

## Recovery contract

The scope includes the exact deployment target, or exact workflow and job when
there is no registered deployment contract. IAM bootstrap and dependent sync
are distinct targets. A bootstrap success cannot resolve a failed dependent
sync. Job paths are derived from the checked-in reusable caller graph, including
the original reviewed PR number for dataset mutation jobs. Unknown job paths
remain separate incidents instead of guessing a target.

The TypeScript SDK's read-only `candidate` job has its own exact-job scope;
`publish` retains the deployment scope below. Candidate recovery requires the
package-verification step to have actually succeeded. Successful wrappers that
skip verification for obsolete or ineligible CI do not recover a candidate
incident. A verified candidate with no release needed can recover it without
publishing a package.

Existing open SDK deployment episodes containing only candidate failures are
reclassified after their checkpoint signature and every historical job are
verified, including that each publication job was skipped. The original incident
ID, parent, delivery claims and failure identities are retained; signed source
checkpoints remain audit history. Canonical checkpoints must preserve the same
parent and history. A later candidate episode requires a signed resolved
checkpoint for the migrated predecessor. Mixed publication episodes or conflicting
parents fail visibly. During this rollout, pause notification-workflow admission,
drain existing workers before merging, then re-enable it on the new main revision.

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

### A completed publication with a failed PR receipt

When promotion, metadata finalization and index rebuild succeeded but the live
breaking-change PR comment failed with GitHub's integration-permission 403, do
not rerun the mutation. `Approved dataset mutation` has an owner-only,
protected receipt recovery: supply the original `pr_number`,
`receipt_recovery_run_id` and `receipt_recovery_attempt` on tested `main`.
Leave `authorization_rehearsal` false. The ordinary mutation jobs are excluded.

Recovery rechecks the original immutable authorization artifact, exact-head
acceptance, completed publication steps and failed comment command. It rejects
incomplete publications, other failures and the webhook's failed-delivery
diagnostic. It restores only that comment's exact deduplication marker, or
reuses an existing trusted receipt. It has no GCP authentication or Slack send
and leaves retained staging objects for the normal scratch cleanup process.

The observer requires the repair step to have succeeded. Its green parent
update covers only the original PR's apply run and attempt named by the recovery
job; it cannot clear another failed attempt or feature-ID reset. Verify the
original Slack parent after completion. A successful workflow wrapper alone is
insufficient.

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
Incident/deployment and reviewed-dataset authorization JSON reads, including
paginated enumeration, use at most three attempts
for HTTP 500/502/503/504, the CLI's confirmed GitHub connection failure, or a
60-second command timeout, waiting 5 then 15 seconds. Each attempt repeats the
same explicit GET; failed or partial output is discarded. Authentication,
authorization, rate-limit responses and unknown CLI errors fail immediately.
Successful responses still pass all existing JSON, enumeration and provenance
checks. This transport policy never retries writes or uncertain Slack posts.
The read-only verifier retries the exact GitHub attestation-service HTTP 503 for
the same repository and receipt digest at most three times, waiting 5 then 15
seconds. Every attempt uses the same bytes and signer/source policy. Exhausted
outages, unavailable signatures, invalid evidence and all other errors remain
failures; no production mutation or Slack delivery is retried by this helper.
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
GitHub's queue is bounded and completion order can vary. Scheduled ticks and
completions of the hourly terminal verifier reconcile the last 24 hours of main
runs, starting no earlier than the explicit activation timestamp. Complete API
enumeration and authoritative source-attempt checks are required. Longer
GitHub outages require reviewed replay of the missing interval; blanket
production retries are never a notification recovery mechanism.

Each tick completes the GitHub claims, delivery outcomes and durable signature
verification in `github-incidents/` before requesting observer credentials.
Optional cloud collection uses a separate `slack-incidents/` plan after that
barrier and does not repeat the GitHub replay. A failed observer authentication
or scan cannot undo completed GitHub recovery, and cannot advance cloud coverage
or emit a heartbeat. Independent GitHub escalation and quiet edits of known bot
parents remain available for mixed incidents without cloud credentials. A
canonical translation green still requires a fresh manifest read in the cloud
phase. Uncertain sends retain the existing inspection requirement in both phases.

After complete ledger enumeration, up to four independent receipt proofs are
verified concurrently. Every proof still requires current complete statuses,
valid state and exact signer/source verification. API reads remain serial and
share the caller's retry budget; only independent attestation verification
overlaps. The loader waits for every proof before returning states or processing
aliases, and any invalid proof fails the complete load. Verification caches
remain private to the current process. Writes and Slack delivery remain serial;
this optimization does not guarantee a supervision turnaround time.

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
a command argument. Curated announcement entrypoints retain their separate
reviewed delivery contract. There is no workflow-failure webhook fallback.
Enabled mode fails visibly on missing or
invalid configuration rather than silently choosing another transport.

Observe the next real escalation/recovery sequence: one parent, updated failure
evidence without automatic retry replies, that same parent edited green, matching SHA/artifact,
and incident permalink in the notification run summary. Tests exercise the
actual HTTP request construction offline, including negative controls for
broadcasts, duplicate posts and false recovery. Do not create synthetic channel
messages or deliberately fail a deployment to validate notification delivery.

Disabling incident mode pauses operational delivery. Existing
incident messages and checkpoints remain available; changing bot/channel/workspace
identity while retaining a registry requires an explicit migration.

For the signed v3 rollout, pause admission by disabling
`unattended-workflow-alert.yml` immediately before the approved merge. Completely
enumerate its existing nonterminal runs and let them finish without cancelling
an active send. Merge only after they have drained, verify the new main revision,
then re-enable the workflow. Already-created workers check out their original
workflow revision; a newer merge cannot upgrade them. Keep the pause within
24 hours so the next hourly terminal-verification completion reconciles missed
main events. A longer pause requires the reviewed missing-interval replay
described above. Changing the incident-mode variable alone does not prevent
old workflow admissions and is insufficient for this schema transition.
Do not rerun old v1/v2 notification workers after v3 checkpoints have been written;
use the current main worker's owner reconciliation for uncertain delivery.
Verified v1/v2 parents, history and uncertain claims survive migration. Old
webhook/Monitoring messages remain audit references; they are not editable bot
parents and ambiguous legacy sends must not be resent. Complete cloud cutover
only after the separately reviewed observer rollout and 24-hour shadow evidence.

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
- `catalog/alert-routing.json` and `scripts/operational_policy.py` own explicit
  classifications, bounded recovery and supervision clocks.
- `scripts/slack_supervision.py` requests authenticated transitions and verifies
  the exact workflow run completed signed-checkpoint verification.
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
  sequence offline: parent, permalink and green parent
  update. No production channel test is needed.

Invariant enforced: only eligible actionable episodes create public parents;
verified target-specific recovery only updates their original parent. Exact
scope/revision evidence, fixed ownership clocks and signed claims remain required.
Boundary changed: signed v3 state and authenticated main-only supervision.
Code removed: legacy workflow webhook bypass and automatic failed-attempt replies.
Internal handling removed: per-attempt posts no longer enter the active outbox.
Fallbacks added: explicit signed migration for verified historical state only.
Fallbacks rejected: broadcast replies, new green messages, blind reposts, clock
resets and inferred recovery. Deletion candidates left in place: historical v1/v2
snapshots and uncertain reply claims remain persisted audit evidence; curated
announcement and existing runtime routes await the gated cloud cutover.
Remaining uncertainty: actual delivery depends on Slack availability and bot
configuration; ambiguous non-idempotent sends still require owner inspection.
