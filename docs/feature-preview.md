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
existing preview release indexes. In `reset` mode, it first plans and applies a
saved destroy reset from the selected branch preview Terraform, waits if the
preview Firestore database ID needs reuse time, then creates the new preview
stack and publishes a catalog shell. In both modes, it prints the preview
service and catalog viewer Cloud Run URLs.

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
when you need to tear down the previous preview bucket, Firestore database,
Cloud Run services, and preview bucket/IAP IAM bindings before creating the new
preview deployment. Reset clears previous preview data that is no longer present
in the selected ref.

The deploy workflow does not own the stable conditioned project IAM grants that
let the preview service and preview loader use the preview Firestore database,
the stable preview service accounts, the preview service self-signing grant used
for catalog viewer signed URLs, or the preview loader Workload Identity binding.
Those bootstrap resources are managed by the protected GitHub Actions workflow
named `Preview Terraform IAM sync` from
`terraform/envs/prod/preview_terraform_iam.tf`.
That sync owns creation of `feature-preview-service` and
`feature-preview-loader`; deploy and destroy only validate or use those stable
identities.
That sync workflow applies only from `main` after the reviewed control-plane
changes have landed. Deploy validates those bootstrap resources before Docker
build or Terraform reset, and fails without mutating the preview slot if the
sync has not run.

During the migration from the earlier preview root, deploy reset first removes
the stable preview service accounts and loader Workload Identity binding from
preview Terraform state without deleting the live resources. The saved preview
reset plan may also delete old preview-stack resources that remain in
`terraform/envs/preview` state from earlier naming.

## Destroy The Preview

Use the GitHub Actions workflow named `Destroy Preview Environment`. The
protected workflow applies only from `main`, checks out the reviewed `main`
control plane, plans `terraform/envs/preview` with `-destroy`, enforces the
preview-resource allowlist, and applies only that saved destroy plan. This
removes the preview Cloud Run services, preview bucket contents, preview
Firestore database, preview IAM bindings owned by the preview root. It does not
delete the stable preview service accounts or loader Workload Identity binding.

## Preview Catalog Viewer

The deploy workflow creates an IAP-protected preview catalog viewer at the
`preview_catalog_viewer_uri` Terraform output. It serves `_catalog/web/` from
`gs://skytruth-shared-datasets-1-preview/`, not from the production bucket.

Preserve-mode deploys rebuild the catalog from existing release indexes under
`gs://skytruth-shared-datasets-1-preview/_catalog/releases/*.json`. Reset-mode
deploys publish a catalog shell with no listed assets until preview bundles and
release indexes are uploaded again. After uploading data, dispatch
`Deploy Feature Branch to Preview` with `preview_data_mode=preserve`. It
refreshes the catalog by downloading every preview release index under
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

The preview service reads release indexes and feature metadata sidecars from
the preview bucket. Firestore serving is inactive and is not required for
current preview lookups. Write the complete release bundle under:

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

Preview uploads do not use the production `_scratch/pending-publishes/`
promotion path. Use `publishing_concierge.py start` with
`--request-classification preview-only`, `--release-date`, and
`--preview-ref`, then follow `next` and `confirm` through the preview bundle
and validation gates.

After uploading with safe generation preconditions, record every object's
preview-bucket URI and exact generation in the concierge's `preview-upload`
evidence. Follow its `preview-catalog-refresh` instructions:

```bash
gh workflow run feature-preview-deploy.yml --ref "$PREVIEW_REF" \
  -f preview_data_mode=preserve
```

Select the source branch or tag in `$PREVIEW_REF`. Wait for the workflow's
catalog collection, build, and publication steps to succeed, then verify that
the preview viewer lists the intended release and enriches clicked features
from its sidecar. Record the catalog generation and viewer verification in the
concierge evidence before reporting completion.

### Dormant Firestore Loader

`.github/workflows/feature-preview-index-load.yml` still contains Firestore
loader plumbing, including generation-pinned sidecar/schema/manifest inputs.
It is not part of the current sidecar-backed upload and catalog-refresh path.
Do not dispatch it while serving is inactive. A feature that explicitly enables
Firestore must establish its reviewed serving contract and validate the exact
workflow and loader at the chosen ref before loading an index. See
[Feature Metadata API operations](feature-metadata-api.md#operations).

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
exact generations, record them in concierge upload evidence, and refresh the
catalog with a preserve-mode deploy. Firestore loads remain dormant.
