# Inspect the isolated WDPA runtime

The first complete cloud validation, `wdpa-processing-validation-psg4m`, exited
before downloading inputs on October 2, 2026. Its CPU/memory preflight could not
read the expected Docker cgroup limits. The Cloud Run API independently verifies
4 CPU, 8 GiB RAM, a 100 GiB DISK at `/work`, zero retries and a 24-hour timeout.
No complete-build or resource acceptance is claimed from that failed startup.

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
has actually reached Slack. Keep the same immutable image for the two complete
replays and retain their raw reports and terminal execution status.

A changed dependency lock changes the processing fingerprint. Existing reports
remain evidence for their captured image and source digest; they must not be
relabeled to match newer code. After fixing runtime telemetry, rerun the small
and marine stages on the final processing digest before repeating cloud
acceptance. No resource increase or publication-state reset is part of recovery.
