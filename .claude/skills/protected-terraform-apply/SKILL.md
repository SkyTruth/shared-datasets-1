---
name: protected-terraform-apply
description: Use before suggesting, planning, running, approving, or documenting any Terraform apply, terraform_prod_apply.py use, production infrastructure mutation, GCP IAM/storage/Cloud Run/Scheduler/CDN Terraform change, PMTiles CDN sync, catalog viewer infrastructure deploy, or shared-datasets prod Terraform deployment.
---

# Protected Terraform Apply

Follow [Change a Terraform or IAM target](../../../docs/terraform-reconciliation.md#change-a-terraform-or-iam-target)
for the ordered procedure. That document also owns manual state reconciliation;
[deployment contracts](../../../docs/deployment-contracts.md) own provenance,
queues, saved-plan scope, permission checks and signed outcomes.

## Authority

- Do not run or suggest local production `terraform apply`, targeted applies or
  `scripts/terraform_prod_apply.py` as the normal path. This includes IAM,
  storage, CDN, Cloud Run, Scheduler, Monitoring, APIs and service accounts.
- Local `terraform fmt`, `terraform validate`, `terraform plan` and
  `terraform show -json` may provide review evidence.
- Production changes require a PR reviewed by `jonaraphael`, merge to `main`,
  and a protected workflow in `shared-datasets-production`. If the resource
  class has no constrained workflow, add one by PR rather than applying locally.
- Review, successful CI, environment reviewers and dataset mutation acceptance
  are separate gates. Canonical object promotion/deletion follows the
  [immutable dataset-plan contract](../../../.github/dataset-plans/README.md).

## Identity and storage constraints

The Terraform Workload Identity provider restricts OIDC to this repository and
`shared-datasets-production`. Configure `GCP_WORKLOAD_IDENTITY_PROVIDER` from
`github_workload_identity_provider`; read-only catalog workflows use
`GCP_READONLY_WORKLOAD_IDENTITY_PROVIDER` and `GCP_READONLY_SERVICE_ACCOUNT`
from the corresponding `github_readonly_*` Terraform outputs.

Publisher and scheduled-job identities own canonical writes; scratch writers
remain separate. The publisher reads `_scratch/pending-publishes/` for reviewed
promotion. Keep `scratch_writer_members` scoped to the approved scratch-only
identity before removing broad human writes. The bucket retains deleted or
overwritten objects with 30-day soft delete.

The optional `canonical_mutation_deny_policy_enabled` backstop defaults off.
Enabling it requires approved organization-level `roles/iam.denyAdmin` authority;
project ownership cannot grant that role. These changes follow the same protected
procedure. Do not add object ACLs or use Terraform for changing dataset files.

## Break-glass exception

Local production apply requires an explicitly named emergency, exact mutation
and acceptance of bypassing the PR/protected workflow. Record operator identity,
commands, affected resources and rationale, plus a follow-up PR when practical.
“Deploy it”, “apply this” and “make it work” do not establish break-glass authority.
Historical temporary retirement authority is expired; its
[audit record](../../../docs/history/terraform-retirement-2026-10-27.md)
does not renew or grant permissions.
