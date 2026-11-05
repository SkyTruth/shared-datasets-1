# Production Terraform targets and reconciliation

Load [protected Terraform guidance](../.claude/skills/protected-terraform-apply/SKILL.md)
before planning production changes. This document owns the target-change and
state-only procedures. Shared execution rules live in
[deployment contracts](deployment-contracts.md#serialization-and-protected-execution).

## Change a Terraform or IAM target

1. **Define the exact mutation.** Name the resource addresses and actions, owner,
   prerequisites and affected consumers. Use Terraform with remote shared state;
   retain a local plan summary for review when useful. A local operator's broader
   credentials cannot establish the CI deployer's readiness.
2. **Update the single scope registry.** Edit
   [`.github/terraform-targets.json`](../.github/terraform-targets.json), keeping
   stable synchronization names because historical receipts bind their hashes.
   Each `targets` entry supplies exact resources, readiness probe, variables,
   deletion policy and optional preparation. Viewer/CDN scopes use `plan_scopes`.
   [Registry ownership](deployment-contracts.md#target-registries) explains which
   consumers read each view. No caller may widen the scope.
3. **Declare dependencies and permission checks.** Update
   [`terraform/iam-plan-prerequisites.json`](../terraform/iam-plan-prerequisites.json)
   and the wrapper's `needs` when a grant/resource must exist first. Extend the
   resource/action checks in [`deployment_permissions.py`](../scripts/deployment_permissions.py)
   and its probe implementation only when the new class requires them. Missing
   Terraform prerequisite declarations fail CI plan construction before fanout.
   Keep metadata-read readiness distinct from mutation authority.
4. **Use the constrained executor.** Thin IAM wrappers call
   [`prod-terraform-target-apply.yml`](../.github/workflows/prod-terraform-target-apply.yml)
   by synchronization name. Preserve caller identities, bootstrap order and the
   [queue/saved-plan contract](deployment-contracts.md#serialization-and-protected-execution).
   If no protected path covers the class, add one in the PR. Monitoring prepares
   its exact tested image and credentials before the queue; propagation waits
   run unprivileged after a successful signed apply.
5. **Test scope and refusal behavior.** Run focused target/permission tests with
   realistic saved-plan fixtures, including unrelated mutations, replacements,
   missing prerequisites and the declared deletion policy. Run
   `uv run python scripts/terraform_target_contracts.py` and
   `uv run python scripts/release_contracts.py --target all` for offline closure
   and workflow contracts. Terraform formatting/validation and full final checks
   follow [validation before push](ci-preflight.md#validate-before-push).
6. **Review, then observe protected execution.** Include intended actions,
   allowlist coverage and any unresolved live prerequisites in a PR reviewed by
   `jonaraphael`. After merge and successful main `ci-ready`, automatic targets
   run through their selected entrypoints. Manual-only callers require
   `executor_sha`, `source_run_id` and `source_run_attempt` from that exact
   successful main-push CI. Verify signed terminal outcomes and effective live
   state; handle failures through [recovery](ci-recovery.md).

## Manual state-only reconciliation

Use [`prod-terraform-state-reconcile.yml`](../.github/workflows/prod-terraform-state-reconcile.yml)
only after the intended infrastructure changes and authority revocation are
verified. It is manual-only and uses the registry's paired-empty scope; it
cannot mutate live resources or replay retirement.

1. Read the latest signed outcomes and current state-recorded deployment inputs.
   Resolve required full-state reads first through the reviewed manual
   [`terraform-state-read-iam-sync.yml`](../.github/workflows/terraform-state-read-iam-sync.yml).
   Its `terraform-state-read` probe checks shared-project metadata permissions,
   not access to foreign-project accounts.
2. Dispatch state reconciliation from reviewed `main` with its exact successful
   CI source triple and `tf_vars` containing newline-separated current deployment
   variables: immutable job/service images, Monitoring channel and numeric token
   version. These inputs permit planning; they do not authorize redeployment.
3. Inspect the full normal refreshed saved plan. The empty resource allowlist
   rejects every create, update, replacement or delete, including revived
   temporary grants. Only state/output bookkeeping may be applied by the
   protected executor.
4. Verify signed completion, then fresh full production and preview plans with
   current inputs. Both must exit zero with stable state; output differences or
   a refresh-only plan do not satisfy this gate. Preserve rollback evidence until
   that final verification completes.

## Known prerequisite and historical evidence

The recorded shared-project bootstrap added five metadata reads after run
`40564777085/1` failed before claim/apply. The full state still records
`shared-datasets-reader@x30-399415.iam.gserviceaccount.com`; its previously denied
`iam.serviceAccounts.get` remains deferred. The shared-project readiness probe
cannot prove that foreign read. Do not retry a known denied read or use
`-refresh=false` to hide it during full-state reconciliation.

The [October–November retirement audit](history/terraform-retirement-2026-10-27.md)
retains exact scopes, fixtures, expired authority, observed deletions and the
failed-read evidence. Its completed investigation is excluded from onboarding;
its unresolved verification gate remains above. No historical approval renews
an expired grant.
