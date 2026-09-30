> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# E immutable authorization — independent adversarial crossreview

**Conclusion: no new P1/P2 bypass found within the accepted contract.** The checked-in-plan/exact-head acceptance/trusted-executor/artifact handoff boundary holds in the reviewed source and the local adversarial cases below. This is a bounded source review, not permission to merge or deploy.

Read `plans/publish-approval.md` and `reviews/publish-approval-supervisor.md` first. Reviewed the authority module, canonical parser, `publish_workflow.py` integration, and both publication and metadata-localization workflows in `worktrees/publish-approval`. No source changes, Git mutations, dependency installs, network/provider calls, or live workflow executions were made.

## Independent challenges

External reproducer: `adversarial_cases.py` (historical artifact: `../evidence/publish-approval-crossreview/adversarial_cases.py`). Detailed results: `results.json` (historical artifact: `../evidence/publish-approval-crossreview/results.json`). It imports the repository's deterministic GitHub fixture and invokes the actual authority code; it does not substitute a new authorization implementation.

**17 cases passed: 15 refusals and 2 positive controls.**

- A replacement source generation with the document path, blob SHA, normalized digest, execution identity and archive digest all recomputed still fails against the immutable reviewed-head blob.
- Forged acceptance review IDs and the self-authored exception fail against current effective acceptance. Later revocation fails, including a timestamp whose ISO offset would misorder under lexical sorting. A different subsequent exact-head approval requires a fresh gate rather than silently changing the captured acceptance identity.
- A structurally valid `no_mutation` envelope cannot conceal the PR's checked-in mutation document.
- A previous attempt's envelope relabeled as the next successful attempt is refused; so is an inner attempt substitution. Wrong workflow ID/path, wrong immutable repository ID, and a cancelled upstream attempt are refused.
- An archive with a second aliased envelope member is refused despite a correct archive digest. Duplicate JSON keys are refused despite a correct archive digest.
- A post-capture PR-body replacement leaves the original source generation intact. Downstream validation preserves the captured executor without resolving moving main again.

The workflow wiring agrees with these boundaries: publication checks out the captured executor, downloads the exact artifact ID, verifies the separately captured envelope hash, and revalidates before authentication and mutation/deletion. Automatic localization derives its envelope from the exact successful upstream run/attempt, verifies repository/workflow/artifact provenance, then checks out and verifies that captured executor. It does not regain authority through current-body extraction or heuristic PR resolution. The publication helper consumes the extracted verified payload; it is not a second authorization issuer.

Independent affected-suite run: **45 passed, 53 subtests passed**:

```sh
UV_PROJECT_ENVIRONMENT=${REPO_ROOT}/.venv \
UV_CACHE_DIR=${REPO_ROOT}/.uv-cache \
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest -q -p no:cacheprovider \
  tests/test_dataset_mutation_authorization.py \
  tests/test_reviewed_dataset_plan.py \
  tests/test_publish_dataset_workflow.py \
  tests/test_metadata_localization_workflow.py
```

External reproducer command, with the same environment and the E worktree as working directory:

```sh
uv run --no-sync python ${REMEDIATION_WORKDIR}/evidence/publish-approval-crossreview/adversarial_cases.py
```

## Limits retained from acceptance

The workflow/runtime and GitHub API are trusted inputs. A locally fabricated artifact is not evidence that an attacker can upload that artifact into a successful trusted source run. Conversely, these local tests do not establish live branch/environment protection, authenticity of a live API response, or production artifact retention. Historical workflow rerun rollout controls, finite retention, the review-revocation/write race, manual localization inputs, and F/G replay/finalization/localization-concurrency ownership remain the documented prerequisites or separate work. They are not relabeled here as new E code bypasses.

## Reviewed source hashes

| File | SHA-256 |
|---|---|
| `scripts/dataset_mutation_authorization.py` | `6f6727b36e9e3dd124ee31f092d80f896293a3012066d8111c73625480fd6988` |
| `scripts/reviewed_dataset_plan.py` | `191f77e00cbe9826f203e3c72405b250ff3e88eecae7831949b589400d9d8690` |
| `scripts/publish_workflow.py` | `3ece896bbb4ce23ee7399e00e3c9edb668095f1fbe63715a82277a3bc38e0a08` |
| `.github/workflows/publish-dataset.yml` | `9b0ccd7bd2a3ccc590daec1acf3259b96763224578b36f6a32bb57efb98368d0` |
| `.github/workflows/metadata-localization.yml` | `20dd0a5df92aa0eae64bfc9b9100f400814d9c780f5947315747551a9c4958be` |

Retained only the external evidence directory and this review. B and D remain frozen and untouched.
