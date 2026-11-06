---
name: ci-incident-recovery
description: Use when investigating a failed GitHub Actions run, recovering a failed deployment or publication, reconciling an unresolved workflow incident, or diagnosing repeated recovery failures or excessive incident-related merge time in shared-datasets-1.
---

# CI Incident Recovery

Follow [CI and deployment recovery](../../../docs/ci-recovery.md) in order.
It owns identity/owner discovery, failure classification, bounded retries,
protected recovery selection, terminal verification and original-parent closure.
Load only the linked contract for the failed boundary.

This skill grants no Git, production, Slack or cross-thread messaging authority.
Continue already-authorized recovery through its terminal outcome; ask only for
missing authority. Use [agent integration](../../../docs/agent-integration.md)
for overlaps and accepted handoffs, and load invariant-first-engineering for
code or persisted-state fixes. Load protected-terraform-apply before production
Terraform/CDN planning, plus ingestion, publishing, preview or GCS skills when
those surfaces are involved.

Keep code merged, operation completed, observer verified and original incident
reconciled as separate facts. An asynchronous launch or healthy observer alone
cannot establish completion. Do not invent automatic retries or schedules.

Use [Slack persistence/delivery](../../../docs/slack-incidents.md#persistence-and-delivery)
for uncertain claims and original parents, and
[alert routing](../../../docs/alert-routing.md) for native Monitoring closure.
Preserve complete **Decision needed** requests and record accepted decisions in
the original thread without broadcast. Use isolated fixtures; never deliberately
fail production or send synthetic channel messages.

Report the failure/cause, missing test or prerequisite, tested revision/PR,
merge state, recovery run and terminal proof, original incident and visible
reconciliation, remaining blocker/owner and retained local evidence directory.
