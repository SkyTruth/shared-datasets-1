# Complete validation before pushing

## Required boundary

Finish the [publication decision inventory](publication-readiness.md) before
expensive dataset validation. For code-only publication changes, use complete
synthetic batches. Both local preflight and hosted selection first run
`scripts/publication_runtime_check.py` without production credentials.

Follow [agent integration](agent-integration.md): resolve prerequisites and
shared-file changes, synchronize main when required, commit, then freeze the
clean checkout and exact base/head before full preflight:

```bash
uv run python scripts/ci_preflight.py --base <current-main-sha> --head <branch-head-sha>
```

The driver creates an isolated prospective merge under the
[standard temporary root](standards/local-temp-workspaces.md). It never edits
the source checkout or index. Conflicts, shallow history, missing tools, failed
checks, empty collection and unexpected relevant skips fail validation. Missing
comparison history selects all suites but does not waive history-dependent checks.
Revalidate after changes to code, base, toolchain or validation contract. Local
hooks and evidence supplement GitHub checks; they grant no release authority.

One nonblocking kernel lock at
`/tmp/shared-datasets-1/coordination/preflight-<uid>.lock` admits a full local
driver across worktrees and temporary roots. A busy slot fails and identifies its
owner. Do not wait automatically, delete the lock, or edit a validating checkout.
The kernel releases it on process exit; after a crash, inspect surviving owned
Docker work before restarting. Focused checks do not take this slot.

## Runtime and evidence

Docker must be running. The driver runs at most two isolated suites at a time,
starting production images first. Container suites run on the Docker server's
native Linux AMD64 or ARM64 architecture. Images must match that architecture;
no emulation is installed. Hosted native fixtures and all production-image
checks retain AMD64 coverage. Tool pins and installers are owned by
`scripts/ci_toolchain.py`, `scripts/ci_install_tools.py` and `.github/docker/`.

Containers receive copies of the clean source and return evidence; host
credentials, configuration and the Docker socket are never mounted. Production
image checks use a separate source copy and the host Docker client, with empty
HOME/Docker/XDG/gcloud configuration. Only explicit executable paths,
locale/temporary settings, proxy/CA settings and resolved Unix/TCP Docker
transport survive. Necessary daemon TLS files are copied separately and removed
afterwards. SSH/agent transport is unsupported. Host processes retain normal
filesystem and Docker access; configuration isolation is not a malicious-code
sandbox. Downloads and read-only Terraform initialization need network access.

Locked dependency sync, interpreter checks and actual executable version probes
remain mandatory. Per-suite `CI=true`, pinned Python, ARM OpenSSL portability and
browser reporter settings are recorded by the runner. Browser Git capture is
disabled because it can make a complete checkout shallow. Worker crashes and
browser failures remain failures; there are no test retries.

`plan.json` binds base, original head, tested revision/tree, selected suites,
deployments and contract digest. Each suite records commands, exits, logs,
actual tools and source attempt. `runtime.json` records platforms and image IDs.
`preflight.json` exists only after complete verification and source rechecks.
Successful local preflight removes only its own verified image TARs, retaining
manifests/hashes/logs. Failed runs retain available evidence and archives.

## Selection and PR deployment checks

`scripts/ci_contract.py` owns selection. Unknown/shared inputs select all suites;
component inputs select their consumers. `lint` and `tests` are always present.
Documentation-only changes run whitespace, secret scanning, locked sync,
catalog documentation consistency and site generation. Catalog inputs,
templates, asset docs and fixtures keep their consumer checks. The packaged
TypeScript README runs both Node release-policy checks.

Every selected deployment consuming a tested image forces `production-images`
and its required image target. IaC-only changes build those targets rather than
all recipes. Traced Docker/probe inputs can require additional images; shared or
unknown inputs retain all recipes. Narrow automation exceptions are guarded by
reverse-import and Docker-input tests. Selection never grants mutation authority.

Non-documentation PRs run workflow syntax, Terraform formatting, credential-free
`init -backend=false`/`validate` for prod and preview, and the real Terraform
dependency-closure checker. That checker uses a disposable local backend,
lock-verified providers, exact workflow allowlists and
`terraform/iam-plan-prerequisites.json`, including historical negative controls.
It reads no production state. Python policy tests also evaluate provider-free
Terraform console fixtures. Every non-documentation lint run also checks all
offline release contracts: declared permissions, saved-plan probes, image pushes
and signed receipt boundaries.
Live IAM, production-state plans and canaries still require protected execution.

