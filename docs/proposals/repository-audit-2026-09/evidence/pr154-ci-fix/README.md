> Preserved planning/review record. See the [archive index](../../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# PR #154 native integration correction

The geospatial integration job in run 35947609536 failed at the only native WDPA fixture call: `build_asset_outputs` was missing the newly required keyword-only `baseline`. The other 93 geospatial tests passed; ordinary tests and lint passed. Current PR head was `2a961b58d34b95244d46fc27534483c7e18fd353`, which already incorporated main.

An isolated checkout at that exact remote commit is retained in `worktrees/feature-id-ci-fix`. Existing local branch checkouts remain unchanged. No history rewrite or forced push is used.

Fix: the synthetic ZIP is a first release, so explicitly pass `GeneratedIdentityBaseline.genesis()`. Extend the existing native test to check IDs 1–4, counter 1→5, and absent prior release/snapshot. Only `tests/test_wdpa_monthly.py` changes. Production still requires a supplied, validated baseline; no optional parameter, guessed history, or legacy fallback is added.

An independent AST/caller audit found no other omitted required baseline in ingestion/scripts/tests. Production WDPA and sea-ice pass their verified loaded baselines; the sea-ice native fixture and keyword helper wrappers already supply one.

Local focused validation: 105 passed and 44 subtests passed; one native WDPA test skipped because local GDAL is not runnable and Docker daemon is unavailable. Ruff and diff checks passed. Native validation must be confirmed in GitHub's pinned geospatial container after push.

No production migration, canonical GCS write, deployment, merge, or repository-settings mutation. The PR remains draft with migration and publication-ownership blockers. No repo-alert block is warranted for a fixture repair.

## Completion

Committed and pushed `362c2604944ab188fb27506b58b881f8059717f6` to the existing PR branch, without rewriting history. The isolated correction checkout is clean. GitHub CI run 35948059907 passed all 94 native geospatial integration tests and the ordinary suite; lint, catalog, local hygiene, geospatial-change detection, and protected-readiness checks passed. Alert/route checks intentionally skipped for the draft PR. Verified the exact pushed head remains open/draft/unmerged and has no merge conflicts.

Invariant preserved: generated builds require explicit allocation authority. This synthetic first-release fixture supplies genesis; no production default or fallback was introduced. No production code or internal error handling was removed. Remaining uncertainty is operational: existing-asset migration and publication ownership are still merge/deployment prerequisites.
