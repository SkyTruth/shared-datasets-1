> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# PR 153 bootstrap guardrail repair — handoff

Complete and frozen for supervisor review. Worktree: `worktrees/publish-approval`; starting HEAD `1ef87d293f75370b04b13cf399f275414d9b898a`. [Approved reduced plan](../plans/publish-approval-ci-guardrails.md).

## Change

The guardrail now recognizes a pinned main-workflow checkout only when an unconditional canonical inline main/ref check runs first, followed by exact `github.workflow_sha` in the default repository/workspace. Job/step ignored failures, conditional steps, repository/path overrides and inherited shell overrides cannot qualify. Both publication and localization now perform that guard before checkout.

Six files touched by this assignment:
- `.github/workflows/publish-dataset.yml`: add first-step main/workflow-ref guard.
- `.github/workflows/metadata-localization.yml`: replace manual-only guard with that unconditional guard.
- `scripts/repo_guardrails.py`: 51-line diff recognizing the guarded immutable alternative; existing literal-main/preview handling remains.
- `tests/test_repo_guardrails.py`: valid/refused bootstrap cases, workflow/job shell-default rejection, execute both real inline guards across main/branch/tag/wrong-workflow/repository cases.
- `tests/test_publish_dataset_workflow.py`: add only `test_only_expected_executor_checkouts_are_present`; preserve the routing agent's additions.
- `tests/test_metadata_localization_workflow.py`: add complete expected checkout refs/conditions assertion.

The routing workflow and root's `.gitleaks.toml` were not edited by this assignment.

## Evidence and validation

Baseline log (historical artifact: `../evidence/publish-approval-ci-guardrails-baseline.txt`) records the exact three check-static failures. They no longer occur; final check-static prints `repo guardrails passed`.

Final test log (historical artifact: `../evidence/publish-approval-ci-guardrails-tests.txt`): **62 passed, 202 subtests passed**:

```
uv run --no-sync pytest tests/test_repo_guardrails.py tests/test_dataset_mutation_authorization.py tests/test_publish_dataset_workflow.py tests/test_metadata_localization_workflow.py -q -p no:cacheprovider
```

Also passed: Ruff on all four touched Python files; `scripts/repo_guardrails.py check-static`; bundled `git diff --check`. Commands used the existing shared UV environment/cache, PYTHONDONTWRITEBYTECODE=1, and bundled Git on PATH from the assigned worktree. No environment synchronization/install or live GitHub workflow execution.

## Boundary/removal pass

Invariant: only the repository's exact main-workflow revision can qualify as the new immutable bootstrap. Removed the moving-main-only assumption for that case. No general expression allowlist, path exemption, dummy checkout, fallback or duplicate E protocol was added. The generic predicate proves bootstrap only; existing runtime authorization and workflow tests remain responsible for captured executor/artifact/hash provenance. Complete checkout inventories catch extra PR/arbitrary checkout steps in E workflows. Legacy literal-main/preview handling is deliberately unchanged and is not claimed to provide general workflow taint analysis.

No Git index/history, remote, settings, deployment or dependency mutations. Ready for independent review; no further edits planned.
