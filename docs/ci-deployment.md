# Change a production deployment

Start here for ingestion, private usage telemetry, and Terraform/IAM changes. [AGENTS.md](../AGENTS.md)
governs Git operations, asset admission and publication identity decisions. Production changes require
`jonaraphael` review, merge to `main`, and a protected `shared-datasets-production` workflow. Local `fmt`,
`validate`, `plan` and `show -json` provide review evidence; local production apply is prohibited outside an
explicitly named break-glass emergency. Dataset files never belong in Terraform state. Source behavior belongs
in the owning job's README. The linked contracts below provide reference detail; the change procedure is
complete here.

1. **Declare scope and ownership.** Name the target, exact resources/actions, prerequisites, affected
   consumers and intended outcome. Name one integration owner and resolve overlapping work before validation.
   For real publication batches, finish [publication readiness](publication-readiness.md) before expensive
   builds, CI or recovery; retain source pins, claims and checkpoints. New assets/pipelines require admission
   evidence and the feature identity decision under AGENTS.md. Release at most one held ingestion target per
   change.
2. **Register the producer and exact scope.** [`ingestion_targets.py`](../scripts/ingestion_targets.py)
   constructs `DEPLOY_TARGETS` for the five shared-executor callers: WDPA, MPAtlas, EAMLIS, sea ice and usage.
   It owns resource scope, runtime window, canary policy, image-variable binding and verification hooks. Its
   eleven-field constructor rejects incompatible policies; `TARGETS` exposes only the four publication
   producers for release/freshness contracts. Telemetry requires neither a release index nor feature-ID
   installation. Names derive workflow/image variables; plan policies are `update`, `install`, `wdpa` or
   `usage`, canaries terminal or deferred. Keep claims and installed identity state intact. Thin
   `<target>-deploy.yml` wrappers call `ingestion-deploy.yml`; usage delegates its scope/plan guard to
   `dataset_usage_deploy.py`, preserving reader-first activation/removal. Recovery resolves original image
   variables before checkout. In [the scope registry](../.github/terraform-targets.json), `targets` owns
   stable synchronization names, exact resources, readiness probes, variables, deletion policy and optional
   preparation; historical receipts hash these names. `plan_scopes` owns viewer/CDN/usage scopes. Callers
   cannot widen them.
3. **Connect prerequisites, selection and authority.** The registry's `workflows` table owns selection names,
   images, deployment ordering, signed-record targets, historical publication job names and unattended events.
   `terraform_prerequisites` must match CI callers' `needs`; ordering does not select unchanged owners. Each
   narrow `targets` row declares managed `prerequisites` and optional `retired_resources`, matching its
   wrapper's bootstrap `needs`. Missing declarations fail plan construction before fanout. `permission_probes`
   owns named read-only checks; unknown targets fail closed. Extend resource/action coverage in
   `terraform_plan_permissions.py` when introducing a resource class. A local operator's broader credentials
   do not prove CI deployer readiness; metadata reads and mutation authority stay distinct. Thin IAM wrappers
   call `prod-terraform-target-apply.yml` by synchronization name. Preserve caller identities/bootstrap order;
   add a constrained protected path by PR if none covers the resource class. Selection follows
   workflow/nested-action/script closure, Docker COPY and parsed Terraform ownership/modules.
   `selection_exceptions` owns reasoned traversal boundaries/input additions. Executor-only exclusions replace
   inferred callers with explicit representatives: sea ice, Artifact Registry IAM, viewer and CDN. Admission
   imports stop traversal. Content, variables and registry changes retain all actual consumers;
   validation/image selection remain independent.
