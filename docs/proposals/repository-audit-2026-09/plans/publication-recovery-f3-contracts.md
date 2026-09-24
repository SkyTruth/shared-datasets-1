> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# F3b/c contract revision — PLAN ONLY

F3a is source-accepted and frozen (`reviews/publication-rollout-gate-supervisor.md`). Its policy still accepts HOLD only. No adapter, seed, authority schema, request mechanism or enabling transition below is implemented or approved. This revision replaces the deferred adoption-evidence and pre-fingerprint request sections of `publication-recovery-f3.md`; other writer/validator limits still apply.

## A. Define the adoption producer before its consumers

Recommend one bounded new **operational evidence** payload, reusing the approved `publications/inputs/{key}/0.catalog.json` path. This is not a dataset manifest format and contains no feature rows. The existing F2 adoption receipt remains the sole adoption authority record; no parallel receipt schema is invented.

The preparer, `scripts/publication_adoption.py prepare`, is read-only against canonical data. It captures a coherent current manifest, dated manifest, schema, sidecar identity, current index payload and allocation-history evidence into canonical JSON. Missing/partial/contradictory history yields a blocked report, not a seed value guessed from current records. Stage those exact bytes in approved scratch with a source generation and SHA. No prepare command creates state or makes a legacy release managed.

Minimum evidence payload (strict keys/types, duplicate/nonfinite JSON refusal, maximum 8 MiB, no truncation):

```json
{
  "adoption_evidence_version": 1,
  "bucket": "skytruth-shared-datasets-1",
  "asset_root": "100-geographic-reference/130-protected-areas/wdpa-marine",
  "asset_slug": "wdpa-marine",
  "captured_at": "<fixed UTC>",
  "baseline": {
    "release": "YYYY-MM-DD",
    "release_manifest": {"path":"gs://.../releases/YYYY-MM-DD/A.manifest.json","generation":1,"sha256":"<64hex>"},
    "latest_manifest": {"path":"gs://.../latest/A.manifest.json","generation":2,"sha256":"<64hex>"},
    "manifest_payload": {"...":"existing release-feature manifest"},
    "schema_snapshot": {"path":"gs://.../releases/YYYY-MM-DD/A.schema.json","generation":3,"sha256":"<64hex>"},
    "canonical_sidecar_snapshot": {"path":"gs://.../releases/YYYY-MM-DD/A.metadata.ndjson.gz","generation":4,"sha256":"<64hex>"}
  },
  "legacy_index": {"snapshot":{"path":"gs://.../_catalog/releases/A.json","generation":5,"sha256":"<64hex>"},"payload":{"...":"existing public index"}},
  "identity": {"strategy":"generated_sequence_source_fields","reserved_next_feature_id":1000,"basis":{"kind":"reviewed_history","bounds":[{"snapshot":{"path":"gs://.../releases/.../A.metadata.ndjson.gz","generation":7,"sha256":"<64hex>"},"format":"metadata","next_feature_id":1000}],"review_notes":"<documented history coverage>"}},
  "inventory": [{"path":"gs://.../releases/...","generation":6,"sha256":"<64hex>"}],
  "unresolved": []
}
```

All snapshot fields use the B-compatible path/generation/SHA shape. The displayed placeholders stand for typed existing manifest/schema/index content, not arbitrary executable parameters. Source-field strategy requires null high-water and `basis: {kind: source_field}`. Generated strategy requires a positive non-bool integer within B's limit, exact assignment strategy, and the exact basis fields `kind`, `bounds`, `review_notes`. Each unique bound has only `snapshot`, `format` (manifest, metadata or fgb), and positive non-bool `next_feature_id`; it must reference the own-asset inventory. The seed verifier downloads that generation and recomputes the bound from its validated persisted sequence or exhaustive generated-ID scan. Review notes are nonempty documentary context, not executable authorization. Legacy intervals without valid persisted sequence require an explicit reviewed historical allocation inventory; the validator proves the seed is at least every audited bound and greater than all audited IDs. It does not infer completeness from only the latest records. Completeness of legacy allocation evidence is a human review prerequisite; unexplained history gaps/partial output in unresolved block the producer. A fabricated “validated” boolean is not accepted.

The index payload is frozen as **reviewed legacy listing facts**, not retroactively labeled managed commits. New managed rebuilds use that payload plus verified new committed receipts and never rescan loose blobs into successful releases. Snapshot payload hashes and all current-role references are cross-checked before seeding. Genesis uses baseline/index null, an empty canonical release/latest inventory, and high-water 1 for generated strategy; absence is checked again immediately before state creation under external writer quiescence.

### Authorization and sole seed writer

