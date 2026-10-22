# Independent Monitoring incident relay

This is the implementation and rollout contract for replacing new Google
Monitoring Slack messages with bot-owned parents that can be updated. Monitoring
continues to detect incidents. An independent Cloud Scheduler job invokes a
Cloud Run Job that reads the Monitoring Alerts API and maintains one durable
delivery record per native incident. GitHub is used to review and deploy the
relay; a running relay must not depend on GitHub availability.

The initial public scope is exactly the three independent policies:

- Operational reconciliation heartbeat absence.
- Dataset object deletion.
- Dataset object writes by an unapproved principal.

The four operational policies retain the original unified-engine cutover and
its 24-hour qualification requirement. This relay must not become another public
route for those same operational failures. Curated announcements are unchanged.

## Invariants

1. Detection remains enabled and an independently verified notification path
   remains installed throughout rollout. A healthy relay process alone is not
   proof that discovery, durable state or Slack delivery works.
2. Only authenticated Monitoring responses for the configured project and
   exact policy resource names establish native incident lifecycle facts.
   Display names, log text and approximate timestamps cannot establish identity.
3. One exact native incident has at most one bot parent. Repeated polls, retries,
   duplicate rows and overlapping job executions cannot authorize another send.
4. A durable claim precedes a Slack send. An uncertain send remains uncertain
   until authoritative inspection resolves it; elapsed time never permits replay.
5. Monitoring lifecycle closure and verified problem recovery are separate.
   Neither a closed security incident nor a quiet log proves data repair.
6. No partial enumeration or failed persistence advances the discovery checkpoint
   or establishes relay health. Missing rows and HTTP 404 do not prove recovery.
7. The runtime can read alerts and its exact secret, and read/write its dedicated
   operational state. It cannot mutate datasets, start ingestion executions,
   alter infrastructure, or write the reconciliation heartbeat.

## Provider boundary and discovery

Use the documented read-only endpoints:

```text
GET https://monitoring.googleapis.com/v3/projects/PROJECT/alerts
GET https://monitoring.googleapis.com/v3/projects/PROJECT/alerts/ALERT_ID
```

The response uses `openTime` and `closeTime`; the documented REST filter syntax
uses `open_time` and `close_time`. For each exact configured policy, discover all
open incidents regardless of age, plus overlapping recent openings and closures:

```text
policy.name="projects/PROJECT/alertPolicies/POLICY_ID" AND
(state=OPEN OR open_time>="LOWER_BOUND" OR close_time>="LOWER_BOUND")
```

Three separate complete queries for those branches are also valid. The lower
bound comes from the last successfully persisted scan with an explicit overlap;
it must not move to the current time after an unsuccessful pass. An incident
that opens and closes between polls must still be observable. Revisit every
known parent needing a lifecycle or delivery update with an exact alert GET.

A read-only production probe confirmed these individual and combined filter
forms, including exact `policy.name`, on 2026-10-22. A nonexistent policy returned
no matches; an unknown field returned HTTP 400. This proves the request grammar,
not deployment readiness or permission for a future runtime identity.

Consume every `nextPageToken`; reject token loops, inconsistent duplicate
identities and incomplete responses. `pageSize` is at most 1,000. `totalSize` is
an **estimate**, not a completeness assertion or pagination stopping condition.
Tokens are valid for 72 hours; start each polling pass from its first page
rather than persisting a page token as the discovery checkpoint. Closed alerts
are retained for 13 months and open alerts indefinitely. A gap beyond the
recoverable history needs explicit reconciliation, not an assertion of coverage.

The API includes the exact `policy.name`, native alert `name`, `state`,
`openTime`, optional `closeTime`, and resource/metric or log metadata. Validate
those identities and timestamps at the provider boundary. Treat notification
documentation, resource labels and extracted log values as display data; escape
them before rendering Slack text. Construct evidence links from verified IDs.

The tiny JSON files in `tests/fixtures/monitoring-slack/` model the official Alert
representation. They are offline examples, not observations, delivery receipts,
or authority to mark a real incident recovered. `health-open.json` intentionally
omits `closeTime`; `security-closed.json` intentionally contains no repair proof.

