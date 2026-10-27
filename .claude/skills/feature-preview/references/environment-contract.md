# Preview environment contract

Read for deployment/IAM/state questions; upload procedure lives in SKILL.md.

The feature branch preview is one replaceable test slot inside the
`shared-datasets-1` GCP project. It lets maintainers deploy selected feature
branches before deciding whether to merge them to `main`.

The preview is not a second production environment. It owns only preview-named
Cloud Run, Cloud Storage, IAM, and service account resources, with
Terraform state isolated under `000-system/terraform/state/preview`.

The preview bucket is disposable and uses `force_destroy = true` so the preview
stack can be destroyed after testing. Do not put canonical dataset releases,
production-only credentials, or irreplaceable data in the preview bucket.

The catalog viewer uses a separate private temporary comparison cache at
`gs://skytruth-shared-datasets-1-preview-comparisons/`. It keeps job state and
completed indexes available across viewer instances. Objects expire after one
day, and preview destruction removes this disposable bucket too. Cache writes
are scoped to this bucket; it contains no canonical dataset releases.

## Deployment and state boundaries

The protected workflow checks out `main` at the workspace root and checks out
the selected workflow branch separately under `preview-source/`. It builds the
preview service and preview catalog viewer images from the selected branch
after verifying the protected preview IAM bootstrap, then plans and applies
`preview-source/terraform/envs/preview` through the preview resource-change
allowlist. This lets feature branches exercise preview-only Terraform changes
before merge while still refusing non-preview resources. In `preserve` mode, it
plans and applies the updated preview stack without first destroying the preview
bucket, then rebuilds the catalog web bundle from existing preview release
indexes. In `reset` mode, it first plans and applies a
saved destroy reset from the selected branch preview Terraform, then creates
the new preview stack and publishes a catalog shell. In both modes, it prints the preview
service and catalog viewer Cloud Run URLs.

This split is intentional. The workflow branch dropdown provides the source and
preview Terraform that are deployed into the preview slot, while stable
production-scoped IAM bootstrap remains on `main` through the separate sync
workflow.

The selected feature branch must include the baseline preview service source,
catalog viewer source, catalog site generator, and preview Terraform with the
sidecar backend and Firestore retirement configuration. Feature branches should
rebase or merge from `main` before using the preview workflow.

To replace the preview with a different feature branch, run the same workflow
again and select the new branch or tag in the workflow branch dropdown. There is
only one active preview slot. Use `preserve` when you are iterating on preview
service or catalog viewer code against already loaded test data. Use `reset`
when you need to tear down the previous preview bucket,
Cloud Run services, and preview bucket/IAP IAM bindings before creating the new
preview deployment. Reset clears previous preview data that is no longer present
in the selected ref.

The deploy workflow does not own the stable preview service accounts, the
preview service self-signing grant used for catalog viewer signed URLs, or the
preview loader Workload Identity binding.
Those bootstrap resources are managed by the protected GitHub Actions workflow
named `Infrastructure / Preview Terraform IAM sync` from
`terraform/envs/prod/preview_terraform_iam.tf`.
That sync owns creation of `feature-preview-service` and
`feature-preview-loader`; deploy and destroy only validate or use those stable
identities.

`feature-preview-loader` remains the catalog web publisher; its account,
Workload Identity binding, and preview bucket write grant are required.
That sync workflow applies only from `main` after the reviewed control-plane
changes have landed. Deploy validates those bootstrap resources before Docker
build or Terraform reset, and fails without mutating the preview slot if the
sync has not run.

During the migration from the earlier preview root, deploy reset first removes
the stable preview service accounts and loader Workload Identity binding from
preview Terraform state without deleting the live resources. The saved preview
reset plan may also delete old preview-stack resources that remain in
`terraform/envs/preview` state from earlier naming.

Before deployment, reset, or destruction, a saved plan detaches the retired
`feature-preview` Firestore database from Terraform state with `destroy = false`.
The active `metadata_retirement_plan.py --database-only preview` validator
accepts only forgetting that database and no-op/read actions elsewhere; it
refuses database deletion and unrelated mutations. Existing Firestore data
remains untouched and no longer belongs to the disposable preview slot. Selected
branches must include this preservation configuration. The completed production
retirement modes and temporary IAM root are removed; do not recreate them. See
[metadata stack retirement](../../../../docs/metadata-stack-retirement.md).

## Destroy boundary

Use the GitHub Actions workflow named `Preview / Destroy environment`. The
protected workflow applies only from `main`, checks out the reviewed `main`
control plane, plans `terraform/envs/preview` with `-destroy`, enforces the
preview-resource allowlist, and applies only that saved destroy plan. This
removes the preview Cloud Run services, preview bucket contents, and preview
IAM bindings owned by the preview root. It does not
delete the stable preview service accounts or loader Workload Identity binding.

## Authentication

The preview workflows currently use the existing
`shared-datasets-production` GitHub environment for Workload Identity
Federation and approval. That environment is only the authentication gate; the
Terraform root and service accounts are preview-specific, and the workflow
allowlist refuses non-preview resource changes.

If the repo later creates a separate GitHub environment and WIF provider for
preview, update `terraform/envs/preview/variables.tf` and both preview
workflows together.
