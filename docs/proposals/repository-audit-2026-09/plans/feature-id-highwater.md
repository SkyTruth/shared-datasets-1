> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# Package B: generated feature-ID high-water preservation — plan for Gate 1

Status: PLAN ONLY. No tracked file edits, dependency changes, Git mutations, or remote calls/writes performed. Requires supervisor `PLAN APPROVED` before implementation. Base and worktree HEAD: `1bf095d861d921e2378203495bd9a0da0bdf650c`. Worktree: `${REMEDIATION_WORKDIR}/worktrees/feature-id-highwater`.

## 1. Verified baseline and reproducer

The defect remains at this base. Inspected the actual allocation and ingestion producer chain:

- `scripts/release_feature_model.py:314`: `assign_generated_feature_ids` calculates `max(previous live IDs and overrides) + 1`; there is no sequence-state input or result.
- `ingestion/common/feature_metadata.py:384`: `write_generated_id_release` calls that allocator, then calculates the returned next-ID from **current output** max. It rejects empty current releases.
- `ingestion/common/gcs.py:70`: `load_latest_metadata_records` fetches only mutable `latest/*.metadata.ndjson.gz`, without generation pinning, and never loads a manifest.
- `ingestion/wdpa_monthly/run.py:747,874,1107`: the GCS loader/legacy adapter and both WDPA builders carry records only. The publish manifest writes the recomputed value; `previous_release` is left null.
- `ingestion/sea_ice_daily/run.py:518,794`: same records-only flow. Its hash exclusions and geometry-only ambiguity matching must remain unchanged.
- `scripts/publish_release.py:796`: records-only loader exists here too, but it is an ambiguity-report reader, **not an allocator**. Its generated-ID publication validation requires coordination with package F, not an unsolicited rewrite of reporting.
- E-AMLIS publishes source-field IDs through `enrich_features_with_source_field_ids`; it must keep its source-ID behavior.

Executed a synthetic four-release fixture through the **actual streaming writer**, with module paths verified to point into this worktree:

| Release | Live identities and IDs | Reported next-ID |
| --- | --- | --- |
| R1 | A=1, B=2 | 3 |
| R2 | A=1 | 2 — regression |
| R3 | A=1, C=2 | 3 — unrelated C reused B's ID |
| R4 | A=1, B=3, C=2 | 4 |

`build_identity_metadata` also accepted `None`, `True`, `0`, `-1`, `"3"`, and `1.5` as `next_generated_feature_id_after_release`. A fake-bucket execution of the real `GcsPublisher` loader requested only `asset/latest/test-asset.metadata.ndjson.gz`; no manifest was read. Empty writer input raised `RuntimeError: test-asset metadata sidecar would be empty`.

Retained evidence and synthetic artifacts: `${REMEDIATION_WORKDIR}/evidence/feature-id-highwater/` (`baseline.json`, `gcs-baseline.json`, R1–R4 enriched streams and sidecars). No claim about production ID reuse is made; no live GCS audit occurred. “GCS baseline inspection” here means inspecting and exercising the real loader against a fake bucket, not reading production data.

## 2. Invariant, ownership, and boundaries

Invariant: for a verified baseline with next-ID N, every newly allocated generated ID is a canonical positive decimal at least N; the allocator returns M >= N and M exceeds every generated ID allocated in the result. Reuse/keep decisions may preserve an already-owned ID, but cannot lower M or introduce an arbitrary unallocated override. Deletion, no new identities, or an empty release never lower N. A returning key absent from the immediate baseline receives a fresh ID >= N; automatic historical identity resurrection is outside this change.

Impossible states: generated allocation with records but no verified sequence baseline; bool/string/float/nonpositive next-ID; next-ID <= any existing numeric generated ID; a consumed override that is not a previous allocation; result next-ID lower than input; treating a missing latest object as a new dataset despite existing historical objects; combining records and state from different release generations.

Owner: `release_feature_model` owns generated sequence validation and allocation. A validated baseline value holds identity records/mapping and the sequence state together. GCS is the persistence adapter; ingestion consumes the value and carries the allocation result through its manifests. Source-field identity and all existing hash algorithms/canonicalization remain unchanged.

Trust boundaries: local caller-provided baseline, persisted manifest/sidecar bytes, remote object generations, reviewed identity decisions. Failure classification: producer bug plus absent persisted-state boundary validation; legacy state may already be invalid and must not be silently repaired.

Scope of allocation: durable release allocation lineage, including partial published releases handled by package F. Repeatable local speculative builds do not reserve global IDs. This change alone cannot serialize two independent publishers; see dependency below.

