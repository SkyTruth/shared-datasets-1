# Per-image artifact handoff measurements

## Problem and invariant

Main CI run [40456414884/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/40456414884)
retained six images in one 2,667,481,474-byte ZIP. MPAtlas downloaded that entire
artifact to load its 648,775,680-byte TAR. Its handoff step took 378 seconds,
but the existing log did not separate network, hashing, extraction and Docker
work. Monitoring consumed the same bundle in 109 seconds on a different runner;
those executions are observations, not a controlled timing comparison.

The invariant is unchanged: deployment consumes exactly the image tested by the
successful CI producer at the authorized revision and producing attempt.
The change separates storage by image, while retaining complete validation
metadata and every archive, config, rootfs, loaded-image and registry check.
All six uploads must succeed before compact gate evidence is uploaded.

## Exact input

- Source revision: `7bcded0ac55cebc67889321f11782895ffd729f0`.
- GitHub artifact: `12713525214`.
- ZIP SHA-256, verified against authenticated GitHub metadata:
  `febc02f5e882fac2956acc2ebb3f62e1387e9d12b0f5fc84c850906c8f4a043b`.
- MPAtlas TAR SHA-256:
  `6c2e88e6856e598cebac47230bc3d64882a71d2878a3da6460ad9c3aa66a6dd5`.
- Tested image config:
  `sha256:54afa859a28a87b56b4dbb4e9796e7e02a699902ebd5416658b439d55b84f1bc`.

The original TAR bytes were never rewritten. All variants passed real TAR,
config and rootfs verification and actual Docker loading/config verification.

## Local compression comparison

Three repetitions per level used the same TAR and the upload action's Archiver
7.0.1, zip-stream 6.0.1 and compress-commons 6.0.2 writer with its 8 MiB buffer.
Order was `0,1,1,0,0,1`. The local runtime was Node 20.20.0 with zlib 1.3.1;
hosted Actions uses a different Node/Linux runtime. Times are medians.

| Compression | ZIP bytes | Serialize seconds | Decode and CRC seconds |
| --- | ---: | ---: | ---: |
| 0 | 648,775,846 | 0.458 | 0.079 |
| 1 | 248,095,774 | 4.234 | 0.782 |

Level 1 removed 61.76% of the selected-image ZIP bytes at a local cost of about
3.78 seconds serializing and 0.70 seconds decoding. The separate small metadata
ZIP was 155,096 bytes. Together, the selected image and metadata were about
90.7% smaller than the original full bundle. This supports using level 1;
it does not by itself prove an end-to-end hosted speedup.

## Local handoff comparison

The actual old and new consumer functions ran in the balanced order
`full,0,1,1,0,full`, with a file-copy transport adapter and real verification and
Docker operations. Two repetitions per variant are descriptive samples.

| Variant | Total seconds, both trials | Median total seconds | Median selection seconds |
| --- | --- | ---: | ---: |
| Full six-image bundle, level 0 | 15.109, 15.083 | 15.096 | 7.066 |
| Per-image, level 0 | 12.101, 10.156 | 11.128 | 1.864 |
| Per-image, level 1 | 9.595, 10.114 | 9.855 | 1.457 |

These totals include file copying, ZIP hashing/extraction, TAR/rootfs
verification, Docker loading and loaded-config verification. They exclude real
GitHub transfer and unchanged outer production authorization. The source tag
and config were absent before every trial; only task-created tags were removed.
Shared layers and filesystem caches were not cold-controlled. Docker Desktop
4.16.2 / Engine 20.10.22 used a classic ARM64 store, unlike hosted containerd.
The local result is therefore not a prediction that production's 378-second
handoff becomes 9.855 seconds.

## Hosted comparison

The PR's `image-handoff-benchmark` job compares actual GitHub downloads from
the same successful production-image producer and the same MPAtlas bytes:

1. Retain the original full bundle at level 0 and the selected TAR at levels
   0 and 1 using the real upload action.
2. On a separate Ubuntu 24.04 runner, verify PR/run/attempt/producer identities,
   all artifact digests and complete result/manifest identity.
3. Perform one separately recorded warm-up. Run two timed repetitions of each
   variant in forward/reverse order with the exact warmed image present.
4. Include fresh metadata/plan reads, downloads, ZIP hashing/extraction,
   TAR/rootfs checks, Docker load and loaded-config proof in each total.
   Record actual Docker versions, artifact IDs, bytes and phase timings.

The original consumer is loaded from the pinned baseline revision only for this
comparison. A benchmark-only in-memory name alias selects each compression
artifact; the report records its actual GitHub ID, name and digest. Production
consumers have no alias or legacy fallback. Main-only deployment authorization
is excluded from the PR diagnostic and remains required in production.

The job has only `contents: read` and `actions: read`, with no production
environment, GCP authentication, image push, deployment or Slack delivery.
It runs only for same-repository PR changes to the image loader, bundle verifier
or benchmark script. A failure is a failed benchmark, never passing evidence.
Its `image-handoff-benchmark-attempt<N>` artifact contains `results.json`,
`results.md` and per-trial phase logs. Hosted numbers and the exact run link
belong in the PR after those measurements finish.

The complete clean-checkout preflight and required hosted CI remain separate
validation gates. No production speedup or recovery is claimed before the
corresponding real execution has been observed.
