# Small Python image cache investigation and benchmark

Catalog-viewer and monitoring-slack now build without GitHub Actions remote
cache import or export. They retain the same production recipes, Buildx
`--load`, immutable-image checks, installed-code probes, TAR/config/rootfs proof,
loader roundtrip, six required retained images and existing deployment gates.
The other five image cache scopes and local preflight behavior are unchanged.

## Demonstrated problem

Two real PR CI attempts completed successfully after unusually slow cache reads:

| Run/attempt | Image and operation | Observed wait |
| --- | --- | ---: |
| [40523666230/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/40523666230/job/122257442434) | Catalog-viewer cached COPY, fetching a 16.48 kB blob | 1,038.9s |
| [40534428046/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/40534428046/job/122295868571) | Monitoring-slack cache-manifest import | 1,048.8s |

All 18 dependency RUN commands were cached in both attempts. The underlying HTTP
or service fault was not exposed by the logs. Subsequent main run
[40538485481/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/40538485481)
finished the complete recipe command in 177s with unchanged cache settings. That
healthy execution demonstrates variability, not a repair.

Earlier hosted uncached dependency commands took 6.6s for viewer's pip install
and 1.5s for monitoring's uv sync, using the same resolved Python base. These
figures exclude complete target build/check/archive time.

A shorter cache timeout does not bound every read in the observed BuildKit
v0.33.1: its [lazy reader uses `context.TODO()`](https://github.com/moby/buildkit/blob/v0.33.1/cache/remotecache/gha/gha.go#L627-L633),
and its [Azure downloader](https://github.com/moby/buildkit/blob/v0.33.1/vendor/github.com/tonistiigi/go-actions-cache/cache_v2.go#L53-L106)
has a separate retry policy. The small repair removes those two remote-cache
paths, without adding build retries or fallback handling.

## Hosted comparison

The PR's temporary read-only diagnostic seeds isolated run/attempt/target cache
scopes outside the timed samples. It alternates current/proposed policy order,
using a fresh identically pinned builder for each sample on one Ubuntu runner.
This is a fresh-builder, warm-host comparison, not fresh virtual machines. It
records build, installed checks, archive proof and load timings separately.

Each sample builds the actual recipe and verifies its own immutable image and
archive. Installed package inventories are recorded because ranged dependencies
and build timestamps can make a rebuilt image differ from a cached image.
Current-policy samples must demonstrate cached dependency RUNs. Seed failures,
build errors, failed probes and failed archive/load proofs remain failures.

Hosted results are pending. The diagnostic will be removed from the final PR
diff after evidence is captured; final preflight and normal CI will then run on
the exact final head. No production environment, GCP authentication, registry
push, deployment or live Slack test is part of the comparison.

## Limits and follow-through

This removes both demonstrated cache-read paths for the two affected targets.
The remaining five images still use the same upstream backend, and registry or
package downloads can also be slow. Subsequent real executions must be checked
before claiming practical recurrence has stopped. No percentage improvement or
latency guarantee is inferred from the historical outages.