## 3. Smallest complete producer change

1. Introduce a small immutable generated-baseline/allocation-result model in `release_feature_model.py`. The baseline includes previous identity records (or the existing projected mapping), verified next-ID, and previous release. Allocation returns both IDs and next-ID; do not reconstruct next-ID at the streaming writer. Require explicit genesis (no prior allocation, next-ID 1) rather than an optional implicit reset. Update all direct in-repo allocator callers/tests.
2. Validate sequence input once: `type(value) is int`, 1 <= value <= 10**64, next-ID strictly above prior generated numeric IDs. Generated allocated IDs use canonical ASCII `[1-9][0-9]{0,63}`. The sequence value 10**64 is the explicit exhausted sentinel after allocating the largest 64-digit ID: retained/reused IDs remain valid, but any request for a new ID fails before output. This avoids discarding the final usable ID or emitting an overlength ID. Generated reused overrides must refer to previous IDs and stay below the next-ID. Maintain existing key/decision legality and collision behavior. Remove the used-ID collision-skipping loop when the validated high-water invariant makes it unreachable. Do not change source-field ID validation.
3. Replace the writer's output-max counter with the allocator's resulting sequence. Projection may discard previous properties but cannot discard sequence state. Preserve two-pass bounded memory. Permit the generic writer to represent an empty generated release with a valid carried sequence; retain WDPA/sea-ice domain checks that reject unexpected empty upstream data. No native empty-vector publishing feature is being added. Explicit empty schema/write validation must remain coherent.
4. Change generated GCS baseline loading to read `latest/*.manifest.json` at an observed generation, validate its asset/release and generated sequence state, then read the **release** metadata artifact named in that manifest at its declared positive integer generation. Check the metadata path belongs to that asset/release and verify its SHA-256, asset/release, count, and mappings. Do not read independently changing latest metadata. A race/precondition mismatch fails with a narrow error, without defaulting to an empty baseline. Capture the latest manifest identity (path/generation/hash) for package F freshness enforcement. If there is no latest manifest, inspect the asset's existing relevant release/latest objects before allowing genesis; any previous data blocks implicit bootstrap.
5. Wire the baseline through WDPA and sea-ice build entry points and outputs. Both manifest builders consume the same allocation result, fill `previous_release`, and emit the new sequence guarantee. Replace WDPA's broad `RuntimeError` → legacy reinterpretation path on this allocation route; legacy content requires the explicit migration route described below, not an exception fallback.
6. Persist a small additive sequence-contract discriminator in the existing `identity` object (proposed `sequence_state_version: 1`) plus `next_generated_feature_id_before_release`, alongside the existing `next_generated_feature_id_after_release` and `previous_release`. New generated writers must emit valid before/after state with after >= before. There is no sidecar format or hash algorithm change and no second next-ID representation. The baseline's pinned manifest reference is exposed to publication code; package F decides the exact common publication-precondition field. The sequence marker distinguishes outputs built by the fixed invariant from historically regressing writers. It is a writer provenance contract, not cryptographic proof of arbitrary hand-authored manifests.
7. Keep existing generated manifests readable by archival validators where their old contract allows missing sequence state; a separate strict allocation-baseline parser refuses unversioned state. For archival identity-v1 generated manifests, missing/null sequence fields remain readable as unknown state, valid numeric values are readable but unverified, and malformed supplied values (including bool) are rejected. The new generated writer requires the version discriminator plus valid before/after values and never emits missing/null state. Source-field writer/reader semantics are unchanged. No code path may “upgrade” an old manifest merely because its current next-ID exceeds its current live IDs.

Simpler alternatives fail: changing only the allocator leaves GCS/ingestion unable to supply state; using max(current IDs) or max(latest manifest, current IDs) loses historical allocations; always trusting the old field accepts previously regressed marks; a large numeric offset has no historical proof; sidecar-only genesis resets deleted assets; a record ledger/tombstone expansion introduces a larger identity migration than needed to stop numeric reuse.

## 4. Existing-data strategy: one offline evidence-bound migration artifact

Legacy generated refreshes fail closed until a reviewed migration. A numeric next-ID in an old manifest is not allocation authority: the old writer could regress it while still exceeding all currently live IDs. The WDPA ext_id adapter preserves current key mappings only; it cannot establish historic high-water and will cease to be an automatic refresh fallback.

Add a bounded offline `scripts/feature_id_sequence_audit.py` tool with `audit` and `prepare-manifest` subcommands. No network, GCS mutation, bulk production audit, populated production seed, or runtime integer override is included. Its single review artifact is an evidence-bound sequence seed; it does not create an alternate allocation authority used indefinitely by jobs.

