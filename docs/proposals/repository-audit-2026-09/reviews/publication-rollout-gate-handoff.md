> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# F3a publication rollout HOLD handoff

Complete for supervisor review. Worktree `worktrees/publication-rollout-gate`, branch `codex/audit-publication-rollout-gate`, base `1bf095d861d921e2378203495bd9a0da0bdf650c`. Approved narrow scope is in `plans/publication-rollout-gate.md`; no F3b/c implementation or dependency overlays.

## Behavior and scope

The three ingestion deploy workflows now run a fatal, unconditional stdlib gate immediately after main-ref validation and checkout. It blocks push and manual dispatch before GCP authentication, build/push, Terraform, canary and scheduler resume. The workflow filters include the gate and policy paths.

Policy v1 supports only HOLD. A valid policy exits 1 with PUBLICATION_ROLLOUT_HOLD; missing/malformed/unsupported policy or catalog registration exits 2 with PUBLICATION_ROLLOUT_INVALID. The registry must include exactly all three jobs and four owned assets, with roots matching unique catalog rows and canonical bucket paths. Duplicate JSON fields, unknown fields, bool/unsupported versions, future stages and incomplete/foreign registration refuse. The CLI has no policy-path, environment, allow or deployment override. It is runnable with Python isolated mode and site imports disabled.

No runtime managed-path guard, storage/receipt schema, adoption/seed writer, cloud probe, install_guard/active transition, Terraform resource, IAM or existing ingestion behavior changes. Existing deployments/schedules and historical workflow versions are not fenced by this patch. HOLD must remain until writer integration, adoption and old-writer quiescence are separately reviewed.

## Exact files and frozen snapshot

New: policy JSON, gate script, and one gate/policy/workflow test file. Modified: three deploy workflows, three ingestion README deployment sections, and deploy-scheduled-ingestion SKILL.md. No other source files changed.

Frozen patch: `reviews/publication-rollout-gate.patch` (27346 bytes), SHA-256 `cb35c45fae9f1f375176daca3549da32c7b9a04d08a9099650a30a5b28e5d364`. Exact per-file hashes are in `evidence/publication-rollout-gate-final-inputs.json`. F1/F2 frozen patch hashes remain unchanged; their worktree was not edited.

## Proof

- New workflow fence tests failed against baseline before implementation: `evidence/publication-rollout-gate-baseline.txt` (2 failures: no mandatory gate before cloud steps).
- Final command: existing uv environment, `uv run --no-sync python -m pytest -q tests/test_publication_rollout_gate.py tests/test_wdpa_monthly_deploy_workflow.py tests/test_sea_ice_daily_deploy_workflow.py tests/test_eamlis_monthly_deploy_workflow.py tests/test_repo_guardrails.py` — **40 passed, 204 subtests**, no skips. Log: `evidence/publication-rollout-gate-tests.txt`.
- Tests parse actual workflow jobs/steps, execute their actual main-ref/gate shell bodies locally, and apply Actions implicit success-condition behavior after the real nonzero exit. Every push/dispatch/cancel/resume combination reaches only main-ref, checkout and HOLD. They reject altered fixtures with conditional/ignored gate, early auth, downstream always/failure conditions, alternate job, or --help bypass. No auth/deploy/cloud command was executed.
- Policy adversarial tests cover every job and asset, missing/duplicate catalog entries and columns, wrong bucket/path/taxonomy, missing files, malformed/duplicate/nonfinite JSON, unsupported stages, overrides and unknown CLI jobs. Stdlib-only execution is checked from another cwd with `python -I -S`.
- Ruff on new script/test, catalog docs check (24 current docs; existing source-description placeholder warnings), and `git diff --check`: pass. Full ingestion/native tests are unnecessary for this workflow-only fence; runtime/native code is untouched.

## Removal pass and compatibility

There is no permissive branch to remove or legacy bypass to preserve in this new gate. Existing downstream workflow actions remain blocked rather than copied or rewritten. Their protected environment, concurrency and Terraform allowlists stay intact. Docs no longer imply that a merge currently reaches image deployment or canary. No runtime safety/atomicity claim follows from HOLD.

No Git index/history mutation, install, remote inspection/write, settings change, workflow dispatch, deployment, commit, push, PR or merge occurred. Retained artifacts stay in this named remediation directory. Review the frozen patch and rerun the five-file test command, then accept/revise before implementation scope widens. F3b/c revisions remain plan-only.
