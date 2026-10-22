---
name: ci-incident-recovery
description: Use when investigating a failed GitHub Actions run, recovering a failed deployment or publication, reconciling an unresolved workflow incident, or diagnosing repeated recovery failures or excessive incident-related merge time in shared-datasets-1. Establish exact run identity, classify the failed boundary, preserve authorization, own terminal recovery, and verify quiet reconciliation of the original Slack alert.
---

# CI Incident Recovery

## Intent

Carry an incident from evidence to verified recovery without weakening validation, production authority, or notification delivery guarantees.

Keep four facts separate:

- The code fix is merged.
- The authorized operation reached its required terminal outcome.
- The observer verified that outcome.
- The original incident alert was reconciled.

Do not report one as proof of another.

## When To Use

Use for:

- A reported failed GitHub Actions run.
- A deployment or publication that needs selective recovery.
- An incident that remains open after a fix or successful retry.
- Repeated failures in notification or terminal-verification workflows.
- Excessive validation or waiting during incident repair.

## When Not To Use

Do not trigger for ordinary repository work without an incident.

Negative examples:

- A routine documentation correction with no failed workflow belongs to the documentation workflow.
- A new dataset intake request belongs to the publishing and GCS skills; it is not incident recovery merely because publication uses CI.

This skill does not authorize Git operations, production mutations, Slack messages, or cross-thread messages by itself.

## Load The Relevant Contracts

Read AGENTS.md and the existing CI and deployment documentation.

Use invariant-first-engineering for code and persisted-state fixes.

Use protected-terraform-apply before planning or running production Terraform or CDN recovery. Use the ingestion, publishing, preview, and GCS skills when their operations are involved.

Preserve existing user authorization. Do not add another approval request when the action is already authorized. If approval is genuinely required, identify the exact missing authorization and its source.

## Establish Identity And Ownership

Record:

```text
Repository:
Run URL and attempt:
Event, branch, and executor SHA:
Failed job and step:
Failure signature:
Affected target:
Deployment or publication record:
Incident ID and original Slack message:
Recovery owner:
```

Read the exact failed attempt and surrounding successful steps. Do not infer the cause from an alert title, an earlier incident, or a different run.

Check whether another task is already changing the affected area. Keep one accountable recovery owner. Delegation does not transfer ownership unless an explicit handoff is accepted.

Follow `docs/agent-integration.md` for overlapping PRs and expensive validation.
Record dependencies before final preflight, complete required main synchronization
first, and keep the validating checkout frozen. A background follow-up is the
same owner, not a second independent retry or integration task.

Follow the applicable authorization rules before messaging another task.

## Classify The Failure

Distinguish:

- Reproducible code or validation failure.
- Missing deployment prerequisite.
- Authorization or persisted-contract rejection.
- External runner, network, or service outage.
- Terminal-verification failure after a possible mutation.
- Notification or observer failure.
- An expected negative control.

Explain the evidence for the classification.

For a reproducible failure that escaped CI, identify the missing test boundary or suite-selection dependency. Do not treat a large passing suite as evidence that the affected boundary was tested.

## Find The Mutation Frontier

Determine the last confirmed action.

Distinguish failure before mutation, partial mutation, successful mutation with failed verification, and failed notification after verified success.

Inspect authoritative records and live prerequisites through trusted code when needed. An unknown outcome must not be treated as proof that nothing changed.

An unavailable attestation or record service does not authorize skipping verification.

## Choose The Existing Safe Recovery

Inspect existing protected recovery paths before proposing another workflow.

Before dispatch, establish the target outcome, ledger and replay eligibility, workflow trigger, tested-source requirements, terminal proof, and observer action. Check the target-specific contracts and actual deployment selector. Validation-suite selection does not establish deployment selection. Determine whether a failed or incomplete record requires reconciliation before retrying.

Use the smallest existing path that can perform the authorized recovery while retaining:

- The exact tested executor revision.
- Original PR identity and applicable approval.
- Immutable plan hashes and generation conditions.
- Target serialization and deployment records.
- Protected environments and narrow permissions.
- Required terminal verification.

