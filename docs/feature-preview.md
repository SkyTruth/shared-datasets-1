# Feature Branch Preview

The feature branch preview is one replaceable test slot inside the
`shared-datasets-1` GCP project. It lets maintainers deploy selected feature
branches before deciding whether to merge them to `main`.

The preview is not a second production environment. Stable preview-named Cloud
Storage, Firestore, service accounts, and their narrow IAM grants are owned by
the protected production bootstrap. Feature-branch Terraform owns only the two
disposable Cloud Run services and their IAP bindings. Preview Terraform state
is isolated under `000-system/terraform/state/preview` in the private state
bucket.

The preview bucket and named Firestore database remain present across deploys
and destroys, but their data is disposable. Do not put canonical releases,
production-only credentials, or irreplaceable data in them.

## Deploy Or Replace The Preview

Use the GitHub Actions workflow named `Deploy Feature Branch to Preview`.

Selection:

- In the GitHub **Run workflow** branch dropdown, select the branch or tag to
  deploy into the preview slot.
- In **Preview data handling**, choose `preserve` to update the preview services
  and catalog viewer while keeping the existing preview bucket, release indexes,
  and dormant Firestore metadata state. Choose `reset` only when you want a
  clean preview slot; do not reload preview Firestore metadata while serving is
  inactive.

The selected branch or tag is both the workflow ref and the preview source ref.
The protected workflow checks out `main` at the workspace root and checks out
the selected workflow branch separately under `preview-source/`. It builds the
preview service and preview catalog viewer images from the selected branch
after verifying the protected preview IAM bootstrap, then plans and applies
`preview-source/terraform/envs/preview` through the preview resource-change
allowlist. This lets feature branches exercise preview-only Terraform changes
before merge while still refusing non-preview resources. In `preserve` mode, it
plans and applies the updated preview stack without first destroying the preview
bucket or Firestore database, then rebuilds the catalog web bundle from
existing preview release indexes. In `reset` mode, it first validates the
migrated backend and the saved Terraform plan, then authenticates as the
preview loader, deletes every listed bucket object with its exact generation,
recursively deletes only the `feature_preview_index` collection, verifies both
stores are empty, then deploys the services and publishes a catalog shell. In
both modes, it prints the preview service and catalog viewer Cloud Run URLs.

This split is intentional. The workflow branch dropdown provides the source and
preview Terraform that are deployed into the preview slot, while stable
production-scoped IAM bootstrap remains on `main` through the separate sync
workflow.

The selected feature branch must include the baseline preview service source,
catalog viewer source, catalog site generator, preview Terraform, and preview
Firestore database override. Feature branches should rebase or merge from
`main` before using the preview workflow.

To replace the preview with a different feature branch, run the same workflow
again and select the new branch or tag in the workflow branch dropdown. There is
only one active preview slot. Use `preserve` when you are iterating on preview
service or catalog viewer code against already loaded test data. Use `reset`
when you need a clean data plane before deploying the new services. Reset clears
prior preview objects and indexed feature documents without deleting or
recreating the bucket or database.

The deploy workflow does not own the stable preview bucket, named database,
bucket IAM, or conditioned project IAM grants that
let the preview service and preview loader use the preview Firestore database,
the stable preview service accounts, the preview service self-signing grant used
for catalog viewer signed URLs, or the preview loader Workload Identity binding.
Those bootstrap resources are managed by the protected GitHub Actions workflow
named `Preview Terraform IAM sync` from
`terraform/envs/prod/preview_terraform_iam.tf`.
That sync owns the bucket, database, creation of `feature-preview-service` and
`feature-preview-loader`, and their storage/Firestore grants; deploy and destroy
only validate or use those stable resources.
That sync workflow applies only from `main` after the reviewed control-plane
changes have landed. Deploy validates those bootstrap resources before Docker
build or Terraform reset, and fails without mutating the preview slot if the
sync has not run.

