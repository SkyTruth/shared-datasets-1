# November WDPA activation attempt

The protected deployment installed the automatic monthly worker but its first
November canary failed. The schedule remains paused. This is failure evidence,
not November publication acceptance or permission to clear a publication claim.

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
  remains open. Its [original Slack parent](https://skytruth.slack.com/archives/C0B12LDMB1A/p1793593147771669)
  confirms actual failure delivery.

## Failed boundary and local correction

The source URL returned HTTP 404 for
`WDPA_WDOECM_Nov2026_Public_all_shp.zip`. That should produce a skipped
check-in while the schedule waits for upstream availability. The new producer
instead supplied `run_date`, while the release-index contract requires
`release_date`. Derivation raised `ReleaseIndexError: run record is missing
release_date` after the publisher had prepared a receipt and claimed the marine
asset.

The local fix writes the scheduler attempt as `release_date`, retaining the
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
at generation `1793309285992493`. That generation is still current: the skip
never wrote it. The terrestrial release index remains at
`1793309291212844`, and terrestrial state at `1793309293057326` has no active
claim and reserved next feature ID `497919`.

Both committed releases remain `2026-10-01`. No new release, latest dataset,
run-record or release-index artifacts were published by this attempt.
Generation-pinned state, receipt and index snapshots are retained in the task's
local `incident/` evidence directory.

## Remaining recovery boundary

This chat owns recovery. PR #361 merged the readiness, observer retirement and
future skip fixes. The existing claim still needs its separate reviewed recovery.

The protected recovery code must reach reviewed `main` before a separate PR adds
its first immutable recovery document. The planned-alert gate intentionally uses
trusted base code, so a code-and-first-plan PR cannot introduce its own accepted
mutation schema. That later plan must pin the original state/receipt above and
the exact corrected index bytes. Verify its terminal control-installer execution
and live receipt/state before deploying the fixed worker with a canary.

The malformed prepared intent belongs to the original execution and executor.
A new image, date or execution cannot take over its claim. Do not delete the
receipt, clear the state, rewind counters, rewrite captured intent or rerun the
failed deployment blindly. The existing generic object publisher cannot mutate
publication control objects.

Live completion requires a reviewed, constrained recovery for this exact
uncompleted check-in, preserving the original receipt and committed identity
history. That recovery must be followed by a CI-tested fixed worker, a verified
canary, both release-index checks and protected schedule resume. Observer
deletion separately needs the reviewed protected ten-resource retirement.
The existing retirement authority expires at `2026-11-03T00:00:00Z`; this work
does not extend it. Do not resolve the original alert until recovery is verified.