4. **Exercise the boundary and validate.** Run focused positive/refusal checks for selection, unmanaged
   ancestors, realistic saved plans, unrelated changes, replacements, missing prerequisites, deletion policy
   and runtime outcomes. Run `uv run python scripts/terraform_target_contracts.py` and `uv run python
   scripts/release_contracts.py --target all`; initialize pinned providers first as described in [CI
   tools](ci-preflight.md#isolation-and-tools). Recipes and Terraform consumers determine images; register
   probes under `image_checks`. Missing typed executor inputs fail before authentication. Every selected
   deployment retains its image validation floor, including holds. Use complete synthetic
   runtime/serialization batches for publication-code changes without a release, and the actual conversion
   chain for native changes. Size CPU/memory/timeout/scratch against full sources; fractional experiments
   never publish sampled bytes. Installed probes run once in CI with production limits, no network/source
   mounts and memory-backed `/tmp`. WDPA's 13-hour task timeout retains at least 1.5 times its observed
   8 h 21 m maximum. Finish prerequisites/base integration, freeze a clean committed head and
   follow [validation before push](ci-preflight.md#validate-before-push).
5. **Review and authorize.** Include expected resource actions, allowlist and permission coverage, and
   unresolved live prerequisites in the reviewed PR. Successful CI and environment review do not replace
   dataset mutation approval or installed publication-state checks. See the acceptance/identity rules below.
6. **Observe protected execution.** Successful main-push `ci-ready` supplies `executor_sha`, `source_run_id`
   and `source_run_attempt` and exact tested images by digest. Manual entrypoints require the same source
   triple; deployment never rebuilds missing bytes. The verifier checks repository/workflow IDs, main-push
   event, exact revision and successful attempt through GitHub's API; automatic callers identify their current
   attempt. Inputs, PR/fork revisions and fabricated completions cannot grant authority. Finish registry/IAM
   bootstrap first. [Queue and saved-plan
   contracts](deployment-contracts.md#serialization-and-protected-execution) keep image/credential preparation
   and canaries outside the state queue, with guarded plan/claim/apply atomic inside it; propagation waits
   stay unprivileged. Usage holds its target queue through signed apply/canary; other ingestion wrappers
   retain combined rollout. Ingestion/cleanup IAM read bucket metadata through a data source, keeping
   CORS/logging out and independent of usage canaries.
7. **Verify and report completion.** Inspect captured exact-image canary execution, signed terminal outcomes
   and release/latest contracts (or private usage report and activation), then verify effective live state and
   [alert delivery](alert-routing.md#execution-and-delivery-verification). Report image digest, resource
   actions, execution/result, changed remote paths and uncertainty. Deferred verification is incomplete. Use
   [recovery](ci-recovery.md) after failures; maintain separate code-merged, operation-completed,
   observer-verified and incident-reconciled milestones.

## Caller scheduling

Only successful main-push CI exposes delivery outputs. Every post-gate caller uses `!cancelled() &&
!failure()` and its delivery lookup; mutation/catalog/alert callers also require their discovery output.
`needs` retains all registered ordering edges. [GitHub
documents](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions#failure) that
`failure()` includes failed ancestors. Explicit status functions override implicit `success()`, so skipped
unselected prerequisites permit selected work; a failed ancestor blocks it. `cancelled()` only reports a cancelled
workflow, so callers also reject a cancelled direct prerequisite (for example a timed-out job) with
`!contains(needs.*.result, 'cancelled')`; every caller lists its transitive prerequisites in `needs`, so direct needs
suffice. Any failed ancestor blocks every post-gate caller, including `identity-decision-reconcile`. Uniform conditions
make a selected Terraform prerequisite's unexplained skip unreachable: it runs, or an ancestor failed or was
cancelled, or the workflow was cancelled. The graph contract tests this invariant over every selection subset. Publication discovery
still owns immutable mutation authority.

## Acceptance and identities

Deployment bootstrap requires the exact merged executor PR and `jonaraphael`'s effective approval of its final head, or restricted self-authored merge acceptance, before candidate checkout/authentication. Missing, ambiguous, stale, dismissed or unreadable acceptance fails closed; direct main pushes cannot authorize deployment. Reusable callers retain `pull-requests: read` through every layer. The credential-free PR check uses the same gate with an explicit PR number and expected head, including drafts. A draft cannot authorize deployment. Owner approval or marking a PR ready refreshes its failed `owner-acceptance` job and dependent `ci-ready` for the latest CI run of the exact current head. The credential-free `owner-acceptance-refresh.yml` callback also observes CI completion, covering approvals received while suites run; it does not rerun sibling validation jobs. CODEOWNERS routes Dependabot-touched paths to the owner. The ingestion plan guard installs the locked `ci-plan` parsers before authentication.

New per-workflow production identities use exact main workflow refs in [`github-workflow-identities.json`](../terraform/envs/prod/github-workflow-identities.json): `job_workflow_ref` for reusable jobs, `workflow_ref` otherwise, with repository/environment constraints. Phase 1 applies Artifact Registry IAM bootstrap, then the combined additive workflow identity and Artifact Registry writer binding sync, then dependent writer rollout. Legacy provider mappings, accepted tokens and grants remain intact; Terraform impersonation uses an additive IAM member. Phase 2 is a separate reviewed PR after successful main runs prove every new identity authenticates; only then remove legacy access and install the authoritative Terraform WIF binding. See the [identity rollout and evidence gate](github-identity-rollout.md). See [deployment contracts](deployment-contracts.md) for trusted restore, state queues, tested-image handoff and signed receipts.

The new scoped bucket-reader grant covers published objects and pending-publish sources. Phase 1 retains its legacy unconditional bucket-reader grant, so `000-system/` and state reads remain possible until phase 2 removes that grant. Scratch cleanup has exact-leaf listing/conditional pending-publish and cleanup-marker authority; apply workflow identity sync before cleanup. It needs no new authentication variables and has no canonical publisher authority. Observers cannot mutate, administer IAM or access secret versions; the separate [read-only recovery](ci-recovery.md) identity has exact production-state reads for planning. Runtime handles image pushes, canaries, schedules and invalidation, then restores observer credentials; reconcilers own private incident state and only the heartbeat identity emits heartbeat logs.

Use [job operations](../.claude/skills/deploy-scheduled-ingestion/references/job-operations.md) for pause/resume, teardown and activation exceptions, the [WDPA runbook](wdpa-monthly-runbook.md) for monthly operations, the [reset runbook](feature-id-reset-installation.md) for first identity installation, and the [usage README](../ingestion/dataset_usage/README.md#activation-and-cost) for activation/cost gates.

## Automatic read-only Terraform PR plans

The owner's 2026-11-05 decision authorizes automatic live planning for same-repository Terraform PRs before `ci-ready`, without an approval click. Application/docs changes skip. Selection and execution resolve the same registered production scopes, including manual IAM owners and removed declarations from the comparison base. Preview-only, lockfile-only and other unregistered Terraform changes emit a skip notice; static checks still apply.

Up to four registered scopes plan concurrently with Terraform **1.8.5**, refreshed state and state-recorded immutable images. Each gets private temporary plan/variable files, removed on success or failure and never uploaded. Resource/action/image allowlists remain per scope. Policy Troubleshooter v3 must grant both overall and allow access, including inherited/conditional grants and deny policies; unknown or unreadable authority fails closed. This does not evaluate principal access boundaries or replace deploy-time checks. Transient policy reads retry three times (5/10-second delays); 4xx and malformed responses fail immediately. Errors identify scope, resource and permission without provider attributes.

The PR identity reads metadata/IAM, the exact default production state object and bucket object names for workspace discovery. It cannot write/lock state, mutate resources, read Secret Manager metadata/policies/versions, sign or impersonate the deployer. Each of the six reviewed secret-bearing `pr_plan_exclusions` emits a notice; static checks and protected planning still apply.

**Same-repository PR authors can read sensitive values persisted in production state.** Forks skip authentication; keep GitHub's fork write-token option disabled because WIF alone cannot distinguish them. Other PR jobs remain credential-free. The separate PR WIF pool requires this repository, `pull_request`, a numeric PR merge ref and exact CI/planner workflow refs. Checkout credentials are not persisted; full history supports comparison-base scope discovery.

Automatic `Artifact Registry writer binding sync` establishes the production WIF provider. The two new manual scopes have enforced `deployment_hold` reasons during the plain-revert window. Only a separate reviewed PR explicitly closing that window may remove those holds. Then activate through a manual protected `terraform-state-read-iam-sync.yml` run using the exact successful main-push CI source triple: `PR plan identity IAM bootstrap` precedes `PR plan identity provisioning`. This manual scope also provisions the recovery identity and its planning/state grants; recovery is unavailable until it succeeds. The new planning roles and recovery grants are excluded from automatic merge-triggered scopes until their custom-role permissions have been verified live. Set `GCP_PR_PLAN_SERVICE_ACCOUNT` and `GCP_PR_PLAN_WORKLOAD_IDENTITY_PROVIDER` from sensitive `github_pr_plan_*` outputs, and confirm `GCP_TERRAFORM_SERVICE_ACCOUNT`. Then merge a reviewed PR changing the checked-in `workflow_call.inputs.active.default` in [`pr-terraform-plan.yml`](../.github/workflows/pr-terraform-plan.yml) from `false` to `true`; CI callers must inherit that default. While inactive, the notice succeeds without authentication. Once active, missing configuration and auth/plan/allowlist/permission failures block `ci-ready`; losing a variable cannot deactivate planning. Code alone provisions nothing; local apply is prohibited.