### Required offline evidence

The audit takes an explicit asset-scoped inventory and local evidence files, not a latest sidecar or a list silently assumed to be exhaustive. Every referenced object must have canonical URI, positive integer generation, SHA-256, byte size, and corresponding local bytes. It must bind a current migration anchor: exact latest manifest generation/hash and its exact release-sidecar generation/hash; WDPA legacy-only anchors bind the exact legacy sidecar generation and ext_id/SITE_PID mapping instead. No code guesses the anchor from filenames.

The inventory must include all allocation-bearing releases and historical generations through a pinned cutoff, plus partial/abandoned published allocation reservations. Evidence may consist of a complete original allocation journal, or a generation-specific canonical object inventory reconciled with create/replace/delete event evidence from asset genesis through the cutoff. Current object listing or release index alone is insufficient: neither proves that a historical generation or partial publication never existed. Every relevant event must be accounted for by its generation-specific artifact bytes or a durable allocation record proving the reserved interval. A partial FGB/PMTiles publication without a readable identity sidecar or independently verifiable allocation record is NOT READY; this tool will not guess its IDs. Package F supplies reservation/receipt evidence where available, and B will not define another receipt schema.

The tool verifies hashes, sizes, URI/asset/release/generation consistency, one-to-one inventory/event coverage, chronology, declared coverage interval including genesis/cutoff, and all referenced local files. It streams all supplied sidecars/legacy projections to compute an upper bound over **all historical allocations and reservations**, not only currently live IDs or old next-ID values. It also treats valid historic sequence values as a conservative bound. It detects the same ID moving between different keys across history; exact reviewed identity-decision evidence can explain a legitimate reassignment to the same logical feature. Unexplained reuse, missing referenced generations, inventory/event gaps, unsupported record formats, unresolvable partial reservations, or mismatched anchors produce NOT READY with exact missing evidence. They cannot be bypassed with `complete: true` or a user-supplied seed number.

Global historic completeness cannot be proven from arbitrary offline files. Therefore the tool never labels a candidate seed as a certified complete history or allocation-ready. Even a structurally complete candidate is explicitly `prepared_for_review`, reports the evidence it checked and limitations, and requires the protected canonical publication review described next. A claim of completeness in input text has no effect on this status.

### Human evidence and promotion gate

Before approving migration, the reviewer must independently establish that the evidence is authoritative and exhaustive: the asset's true first allocation date, complete coverage of every canonical write/delete and object generation through the bound cutoff, complete coverage of partial allocations, continuity of logging/journaling and retention over that whole interval, and a reconciliation of any earlier format migration or overwritten generations. Exact source export provenance and hashes must be recorded. If those facts cannot be established, the seed remains NOT READY for publication even if all supplied files parse; a separate explicit consumer/namespace remediation decision is needed. An untrusted assertion or review of only latest data is not evidence. No script can resolve that missing historical fact by choosing a large number.

The deterministic seed records the bound anchor, computed next-ID, evidence descriptors/digest, coverage assessment and limitations, and any reviewed identity-transition references. It contains no manually overrideable next-ID. `prepare-manifest` revalidates the seed against the audit input bytes and anchor bytes and prepares replacement **manifest bytes and exact generation-preconditioned promotion candidates** for review; it performs no writes. This is the one migration artifact, not a new canonical data format or a permanent bypass flag. The migration summary/digest is embedded once in the new manifest identity (`sequence_migration`); the prepared manifest gains versioned before/after state at the audited next-ID and preserves all existing IDs, hashes, artifact references, and schema. Normal generated releases carry ordinary sequence state and predecessor identity, not duplicate the migration inventory.

Those prepared bytes go through the existing explicit-PR, reviewed, approved publisher path (coordinated with F/E). The PR contains the seed/report and complete evidence references and exact old/new manifest generation expectations. Reviewer confirmation of evidence authenticity/completeness is a **deployment prerequisite**, not supplied by a seed status flag. After that canonical publication, the strict loader accepts the versioned manifest state and its exact release sidecar normally; it never consumes arbitrary candidate seed files or unverified integers at runtime. For legacy-only sidecars, preparing a coherent release-feature-model bundle also requires reviewed legacy conversion; the audit may prepare mappings and the seed but returns NOT READY for canonical manifest preparation until those required schema/hash/artifact inputs exist.

Already reused production IDs must be reported, not silently relabeled. Such conflicts block a normal sequence-only seed until a separate reviewed remediation decision explains how existing references will be handled. This patch makes no claim that historic IDs were correct.

