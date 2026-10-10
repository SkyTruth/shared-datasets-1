# WDPA validation retirement recovery

Main CI run 38033531700, attempt 1, stopped before its deployment claim and
the protected GitHub Actions mutation. The six-resource retirement plan passed the allowlist, but the
protected Terraform identity lacked `iam.roles.delete`,
`iam.serviceAccounts.delete` and `run.jobs.delete`. Passing bucket-IAM readiness
could not establish those saved-plan permissions.

The existing scratch-cleanup caller now bootstraps a separate custom role and
binding before retirement. Its bootstrap permits only those two IAM resources,
blocks deletes, records its own tested deployment and waits for propagation.
Retirement still permits exactly its original six resource addresses and probes
the complete saved plan before claiming or applying it. Monthly resources,
reader IAM, immutable build evidence and promotion contracts stay outside it.

The grant contains only the three missing delete permissions. It applies to
the protected Terraform identity at project scope until **2026-10-11 12:00 UTC**,
then expires through a fixed `request.time` condition. This is a temporary broad
delete grant within the project, not a resource-scoped IAM restriction. It needs
explicit human approval under AGENTS.md before merge activates it. The workflow
allowlist constrains actual Terraform changes; it does not narrow the IAM grant.
The persistent ingestion deployer gains no deletion permission.

Google's [resource-based condition documentation](https://docs.cloud.google.com/iam/docs/configuring-resource-based-access)
states that IAM resources do not provide `resource.name`; the
[supported resource attributes](https://docs.cloud.google.com/iam/docs/conditions-resource-attributes)
also do not list these IAM resources or Cloud Run jobs. A guessed name condition
would fail closed rather than provision usable authority.

After merge, verify the bootstrap and retirement's signed terminal deployment
records, the resulting live six-resource absence and preserved monthly job, and
automatic reconciliation of incident `SD-43DB05AF66FD` on its original Slack
parent. Validation success and merge completion are separate outcomes. Do not
blanket-rerun the original main graph: its other IAM jobs already succeeded.

## Follow-ups

Remove the expired custom role and binding through a separate reviewed protected
plan. If recovery misses the fixed expiry, review a new bounded window; never
replace it with an unbounded grant or bypass the permission gate.
