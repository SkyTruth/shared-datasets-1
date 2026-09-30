> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# Package B handoff — source review ready; deployment NOT READY

Branch: `codex/audit-feature-id-highwater`.
Worktree: `${REMEDIATION_WORKDIR}/worktrees/feature-id-highwater`.
Base: `1bf095d861d921e2378203495bd9a0da0bdf650c`.

## Result and proof

The allocator now requires one validated baseline and returns IDs together with the next sequence value. The streaming writer, WDPA, and sea-ice builders carry that state into additive manifest metadata (`sequence_state_version: 1`, before/after next-ID, previous release). Deleted/empty live sets cannot lower the sequence. Reviewed reuse/keep preserves IDs without consuming or resetting the sequence; forced-new allocations use the high-water. Source-field IDs and hash algorithms/canonicalization are unchanged.

Independent failures were captured **before implementation** in `evidence/feature-id-highwater/regression-before.txt`: both the pure allocator and actual WDPA builder assigned C=2 after B=2 disappeared and regressed next-ID to 2. The independent final script `evidence/feature-id-highwater/reproduce_after.py` exercises the actual streaming writer over six releases: A1/B2 -> A1 -> A1/C3 -> A1/B4/C3 -> empty -> D5, with next-ID values 3,3,4,5,5,6. Results and module path are in `after/result.json`.

GCS loading observes an exact latest-manifest snapshot and reads the manifest's metadata using both `blob(..., generation=...)` and `if_generation_match`. It checks asset/release/path/hash/count and refuses legacy/malformed state. Tests prove a retained older metadata generation is read after replacement, an unavailable generation fails, and bool/string/float/nonpositive observed manifest generations are rejected.

The frozen baseline retains only ID/hash/key fields. Projection applies declared property exclusions at the loading boundary. Weakref/deepcopy-trap tests prove full properties are neither retained nor copied; the actual mocked-native sea-ice builder preserves the `ice_date` exclusion hash. No storage engine was introduced.

## Offline migration boundary

`feature_id_sequence_audit.py` has only offline `audit` and `prepare-manifest` operations. It validates normalized generation/hash evidence, aware timestamps normalized to UTC, strict JSON without duplicate keys, object shapes, full manifested artifact references, sidecar release/count, historical IDs, explicit reviewed reuse evidence, and partial reservations. FGB/PMTiles/schema writes have explicit exact sidecar mappings plus hash-bound build/provenance evidence; missing mappings remain NOT READY. It does not parse/authenticate raw GCS logs or decode geospatial artifacts.

A successful audit is **prepared_for_review**, never allocation authority or proof of globally complete history. Human review must independently establish authenticity, genesis-to-cutoff coverage, logging/retention continuity, old generations, and all partial allocations. The prepared envelope binds both old manifest generations/hashes and retains all existing IDs/artifact references. Jobs do not accept seed files or integer overrides. Legacy-only anchors still require a coherent reviewed converted bundle; insufficient history or unexplained reuse requires a separate remediation decision.

## Deployment blockers and F interface

1. Every existing generated-ID asset needs its own complete-history review and approved canonical migration. None was audited or migrated here; no production seeds were populated.
2. Package F must atomically claim or otherwise enforce ownership of the captured baseline **before exposing newly allocated IDs**. A read/current-generation preflight alone is insufficient. B does not implement a publication lock or mutation protocol.
3. Source acceptance is not permission to merge/deploy auto-triggering ingestion changes. No merge, deployment, dispatch, or remote mutation was performed.

Approved interface: frozen `GeneratedIdentitySnapshot(path: str, generation: int, sha256: str)`; SHA-256 is bare lowercase hex, path names the observed latest manifest. `dataclasses.asdict(snapshot)` gives those three fields. `GeneratedIdentityBaseline(records, next_feature_id, release, snapshot)` carries it once; generated `AssetOutputs` expose `identity_baseline_snapshot`, `previous_generated_feature_id`, `previous_release`, and existing `next_generated_feature_id`. Genesis has snapshot=None only after the adapter observes no existing latest/release allocation objects. `vector_pipeline.py` and `publish_release.py` were not edited.

