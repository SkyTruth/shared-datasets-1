# Preview IAM refresh dependencies

The protected preview IAM sync targets its two service accounts and their two IAM
members. Terraform also refreshes their managed references: the GitHub workload
identity pool, the catalog viewer signing role and the twelve enabled project
services. Readiness must cover this entire graph before Terraform plans it.

The locked `hashicorp/google` 7.31.0 provider reads the pool and unconditionally
lists its attestation rules, including for a federation-only pool without any
configured attestation rules. Both `iam.workloadIdentityPools.get` and
`iam.workloadIdentityPools.getAttestationRules` must pass on the actual pool.
The existing preview role grants only those pool read permissions; it does not
receive pool create, update, delete, IAM-policy or rule-writing authority.

The remaining refresh contract is:

| Resource | Provider read | Permission |
| --- | --- | --- |
| Preview service accounts | Get each service account | `iam.serviceAccounts.get` |
| Preview IAM members | Read each service account's policy, version 3 | `iam.serviceAccounts.getIamPolicy` |
| Signing custom role | Get the project role | `iam.roles.get` |
| Enabled services | Get the project and list enabled services | `resourcemanager.projects.get`, `serviceusage.services.list` |

`serviceusage.services.get` alone cannot satisfy the provider's enabled-services
list. The pre-plan probe requires the actual read operations and retains the
existing service-account mutation prerequisites. Saved-plan permission checks
still run after the plan allowlist and before its signed claim and apply.

The independent audit fixture
`tests/fixtures/ci_failures/preview_iam_provider_read_contract.json` records the
provider version, upstream tree and source blob hashes, complete read call graph
and targeted resource closure. Tests compare it with the provider lock and the
readiness probe. A provider upgrade requires re-auditing these reads and updating
the fixture; changing the version without that review fails validation.

The original two failures are preserved separately in
`preview_iam_pool_read.json` and `preview_iam_pool_attestation_read.json`. Both
failed during refresh before a deployment claim or apply. Negative controls
reject a get-only pool response and an enabled-service get hint that lacks list
permission. Recovery follows a newly validated main revision; old production
mutations are not blanket-retried.

Audited primary implementation:
[pool Read](https://github.com/hashicorp/terraform-provider-google/blob/v7.31.0/google/services/iambeta/resource_iam_workload_identity_pool.go),
[service Read](https://github.com/hashicorp/terraform-provider-google/blob/v7.31.0/google/services/resourcemanager/resource_google_project_service.go),
[enabled-service batching](https://github.com/hashicorp/terraform-provider-google/blob/v7.31.0/google/services/resourcemanager/serviceusage_batching.go)
and [enabled-service listing](https://github.com/hashicorp/terraform-provider-google/blob/v7.31.0/google/services/resourcemanager/resource_google_project.go).
