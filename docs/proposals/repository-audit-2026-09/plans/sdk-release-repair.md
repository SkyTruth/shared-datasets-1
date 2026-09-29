# TypeScript SDK release repair — plan for supervisor approval

Original status: PLAN ONLY; planning did not mutate source, Git, registry, or environments.

Supervisor approval: **PLAN APPROVED on 2026-09-24.** Implementation authorized
on `codex/fix-sdk-release-review`, including an explicit reviewed 0.9.0 candidate.
Publication, workflow dispatch, merge, and production mutation remain outside
this implementation authorization. The supervisor verified npm currently ends
at 0.8.0. See the associated review handoff for implemented scope and results.

## Baseline and defect

Baseline main is `da2f61b28d5d7e1ae10d9aa32cb416769cf90be1`. Inspected the matching
SDK/release workflow in the `feature-id-ci-fix` checkout. Existing release CI
35944367067 failed when the workflow tried to commit an automatic 0.8.0 → 0.9.0
bump and push it directly to protected main. The workflow currently grants
contents write, parses commit-message bump hints, silently treats failed Git
diff/log reads as empty results, mutates package files, and then publishes.
Existing workflow tests explicitly require that faulty policy. Ordinary CI uses
Node 22 and source-tree tests; publication uses Node 24. `npm pack --dry-run`
does not prove separate consumers can import installed runtime/type exports.

## Invariant and boundaries

- Invariant: a published SDK version and its source are reviewed on main; the
  publisher never edits or pushes repository content. A content-changing PR must
  explicitly increase the version. A retry cannot replace or silently accept
  different bytes under an already-published version.
- Impossible states: implicit commit-message version choice; write-capable Git
  checkout; source changes with unchanged version passing release policy;
  network/registry errors represented as unpublished package; successful
  packaging validation that only imports the source worktree.
- Established by: explicit package/lockfile version consistency and PR diff
  policy, followed by a registry/packed-artifact boundary in the publisher.
- Consumed by: npm trusted publishing of the already-tested tarball.
- Trust boundaries: immutable Git revisions, package/lock JSON, npm registry
  response, npm-produced tarball/manifest, consumer resolution outside repo.
- Failure classification: producer/workflow bug plus missing boundary checks.

## Smallest complete change

1. Delete auto-bump and Git write behavior entirely. Use contents read,
   persist-credentials false, Node 24, existing OIDC trusted publisher. Keep the
   main-only job, serialized release concurrency, automatic main trigger, and
   manual retry entrypoint. Publish the packed and validated tarball explicitly.
2. Add one small SDK release-policy script used by Node 24 PR validation and the
   release workflow. A PR must increase its stable `major.minor.patch` version
   when it changes src, packed README, tsconfig, runtime dependencies, exports,
   package metadata, or the lockfile. Package and lockfile root versions must
   agree. Match the existing simple release-content path list; do not invent
   repair-only exceptions for scripts, dependency metadata, or docs contained in
   the package. Changes only to unpacked tests/tool scripts and workflows do not
   require a version bump. Unknown package fields count as content changes. Git
   failures and malformed manifests fail. Stable versions are the supported
   contract; prerelease distribution would need an explicit tag policy later.
3. On main pushes, repeat the same before/head content-and-version boundary so
   bypassing PR checks cannot publish or silently skip unversioned content. If
   neither release content nor version changed, skip before registry lookup.
   On an explicit version change or manual dispatch,
   read the registry's published versions. Require a new version to exceed
   existing stable releases. If that exact version already exists, compare its
   published integrity to the candidate packed tarball: equal means a successful
   idempotent retry; unequal is an actionable failure. Missing package, malformed
   metadata, unauthorized responses, network failures, and invalid versions fail
   closed. No bootstrap fallback: this package already exists. Test registry
   decisions using injected/fake commands; do not publish while validating.
4. Add a separate `sdk-validation.yml` for ordinary PRs and main pushes, running
   Node 24 SDK tests and packed-consumer validation. Do not edit shared ci.yml
   (the native CI agent owns it); retain its Node 22 compatibility coverage.
   Avoid path-filtered required checks: job runs consistently, PR release-policy
   receives the exact base SHA, checkout fetches sufficient history.
5. Add a package-smoke script: build/pack into a process-owned named directory
   under the repo temp root, install the tarball into a separate consumer without
   lifecycle scripts/network dependency resolution, then execute representative
   root and server imports and compile strict NodeNext type imports. Inspect
   packed metadata to prove entrypoint JS/declarations and README are included
   and source/tests/scripts/node_modules are excluded. Export metadata/tarball
   path for the release workflow so the published bytes are the checked bytes.
   Failures retain useful diagnostics; cleanup targets only directories this
   invocation created.
