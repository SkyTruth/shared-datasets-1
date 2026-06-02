# Feature Metadata Preview

The feature metadata preview is one replaceable test slot inside the
`shared-datasets-1` GCP project. It lets maintainers deploy metadata-service
code from a feature branch before deciding whether to merge that branch to
`main`.

The preview is not a second production environment. It owns only preview-named
resources:

- Cloud Run service: `metadata-service-preview`
- Bucket: `skytruth-shared-datasets-1-preview`
- Firestore database: `feature-metadata-preview`
- Service accounts: `metadata-service-preview` and
  `metadata-index-loader-preview`
- Terraform state prefix: `000-system/terraform/state/preview`

The preview bucket is disposable and uses `force_destroy = true` so the preview
stack can be destroyed after testing. Do not put canonical dataset releases,
production-only credentials, or irreplaceable data in the preview bucket.

## Deploy Or Replace The Preview

Use the GitHub Actions workflow named `Deploy Feature Branch to Preview`.

Inputs:

- In the GitHub **Run workflow** branch dropdown, select the feature branch to
  deploy.
- `action`: choose `deploy`.

The protected workflow keeps the preview control plane checked out from `main`
at the workspace root and checks out the selected workflow branch separately
under `preview-source/`. It builds the metadata-service image from
`preview-source/`, plans and applies an allowlisted `terraform/envs/preview`
destroy reset from the `main` control-plane checkout, then plans and applies the
new preview stack for the selected branch and prints the preview Cloud Run URL.

This split is intentional. `main` provides the reviewed workflow and Terraform
control plane, while the branch selected in the GitHub workflow dropdown
provides the metadata-service source that is deployed into the preview slot.

The selected feature branch must support `FEATURE_METADATA_FIRESTORE_DATABASE`
so the preview service reads from the preview Firestore database instead of the
default production database. `main` carries the baseline metadata service,
Dockerfile, and index-loader mechanics; feature branches should rebase or merge
from `main` before using the preview workflow.

To replace the preview with a different feature branch, run the same workflow
again from that branch in the GitHub dropdown. There is only one active preview
slot; each deploy first tears down the previous preview bucket, Firestore
database, Cloud Run service, IAM bindings, and service accounts before creating
the new preview deployment. This clears previous preview data and metadata that
are no longer present in the selected branch.

## Destroy The Preview

Use the GitHub Actions workflow named `Destroy Preview Environment`. The
protected workflow checks out the reviewed `main` control plane, plans
`terraform/envs/preview` with `-destroy`, enforces the preview-resource allowlist,
and applies only that saved destroy plan. This removes the preview Cloud Run
service, preview bucket contents, preview Firestore database, preview IAM
bindings, and preview service accounts.

## Load Preview Metadata

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

All metadata, schema, and manifest paths inside those JSON files must point to
`gs://skytruth-shared-datasets-1-preview/...`, not to the production bucket.

After the preview release bundle is in place, use the GitHub Actions workflow
named `Feature metadata preview index load`.

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
loader code. It authenticates as `metadata-index-loader-preview`, downloads the
exact preview objects, loads documents into the `feature-metadata-preview`
Firestore database, and writes an index-load record back to the preview bucket
under `000-system/metadata-preview/index-loads/{asset-slug}/{release}/`.

The requested loader ref must support `FEATURE_METADATA_FIRESTORE_DATABASE`.
Branches based on current `main` satisfy that requirement.

## Authentication Note

The preview workflows currently use the existing
`shared-datasets-production` GitHub environment for Workload Identity
Federation and approval. That environment is only the authentication gate; the
Terraform root and service accounts are preview-specific, and the workflow
allowlist refuses non-preview resource changes.

If the repo later creates a separate GitHub environment and WIF provider for
preview, update `terraform/envs/preview/variables.tf` and both preview
workflows together.
