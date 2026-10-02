# WDPA processing validation and rollout evidence

Readiness is **pending**. The implementation has compatibility and integration
evidence, but the two complete October runs at 4 CPU / 8 GiB have not been run.
`catalog/wdpa-processing-acceptance.json` intentionally blocks production deployment.

## Completed checks

- Full Python suite: 1,075 passed, 1,171 subtests passed; five native checks skipped
  on the host and exercised separately in the deployment toolchain.
- Native geospatial suite: 120 passed, 17 subtests passed, no skips. All five
  mandatory native fixtures passed, including old/new normalized metadata,
  field types and geometry, WDPA PMTiles, sea ice, EAMLIS and COG validation.
- Browser checks: three execution-status unit tests and five Chromium scenarios.
  The WDPA scenario preserves October marine and September terrestrial release
  dates alongside a failed overall execution, a newer running execution, later
  cancellation, minute refresh and stale observations.
- Identity tests cover identical/conflicting duplicates, changed source keys,
  reviewed reuse/new-ID decisions, the 64-digit ID boundary and exhaustion.
  Native pipeline tests cover producer failure, broken pipes, disk exhaustion,
  early consumer exit and cleanup. Existing publication tests retain generation,
  reservation, claim and interrupted-publication recovery checks.
- Terraform 1.8.5 formatting and validation passed. The saved production-state
  plan passed the deployment workflow's exact resource and processing-limit
  allowlists. No apply was run.
- Ruff and repository static guardrails passed.

## Deterministic October comparison

[Machine-readable sample evidence](wdpa-processing-sample-evidence.json) records
the frozen inputs and output digests. The selector used fraction `0.001`, seed
`7919`, and the existing `SITE_ID` expression. This is a deterministic subset,
not an assertion that each country is sampled proportionately.

The complete upstream ZIP was 4,786,380,946 bytes, SHA-256
`36181f18bfa2caaa3c62721f2438107c40411b1d995c60a3fc5835665a8d8fd9`.
Baselines were generation-pinned, hash/count-verified committed publications:

| Realm | Baseline release | Manifest generation | Baseline rows | Next ID before comparison |
| --- | --- | --- | --- | --- |
| Marine | 2026-10-01 | 1790866753009809 | 17,938 | 17,943 |
| Terrestrial | 2026-09-30 | 1790815952362262 | 304,817 | 304,818 |

Old and new processing agreed on all 20 marine and 499 terrestrial selected
records: IDs, hashes, source properties, geometry, field types, metadata schemas,
allocation evidence, all six localized sidecars and the canonical translation
CSV. The terrestrial subset contained 193 India records/sites and allocated 193
new IDs identically. These are **sample counts**, not whole-source India totals.
Both FGB and PMTiles passed native contracts, including archive verification and
representative tile decoding. No dataset bytes were published.

The development replay used the pinned native image with workspace code on an
emulated amd64 Docker VM limited to **2 CPU / 2 GiB**. It took 1,483.9 seconds,
including both old and new paths, input copies and baseline indexing. Scratch
peaked at 8.35 GiB; parent-process peak RSS was 457.2 MiB. Sampled cgroup usage
reached the 2 GiB limit, and this kernel did not expose `memory.peak`.
The replay did not freeze a processing digest at startup. It is compatibility
evidence only; **none of these measurements satisfies resource acceptance**.
It also reused a cached translation index. The current runner records and
verifies its processing digest across the run, requires complete replays to
rebuild the production translation index from `translation-sources.json`, and
the gate refuses cached-index runs.

## Infrastructure and failure visibility

The read-only worker/observer plan contains **eight creations, one update, zero
deletions**: the observer service account, custom execution-reader role, four
narrow IAM bindings, observer job and scheduler; the worker changes to 4 CPU /
8 GiB and a 100 GiB DISK at `/work` with Preview launch stage. Immutable production
image resolution, the production Terraform queue and publication rollout gate
remain in the protected deployment workflow.

The proposed failure filter was queried against real audit history: it matches
the WDPA OOM event with status **8** at `2026-10-01T17:34:36.255975Z` and status
**10** failures on September 30; adding status **0** yields no matches.
Identity pauses still exit successfully and retain their separate decision alert.
This query verifies matching, **not notification delivery**. Monitoring changes
must run through `cron-alert-policy-sync.yml` after review and merge.

The protected Terraform identity currently has job-update permissions but lacks
the observer's job/scheduler/custom-role creation permissions. A reviewed
protected bootstrap is a rollout prerequisite. No broader project permissions
have been granted by this PR.

## Remaining acceptance and rollout

1. Provide a Linux Docker runtime with 4 CPU, 8 GiB, reliable cgroup peak telemetry
   and enough disk for the 100 GiB scratch volume. The current VM has only two
   CPUs and about 2.9 GiB total memory; restarting it also affects a running
   database container.
2. Run the deployment image twice on the complete frozen October inputs and
   identical verified baseline/translation snapshots. Require peak memory
   ≤6.4 GiB, scratch <80 GiB, duration ≤24 hours, verified source-derived
   realm/India counts and valid FGB/metadata/PMTiles contracts. Any ambiguity
   requires reviewed decisions before outputs can be emitted.
3. Record complete reports, immutable image digest and processing digest in the
   reviewed acceptance document. Sample/genesis runs, missing peak telemetry,
   mismatched inputs and larger worker sizes cannot satisfy the gate.
4. Obtain the 100 GiB per-instance Preview disk quota and review/provision the
   observer bootstrap permissions through protected workflows.
5. After review and merge, apply monitoring and deploy through protected
   workflows. Verify actual controlled-failure alert delivery before permitting
   the normal dataset canary. Follow the canary to terminal status and inspect
   publication ownership, release indexes, generations and artifact metadata.

## Intended remote paths

The observer writes only
`gs://skytruth-shared-datasets-1/_catalog/wdpa-monthly-execution.json`.
The catalog deployment includes `_catalog/web/app.js`, `index.html` and the new
`execution-status.js`. Normal WDPA publication paths remain
`100-geographic-reference/130-protected-areas/{wdpa-marine,wdpa-terrestrial}/`
with the existing `releases/`, `latest/`, run records and release indexes.
Existing allocation, claim and receipt ownership is unchanged. **No remote
objects or production infrastructure were changed during implementation.**
