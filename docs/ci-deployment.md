# Change or add an ingestion deployment

This is the ordered procedure for deployment changes, including the private
dataset-usage collector. Start with [AGENTS.md](../AGENTS.md) and the
[deployment skill](../.claude/skills/deploy-scheduled-ingestion/SKILL.md).
For Terraform changes, also load the
[protected Terraform skill](../.claude/skills/protected-terraform-apply/SKILL.md).
Read the selected job's runtime README when changing its data or processing
behavior; source-specific operations are linked below.

Protected cloud identities are bound to exact workflow filenames on main in
[`github-workflow-identities.json`](../terraform/envs/prod/github-workflow-identities.json).
Tests compare this generated list with registered executors and authentication
steps. Reusable jobs use `job_workflow_ref`; standalone jobs use `workflow_ref`.
The provider also requires this repository, main and the protected environment.
The Terraform SA's authoritative WIF role binding removes its former shared
environment-wide subject grant. Artifact Registry IAM sync provisions the
identity-manager bootstrap, then the identity scope before its writer phase.
That prerequisite makes the new credentials available before dependent rollouts.

Observer reads have no mutation, IAM administration, Terraform state or secret-version access.
Image pushes, canary launch/cancellation, schedule control and cache invalidation
use the separate runtime identity; verification restores the observer afterward.
The two reconcilers need a separate identity with private incident-state writes.
Only the safety workflow's heartbeat identity can emit heartbeat logs. None of
these identities changes receipt signing, tested-image digests or dataset approval.

1. **Declare the change and its owner.** Name the target, affected resources and
   intended runtime/data outcome. For overlapping PRs, record dependencies and
   one integration owner using [agent integration](agent-integration.md).
   For a real dataset publication, complete [publication readiness](publication-readiness.md)
   before expensive builds or CI; a synthetic fixture cannot settle release decisions.
2. **Keep source logic in the owning job.** Follow [job boundaries](../ingestion/README.md)
   for package layout, shared helpers, release/check-in semantics and stable live
   surfaces. New assets/pipelines also need the admission evidence and feature
   identity decision table required by AGENTS.md before publication. Preserve
   existing publication claims and installed identity state.
