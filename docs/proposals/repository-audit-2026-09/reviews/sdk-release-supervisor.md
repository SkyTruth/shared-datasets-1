# SDK release supervisor review

Reviewed 2026-09-24 against main `da2f61b28d5d7e1ae10d9aa32cb416769cf90be1`.
The supervisor approved the implementation plan before changes and explicitly
approved preparing version 0.9.0 for branch review. Live read-only npm metadata
confirmed published versions 0.1.0 through 0.8.0.

Accepted for a committed branch and user PR decision. Merging this branch will
intentionally trigger publication of 0.9.0; no release is performed by this
acceptance. Actual trusted-publisher authentication still requires the release
workflow and is not proven by local tests.

The supervisor reviewed both scripts, behavioral tests, workflow permissions
and command wiring, package manifests, and release documentation. The separate
cross-review found no blocking issue. Automatic version edits and protected-main
pushes are deleted. Publishing reads Git, checks an explicitly reviewed version,
tests a real installed package, rehashes that same artifact, and refuses a
registry collision with different bytes.

Independent checks:

- Node 24.13.1 `npm test`: 41 passed, zero skipped, including broken installed
  runtime/declaration regressions.
- Node 24 standalone `npm run test:pack`: real installed root/server runtime
  and strict declaration imports passed; candidate retained in the task evidence
  directory.
- Workflow and repository guardrail tests: 32 passed, 101 subtests passed.
- Full-repository Ruff and actionlint on both SDK workflows passed.
- Implementation validation additionally covered all 41 tests on Node 22.

The supervisor will run the real Git base/head version policy after committing,
because that command deliberately reads committed manifests rather than the
working tree. This is a final commit checkpoint, not a reason to mutate Git in
tests or weaken the policy.

Tradeoff: maintainers now choose and review each stable version explicitly.
Every packed README/manifest/lock change needs a version increase, which is
stricter than an automatic bump. This is simpler and more auditable than a
release-PR bot, and avoids granting a token permission to bypass branch
protection. Registry failures remain visible failures rather than silent skips.

The new validation workflow runs on every PR but is not automatically a required
branch-protection check. That settings decision remains separate. No remote
publication, deployment, infrastructure mutation, or dataset operation was
performed. Routine CI/release repair does not warrant a repo-alert block.