A newer tested revision is eligible only when the existing verifier and deployment contract permit it. Do not infer eligibility from ancestry alone.

Do not blanket-retry production mutations. Do not replay a stale deployment over a newer attempted revision.

Retry a read-only transient operation only when its behavior is understood. For a mutation, establish its outcome and replay contract first.

For hosted validation, refresh the live PR head and exact run/attempt before a
selective retry. Use one recovery owner and at most two additional attempts with
15-minute then 60-minute cooldowns; respect a longer provider retry interval.
Stop after a repeated identical outage until there is evidence of recovery.
Code, prerequisite, authorization and evidence-contract failures need repair,
not retries. Do not rebuild locally to compensate for a hosted outage. A
follow-up must stop acting on merged, closed or superseded work and must not retry
an old head; this does not authorize changing another chat's automation.
If no existing path safely supports the authorized recovery, describe the specific gap and implement a reviewed constrained path rather than bypassing the contract.

## Repair And Validate The Failed Boundary

Reproduce the original failure before accepting a fix.

Exercise the real entrypoint through its output, persistence, package, or runtime boundary. Do not mock away the boundary that failed.

Include relevant empty and nonempty inputs, legitimate provider behavior, and persisted-state transitions. Keep negative controls for unauthorized or incompatible states.

Before full preflight, state the required terminal facts and finish focused positive and negative checks for them. Verify artifact bytes, live registration or metadata, and persisted status separately when the target contract requires each.

Check tool resolution through the child-process environments used by preflight and release CLIs. Carry verified executable paths through PATH when subprocesses invoke bare commands; tool-specific overrides may not survive isolated environments. Resolve setup failures with cheap probes before starting the full suite.

Follow the current preflight requirements before every push. Use the repository's pinned toolchain and evidence contract.

A regression fix and broad preflight serve different purposes; both are required when applicable.

## Own Recovery Through Reconciliation

After merge, continue the already authorized recovery and verification.

Report merge completion when it happens. Report deployment and alert reconciliation as later milestones, with their own evidence.

Verify the required terminal result. A started asynchronous execution is insufficient.

Confirm that the automated observer recognizes the recovery and updates the original incident. A successful observer run alone does not prove the visible alert changed.

If an external service blocks reconciliation, state:

- What has already succeeded.
- Which verification or delivery step remains blocked.
- The exact external failure.
- Which existing automatic mechanism will try again, if verified.
- Who owns any remaining manual action.

Do not create a new scheduled task unless requested. Do not imply that automation exists without checking it.

## Keep Production Alerts Quiet

Production incident alerts represent genuine failures.

Use a stable incident identity and retain the original message identity.

After verified recovery, update the original message to its resolved green state. Do not create a new recovery parent or an automatic recovery reply.

Any warranted thread reply must explicitly disable channel broadcast.

Keep synthetic rehearsal notifications out of the production alert channel. Use isolated tests or private evidence for rehearsal and negative controls.

Do not manually duplicate a notification whose delivery outcome is uncertain. Preserve delivery claims and resolve uncertainty through the existing reconciliation contract.

## Measure Delay Before Optimizing

Measure local validation, PR CI, merge waiting, main CI, deployment, and observer reconciliation separately.

Inspect selected suites, dependency rules, artifact sizes, and repeated transfers.

Propose narrower selection or smaller evidence artifacts through reviewed repository changes. Preserve cross-component dependencies and select all suites when paths or comparison history are unknown.

Do not bypass current preflight or required checks to shorten an incident.

Keep the user informed during active work. Do not describe completed merging as still in progress while waiting on a later milestone.

## Completion Evidence

Report:

```text
Failure category and cause:
Missing regression or prerequisite:
Fix PR and tested revision:
Merge status:
Recovery run and terminal outcome:
Original incident and reconciliation status:
Remaining blocker and owner:
Retained local evidence directory:
```

Separate verified facts from expectations. External outages remain visible; genuine failures must not be converted into success.
