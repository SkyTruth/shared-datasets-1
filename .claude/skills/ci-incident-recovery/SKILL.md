---
name: ci-incident-recovery
description: Use when investigating a failed GitHub Actions run, recovering a failed deployment or publication, reconciling an unresolved workflow incident, or diagnosing repeated recovery failures or excessive incident-related merge time in shared-datasets-1.
---

# CI Incident Recovery

Carry an incident from evidence to terminal recovery and reconciliation without
weakening validation, production authority or delivery guarantees. Keep four facts
separate: code merged, operation completed, observer verified, original alert reconciled.

This skill grants no Git, production, Slack or cross-thread messaging authorization.
Preserve existing authorization; request only genuinely missing authority and explain it.

## Load contracts

Read AGENTS.md, [the ordered recovery checklist](../../../docs/ci-recovery.md),
[alert routing](../../../docs/alert-routing.md), [incident lifecycle](../../../docs/slack-incidents.md)
and the affected deployment/ingestion documentation. Use invariant-first-engineering
for code/persisted-state fixes and protected-terraform-apply before production
Terraform/CDN planning. Load ingestion, publishing, preview and GCS skills as applicable.

## Establish identity and ownership

Follow the ordered checklist: record exact repository/run/attempt/revision/event,
failed job/step, signature, target, mutation record, original incident/parent and
owner. Read the actual attempt, not alert titles or earlier incidents. Follow
[agent integration](../../../docs/agent-integration.md) for dependencies/overlaps,
authorized messaging, accepted handoffs and frozen-head validation.

## Diagnose before retrying

Classify the exact failed boundary and missing test/prerequisite using the checklist.
Locate the last confirmed mutation; unknown outcomes and unavailable proof never
authorize bypassing verification. Inspect live records through trusted code.

Choose an existing protected recovery only after establishing target outcome,
replay eligibility, trigger, tested source, terminal proof and observer action.
Validation selection is not deployment selection. Retain exact executor/artifact,
original PR approval, immutable plans/generations, locks, records, environment and
permissions. Reconcile incomplete attempts first when required. Ancestry alone
does not authorize newer executors; never replay over newer attempts. If no safe
path exists, implement a reviewed constrained path rather than bypassing authority.

For transient read-only hosted validation outages, refresh live PR head and exact
run/attempt. The owner has at most two additional attempts, after 15 then 60 minutes
(or a longer provider interval); stop repeated identical outages until recovery
evidence appears. Do not rebuild locally to compensate. Code, prerequisite,
authority and evidence defects need repair, not retries. Stop follow-ups on merged,
closed or superseded work; never retry an old head or alter another chat's automation.

## Repair the boundary

Use invariant-first-engineering for real-boundary fixtures/negative controls and
publication-readiness.md for complete input-bound publication decisions. Resolve
pinned tools through child-process PATH; finish focused checks before the required
full preflight in docs/ci-preflight.md. Missing/failed/unexpectedly skipped evidence
never passes. Verify bytes, live state and persisted results separately.

## Own terminal recovery and delivery

Continue already-authorized recovery after merge. Report merge, operation completion,
observer verification and original-parent reconciliation as distinct milestones.
A started asynchronous execution or successful observer run alone is insufficient.
Do not create schedules unless requested or claim automatic retry without checking it.

Safety health lists actual problems, posts on change plus daily reminders, and warns
72 hours before reviewed holds/pauses expire. Health-only failures do not enter
incident replay or its three-dispatch escalation. Infrastructure failure observation
works without checkout. Native failures remain immediate; token-dispatched failures
are polled every three hours. Claims unresolved for 24 hours require owner inspection.

Follow the loaded incident lifecycle for stable parents, green edits, historical
proofs and uncertain claims; never change observer identities or blindly resend.
Preserve complete **Decision needed** requests and reply with accepted action,
decision maker, rationale and reviewed PR in their original thread without broadcast.
Use isolated fixtures; never fail production or send synthetic channel messages.
Measure validation, review, deployment and reconciliation delays separately.

## Completion evidence

Report failure category/cause, missing regression/prerequisite, tested revision/PR,
merge state, recovery run/terminal result, original incident/visible reconciliation,
remaining blocker/owner and retained local evidence directory. If delivery is blocked,
state what succeeded, the exact external failure and verified automatic next action.