## Durable parent ownership

Use a dedicated, nonpublic operational GCS state bucket or an equivalently
isolated state boundary. Dataset prefixes and the GitHub incident ledger are not
the relay's writable state. Address each incident by its full native alert name;
bind the policy, bot user, workspace and channel in the record. Keep source
evidence, desired presentation and delivery acknowledgement distinct.

All mutations use generation preconditions. Claim a missing record with
generation zero; update an existing record only against the generation actually
read. Resolve a conflict by rereading the current record, never by blind
overwrite. A single Cloud Run task or `parallelism=1` does not replace this guard:
overlapping executions and old revisions remain possible.

Persist the parent claim before `chat.postMessage`. Save the returned exact
channel and message timestamp before treating delivery as confirmed. Once a
parent is confirmed, lifecycle changes edit that parent with `chat.update`;
they do not create broadcasts or automatic attempt replies. Serialize updates
per incident and render the latest durable desired state, so an old open
observation cannot overwrite a known later closure.

A timeout, connection failure, crash after claim, or ambiguous Slack response
does not authorize another post. Preserve the uncertain claim and expose it to
the responsible operator and independent relay-health backstop. The operator
must inspect the exact channel and bot identity, then explicitly record either
the exact confirmed message timestamp or authoritative non-delivery. Never
adopt another app's native message or infer non-delivery from a search miss.
This preparation freezes both posts and known-parent updates after a surviving
claim or uncertain acknowledgement. An old sender could still finish after a
replacement writer and overwrite newer lifecycle evidence. There is no automatic
takeover or implemented manual receipt-only control yet; that control and its
authoritative inspection procedure are prerequisites for public cutover.

Use the existing Slack Web API client. Supply the bot secret explicitly from
its dedicated Secret Manager access grant and pin the configured channel and
workspace/bot identity. Token rotation to a different bot requires an explicit
ownership migration. No domain helper discovers production secrets implicitly.

## Closure is not always recovery

For pipeline health, mark the parent **recovered** only after both an exact
current CLOSED alert and a genuine matching heartbeat point that is newer than
the incident opening and protected absence-policy installation. The point must
be current under the absence policy, have the expected resource/metric/value,
and not be future-dated. The relay reads that point; it never emits it or calls
GitHub to manufacture independent health. This proves pipeline recovery only.

If Monitoring closes the incident but that evidence is unavailable, state
**Monitoring closed; recovery unverified**. Monitoring can close alerts because
of manual action, snoozing or expiration as well as observed recovery.

For security policies, preserve the required investigation and repair action
when native state becomes CLOSED. Log-based incidents close manually or through
auto-close; neither means a deletion was safe, bytes were restored, or an
unapproved writer was authorized. Future security resolution needs an explicit
authoritative scope-specific contract. General job success cannot supply it.

A health incident already closed before its first public delivery remains quiet
and auditable; it must not produce a stale red opening. A security incident
opened after relay activation still requires an action parent when first
discovered CLOSED: native closure does not prove the investigation or repair was
completed. Preactivation security history remains historical rather than being
replayed into the channel. An already confirmed bot parent still receives its
truthful lifecycle update. Preserve a closed tombstone so delayed or repeated
open observations cannot reopen that same native incident. A new native incident
ID remains a distinct source fact.

## Rollout and failure backstop

1. Review and deploy additive relay resources through protected Terraform.
   Verify the runtime's actual supported alert GET/list, state CAS, bot-secret,
   Scheduler invocation and health-monitoring permissions before changing routes.
2. Rehearse the real serialization and transport boundaries with offline fakes
   and an explicitly isolated test channel. An unset test destination refuses
   delivery. Never create synthetic messages in the shared channel or deliberately
   fail production to prove notification delivery.
3. Observe genuine incidents in shadow. Verify complete discovery, exact policy
   ownership, bounded execution, durable state and the independent backstop.
   Native delivery remains installed until the replacement is verified.
