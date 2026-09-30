> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# E — publish approval handoff

**Frozen for supervisor review.** Worktree: `${REMEDIATION_WORKDIR}/worktrees/publish-approval`; branch `codex/audit-publish-approval`; base `1bf095d861d921e2378203495bd9a0da0bdf650c`. See [approved plan](../plans/publish-approval.md) and [charter](../CHARTER.md). No commit, staging, push, PR, merge, dependency/environment change, settings change, deployment or remote write occurred.

## Result

Canonical execution now uses one checked-in content-addressed plan, exact reviewed head/merge blobs, latest effective head-bound acceptance, an immutable executor SHA and a digest-checked cross-job envelope. Both merged events and restricted dispatch enforce acceptance. Self-authored merged `jonaraphael` PRs retain the explicit exception. The automatic localization path verifies exact source repository/workflow/run/artifact identity instead of looking up a PR/body heuristically. Ordinary merged PRs produce a typed no-mutation handoff; missing artifacts still refuse execution.

Concierge/local prepare commands write the immutable document and matching readable fences. Open legacy plans require a new commit/review; merged body-only plans require a new reviewed PR. Changed expectations cannot be supplied by editing a merged body.

## Exact changed/new files

Code:

- `scripts/reviewed_dataset_plan.py`
- `scripts/dataset_mutation_authorization.py` (new)
- `scripts/publish_workflow.py`
- `scripts/publishing_concierge.py`

Workflow/config/docs:

- `.github/workflows/publish-dataset.yml`
- `.github/workflows/metadata-localization.yml`
- `.github/workflows/dataset-breaking-change-alert.yml`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `.github/dataset-plans/README.md` (new)
- `AGENTS.md`
- `README.md`
- `scripts/README.md`
- `.claude/skills/publish-shared-dataset/SKILL.md`
- `.claude/skills/gcp-shared-datasets/SKILL.md`

Tests/fixture:

- `tests/test_reviewed_dataset_plan.py`
- `tests/test_dataset_mutation_authorization.py` (new)
- `tests/test_publish_workflow.py`
- `tests/test_publishing_concierge.py`
- `tests/test_publish_dataset_workflow.py`
- `tests/test_metadata_localization_workflow.py`
- `tests/fixtures/dataset-mutation-authorization-v1.json` (new; shared verbatim with F)

## Evidence and exact checks

All checks used the existing runtime with `UV_PROJECT_ENVIRONMENT=${REPO_ROOT}/.venv`, `UV_CACHE_DIR=${REPO_ROOT}/.uv-cache`, `PYTHONDONTWRITEBYTECODE=1`, and `uv run --no-sync`, from this worktree.

- Final targeted pytest command: `python -m pytest -q tests/test_publishing_concierge.py tests/test_reviewed_dataset_plan.py tests/test_dataset_mutation_authorization.py tests/test_publish_workflow.py tests/test_publish_dataset_workflow.py tests/test_metadata_localization_workflow.py tests/test_feature_metadata_translation_pipeline.py tests/test_repo_guardrails.py` — **174 passed, 140 subtests passed**.
- Supervisor-requested bounded concierge regression proves canonical file path/bytes/hash, unchanged rerender, tampered-file refusal and changed-evidence refusal. Error/docs explicitly require a fresh concierge proposal or a locally prepared replacement payload for a new reviewed PR.
- `ruff check scripts tests` — passed.
- Bundled Git `diff --check` — passed; source status contains only the 21 files above, all unstaged.
- Broader `python -m pytest -q` earlier in this implementation: **744 passed, 468 subtests passed, 4 native-tool skips, 1 unrelated failure**. `test_terraform_prod_apply::test_binary_resolver_accepts_private_executable` creates its executable inside this `/private/tmp` worktree, while the unchanged resolver explicitly rejects `/private/tmp` ancestry. No Terraform/test alteration was made. Subsequent E changes passed the final targeted suite.
- Four refusal regressions run against copied exact-base source **fail as expected**: historical revoked approval, open dispatch, duplicate destinations, multiple fences. Baseline failure log (historical artifact: `../evidence/publish-approval-baseline-regressions.txt`), initial baseline facts (historical artifact: `../evidence/publish-approval-baseline-results.json`).
- Actual `prepare` CLI plus both CLIs' `--help` succeeded. Generated file bytes/digest/fence were independently compared: CLI evidence (historical artifact: `../evidence/publish-approval-cli/results.json`), rendered fence (historical artifact: `../evidence/publish-approval-cli/rendered-pr-plan.md`).
- Read-only GitHub evidence: existing merged run / PR (historical artifact: `../evidence/publish-approval-live-run.json`) confirms source-branch/head API metadata. A read-only exact-revision Git-tree query confirmed commit SHA lookup and ordinary blob mode. No workflows dispatched.

Authorization tests exercise paginated reviews and artifact listings; wrong repository/PR/head/merge/workflow/run/attempt; open/unmerged dispatch; stale/dismissed/revoked reviews; self-authored exception; altered head/merge bytes; malformed/duplicate/unknown JSON; conflicting targets; body edits before and between jobs; wrong executor and cross-job hash; missing/expired/extra/tampered artifacts; explicit no-mutation outcomes; dot proposal IDs and escaping producer symlinks; CLI capture/verify/prepare; and stable replay identity with unchanged destination expectations.

## F seam and limits

`proposal_key` hashes repository identity, PR number, head, merge and normalized plan digest. `execution_contract_sha256` adds executor SHA and `finalize-promoted-release-v1`. Run/attempt provenance is separate. Public helper seam: `validate_envelope`, `approval_identity`, `executor_contract`, `identity_digests`, `require_same_execution_contract` in `dataset_mutation_authorization.py`. F consumes only `outcome=mutation`. Its receipt lookup must precede replanning and refuse a changed executor contract for the same proposal. The shared fixture proves same-contract retry versus changed-executor refusal. E does not implement storage receipts, finalizer changes or refreshed CAS.

Combined E/F validation and F's adoption/rollout controls remain prerequisites to operational acceptance. Current per-object execution can still refuse partial replay. Distributed locale output races and manual localization behavior are outside E. GitHub protections, historic workflow reruns and artifact retention are rollout concerns: missing evidence has no unsafe fallback. There is a finite revocation race after the last acceptance check because GitHub review and GCS mutation are not atomic. Live deployment/IAM/workflow execution were not tested.

## Removal/fallback pass

Invariant enforced: reviewed bytes and executor identity survive every authority handoff. Boundary changed: strict canonical document/parser, effective GitHub acceptance, exact artifact provenance. Removed: any-historical-approval helper; moving-main mutation checkout; second-job body extraction; heuristic localization PR resolution; unused extract/detect/event/lookup CLI paths; catalog-retrieval fallback to a different revision; tests that encoded these unsafe paths. Retained local fence parsing only for `prepare --body`, rendering and gate display comparison. Added no stale/body/branch/CRC/approval fallback. Existing mutation/recovery internals remain F-owned. No new operation modes, receipt schema or source-format migration were introduced.

Review priority: inspect authorization capture/revalidation and no-op union, workflow checkout/artifact/auth order, serializer/concierge write behavior, then shared F fixture. Worktree is frozen pending supervisor feedback.
