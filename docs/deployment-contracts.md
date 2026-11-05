# Tested production deployment contracts

Use [the ingestion procedure](ci-deployment.md) or
[the Terraform target procedure](terraform-reconciliation.md#change-a-terraform-or-iam-target)
for ordered steps. This document owns shared execution and evidence contracts.

Main-push CI supplies `executor_sha`, `source_run_id` and `source_run_attempt`
after successful `ci-ready`. Manual main entrypoints require the same three
inputs. The trusted verifier checks repository/workflow IDs, main-push event,
exact revision and successful CI attempt through GitHub's API. Automatic callers
must identify their current attempt; supplied inputs alone grant no authority.
A PR/fork revision or fabricated upstream completion cannot pass these checks.

`uv run python scripts/release_contracts.py --target all` checks declared
permissions, bootstrap ordering, immutable reset plans, runtime limits and
protected workflow boundaries offline. It does not prove installed publication
state or propagated IAM; protected jobs verify those live before mutation.
API failures and missing authority remain failures.

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

## Target registries

[`scripts/ingestion_targets.py`](../scripts/ingestion_targets.py) exposes two
resolved views. `DEPLOY_TARGETS` contains the five shared-executor callers:
WDPA, MPAtlas, EAMLIS, sea ice and dataset usage. Execution, image-variable
binding, CI selection and replay use this view. `TARGETS` contains the four
publication producers; publication rollout/freshness and asset
contracts use that narrower view. Telemetry has no release-index or feature-ID
installation requirement.

The eleven-field constructor validates publication/telemetry combinations once.
Workflow and image-variable names derive from target names. Plan policies are
`update`, `install`, `wdpa` or `usage`; canaries are terminal or deferred. Release
checks follow the asset root, and scheduler resume is a reviewed verification
hook. Each job resolves flat environment values from the trusted registry.
Installed-image checks run once in CI before image retention.
The usage target delegates its computed scope and special plan guard to
[`dataset_usage_deploy.py`](../scripts/dataset_usage_deploy.py), preserving its
reader-first activation and removal checks. Recovery resolves image variables
before candidate checkout, including the original `dataset_usage_image`.

[`.github/terraform-targets.json`](../.github/terraform-targets.json) owns stable
synchronization names, exact IAM target scopes, viewer/CDN/usage `plan_scopes` and
named read-only `permission_probes`. Unknown permission targets fail closed.
Execution, offline dependency checks, saved-plan guards and recovery read that
same registry; names preserve historical signed hash identities. CI derives
Terraform source consumers from these scopes rather than copying a path table.
The [resolved CI plan](ci-preflight.md#selection-and-offline-checks) carries the
resulting suites and required `production_image_targets` unchanged to consumers.

## Serialization and protected execution

Four publisher wrappers hold `ingestion-<target>` across prepare/apply/verify.
Usage holds `deployment-dataset-usage` continuously across bootstrap and rollout.
CI's read-only `dataset-usage` gate observes this attempt's attesting apply job;
ingestion and cleanup IAM proceed while `dataset-usage-rollout` keeps the canary
inside that target queue. Failed/deferred applies fail; skipped applies require
signed no-op/supersession evidence. Only shared `apply` holds `prod-terraform-state`
with `queue: max`; plan, JSON, allowlist, permissions, claim and apply stay atomic.
Every queue bounds its runners, including reusable leaves. State writers have
30-minute caps, preparation 20, and short follow-ups 5–20; preview control
allows 90, canonical promotion 120, and catalog publishing 45 minutes.
Canary verification allows 150 minutes for two 62.5-minute windows plus setup;
the read-only apply gate allows four hours of queue contention (245-minute cap).

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
HTTP permission reads retry transport/timeouts/5xx three times with 5/10-second
delays; 4xx and malformed success responses fail immediately. WDPA's saved-plan
guard derives literal CPU, memory and timeout from its Terraform module.

Ingestion, narrow Terraform, viewer and CDN runners check out `github.workflow_sha`, then invoke the
`deployment-bootstrap` composite. It authorizes the candidate before checkout,
snapshots the restored entrypoints’ local import closure from that immutable SHA,
then restores it, package initializers, receipt/auth actions and target registry. It
verifies the tested checkout before authentication. The protected environment
remains required; Terraform and verification reads use separate identities. Jobs
retain environment deployment records: [GitHub's documentation](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/control-deployments#using-environments-without-deployments)
does not explicitly confirm OIDC environment claims with `deployment: false`.

Viewer uses a separate prepare/bootstrap/ready/deploy/verify graph under
`deployment-catalog-viewer`. Prepare pushes tested bytes before the state queue.
Prepare also signs the deployment claim under the workflow’s target lock.
Bootstrap runs and acquires the state queue only for `sync_iam` or manual recovery. Ready probes live dependencies outside the queue.
Deploy reacquires it for the guarded runtime saved-plan apply. Verify checks the
newest ready revision and exact tested digest after releasing the queue, and
signs the terminal outcome even after apply failure. CDN likewise invalidates
cache and polls routes after its signed apply releases the queue. Viewer/CDN
probe missing operational permissions with bounded retries; no blind IAM sleep
replaces those probes. Their specialized acceptance checks keep separate executors.

The installed sea-ice probe uses `local_ingestion_smoke.py --sea-ice-only`;
local profiling shares the same conversion. Installed probes run once in CI,
with memory-backed `/tmp` and production resource limits as
specified in [preflight](ci-preflight.md#image-execution-and-hosted-evidence).
WDPA's worker validates the current month's two realms and compatibility sample
before publishing exact staged bytes; October acceptance remains historical.

CI dispatches the top-level SDK publisher only after successful main `ci-ready`
when the resolved plan selects SDK validation. It re-verifies the tested source
and retains exact-source manual recovery. The
[SDK release procedure](../api/typescript/README.md#maintainer-validation-and-releases)
remains authoritative for package releases. Catalog web retains its separate
dataset-mutation/release-index source authorization.

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

Deployment records bind tested revision, source CI attempt, execution run,
artifact digest and saved-plan scope. Under target serialization, an older valid
revision behind a newer attempt becomes an explicit `superseded` no-op after
verifying every decisive record. It writes no claim and performs no mutation;
a newer failed attempt still blocks older code. Divergent history and failed or
incomplete same-revision attempts require reconciliation. Successful identical
revisions skip before image loading/planning. Different same-code bytes require
reconciliation, except reviewed catalog refreshes/index rebuilds and historical
explicit WDPA scratch builds; their separate publication approval still applies.
Ingestion success requires verified runtime evidence, not merely applied config.

Actions-bot metadata and run URLs do not establish emission. Each claim/outcome
has a canonical receipt binding record/status IDs, target, executor, source CI,
artifact, scope and phase. The protected leaf attests those bytes. Checksum-pinned
GitHub CLI 2.96.0 verifies Fulcio evidence for the repository ID, main ref, exact
caller/leaf revisions, hosted runner and run ID/attempt. New ingestion receipts
require the exact `ingestion-deploy.yml` leaf anywhere in the immutable flat or
nested referenced-workflow graph, bound to head SHA and `refs/heads/main`.
Historical wrapper signatures retain their exact original leaf policy; arbitrary
reusable leaves remain unauthorized. Observers/reconcilers sign with their own
exact protected identities. PR signatures cannot substitute for main receipts.

Durable Deployment API records reconstruct receipts by digest through the
attestation API independently of ordinary Actions artifact retention. Each leaf
first rehearses signing in its current run. Replay/rehearsal checks precede
image staging; a verified signed claim precedes every production mutation.
A failed claim may produce a signed failed/unknown outcome proving ownership,
without authorizing mutation or success. If signing remains unavailable, the
unsigned attempt blocks replay. Validation jobs have read-only receipt tokens;
writers require protected production or explicit main-only caller boundaries.
The SDK retains its npm OIDC publisher boundary. Administrative record deletion
remains outside replay integrity and requires explicit recovery.

Before prepare/apply, active executions defer ingestion. Paused WDPA/EAMLIS
schedules require an explicit canary date. If scheduled work starts after apply,
verification waits up to 62.5 minutes before launching its canary. Cancellation
requires explicit `cancel_running_canary` and must drain first. Inventory errors
or timeouts fail visibly. The runtime-window command owns these rules.

Canaries save their asynchronous execution ID before polling terminal status of
the exact deployed image. Short jobs wait 62.5 minutes, beyond their one-hour
task timeout. WDPA watches ten minutes, then records `verification_pending`
with its exact execution/image for the independent completion observer. Failure,
unknown, applied, pending and verified are distinct; launch alone is not success.
The verification job signs failure even after failed apply. Scheduler resume
follows successful canary, release contract and asset hygiene checks. Completion
observers and Cloud Run failure alerts remain active; terminal failure/unknown
fails verification.

## Selective recovery

Dispatch `deployment-recovery.yml` on main with the failed/interrupted record's
`deployment_id`. It validates original authority, checks out the original tested
executor, refreshes only the captured scope and requires a complete no-change
plan. Ingestion also requires the original live digest and latest exact-image
execution's terminal success. Registered IAM/monitoring targets require their
original no-change scope. Later attempts, drift, partial applies, missing scope
and failed executions block reconciliation; no apply or dataset writes occur.
CDN, viewer and catalog records still need reviewed target-specific recovery.

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
