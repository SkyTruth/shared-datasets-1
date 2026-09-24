> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

**Final snapshot reconciliation by the primary reviewer:** PR #146 merged during this review as `e2a29ea0a5ecd7d7688158a67f25e88400d4224f`; the final checkout is clean main. Terraform queue and wrapper-detection findings in the initial snapshot are resolved in code by that merge. Final equivalent-tree verification: 738 Python tests passed, 4 skipped, 427 subtests passed; Ruff passed. The detailed observations below retain their original snapshot context. No implementation changes were made by the review agents.

# Contracts, identity, localization, catalog, and agent-workflow review

Read-only review started at clean commit `c69850b` on 2026-09-22. This is the contracts/localization portion of the broader repository review. Findings distinguish verified failures, code-path risks, and proposed improvements. The coordinating reviewer later observed an external checkout change to `aee6fd5` with unrelated workflow/guardrail edits; this reviewer made no repository source or Git edits. No production objects, infrastructure, or index state were changed. Tiny local reproduction fixtures were created under this review's temp directory and automatically removed; this report is retained.

## Coverage and existing strengths

Reviewed `release_feature_model.py`, the generated-identity path in `ingestion/common/feature_metadata.py`, the feature-metadata translation/localization scripts, production and preview index loaders, catalog generation/validation, admission checks, identity-resolution checks and the checked-in WDPA decisions, relevant tests, standards, templates, asset frontmatter, consumer guidance, and focused skills. The main review covers publishing, services/SDKs, ingestion operations, infrastructure, and the rest of GitHub Actions. No live GCS audit or source-provider access was performed here.

The architecture has valuable foundations: canonical asset docs generate the catalog and README views; sidecars avoid bloated PMTiles; canonical and translated metadata are deliberately separated; stale translations are bound to source-value hashes; gzip output is deterministic; identity decisions match specific release evidence; runtime legality/collision checks catch several identity mistakes before expensive builds; generated-identity processing retains identity information rather than all geometries; protected publishing separates planning from canonical writes. Keep these invariants while simplifying their implementation.

Local inventory: 24 asset docs, all active; 18 public, five private, one internal. `catalog_docs.py check` passes and reports description/source-confirmation warnings for six assets: e-AMLIS, GOGI, IHO seas, WDPA marine, WDPA terrestrial, and WRI DRC permits. Four assets lack declared feature identity/metadata contracts: ACLED Middle East aggregates, GOGI, IUCN mammals, and IUCN reptiles. Fourteen lack admission frontmatter; these may be grandfathered assets, so absence alone is not evidence of a policy violation. Sixteen lack structured `source_url`, although citations may contain their source link. Fifteen lack structured bounds.

`check_identity_resolutions.py --offline` passes all seven WDPA terrestrial decisions: one keep-key decision and six reuses. Their rationale, exact hashes, source keys, previous-key declarations, reviewer, and PR references are present. Remote factual correctness of those declarations was not checked.

## Ranked defects and contract gaps

### C1 — P1: generated IDs can be assigned to unrelated features after deletion

