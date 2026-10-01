# Firestore Metadata Stack Retirement

This branch retires the standalone Firestore metadata endpoint and its loaders.
It preserves the current consumer contract: generation-pinned release sidecars,
both viewers' same-origin lookup routes, signed/public download resolution,
localization, SDK readers, and release publication. Repository cleanup does not
perform the live infrastructure retirement.

## Repository Scope

| Removed file or symbol | Reason and replacement |
|---|---|
| `services/metadata_service/{run.py,Dockerfile,__init__.py}` | Standalone resolver rejected every resolved release with `409 index_not_ready`; serving is retired. |
| `services/feature_preview_service/run.py::FirestoreFeatureIndex` and `DEFAULT_COLLECTION_ROOT` | Neither viewer constructed this adapter; both use `GcsSidecarFeatureIndex`. |
| `services/catalog_viewer/run.py::feature_collection_root_from_env` and collection-root arguments | Configuration belonged to the unused adapter. |
| `scripts/feature_metadata_index.py`, `scripts/feature_preview_index.py` | Firestore writes and index-load report publication are retired. Local bundle validation survives in `scripts/validate_feature_metadata.py`. |
| `.github/workflows/{feature-metadata-index-load,feature-preview-index-load,metadata-service-deploy,metadata-index-loader-iam-sync}.yml` | No loader, standalone image build, deployment, or loader IAM bootstrap remains. |
| `scripts/publishing_concierge.py::{preview_firestore_load_enabled,preview_load_required,commands_for_preview_load,validate_preview_load}` and `preview-load` step | Preview completion requires upload, catalog refresh, and viewer verification. Old saved load flags cannot restore the step. |
| `scripts/release_feature_model.py::{index_load_record_name,MAX_FIRESTORE_DOCUMENT_BYTES}` | Unused Firestore helpers; active sidecar size checks remain. |
| Compliance audit `index_load_matches` | Uncalled historical serving-readiness helper. Historical path recognition remains. |
| Identity-reset independent index-report mutation exception | A retired loader can no longer bypass owned publication. Historical records remain readable. |
| Firestore dependency in `pyproject.toml`, `uv.lock`, and both viewer Dockerfiles | Active services require Cloud Storage; no Firestore SDK import remains. |
| `index_backend` frontmatter in 20 asset docs and both README templates | Unused dormant backend annotation; sidecar/schema/manifest paths remain. Generated catalog consumer fields are unchanged. |
| Standalone/loader tests | Replaced by local validation and constrained retirement tests; active sidecar, localization, publication, and viewer tests remain. |

Documentation now describes the viewer route in `docs/feature-metadata-api.md`,
preview deployment in `docs/feature-preview.md`, and sidecar consumption in the
consumer guide and both SDK READMEs. Updating the packaged TypeScript README
requires the documentation patch version `0.9.1` under the existing release
policy; TypeScript runtime source is unchanged. The preview skill and `AGENTS.md` route future
work to this contract.

## Protected Infrastructure Scope