Compatibility: source-field IDs and geometry/properties hashing remain byte-compatible. Internal generated helper signatures change with every in-repo caller updated. Additive sequence fields preserve the rest of the existing manifest/sidecar formats. Archival reading and allocation trust are separate contracts. Existing jobs will refuse unverified legacy baselines until a reviewed migration; local correctness acceptance is possible before that production deployment prerequisite is satisfied.

## 5. Intended files

Production code:

- `scripts/release_feature_model.py`: baseline/result types, strict allocation state, allocator output, generated manifest state validation/construction.
- `scripts/feature_id_sequence_audit.py` (new): offline evidence audit, one deterministic seed artifact, NOT READY diagnostics, and bound manifest preparation; no remote I/O or approval bypass.
- `ingestion/common/feature_metadata.py`: baseline projection/propagation, allocator adapter, empty generic release state, remove output maximum.
- `ingestion/common/gcs.py`: generation-pinned generated baseline loader and explicit genesis/refusal boundary; expose baseline snapshot reference for F.
- `ingestion/wdpa_monthly/run.py`: baseline through real builders and publisher metadata, retire unsafe automatic legacy fallback.
- `ingestion/sea_ice_daily/run.py`: same propagation, preserve content hash exclusions and source validation.

Tests/fixtures:

- `tests/test_release_feature_model.py`
- `tests/test_feature_id_sequence_audit.py` (new)
- `tests/test_release_streaming.py`
- `tests/release_streaming_helpers.py`
- `tests/test_identity_key_corroboration.py` (new explicit baseline argument; existing action semantics)
- `tests/test_ingestion_common.py` (generation-aware fake downloads and GCS baseline cases)
- `tests/test_wdpa_monthly.py`
- `tests/test_sea_ice_daily.py`
- Any additional existing test fixture that explicitly constructs generated metadata must supply the new valid writer state; constrain edits to that requirement.

Documentation after loading sync-docs-with-code (already read for planning):

- `docs/standards/asset-layout-and-formats.md`: authoritative sequence continuation, empty generic state and legacy migration prerequisite; fix the sequence example only where needed.
- `docs/feature-id-sequence-migration.md` (new focused operator guide): evidence needed, offline tool commands, interpretation of NOT READY/prepared-for-review, exact generation binding, and protected publication prerequisite. It must not describe human attestation alone as proof or suggest any direct remote write.
- `docs/consumer-guide.md`: precise previous-release/sequence contract and return-after-absence limitation.
- `ingestion/wdpa_monthly/README.md` and `ingestion/sea_ice_daily/README.md`: refusal of unverified legacy baseline before deployment.

No changes planned to source-field E-AMLIS logic, catalog asset metadata/derived catalog, identity-decision JSON, hash algorithm constants, Terraform, workflows, or remote objects. `vector_pipeline.py`/`publish_release.py` are package-F-owned unless a shared-interface edit is approved and coordinated.

## 6. Regression and adverse tests

Tests must assert behavior, not source text.

1. Four-plus releases through allocator AND streaming writer: A1/B2 -> A1 -> A1/C3 -> A1/C3/B4. R2 retains next3, R3 next4, R4 next5. Deleted IDs are never reassigned to another new key.
2. Deletion-only, no-new-feature, all-features-deleted, consecutive empty, and new-after-empty cases preserve high-water, including empty baseline with N far above 1. Generic writer empty output is valid and still carries N; job-specific empty-source rejection remains.
3. Reviewed reuse/keep decisions preserve the existing ID while retaining the higher sequence; explicit force-new allocates at the high-water. Override of an ID not in the validated previous allocation is refused. Conflicting decisions still fail before output.
4. Reject bool, null, nonpositive int, float, numeric string, malformed/unsupported state version, next-ID at/below existing numeric IDs, and after < before. Cover largest legal ID, transition to exhausted sentinel, no-new reuse while exhausted, and rejection of a further new allocation. Assert output files do not exist when validation fails.
5. Legacy records without sequence, legacy manifests with null/missing state, and an old manifest that advertises a numerically valid but historically regressed mark all refuse allocation. Archival legacy reads and source-field IDs remain supported. WDPA legacy sidecars must not silently bootstrap.
6. Real `GcsPublisher` fake-bucket sequence: deliberately make latest sidecar from a different release; loader uses the manifest-bound immutable release sidecar and its high-water. Reject wrong asset/release, wrong URI namespace, bad hash, missing artifact/generation, bool generation, generation replacement during read, missing manifest with existing history, and legacy state. No writes occur.
7. Mock native conversion only while running actual WDPA and sea-ice builder/writer/manifest code. Supply N=100 with live ID1; assert new IDs start at100 and final manifest after is101 with the correct previous release. This must fail at baseline independently of pure helper tests.
8. Deterministic retry of the same validated input produces the same IDs/state; a rejected snapshot never retries by resetting to genesis. Concurrent/stale activation test belongs to F: pass baseline manifest generation into publication and reject when it changed. B provides the pinned reference and a loader race test; no false standalone atomicity claim.
9. Offline audit fixtures cover complete synthetic history with deletion/new/return, exact approved reuse, regressed old manifests, overwritten generations, partial reservations, reordered deterministic input, missing event/object/bytes/generation, truncation/hash mismatch, mismatched anchor, unresolved ID reuse, logging coverage gaps, and latest-only input. A fabricated completeness flag never makes a result allocation-ready. Manifest preparation verifies exact seed/anchor binding and preserves IDs/hashes; missing legacy conversion evidence yields NOT READY. The tool performs no network calls or writes to input files.
10. Existing source-field, hash, corroboration, decision legality/collision, memory retention, and two-pass tests remain green. No blanket golden changes to hashes or IDs.

