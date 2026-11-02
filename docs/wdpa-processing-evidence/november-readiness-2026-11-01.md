# November WDPA activation attempt

The first protected November canary failed while recording a source-unavailable
skip. Its captured marine transaction has since completed through the reviewed
recovery below. This is failure and recovery evidence, not acceptance of a new
November dataset release or permission to clear publication claims.

## Exact attempt

- [Deployment run 41386681213, attempt 1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41386681213/attempts/1).
- Tested executor: `c43315539a496eebc84c8d888a26f8d61130ccde`.
- Main CI source: `41374742252/1`; `ci-ready` and the retained WDPA image passed.
- Deployment record: `7484046062`; terminal phase `failed`.
- Cloud Run execution: `wdpa-monthly-vfcj6`, failed with exit 1 at
  `2026-11-02T04:17:35.574263Z`.
- Installed worker and dormant observer image:
  `us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/wdpa-monthly@sha256:57d5adf51791bc669bece050476b3647cdcc9779d7cdb4bacfd99d89efe74ca0`.
- The worker no longer pins `WDPA_PROMOTION_BUNDLE` or a template `RUN_DATE`.
  The canary alone used `RUN_DATE=2026-11-01`.
- [Monitoring incident 0.oejb7pfczv4r](https://console.cloud.google.com/monitoring/alerting/alerts/0.oejb7pfczv4r?project=shared-datasets-1)
  was authoritatively `CLOSED` at `2026-11-02T05:24:11Z`. Closure alone did
  not establish dataset recovery. Its [original Slack parent](https://skytruth.slack.com/archives/C0B12LDMB1A/p1793593147771669)
  confirms actual failure delivery. [Protected reconciliation run 41496679143](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41496679143/attempts/1)
  succeeded after transaction recovery; a subsequent read verified the same
  parent changed to “Closed by Monitoring” with no new thread message.

## Failed boundary and producer correction

The source URL returned HTTP 404 for
`WDPA_WDOECM_Nov2026_Public_all_shp.zip`. That should produce a skipped
check-in while the schedule waits for upstream availability. The new producer
instead supplied `run_date`, while the release-index contract requires
`release_date`. Derivation raised `ReleaseIndexError: run record is missing
release_date` after the publisher had prepared a receipt and claimed the marine
asset.

PR #361 writes the scheduler attempt as `release_date`, retaining the
month-start `target_release_date`. Generated skip intents also validate their
complete derived index before persisting a receipt or acquiring ownership.
Regression tests first reproduced the missing field and premature writes, then
passed through the actual publisher and persisted index for both WDPA realms.
They preserve the existing release, manifests and feature-ID counters.

## Remote mutation frontier

The protected Terraform apply updated only the monthly worker and dormant
observer jobs. It did not resume either schedule. The runtime changed these two
objects under
`gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/wdpa-marine/publications/`:

| Object | Observed generation | State |
| --- | --- | --- |
| `state.json` | `1793593048719262` | Active check-in claim; reserved next feature ID remains `17943`. |
| `receipts/348383de9527e070739cfaa18074b789de67703fcf88421ba7f97ebe6de68da5.json` | `1793593048075111` | `prepared`, empty `results`, no reservation. |

Transaction:
`c1a5470ad44969e919fae04946185f1572585a830e70068236909d285bb3c02f`.
Its only proposed effect is
`gs://skytruth-shared-datasets-1/_catalog/releases/wdpa-marine.json`, captured
at generation `1793309285992493`. That generation was still current before
reviewed recovery: the failed skip never wrote it. At that boundary the
terrestrial index remained at `1793309291212844`, and state generation
`1793309293057326` had no active claim and reserved next feature ID `497919`.

Both committed releases remain `2026-10-01`. No new release, latest dataset,
run-record or release-index artifacts were published by this attempt.
Generation-pinned state, receipt and index snapshots are retained in the task's
local incident evidence directory.

## Reviewed check-in recovery

This WDPA readiness chat owns recovery. PR #361 merged the readiness, observer
retirement and future skip fixes. PR #362 adds the constrained recovery executor
and trusted-base plan schema. The immutable proposal below completed that original claim through the
protected installer.

The [immutable recovery proposal](../../.github/dataset-plans/wdpa-marine/wdpa-skip-recovery-20261102/255f7368e59828be1b71a11768bdd4f0baa5f8224730b92146d94bf4b5ad4006.json)
pins the state and receipt above and stages the exact corrected check-in at
`gs://skytruth-shared-datasets-1/_scratch/pending-publishes/wdpa-marine/wdpa-skip-recovery-20261102/recovered-index.json`,
generation `1793637440852623`, SHA-256
`844a118ad673db261c5ffd8e76c06e3833d79fbf5addd2639134a5617453f721`.
The protected control installer resumes the captured transaction: index write,
receipt result, then normal claim release. It preserves the original intent,
October current release, manifest snapshots and feature-ID high-water `17943`.
The protected installer succeeded in [main run 41492611273, attempt 1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41492611273/attempts/1),
job `125443442955` (`Publish PR #364 / Install reviewed feature-ID reset`).
Independent generation-pinned GCS reads verified all postconditions:

| Object | Recovered generation | Verified result |
| --- | --- | --- |
| Original receipt above | `1793641445980498` | `derived_complete`; original intent and transaction unchanged. |
| Marine `publications/state.json` | `1793641452518588` | `active: null`; all other fields unchanged, including October manifests and counter `17943`. |
| Marine release index | `1793641441933312` | SHA-256 `844a118ad673db261c5ffd8e76c06e3833d79fbf5addd2639134a5617453f721`; November 1 source-unavailable skip; October release history unchanged. |

Terrestrial state/index generations remained `1793309293057326` and
`1793309291212844`, with no active claim and counter `497919`. Both assets
passed the publication rollout gate with `--require-published`; both indexes
passed their release-index contract. The later canary may add fresh skipped
check-ins; it does not undo the original receipt history.

The malformed prepared intent belongs to the original execution and executor.
A new image, date or execution cannot take over its claim. Do not delete the
receipt, clear the state, rewind counters, rewrite captured intent or rerun the
failed deployment blindly. The existing generic object publisher cannot mutate
publication control objects.

Activation follows the recovered transaction with a CI-tested fixed worker, a
verified canary, both release-index checks and protected schedule resume. The protected
ten-resource retirement has removed the observer job, scheduler and service
account, and revoked its temporary grant. Live inventories verified their
absence; do not recreate the observer or renew its retirement grant. Do not
resolve the original alert until recovery is verified.

## Fixed-worker activation

The fixed worker uses reviewed main executor
`2970e28547e45cb1cb8a5f93384ee25642c2542d` and successful main CI
`41492611273/1`. [Protected deployment 41494126901, attempt 1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41494126901/attempts/1)
loaded that CI attempt's retained image without rebuilding. Deployment record
`7499302344` pins
`us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/wdpa-monthly@sha256:1b10e13894c14bad21abd387fdd4599562f23939e267e7e22e1ea929cd5e105c`.
Its allowlisted saved plan updated only
`module.wdpa_monthly_job.google_cloud_run_v2_job.this`.

The single rollout canary is `wdpa-monthly-5s4cw`, with execution-only
`RUN_DATE=2026-11-01`. Live worker-template inspection verifies the immutable
image/config pair and the absence of persistent `RUN_DATE` or
`WDPA_PROMOTION_BUNDLE`. The observer remains retired.

The canary exited zero at `2026-11-02T18:34:12Z` with a designed
`identity_decision_required` block for one marine record. No November release or
canary publication receipt was created. Source key `555729583`, already owning
feature ID `15378`, now matches the geometry of existing feature IDs `11347` and
`15093`. Their published names are Bonden, Sweden, under separate designations.
The maintainer explicitly approved `keep_previous_key_mapping` for source key
`555729583`, preserving its own feature ID `15378`. Reusing `11347` or `15093`
would merge distinct existing designations and is not legal for this key. The
release-scoped resolution must reach reviewed main before another canary; a
zero exit or signed terminal-execution outcome alone does not authorize schedule
activation. The schedule remains paused pending that reviewed resolution, a newly tested
worker and a successful publication canary. The expected decision alert was
observed in Slack; Monitoring incident `0.oejurkps0izq` tracks this review gate.
