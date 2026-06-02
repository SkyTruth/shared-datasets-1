# Feature Branch Preview

The feature branch preview is one replaceable test slot inside the
`shared-datasets-1` GCP project. It lets maintainers deploy selected feature
branches before deciding whether to merge them to `main`.

The preview is not a second production environment. It owns only preview-named
Cloud Run, Cloud Storage, Firestore, IAM, and service account resources, with
Terraform state isolated under `000-system/terraform/state/preview`.

The preview bucket is disposable and uses `force_destroy = true` so the preview
stack can be destroyed after testing. Do not put canonical dataset releases,
production-only credentials, or irreplaceable data in the preview bucket.

## Deploy Or Replace The Preview

Use the GitHub Actions workflow named `Deploy Feature Branch to Preview`.

Selection:

- In the GitHub **Run workflow** branch dropdown, select the feature branch to
  deploy.

The protected workflow keeps the preview control plane checked out from `main`
at the workspace root and checks out the selected workflow branch separately
under `preview-source/`. It builds the preview service image from the selected
branch after verifying the protected preview IAM bootstrap, plans and applies
an allowlisted `terraform/envs/preview` destroy reset from the `main`
control-plane checkout, then plans and applies the new preview stack for the
selected branch and prints the preview Cloud Run URL.

This split is intentional. `main` provides the reviewed workflow and Terraform
control plane, while the branch selected in the GitHub workflow dropdown
provides the source that is deployed into the preview slot.

The selected feature branch must include the baseline preview service source and
support the preview Firestore database override. `main` carries those mechanics;
feature branches should rebase or merge from `main` before using the preview
workflow.

To replace the preview with a different feature branch, run the same workflow
again from that branch in the GitHub dropdown. There is only one active preview
slot; each deploy first tears down the previous preview bucket, Firestore
database, Cloud Run service, and preview bucket/IAP IAM bindings before creating
the new preview deployment. This clears previous preview data that is no longer
present in the selected branch.

The deploy workflow does not own the stable conditioned project IAM grants that
let the preview service and preview loader use the preview Firestore database,
the stable preview service accounts, or the preview loader Workload Identity
binding. Those bootstrap resources are managed by the protected GitHub Actions
workflow named `Preview Terraform IAM sync` from
`terraform/envs/prod/preview_terraform_iam.tf`.
That sync workflow applies only from `main` after the reviewed control-plane
changes have landed. Deploy validates those bootstrap resources before Docker
build or Terraform reset, and fails without mutating the preview slot if the
sync has not run.

During the migration from the earlier preview root, deploy reset first removes
the stable preview service accounts and loader Workload Identity binding from
preview Terraform state without deleting the live resources. It may also delete
the two legacy preview-scoped Firestore project IAM bindings that remain in old
preview Terraform state. The verifier only permits those deletes when the
member, role, and condition are scoped to the preview service accounts and
`projects/shared-datasets-1/databases/feature-metadata-preview`.

## Destroy The Preview

Use the GitHub Actions workflow named `Destroy Preview Environment`. The
protected workflow applies only from `main`, checks out the reviewed `main`
control plane, plans `terraform/envs/preview` with `-destroy`, enforces the
preview-resource allowlist, and applies only that saved destroy plan. This
removes the preview Cloud Run service, preview bucket contents, preview
Firestore database, preview IAM bindings owned by the preview root. It does not
delete the stable preview service accounts or loader Workload Identity binding.
It may delete the same legacy preview-scoped Firestore project IAM bindings
described above so the preview state can converge onto the protected IAM sync
workflow.

## Load Preview Data

The preview service reads only from the preview bucket and the preview
Firestore database. A release bundle used for preview must therefore be written
under:

```text
gs://skytruth-shared-datasets-1-preview/{category}/{subcategory}/{asset-slug}/releases/YYYY-MM-DD/
```

The release index must also be present at:

```text
gs://skytruth-shared-datasets-1-preview/_catalog/releases/{asset-slug}.json
```

All schema, manifest, and related artifact paths inside those JSON files must
point to `gs://skytruth-shared-datasets-1-preview/...`, not to the production
bucket.

After the preview release bundle is in place, use the relevant preview load
workflow for the feature under test.

Inputs:

- `ref`: branch, tag, or SHA whose loader code should run.
- `asset_slug`: exact asset slug.
- `release`: concrete `YYYY-MM-DD` release.
- `sidecar_uri`, `schema_uri`, and `manifest_uri`: preview-bucket release
  artifact URIs.
- `sidecar_generation`, `schema_generation`, and `manifest_generation`: exact
  GCS object generations.
- `load_id`: optional; when omitted, the workflow uses
  `github-{run_id}-{run_attempt}`.

The workflow keeps the preview workflow code checked out at the workspace root
and checks out the requested `ref` under `preview-source/` for the catalog and
loader code. It authenticates with the preview loader identity, downloads the
exact preview objects, loads documents into the preview Firestore database, and
writes a load record back to the preview bucket.

Branches based on current `main` satisfy the preview loader requirements.

## Authentication Note

The preview workflows currently use the existing
`shared-datasets-production` GitHub environment for Workload Identity
Federation and approval. That environment is only the authentication gate; the
Terraform root and service accounts are preview-specific, and the workflow
allowlist refuses non-preview resource changes.

If the repo later creates a separate GitHub environment and WIF provider for
preview, update `terraform/envs/preview/variables.tf` and both preview
workflows together.
