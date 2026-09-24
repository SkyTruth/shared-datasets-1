> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# PR 153: immutable executor/static guardrail alignment — revised plan only

Baseline: publish-approval HEAD `1ef87d293f75370b04b13cf399f275414d9b898a`. No tracked edits made for this diagnosis. Root owns `.gitleaks.toml`; another agent owns `dataset-breaking-change-alert.yml` and `tests/test_publish_dataset_workflow.py`; neither is in this scope.

## Diagnosis

With bundled Git on PATH, shared-runtime `uv run --no-sync python scripts/repo_guardrails.py check-static` reports exactly three errors: metadata-localization lacks a trusted-main checkout marker; publish-dataset lacks both that marker and the shell main-ref marker.

The rule at `scripts/repo_guardrails.py:493` searches workflow text for a shell main-ref check and literal `ref: main`/default-branch checkout. E deliberately uses `github.workflow_sha`, followed by the captured executor SHA, to prevent moving-main code from changing an approved run's executor.

Current publish capture checks its checkout SHA, `GITHUB_REF=refs/heads/main`, exact `GITHUB_WORKFLOW_REF`, repository, merge ancestry and review before producing its envelope. These runtime checks happen after the bootstrap checkout. Apply uses the capture outputs and exact artifact ID, verifies envelope hash and executor before GCP authentication, and repeats review validation before auth/mutation. Localization guards manual main refs before bootstrap; automatic runs validate exact upstream repository/run/workflow/artifact through `from-run`, then check out and verify the captured executor. Existing authorization and workflow tests own those protocol contracts.

Both workflows currently grant id-token/write at workflow scope; actual GCP auth occurs later in a protected production job. This bounded repair does not change permission/IAM policy or claim the static linter replaces branch/environment protection.

## Minimal design and ownership

Invariant for the new guardrail alternative: a qualifying immutable bootstrap must execute this repository's own workflow revision from main, established before checkout. Arbitrary expressions, PR refs, skipped checks and marker text must not qualify.

1. Add an unconditional first-step inline guard in publish-dataset. Replace localization's manual-only first guard with the same unconditional guard. Check both `GITHUB_REF == refs/heads/main` and `GITHUB_WORKFLOW_REF == ${GITHUB_REPOSITORY}/.github/workflows/<actual filename>@refs/heads/main`, with `set -euo pipefail` and an explicit failure. Keep all checkout refs and authorization behavior unchanged. The localization guard also covers workflow_run, whose trusted workflow is the main-branch consumer.
2. Add one generic, parsed predicate in repo_guardrails for a guarded immutable bootstrap. A qualifying job must begin with the expected unconditional inline guard and an unconditional `actions/checkout` with exact `${{ github.workflow_sha }}`. The guard must match the actual relative workflow path, rather than trust a supplied name. Neither step may ignore failure; checkout may not override repository/path. Check actual YAML fields, not comments, step names or substring presence. Use a small fixed shell contract rather than a shell/GitHub-expression interpreter.
3. The existing GCP dispatch rule may accept this predicate as an alternative to its old main-ref-plus-literal-main markers. Preserve the legacy literal-main and narrow feature-preview behavior unchanged. Do not add expressions to the general marker tuple and do not exempt workflow filenames.
4. Preserve separation of responsibilities: this static predicate proves the initial bootstrap, not the full later job graph. That is the same scope as the present trusted-main marker, but stronger for the new immutable alternative. E runtime validation and existing E workflow tests remain responsible for later executor provenance, artifact/hash wiring and verify-before-auth. Add one compact real-workflow test that enumerates **all** checkout refs/conditions in the two workflows, so an inserted PR/arbitrary checkout cannot be hidden behind the legitimate bootstrap. This does not duplicate the complete E protocol in guardrails.

No per-workflow production profiles. The originally considered approach would have duplicated capture/from-run/verify schemas, outputs, conditions and ordering (~130–200 production lines plus extensive tests). The generic predicate is expected to take ~40–60 production Python lines, ~15 net YAML lines, and ~90–130 test lines including executable shell checks and the small checkout-inventory regression. Exact count depends on fixtures; stop and simplify if it grows materially beyond this scope.

The limit is explicit: existing unrelated workflows that satisfy legacy markers are not newly audited for every subsequent checkout or arbitrary execution step. This patch must not claim a repository-wide workflow taint analysis. It prevents new expressions/PR refs from being accepted *as trusted bootstraps* and keeps E's complete checkout inventory covered separately.

## Exact files

- `.github/workflows/publish-dataset.yml`: first inline main/workflow-ref guard.
- `.github/workflows/metadata-localization.yml`: strengthen existing first guard to the unconditional bootstrap contract.
- `scripts/repo_guardrails.py`: one parsed immutable-bootstrap alternative at the existing dispatch boundary.
- `tests/test_repo_guardrails.py`: predicate refusal/acceptance, actual guard execution, and complete E checkout inventory.

The test inventory stays here to avoid concurrent edits to the other agent's workflow test file. No Python authorization implementation, plan/envelope/public schema, dependencies, mutator, settings or remote changes. Existing-data strategy: none required; runtime authority and artifact formats are unchanged.

## Tests and validation

- Real workflows pass check-static after adding guards. All existing literal-main/preview checks remain passing.
- New qualifying fixture passes; changing its first checkout to `github.sha`, PR head, input, arbitrary expression or unrelated output fails. A raw workflow_sha checkout without the parsed guard also fails. Marker text in names/comments/another step is insufficient. Wrong workflow path, delayed/conditional guard, conditional checkout, ignored failure, repository/path override fail.
- Execute the actual two YAML guard scripts as Bash: main/exact workflow-ref passes; branch, tag, wrong workflow filename, wrong repository in workflow ref, or non-main workflow-ref fail before checkout. Assert return codes, not only text.
- Compact real-workflow inventory asserts exactly the expected bootstrap/captured checkout refs and conditions, with no additional checkout. Existing workflow tests retain full artifact/verify-order checks; existing authorization CLI tests exercise exact HEAD/main/lineage and source provenance.
- Shared-runtime pytest: tests/test_repo_guardrails.py, tests/test_dataset_mutation_authorization.py, tests/test_publish_dataset_workflow.py, tests/test_metadata_localization_workflow.py. Ruff touched Python, check-static, bundled Git diff --check. Use existing UV environment/cache, no-sync, PYTHONDONTWRITEBYTECODE=1 and explicit publish-approval workdir. No installs/live GitHub execution.

Removal pass: remove the moving-main-only assumption by accepting a demonstrably guarded immutable equivalent. Retain legacy checks and E runtime authority; reject generic expression allowlists, dummy main checkouts and full duplicated authorization profiles. Completion requires the exact three errors resolved, passing refusal tests, unchanged immutable handoff and no unrelated edits. Stop for supervisor approval before implementation.
