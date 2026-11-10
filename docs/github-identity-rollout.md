# GitHub workflow identity rollout

Identity migration has two separately reviewed phases. A Git revert cannot
restore an IAM grant removed by a previous apply, so phase 1 must leave the
current authentication path usable even if a new mapping is wrong.

## Phase 1: expand

Artifact Registry IAM bootstrap precedes **Artifact Registry writer binding sync**,
which includes workflow identity provisioning and precedes dependent writer rollout.
Sync adds `job_workflow_ref` capability attributes and
the observer, runtime, heartbeat, reconciler and cleanup identities. Recovery and
PR-planning identities are reserved for the protected
`terraform-state-read-iam-sync.yml` bootstrap/provisioning sequence after the
plain-revert window closes; both scopes have enforced deployment holds in phase 1.
After a successful signed identity-sync apply, the reusable executor waits 120
seconds in its unprivileged propagation job before dependent callers can
authenticate. This stays below that job's five-minute timeout and holds neither
the production environment nor the Terraform state queue.

Preserve all legacy mapping keys with their original expressions and all
existing IAM grants at the same addresses with unchanged members, roles and
conditions. The production provider keeps its previous repository/environment
condition. The read-only provider accepts `(legacy) || (workflow)` because the
new held-EAMLIS preview workflow's display name fails the legacy condition.
New capability mappings still require their registered main leaf (or the exact
PR advisory leaf); legacy token acceptance remains available in parallel.

Terraform WIF uses an additive `google_service_account_iam_member`, with an
explicit dependency on the updated provider. It must not own the entire
`roles/iam.workloadIdentityUser` binding: the existing environment subject grant
was created outside Terraform and must remain untouched in this phase.

Publisher, preview, read-only WIF and reconciler access get separate companion
resources. The reader gets a conditional companion beside its unconditional
legacy bucket grant; its effective access stays broad until phase 2.
Non-retirement IAM syncs use `block_deletes: true`: the saved-plan allowlist
rejects any action containing `delete`, including either replacement order,
even for an otherwise allowlisted address. Existing exact obsolete-resource
retirements and temporary-authority revocation remain separate scopes.

## Receipt compatibility during rollback

The phase-1 merge keeps `dataset_usage` in
[the rollout holds](../.github/ingestion-rollout-holds.json), owned by `jonaraphael`
through `2026-11-19T00:00:00Z`. A separate reviewed PR must remove it; expiry does
not release a hold automatically. Image validation still runs while delivery is
held. The four publication producers retain their existing holds.

The old-main receipt reader at `4aa19c71` accepts these merge-push contracts:

| Target | Receipt signer after merge | Old-main verification |
| --- | --- | --- |
| `catalog-viewer` | `catalog-viewer-deploy.yml` | Compatible schema, signer and `started`/`verified` phases. |
| `pmtiles-cdn` | `pmtiles-cdn-sync.yml` | Compatible schema, signer and `started`/`applied`/`verified` phases. |
| `dataset-usage` | `ingestion-deploy.yml` | Rejected: old main requires `dataset-usage-deploy.yml`; held during the rollback window. |

Old `deployment_revision.check` parses all records for its exact target
environment, but verifies provenance only for the same revision or a
non-ancestor. Thus an older ancestor record alone does not prevent a fresh
revert deployment. `verify_record`, `verified_statuses` and incident
`terminal_success`/`verify_saved_success` do verify signatures; an incompatible
receipt fails those operations and cannot establish incident recovery.
The detached verifier enumerates only pending WDPA records. Usage's reader-first
gate verifies the viewer receipt, whose signer remains compatible.

Workflow identity provisioning is folded into the already registered
`Artifact Registry writer binding sync` scope in `artifact-registry-iam-sync.yml`;
there is no separate `GitHub workflow identity sync` target or checkpoint namespace.
The combined scope retains the exact resource union and prerequisites (plus the
bare recovery account, moved from the held PR-plan scope), both original
permission probes, `block_deletes: true`, bootstrap ordering and the
unprivileged 120-second propagation barrier before dependent delivery. An
empty plan waits only for what remains of the previous attempt's window.

Every enabled Terraform scope keeps an old-main target name/hash, deployment
environment and `prod-terraform-target-apply.yml` receipt signer. Old main accepts
the signed expanded resource lists; it does not compare them with its own target
registry. It checks their address syntax for explicit recovery, the deployment
schema, exact target/environment binding, immutable revision/artifact, Actions
creator, CI/execution run provenance and signed receipt bytes. Incident state
retains its existing schema. The terminal contract digest is a cache key: reverting
reader code changes it and triggers full receipt revalidation rather than rejecting
the checkpoint. Retained resolved checkpoints undergo that same target validation.

