# Complete validation before pushing

GitHub's `ci-ready` independently validates hosted evidence. A local pass grants no merge, deployment or publication authority.

## Validate before push

1. Finish focused positive and refusal checks. Real releases require [publication readiness](publication-readiness.md); publication-code changes without a release require complete synthetic runtime/serialization batches and `uv run python scripts/publication_runtime_check.py`.
2. Resolve prerequisites/overlaps with the [integration owner](agent-integration.md), fetch current main and complete any authorized base update. Commit only when authorized; use a clean checkout with full history. Freeze code before full validation.
3. Run `uv run python scripts/ci_preflight.py --base <current-main-sha> --head <branch-head-sha>`. Docker must support Linux AMD64/ARM64 execution; production image checks retain AMD64 coverage. Use the locked uv environment.
4. Inspect final evidence/logs. Fix missing tools, failures, empty collection and unexpected relevant skips. Recheck head, main and cleanliness before pushing. Revalidate subsequent pushes after code, base, toolchain or contract changes; PR-body edits and waiting on hosted checks need no repeated run.

## Isolation and tools

The driver validates an isolated prospective merge under the [standard temp root](standards/local-temp-workspaces.md), preserving index/working tree. One nonblocking kernel lock admits a local driver across worktrees. Busy is failure with an owner, never a pass: do not remove the lock, poll for retries or edit/commit in a validating checkout. Inspect surviving process-owned Docker work after process death.

Host Docker configuration and inherited credentials are isolated; required host capabilities must exist. [`ci_toolchain.py`](../scripts/ci_toolchain.py) pins tools installed by [`ci_install_tools.py`](../scripts/ci_install_tools.py) and the [`ci-tools` action](../.github/actions/ci-tools/action.yml). Syntax checks require checksum-verified ShellCheck **0.11.0** before actionlint. Evidence records executed commands, resolved tools and versions.

## Selection and offline checks

[`ci_contract.py`](../scripts/ci_contract.py) resolves deployments, validation suites and `production_image_targets` once. Outputs, runners, `ci-ready` and image loaders consume that serialized image list; selected deployments impose an image floor, including hold-only releases. Selection grants no mutation authority.

Image inputs follow Docker COPY, Python imports/relative imports and literal subprocess scripts. Native/browser suites follow fixture imports. Deployment inputs derive from workflow/nested-action/script closure and the shared parsed Terraform index, including local modules. Reviewed input exceptions require reasons; admission/verification imports invalidate evidence without recursively creating deployment content. Manual-only state-reader/reconciliation workflows remain manual. Missing IAM prerequisite declarations fail before fanout. The prerequisite graph orders selected mutations; it does not select unchanged owners.

For `ci.yml`, compare parsed job definitions against the base: select changed suite jobs and both SDK runtimes for `sdk-validation`; shared scheduling/selector/gate changes select all suites. Missing comparison history broadens validation. Unrelated administration keeps lint/Python coverage.

Non-doc lint performs credential-free Terraform init/validate, dependency closure, changed-HCL apply-scope/permission checks and `uv run python scripts/reviewed_dataset_plan.py check-diff --base <base-sha> --head <head-sha>`. Immutable-plan validation checks added committed bytes/path/schema and rejects changes/deletion of existing plans; WDPA build envelopes retain their validator. Dynamic values, instance keys and effective IAM require protected saved-plan/live checks. Offline acceptance never replaces PR approval, source generations or publication prerequisites. Docs-only changes use lightweight diff, secret, catalog-doc and site checks.

## Image execution and hosted evidence

[`production_image_contracts.py`](../scripts/production_image_contracts.py) builds production recipes and executes installed probes under Terraform CPU/memory limits (Cloud Run defaults only when service limits are unset), without network/source mounts. Sea ice runs its synthetic conversion once at **2 vCPU / 512 MiB** with memory-backed `/tmp`; WDPA builds both realms. EAMLIS/MPAtlas use [`installed_ingestion_fixture.py`](../scripts/installed_ingestion_fixture.py). Monitoring executes an explicit installed receiver probe; test-module source is never the container payload.

Hosted images use a fail-fast matrix; local batches allow two producers and terminate remaining producers on failure. Results bind target, SHA/tree, toolchain and exact producing run/attempt. Compact receipts require diagnostic evidence and, outside PRs, tested TAR uploads. PRs retain metadata without unconsumed TARs. Viewer/Slack skip buildx/remote caching; Python is pinned through uv.

`ci-ready` requires every selected suite/image and both SDK runtimes, proven by successful producing jobs. Missing, stale, failed, cancelled or unexpectedly skipped evidence fails. Same-run selective reruns preserve exact attempt proof; cross-run reuse is unsupported. Historical receipts remain readable, loaders never rebuild missing bytes, and artifacts last 14 days. PRs cancel obsolete graphs; main/dispatch evidence and mutation locks remain independent.

Evidence hashes cover complete validator/common modules and nested local composites. Native fixture tools and browser no-skip/no-retry requirements remain mandatory.

## Holds, failures and retries

Holds retain validation/images while blocking deployment; [alert routing](alert-routing.md#scheduled-freshness-and-capacity) owns hold/pause schema, expiry and health checks. Clock expiry does not change selection or fail `ci-ready`. Reviewed hold removal selects its deployment/image on the next main push. Historical bare hold lists are comparison baselines only.

Follow [recovery](ci-recovery.md) for bounded hosted retries and [deployment contracts](deployment-contracts.md#serialization-and-protected-execution) for pre-mutation lock/plan-read retries. Neither authorizes replay of an uncertain mutation.
