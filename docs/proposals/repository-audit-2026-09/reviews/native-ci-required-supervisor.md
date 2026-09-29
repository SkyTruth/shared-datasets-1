# Native CI supervisor review

Reviewed 2026-09-24 against main `da2f61b28d5d7e1ae10d9aa32cb416769cf90be1`.
The implementation plan was approved before source changes. The supervisor
also approved adding `pyproject.toml` and `uv.lock` to native selection because
the native job installs those dependencies.

Accepted for a committed branch and user PR decision. This is not merge or
deployment approval. Actual native execution at the final branch head remains
a required GitHub Actions check after a PR is authorized.

The supervisor read the workflow, exact-result checker, regression tests and
implementation handoff. The success boundary requires both successful pytest
execution and exactly one passing result for each required fixture. Missing,
renamed, duplicated or skipped cases cannot manufacture a successful result.
Existing failure artifact upload and optional local skips remain appropriate.

Independent validation:

- Full `uv run --no-sync pytest -q`: 926 passed, 4 optional native skips,
  710 subtests passed, and one environment-specific failure described below.
  The nine new/focused native contract tests all passed within this run.
- Full-repository Ruff passed.
- The actual portable native-suite JUnit report has the four required names;
  it records skips, so it cannot serve as successful native execution evidence.

The sole full-suite failure was the unchanged
`test_binary_resolver_accepts_private_executable`: this worktree lives beneath
`/private/tmp`, and the existing Terraform executable resolver intentionally
rejects that location. The supervisor verified that both the test and resolver
are identical between the normal checkout and this branch's base. The same
test passed independently in the normal checkout (1 passed). No production
Terraform operation was performed and no unrelated exception was added.

Tradeoff: required fixture renames must update the small explicit identity
list. This is preferable to a total-count assertion, which can pass when an
important fixture disappears. A generic pytest plugin would add more policy
surface than these four fixed requirements need.

No repo-alert block is warranted: this is routine CI repair. Plans and reviews
are retained in this branch; detailed disposable reports remain in the named
remediation evidence directory.