The new-only `PR plan identity IAM bootstrap` and `PR plan identity provisioning`
scopes have checked-in `deployment_hold` reasons. Registry export refuses them
before cloud authentication, and deployment admission rejects both `check` and
`start` before any API read/write. Manual dispatch cannot create their deployment
checkpoints during phase 1. Remove the holds only in a separately reviewed PR
that explicitly closes the plain-revert window; then provision these identities
and collect their authentication evidence before removing legacy access.

`tests/test_deployment_checkpoint_revert.py` runs the current deployment and
incident writers against a pinned `4aa19c71` reader snapshot, with only external
API/attestation transport replaced. It exercises retained open and resolved
checkpoints for every enabled Terraform scope through receipt verification,
incident reconciliation and the scheduled safety dry-run, plus refusal of
altered environment/resource/signer/digest evidence and both held manual scopes.
Thus a plain Git revert needs no incident-state cleanup for checkpoints this
phase can write. This guarantee ends when a later reviewed change lifts these
holds or enables an incompatible receipt signer.

## Phase 2: contract

A separate PR removes legacy grants and mapping keys, requires production main
refs, narrows read-only token acceptance and bucket reads, and makes Terraform
WIF authoritative for the new capability. Retain the new service accounts,
roles and companion grants. Do not merge this phase until successful main runs
have authenticated as **each** of Terraform, publisher, preview, bucket-reader,
observer, runtime, heartbeat, reconciler, cleanup and recovery. PR-planning
activation remains separately gated: its policy requires a same-repository PR
merge ref, so retain successful PR-plan authentication evidence from the reviewed
main workflow implementation rather than requiring an impossible main-ref token.

Attach run URLs, exact main SHAs and attempts, reusable leaf refs, actual
authenticated service-account identities and successful capability operations.
Include a fresh saved-plan review listing every intended removal/replacement,
proof that manual recovery/planning provisioning succeeded, and a successful
Terraform identity-sync run using the new mapping. Existing legacy grants can
mask a broken new WIF grant: inspect rendered token attributes and prove the
new principal path rather than treating an auth step alone as proof.
Review and apply through the protected workflow after owner approval and merge.

## Audit of existing resources

Compared with main `7bd972e6150a01ca3be538d1ca33bc543d6aa6f8`, these are all
pre-existing IAM resources modified by the original CI/CD proposal. Resource
addresses below omit the common `google_` prefix.

| Resource | Phase-1 disposition |
| --- | --- |
| `iam_workload_identity_pool_provider.github` | Legacy condition and mapping expressions unchanged; capability mappings added. |
| `iam_workload_identity_pool_provider.github_readonly` | Legacy mappings unchanged; token condition union adds the new reader. |
| `service_account_iam_member.shared_datasets_publisher_github_wif` | Unchanged; `_workflow` companion added. |
| `service_account_iam_member.github_readonly_wif` | Unchanged; `_workflow` companion added. |
| `storage_bucket_iam_member.github_readonly_object_viewer` | Unchanged; `_scoped` companion added; narrowing deferred. |
| `service_account_iam_member.feature_preview_loader_github_wif` | Unchanged; `_workflow` companion added. |
| `storage_bucket_iam_member.monitoring_slack_recovery` | Unchanged; `monitoring_slack_reconciler` companion added; SA swap deferred. |
| `storage_bucket_iam_member.shared_datasets_publisher_pending_publish_viewer` | Policy unchanged; same bucket through IAM-only data source. |
| `storage_bucket_iam_member.shared_datasets_publisher_pending_publish_cleanup_user` | Policy unchanged; same bucket through IAM-only data source. |
| `storage_bucket_iam_member.eamlis_job_object_user` | Policy unchanged; same bucket through IAM-only data source. |
| `storage_bucket_iam_member.mpatlas_job_object_user` | Policy unchanged; same bucket through IAM-only data source. |
| `storage_bucket_iam_member.wdpa_reset_translation_reader` | Policy unchanged; same bucket through IAM-only data source; no SA swap exists in this diff. |
| `project_iam_custom_role.monitoring_alert_policy_manager` | Existing permissions retained; log-metric create/read/update permissions added. |

The unmanaged Terraform environment subject grant remains untouched; its new
companion is `service_account_iam_member.github_actions_terraform_wif`.
The bucket data source in `shared_bucket_public.tf` adds no grant. The existing
Monitoring failure filter changes its canary creator to the runtime SA, while
the four ingestion files also adjust job resource limits; these are independent
of IAM policy membership. `monitoring.tf` adds the dedicated heartbeat role and
grant without changing an existing IAM grant. The pinned test fixture records
all 40 existing grants in the touched files and the existing Monitoring role's
permissions, so policy preservation does not depend on this prose audit.
