# Reviewed operational Slack cutover evidence

There is no production cutover evidence checked in yet. Dormant native-default
support may merge after the cloud observation foundation. With `mode: native`,
null evidence and runtime secret retirement disabled, that merge preserves native
operational routes and webhook access; normal protected deployments may still run.
Keep the later activation PR/config change draft until the real prerequisites
below are reviewable. Synthetic notifications and invented cycle/deployment
identifiers are not acceptable evidence. The reviewed activation change must also
update the explicit native-default test fixture alongside its real immutable evidence.

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
| `audit_setup_deployment_id` | Exact signed successful protected Operational observation setup receipt proving the current observer Terraform and unexempted Run DATA_WRITE audit configuration. Shadow history starts after its applied timestamp. |
| `policy_ids` | Every Terraform policy address and its exact installed `projects/shared-datasets-1/alertPolicies/…` identity. |
| `cycles` | Ordered `{deployment_id, manifest_sha256}` references to at least 24 elapsed hours of signed, complete, provenance-complete shadow cycles; no public cloud delivery, missing predecessor, coverage gap or completion interval over 25 minutes. This leaves five minutes before the independent 30-minute heartbeat absence alarm; final evidence freshness remains ten minutes. |
| `runtime_deployments` | Exactly `wdpa-monthly`, `eamlis-monthly`, `sea-ice-daily`, `mpatlas-monthly`, each with `{deployment_id, runtime_sha256}` binding a signed applied/verified source anchor to the reviewed runtime bridge contract. The gate independently resolves the latest signed deployment and exact current unified image under that same contract. |

The gate reads every referenced signed manifest and part, verifies main ancestry,
and requires at least 24 hours of history after the verified audit setup applied
timestamp, with corresponding observed heartbeat samples after creation or last
mutation of the current absence policy. Live Run or all-services DATA_WRITE audit
capture must also remain enabled without exemptions. The gate also verifies a
fresh signed rolling 24-hour shadow window under the same reviewed protocol,
policy and main ancestry. A fixed review snapshot cannot
establish current pipeline health; the latest cycle must be at most ten minutes
old, and an incomplete or failed latest cycle cannot fall back to older success.
First seed and verify the heartbeat after installing the absence policy; Monitoring cannot establish absence coverage before an initial sample.
Freshness is checked again after signature enumeration. The worker retains the
verified IDs/timestamps with hashes of the exact executor, saved plan, config and
target scope, then rechecks those bindings and the current clock immediately
before apply, after signing the deployment claim. Long verification cannot
silently extend the ten-minute freshness limit.

Immediately before the saved-plan apply, a complete live read-only routing audit
must match installed before-values. Independent canonical-write/delete security
and 30-minute pipeline-health policies must remain enabled with native channels.
All eight exact filtered Logging views must also match the reviewed Terraform filter;
a known view identity alone cannot prove the intended access or coverage boundary.
This check uses existing protected setup metadata-read authority and grants no
additional observer access. The Terraform view boundary participates in the
signed cycle contract hash; later unobserved live view drift remains a limitation
until a dedicated narrowly scoped metadata reader is installed for every cycle.
Unknown enabled Slack-bound policies are discrepancies for review, never targets
for automatic disabling. Detection changes cannot ride with channel removal.

The gate additionally reads each current job and every execution page. Reviewed
source anchors can come from native-compatible shadow deployments; they do not
pre-name future deployment IDs. After the unified config merges, the protected
job workflows deploy all four jobs. The gate resolves each latest signed terminal
deployment, verifies its source descends from the reviewed anchor with the same
bridge contract, and records both identities in its proof. Each current job and
every nonterminal execution must use that exact current image and explicit
`SHARED_DATASETS_OPERATIONAL_DELIVERY_MODE=unified`. An older image, native
execution, partial enumeration, failed latest attempt or uncertain receipt
prevents cutover. If policy sync runs before those deploys finish, it safely
refuses; the integration owner re-dispatches it with the same exact tested main
identity after terminal deployment verification. MPAtlas CI deliberately depends
on policy-sync success; for this staging transition, dispatch the existing protected
`mpatlas-monthly-deploy.yml` with the exact tested main revision and its successful
`ci-ready` run/attempt, verify deployment and drain, then dispatch
`cron-alert-policy-sync.yml` with that same identity. Preserve the normal CI
prerequisite. Never resolve sequencing with a local apply.

A config merge alone cannot activate cloud delivery. The notification worker
retains shadow mode until it verifies the latest signed successful protected
policy-sync receipt, its complete policy scope, matching immutable cutover
reference and exact gate protocol. It rechecks that activation before delivery,
finalizing the signed checkpoint and emitting the heartbeat. An invalid, failed
or uncertain later attempt cannot be replaced by an older successful receipt.

This check also applies to native-config workers. Cloud coverage and health wait
for a supported signed installed native receipt; an old queued native worker
cannot emit health after unified policy installation. The writer rechecks after
predecessor verification and immediately before manifest commit, and the heartbeat
step rechecks after durable signature verification. The fenced v2 heartbeat series
is shared by shadow and unified mode. The legacy descriptor stays managed, but
old-worker samples cannot keep the current absence policy healthy. Install the
final protected v2 descriptor and absence policy before enabling collection, seed
only from a genuine complete cycle, and collect a fresh compatible 24-hour shadow
window. Pre-fence cycles, old metric samples and unsigned setup are ineligible.

Runtime webhook access is a separate later step. Set
`retire_runtime_webhook_access: true` only with an immutable retirement document
of schema `shared-datasets-runtime-webhook-retirement-v1`. Its `cutover_sha256`
must match the existing cutover document hash. Its `runtime_deployments` may name
fresh signed source anchors after routine redeployment. The gate independently
verifies the latest compatible current images and execution drain, together with
live routing, fresh post-policy pipeline heartbeat and the signed original
cutover. It does not require the original image to remain deployed forever.
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

Native rollback pauses cloud coverage and health until a supported signed native
policy restoration matches the worker protocol and configuration. It then resumes
shadow collection. Until channel restoration is verified there is a transitional
gap in public operational delivery; retained signed observations remain audit
evidence and independent security/pipeline health stay native. The integration
owner must retain supervision throughout this gap,
check in at least every ten minutes, and escalate unresolved restoration by the
original two-hour supervision deadline; a rollback or new commit never resets
that clock. A lost owner or incomplete collection cannot be treated as verified
recovery. Legacy webhook parents, `claimed` markers and uncertain sends remain
audit references; rollback never resends or adopts them.

After activation, retain seven days of classified operational evidence: public
parents, duplicates, automated recovery, owner-held issues, expired supervision,
delivery uncertainty, coverage gaps and time to actionable escalation. Do not
publish routine measurements into the shared channel.
