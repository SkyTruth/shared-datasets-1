# Tested production deployment contracts

[The production deployment procedure](ci-deployment.md) is the entry point for
all changes. This reference owns shared execution and evidence contracts.

`uv run python scripts/release_contracts.py --target all` checks declared
permissions, bootstrap ordering, immutable reset plans, runtime limits and
protected workflow boundaries offline. It does not prove installed publication
state or propagated IAM; protected jobs verify those live before mutation.
API failures and missing authority remain failures.

Identity activation follows the [two-phase rollout](github-identity-rollout.md):
phase 1 preserves legacy access and adds per-workflow identities; phase 2 removes
legacy grants only after successful main runs prove each new identity.
The saved-plan guard blocks deletes and replacements for every non-retirement IAM
sync, including workflow identity sync. Exact obsolete-resource retirement and
temporary-authority revocation scopes retain their separate deletion contracts.
The six WDPA/operational retirement jobs in `scratch-cleanup-iam-sync.yml` run
only on `workflow_dispatch` from `main` by `jonaraphael`. CI delivery still runs
the non-retirement scratch-cleanup IAM sync. Owner dispatch retains tested-main
provenance, protected execution, exact deletion scopes and revocation after any
grant attempt; it does not renew the expired temporary retirement authority.

## Images and execution

`production_image_contracts.py` builds actual Linux production Dockerfiles and
exercises installed imports, native CLIs, the WDPA input probe and small build
fixtures. GDAL/NumPy checks use `gdal_calc.py`'s actual shebang interpreter.
These checks publish no images and access no production datasets. CI retains
exact tested images with SHA, config IDs, archive hashes and ordered rootfs
hashes. Deployment loads those bytes from the successful source attempt without
rebuilding. Docker-save normalization preserves config/rootfs bytes; the pushed
registry manifest must retain the tested config ID before Terraform receives
its immutable digest. Missing, expired or mismatched evidence fails closed.

## Serialization and protected execution

Publisher wrappers hold `ingestion-<target>` across prepare/apply/verify; usage holds
`deployment-dataset-usage` across bootstrap/rollout. CI's `dataset-usage` reusable
caller owns the complete rollout. Ingestion and cleanup IAM read the existing
canonical bucket through a data source, keeping bucket logging/CORS out of their
plans and allowing them to proceed in parallel with usage verification. A second
usage revision cannot apply before the first finishes verifying its image. There
is no polling runner or nested-job-name protocol. Only shared `apply` holds `prod-terraform-state` with
`queue: max`; plan, JSON, allowlist, permissions, claim and apply stay atomic.
Every queue bounds runners, including reusable leaves: state writers 30 minutes,
preparation 20, follow-ups 5–20, preview 90, canonical promotion 120, catalog publishing 45.
Canary verification allows 150 minutes for two 62.5-minute windows plus setup.

Every production Terraform writer uses that same literal job-level state queue,
including reusable IAM syncs and CDN/viewer jobs. Do not put the matching group
on a reusable caller or parent workflow. Preview uses separate state and queue.
Drain active/queued runs on the old configuration before changing serialization.
`queue: max` retains up to 100 pending jobs; overflow still cancels work and
requires an owned redispatch from reviewed main after capacity is available.

[`terraform_retry.sh`](../scripts/terraform_retry.sh) defaults to five attempts
with 15/30/45/60-second delays. Lock-acquisition failures may retry before
mutation. Only `plan` also retries recognized TLS/handshake timeouts and provider
500/502/503/504 reads. Permission/authentication errors, invalid or stale saved
plans and lock-release errors fail immediately; apply transport errors never
retry because writes may already have happened. Never regenerate an apply plan
outside its allowlist/permission sequence to bypass staleness.
HTTP permission reads retry transport/timeouts/5xx three times (5/10-second delays);
4xx/malformed responses fail immediately. WDPA's saved-plan guard reads Terraform's CPU/memory/timeout.

