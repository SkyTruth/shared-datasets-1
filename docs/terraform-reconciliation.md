# October Terraform reconciliation

The October 27 read-only audit found 20 resources left over from the retired
operational observer/schema alert and one WDPA launch-stage mismatch. Preview
had no differences. Terraform's saved production state still tracked those
resources; this reconciliation removes the live leftovers through the existing
protected executor, then verifies both environments with full untargeted plans.

The integration/recovery owner is the chat that opened this PR, on branch
`codex/reconcile-production-terraform`. There are no prerequisite PRs. Concurrent
image-artifact work owns `.github/workflows/ci.yml` and `docs/ci-preflight.md`;
this change does not edit those files. Merge current main before final validation
if either that work or another prerequisite lands first.

## Protected sequence

`scratch-cleanup-iam-sync.yml` continues to use
`prod-terraform-target-apply.yml`, with the production environment, serialized
state queue, exact tested-main provenance, saved-plan allowlist, live operation
permission probes and signed deployment records intact.

1. `operational-retirement-bootstrap` creates the dedicated role without granting
   it to anyone.
2. `operational-retirement-grant` temporarily binds it to the Terraform deployer
   after role creation. The grant is disabled by default. Its role name is
   independent of the managed role resource so revocation cannot recreate it.
3. `retire-operational-infrastructure` targets exactly the 20 resource instances
   in its `targets`/`allowed_exact` lists. `delete_only` permits pure deletions
   and no-ops; it rejects creations, updates, replacements and unrelated deletes.
4. `revoke-operational-retirement` disables/removes the binding after any attempted
   grant, including a failed grant or retirement. The unbound role stays
   in Terraform for auditability; it grants no authority on its own.

The reviewed scope is one old WIF provider, eight filtered log views, two custom
heartbeat metric descriptors, seven old custom roles, the old observer service
account, and the obsolete dataset-schema alert. Buckets, dataset objects, active
Cloud Run jobs/services, schedulers, current identity providers and current alert
policies are outside its mutation allowlist. Removing a log view preserves the
underlying logs. Removing the two retired metric descriptors makes their old
time series inaccessible; this is part of the reviewed retirement. See
[Google's metric deletion contract](https://docs.cloud.google.com/monitoring/custom-metrics/creating-metrics#delete-metrics).

The sanitized saved-plan fixture is
`tests/fixtures/terraform_targets/operational-retirement.json`. Tests feed those
serialized bytes through the actual workflow shell guard and the saved-plan
permission derivation, including empty/partial plans and negative controls.
`terraform/iam-plan-prerequisites.json` records the 13 absent declarations behind
the 20 instances; it does not expand the exact instance-level mutation allowlist.

## Approved temporary authority

The user explicitly approved this scope and absolute window on October 27.
The project binding expires at **2026-11-03 00:00 UTC**, independently of immediate
revocation. It grants the Terraform deployer the following additional permissions:

```text
iam.roles.delete
iam.serviceAccounts.delete
iam.serviceAccounts.get
iam.workloadIdentityPoolProviders.delete
iam.workloadIdentityPoolProviders.get
logging.views.delete
logging.views.get
monitoring.metricDescriptors.delete
monitoring.metricDescriptors.get
```

While enabled, this is project-level authority for those resource classes, not
an IAM restriction to the target names. IAM role/account/provider permissions
cannot use resource-name conditions to establish that narrower boundary. The
reviewed saved plan and exact allowlist establish the workflow's mutation scope.
Existing Monitoring policy deletion and IAM bootstrap permissions are reused.
No dataset object writes, job execution, bucket deletion or pool deletion
permission is added. Do not silently extend the expiry or revive the old expired
WDPA retirement grant.

If the retirement or revocation fails, inspect its signed record and live state
before recovery. Finish revocation through the protected workflow; do not perform
a local apply. A partial retirement is not completion. The absolute expiry is a
backstop for interruption, not evidence that immediate revocation succeeded.

## WDPA and completion

Cloud Run reports the deployed job at `GA`, including its 100 GiB `/work` disk.
The shared job module now declares `GA`; it retains the disk, resources, task
limits and exact image. The API reports the least stable feature stage actually
used, which can differ from an input preview stage; see the
[Cloud Run Job contract](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs#Job).
The full candidate plan proves the change eliminates the WDPA update without
changing the job's image or disk.

After review/merge and the protected sequence, verify signed terminal outcomes,
absence of the temporary binding and all 20 retired resources, and full refreshed
production/preview plans using recorded deployment image inputs. Only a clean
plan in both environments satisfies completion. Then delete the rollback backup
branch with its expected-OID guard and verified Git recovery evidence. Removing
that Git bookmark alone never changes infrastructure or Terraform state.
