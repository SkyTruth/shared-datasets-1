# CI and deployment recovery

This is the ordered procedure for failed validation, deployment and incident
recovery. Load the [recovery skill](../.claude/skills/ci-incident-recovery/SKILL.md)
for agent routing. Keep code merged, operation completed, observer verified and
original alert reconciled as distinct milestones.

1. **Establish identity and ownership.** Record repository, run/attempt, event,
   executor SHA, target, failed job/step and error signature. Read that actual
   attempt, not an older alert title. Name one recovery owner and the original
   incident/Slack parent. For overlapping work, use
   [agent integration](agent-integration.md) and obtain an explicit handoff;
   this guide grants no cross-thread messaging or mutation authority.
2. **Locate the failed boundary and last confirmed mutation.** Inspect signed
   records, live state and exact image/plan/object-generation evidence. Classify
   the failure using the table below. Unknown impact stays unknown; cancellation
   does not prove Cloud Run or remote writes stopped.
3. **Repair defects and prerequisites before retrying.** Exercise the changed
   runtime/serialization boundary with positive fixtures and negative controls.
   Complete [publication readiness](publication-readiness.md) for a real release,
   or complete synthetic batches for publication-code fixes without one. Resolve
   pinned tools through the child process PATH and finish
   [final validation](ci-preflight.md#validate-before-push). Missing or failed
   checks and unexpected relevant skips never pass.
4. **Choose the existing protected recovery.** Establish target outcome, replay
   eligibility, trigger, tested source, terminal proof and observer action. Follow
   [selective recovery](deployment-contracts.md#selective-recovery) for supported
   deployment/SDK records, [state reconciliation](terraform-reconciliation.md#manual-state-only-reconciliation)
   for bookkeeping, or the owning job's publication recovery. Preserve original
   approval/executor/artifact, immutable plan, generations, locks and receipts.
   Ancestry alone cannot authorize a new executor or replay over a newer attempt.
   If no safe path exists, implement a reviewed constrained path.
5. **Observe terminal completion and reconcile delivery.** Continue already
   authorized recovery after merge. Verify actual bytes/live state, persisted
   outcomes and observer proof separately. An async launch or successful observer
   run alone is insufficient. Follow the
   [Slack recovery contract](slack-incidents.md#recovery-contract) to resolve the
   original parent; native incidents require
   [authoritative Monitoring closure](alert-routing.md#editable-monitoring-messages).
   Report changed paths/generations, proof, owner/blocker and retained evidence.
   Do not create schedules or claim automatic retries without authorization and
   verified configuration.

`deployment-recovery.yml` retains its trusted main working directory;
`GIT_DIR` binds revision reads to `original-executor/`. Trusted retry/reconciler scripts
inspect the original Terraform configuration or SDK bytes; executor scripts
never replace recovery policy. Infrastructure plans use dedicated recovery credentials,
refresh live resources with `-lock=false` and never apply or write state.
After the automatic **GitHub workflow identity sync** establishes the production
WIF provider, manually run protected `terraform-state-read-iam-sync.yml` from the
exact successful main-push CI source triple: **PR plan identity IAM bootstrap**
precedes **PR plan identity provisioning**. That manual scope installs the PR
planning roles and the recovery identity, metadata/IAM-policy reads (including
Secret Manager policy refresh), and exact default-state read/workspace-list grant.
Recovery is unavailable until this provisioning succeeds; merge alone does not
activate it. Unverified custom-role permissions stay out of automatic scopes.
Only `deployment-recovery.yml` can assume this identity. State may contain the
Terraform-managed Monitoring Slack channel auth token; observers retain no state
read grant. Recovery has no state writes, Secret Manager version access or
deployment authority.
Unsupported metadata reads fail closed and require reviewed recovery. Receipt
verification requires pinned `gh`; a version failure identifies the installer.
Historical WDPA records without the original image configuration digest fail closed
before recovery authentication. A reviewed ordinary rollout must establish a new
signed record with that digest; never guess it or rewrite historical evidence.

## Failure classification

| Failed boundary | Required next action |
| --- | --- |
| Reproducible code/test or serialization defect | Repair the producer/boundary and add a meaningful regression/negative control before full validation. |
| Missing prerequisite, denied IAM or unsupported scope | Repair the reviewed dependency/permission contract; another unchanged deployment cannot establish readiness. |
| Transient read-only hosted validation outage | Refresh the live PR head and exact run/attempt; the owner may retry failed jobs at most twice, after 15 then 60 minutes or a longer provider interval. |
| Same read-only outage after bounded retries | Stop identical retries until new provider recovery evidence appears; do not rebuild locally or manufacture a push to compensate. |
| Mutation started, partial apply or uncertain transport result | Inspect durable records and live effects, then use protected target-specific reconciliation; never blanket-rerun. |
| Stale/missing authority, artifact or signed evidence | Preserve the failed record and obtain reviewed recovery; a newer success cannot silently replace missing proof. |
| Failed safety setup/discovery or incomplete health read | Inspect its retained health report; setup and health findings use change-plus-daily reminders. Missing heartbeat series means absence detection is unestablished. |
| Uncertain Slack send/claim | Use [delivery reconciliation](slack-incidents.md#configure-and-reconcile); inspect the exact parent/checkpoint instead of blindly resending. |

Follow-ups belong to the same owner and must refresh live head/run identity.
Stop on merged, closed or superseded work; never retry an old head or modify
another chat's automation. Measure validation, review, deployment and delivery
reconciliation delays separately.

## Interrupted publication

Preserve source inputs, executor contract, claims, reservations and checkpoints.
Never delete ownership, rewind IDs, change the release date to bypass a claim,
or overwrite partial releases. Follow the owning ingestion README and
[feature-ID recovery](feature-id-reset-installation.md).

For a compatible unfinished publication, rerun the entire original eligible main
workflow with its executor and plan (GitHub limits: 30 days / 50 attempts).
Apply-only reruns can retain stale envelopes. A newer executor may verify an
activated reset and finish its journal, but cannot create further reset objects
under a changed contract. Missing original authority or evidence needs reviewed
recovery, not a fallback mutation path.

Terraform retries are separately bounded to the exact safe cases in
[the saved-plan contract](deployment-contracts.md#serialization-and-protected-execution).
The [incident lifecycle](slack-incidents.md) owns persistence, signed verification,
claims and transport configuration; [alert routing](alert-routing.md) owns native
monitoring and health cadence.