The Python suite runs the complete collection with two workers and no worker
replacement; only named native fixtures may skip there. Native validation runs
the complete selected corpus with no skips. SDKs execute Node 22 and 24 tests,
release policy and packed-consumer checks. Browser validation keeps its
no-skips/no-retries report gate. See the shared command builder for exact checks
rather than duplicating command lists here.

## Tested images and hosted gate

Selected production recipes run at most two targets concurrently in the existing
producer job, with separate target outputs and a single checked manifest. The
producer installs only Python and uv alongside the runner's Docker tools.
Recipes are built and exercised by immutable image identity,
then exported, hashed and loaded again through the deployment loader. Exact
config/rootfs hashes bind retained bytes. Layer caches never substitute for
validation evidence. Native/usage images use per-recipe Actions caches; the
small viewer/monitoring images avoid the demonstrated long remote-cache stalls.
Base digest updates remain reviewed dependency changes. Completed cache/handoff
experiments are in [history](history/); their diagnostic jobs are retired.

Every hosted suite uploads `ci-result-<suite>-attempt<N>` diagnostics and a compact
`ci-gate-result-<suite>-attempt<N>` containing only `result.json`. Image TARs are
separate `ci-image-<target>-attempt<N>` artifacts at compression level 1. The
image result keeps the selected manifest and logs; its compact receipt requires
every selected image upload plus diagnostics to succeed. Artifacts last 14 days.
SDK results retain their exact tested package and hash.

`ci-ready` always evaluates. It downloads compact results and independently
checks every selected suite, both Node entries, exact revision/tree/contract,
toolchains and API-proven producing jobs. Missing, failed, cancelled or
unexpectedly skipped work fails the gate. Its evidence names the original plan
and suite artifacts. Selective reruns may use passing jobs from earlier attempts
of the same run only with exact source identity and successful job proof; a
newer failure cannot reuse an older success.

Consumers distinguish the triggering attempt from the execution that produced
the gate. Gate evidence must be created inside that successful execution's
window and verified in its declared attempt. Plan/results cannot be newer than
the gate. A newly executed gate with missing evidence cannot reuse an older gate.
Image consumers verify API ZIP digests, exact manifests, bounded TARs,
config/rootfs hashes, loaded images and registry digests; missing evidence fails
without rebuilding. The loader emits phase timings, not authorization.

PR-to-main cross-run reuse was removed after observing 0/56 production-image,
14/56 native and 12/53 browser hits. It never shortened the dominant image
critical path and required a second provenance/retention policy. Main executes
selected suites afresh; same-run selective rerun proof remains.

A newer PR run cancels the whole obsolete PR graph. Main-push and manual runs
have unique concurrency groups and retain their evidence. Protected deployments
keep non-cancelling state locks. The complete CI workflow runs on every PR with
no workflow-level path filters; retain required checks through any ruleset
migration. `.github/ingestion-rollout-holds.json` still suppresses only automatic
ingestion deployment: validation and image retention continue. Clearing a hold
alone does not select a new deployment.

## Workflow names and focused checks

Actions labels use `Domain / Purpose`; filenames/job names remain stable for
provenance. `scripts/workflow_identity.py` retains historical display-name aliases.
The packaged SDK README keeps its released label until a versioned SDK release.
`Bucket hygiene audit`, `Catalog drift guard` and `Dataset breaking change alert`
labels are also bound in the read-only OIDC provider: renaming needs a reviewed
protected IAM change first. The actionlint adapter independently validates
`concurrency.queue` before omitting that unsupported scalar from parser input.

During editing, use the locked uv environment and affected tests:

```bash
uv run pytest -q <affected-test-files>
uv run ruff check .
uv run python scripts/check_workflow_syntax.py
```

Native fixture runs additionally require pinned GDAL, Tippecanoe and PMTiles and
`RUN_GDAL_INTEGRATION_TESTS=1`. For standalone Terraform checks, use the pinned
version, `terraform fmt -check -recursive terraform/`, then initialize each
environment with `-backend=false -input=false -lockfile=readonly` and validate.
Focused checks do not replace full clean-checkout preflight before pushing.
