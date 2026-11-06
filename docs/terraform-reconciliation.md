# Production Terraform targets and reconciliation

## Change a Terraform or IAM target

Follow [the production deployment procedure](ci-deployment.md) in order for
scope, registry/prerequisites, permission coverage, validation, review and rollout.
This document owns the separate state-only operation below.

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
