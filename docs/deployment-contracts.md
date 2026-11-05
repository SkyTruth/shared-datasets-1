# Tested production deployment contracts

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

The four ingestion wrappers call `ingestion-deploy.yml` without inherited
secrets. `scripts/ingestion_targets.py` owns the versioned target contract shared
by execution, CI selection, Terraform allowlists, release checks and recovery:
workflow names, image/config variables, resource scopes, canary limits and terminal
phases, release validation, scheduler resume, smoke probes and asset metadata.
Registry defaults are resolved before its GitHub output/environment transport.
Recovery resolves image variables before checking out an older executor.
WDPA code deployment is independent of the month: its scheduled worker builds
the current month, validates both realms and a compatibility sample, and
publishes exact staged bytes with predecessor/generation/hash checks. October
acceptance and promotion documents remain historical evidence.

Each wrapper holds `ingestion-<target>` across prepare/apply/verify. Only `apply`
holds the non-cancelling `prod-terraform-state` queue with `queue: max`; plan,
JSON export, allowlist, permission probe, signed claim and saved-plan apply remain
together. Other targets can prepare images or verify canaries concurrently.
The shared ingestion IAM prerequisite runs once before dependents, with secret
authority bootstrap and live permission checks before runtime binding plans.

Ingestion, narrow Terraform, viewer and CDN runners check out `github.workflow_sha`, then invoke the
`deployment-bootstrap` composite. It authorizes the candidate before checkout,
restores the receipt policy and both target registries from that immutable workflow SHA,
and verifies the tested checkout before authentication. The Terraform service
account and `shared-datasets-production` environment remain unchanged. Jobs
retain environment deployment records: [GitHub's documentation](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/control-deployments#using-environments-without-deployments)
does not explicitly confirm OIDC environment claims with `deployment: false`.

Dataset-usage and viewer prepare and push tested images before entering the
state queue, then recheck replay admission after acquiring it. Their target
queues cover the entire rollout. Dataset-usage carries the signed claim and
prepared image into its separate canary job; viewer checks its ready revision
beside apply. CDN invalidation and route polling follow signed apply in a separate
verification job that records failures even when apply failed. Viewer/CDN use
bounded operational permission probes instead of unconditional IAM sleeps.
These specialized apply scopes and acceptance checks retain their own executors.

The installed sea-ice smoke mounts `scripts/local_ingestion_smoke.py` read-only
and invokes `--sea-ice-only`; local profiling calls the same synthetic build.
The SDK retains its eligibility-aware npm publisher bootstrap, and catalog web
retains dataset-mutation/release-index source authorization. Those contracts
differ from the tested-main deployment bootstrap.

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
