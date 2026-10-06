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
the Docker socket are never mounted. Lint, standard Python, Node 22/24 and Chromium
run on the Docker server's native Linux architecture. Geospatial release fixtures always
run on Linux AMD64, matching production and hosted CI; ARM Docker hosts use the
pinned, checksum-verified BuildKit direct-exec emulator for these fixtures.
Emulated child processes use that same emulator. No daemon settings
or system binfmt registration change. The per-suite architecture and image ID,
runtime release, version and hashes are recorded in `runtime.json`.
Preflight installs its pinned Python, uv, Node 22/24,
Terraform, gitleaks and actionlint binaries inside a Linux container. Native
fixtures use the repository's GDAL/Tippecanoe/PMTiles image. Production credentials
are not forwarded. Downloads and Terraform provider initialization require
network access. Tool versions and the classifier are owned by
`scripts/ci_contract.py`; CI setup and image pins are checked against that contract.
The general Linux image includes pinned GDAL headers so the locked Rasterio
dependency can build on ARM. Browser fixture dependencies have an explicit
locked `browser` group. Both boundaries disable Go asynchronous preemption for compatibility with local
amd64 emulation. This runtime setting does not suppress validation errors.
Native ARM containers set `OPENSSL_armcap=0`, selecting OpenSSL's portable CPU
implementation because older Apple Linux VMs advertise unsupported extensions
([OpenSSL CPU documentation](https://docs.openssl.org/master/man3/OPENSSL_armcap/),
[upstream cryptography report](https://github.com/pyca/cryptography/issues/14764)).
The locked wheel, algorithms and test corpus remain unchanged; this setting is
included in the recorded runtime evidence.
Native lint and Node execution also avoid observed UV and subprocess faults under
AMD64 emulation. Both Node versions execute the same SDK tests and package-byte checks
as hosted CI; a failed suite remains a failure and is never automatically retried.
Terraform retains read-only initialization. Linux ARM and AMD64 provider package
hashes cover the same pinned version and are verified against the reviewed archive
checksums before inclusion in the locks.
Browser validation removes its process-owned uv download cache after installing
the locked fixture environment, reducing Chromium's peak disk use without
removing installed dependencies or changing test coverage.

The shared classifier always selects lint, workflow syntax, full-history secret
scanning, admission and diff/static guardrails, and Python tests. It adds native,
SDK and browser suites according to component dependencies. Shared scripts,
workflow changes, unknown paths and unavailable comparisons select all suites.
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
