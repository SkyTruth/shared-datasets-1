# Complete validation before pushing

This is the authoritative validation procedure for local work. GitHub's
`ci-ready` independently checks hosted evidence; a local pass grants no merge,
deployment or publication authority.

## Validate before push

1. Finish focused tests for the changed behavior and refusal cases while editing.
   For any real dataset publication, complete [publication readiness](publication-readiness.md)
   first; publication-code changes without a pending release use complete synthetic
   runtime/serialization fixtures and `uv run python scripts/publication_runtime_check.py`.
2. Finish prerequisites and resolve overlaps with the integration owner when
   working concurrently; [agent integration](agent-integration.md) owns that
   coordination. Fetch current main and complete any authorized base update before
   freezing the head. This procedure does not authorize Git mutations.
3. Commit the focused changes when authorized, and use a clean checkout with full
   history. Ensure Docker supports Linux AMD64 and ARM64 execution; production
   image checks retain Linux AMD64 coverage. Use the repo's locked uv environment.
4. Run the complete driver against the exact current main and branch head:

   ```bash
   uv run python scripts/ci_preflight.py --base <current-main-sha> --head <branch-head-sha>
   ```

5. Read the final evidence and command logs. Fix missing tools, failed checks,
   empty test collection and unexpected relevant skips before pushing. Focused
   checks cannot replace this full run.
6. Recheck head, current main and checkout cleanliness before the first push.
   Revalidate before every later push when code, comparison base, toolchain or
   validation contract changed. An unchanged qualifying run need not be repeated
   for a PR-body edit or while waiting on hosted checks.

## Isolation and tools

The driver validates an isolated prospective merge under the
[standard temporary root](standards/local-temp-workspaces.md), preserving the
working tree and index. One nonblocking kernel lock admits a local driver across
worktrees; a busy result names the owner and is a failure, not a pass. Never
remove its lock, poll for an automatic retry or edit/commit in a validating
checkout. The kernel releases the slot on process death; inspect surviving
process-owned Docker work before another heavy run.

Host Docker configuration and inherited credentials are isolated from checks;
required host capabilities still have to exist. The pinned Linux toolchain is
owned by [`ci_toolchain.py`](../scripts/ci_toolchain.py), installed by
[`ci_install_tools.py`](../scripts/ci_install_tools.py), and used by local
containers and the hosted [`ci-tools` action](../.github/actions/ci-tools/action.yml).
Workflow syntax validation requires checksum-verified ShellCheck **0.11.0**;
missing or different versions fail before actionlint runs. Exact executed
commands and resolved tool versions are recorded in suite evidence.

## Selection and offline checks

[`ci_contract.py`](../scripts/ci_contract.py) resolves deployments first, then
required suites and `production_image_targets`, into one serialized plan.
GitHub outputs, suite runners, `ci-ready` and the tested-image loader consume
that image list unchanged; they must not independently infer a matrix from paths.
Each selected deployment imposes its image-validation floor, including when a
change only releases a rollout hold. Selection never authorizes mutation.

Image dependencies follow Dockerfile COPY inputs and local Python import closure,
including relative imports and literal subprocess scripts. Native and browser
suites follow their fixture import closures. Unrelated scripts/workflows keep
lint and Python coverage without unnecessary image builds; unknown inputs or
missing comparison history broaden validation.

Terraform deployment sources derive from the ingestion and Terraform registries,
which also select their automatic consumers. State-reader and state-only
reconciliation remain manual-only. An IAM prerequisite without a Terraform
declaration fails plan construction before validation fanout.

Non-documentation lint runs credential-free Terraform init/validate, dependency
closure, and changed-HCL apply-scope/permission checks. These checks cannot prove
dynamic resource values, instance keys or effective IAM; protected jobs still
validate their exact saved plan and live permissions before mutation.

The same lint suite runs the offline immutable-plan check:

```bash
uv run python scripts/reviewed_dataset_plan.py check-diff --base <base-sha> --head <head-sha>
```

It validates added committed plan bytes/path/schema and refuses edits or deletion
of existing JSON plans; the dedicated WDPA-build envelope retains its own validator.
Offline acceptance never substitutes for protected PR approval, source generations
or live publication prerequisites. Documentation-only plans use lightweight diff,
secret, catalog-doc and catalog-site checks instead of all runtime suites.

## Image execution and hosted evidence

[`production_image_contracts.py`](../scripts/production_image_contracts.py)
builds actual production recipes and runs installed probes under Terraform CPU
and memory limits, using Cloud Run defaults only when service limits are unset.
Sea ice runs its synthetic conversion inside the sea-ice image under **2 vCPU /
512 MiB**; WDPA's installed smoke builds both WDPA realms. EAMLIS and MPAtlas
build tiny native fixtures through the linted
[`installed_ingestion_fixture.py`](../scripts/installed_ingestion_fixture.py).
Installed checks run without network or source mounts. The sea-ice installed probe uses
`local_ingestion_smoke.py --sea-ice-only` once in CI before image retention.

Hosted production images use a fail-fast target matrix. Local image batches run
at most two producers and terminate remaining producers on failure. Each result
binds target, SHA/tree, toolchain and exact producing run/attempt. A compact gate
receipt requires diagnostic evidence and, outside PRs, the tested TAR upload.
PRs have no deployment consumer and retain image validation metadata without TAR uploads.
The matrix installs pinned Python through uv; viewer/Slack image jobs skip buildx
and remote layer caching, preserving exact-image evidence.

`ci-ready` requires every selected suite/image and both Node 22/24 runtimes when
SDK validation is selected, with API-proven successful producers. Missing, stale,
failed, cancelled or unexpectedly skipped evidence fails. Same-run selective
reruns retain exact attempt proof; cross-run reuse is unsupported. Historical
image receipts remain readable, and deployment loaders never rebuild missing bytes.
Artifacts last 14 days. PR runs cancel obsolete PR graphs; main/dispatch evidence
and protected mutation locks remain independent.

Evidence hashes include complete validator/common modules and local composites,
including nested actions, so changes invalidate incompatible proof. Native fixture
tools and browser no-skips/no-retries requirements remain mandatory.

## Holds, failures and retries

Rollout holds retain validation and images while blocking deployment. Hold schema,
Scheduler pauses, expiry warnings and safety checks have one reference in
[alert routing](alert-routing.md#scheduled-freshness-and-capacity). Clock expiry
does not alter selection or make `ci-ready` fail; a reviewed hold removal selects
its deployment and exact image on the next main push. Historical bare-name hold
lists remain readable only as comparison baselines.

Use [CI/deployment recovery](ci-recovery.md) for bounded hosted retries and failed
operations. Terraform's separate pre-mutation lock/plan-read retry contract lives
in [deployment contracts](deployment-contracts.md#serialization-and-protected-execution).
Neither retry mechanism permits replaying an uncertain production mutation.