Ingestion, narrow Terraform, viewer and CDN runners check out `github.workflow_sha`, then invoke the
`deployment-bootstrap` composite. It verifies owner acceptance, immutable main context and tested CI authority before authentication.
When the executor differs from the trusted workflow SHA, it snapshots the restored entrypoints’ local import closure,
checks out the exact executor and restores trusted code, package initializers, receipt/auth actions and the target registry.
An exact HEAD assertion follows that checkout. Equal SHAs retain the already verified trusted checkout directly. The protected environment
remains required; Terraform and verification reads use separate identities. Jobs
retain environment deployment records: [GitHub's documentation](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/control-deployments#using-environments-without-deployments)
does not explicitly confirm OIDC environment claims with `deployment: false`.

Viewer retains `deployment-catalog-viewer` across prepare/bootstrap/ready/deploy/verify.
Prepare pushes tested bytes outside the state queue and signs its claim under the target
lock. Bootstrap queues only for `sync_iam` or manual recovery; readiness stays outside.
Deploy queues the guarded saved-plan apply; verification checks newest ready revision/
tested digest and signs even after failure outside the queue. CDN invalidation/route
polling likewise follows signed apply outside the queue. Both reuse anchored trusted bootstrap steps and bounded permission
probes instead of blind IAM sleeps; specialized acceptance keeps separate executors. CDN route checks run the trusted
standard-library verifier with redirects disabled, bounded retries, and a canonical
metadata sidecar selected from the latest or historical release index.

[Preflight](ci-preflight.md#image-execution-and-hosted-evidence) owns once-per-CI installed
probes with production limits and memory-backed `/tmp`; sea ice and local profiling share
`local_ingestion_smoke.py --sea-ice-only`. WDPA validates both current-month realms and
the compatibility sample before publishing staged bytes; October acceptance is historical.

Only successful main `ci-ready` selecting SDK validation dispatches the top-level publisher.
It re-verifies tested source and supports exact-source manual recovery; the
[SDK release procedure](../api/typescript/README.md#maintainer-validation-and-releases) governs releases.
Catalog web retains separate dataset-mutation/release-index source authorization.

Immediately before each apply, `deployment_permissions.py --target <target>
--plan-json <plan.json>` checks every changed resource's actual action and
before/after identity. Resource-specific probes preserve IAM conditions. New
resources require known parent authority and explicit creation dependencies;
unknown identities, deferred changes and unsupported mutation classes fail.
Unchanged resources do not require create/delete authority. CDN cache
invalidation remains required even on a no-change plan. Compute probes first
check method-specific list permissions; see the [probe-call contract](compute-probe-call-contract.md).
Mutation authority does not establish runtime acceptance or installed state.

## Records, receipts and runtime outcomes

Records bind tested revision, CI/execution attempts, artifact digest and the full planned
resource scope, including unchanged addresses needed for canary reconciliation.
Under the target lock, verified decisive records supersede older revisions without claims
or mutation; even newer failed attempts block them. Divergence or incomplete/failed
same-revision attempts require reconciliation. Successful identical revisions skip before
staging/planning. Different same-code bytes require reconciliation except reviewed catalog/
index refreshes and historical WDPA scratch builds; separate publication approval remains.

Every new ingestion claim runs saved-plan apply and its own canary, including unchanged
plans. Older executions cannot verify a new claim. Same-revision success and supersession
still skip before claiming. Only sea ice requests the optional scheduler resume hook.

Canonical receipts bind record/status IDs, target, executor, source CI, artifact, scope
and phase. Actions-bot metadata/URLs alone prove nothing. Protected leaves attest bytes;
checksum-pinned GitHub CLI 2.96.0 verifies Fulcio repository ID, main ref, exact caller/leaf
revisions, hosted runner and run ID/attempt. Ingestion requires `ingestion-deploy.yml` in
the immutable flat/nested graph bound to head SHA/main. Historical wrappers retain their
original leaf policy; arbitrary leaves/PR signatures remain unauthorized. Observers and
reconcilers use their exact protected identities.

Durable API records reconstruct receipts by digest independently of artifact retention.
Leaves rehearse signing before staging and verify signed claims before mutation. Signed
failed/unknown outcomes can prove ownership after claim failure, never success/mutation;
unsigned attempts block replay. Validation tokens stay read-only; writers need protected
production or main-only gates. SDK npm OIDC stays separate; record deletion needs explicit recovery.

The runtime-window command defers prepare/apply for active executions and requires an
explicit canary date for paused WDPA/EAMLIS schedules. Preparation first admits and pushes
the tested image; closed prepare/apply windows retain a signed `deferred` pending record.
Deferrals authorize no apply and may retry after receipt verification. The image-lag
check includes them; a later deployment of that digest clears the finding. Post-apply verification waits up
to 62.5 minutes for scheduled work. Explicit `cancel_running_canary` must drain first;
inventory errors/timeouts fail visibly. Canaries save their asynchronous execution ID
and poll that exact deployed image: short jobs wait 62.5 minutes beyond the one-hour
task timeout; WDPA watches ten minutes then records `verification_pending` with exact
execution/image for the independent observer. Launch alone is not success: failed,
unknown, applied, pending and verified stay distinct. Verification signs failure even
after failed apply. Scheduler resume follows canary, release and hygiene success.
Completion observers/Cloud Run failure alerts stay active; terminal failure/unknown fails.

## Selective recovery

Dispatch `deployment-recovery.yml` on main with the failed/interrupted `deployment_id`.
It validates original authority, checks out the tested executor, refreshes only captured
scope and requires a complete no-change plan. Ingestion requires the original live
digest and latest exact-image execution's terminal success; registered IAM/monitoring
requires its original no-change scope. Later attempts, drift, partial applies, missing
scope or failed executions block reconciliation; no apply/dataset writes occur.
CDN/viewer/catalog still need reviewed target-specific recovery.

SDK reconciliation retains the trusted checkout and reads the original executor
separately. It verifies source CI, producing job, retained archive and package
digest, then checks npm version/integrity and byte-for-byte tarball equality.
Missing evidence, different bytes/executor or later attempts fail closed; it
never republishes. Normal registry confirmation forces online cache checks.
Manual dispatch cannot erase failed records or authorize blanket retries.

After runner/network failures, inspect persisted records and whether mutation
began before retrying safe reads/validation. Preserve publication ownership,
original inputs and executor contracts: never delete claims, reset counters or
choose a new date to bypass an interrupted publication transaction.
