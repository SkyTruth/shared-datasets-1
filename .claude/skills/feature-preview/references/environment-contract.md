# Preview environment contract

Read for deployment/IAM/state questions; upload procedure lives in SKILL.md.

The preview is one replaceable test slot in `shared-datasets-1`, running reviewed
main code against disposable data. Its Cloud Run, bucket and IAM resources are
preview-named; state lives under `000-system/terraform/state/preview`.
The preview bucket uses `force_destroy = true`. Keep canonical releases,
production-only credentials and irreplaceable data elsewhere.

The private comparison cache at
`gs://skytruth-shared-datasets-1-preview-comparisons/` stores job state and indexes
across viewer instances. Objects expire after one day; preview destruction also
removes this bucket. Cache writes are limited to that disposable bucket.

## Deployment and state boundaries

The protected deploy workflow pins all executable code, Dockerfiles, dependency
files and Terraform configuration to its immutable main workflow SHA.
`source_ref` must resolve to that exact SHA, normally using `refs/heads/main`;
unmerged branches, older tags and changed main refs fail before dependency
installation or authentication. The ref is validated as data and never checked
out. Deploy and destroy require the exact main revision's merged PR owner
acceptance. Supporting unmerged code again requires a separate preview identity
without production authority.

Deploy plans `terraform/envs/preview` through the preview resource-change
allowlist. `preserve` keeps data and rebuilds the catalog from existing release
indexes. `reset` applies a saved destroy plan before recreating the stack and
publishing a catalog shell. Both modes report service and catalog viewer URLs.

The protected **Infrastructure / Preview Terraform IAM sync** owns the stable
`feature-preview-service` and `feature-preview-loader` accounts, service
self-signing grant and loader WIF binding in
`terraform/envs/prod/preview_terraform_iam.tf`. Deploy checks these bindings before
Docker builds or reset. The loader publishes the catalog and needs its preview
bucket write grant. Provision these prerequisites from reviewed main first.

Deploy/destroy remove legacy stable-account and loader-WIF entries from preview
state without deleting the live resources. Before deployment, reset or destroy,
a saved plan detaches the retired `feature-preview` Firestore database with
`destroy = false`. `metadata_retirement_plan.py --database-only preview` permits
only forgetting that database and no-op/read actions elsewhere. The data remains
untouched outside the disposable stack. Do not recreate retired production
metadata modes or their temporary IAM root; see
[metadata stack retirement](../../../../docs/metadata-stack-retirement.md).

## Destroy and authentication

**Preview / Destroy environment** checks out the immutable main workflow SHA,
plans `terraform/envs/preview` with `-destroy`, enforces the preview allowlist and
applies only that saved plan. It removes preview services, disposable buckets
and preview-root IAM, while retaining stable service accounts and loader WIF.

Both workflows use `shared-datasets-production` and the production Terraform
identity, so only owner-accepted main code may execute. The Terraform root and
service accounts are preview-specific. A separate preview identity/environment
requires coordinated workflow, WIF, resource scope and backend changes before
unmerged feature branches can execute again.