6. Prepare the explicit reviewed 0.9.0 package/lockfile bump in this branch. This
   releases the already-merged SDK consumer changes together with the corrected
   release controls if the user later approves merging this branch. Update root
   README release instructions: explicit version command in a PR,
   commit both manifests, CI requirements, no automatic bump/trailers, reviewed
   merge publishes, retry integrity rule. Update SDK README with local validation
   commands and the deliberate package-version review requirement.

## Version decision and existing data

Prepare version 0.9.0 in both package manifests as an explicit reviewed change,
not a workflow-generated bump. Current main includes unreleased SDK consumer
changes under 0.8.0; a 0.9.0 release matches the failed workflow's intended minor
release while leaving the final choice visible in this branch. Supervisor must
verify registry state before freezing the branch (no publish). If 0.9.0 has been
published in the meantime, return for a revised version decision rather than
silently generating another version.

No publication occurs during development, committing, pushing the feature branch,
or opening its eventual PR. Merging the approved branch to main will intentionally
trigger an npm release; explain that consequence in the branch handoff/PR. A
same-version dispatch after successful publication succeeds only if the candidate
tarball has the same registry integrity. No repair-only content exemptions.

## Intended files

- `.github/workflows/publish-typescript-sdk.yml` (replace inline bump machinery)
- `.github/workflows/sdk-validation.yml` (new read-only Node 24 CI)
- `api/typescript/scripts/release-policy.mjs` (new shared boundary owner)
- `api/typescript/scripts/package-smoke.mjs` (new real consumer check)
- `api/typescript/tests/release-policy.test.mjs` (behavioral policy tests)
- `api/typescript/tests/package-smoke.test.mjs` (real isolated package/declaration
  failure cases, kept separate from pure release-decision tests)
- `api/typescript/package.json` (test:pack command and reviewed 0.9.0)
- `api/typescript/package-lock.json` (matching reviewed 0.9.0; no dependency changes)
- `api/typescript/README.md` (local validation and version review commands)
- `tests/test_typescript_sdk_release_workflow.py` (replace old write-policy
  assertions; validate trigger, permissions, actual command invocation, conditions)
- `README.md` (authoritative release instructions)
- `docs/proposals/repository-audit-2026-09/plans/sdk-release-repair.md`
- `docs/proposals/repository-audit-2026-09/reviews/sdk-release-repair-handoff.md`

## Required tests and completion evidence

- Reproducer: baseline workflow requires contents write and direct Git push;
  revised workflow contains neither and executable policy is exercised.
- PR boundary: source/README/export/runtime-dependency/build changes without bump
  fail; proper increase succeeds; rollback, malformed stable versions, package /
  lock mismatches fail; test-only changes with same version succeed; Git errors
  fail; unknown package metadata changes demand a bump.
- Publisher boundary: content-changing no-bump main push fails; workflow-only
  no-bump main push skips; higher reviewed version publishes;
  same-version exact-integrity retry skips; different integrity fails; old/new
  version collision fails; malformed registry response, 404, auth/network errors
  fail without any publication output. Numeric comparison tests include 0.9 vs
  0.10, not lexicographic ordering.
- Packed consumer: actual root/server JS calls and declarations resolve only
  from installed tarball. Deliberately remove a packed runtime/type export in a
  disposable package and prove validation fails; no source-tree import fallback.
- Node 24 `npm test`, release-policy tests and `npm run test:pack`; retain Node 22
  source tests. Focused pytest workflow tests, ruff touched Python, repo static
  guardrails, Git diff check. Supervisor supplies exact base/head validation.
- No source API or existing npm artifacts change during this task; future approved
  merge publishes the reviewed 0.9.0 candidate. No network install without
  supervisor coordination, no npm publish, dispatch, push, commit, or merge by
  subagent. Supervisor owns Git and final review.

## Alternatives, deletions, and scope

Reject a PAT/bypass: it evades the review boundary and adds privilege. Reject an
automatic release PR bot: unnecessary state, credentials, retry coordination,
and review churn for a small SDK. Reject just deleting git push: it would publish
an unreviewed version or silently skip source changes. Reject always treating
already-published version as success: it conceals differing source bytes.

Remove commit-message bump parsing, custom prerelease bump machinery, auto
commit/push/config, contents-write permission, Git-error empty fallback, npm
404-as-bootstrap fallback, and tests that require these paths. Keep existing
Node 22 CI as a compatibility check. No deployment/production/GCS work or SDK
public API changes. No dependency conflict with native/browser agents because
this package owns a separate workflow and its own SDK paths.

Remaining limitation: proposed protections only apply after this workflow lands;
branch protection must require the new validation job to make it an enforced
merge gate. No repository-settings change is included. Trusted-publisher account
configuration is pre-existing and not changed; real OIDC publish is not exercised
by a branch test. Parent should verify against current npm docs if workflow
details beyond existing working configuration need adjustment.