4. Activate new bot parents only for incidents opened after an explicit
   activation boundary. Existing Google posts remain historical and uneditable
   by this bot. Do not resend them as migration messages. Retain the reviewed
   treatment of an incident already open at activation; no implicit adoption.
5. Remove a direct native Slack route only through a protected reviewed change
   whose live readback proves detection and replacement delivery coverage.
   Existing security/routing guards must explicitly understand that proof;
   an unused notification channel is not a substitute for independent delivery.

Polling adds its schedule and execution latency after Monitoring's detection
window; it is not an immediate-delivery guarantee. Polling frequency, bounded
retry duration and relay-health thresholds must be explicit operational choices.
Scheduler retries must be harmless even after the worker has persisted a claim.

The protected deploy workflow seeds a bot version only when
`confirmed_secret_version` is empty and the dedicated secret has no versions.
If seeding succeeded before a later preparation failure, inspect that exact
version and the failed deployment boundary, then supply its numeric version to
the protected workflow's `confirmed_secret_version` input. This selects a
metadata-only confirmation path: complete version enumeration must find the
exact enabled version; it does not append a version or read its payload. Never
select `latest`, infer a version after an ambiguous seed, or delete versions to
make seeding run again. Existing runtime deployment claims still require their
ordinary authoritative reconciliation before replay.

For an existing relay, preparation must preserve the deployed activation time
when every other pinned configuration identity agrees. A redeploy must retain
the delivery-state binding. Changing the bot version, destination or policy
identity requires a reviewed migration rather than a new implicit activation.

The collector retains successful relay executions and capability probes as audit
evidence. Only the exact immutable worker's typed completion event from a normal
live cycle, after durable acknowledgements and a final check for actionable or
uncertain work, can establish relay recovery. That event cannot establish native
security resolution, dataset publication, or the main pipeline heartbeat.

Monitor completed relay passes and pending/uncertain delivery through a path
that does not depend on this same relay or GitHub. Native Cloud Run/Scheduler
metrics avoid granting the relay permission to fabricate the monitored pipeline
heartbeat. The final backstop needs an independent recipient/transport, or an
explicit retained break-glass native route. Slack itself failing cannot be
reported reliably through another Slack sender alone. Never silence security
or pipeline-health coverage to remove duplicate messages.

The current `47408aad6aa0d1cb2773bca42acc0528687237ca763ab624c8420757edeba631`
qualification window belongs to the installed observation protocol. This
additive design is not cutover evidence. Changing an existing cycle-hashed
source or installed activation protocol invalidates that source qualification;
do not credit an earlier window to changed semantics or silently weaken the
hash closure. Resolve that integration boundary explicitly before rollout.

## Operator handoff

Retain the relay image/source, activation boundary, exact policy IDs, schedule,
complete-scan checkpoint, state generations, confirmed parent timestamps,
uncertain claims, independent health evidence and last successful pass. Report
the failed boundary and responsible owner when API access, enumeration,
persistence, delivery or backstop coverage fails. Preserve claims on rollback;
never replay uncertain sends when restoring the prior route.

The unified engine and relay must have one declared public owner per policy.
The relay owns the three independent native policy lifecycles; the unified
engine owns operational action episodes after its reviewed cutover. Sharing
rendering or transport code does not authorize two writers for one parent.
Cross-source parent adoption requires a separate exact provenance and ownership
contract; approximate resource names or times are insufficient.

## Provider references

- [Monitoring alerts list](https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.alerts/list)
- [Monitoring alert representation and exact GET](https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.alerts)
- [REST filter examples and metric alert closure](https://docs.cloud.google.com/monitoring/alerts/incidents-events)
- [Log-based incident closure](https://docs.cloud.google.com/monitoring/alerts/log-based-incidents)
- [Alert retention and limits](https://docs.cloud.google.com/monitoring/quotas)
- [Monitoring notification strategies](https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.alertPolicies#AlertStrategy)
- [Slack message update ownership](https://docs.slack.dev/reference/methods/chat.update/)
