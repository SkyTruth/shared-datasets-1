# Reviewed operational Slack cutover evidence

There is no production cutover evidence checked in yet. The native route is the
default; synthetic notifications and invented cycle/deployment identifiers are
not acceptable evidence. Keep the cutover PR draft until the real prerequisites
below are satisfied.

`catalog/operational-slack-rollout.json` is the reviewed switch. `mode: native`
retains native operational delivery. A later reviewed change to `mode: unified`
must reference an exact JSON document here by repository path and SHA-256.
The protected reusable Terraform workflow verifies the document from its exact
tested main revision before claiming or applying the exact saved plan.

The cutover document has schema
`shared-datasets-operational-slack-cutover-v1` and these fields:

| Field | Required evidence |
| --- | --- |
| `collector_contract_sha256` | Current signed cycle protocol hash, including producers, collector, adapter, worker and eligibility gate. |
| `policy_sha256` | Exact current classification registry hash. |
| `policy_ids` | Every Terraform policy address and its exact installed `projects/shared-datasets-1/alertPolicies/…` identity. |
| `cycles` | Ordered `{deployment_id, manifest_sha256}` references to at least 24 elapsed hours of signed, complete, provenance-complete shadow cycles; no public cloud delivery, missing predecessor, coverage gap or interval over ten minutes. |
| `runtime_deployments` | Exactly `wdpa-monthly`, `eamlis-monthly`, `sea-ice-daily`, `mpatlas-monthly`, each with `{deployment_id, runtime_sha256}` binding a signed applied/verified exact image to the reviewed runtime bridge source. |

The gate reads every referenced signed manifest and part, verifies main ancestry,
and requires corresponding observed heartbeat samples after creation or last
mutation of the current absence policy. The final shadow sample must be at most
ten minutes old. First seed and verify the heartbeat after installing the absence
policy; Monitoring cannot establish absence coverage before an initial sample.
Freshness is checked again after signature enumeration. The worker retains the
verified IDs/timestamps with hashes of the exact executor, saved plan, config and
target scope, then rechecks those bindings and the current clock immediately
before apply, after signing the deployment claim. Long verification cannot
silently extend the ten-minute freshness limit.

Immediately before the saved-plan apply, a complete live read-only routing audit
must match installed before-values. Independent canonical-write/delete security
and 30-minute pipeline-health policies must remain enabled with native channels.
Both exact filtered Logging views must also match the reviewed Terraform filter;
a known view identity alone cannot prove the intended access or coverage boundary.
This check uses existing protected setup metadata-read authority and grants no
additional observer access. The Terraform view boundary participates in the
signed cycle contract hash; later unobserved live view drift remains a limitation
until a dedicated narrowly scoped metadata reader is installed for every cycle.
Unknown enabled Slack-bound policies are discrepancies for review, never targets
for automatic disabling. Detection changes cannot ride with channel removal.

The gate additionally reads each current job and every execution page. Each
current job and every nonterminal execution must use the receipt's exact image
and explicit `SHARED_DATASETS_OPERATIONAL_DELIVERY_MODE=unified`. An older image,
native execution, partial enumeration or uncertain receipt prevents cutover.
Deploy these settings using the reviewed protected job workflows first. If the
policy sync runs before those deploys finish, it safely refuses; the integration
owner re-dispatches it with the same exact tested main identity after terminal
deployment verification. Never resolve sequencing with a local apply.

A config merge alone cannot activate cloud delivery. The notification worker
retains shadow mode until it verifies the latest signed successful protected
policy-sync receipt, its complete policy scope, matching immutable cutover
reference and exact gate protocol. It rechecks that activation before delivery,
finalizing the signed checkpoint and emitting the heartbeat. An invalid, failed
or uncertain later attempt cannot be replaced by an older successful receipt.

Runtime webhook access is a separate later step. Set
`retire_runtime_webhook_access: true` only with an immutable retirement document
of schema `shared-datasets-runtime-webhook-retirement-v1`. Its `cutover_sha256`
must match the existing cutover document hash and its `runtime_deployments` must
match the exact drained deployments. The gate rechecks live routing, fresh
post-policy pipeline heartbeat, signed original cutover and current image drain.
It only permits deletion of the two registered WDPA/e-AMLIS secret-accessor
bindings. The workers' webhook secret mounts can then be removed by their
protected deployments. No runtime secret read is part of verification.

For rollback, review `mode: native`, `retire_runtime_webhook_access: false`, and
clear the retired-access reference. This config selects policy sync, ingestion
IAM and all four runtime deployments. Restore in this order through protected
workflows: operational policy channels first, retired runtime accessor bindings
second if needed, then native job mode and secret mounts. Verify every terminal
result and a fresh live routing audit; explicitly dispatch the exact tested-main
protected workflows if automatic dependency ordering leaves a prerequisite
pending. There is no local-apply fallback.

Native mode makes the cloud worker shadow immediately. Until channel restoration
is verified there is a transitional gap in public operational delivery, while
signed collection retains observations and independent security/pipeline health
stay native. The integration owner must retain supervision throughout this gap,
check in at least every ten minutes, and escalate unresolved restoration by the
original two-hour supervision deadline; a rollback or new commit never resets
that clock. A lost owner or incomplete collection cannot be treated as verified
recovery. Legacy webhook parents, `claimed` markers and uncertain sends remain
audit references; rollback never resends or adopts them.

After activation, retain seven days of classified operational evidence: public
parents, duplicates, automated recovery, owner-held issues, expired supervision,
delivery uncertainty, coverage gaps and time to actionable escalation. Do not
publish routine measurements into the shared channel.
