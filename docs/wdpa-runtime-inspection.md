# Inspect the isolated WDPA runtime

The one-time runtime inspection is retired. The workflow and probe recipe have
been removed; the observations below are historical evidence, not instructions
for monthly deployment.

The first complete cloud validation, `wdpa-processing-validation-psg4m`, exited
before downloading inputs on October 2, 2026. Its CPU/memory preflight could not
read the expected Docker cgroup limits. The Cloud Run API independently verifies
4 CPU, 8 GiB RAM, a 100 GiB DISK at `/work`, zero retries and a 24-hour timeout.
No complete-build or resource acceptance is claimed from that failed startup.

The protected inspection run
[`37044294978`](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37044294978)
completed successfully as `wdpa-processing-validation-lb5ln` at
2026-10-02T18:07:28Z. Its immutable image was
`wdpa-validation@sha256:325aa32eb8b4ab968b6b03e3c162ff7d21ca207b1c130f8074e18e2ba287285b`.
Cloud Run exposed namespaced **cgroup v1** controllers: CPU quota `372000`
microseconds per `100000` microseconds (3.72 CPUs), memory limit `8589934592`
bytes and `memory.max_usage_in_bytes` kernel peak. Affinity included five cores;
affinity does not replace the quota. No v2 `cpu.max`, `memory.max` or
`memory.peak` file existed at the root.

The corrected reader supports both this observed v1 interface and Docker's v2
interface. Reports preserve the actual CPU quota. The protected Terraform
allowlist continues to require exactly 4 configured CPU, 8 GiB RAM and a 100 GiB
disk. Resource checks require a positive measured CPU quota no greater than 4,
exactly 8 GiB memory and a real kernel high-water mark. Missing telemetry blocks
validation; sampled usage and process RSS cannot replace the kernel peak.
The enforced 8 GiB memory limit, <80 GiB scratch and 24-hour completion checks
remain intact. The preferred 6.4 GiB peak is advisory: a successful complete build
at 7.2 GiB can be accepted with a warning when all artifact and ownership checks
pass. Keep the real kernel lifetime peak; do not reset or subtract file cache.

The refreshed alert apply
[`37044038452`](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37044038452)
succeeded. The live Monitoring API confirmed `protoPayload.status.code>0`,
with the project, region, resource, severity, service and method restrictions
retained. Applying the live filter to historical logs matched a WDPA code 8
failure at 2026-10-01T17:34:36Z and a validation code 10 failure at
2026-10-02T17:15:16Z, and matched no successful code 0 executions.
The controlled pre-write failure `wdpa-processing-validation-tlwbd`
actually reached `#shared-datasets-alerts`; a green workflow alone is not proof
of live filter or notification delivery.

Version 3 acceptance evidence separates the deterministic October old/new
sample (`fraction=0.001`, `seed=7919`) from one complete retained artifact build.
The sample compares both realms' IDs, hashes, properties, geometry, field types,
canonical metadata, every locale and translation CSV. Its processing digest,
frozen source/baseline/translation snapshots, native versions and image config
digest must match the complete build. Full reports retain
`compatibility_verified=false`; their contract checks do not imply a legacy
comparison. Keep the raw sample and full reports unchanged.

Cloud deployment now loads and verifies the reviewed staged image artifact,
then tags and pushes those same bytes. It does not rebuild after the staged
tests. `image_digest` in reviewed benchmark evidence identifies the verified
image **configuration** digest from `image.json`; complete cloud evidence also
records the execution's immutable registry URI as `cloud_image`. The accepted
build also records its retained bundle. Capture these deployment facts alongside
raw reports rather than rewriting a raw compatibility or measurement result.
The image artifact lasts seven days; expired bytes require rerunning stages.

The protected deployer's existing scheduled-ingestion role also needs
`run.jobs.list` for the pre-deploy existence check. This is a read permission;
the role still grants no job IAM editing, deletion or extra runtime dataset
access. The narrowly allowlisted scheduled-ingestion IAM sync applies the role
update. Observer IAM creation remains a separate rollout prerequisite; do not
assume the deployer has `run.jobs.setIamPolicy` or Scheduler creation permission.

Protected target applies refresh live infrastructure before creating their saved
plans. A failed provider update can leave cached Terraform values ahead of the
live resource. Disabling refresh made the alert-filter retry report no changes
while the API still showed `status.code=10`; the workflow now explicitly uses
`-refresh=true`. The same queued job still validates its resource allowlist and
applies only that saved plan. Verify the live filter and notification delivery
after deployment.

Use the manual protected-main `WDPA isolated runtime inspection` workflow to
inspect the runtime before changing telemetry. It reuses the already deployed
immutable validation image and refuses to deploy while a validation execution
is pending or running. Its saved Terraform plan may update only the existing
isolated validation job; the plan checker preserves the empty runtime identity,
resource limits, disk and timeout, and accepts only the checked-in inspection
command in `catalog/wdpa-runtime-inspection.json`.

The inspection prints selected cgroup v1/v2 files, cgroup mounts, CPU affinity,
kernel version and the image's processing source digest to Cloud Logging with
event `wdpa_runtime_inspection`. It reads no dataset, downloads no source,
publishes no objects and makes no allocation or publication decisions. Its
scope is explicitly `runtime-inspection-not-acceptance`; it cannot substitute
for a successful sea-ice, marine, or complete October benchmark.

This diagnostic workflow changes the validation job's command. The normal
`wdpa-processing-validation-deploy.yml` workflow restores the processing
command when `wdpa_validation_runtime_inspection` uses its default `false`.
Do not manually start a complete replay until that command is restored,
the staged gate matches the current processing source, and the alert probe
has actually reached Slack. Run one complete retaining build and preserve its
raw report, terminal execution status and immutable bundle references. Promotion
uses those same files without a second replay.

A changed dependency lock changes the processing fingerprint. Existing reports
remain evidence for their captured image and source digest; they must not be
relabeled to match newer code. New processing code uses small/sample checks on
the final image before its one complete retaining build. Preferred headroom
warnings do not require rebuilding a valid retained bundle. No resource increase
or publication-state reset is part of recovery.
