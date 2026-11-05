# Complete validation before pushing

Complete [publication readiness](publication-readiness.md) and
[agent integration](agent-integration.md), commit, and freeze the clean checkout:

```bash
uv run python scripts/ci_preflight.py --base <current-main-sha> --head <branch-head-sha>
```

The driver uses an isolated prospective merge under the [standard temp root](standards/local-temp-workspaces.md).
One nonblocking kernel lock admits a local driver across worktrees; a busy result
names its owner. Never delete its lock or edit a validating checkout. Revalidate
when code, base, toolchain or contract changes. Local evidence grants no authority.
Docker must provide Linux AMD64/ARM64; production images retain AMD64 coverage.
Host image checks isolate inherited credentials/configuration, not host capabilities.
Pinned tools, exact commands and evidence formats live in
`scripts/ci_toolchain.py`, `scripts/ci_preflight.py` and `scripts/ci_contract.py`.

Terraform PRs run credential-free init/validate, dependency closure and changed-HCL
apply-scope/permission-contract checks using `scripts/ingestion_targets.py` and
`.github/terraform-targets.json`. Registry changes select their automatic consumers;
state-reader and state-only reconciliation remain manual. Static checks cannot prove dynamic values,
instance keys or effective IAM; protected saved-plan/live probes remain required.
Unknown inputs keep broad validation; selection never authorizes deployment.

Hosted images use a fail-fast target matrix; local image batches run two targets
and terminate remaining producers on failure. Each matrix result binds its target,
SHA/tree, toolchain and producing attempt. Its compact receipt requires both TAR
and diagnostic uploads. `ci-ready` requires all selected suites/images and both
SDK runtimes, with API-proven successful producers. Missing/stale/failed/cancelled
or unexpectedly skipped evidence fails. Same-run selective reruns retain exact
attempt proof; cross-run reuse is unsupported. Historical image receipts remain
readable. Loaders verify retained/registry bytes without a rebuild fallback.
Artifacts last 14 days. PR runs cancel obsolete PR graphs; main/dispatch evidence
and protected mutation locks remain independent.

`.github/ingestion-rollout-holds.json` maps targets to nonempty `owner`, `reason`
and timezone-qualified `expires_at`. Expired holds fail selection/lint. Holds
retain validation/images; removing one selects deployment on the next main push.
Historical bare-name lists are readable only as comparison baselines.
Use focused pytest/ruff/workflow-syntax checks while editing; they do not replace
full preflight. Native fixture tools and no-skips requirements remain mandatory.
