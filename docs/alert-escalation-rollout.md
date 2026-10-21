# Accountable alert escalation rollout

Integration owner: the agent in originating chat
`01a15cc7-3222-7610-acd6-19ec6f6a848a`, acting for `jonaraphael`.
The owner prepares the sequential PRs, verifies their exact revisions, and owns
authorized recovery and alert reconciliation. PR creation does not authorize
production mutation, merge, or transfer to another chat.

## Sequence and dependencies

1. **Close proven holes** (`codex/slack-escalation-1-close-holes`): recognized
   safe-read retry classification and one bounded operation budget, catalog gate
   readiness, complete probe snapshots, offline notification isolation, isolated
   delivery rehearsal, and protected legacy policy retirement.
2. **Policy and ownership**, dependent on 1: signed v3 state, explicit reason
   vocabulary, fixed escalation clocks, authenticated supervision and clarification
   records, source correlation and one actionable parent without attempt replies.
3. **Cloud observations and health**, dependent on 2: structured runtime events,
   filtered read-only observer identity, complete checkpointed scans, shadow
   evaluation and independently monitored successful-cycle heartbeat.
4. **Operational cutover**, dependent on 3 and 24 hours of successful shadow
   collection: migrate historical delivery evidence, remove duplicate operational
   routes and obsolete secret access, and verify live routing. Preserve independent
   security coverage throughout. Seed the heartbeat after its absence policy exists.

The overlapping contracts are `deployment_revision.py`, `slack_incidents.py`, the
notification workflow and its provenance verifier, protected Terraform policy/IAM
allowlists, and alerting documentation. Finish prerequisites before final integration;
freeze each committed head before full preflight. Later PRs must not claim the
currently deployed v2 worker already implements private owner retention or delayed
escalation.

## First-PR counterfactual evidence

These are boundary replays, not claims that an unavailable provider would have
recovered. The reviewed October 7–21 investigation identified the following
signatures:

| Historical evidence | Implemented behavior | Remaining escalation |
|---|---|---|
| GitHub plain `HTTP 500`, runs `38496427534` and `38495751381`; catalog `502`, run `39278377548` | Both CLI error representations retry recognized GET failures; failed pagination and stream bytes are discarded. The real subprocess replay verifies request flags and complete replacement output. | Exhausted outage, unknown error, permission denial and malformed data still fail. |
| Catalog credential-service `503`, run `39350886819` | Read-only preparation shares a 30-minute budget, disables nested SDK retries, and retries with a fresh complete local snapshot. | Authentication/configuration defects and exhausted recognized outages remain actionable. |
| Catalog run `39483553569` rejected CI while exact `ci-ready` already passed | The existing exact gate verifier is authoritative even while other CI jobs run. | A truly pending exact gate defers scheduled preparation; failed, ambiguous or wrong-revision evidence cannot authorize deployment. |
| Repeated catalog probe failures were suppressed whenever publication skipped | Only successful idle probes remain quiet; failed probes reach incident handling. | Persistent inability to observe production becomes visible. A skipped publisher never establishes recovery. |
| Identity-decision unit test escaped through ambient notification credentials | Test-session guards prevent real Slack transport and production webhook resolution, including inherited child interpreters; fake HTTP boundaries remain testable. | No synthetic shared-channel delivery is required or allowed. |
| Schema policy `10109535963709095250` survived removal from Terraform | Exact declarative import, disabled state and empty channels are reviewed through protected policy sync; deletion policy joins managed scope. | Until the reviewed change merges and protected sync verifies it, the live legacy route remains enabled. |

Completed publication with a failed PR receipt continues to use the existing
receipt-only recovery path. Its log and comment reads now use the common reader;
this change grants no data-mutation or Slack-redelivery capability.

## Verification and live gates

Focused tests use fake clocks, real subprocess serialization and offline HTTP
transports. The required full clean-checkout preflight and hosted `ci-ready`
remain independent exact-head checks. Record their evidence in each PR.

Policy retirement requires reviewed merge and protected `Cron alert policy sync`.
Run the read-only routing audit against complete reviewed Terraform planned values
and complete live policy/channel inventories. Findings never authorize automatic
disabling of unknown policies. No local Terraform apply or import is part of this
rollout.

Before v3 migration, drain notification workers and preserve verified historical
records and uncertain claims. Before cloud cutover, retain 24 hours of complete
shadow scan and heartbeat evidence; a missed or incomplete cycle emits no heartbeat
and advances no coverage checkpoint. After cutover, retain seven days of classified
outcomes privately as operational evidence, including parent counts, duplicates,
automatic recovery, owner holds, expired supervision, uncertain delivery, coverage
gaps and time to actionable escalation. Do not publish routine metric summaries to
the shared channel.
