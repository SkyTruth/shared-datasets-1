# Repository audit and PR #154 rollout records

These documents preserve the September 2026 audit, remediation plans, review
handoffs, branch decisions and CI repair notes that were previously held in
local temporary workspaces. They are committed with PR #154 so the written
reasoning survives cleanup of those workspaces.

Start with the [PR #154 merge and rollout plan](plans/pr154-merge-and-rollout.md).
That is the current planning checkpoint. The older documents retain superseded
proposals and historical status statements; later supervisor decisions take
precedence. None of these records authorizes new implementation, infrastructure
changes, migration or deployment. `AGENTS.md` remains the operating contract.

## Current checkpoint

At source commit `362c2604944ab188fb27506b58b881f8059717f6`, PR #154 is draft,
CI is green (including 94 native geospatial tests), and main is
`da2f61b28d5d7e1ae10d9aa32cb416769cf90be1`. PRs #148–#153 merged outside this
agent's work. PR #154 still requires the merge-isolation and production-readiness
gates in its plan. This documentation preservation does not clear those gates.

## Navigation

- [Original exhaustive repository review](initial-audit/repository-review.md)
- [Supervision contract and completion gates](CHARTER.md)
- [Execution board](EXECUTION-BOARD.md)
- [Review dossier](REVIEW-DOSSIER.md)
- [Branch and PR decisions](BRANCH-DECISIONS.md)
- [Commit record](commits/README.md)
- [Feature-ID source review](reviews/feature-id-highwater-supervisor.md)
- [Publication core source review](reviews/publication-recovery-f2-supervisor.md)
- [Deployment hold source review](reviews/publication-rollout-gate-supervisor.md)
- [Deferred publication contracts](plans/publication-recovery-f3-contracts.md)
- [PR #154 CI repair evidence summary](evidence/pr154-ci-fix/README.md)

The `plans/`, `reviews/`, `prompts/` and `prs/` folders also retain the other
packages' written plans, reviews, agent prompt and PR-body snapshots. A PR-body
snapshot is historical documentation, not an executable publish-plan document.

## Preservation limits

Only text documentation and its provenance inventory are included. Temporary
worktrees, source patches, dependency environments, downloaded datasets, cloud
exports, generated artifacts and raw execution logs remain outside this archive.
Referenced tests and logs are historical reported evidence, not newly rerun
verification. See [the manifest](archive-manifest.json) for every archived file,
its original/copied SHA-256, transformations and excluded artifact references.

Local paths were replaced with `${REPO_ROOT}`, `${REMEDIATION_WORKDIR}`,
`${INITIAL_AUDIT_WORKDIR}`, `${HOME}` or `${GIT_BIN}` placeholders. These describe
historical command context and are not configured environment variables.
Document links now resolve within this repository where their targets were
preserved. Links to excluded raw evidence became explicitly labeled historical
references. Source-file line anchors record the original review and may drift.

Original temporary workspaces were retained; this change deletes nothing.
