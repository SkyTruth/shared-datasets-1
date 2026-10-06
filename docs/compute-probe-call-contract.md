# Permission-probe call prerequisites

A permission probe can require authority to call the probe itself. That
authority is distinct from permission to perform the proposed operation.
`deployment_permissions.py` checks the selected Compute probe-call permissions
on the project first, then checks the operation permissions on each actual
resource. Neither phase substitutes for the reviewed saved-plan allowlist,
protected identity, signed deployment claim or provider authorization.

The complete endpoint-family audit is retained in
`tests/fixtures/ci_failures/compute_probe_call_contract.json`. Compute v1 API
discovery revision `20260922` contains all five supported method paths. The
regional list permissions below come from each method's IAM requirements;
they are not inferred from the URL or treated as global permission aliases.

| Actual probe endpoint | Permission required to call it |
| --- | --- |
| [Global URL map](https://docs.cloud.google.com/compute/docs/reference/rest/v1/urlMaps/testIamPermissions) | `compute.urlMaps.list` |
| [Global backend bucket](https://docs.cloud.google.com/compute/docs/reference/rest/v1/backendBuckets/testIamPermissions) | `compute.backendBuckets.list` |
| [Global backend service](https://docs.cloud.google.com/compute/docs/reference/rest/v1/backendServices/testIamPermissions) | `compute.backendServices.list` |
| [Regional backend bucket](https://docs.cloud.google.com/compute/docs/reference/rest/2026-10-01-preview/regionBackendBuckets/testIamPermissions) | `compute.regionBackendBuckets.list` |
| [Regional backend service](https://docs.cloud.google.com/compute/docs/reference/rest/v1/regionBackendServices/testIamPermissions) | `compute.regionBackendServices.list` |

The same audit covers project, bucket, managed-folder, service-account,
workload-identity-pool, Artifact Registry repository, secret, Cloud Run job,
Cloud Run service and IAP service permission-probe endpoints. Their primary
references either explicitly permit the call without an IAM permission or list
OAuth scopes without an additional call-specific IAM permission. Their original
resource-operation checks remain intact. An API, network or malformed-response
failure stops readiness; it is never converted into success or retried as an
operation.

For the current no-change CDN plan, only URL-map cache invalidation is selected.
The existing `sharedDatasetsPmtilesUrlMapSync` role has that operation authority
but lacks the list permission needed to call its resource probe. The protected
CDN bootstrap adopts this existing role at
`google_project_iam_custom_role.pmtiles_url_map_sync`, retaining its title,
description, GA stage and all nine permissions. It adds `compute.urlMaps.list`
and `compute.backendBuckets.list`: the latter is needed when a normal catalog
routing update checks use authority on its existing global backend bucket.
This includes the existing consumer's update path as well as today's no-change
cache invalidation. It introduces no identity or binding. The independent
bootstrap identity already has role read/update authority.

Google currently marks `compute.urlMaps.list` as **TESTING** for custom-role
compatibility. [Google permits such permissions in custom roles but does not
fully support that compatibility](https://docs.cloud.google.com/iam/docs/roles-overview).
The narrowly scoped grant retains the existing role instead of replacing it
with a broader predefined role. Live prerequisite verification remains required.
The backend-bucket list permission has the default SUPPORTED custom-role
compatibility status in the complete IAM metadata response. Other Compute
probe-call permissions are modeled and tested; this repair does not grant them
for unselected backend-service or regional operations.
The variable default is `redirect`, but the committed production auto-tfvars
selects `cdn` and the protected workflow supplies no serving-mode override.
Offline readiness binds this role to that effective mode and the existing
global backend bucket. Switching mode or introducing another active backend
fails before merge until its operation and probe-call authority is reviewed.
Additional automatically loaded Terraform variable files or unmodeled CLI and
environment variable overrides also fail this contract instead of silently
changing the effective production mode.

The initial import/update plan must preserve the exact live role identity and
metadata and add exactly `compute.urlMaps.list` and
`compute.backendBuckets.list`. A
later complete-role no-op is allowed. Creation, deletion, replacement, unrelated
role adoption, unknown values, extra permissions and dropped permissions fail
the saved-plan guard. Terraform 1.8.5 still reads a new import with
`-refresh=false`; the pinned Google 7.31.0 role reader uses `Projects.Roles.Get`,
covered by the existing bootstrap `iam.roles.get` check. Other targeted
deployments do not target or depend on this role.

The historical failure fixture records run `37438276210/1`, job
`112187253799`. Both plans reported no changes and the no-change mapper selected
one URL-map permission probe, which returned HTTP 403 before dependent apply or
cache invalidation. Its required list permission was absent from the deployer's
live role bindings. The original error body and matching Compute audit entries
were unavailable; endpoint attribution follows the no-change plan contract.
Future HTTP errors retain the URL, status and at most 4 KiB of structured vendor
diagnostics, redacting the bearer token and omitting incomplete or oversized
bodies. They still fail loudly.