## Validation

All commands ran from this worktree using the charter's existing uv runtime (`UV_PROJECT_ENVIRONMENT` and `UV_CACHE_DIR`, `uv run --no-sync`); no environment sync/install.

- Baseline regression: 2 expected failures (allocator and actual WDPA builder), saved before source edits.
- Final focused run: **221 passed, 3 native integration skips, 61 subtests** across release model, streaming, corroboration, GCS/common ingestion, WDPA, sea-ice, E-AMLIS, offline audit, publish-release, finalizer, and metadata-index tests. Log: `tests-final-focused.txt`.
- Final non-object manifest evidence case: **1 passed**, after the above run; rejects a hash-valid JSON array with documented NOT READY rather than AttributeError.
- Full-suite checkpoint: **777 passed, 4 native skips, 479 subtests, 1 unrelated failure**. `test_terraform_prod_apply::test_binary_resolver_accepts_private_executable` creates its fixture inside this `/private/tmp` worktree, which the unchanged Terraform safety resolver correctly rejects. The supervisor confirmed the unchanged test passed on root main. No guard/test was weakened. Log: `tests-full.txt`. Final audit hardening was subsequently covered by focused tests.
- Ruff on every changed Python file: passed.
- `catalog_docs.py check`: passed for 24 assets; six existing source-confirmation description warnings.
- `check_identity_resolutions.py --offline`: passed all 7 existing WDPA decisions.
- `git diff --check`: passed; `git diff --cached --stat`: empty. All changes are unstaged.
- Native GDAL/Tippecanoe/PMTiles tests and live GCS integration were not run. Actual builders were exercised with native conversion boundaries mocked.

## Files and review order

1. `scripts/release_feature_model.py`: compact baseline/snapshot/allocation types, sequence parser, strict new-writer versus archival-reader validation, exhaustion sentinel.
2. `ingestion/common/feature_metadata.py`, `ingestion/common/gcs.py`: writer consumption and generation-bound loading.
3. `ingestion/wdpa_monthly/run.py`, `ingestion/sea_ice_daily/run.py`: real producer propagation and F snapshot interface.
4. New `scripts/feature_id_sequence_audit.py`, `tests/test_feature_id_sequence_audit.py`, `docs/feature-id-sequence-migration.md`: candidate-only migration boundary; review completeness limitation and exact-generation binding carefully.
5. Existing tests changed: `tests/release_streaming_helpers.py`, `test_release_feature_model.py`, `test_release_streaming.py`, `test_identity_key_corroboration.py`, `test_ingestion_common.py`, `test_wdpa_monthly.py`, `test_sea_ice_daily.py`.
6. Docs: `docs/consumer-guide.md`, `docs/standards/asset-layout-and-formats.md`, and both generated ingestion READMEs.

To independently verify, rerun the six-release script with `python -c 'import runpy; runpy.run_path("${REMEDIATION_WORKDIR}/evidence/feature-id-highwater/reproduce_after.py")'` under the charter runtime from this worktree, then the focused pytest files listed above. Challenge malformed version/state, empty/exhausted sequences, old-generation reads, audit missing objects/offset timestamps/duplicate keys/release-count mismatches, and the explicit deployment blockers.

## Removal pass and retained uncertainty

Removed: max-live-ID initialization, writer output-max tracking, now-unreachable allocation collision-skipping loop, WDPA's broad RuntimeError legacy reinterpretation, and its now-unused automatic legacy adapter. The audit retains an explicit WDPA legacy projection only as evidence, not allocation authority. Compact identity projection has one model owner.

Retained boundary handling: duplicate normalized overrides and feature IDs remain explicit errors. The old generic sidecar records reader is retained as an existing internal compatibility surface but no generated producer calls it. No guessed seed, numeric offset, legacy numeric-trust, or empty-baseline fallback was added.

Remaining uncertainty: actual production historical correctness/completeness is unaudited; ID alias repair and publication ownership remain outside B. Remote paths changed: none. Retained local work: this worktree and `evidence/feature-id-highwater/` under the named remediation directory. No prohibited Git/index/history action, shared-environment mutation, external message, or remote write occurred.