Evidence: [release_feature_model.py:335](../../../../scripts/release_feature_model.py#L335), [feature_metadata.py:470](../../../../ingestion/common/feature_metadata.py#L470), [feature_metadata.py:536](../../../../ingestion/common/feature_metadata.py#L536).

The allocator derives its next ID from the maximum ID in only the immediately preceding sidecar. When the feature with the highest historical ID disappears, the next release loses that high-water mark. The manifest emits `next_generated_feature_id_after_release`, but this value is not consumed by the allocator; its writer also computes it from the current release's maximum.

Verified pure-Python reproduction: R1 has A=1, B=2; R2 contains only A=1; R3 contains A and new C. R3 assigns C=2. A historical client reference to B can now refer to C. This contradicts monotonic IDs and the documented logical identity continuity.

Fix at the producer: make a validated persisted high-water mark part of the identity baseline, require every refresh to consume it, and carry it forward even if the highest feature vanishes. A small identity ledger/tombstone policy also makes return-after-absence behavior explicit. Add three-release deletion/reappearance tests rather than only adjacent-release reuse tests. Before changing existing IDs, audit whether reuse has already happened; no existing production corruption is asserted by this review.

### C2 — P2: ordinary numeric serialization changes geometry identity

Evidence: [release_feature_model.py:182](../../../../scripts/release_feature_model.py#L182), [consumer-guide.md:98](../../../consumer-guide.md#L98).

`normalize_number` leaves integer `1` as an integer and float `1.0` as a float. Canonical JSON therefore differs for the exact same Point represented as `[1,1]` and `[1.0,1.0]`. This was reproduced locally: their `geometry_hash` values differ. Polygon ring start rotations also produce different hashes; clockwise/counterclockwise orientation and component ordering are not normalized. Documentation calls this a geometry-equivalence key, which promises more than the implementation provides.

Normalize integral floats consistently as a minimum. Decide whether the contract is serialization-equivalent geometry or normalized geometric equivalence; document the narrower guarantee or introduce a versioned topology-normalization rule. Because hashes participate in identity assignment, an algorithm change needs a deliberate baseline migration and tests; silently changing the existing hash version could destabilize every asset.

### C3 — P2: non-finite property values pass validation and produce invalid browser JSON

Evidence: [release_feature_model.py:172](../../../../scripts/release_feature_model.py#L172), [release_feature_model.py:896](../../../../scripts/release_feature_model.py#L896).

`canonical_json` uses Python's default `allow_nan=True`; `json.loads` also accepts those non-standard constants. `validate_sidecar_records` checks shape, hashes, IDs, and size but not finite numbers. A valid-shape record with `properties.v = float('nan')` was accepted with `valid=True`, and the serialized record contained literal `NaN`. JavaScript's `JSON.parse` rejects it, so a release can pass the Python boundary and break downstream browser metadata reads. There are no `isfinite`/NaN-normalization checks in this path.

Reject non-finite numbers before hashing and publishing using strict JSON serialization/parser behavior, or normalize them explicitly at source ingestion where missing-value semantics are known. This belongs at the producer boundary, not as repair logic in each SDK. Include a cross-language JSON conformance fixture with NaN, infinities, and nested values.

### C4 — P2: localization can erase its canonical input and report success

Evidence: [feature_metadata_localization.py:314](../../../../scripts/feature_metadata_localization.py#L314), [release_feature_model.py:969](../../../../scripts/release_feature_model.py#L969).

There is no input/output alias check. Passing the canonical sidecar as `output_sidecar` opens that same file for truncation before the lazy input iterator reads it. In a temporary fixture this replaced a valid one-row canonical sidecar with an empty gzip and returned `valid=True`, `feature_count=0`, and one orphan translation. The row-count comparison checks the output against a counter incremented while reading the already-truncated input, so both sides are zero.

Reject resolved-path/inode aliasing between input and output. Write a temporary output, validate it against a validated positive input count, and rename only on success. This also prevents failed validation or `--fail-on-stale` from leaving a plausible final output path behind. Preserve original bytes on all failure paths.

### C5 — P2: a transient translation failure becomes a permanent successful fallback

Evidence: [feature_metadata_machine_translate.py:473](../../../../scripts/feature_metadata_machine_translate.py#L473), [feature_metadata_machine_translate.py:513](../../../../scripts/feature_metadata_machine_translate.py#L513), [feature_metadata_machine_translate.py:559](../../../../scripts/feature_metadata_machine_translate.py#L559).

The default error mode is `source`. A failed provider call creates a current-source-hash CSV row containing the original source value and review state `source_provided`; the report says `valid=True`. The next run skips all existing current keys without consulting failure provenance. A local fake translator reproduced this: first run produced one failure and one saved fallback row; the second run with a working provider scheduled zero tasks and never called it. `--refresh-current` retries everything, including successful/human work, rather than selectively retrying failures.

Separate translation status from provenance: failed/pending rows must remain retryable; genuine source-provided values should remain successful source-provided rows. Make canonical fallback a materialization behavior, not a fabricated successful translation. Add `retry-failed`, explicit completion/coverage metrics, and a durable success cache so outages and restarts do not waste previous work.

### C6 — P2: workbook import can silently attach translations to the wrong features

Evidence: [feature_metadata_document_translate.py:456](../../../../scripts/feature_metadata_document_translate.py#L456), [test_feature_metadata_document_translate.py:77](../../../../tests/test_feature_metadata_document_translate.py#L77).

Returned workbooks are paired to shards by caller argument order, and rows to source values by row position. The importer only increments a count when hash columns disagree; it still assigns translated text to the expected positional hash and returns a valid report. Extra rows are ignored as long as there are not too few. A two-row workbook with intact but reversed hash/text pairs was accepted and mapped Alpha to Beta's translation and Beta to Alpha's translation.

The test explicitly preserves this positional behavior because translation software may alter hash text. Keep any needed positional recovery as an explicit reviewed mode, not the default for detectable intact-hash mismatches. Match valid hashes directly, require exact row/shard cardinality, record an immutable shard identifier, and stop when a returned valid hash belongs to a different expected source. For hash-damaged workbooks, require a visible positional-review checkpoint and a sample report before import.

### C7 — P1/P2 depending on concurrency: an older localization run can overwrite newer derived metadata

Evidence: [metadata-localization.yml:52](../../../../.github/workflows/metadata-localization.yml#L52), [feature_metadata_translation_pipeline.py:171](../../../../scripts/feature_metadata_translation_pipeline.py#L171), [feature_metadata_translation_pipeline.py:109](../../../../scripts/feature_metadata_translation_pipeline.py#L109).

The concurrency group contains the workflow run ID, so different materialization runs do not serialize. A run downloads its source generations, generates sidecars, then reads whatever destination generation exists immediately before upload and uses that as its precondition. Thus slow run A may read source generation 1, run B may publish generation 2's localized result, and A may then read B's destination generation and successfully replace it with source-1 output. The precondition prevents simultaneous writes after the final reload, but does not establish freshness of the inputs. Downloaded canonical metadata/schema/translation objects also lack one common snapshot binding, especially under `latest/`.

Use a concrete release/source-generation bundle and capture destination expectations before derivation; check the bundle is still current before activation. Serialize by asset/release or publish operation and coordinate with the publisher, not by run ID. A generation-addressed derived-result manifest plus one compare-and-swap activation record would make old results harmless. This is a verified code-path race, not a claim that it has occurred in production.

### C8 — P2: removing a locale from the translation source does not remove its served result

Evidence: [feature_metadata_localization.py:348](../../../../scripts/feature_metadata_localization.py#L348), [feature_metadata_translation_pipeline.py:191](../../../../scripts/feature_metadata_translation_pipeline.py#L191).

Materialization selects only locales present in the current CSV and uploads only those outputs. If all Spanish rows are removed, the old Spanish sidecar remains in the bucket and in the object-derived release listing; clients can continue receiving translations the maintainer intended to withdraw. A header-only CSV fails with “does not contain any locales,” so it cannot express clearing all translations.

Represent the approved desired locale set independently from existing nonblank CSV rows. Reconcile the active release's files against that set, with explicit reviewed deletion plans where deletion is necessary, or publish a canonical-fallback view and stop advertising stale locale outputs. Do not silently delete historical releases.

### C9 — P2: the authoritative templates and standards prescribe unsupported formats

Evidence: [dataset_README.template.md:104](../../../../templates/dataset_README.template.md#L104), [dataset_README.minimal.template.md:104](../../../../templates/dataset_README.minimal.template.md#L104), [catalog_docs.py:170](../../../../scripts/catalog_docs.py#L170), [asset-layout-and-formats.md:423](../../../standards/asset-layout-and-formats.md#L423).

Both README templates use `metadata_translation_source` and `metadata_localized` file roles, which the catalog validator does not accept. The standards repeat those roles. The same standards document includes the current CSV header at line 212 and an incompatible old header at line 423 (`source_value`, `translated_value`, no `value`). The localization parser requires `value` and rejects those extra columns. An agent following the templates/standard literally will fail the actual tool boundary.

Generate examples and templates from the same role/CSV schema definitions used by validators, and execute filled-in template/examples in CI. Delete the duplicate obsolete specification rather than documenting an alias. Existing catalogs can pass `catalog_docs check` while the onboarding templates remain unusable, so that check alone is insufficient.

### C10 — P2 policy gap: admission checks do not enforce new-asset admission, and jobs are not bound to evidence

Evidence: [admission_check.py:248](../../../../scripts/admission_check.py#L248), [test_admission_check.py:72](../../../../tests/test_admission_check.py#L72).

The check records added asset docs but never validates their admission block unless a new ingestion job also appears. Tests explicitly require new asset docs without admission, numeric size, or a large-data exception to pass. When new jobs do appear, one complete changed asset doc satisfies any number of jobs; the relationship between job and asset is not verified. This differs from AGENTS' stated admission requirements. Other publishing paths may enforce additional requirements, so this is specifically a CI-policy gap rather than an assertion that every publication path is unguarded.

Make an explicit, machine-readable job-to-assets mapping and validate admission for each genuinely new canonical asset and each new job's outputs. Preserve documented grandfathering for pre-existing assets. If CI is intentionally not the owner of this policy, update the guide and route every entry path through the actual enforced owner.

### C11 — P2/P3: independently maintained contract validators disagree

Evidence: [catalog_docs.py:595](../../../../scripts/catalog_docs.py#L595), [release_feature_model.py:1206](../../../../scripts/release_feature_model.py#L1206), [release_feature_model.py:1288](../../../../scripts/release_feature_model.py#L1288).

A reproduced example: catalog metadata validation accepts `strategy=source_field, source_fields=[a,b]`, while the release identity validator rejects it because exactly one field is required. Canonical format sets, locale regexes, ID rules, file roles, identity strategy rules, and partial schema definitions are maintained in several places. `require_generations` checks only `isinstance(value, int)`, which admits booleans and nonpositive integers; generated identity metadata does not validate its declared sequence state. These weak boundaries invite compensating checks in consumers.

Use a small dependency-light contracts module or generated JSON Schema as the owner of persisted formats. Have the catalog adapters, release builders, CLI validators, and SDK fixtures share it. Derive separate discovery/projection views explicitly rather than duplicating the meaning of an identity. Add contract examples that all implementations must accept or reject together.

## Simplification, performance, and product opportunities

### O1 — Make translation work resumable and bounded

[Machine translation](../../../../scripts/feature_metadata_machine_translate.py#L496) and [document export](../../../../scripts/feature_metadata_document_translate.py#L230) load the whole canonical sidecar into a list; tasks multiply by features × fields × locales. Machine translation creates every Future up front at line 435 and persists the final CSV only after all requests finish. A cancellation can lose all completed provider work. The “large-workload” document path still retains all feature records and tasks before producing shards. Multi-locale materialization reparses the entire translation CSV for every locale and retains every stale/orphan detail in the report.

Use an incremental SQLite/task-ledger cache keyed by source hash, target locale, translator version, and approved field context; queue a bounded number of requests; persist successful results atomically; stream canonical rows and CSV output. Parse translations once, or use indexed lookups per locale. Bound sample error details while retaining totals. This enables reliable LLM handoff/resume and removes much of the manual large-versus-small workflow branching. Preserve the existing direct-run estimate and human-approved fields/locales.

### O2 — One release-bundle validator should replace the dormant index-loader-as-validator pattern

[feature_metadata_index.py:176](../../../../scripts/feature_metadata_index.py#L176) is documented as the local dry-run validation tool, yet it primarily validates shapes, generation labels, and projected field names. It does not compare supplied sidecar/schema bytes to manifest SHA-256 values, compare the embedded manifest schema to the separately supplied schema, or enforce property values against schema types/nullability. It reads/decompresses the sidecar three times. The [preview loader](../../../../scripts/feature_preview_index.py#L210) only checks schema/manifest files exist and never reads them; it retains a different record validator and materializes the full sidecar.

Because Firestore serving is explicitly inactive, treat these as dormant-design debt rather than an active serving incident. Extract a pure `validate_release_bundle` operation with explicit local/planned and published-generation modes, strong byte/count/schema relationships, and a bounded report. Index loaders and publishing should consume the same validated bundle if reactivated. Delete or quarantine redundant dormant loaders/workflow knobs if no near-term serving plan exists; retain documented public/persisted compatibility intentionally.

### O3 — Give data dictionaries a machine-readable owner

[ReleaseSchemaField](../../../../scripts/release_feature_model.py#L165) contains only name, type, nullable, and projectable. Field explanations, units, code-list meanings, display labels, citation/source links, and analytical cautions live separately in Markdown tables. Existing warnings include unknown units/code meanings; e.g. IHO `area`, e-AMLIS codes, and WRI permit fields. Consumers must rediscover those meanings, and schema drift cannot be checked automatically.

Extend the source schema with descriptions, units, enum/code lists, source-field provenance, and explicit translation eligibility where useful. Generate the README property table and catalog/SDK field help from it. Track unknown fields as explicit stewardship work rather than repeated prose placeholders. A schema diff should report added/removed/type-changed fields and invalidate generated docs before publication. Keep provider confirmation human-owned.

### O4 — Publish release changes and identity continuity as consumer-facing evidence

The existing identity-decision counters and PR references are a strong start. A compact release diff should show retained/new/retired/rekeyed feature counts, schema changes, changed bounds, translation coverage changes, steward, and update freshness. Downstream applications could show freshness/compatibility without downloading whole sidecars. Maintainers could review surprising identity churn before large conversion and publication, including the high-water regression in C1.

The four assets without declared sidecar/identity contracts should have an explicit supported-capabilities indicator or migration status. This is more useful than assuming every active vector asset supports click metadata. Do not infer remote absence from missing frontmatter; the initial step is an inventory against published release indexes.

### O5 — Convert duplicated narrative rules into an executable agent contract

The repo has substantial, useful routing guidance and scoped skills. Its remaining failure modes occur where the same rule is independently described in AGENTS, skills, templates, standards, catalogs, scripts, and tests (C9–C11). Keep safety rationale and semantic decisions in prose, but expose a single deterministic command that prints current task state, validated inputs, missing decisions, expected output artifacts, and permitted next steps as JSON and concise text. The publishing concierge may already own portions of this; extend that workflow rather than creating another orchestrator.

Useful durable checkpoint fields: input content hashes and GCS generations; source/release/identity baseline; approved locales and fields; exact toolchain; step completion and output checksums; validation results; reviewed plan/PR link; promotion and derived-materialization status. Explicit enums such as `pending`, `validated`, `partial`, `complete`, and `failed` are more useful than unconditional `valid: true` for partial translation output. Ensure a task can restart from the checkpoint without relying on chat history or an agent remembering which fallback was intentional.

## Verification and limitations

Existing related work: the coordinating reviewer identified open [PR #145, “Spec: release-coupled translation maintenance and debt surfacing”](https://github.com/SkyTruth/shared-datasets-1/pull/145). Its specification proposes declared asset locales, per-release regeneration, reuse of unchanged source-hash-bound translations, manifest coverage with separate machine-placeholder counts, and one debt notice per release. This directly overlaps C5, C8, and parts of O1/O4/O5; use that proposal as the product/design discussion rather than opening a competing one. It is specification-only, so the implementation findings above remain. Publication of machine placeholders, reviewer ownership, routing, and thresholds are open decisions in that proposal; this report does not treat them as settled.

Commands used: `UV_CACHE_DIR=.uv-cache uv run --no-sync python scripts/catalog_docs.py check`, the same runtime with `scripts/check_identity_resolutions.py --offline`, source/line inspections, and small offline Python reproductions for C1–C6 and C11. The root review separately reports the full test/lint results. An initial `uv run --no-sync` without the repo cache variable was blocked by sandbox access to the user's UV cache; rerunning with the existing repo cache succeeded and no environment was changed.

All reproduced faults used local synthetic records, not downloaded data. C7 and C8 follow directly from control flow but were not exercised against GCS. No statement here confirms a production race, historical ID collision, malformed published NaN, or current broken remote object. Those require a read-only live audit with generation-pinned sampling and the GCS skill.

Invariant-first recommendation: make one validated release identity/bundle and one durable translation task state the source of truth; reject invalid source/output combinations before writes; make partial work explicit; generate docs from those contracts. No code was changed, no fallbacks were added, and no public or persisted compatibility path was removed during this review.
