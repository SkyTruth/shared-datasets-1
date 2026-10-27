# October Terraform reconciliation

The October 27 read-only audit found 20 resources left over from the retired
operational observer/schema alert and one WDPA launch-stage mismatch. Preview
had no differences. Terraform's saved production state still tracked those
resources; this reconciliation removes the live leftovers through the existing
protected executor, then verifies both environments with full untargeted plans.

The originating reconciliation chat owns integration and recovery. Retirement
landed in PR #324 from `codex/reconcile-production-terraform`, after the image
handoff and selector prerequisites merged. The state/output follow-up uses
`codex/reconcile-terraform-state-outputs` and requires the completed retirement
and revocation. Merge current main before final validation when necessary.

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

## Final state and output reconciliation

The protected retirement in PR #324 completed all 20 deletions and revoked the
temporary binding. A full post-deployment normal plan found no resource actions,
but it still exited 2 for five saved root outputs. Targeted applies had retained
four removed outputs and stale managed-folder metadata:

- Delete `metadata_index_loader_service_account`.
- Delete `operational_observer_log_views`.
- Delete `operational_observer_service_account`.
- Delete `operational_observer_workload_identity_provider`.
- Refresh `shared_bucket_public_managed_folders` from the live folder metadata.

`prod-terraform-state-reconcile.yml` reconciles this bookkeeping through the
existing protected executor after a reviewed merge and successful main CI. It
supplies an empty target list, so Terraform creates a full normal refreshed plan,
and an empty resource allowlist, so every resource create, update, replacement
or deletion is refused before the saved plan can be applied. Terraform can then
persist refreshed state and outputs without mutating live resources. There is no
new apply implementation, executor flag, IAM grant, image preparation or retry.
The offline graph contract recognizes only a paired-empty scope without resource
prerequisites or side-effect preparation. Nonempty IAM scopes retain their
existing graph checks.

Dispatch this workflow from reviewed `main` with `executor_sha`, `source_run_id`
and `source_run_attempt` identifying that revision's successful main-push CI.
Supply `tf_vars` as newline-separated current state-recorded production deployment
variables, including immutable job/service images and the Monitoring receiver's
channel and numeric token version. These are planning inputs, not permission to
redeploy images. Stale or incompatible inputs that propose any resource mutation
fail the empty allowlist. The workflow is not wired to automatic main deployment,
and it does not replay retirement or re-enable temporary authority.

The normal saved-plan fixture is
`tests/fixtures/terraform_targets/state-only-outputs.json`. Boundary tests run
the actual plan shell and resource guard, proving that the full plan omits
targets, preserves variable arguments, accepts zero/one/all five output changes,
and rejects every resource action, including revival of the retired grant.

After the signed state-only apply succeeds, run fresh full normal production and
preview plans again with current deployment inputs. Both must exit 0, with stable
remote state, all 20 resources retired and the temporary binding absent, before
the rollback backup can be deleted. A refresh-only plan or a zero-resource count
with remaining output differences does not satisfy this final gate.
