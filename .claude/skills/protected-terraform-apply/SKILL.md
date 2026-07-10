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

- Catalog web bundle publication: `.github/workflows/catalog-web-deploy.yml`
  after trusted `main` pushes.
- PMTiles CDN route and public-folder sync:
  `.github/workflows/pmtiles-cdn-sync.yml` after trusted `main` pushes, with a
  Terraform plan allowlist.
- Dataset object promotion/deletion:
  `.github/workflows/publish-dataset.yml` after approved PR plans or restricted
  dispatch.
- Stable preview data-plane ownership:
  `.github/workflows/preview-terraform-iam-sync.yml`, followed once by
  `.github/workflows/feature-preview-state-ownership-migration.yml`. Keep the
  committed migration marker until a reviewed cleanup PR verifies single state
  ownership and removes both one-time ownership workflows.

For any Terraform resource not covered by an existing protected workflow, add or
extend a constrained workflow in the same PR as the infrastructure change.

## Break-Glass Exception

Only use local production apply when the user explicitly says this is
break-glass or emergency work, names the exact live mutation to make, and accepts
that it bypasses the PR/protected-workflow path. Record the operator identity,
commands, affected resources, and rationale in the final response and in a
follow-up PR when practical.

Ambiguous phrases such as "deploy it", "apply this", "push it live", or "make
it work" are not break-glass approval.