Do not bypass E with a local seed switch or a standalone dispatch. Propose an additive **plan_version 2, adoption-only document** in `reviewed_dataset_plan.py`; existing v1 publish/delete documents and fixtures retain exact semantics. V2 has the same finalization identifier and one `adoption` object with exact fields: asset_slug, proposal_id, asset_root, bucket, evidence source URI/generation/SHA, and expected_state_generation (must be 0). No combined publish/delete/adoption and no replacement adoption are allowed in v2. E's existing canonical document path/hash, head/merge blob checks, review verification, envelope and proposal/contract digest functions apply unchanged to this document type. Extend its capture outputs with has_adoption_plan and add one protected publish-workflow seed execution step, still pinned to the original executor SHA. This narrow E schema extension must be reviewed with F; no source has been copied or edited yet.

`publication_adoption.py seed --authorization-envelope ... --authorization-sha256 ...` is the only proposed producer. It first resolves E proposal key and any existing seed outputs before fresh preparation. It rechecks reviewed authority and pins/hash-validates the staged evidence and its referenced canonical snapshots. Require registered root, externally established quiescence and no existing competing state; neither an env flag nor a scalar attestation proves quiescence. Cloud readiness checking and protected rollout evidence will be explicit inputs to the later enabling workflow, not silently fabricated here.

Persist in this order, always create-only:

1. `{root}/publications/inputs/{E.proposal_key}/0.catalog.json`: **exact approved evidence bytes**, preserving their digest. Ownership metadata binds proposal, execution-contract hash and role; never rewrite it during retry.
2. `{root}/publications/receipts/{E.proposal_key}.json`: existing exact F2 adoption shape `{schema_version:1,kind:adoption,asset_slug,asset_root,bucket,baseline,reserved_next_feature_id,evidence_sha256}`. Baseline is the two manifest snapshots/release from evidence, or null. Tag with the same seed ownership context.
3. `{root}/publications/state.json`: exact F2 state with that adoption receipt, reserved high-water, baseline current and active null, using generation 0. Only this last create makes the asset managed.

Lost response recovery requires matching proposal/contract/role metadata and exact expected bytes at the deterministic evidence/receipt paths; equal unrelated content is not accepted. A competing seed can at most leave orphaned evidence/receipt; it cannot replace state. If state creation succeeded and another managed transaction later advanced it, a seed retry recognizes the same adoption receipt through validated descendant state and returns already-adopted without rewriting. A different adoption reference refuses. Source/evidence loss before completion blocks; no fallback to fresh generations. No seed cleanup/deletion is automatic.

### Authority reader boundary

Before any managed claim, F2's single state-reference verifier will additionally load the deterministic evidence path derived from adoption receipt key, verify its pinned read SHA against evidence_sha256, validate the evidence model, and require exact equality of seed asset/strategy/high-water/baseline facts. This is an explicit F3 strengthening of F2, whose frozen patch stays unchanged. `ingestion/common/publication_adoption.py` owns the pure evidence model; producer and verifier call it. Do not put a weaker copy in each adapter. Missing evidence is an invalid authority, not a legacy exception. Index rebuild reads the frozen legacy index from this same authority. Because F2 has never been adopted/deployed, no compatibility branch accepts earlier hash-only test seeds.

Tests before enabling: invalid history/high-water, false genesis, duplicate JSON, foreign roots, changed source generation, missing evidence, and every seed write/response-loss window; two seeds racing; retry after managed advancement; exact E same-proposal executor refusal. This defines producer/storage/readers together. Actual seed execution remains prohibited in this task.

## B. Request identity before upstream fingerprint exists

**Separate request identity from discovered source facts.** Upstream fingerprint and pipeline configuration belong in immutable intent/execution contract; they must not be needed to find the receipt on retry.

For scheduled ingestion, Context proposal_key hashes `{kind: ingestion_request_v1, writer_id, asset_slug, request_id}`. Request ID is the original Cloud Run execution name, available before source discovery and stable across task retries; task attempt, invocation timestamp and upstream fingerprint are excluded. For a protected manual request outside Cloud Run, require an explicit nonempty reviewed request ID rather than generating one at runtime. Executor contract additionally binds executor commit SHA and pipeline-configuration digest; a changed executor/config does not create another proposal key for that request.

At entry, before upstream HTTP/download or B baseline/build:

1. Resolve explicit request ID and Context, then load its receipt. If present, require exact executor/config, validate stored request identity and resume its immutable operations. Use its frozen requested date, selected source fingerprint and checkpoints; do not recalculate a date from today's clock.
2. Read asset active state. If it belongs to another request, refuse with its receipt locator before source discovery. Do not infer equivalence from same asset/date, source URL or checksum.
3. Only a genuinely new request with no conflicting active owner discovers source facts and B baseline inside first preparation. Freeze requested anchor date, actual selected source/release, fingerprint and configuration in typed intent parameters. Until preparation creates the receipt there are no claim/canonical writes, so an interrupted fetch may safely restart source discovery. Once receipt exists, replacement source bytes never become a substitute.

A **new Cloud Run execution is a new request**, not a retry of an old transaction. If an operator wants cross-execution recovery, an explicit protected `PUBLICATION_RESUME_PROPOSAL` input selects the original receipt; it must match writer/asset and original pinned executor/config. It cannot supply new source/destination expectations or a new requested date. A mismatch refuses. No automatic “same date means resume” fallback. Repeated scheduler executions encountering an incomplete other request stop and report the existing receipt; they do not steal it. This conservative behavior is deliberate, and is recoverable through the explicit original-proposal entry point.

A completed request rerun with the same ID returns its recorded outcome. A new request discovering the same source emits the managed skip/run-only intent without new allocations; a different source at an already activated release date requires an explicit reviewed repair decision, and older activation remains refused. WDPA requests produce distinct per-asset keys from the same execution; one asset's completion does not authorize another's writes. Sea-ice lookback selection freezes the actual source date only on first preparation, so it is not confused with request/attempt date.

The same distinction applies to protected catalog refresh: original workflow run ID plus checkout is an explicit refresh request, attempts excluded; cross-run recovery must supply its original request locator. E reviewed mutation requests continue to use E's PR/head/merge/plan identity, never a workflow run ID. These are different trusted request producers feeding one F receipt model.

## C. Implementation ordering proposed for approval

First approve the operational evidence model, additive E adoption-only document and sole seed producer above. Implement their validators/producer + fault tests together before any runtime/index consumer requires evidence. Then add request-identity entry and actual bundle/derivation adapters; then remaining writer guards. Keep F3a HOLD in place throughout. Only a separately reviewed enabling rollout may extend the hold-only gate after these tests pass and external quiescence/adoption readiness is established. No enabling stage, cloud probe or live seed is silently included in F3a acceptance.

New/overlap files beyond the prior plan: `ingestion/common/publication_adoption.py`; F2 state-reference verifier/test fixtures get the explicitly reviewed evidence requirement; E reviewed_dataset_plan, authorization capture output, and publish workflow gain adoption-only v2 support with unchanged v1 fixtures. Request context lives in the existing ingestion vector_pipeline/reviewed workflow adapters, not a new receipt schema. All changes still require PLAN APPROVED. The unresolved approval is acceptance of this precise authority contract and E extension, not an unspecified future evidence format.


## D. Complexity/removal budget and reviewable packages

Use at most **five new production modules beyond accepted F2/F3a**: common/publication_adoption (pure evidence model), scripts/publication_adoption (prepare/seed CLI), common/publication_bundle (one domain closure/planning owner), common/publication_derivation (closed pure transformations), common/publication_policy (managed/legacy target routing). Retire the earlier proposal for a separate scripts/publication_adapter: use existing publish_workflow, publish_release and vector_pipeline as thin request adapters. No extra ledger, coordinator service, database, dependency, generic task framework, retry daemon, allocation model or approval verifier. Core publication.py retains receipt/state/claim ownership; E retains authorization identity; B retains allocation. If implementation needs another production module or schema, return for approval rather than hiding it in adapter glue.

| Independently reviewed package | Exact main ownership | Removal obligation |
|---|---|---|
| F3b1 adoption producer/authority | new common/publication_adoption.py and scripts/publication_adoption.py; core _verify_state_references hook; E adoption-only document normalization/capture-output addition and its one workflow seed step; their tests | Replace F2 test-only hash-without-evidence seeds. No per-adapter authority fallback or duplicate seed receipt. V1 E fixture behavior unchanged. |
| F3b2 bundle planner/validator and deriver | new common/publication_bundle.py and publication_derivation.py; extract pure existing finalize payload/merge helpers; new domain tests | One owner of role/ID/hash/schema/mode closure. Remove native/tool-missing permissiveness and any adapter “validated” boolean. Deterministic manifest/run/index/schema output logic is extracted/reused, never copied into two implementations. No workflow/runtime writer hook in this package. |
| F3c1 reviewed/local adapters | publish_workflow promote/notification execution, publish_release execution router, finalize_promoted_release_metadata managed refusal, dataset_alerts pure-vs-write split; publish workflow steps and tests | Managed command_promote loses its copy/stat loop and catalog-equality exception; managed schema updates leave the per-object loop. Remove managed standalone finalization/rebuild/upload-summary/delete-scratch workflow steps. F1 legacy execution loops remain explicitly unmanaged for compatibility, not duplicated for managed use. |
| F3c2 ingestion adapters | common/vector_pipeline and common/gcs publication methods, three run.py publication/skip branches, Docker COPY/request provenance, ingestion tests | Managed publish_vector_bundle loses its release/latest upload loops; skip/success paths lose independent record/index writes. Retire managed replace_latest_metadata_from_run_record patch, EAMLIS write_run_record_once direct upload, assert_no_partial_release heuristic and “any existing success” ownership shortcut. Keep source parsing/building and B allocator unchanged. |
| F3c3 other writer participation | common/publication_policy, common/release_index mutation/rebuild, gcs_asset mutation entry, feature_metadata_translation_pipeline remote entry, catalog_web_publish data payloads and relevant workflow steps, concierge retry presentation | Managed index rebuild stops synthesizing success from loose files; generic managed writes/deletes refuse. Managed localization stops fresh-generation uploads/index rebuild and cannot open a second same-proposal transaction. Managed concierge removes refreshed-expectation retry. Catalog global payloads use one fixed-expectation receipt; static presentation shell uploads stay separate. |