3. **Declare the deployment once.** Update `DEPLOY_TARGETS` in
   [`scripts/ingestion_targets.py`](../scripts/ingestion_targets.py) for workflow,
   resource scope, runtime window, canary mode and named Python verification hook.
   The constructor
   rejects incompatible publication/telemetry policies; image variables and caller
   filenames derive from the target name. The executor declares the exact typed
   environment keys it consumes; missing or renamed keys fail before authentication.
   Native and synthetic image checks run
   once in CI before retention. Its `TARGETS` view includes only dataset publishers; telemetry must
   not acquire publication requirements. Use a thin `<target>-deploy.yml` wrapper
   calling [`ingestion-deploy.yml`](../.github/workflows/ingestion-deploy.yml).
   See [registry ownership](deployment-contracts.md#target-registries) before
   changing a shared field; avoid executor branches keyed to target names.
4. **Connect prerequisites and validation.** Wire the wrapper into
   [`ci.yml`](../.github/workflows/ci.yml) with explicit `needs` for registry,
   IAM and monitoring prerequisites. Add or extend Terraform/IAM scopes through
   [the target-change procedure](terraform-reconciliation.md#change-a-terraform-or-iam-target)
   when needed. Declare the production Dockerfile and its Terraform image consumer;
   [`tested_image_bundle.py`](../scripts/tested_image_bundle.py) derives the recipes.
   Register installed probes under `image_checks` in [the registry](../.github/terraform-targets.json).
   Its per-target `deployments` declarations own additional inputs and Terraform
   prerequisites; resource scopes and image COPY inputs are derived.
   Check that the [resolved CI plan](ci-preflight.md#selection-and-offline-checks)
   includes the target's suites and exact image even for a hold-only release.
5. **Exercise the changed boundary.** Run focused positive and negative tests
   for selection, prerequisites, saved-plan scope and runtime verification.
   Changes to shared ingestion helpers need every importing production job's
   tests. Test native conversion changes through the production chain; use a
   fractional source sandbox for schema/format bugs and complete synthetic
   batches for publication-code changes without a pending release. Size CPU,
   memory, timeout and scratch for the full source, and preserve the
   [installed-image checks](deployment-contracts.md#images-and-execution).
   Installed probes use memory-backed `/tmp` under those limits. WDPA's 12-hour
   task timeout allows 3 h 39 m above the observed 8 h 21 m maximum. Release at
   most one held ingestion target per change so each rollout can be observed.
6. **Validate the final revision and obtain review.** Follow
   [validation before push](ci-preflight.md#validate-before-push) once the code,
   prerequisites and focused checks are ready. Include expected resource actions,
   allowlist coverage and unverified live prerequisites in the PR. Production
   Terraform changes require `jonaraphael` review, merge to `main` and the
   protected `shared-datasets-production` workflow; local plans are review
   evidence only. Git operations still require the authorization in AGENTS.md.
7. **Observe the protected rollout.** Successful main `ci-ready` supplies the
   tested source to the selected wrapper. First-time repository/IAM bootstrap
   must finish before pushing the retained image. Read
   [queue ownership](deployment-contracts.md#serialization-and-protected-execution)
   before changing concurrency: prepare and canary verification stay outside
   the state queue. Automatic usage deploys emit a signed applied receipt before
   their separate canary job; ingestion and cleanup IAM depend on that apply. Both
   phases retain the `deployment-dataset-usage` target queue, outside the state queue.
   Split verification checks the exact target, image, source and execution attempt
   against the signed receipt before authentication. Other ingestion wrappers
   keep the combined rollout. Manual recovery/dispatch requires `executor_sha`,
   `source_run_id` and `source_run_attempt` from the exact successful main-push
   CI attempt. A deferred rollout is not a completed deployment.
8. **Verify and report completion.** Use the workflow's captured canary ID;
   do not launch an additional execution when it already supplied the canary.
   Confirm the [terminal runtime outcome](deployment-contracts.md#records-receipts-and-runtime-outcomes),
   release indexes and current `latest/` metadata for publishers, or private
   report/activation checks for usage telemetry. Confirm project/region failure
   coverage still includes the job; when changing alerts, follow
   [alert verification](alert-routing.md#execution-and-delivery-verification).
   Report image digest, resource actions, execution/result, changed remote paths
   and anything unverified. Route failures to [recovery](ci-recovery.md).

## Job-specific operations

Use [job operations](../.claude/skills/deploy-scheduled-ingestion/references/job-operations.md)
only for pause/resume, teardown or a selected job's activation exceptions.
WDPA monthly operation and owned recovery live in
[the monthly runbook](wdpa-monthly-runbook.md); first identity installation lives
in [the reset runbook](feature-id-reset-installation.md).
Usage telemetry's reader-first activation and cost gates live in
[its README](../ingestion/dataset_usage/README.md#activation-and-cost).

## Automatic read-only Terraform PR plans

By the repository owner's 2026-11-05 decision, same-repository Terraform PRs
automatically run `pr-terraform-plan` before `ci-ready`, without an approval
click. Documentation and application-only changes skip it. The runner consumes
CI's selected deployments and the registered owners of changed root Terraform
resources, including manual-only IAM targets. Each scope gets a refreshed
Terraform **1.8.5** plan with its registered targets and variables, using current
state-recorded immutable image digests. Every saved plan passes the production
resource/action allowlist and `plan_checks`. Policy Troubleshooter v3 evaluates
effective IAM for the **production deploy identity**, including inherited and
conditional grants and deny policies; both overall and allow verdicts must confirm
access. Missing, conditional-unknown or unreadable authority fails closed. This rehearsal does not replace protected deploy-time checks.

The dedicated `shared-datasets-pr-plan` service account has metadata/IAM reads
and read access to the exact default production state object (plus bucket object
name listing required by Terraform's GCS workspace discovery). It has no state
lock/write, resource mutation, Secret Manager, signing or deployer impersonation
permissions. The owner's no-Secret-Manager rule also excludes metadata/policy
reads. The reviewed `pr_plan_exclusions` in `.github/terraform-targets.json`
list exactly the six secret-bearing scopes. Each selected exclusion emits an
explicit PR notice; static IaC checks and protected deploy-time plans still run.
All other selected scopes must pass the live gate once activated.
**Same-repository PR authors can read production state**, including any
sensitive values already persisted there; this exposure is the owner's explicit
exception to credential-free PR validation. Fork PRs skip authentication; GitHub
normally withholds their `id-token: write`. Keep the repository's fork write-token
option disabled: WIF's PR subject alone does not distinguish a fork. All other
PR jobs remain credential-free.

The PR identity uses a separate WIF pool and requires this repository,
`pull_request`, a numeric PR merge ref, the exact CI caller's `workflow_ref` and
the exact reusable planner's `job_workflow_ref` at that ref. These are distinct
claims: GitHub supplies `job_workflow_ref` only for
[reusable workflows](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-with-reusable-workflows).
Plans/JSON are never uploaded, provider output is captured privately, temporary
plans are removed, checkout credentials are not persisted and logs emit only
resource addresses/actions and permission names. Failed provider diagnostics are
withheld because they can contain attribute values. Policy evaluation follows
[Google's v3 access verdict](https://docs.cloud.google.com/policy-intelligence/docs/reference/policytroubleshooter/rest/v3/iam/troubleshoot),
including deny policies; principal access boundary evaluation is not claimed.

Bootstrap the two new registered scopes through the reviewed, protected
`terraform-state-read-iam-sync.yml` workflow: `PR plan identity IAM bootstrap`
precedes `PR plan identity provisioning`. No local apply is permitted. Configure
repository variables `GCP_PR_PLAN_WORKLOAD_IDENTITY_PROVIDER` and
`GCP_PR_PLAN_SERVICE_ACCOUNT` from the sensitive `github_pr_plan_*` outputs.
Set the service-account variable first and the provider variable last, after
protected provisioning succeeds. Until the provider variable exists, the PR job
reports **not yet activated** and succeeds without authenticating. Once it exists,
authentication, planning, allowlist and permission failures block `ci-ready`.
No identity was provisioned by the code change itself.
