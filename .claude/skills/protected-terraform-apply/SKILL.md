---
name: protected-terraform-apply
description: Use before suggesting, planning, running, approving, or documenting any Terraform apply, terraform_prod_apply.py use, production infrastructure mutation, GCP IAM/storage/Cloud Run/Scheduler/CDN Terraform change, PMTiles CDN sync, catalog viewer infrastructure deploy, or shared-datasets prod Terraform deployment.
---

# Protected Terraform Apply

Production Terraform is a reviewed control-plane mutation. In this repository,
agents may inspect and plan production Terraform, but they must not suggest or
run local production applies as the normal path.

## Default Rule

- Do not run or suggest `terraform apply`, targeted applies, or
  `scripts/terraform_prod_apply.py` for production from a local terminal.
- Do not use local apply as a shortcut for GCS access changes, IAM changes,
  PMTiles CDN route sync, Cloud Run services/jobs, Cloud Scheduler, monitoring,
  APIs, service accounts, Artifact Registry, or load balancer changes.
- Do run `terraform fmt`, `terraform validate`, `terraform plan`, and
  `terraform show -json` when useful for review.
- Route production Terraform mutations through a PR reviewed by `jonaraphael`,
  merged to `main`, then applied by a protected GitHub Actions workflow in the
  `shared-datasets-production` environment.
- If no protected workflow exists for the infrastructure class, implement that
  workflow by PR. Do not fall back to a local apply.

## Normal Workflow

1. Make the repo change on a focused branch.
2. Run local checks:

```bash
terraform -chdir=terraform/envs/prod fmt -check
terraform -chdir=terraform/envs/prod validate
terraform -chdir=terraform/envs/prod plan -input=false ...
```

3. Summarize the expected live mutation from the plan, including changed
   resource addresses and whether the existing protected workflow allowlist
   covers them.
4. Open a PR requesting `jonaraphael` review.
5. After review and merge to `main`, let the protected workflow apply the
   approved Terraform changes.

## Existing Protected Paths

### Which workflow applies what

`ci.yml` routes main-push mutations after successful `ci-ready`. The IAM
entrypoints select a stable synchronization name from
[the versioned target registry](../../../.github/terraform-targets.json).
`scripts/terraform_target_contracts.py` resolves that same resource scope for
execution, offline dependency checks and release contracts; deployment recovery
uses its unchanged historical name/hash identities. Edit the registry when a
reviewed target scope changes, instead of copying allowlists into callers.

`prod-terraform-target-apply.yml` runs trusted source and permission checks plus
Monitoring image/credential preparation before entering `prod-terraform-state`.
Its `sync` job rechecks source/replay admission after acquiring that queue,
initializes Terraform, validates the exact saved plan, signs the claim and applies
that saved plan. Only this job owns the queue. IAM propagation waits follow in an
unprivileged job, outside the production environment and state queue. The WIF
provider, Terraform service account and protected environment are unchanged.

The narrow entrypoints retain their ordering and manual-dispatch interfaces:
`artifact-registry-iam-sync.yml`, `scheduled-ingestion-deploy-iam-sync.yml`,
`preview-terraform-iam-sync.yml`, `scratch-cleanup-iam-sync.yml`, and
`cron-alert-policy-sync.yml`. State reconciliation and its metadata-read bootstrap
remain manual-only (`prod-terraform-state-reconcile.yml` and
`terraform-state-read-iam-sync.yml`). State-only reconciliation has no resource
allowlist or preparation and refuses every live mutation; supply current recorded
deployment variables. Operational retirement retains its exact thirty-resource
pure-delete scope and always-run authority revocation; see
[Terraform reconciliation](../../../docs/terraform-reconciliation.md).

Ingestion deployments use `ingestion-deploy.yml` and exact tested-image checks.
`pmtiles-cdn-sync.yml` owns the separately constrained managed-folder/bootstrap
and CDN routing scopes. Neither path inherits a generic IAM mutation scope.

Other protected publication paths:

- Catalog web bundle publication: `.github/workflows/catalog-web-deploy.yml`
  after trusted `main` pushes.
- Dataset object promotion/deletion:
  `.github/workflows/publish-dataset.yml` after approved PR plans or restricted
  dispatch.

For any Terraform resource not covered by an existing protected workflow, add or
extend a constrained workflow in the same PR as the infrastructure change.

Full state reconciliation uses the deployer's `terraform-state-read` readiness
probe for the shared project. 30x30 reconciliation is deferred; the existing
foreign-account Terraform record remains, so a full production plan is still
blocked on that read. An operator's successful local plan or `iam-bootstrap`
hint does not prove that the CI identity can refresh the full state. Do not
retry while known reads are denied or hide them with refresh disabled. See
`docs/terraform-reconciliation.md` for the observed failed boundary and scopes.

The October operational retirement has explicit human approval for its temporary
project-level delete/read scope through `2026-11-03T00:00:00Z`. The November
observer-removal change adds ten exact targets and the two Cloud Run/Scheduler
delete permissions to that protected scope; it requires review/merge before live
use and does not extend the absolute window. Its Terraform
variable defaults the grant off, the pure-delete saved-plan policy refuses
resurrection/replacement and unrelated changes, and the revocation job runs even
after a partial grant failure. Role creation precedes the grant, and revocation
has no dependency that could recreate the role. Do not renew this absolute window implicitly.
If revocation fails, the integration owner must finish the existing protected
revocation with its exact tested revision; the fixed expiry remains the backstop.

## Publisher and read-only identity configuration

The Terraform Workload Identity provider restricts OIDC to this repository and
its `shared-datasets-production` environment. After the protected workflow
provisions it, set the environment variable `GCP_WORKLOAD_IDENTITY_PROVIDER` to
Terraform output `github_workload_identity_provider`. Read-only catalog drift
and hygiene workflows use repository variables
`GCP_READONLY_WORKLOAD_IDENTITY_PROVIDER` and `GCP_READONLY_SERVICE_ACCOUNT`, from
outputs `github_readonly_workload_identity_provider` and
`github_readonly_service_account`.

GitHub environment deployment reviewers, when configured, are a separate gate
from PR approval. The canonical mutation approval and executor contract lives
in [.github/dataset-plans/README.md](../../../.github/dataset-plans/README.md).

The publisher can read `_scratch/pending-publishes/` to promote reviewed bytes.
`scratch_writer_members` retains the current scratch-only writer; override it
with the approved scratch-only group/account when ready before removing broad
human write grants. Conditional IAM and alerting are the standing project-scope
controls: canonical writers are the publisher and scheduled-job identities;
scratch access stays separate and unexpected canonical writes/deletes alert.

The optional project-level deny backstop defaults off.
`canonical_mutation_deny_policy_enabled=true` requires an approved
organization-level IAM administrator or controlled IaC identity with
`roles/iam.denyAdmin` at organization scope; project ownership alone cannot grant
or use that role. Changes to any of these resources follow the protected workflow
above. The shared hierarchical-namespace bucket retains deleted/overwritten
objects through Cloud Storage soft delete for 30 days.

## Break-Glass Exception

Only use local production apply when the user explicitly says this is
break-glass or emergency work, names the exact live mutation to make, and accepts
that it bypasses the PR/protected-workflow path. Record the operator identity,
commands, affected resources, and rationale in the final response and in a
follow-up PR when practical.

Ambiguous phrases such as "deploy it", "apply this", "push it live", or "make
it work" are not break-glass approval.