## 7. Dependencies and non-goals

Package F must bind generated publication to this exact baseline (including partial/reserved allocations). Otherwise two builders starting at the same N can build unrelated allocations with the same numbers, or an old/backfill publication can regress the current sequence despite correct local logic. Coordinate the baseline-reference interface before editing common publication code. The parent has been notified. This branch must be reported as producer/loader correctness only until F supplies publication freshness/serialization; per-object CAS alone does not provide global allocation safety.

Potential conflict: F also touches `ingestion/common/gcs.py`; coordinate or apply sequentially. No dependency on A's cleanup schema or E's approval-digest schema, and do not invent competing receipts. G may touch streaming sidecar helpers for transactional translation output; B changes generated writer sequence handling only.

Non-goals: production refresh/deployment/migration, new metadata backend, all-history identity resurrection, repairing already assigned historical IDs, broad schema-validator consolidation, C2 hash canonicalization, C3 nonfinite JSON, source-field semantics, cleanup/deletion, and speculative future serving contracts.

## 8. Validation and completion checklist

Use the existing runtime without syncing, from this worktree, with:

`UV_PROJECT_ENVIRONMENT=${REPO_ROOT}/.venv UV_CACHE_DIR=${REPO_ROOT}/.uv-cache uv run --no-sync ...`

Targeted commands after implementation:

- `python -m pytest tests/test_release_feature_model.py tests/test_release_streaming.py tests/test_identity_key_corroboration.py tests/test_ingestion_common.py tests/test_wdpa_monthly.py tests/test_sea_ice_daily.py tests/test_eamlis_monthly.py tests/test_feature_id_sequence_audit.py`
- `python -m pytest tests/test_publish_release.py tests/test_finalize_promoted_release_metadata.py tests/test_feature_metadata_index.py` for unchanged manifest consumers; expand only on failure/new shared-interface edits.
- `ruff check` on changed Python files using the existing runtime; run repo-required relevant formatting/checks.
- `python scripts/catalog_docs.py check` and `python scripts/check_identity_resolutions.py --offline`.
- Re-run an independent four-release script against final code and save results next to baseline evidence.
- Fallback Git binary `diff --check`, `diff --stat`, and `status --short` to prove focused working-tree edits and no index mutation.

Native GDAL/Tippecanoe/PMTiles integration is optional only where environment/tool availability permits; synthetic tests exercise the actual identity writer and mock geospatial conversion. State skipped native/live checks explicitly. No installs or shared-environment sync. No GCS/GitHub mutations.

Acceptance: reproducer fails before and passes after; no caller can drop sequence state; legacy cannot silently proceed; offline audit/seed preparation provides a bounded review path and never certifies incomplete history; empty and explicit-decision state is correct; source/hash semantics unchanged; baseline snapshot is available to F; every refusal path is exercised; docs say production migration is still required. Finish with `reviews/feature-id-highwater-handoff.md` containing files, commands/results, exact review instructions, and dependencies.

Removal pass: delete live-output max tracking; delete max(previous-live-IDs) allocator fallback; remove redundant used-ID skipping made impossible by verified N; remove automatic legacy RuntimeError reinterpretation on allocation; retain archival legacy readers only as explicit compatibility boundaries. Add no guessed/high-water fallback. Remaining uncertainty: actual historical production allocation correctness and completeness have not been audited, and cross-publisher serialization is package F's prerequisite.
