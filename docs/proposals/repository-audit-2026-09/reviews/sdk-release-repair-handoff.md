# SDK release repair implementation handoff

Date: 2026-09-24. Branch: `codex/fix-sdk-release-review`.
Base: `da2f61b28d5d7e1ae10d9aa32cb416769cf90be1`.
Status: implemented for supervisor review; no merge or publication authorized.
Plan: [approved SDK release plan](../plans/sdk-release-repair.md).

## Behavior and release consequence

The failed release tried to push an automatically generated version commit to
protected main. The publisher now has read-only Git access and publishes only a
version already present in reviewed source. This branch explicitly prepares
**0.9.0** in both package manifests. Approving and merging this branch to main
will intentionally trigger publication of that version, including SDK consumer
changes already merged under 0.8.0. Branch validation never publishes it.

A small shared script checks exact base/head manifests, package/lock agreement,
and release-content changes. PRs and main pushes must increase the stable version
for source, packed README, package/lock metadata, or tsconfig changes. There is
no special repair-only exemption. Workflow/test-only changes outside those paths
do not invent a release. Registry, JSON, Git, or metadata failures stop the
publisher. A higher version may publish; an existing version succeeds only if
the packed artifact's SHA-512 integrity matches npm. Older retries cannot retag
an existing version as latest.

The workflow publishes the exact tarball validated in an isolated consumer. New
Node 24 CI exercises root/server runtime exports and strict TypeScript imports,
while ordinary Node 22 CI remains in place. Smoke artifacts and npm's pack/cache
writes use an invocation-owned directory under the standard repo temp root.
The dependency-free SDK is installed offline into that consumer with lifecycle
scripts disabled. TypeScript and Node declarations come from the locked dev
toolchain; SDK imports resolve from the consumer's installed tarball.

## Changed files

- `.github/workflows/publish-typescript-sdk.yml`: remove Git writes/auto bump;
  check reviewed content, test and validate tarball, compare registry integrity,
  publish exact checked bytes. Main-only, serialized, 15-minute limit.
- `.github/workflows/sdk-validation.yml`: read-only Node 24 checks for every PR,
  main push, or explicit dispatch; no path-filtered missing check.
- `api/typescript/scripts/release-policy.mjs`: pure version/content/registry
  decisions plus narrow read-only Git/npm command boundary.
- `api/typescript/scripts/package-smoke.mjs`: actual npm pack, isolated install,
  runtime/type resolution, and artifact identity for the publisher.
- `api/typescript/tests/release-policy.test.mjs`: eight policy/boundary tests.
- `api/typescript/tests/package-smoke.test.mjs`: file-contract and actual consumer
  tests, including removing real packed declarations and JavaScript.
- `api/typescript/package.json`, `api/typescript/package-lock.json`: explicit
  reviewed 0.9.0; one `test:pack` command; dependencies unchanged.
- `tests/test_typescript_sdk_release_workflow.py`: replace tests demanding the
  broken write policy with three workflow-wiring/permission tests.
- `README.md`, `api/typescript/README.md`: reviewed versioning, smoke checks,
  publication/retry behavior; remove duplicate development instructions.
- This handoff and its approved plan preserve the local supervision record.

## Validation performed

From this worktree, using the existing locked SDK dev dependencies (all installed
dependency versions checked against package-lock before copying to this isolated
worktree; no dependency or lock resolution changed):

| Check | Result |
|---|---|
| Node 24 `npm --prefix api/typescript test` | 41 passed, zero skipped |
| Node 22 `npm --prefix api/typescript test` | 41 passed, zero skipped |
| Node 24 `npm --prefix api/typescript run test:pack` | Actual packed root/server runtime and declaration imports passed |
| `uv run --no-sync pytest tests/test_typescript_sdk_release_workflow.py -q` | 3 passed |
| `uv run --no-sync ruff check tests/test_typescript_sdk_release_workflow.py` | Passed |
| `actionlint .github/workflows/publish-typescript-sdk.yml .github/workflows/sdk-validation.yml` | Passed |
| `uv run --no-sync python scripts/repo_guardrails.py check-static` | Passed |
| `git diff --check` | Passed |

Regression tests cover missing bump, rollback, invalid stable versions, both
lockfile versions, exact Git revisions, failed Git reads, higher-version release,
identical retry, changed/missing integrity, older version, malformed registry,
404/auth/network errors, missing packed declaration/runtime exports, and
accidentally packed source. Registry tests inject read-only command responses;
they do not call npm publication. The supervisor independently read npm's live
published-version list and confirmed it ends at 0.8.0.

An initial local smoke attempt was blocked from npm's home cache by the sandbox.
The script now keeps both pack and consumer cache files in its own scratch
directory; no machine cache permissions were modified. Fresh test-created
directories are removed by their owning tests. Standalone smoke artifacts are
retained and the command prints their exact path.

## Removal and invariant review

- Invariant enforced: reviewed version/source determine immutable published
  bytes; publishing cannot mutate the source branch or repair its version.
- Boundary changed: exact Git diff plus manifests, registry versions/integrity,
  actual packed-consumer resolution.
- Code removed: inline auto-bump implementation, commit-message trailer parser,
  automatic commit/push/config, write permission, old dry-run-only validation.
- Internal handling removed: failed Git commands returning empty changes and
  npm 404 interpreted as permission to bootstrap publication.
- Fallbacks added: none. Exact-integrity retry is an explicit success case.
- Fallbacks rejected: PAT/protection bypass, bot-generated version PR machinery,
  silently accepting changed bytes under an existing version.
- Deletion candidates retained: Node 22 CI remains intentional compatibility
  coverage; existing bootstrap documentation remains limited to new packages.
- Remaining uncertainty: no actual OIDC publication was run. Trusted-publisher
  settings are pre-existing and unchanged. Branch protection must separately
  require the new validation job if maintainers want it enforced as a merge gate.

## Supervisor completion checks

Review the release consequence and 0.9.0 version before committing. After the
supervisor commits this branch, run the real `release-policy.mjs changes BASE
HEAD` command against its committed baseline/head; the subagent did not mutate
Git to create temporary commits. Independently repeat the Node 24 smoke check,
then preserve the branch for user review before any PR/merge decision.

Primary documentation checked: [npm trusted publishing](https://docs.npmjs.com/trusted-publishers/)
requires npm 11.5.1 or later and a supported Node version; [npm publish](https://docs.npmjs.com/cli/v11/commands/npm-publish/)
accepts a tarball. The workflow keeps Node 24 and existing OIDC configuration.

No Git index/history mutation, npm publication, workflow dispatch, repository
settings change, dataset write, or infrastructure change was performed by the
implementation subagent.