The one-time `Feature Preview State Ownership Migration` workflow releases the
bucket, database, two service accounts, loader Workload Identity binding, and
three bucket IAM bindings from preview state with an exact forget-only plan
after verifying every corresponding prod-state owner. A committed
`state-migration-pending` marker blocks deploy and destroy throughout the
transfer. After the workflow verifies the resources are present only in prod
state, remove the marker and both one-time ownership workflows in a final
reviewed cleanup PR.
Deploy and destroy contain no direct `terraform state rm` fallback.

## Destroy The Preview

Use the GitHub Actions workflow named `Destroy Preview Environment`. The
protected workflow applies only from `main`, checks out the reviewed `main`
control plane, verifies and applies the allowlisted saved destroy plan for
`terraform/envs/preview`, and only then clears stable preview data through the
loader identity. This removes the two Cloud Run services and their IAP
bindings. It leaves the empty preview bucket,
Firestore database, service accounts, and bootstrap IAM in place.

## Preview Catalog Viewer

The deploy workflow creates an IAP-protected preview catalog viewer at the
`preview_catalog_viewer_uri` Terraform output. It serves `_catalog/web/` from
`gs://skytruth-shared-datasets-1-preview/`, not from the production bucket.

Preserve-mode deploys rebuild the catalog from existing release indexes under
`gs://skytruth-shared-datasets-1-preview/_catalog/releases/*.json`. Reset-mode
deploys publish a catalog shell with no listed assets until preview data is
loaded again. The `Feature preview index load` workflow refreshes that catalog
after each successful load by downloading every preview release index under
`gs://skytruth-shared-datasets-1-preview/_catalog/releases/*.json`, rebuilding
the catalog web bundle, and publishing it back to the preview bucket with
generation preconditions.

The preview catalog intentionally includes only assets with preview-bucket
release indexes. It materializes each asset's top-level "latest" reference from
the release index and forces `access_tier: private` so the authenticated preview
viewer signs short-lived GCS URLs for FGB downloads and PMTiles previews. This
prevents the preview UI from silently linking to production `latest/` objects.
The generated catalog also preserves every file entry from each preview release
index in `versions[].files`, including metadata sidecars, schemas, manifests,
and any other new sidecar datafiles that belong to the preview release bundle.

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

Preview data loading is explicit and does not use the production
`_scratch/pending-publishes/` promotion path. The preview load workflow requires
preview-bucket artifact URIs and exact generations.

After the preview release bundle is in place, use the relevant preview load
workflow for the feature under test.

Inputs:

- `ref`: branch, tag, or SHA whose loader code should run.
- `asset_slug`: exact asset slug.
- `release`: concrete `YYYY-MM-DD` release.
- `sidecar_uri`, `schema_uri`, and `manifest_uri`: preview-bucket release
  `.metadata.ndjson.gz`, `.schema.json`, and `.manifest.json` artifact URIs.
- `sidecar_generation`, `schema_generation`, and `manifest_generation`: exact
  GCS object generations.
- `load_id`: optional; when omitted, the workflow uses
  `github-{run_id}-{run_attempt}`.

The workflow keeps the preview workflow code checked out at the workspace root
and checks out the requested `ref` under `preview-source/` for the catalog and
loader code. It authenticates with the preview loader identity, downloads the
exact preview objects, loads documents into the preview Firestore database,
writes a load record back to the preview bucket, and refreshes the preview
catalog viewer bundle from the preview release indexes.

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

## Agent Notes

Use `.claude/skills/feature-preview/SKILL.md` for feature preview,
preview test dataset, and feature preview requests.

Preview test data is not production publishing. Do not use production
`_scratch/pending-publishes/`, `shared-datasets-publish-plan`, or the
`Approved dataset mutation` workflow. Upload disposable release bundles directly
to `gs://skytruth-shared-datasets-1-preview/` with safe preconditions, stat the
exact generations, and pass the explicit preview-bucket URIs and generations to
the preview load workflow inputs documented above.
