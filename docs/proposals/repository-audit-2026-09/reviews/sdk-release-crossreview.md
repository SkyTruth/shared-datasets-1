# SDK release repair independent cross-review

Reviewed after implementation agent source freeze on 2026-09-24.
Branch: `codex/fix-sdk-release-review`; baseline
`da2f61b28d5d7e1ae10d9aa32cb416769cf90be1`.

Decision: **no blocking findings**. Ready for supervisor acceptance and the
previously agreed user branch/PR review, subject to normal exact-head CI.
This is source review, not npm publication or merge approval.

## Reviewed conclusions

1. The observed protected-main failure is removed at its source: repository
   permission is contents read, checkout does not persist credentials, and
   the publisher neither generates a version nor commits/pushes. The explicit
   0.9.0 version is visible in package and both lockfile version fields.
2. Content/version policy is shared by PR validation and main publication.
   Git read failures, malformed manifests, mismatch, rollback, and no-bump
   content changes fail. PR checkout uses the GitHub merge revision and the
   actual base SHA, checking the resulting merge rather than a detached
   source-only view. Full fetch supplies the comparison history.
3. Registry reads use npmjs explicitly and propagate network/auth/404/JSON
   errors. A new version must exceed existing releases; an already-published
   version is an idempotent success only for identical SHA-512 tarball bytes.
   Existing-version retries never publish or move latest backwards. The
   stable-only contract is explicit rather than silently ignoring prereleases.
4. Smoke validation packs a real tarball and installs it offline into a fresh
   separate consumer with lifecycle hooks disabled. Root/server runtime
   resolution is checked to stay under that installed package, and strict
   TypeScript import checks resolve the package declarations there. The
   compiler itself correctly comes from the locked dev toolchain. Negative
   tests remove real packed runtime/declaration exports.
5. The registry step rehashes the candidate after testing, and the final step
   publishes that same tarball with lifecycle hooks disabled. It does not
   rebuild a different package at publication time. OIDC permission and the
   existing trusted-publisher workflow identity are retained; Git privileges
   are reduced.
6. The change deletes the inline bump/commit machinery and replaces it with
   two focused scripts. No release bot, registry framework, fallback version,
   source import shortcut, or dependency change was introduced. A minor docs
   duplication noted during review was removed before source freeze.

## Independent validation

- `node --test api/typescript/tests/release-policy.test.mjs`: **8 passed** on
  the host Node 20.20.0 runtime. This is supplemental policy evidence; it does
  not replace the implementation agent's Node 24 and Node 22 runs.
- `uv run --no-sync pytest tests/test_typescript_sdk_release_workflow.py`:
  **3 passed**.
- `actionlint` for both changed/new workflows: passed.
- `git diff --check`: passed.
- Read final scripts, tests, workflows, package/lock diff, authoritative release
  docs, and implementation handoff. The handoff reports 41 tests passing on
  each Node 24/22 and a standalone packed-consumer pass; this reviewer did not
  repeat installation or artifact generation.

## Remaining release checkpoints

The supervisor should run the committed exact-base/head policy command and
normal CI before final approval, as already required by the implementation
handoff. Merging this branch intentionally triggers the reviewed 0.9.0 npm
release. Actual OIDC publication is not proven by branch tests; no publication
was attempted. Making the new CI job a required branch-protection check remains
an explicitly separate repository-settings decision, not an implementation
blocker or a claim this branch has changed those settings.

This reviewer made no source changes, Git mutation, installation, network
request, npm publication, workflow dispatch, or remote mutation. The only file
written was this local review artifact.