F3b1 and F3b2 can be assigned independently **after their exact producer contracts are approved**. F3c1/c2 are consumers of those interfaces; common/gcs ownership is limited to F methods while B loader changes remain frozen. F3c3 receives one target classifier from publication_policy; it must not duplicate root/receipt checks across CLI wrappers. Only one agent edits a shared workflow or helper at a time, with parent integration of accepted deltas. Each package must list deleted/retained loops and net production-code growth in its handoff; an adapter that reimplements claim/recovery/semantic closure fails review regardless of test count.

Legacy loops retained for unadopted formats are not claimed retired globally. Global presentation writers outside a managed transaction likewise do not provide global atomicity; all delayed effects *of that managed transaction* remain in its fixed operations. No package silently broadens the adopted set, weakens HOLD, adds speculative activation stages or takes over an incomplete claim. Adoption execution and deployment remain prohibited during this coding assignment.


## E. Decision and remaining acceptance — implementation not approved

**F3b/c is not approved for implementation in this pass.** F3b1, F3b2, F3c1, F3c2 and F3c3 above are separately planned future packages. Their module/removal boundaries and before-upstream request identity are design directions only. F2 is accepted as core-only and remains held from merge/deployment; accepted F3a remains HOLD-only. This decision supersedes the earlier callable seed-step and same-date repair sketches.

The seed producer must remain unreachable through both CLI and protected workflow until a separately reviewed, concrete rollout/readiness contract demonstrates that old writers cannot race adoption. That contract must account for already-running Cloud Run executions, future Scheduler starts, old deployed images, in-flight/queued or historical GitHub workflow executions, manual canonical writer paths, and the interval between readiness observation and state creation. It must identify enforced controls, their authoritative evidence, who establishes them, how their validity persists through adoption, and the refusal/recovery behavior if that protection changes. A scalar attestation, environment flag, paused-scheduler observation alone, or empty-running-jobs snapshot is insufficient. The present plan does not provide that contract, so no seed step, seed execution, migration or rollout transition is authorized.

V1 refuses a changed source for an already activated date. There is no unspecified same-date repair mode that can introduce changed dataset bytes or new IDs. Existing byte-identical restoration and metadata-only limits do not authorize that case.

Each future package returns to CHARTER Gate 1: submit its exact producer/consumer contract, source ownership, removals and tests; receive an explicit supervisor message beginning **PLAN APPROVED** naming that package before any source edits, overlays or implementation worktree setup. Approval of a data model does not authorize a callable seed path or activation. Acceptance of F3b1 must include the readiness contract above before its seed producer can become reachable; F3c consumers cannot assume missing adoption authority.

Required shared fixture gates before dependent packages are accepted: E v1 authorization bytes/digests remain unchanged and adoption-only authorization rejects altered source/executor; B snapshots/ranges agree with persisted adoption/claim high-water; seed evidence→receipt→state crash/race cases include an old writer attempting each transition and invalidated readiness; all actual writer entry points reject absent/incomplete/foreign adoption authority; before-source request replay uses the original receipt and checkpoints while incompatible configuration/executor and same-date changed source refuse; G localization uses captured source/output identities and cannot bypass managed ownership; F3a workflow HOLD remains effective throughout. These tests must exercise combined producer/consumer fixtures, not substitute per-package permissive callbacks.

No F3b/c source, overlay, worktree, migration, remote operation or execution was performed. The follow-on plan is handed off with this unresolved acceptance condition; work stops here.