`terraform/envs/prod/metadata_service.tf` retains only a `removed` block for
`google_firestore_database.feature_metadata` with `destroy = false`.
`terraform/envs/preview/main.tf` does the same for
`google_firestore_database.feature_preview`. These retain the live `(default)`
and `feature-preview` databases and their data, while ending Terraform ownership.
Terraform 1.7 or newer is required; CI and protected workflows use 1.8.5.
[HashiCorp documents this state-removal behavior](https://developer.hashicorp.com/terraform/language/block/removed).

The manual, opt-in `.github/workflows/metadata-stack-retire.yml` runs only from
reviewed `main` in `shared-datasets-production`, sharing the production state
queue. `scripts/metadata_retirement_plan.py` allows only these removals:

```text
module.metadata_service_account.google_service_account.this
module.metadata_index_loader_service_account.google_service_account.this
google_cloud_run_v2_service.metadata_service
google_cloud_run_v2_service_iam_member.metadata_service_iap_invoker
google_iap_web_cloud_run_service_iam_member.metadata_service_accessors[...]
google_project_iam_member.metadata_service_firestore_viewer
google_project_iam_member.metadata_index_loader_firestore_user
google_storage_bucket_iam_member.metadata_service_object_viewer
google_storage_bucket_iam_member.metadata_index_loader_object_viewer
google_storage_bucket_iam_member.metadata_index_loader_index_load_creator
google_storage_bucket_iam_member.metadata_index_loader_index_load_folder_admin
google_service_account_iam_member.metadata_index_loader_github_wif
google_monitoring_alert_policy.metadata_service_error_logs
google_project_iam_member.feature_preview_service_firestore_viewer
google_project_iam_member.feature_preview_loader_firestore_user
module.preview_metadata_index_loader_service_account.google_service_account.this
module.preview_metadata_service_account.google_service_account.this
google_project_iam_member.preview_metadata_index_loader_firestore_user
google_project_iam_member.preview_metadata_service_firestore_viewer
google_service_account_iam_member.preview_metadata_index_loader_github_wif
```

Read-only live state inspection on 2026-10-01 found the last five older preview
addresses after the original retirement PR merged. Their principals are
`metadata-index-loader-preview` and `metadata-service-preview`, not the active
`feature-preview-loader` catalog publisher or `feature-preview-service`. The
older Firestore grants name the absent `feature-metadata-preview` database.
Neither older identity appears in live Cloud Run service/job consumers or
preview bucket IAM. Their source resources had already been removed; the
follow-up expands the protected target list to clean up this orphaned state.
The validator requires the observed project, account/role/member, database
condition, or Workload Identity binding before allowing each legacy deletion.
No replacement, active-preview identity removal, or database deletion is allowed
in the retirement plan. Temporary account-scoped deletion authority is validated
separately as described below.

Two updates are also allowed: remove only `datastore.*` permissions from the
preview Terraform custom role, and remove only the production metadata loader's
exemption from the canonical-write alert filter. All other creates, updates,
replacements, deletions, or database destruction are rejected. Required root
ingestion-image inputs use placeholders because ingestion jobs are outside the
targeted scope. The workflow validates each saved plan and applies that same plan.

The first protected retirement run on 2026-10-01 detached the production database
and removed ten obsolete IAM grants. Its apply then failed because
`shared-datasets-terraform` lacked `iam.serviceAccounts.delete` on the three
remaining retired accounts. Both databases survived; production state contained
only these three retired service-account resources afterward. Retrying without
changing that authority cannot complete retirement.

The permission repair uses `terraform/envs/metadata-retirement-iam` with its own
state prefix, `000-system/terraform/state/metadata-retirement-iam`, under the same
protected production queue. It grants `roles/iam.serviceAccountDeleter` to
`shared-datasets-terraform` on individual retired accounts, never on the project.
The three permitted immutable IDs are:

| Retired account | Immutable ID |
|---|---|
| `metadata-index-loader` | `117696104962177306505` |
| `metadata-index-loader-preview` | `104364831142635248810` |
| `metadata-service-preview` | `115924014602363410114` |

The retirement validator requires the observed account ID, email, project, and
immutable ID before deleting these resources. Temporary IAM grants are derived
only from account deletions present in that validated saved plan; their own
validator requires the exact resource ID, recipient, and deletion-only role.
The provider requires an email for the IAM binding, so a fresh Terraform account
lookup must also match the reviewed immutable ID before authority is granted.
An account recreated with the same email fails that postcondition.
[Google documents account-level IAM grants](https://docs.cloud.google.com/iam/docs/manage-access-service-accounts)
and the [deletion permission](https://docs.cloud.google.com/iam/docs/reference/rest/v1/projects.serviceAccounts/delete).

After applying the saved IAM plan, the workflow polls the authenticated caller's
deletion permission with a five-minute deadline and 30-second request timeouts,
using `testIamPermissions` without logging the access token. API errors and timeout
stop before the retirement apply. The workflow then applies the original saved
retirement plan. It finishes with an empty-input IAM plan, validated as
delete-only, to remove temporary authority and clear its state. If a run stops
partway through, remote IAM state supports a subsequent reviewed retry;
any surviving grant can affect only a still-existing reviewed retired account.
The new IAM root defaults to no grants and receives backend-disabled CI
validation alongside production and preview.

Preview deploy/destroy workflows detach the retired preview database with a
separate validated saved plan before a reset or destroy can affect the slot.
The production retirement workflow also detaches its database in a separate
validated saved plan before service/IAM removal. Only these state-only database
plans skip refresh: they cannot mutate a live resource and must remain usable
after Terraform loses its Firestore read permissions. The production service/IAM
retirement plan still refreshes live resources and rejects database changes.
Source branches with a Firestore database resource are refused. Preview service
accounts and their signing/WIF bindings remain. In particular,
`feature-preview-loader` still publishes `_catalog/web/`; its bucket write grant
keeps its existing Terraform address to avoid unnecessary IAM churn.

`firestore.googleapis.com` remains enabled. Other project usage is unknown and
disabling the API would broaden retirement. Database deletion, API disabling,
historical GCS record deletion, image cleanup, and repository-variable cleanup
are separate follow-ups requiring an explicit scope and live usage evidence.

## Compatibility And Consequences

Retirement does not rewrite dataset releases or release-index objects. Normal
viewer deployments still rebuild `_catalog/web/`. The serialized
`index_load_status` string and `index_status_policy.mode = inactive_firestore_serving`
remain in manifest/release-index producers and validators. Changing them would
be a persisted-format migration. Historical `index-loads/` paths continue to be
recognized by object-layout validation and compliance audits.

Consumers of the old standalone Cloud Run URL lose that endpoint after the
protected retirement runs. Repository consumers already use sidecars or viewer
routes, and the old resolver had no successful serving path. External callers,
out-of-repository loader users, and database contents remain unknown. GCP access
was restored for rollout checks: the standalone service was already absent,
neither older preview identity appeared in project Cloud Run service/job
consumers, and bounded 30-day logs contained no matching requests or principal
activity. Absence of those logs does not establish absence of external or
other-project usage. The maintainer approved the identity removals;
database bytes remain preserved.

## Validation And Rollout

Before merge, run the full Python suite, Ruff, TypeScript SDK tests, static repo
guardrails, Terraform formatting, and backend-disabled validation of all three roots
with the CI version. Validator tests exercise duplicate/invalid IDs, hashes,
release identity, schema projection, checksums, counts, and generation/path
mismatches. Viewer tests retain pinned downloads, cache isolation, ETags,
bounded lookup, and catalog download behavior. Retirement tests reject database
deletion and any mutation outside the named scope.

Local verification on 2026-10-01: the full Python suite passed 1,044 tests
and 1,147 subtests; four native geospatial integration tests were skipped for
missing/disabled native tools. All 41 TypeScript SDK tests passed at `0.9.1`.
Ruff, static guardrails, workflow shell parsing, recursive Terraform formatting,
and backend-disabled Terraform 1.8.5 validation of both roots passed. All 20
asset docs produced identical normalized feature-metadata fields and retained
their identity, translation, and artifact declarations. An isolated local-state
plan confirmed `forget` in the targeted removal plan and `delete` under destroy
mode, validating the need to detach before preview reset/destruction. No apply,
workflow dispatch, GCS write, or live IAM change was performed.

Follow-up readiness checks passed 44 focused tests and 118 subtests, including
execution of the actual production workflow shell against a Terraform stub.
Both targeted plans received all required root inputs, applied their own saved
plans, and a simulated database deletion stopped before any apply. Static
guardrails and Ruff also passed. A fresh read-only GCP check still required
interactive reauthentication. GitHub access worked; the current main rules do
not require CODEOWNER approval and the production environment has no reviewer
gate. The repository's maintainer-review requirement therefore remains a
manual prerequisite; these GitHub settings do not waive it.

After maintainer review and merge, run the opt-in retirement workflow. It checks
the fresh saved plan before applying; unexpected drift stops the run and requires
a reviewed scope change. Use a preserve-mode preview deployment
and the normal catalog viewer deployment to roll out the dependency/configuration
cleanup. Verify authenticated same-origin lookup, exact returned sidecar identity,
large-sidecar inspection, public/private download URLs, localized sidecars,
preview catalog refresh, and unaffected publication/ingestion jobs. Do not
dispatch loaders, remove IAM locally, or apply production Terraform locally.

The maintainer decision is whether to merge this sidecar-only serving contract
and retire the standalone URL and loader identities through the protected path.
Database data deletion is excluded. Reactivation would require a new reviewed
consumer requirement, an exact-generation serving contract, rebuild validation,
cost/ownership decisions, and a protected deployment/loading path.
