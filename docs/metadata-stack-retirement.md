# Firestore Metadata Stack Retirement

The protected retirement completed on **2026-10-01**. The standalone Firestore
metadata endpoint, index loaders, their obsolete Cloud Run/IAM resources, and
three remaining retired service accounts were removed. The one-time workflow
and its temporary account-level deletion authority are retired.
The [successful protected run](https://github.com/SkyTruth/shared-datasets-1/actions/runs/36899723137)
removed the final accounts and completed temporary-authority cleanup.

Read-only verification on **2026-10-09** with Terraform 1.8.5 confirmed that
`terraform -chdir=terraform/envs/metadata-retirement-iam init -input=false`
succeeded and `state list` returned no addresses (exit 0, empty stdout/stderr).
The backend was `gs://skytruth-shared-datasets-1/` with prefix
`000-system/terraform/state/metadata-retirement-iam`.
Project IAM and service-account listing confirmed no matching bindings or
`roles/iam.serviceAccountDeleter` grant, and no accounts matching these emails
or immutable IDs:

| Retired account | Immutable ID |
|---|---|
| `metadata-index-loader` | `117696104962177306505` |
| `metadata-index-loader-preview` | `104364831142635248810` |
| `metadata-service-preview` | `115924014602363410114` |

The empty temporary IAM root and completed production-retirement validator modes
have been removed. Routine CI validates only the production and preview roots;
it no longer initializes, validates, or tests the temporary retirement root.

## Active preview preservation

Both Firestore databases and their data remain preserved. The production and
preview Terraform roots retain database `removed` blocks with `destroy = false`.
Preview deploy/reset and destroy workflows still detach the preview database
with a separate saved plan validated by
`scripts/metadata_retirement_plan.py --database-only preview` before destructive
planning. The validator accepts only `forget` for the named preview database and
no-op/read actions on other resources. Database deletion and unrelated mutations
are refused. Selected preview source branches that still declare a Firestore
database resource are refused.

Viewer routes, generation-pinned GCS sidecars, localization, SDK readers, and
release publication remain active. Historical `index-loads/` records and
serialized index-status fields remain readable. `firestore.googleapis.com`
remains enabled; deleting database data, changing persisted formats, or cleaning
up historical objects requires separately reviewed scope and live usage evidence.
