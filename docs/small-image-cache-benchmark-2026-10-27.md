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

The [hosted diagnostic](https://github.com/SkyTruth/shared-datasets-1/actions/runs/40578415216/job/122451142574)
passed on October 27, 2026. It seeded isolated run/attempt/target cache scopes
outside the timed samples, then ran current/proposed/proposed/current for each
target. All ten samples, including the two excluded seeds, used distinct fresh
builders with the same BuildKit image identity. This is a fresh-builder,
warm-host comparison on one Ubuntu runner, not fresh virtual machines.

Each sample built the actual recipe, checked its immutable image, ran installed
code probes, inventoried packages, packed and verified the TAR/config/rootfs,
then loaded and verified the archive again. All four current-policy samples
proved their dependency RUN was cached. Proposed samples had no GHA import or
export; their viewer pip RUN took 8.0s in each trial and monitoring uv sync took
1.5s and 1.6s. Package inventories matched across all timed samples per target.
Image timestamps and therefore image identities can still differ; every image
was independently verified rather than requiring cross-policy byte equality.

| Target | Policy | Median build | Median checked path | Both checked-path trials |
| --- | --- | ---: | ---: | --- |
| Catalog-viewer | Current GHA cache | 5.05s | 9.94s | 10.26s, 9.63s |
| Catalog-viewer | Proposed bypass | 17.54s | 21.61s | 22.39s, 20.84s |
| Monitoring-slack | Current GHA cache | 2.98s | 11.84s | 11.56s, 12.12s |
| Monitoring-slack | Proposed bypass | 11.83s | 20.65s | 21.27s, 20.03s |

The checked path includes build/import/export/load, immutable-ID resolution,
installed checks and package inventory, canonical retag, archive packing/proof
and loader roundtrip. Builder setup, strict seed, cleanup and job setup are
recorded separately and excluded. Seeds took 30.13s for viewer and 24.81s for
monitoring on that same checked path; they are not part of the medians.

Healthy cache hits were faster. The bypass added about 11.67s for viewer and
8.81s for monitoring in this experiment. This is a reliability tradeoff to remove
the demonstrated 17-minute read paths, not a measured median speedup. The two
historical outages were not reproduced during the benchmark. Their frequency
and the change's long-run expected time saving remain unknown.

### Provenance and evidence

- Source head: `ad270924d38ee79f28daf73c32c64e25be6e6ce8`.
- Base: `81cba33e10fdb5c7ef12291b3425c137feaca96b`.
- Tested PR merge: `f3d6a4a4a139c3f51fcfd7c13fb634f2cf0559bc`.
- Tested tree: `f22d9ac83173cc658e6e53f6d1b3b0ff0da6e45e`, identical to the
  current-base full preflight tree.
- Runtime: Linux AMD64, Docker 28.0.4, Buildx 0.38.1, BuildKit 0.33.1.
- Builder image: `sha256:27933730df224df80c41f4e5a9b33fa78831a79fd31903df3bb7deb49363422f`.
- Python base: `python:3.12-slim@sha256:e70237b264442328526ff29d6b3c43bd8abcf6cbab78a949322fb69b7d91d6b6`.
  Both Dockerfiles, pyproject.toml and uv.lock input hashes are retained in the
  raw evidence. The baseline and proposed modules were AST-identical outside
  the cache-policy function.
- [Raw benchmark evidence](https://github.com/SkyTruth/shared-datasets-1/actions/runs/40578415216/artifacts/12769071577):
  55,124-byte ZIP, API SHA-256
  `c0ca1809246fc48246e8cd36af9c0a95396225a0a1f252f88b6b4cc9a959475f`.
  Digest and ZIP CRC were independently checked. It contains all sample phase
  times, logs, installed packages and verified image identities. GitHub retains
  it for 14 days; a local verified copy is retained in the task workspace.
- The [measurement harness](https://github.com/SkyTruth/shared-datasets-1/blob/ad270924d38ee79f28daf73c32c64e25be6e6ce8/scripts/benchmark_small_image_cache.py)
  and its workflow/tests are available at the measured commit and removed from
  the final PR diff. They add no permanent job, configuration or benchmark API.

The measurement head's complete normal CI also passed all seven suites and
`ci-ready`. Its actual seven-recipe command took 151s and the producer job 230s;
all six required images were retained. That is a separate observation, not a
controlled whole-suite speedup. No production environment, GCP authentication,
registry push, deployment or live Slack test was part of the comparison.

## Limits and follow-through

Only two timed samples per target/policy were collected on one shared host.
Registry, package-service and host-cache variability remain. This compares the
two isolated targets, not complete seven-recipe execution or deployment latency.
No build/check/load failure was suppressed, and no automatic retry was added.

This removes both demonstrated cache-read paths for the two affected targets.
The remaining five images still use the same upstream backend, and registry or
package downloads can also be slow. Subsequent real executions must be checked
before claiming practical recurrence has stopped. No percentage improvement or
latency guarantee is inferred from the historical outages.
