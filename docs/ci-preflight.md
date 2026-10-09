# Complete validation before pushing

From a clean checkout with full Git history, run:

```bash
uv run python scripts/ci_preflight.py --base <current-main-sha> --head <branch-head-sha>
```

Preflight creates a disposable clone under the standard shared-datasets temp
root, constructs the prospective merge, and runs the same suite commands as CI.
It does not change the source checkout or index. A merge conflict, shallow
history, missing comparison, missing required tool, failed test, empty test
collection, or unexpected skip fails validation. Resolve a missing base before
retrying; selecting all suites does not make history-dependent checks optional.

Docker must be running. Preflight copies its clean checkout into disposable
containers and copies suite evidence back; host file sharing, credentials and
the Docker socket are never mounted. All six container suites, including the full
geospatial fixture corpus, run on the Docker server's native Linux architecture
(AMD64 or ARM64), using the same pinned tool versions and commands as CI.
Preflight rejects a validation image whose actual platform differs from that
native runtime. It installs no emulator or system handler and records each
suite's actual architecture, Docker version and immutable image ID in
`runtime.json`. The required hosted geospatial suite runs the full corpus on
AMD64. A local ARM fixture pass does not cover every AMD64-specific code path;
the actual production-image checks and the hosted suite retain that coverage.
Preflight installs its pinned Python, uv, Node 22/24,
Terraform, gitleaks and actionlint binaries inside a Linux container. Native
fixtures use the repository's GDAL/Tippecanoe/PMTiles image. Production credentials
are not forwarded. Downloads and Terraform provider initialization require
network access. Tool versions are owned by the dependency-free
`scripts/ci_toolchain.py`; suite selection is owned by `scripts/ci_contract.py`.
CI setup and image pins are checked against that contract.
The production-image suite uses the host Docker client and an isolated host
environment with the same pinned Python and locked dependencies. Its process-owned
HOME, Docker, XDG and gcloud configuration directories contain no caller ADC or
registry authentication. Only explicit tool paths, locale/temp settings, proxy/CA
settings and the resolved Docker connection are retained; GitHub/cloud credentials,
event identity and SSH agents are excluded. Unix and TCP connections are supported;
required daemon TLS files are copied separately and removed after the suite.
Unsupported authentication transport fails before validation. The host process
retains normal filesystem and Docker API capabilities; environment isolation is
an operational credential boundary, not a sandbox for malicious code. It builds and
tests all five actual Linux AMD64 deployment recipes on every host without mounting the Docker
socket into a validation container. Its evidence retains the four deployable
images with their config and rootfs hashes; missing image evidence fails
`ci-ready`. The suite also exercises the deployment loader on the actual CI
Docker daemon before admitting each retained image. Deployment consumes these
tested bytes without rebuilding.
The general Linux image includes pinned GDAL headers so the locked Rasterio
dependency can build on ARM. Browser fixture dependencies have an explicit
locked `browser` group. Tool setup retains `GODEBUG=asyncpreemptoff=1`; this runtime
setting does not suppress validation errors.
Native ARM containers set `OPENSSL_armcap=0`, selecting OpenSSL's portable CPU
implementation because older Apple Linux VMs advertise unsupported extensions
([OpenSSL CPU documentation](https://docs.openssl.org/master/man3/OPENSSL_armcap/),
[upstream cryptography report](https://github.com/pyca/cryptography/issues/14764)).
The locked wheel, algorithms and test corpus remain unchanged; this setting is
included in the recorded runtime evidence.
Every local suite explicitly runs with `CI=true`. uv selects the pinned Python
version, and each locked sync is followed by a check of the executing interpreter;
its actual version is retained in the command log and a mismatch fails validation.
Browser validation also uses a
process-owned synthetic GitHub PR event so CI-only reporter behavior is exercised
without forwarding tokens, real run IDs or caller event data. Playwright Git
commit/diff capture is disabled: its CI diff collector otherwise fetches the base
with `--depth=1` and makes a full checkout shallow. The plan owns revision evidence,
and the full-history invariant is still checked after browser execution.
Native lint and Node execution also avoid observed UV and subprocess faults under
AMD64 emulation. Both Node versions execute the same SDK tests and package-byte checks
as hosted CI; a failed suite remains a failure and is never automatically retried.
Terraform retains read-only initialization. Linux ARM and AMD64 provider package
hashes cover the same pinned version and are verified against the reviewed archive
checksums before inclusion in the locks.
The lint suite also runs `scripts/terraform_target_contracts.py` with pinned
Terraform against a disposable source copy and an empty local backend. It reuses
only lock-verified provider binaries, excludes caller credentials and does not
read production state. The actual dependency graph must keep every narrow IAM
target within its exact workflow mutation scope and the prerequisite identities
in `terraform/iam-plan-prerequisites.json`. Existing ordered bootstrap jobs remain
valid prerequisites; that policy does not add any resource to an apply allowlist.
New managed ancestors fail before pushing, including references inside disabled
dynamic blocks. Retained evidence includes the graph, each target closure and
negative controls from failed main revision `ffe2dd53`: the telemetry bucket
dependency must be rejected with collection both disabled and enabled. The usage
owner provisions its private raw bucket with collection disabled before a later
reviewed activation may enable shared-bucket logging.
Browser validation removes its process-owned uv download cache after installing
the locked fixture environment, reducing Chromium's peak disk use without
removing installed dependencies or changing test coverage.

The shared classifier always selects lint, workflow syntax, full-history secret
scanning, admission and diff/static guardrails, local catalog compliance,
offline feature-identity decision checks, and Python tests. The two local hygiene
checks run in the shared `tests` suite before pytest, replacing the separate
bucket-hygiene PR workflow. A finding or invalid decision fails that suite.
It adds native, SDK, image and browser suites according to component dependencies.
Documentation-only component paths and proven script exceptions use the single
selection contract in `scripts/ci_contract.py`; each `NARROW_SCRIPTS` entry records
its consumer proof. Unclassified scripts, workflow changes, lockfiles, unknown
paths and unavailable comparisons select all suites. Test selection does not
change deployment selection.
The standard Python run permits only the five named native fixtures to skip;
native validation requires every selected test and every required fixture to pass.
Browser validation preserves its existing no-skips/no-retries report check.

Each run prints its retained evidence directory. `plan.json` identifies base,
original head, tested merge revision/tree and validation-contract digest. Each
suite records actual tool versions, commands, exits, logs and results;
`preflight.json` exists only after all selected suites succeed. SDK evidence
includes the exact tested tarball and its hash. Evidence cannot be reused after
code, base, toolchain or validation-contract changes: rerun against the current
base and head before every subsequent push.

GitHub's always-evaluated `ci-ready` job independently validates every selected
suite and both SDK matrix entries for its tested revision. Cancelled, missing,
failed and unexpectedly skipped jobs cannot satisfy it. Local evidence is not
an approval artifact. Reviews, protected environments and trusted event
provenance remain GitHub-only authorization boundaries.

Existing `lint`, `tests` and `geospatial-changes` requirements remain available
during migration. Add `ci-ready` while retaining them, verify positive and
negative behavior, then remove redundant requirements. The complete CI workflow
remains active on every PR; it has no workflow-level path filters. SDK and browser
validation are consolidated into this workflow, preserving Node 22/24 coverage.

GitHub artifacts include their producing run attempt. Selective validation
reruns may reuse a prior passing suite only when the Actions API proves its
exact CI workflow, source revision and successful producing job, and its
base/tree/toolchain contract matches. The latest result for each suite wins;
a newer failed result cannot reuse an older success. `ci-ready-evidence` names
the exact selected plan and suite artifacts for consumers of tested bytes.

Each suite first uploads its complete `ci-result-<suite>-attempt<N>` artifact,
then uploads `ci-gate-result-<suite>-attempt<N>` containing only `result.json`.
The compact upload requires successful retention of the complete artifact and
fails if its result file is missing. `ci-ready` downloads only compact results,
preserving its revision, tree, contract, toolchain and producing-job checks.
Its `suite_artifacts` evidence still names the complete artifacts. Deployment
consumers continue verifying and loading the retained image or SDK bytes;
diagnostic logs and deployment bundles remain available for 14 days.

The pinned actionlint version predates GitHub's `concurrency.queue` field.
`scripts/check_workflow_syntax.py` independently requires `queue: max` together
with explicit `cancel-in-progress: false`, then omits only that validated scalar
line in disposable parser input. All remaining syntax errors fail normally;
production workflow files and concurrency behavior are unchanged.

The receipt bootstrap lands before dependent deployments adopt signed deployment
records. After this workflow is on main, `jonaraphael` dispatches
`Deployment receipt rehearsal` through the existing protected production
environment, then dispatches `Reusable deployment receipt rehearsal`. The same
protected leaf signs a synthetic receipt and verifies its exact bytes, main
revision, distinct caller and signer identities and producing run with the pinned
official verifier. Both direct and reusable invocation must pass.
It has no GCP authentication or deployment-write permission. A successful
protected rehearsal is required before enabling dependent production mutations;
an unsigned fixture or a locally passing test cannot replace this live proof.

## Focused local checks

For quick feedback during editing, the default Python suite uses local fixtures
and mocked GCS, Slack, and source downloads:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest
UV_CACHE_DIR=.uv-cache uv run ruff check .
```

The locked dev group includes GeoPandas and Rasterio for tiny generated FGB/COG
consumer fixtures; exact-byte Python SDK fetching does not require them.
Native tests need GDAL CLIs, PMTiles, and the Tippecanoe decoder. Enable the GDAL
fixtures explicitly for a focused local run:

```bash
RUN_GDAL_INTEGRATION_TESTS=1 UV_CACHE_DIR=.uv-cache uv run pytest \
  tests/test_raster_standards.py tests/test_wdpa_monthly.py \
  tests/test_sea_ice_daily.py tests/test_eamlis_monthly.py
```

For standalone Terraform formatting/validation, use the pinned version in
`scripts/ci_toolchain.py`, run `terraform fmt -check -recursive terraform/`, and
initialize prod and preview with `init -backend=false -input=false` before
`validate`. Older Terraform versions reject the optional-variable syntax.
Focused checks supplement the required complete preflight above.
