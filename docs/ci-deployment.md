# Change or add an ingestion deployment

This is the ordered procedure for deployment changes, including the private
dataset-usage collector. Start with [AGENTS.md](../AGENTS.md) and the
[deployment skill](../.claude/skills/deploy-scheduled-ingestion/SKILL.md).
For Terraform changes, also load the
[protected Terraform skill](../.claude/skills/protected-terraform-apply/SKILL.md).
Read the selected job's runtime README when changing its data or processing
behavior; source-specific operations are linked below.

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
   image variables, resource scope, runtime window, smoke, canary and verification
   settings. Its `TARGETS` view includes only dataset publishers; telemetry must
   not acquire publication requirements. Use a thin `<target>-deploy.yml` wrapper
   calling [`ingestion-deploy.yml`](../.github/workflows/ingestion-deploy.yml).
   See [registry ownership](deployment-contracts.md#target-registries) before
   changing a shared field; avoid executor branches keyed to target names.
4. **Connect prerequisites and validation.** Wire the wrapper into
   [`ci.yml`](../.github/workflows/ci.yml) with explicit `needs` for registry,
   IAM and monitoring prerequisites. Add or extend Terraform/IAM scopes through
   [the target-change procedure](terraform-reconciliation.md#change-a-terraform-or-iam-target)
   when needed. Register the image recipe/installed probe in
   [`tested_image_bundle.py`](../scripts/tested_image_bundle.py) and
   [`production_image_contracts.py`](../scripts/production_image_contracts.py).
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
   the state queue. Manual recovery/dispatch requires `executor_sha`,
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
