> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# F — Publication recovery: proposed tactical and protocol phases

**F1 accepted for review and frozen; F2 core/layout source accepted and frozen; F3 PLAN ONLY.** Worktree `worktrees/publication-recovery`, branch `codex/audit-publication-recovery`, base `1bf095d861d921e2378203495bd9a0da0bdf650c`. CHARTER gates apply. F1 and the separately approved F2 core/layout source/tests/docs changed; no Git index/history, environment, or remote mutation. See `reviews/publication-recovery-f1-handoff.md` for F1 evidence.

## Baseline and failure evidence

Read the actual local publisher, reviewed promotion/finalizer, shared ingestion publisher, all three job entry paths, release-index rebuild, generic GCS copy, and translation materializer. Applied invariant-first, GCS safety, and docs-sync instructions. Relevant review findings remain present, including R6 (the finalizer race) as well as the requested R3/R4/R5/R7 and O2/O14.

Independent fake-storage reproductions and fixture script are retained in `evidence/publication-recovery/{baseline.py,baseline-results.json}`:

| Defect | Observed baseline result |
|---|---|
| R5 local source changes after planning | Planned FGB size 9, actual uploaded size 28; publication accepted with the original planned SHA in artifact metadata |
| R3 interrupted local publication | Latest PMTiles conflict left four new release files and new latest FGB; replanning refused the existing release FGB |
| R6 finalizer lost update | Read generation 10, concurrent generation 11 arrived, replacement used **11** and wrote payload derived from generation 10 |
| R7 absent native checks | Arbitrary text FGB + magic-only PMTiles returned `valid=True`, no verification or decoded feature count when native tools were absent |
| O2 ingestion backfill | Requested May release wrote all five latest objects over a recorded September latest state; no chronology consultation occurred |

Baseline targeted suite: **90 tests passed, 8 subtests passed** across publish release/workflow/finalizer, vector validation, and ingestion common tests. That success does not cover the defects above.

Additional source-confirmed gaps: reviewed promotion checks source stat without pinning that stat, does no complete staged bundle preflight, advances schema snapshots inside the object loop, has an unrelated catalog-content equality retry exception, and has no durable result receipt. The release-index rebuild synthesizes `status=success` from any release containing its canonical-format file, even without a manifest or successful run. Current workflow concurrency is per PR, not asset. No current writer owns an asset-wide publication claim.

## Invariants and smallest honest scope

1. Every consumed source is an immutable snapshot whose actual bytes match the approved input generation/digest. Native publish validation is mandatory, including on new targets.
2. A payload derived from generation G may replace only G; fresh metadata reads must not silently refresh that expectation.
3. Before any newly allocated IDs become visible in canonical release or latest bytes, exactly one transaction owns the asset and has durably reserved its allocation interval. An incomplete reservation is never expired, stolen, or forgotten.
4. Resume recognizes **this transaction's exact write**, not merely equal content. A committed release and derived-state completion are distinct from partial progress and notification delivery.
5. Latest activation never goes backward. Managed index readers advertise only committed publications; direct multi-object latest aliases remain non-atomic.

F should be implemented and reviewed in three bounded phases in the same worktree, each with its own baseline-failing tests and removal pass. F1 is independently shippable; F2/F3 are one protocol rollout and must not be called production-ready separately.

### F1 — Tactical source/validation/finalizer fixes (no new persisted protocol)

- Freeze **all** local artifact and metadata-upload inputs into an invocation-private temporary snapshot; verify planned size/SHA for every snapshot before any mutation. All schema/native checks and uploads consume those snapshots, including the manifest template. Do not check the originals and then reopen them for upload. Test changes before execution, changes to later artifacts, and replacement during snapshot capture/upload. Snapshots use the repo temp standard and are removed by their owning invocation.
- Return `LoadedJson(payload, BlobInfo)` from finalizer reads, with a generation-pinned download. Carry that exact generation into replacement; use the upload response generation, not an unconstrained post-write reload. A stale read fails CAS. Existing artifact facts remain legacy finalization behavior until F3 supplies receipts; F1 must not claim receipt-bound publication.
- `vector_asset.validate_outputs` must report failed/incomplete validation when required `ogrinfo`, `pmtiles verify/show`, or representative decode cannot run. Remove the tool-absent success branch. The existing diagnostic fields can remain `None`; `valid` cannot be true. Keep the documented geometry exception in lookup validation; this change does not impose a new geometry policy.

F1 exact files: `scripts/publish_release.py`, `scripts/finalize_promoted_release_metadata.py`, `scripts/vector_asset.py`; their three matching test files; focused `scripts/README.md` and `docs/gcp-asset-operations.md` prose if behavior needs explanation. No schema, access, or canonical-path migration.

### F2 — Approved v1 claim/receipt/checkpoint core (implementation supersedes illustrative schema)

One receipt per immutable proposal, one CAS state object per asset. No generic lock manager, claim timeout, or automatic reservation release.

```text
{asset_root}/publications/state.json
{asset_root}/publications/receipts/{proposal_key}.json
{asset_root}/publications/inputs/{proposal_key}/{ordinal}.{approved-suffix}
_catalog/publications/receipts/{proposal_key}.json  # global-catalog-only case
```

The first two paths hold operational JSON; the third is a **new bounded checkpoint layout proposal**, retaining only already approved canonical-format inputs (FGB, PMTiles, sidecar, schema/manifest JSON, CSV, COG, README/catalog metadata as explicitly typed). No upstream source formats or arbitrary keys. All object names are derived from validated lowercase 64-hex proposal keys and ordinal/known suffix combinations. Checkpoints are written only **after the asset claim reserves the IDs**. This ensures the extra copies do not expose newly assigned feature bytes before ownership. Reviewed proposals already using durable, generation-pinned staging sources need no checkpoint copy. V1 retains checkpoints; reclamation is a separate exact-generation policy, not part of recovery.

`gcs_asset` must validate these exact paths and the operation context; no blanket `publications/**` exception. This expands operational layout, not supported dataset formats. Existing publisher IAM covers category roots and `_catalog`; scheduled writers' objectUser conditions cover their owned asset roots. Thus the proposed paths require no widened grants in checked-in IAM. Actual permissions and rollout still need verification; no live IAM claims or infrastructure changes are authorized. Extra checkpoint storage and retention are an explicit operational cost.

**Identity and resume entry:** E's `proposal_key` is SHA-256 of canonical `{repository,pr_number,head_sha,merge_sha,plan_sha256}`. E's authorization identity additionally binds `{trusted_executor_sha,finalization_version}`. F stores the one receipt at `receipts/{proposal_key}.json`; it never creates a second receipt when the executor changes. `transaction_id` hashes the full immutable intent: proposal/authorization identities, prepared operation graph, predecessor, reserved interval, fixed preparation time, and derivation contract. Run/workflow/attempt IDs are audit-only and excluded from semantic identity.

On every entry, resolve the receipt by proposal key **before rebuilding outputs, reading fresh destination expectations, or calculating a new transaction digest**. If it exists, validate its immutable intent, full original executor contract, and E approval identity; resume its transaction and original operations. A changed executor SHA refuses v1 resume even if `finalization_version` is unchanged. The original pinned executor may resume if authorization still verifies. No fresh transaction against the same proposal is permitted as a fallback. The E fixture `tests/fixtures/dataset-mutation-authorization-v1.json` will supply both attempts and a mismatched executor for shared contract tests.

Ingestion uses an explicit stable request identity `(writer_id, asset_slug, scheduled/requested_release, input-source version fingerprint, pipeline configuration digest)` instead of PR identity. An active claim is looked up first by asset; a matching job request resumes its receipt before fetching/rebuilding. A different request refuses. The adapter must retain a stable source/request fingerprint; if unavailable, it cannot invent equivalence and stops. A new source revision at the same date requires an explicit repair decision, not a new automatic activation.

**Minimum stored schema** (all omitted states are schema-defined; integers exclude booleans):

```json
{
  "receipt_schema_version": 1,
  "proposal_key": "<64hex>",
  "transaction_id": "<64hex>",
  "intent": {
    "authorization": "<E immutable envelope without attempt provenance>",
    "executor_contract": {"trusted_executor_sha": "<40hex>", "finalization_version": "finalize-promoted-release-v1"},
    "asset_slug": "example-asset",
    "asset_root": "100-geographic-reference/110-boundaries/example-asset",
    "mode": "complete_release",
    "release": "2026-09-22",
    "predecessor": {"path": "gs://.../latest/example-asset.manifest.json", "generation": 123, "sha256": "<64hex>"},
    "reservation": {"start": 42, "next": 50},
    "prepared_at": "<fixed UTC timestamp>",
    "operations": [
      {"id": "input-0", "phase": "checkpoint", "source": {"kind": "local_snapshot", "sha256": "<64hex>", "size": 123}, "destination": "gs://.../publications/inputs/<proposal>/0.fgb", "expected_generation": 0},
      {"id": "release-fgb", "phase": "data", "source": {"kind": "operation_result", "operation": "input-0"}, "destination": "gs://.../releases/2026-09-22/example-asset.fgb", "expected_generation": 0},
      {"id": "release-index", "phase": "asset_derived", "source": {"kind": "derivation", "version": "release-index-v1", "inputs": ["<pinned prior index>", "<this receipt results>"]}, "destination": "gs://.../_catalog/releases/example-asset.json", "expected_generation": 321}
    ]
  },
  "phase": "prepared",
  "results": {},
  "notification": {"state": "pending", "payload": "<frozen approved payload>"}
}
```

Every operation also fixes content type, cache metadata, destination class, source digest/size or deterministic derivation parameters, and operation digest. A `results[operation_id]` entry holds exact resulting generation/digest/size/metadata. References may use only prior operation results in an acyclic graph. The operation list is immutable and covers checkpoint writes, data, manifests, README, run, per-asset index/schema snapshots, global catalog outputs, and authorized exact-generation scratch deletion. No late helper may choose a new destination generation. Source cleanup is allowed only for proposal-owned staging sources; shared/reused inputs are retained.

State minimum: `{schema_version,asset_slug,adoption_receipt,reserved_next_feature_id,current,active}`. `current` records the latest committed transaction/receipt and both immutable release-manifest and latest-manifest snapshots. `active` records transaction ID, receipt URI, immutable intent digest, predecessor and reserved interval. Source-field transactions have a null ID reservation; their data/activation writes still require the claim. Initial genesis is explicit, with an absence recheck after claim. The high-water never decreases.

**Durable order and reconciliation:**

1. Freeze/preflight inputs, compute exact intent and all destination expectations. No data mutation. Create the prepared receipt with no-clobber, then CAS idle asset state to the active transaction and reserve IDs in that same write. A losing claim leaves an inert prepared receipt and no allocation-bearing objects. The state always points to an already existing exact receipt; do not reconstruct a missing receipt from live state.
2. After claiming, upload all local checkpoints with no-clobber plus atomic transaction/operation/digest tags, recording exact generations. Only after **all** checkpoint results are verified may canonical writes begin. Use immutable reviewed GCS source generations or pinned checkpoint result generations for every copy.
3. Perform required data operations and manifests last. Before each operation require exact active ownership. Atomic tags + source/destination preconditions identify a write even if its response was lost; recovery hashes the actual tagged generation and metadata. Equal untagged bytes are never proof. Record each result using receipt CAS; reload/converge only on the identical intent and compatible result set.
4. Once every required data/manifest result is verified, CAS receipt to `committed`; only then CAS asset `current` forward, retaining `active`. The current-state advance includes exact receipt/transaction and manifest snapshots. This ordering forbids a valid state advance whose receipt has not reached commit. A state pointing at a missing/incompatible receipt is corruption, never permission to reconstruct or overwrite.
5. Run all protected per-asset derived operations from fixed expectations and receipt facts. CAS receipt to `derived_complete` after their results are accounted for; only then CAS state to clear this exact active transaction. Shared global catalog failures and notification outcomes are separate post-commit outcomes. Each remaining global/cleanup operation still uses its immutable original expectation, and never invokes fresh planning.

| Crash window | Exact reconciliation |
|---|---|
| Prepared receipt exists, claim absent | No data/IDs published; same proposal may retry the original claim if predecessor still matches |
| Claim won before any/all local checkpoints | Reservation remains. Recover checkpoints from matching tagged outputs or exact original bytes; otherwise `NOT_RECOVERABLE_SOURCE`, no canonical replay or automatic unlock |
| Write succeeded, response/result receipt lost | Verify specific tagged generation plus digest/size/metadata; journal it; unrelated/equal bytes refuse |
| Manifest written, receipt still partial | Recover manifest operation result, verify all prerequisites, then CAS receipt committed before state advance |
| Receipt committed, state not advanced | CAS original predecessor to committed snapshots only while same active transaction owns state |
| State advanced, receipt update response lost | Reload receipt: commit must already exist by ordering. Do not infer commit from state alone; missing/inconsistent receipt blocks |
| Derived write succeeded, receipt result lost | Recover tagged exact result as above; do not derive from new live state |
| Receipt derived_complete, active uncleared | Clear only the matching transaction using observed state generation; another active/current transaction is not overwritten |
| Active cleared, old worker wakes | Receipt says protected operations terminal. Any delayed duplicate still has its old per-object precondition, so cannot overwrite a later generation; no expectation refresh |

**Source reproducibility limit:** current gzip writers already use `mtime=0` and empty embedded filenames, but this does not prove stable record order or byte-identical GDAL/Tippecanoe/PMTiles output across rebuilds. The manifest builder is deterministic over inputs; run-record timestamps currently are not. V1 captures timestamps/template bytes in intent and uses named deterministic derivations, never `now()` on resume. It does **not** depend on rebuilding native artifacts byte-for-byte: after checkpoint completion, resume uses exact generation-bound GCS bytes. Before checkpoint completion, vanished local bytes produce the explicit `NOT_RECOVERABLE_SOURCE` blocker unless regenerated bytes independently match every original digest. That narrow pre-checkpoint gap can leave a reserved claim with no canonical writes; reviewed recovery must preserve its high-water. No general recovery promise follows from F1's disposable snapshots.

Every delayed GCS side effect has an immutable expected-generation operation, including schema/index updates and global catalog output. Never call legacy helpers that stat current destinations then overwrite them. After claim release, an old worker's object CAS can only fail or verify its original result, never adopt a later transaction's generation. Notifications cannot be given GCS exactly-once semantics: the receipt CAS records dispatch intent/result; a lost external response is `unknown` and requires an explicit retry choice. Notification retry never reruns publication. This limitation is not hidden behind a receipt.

F2 files: dependency-light `ingestion/common/publication.py` and `tests/test_publication.py`; exact layout validation in `scripts/gcs_asset.py`, `tests/test_gcs_asset.py`, and `docs/standards/asset-layout-and-formats.md`. Split models/storage adapter only if needed for reviewability; do not create a competing receipt model. The checkpoint extension was explicitly approved and is implemented; see `reviews/publication-recovery-f2-handoff.md` and source for the exact validated schema, user metadata and notification journal.

### F3 — Integrate every affected writer/consumer boundary

- **Local `publish_release`:** replace its independent upload loops with execution-plan construction and the shared executor; consume F1 snapshots; move schema and notification side effects to receipt-driven derived/post-commit phases. Keep entry-point mutation authorization intact.
- **Reviewed `command_promote` and finalizer:** consume E's immutable context; pinned-download and hash every source before writes; use the same complete-bundle validator for first uploads and updates; replace ad hoc copy and catalog-equality recovery with receipt replay. Finalizer becomes a deterministic payload builder over template + exact receipt facts. It must never discover newer destination facts for an old transaction.
- **Shared vector ingestion:** `publish_vector_bundle` builds one execution plan for all roles and extras. All three jobs use it; retain their source acquisition/build logic. Replace partial-release rejection with: exact managed receipt resume, otherwise explicit legacy-partial refusal. Write successful job records/schema/index only at the appropriate recorded commit; never classify an unrelated existing `success` record as this transaction's success.
- **Generated identity:** B's frozen `GeneratedIdentitySnapshot(path,generation,sha256)` and `outputs.identity_baseline_snapshot` are consumed without a second snapshot schema. B also exposes previous/next IDs and previous release. Check them against the state reservation under the claim. No generated older backfill or automatic reservation cancellation in v1. B's live deployment remains blocked until this enforcement and legacy seeding are deployed together.
- **Release-index writers/rebuild:** for managed assets, rebuild from the explicit adoption baseline plus committed receipts, not from the presence of an FGB or uncommitted success-looking object. Preserve the existing public release-index schema; commit state is internal authority, not a new SDK release schema. Per-asset CAS updates remain necessary. Legacy unmanaged read behavior remains explicit until adoption; do not silently reinterpret historical objects as committed transactions.
- **Generic `gcs_asset` canonical upload/copy/delete:** managed asset data, manifest, run, and index writes must route through a validated publication context or refuse; possession of an environment mutation flag is insufficient to bypass a claim. Scratch operations and read commands remain unchanged. Generic catalog-only repairs are explicitly classified, not allowed to smuggle allocation-bearing writes.
- **Translation materialization:** `scripts/feature_metadata_translation_pipeline.py` currently writes sidecars and rebuilds the index independently. G owns local translation integrity, not distributed publication. For managed assets, its `--upload` must route prebuilt outputs through a `metadata_update` claim or refuse with a concrete instruction to prepare that reviewed update. V1 recommendation is refusal unless called with an already validated F publication context; no hidden auto-claim after reading stale inputs. Local materialization remains available. The receipt pins canonical sidecar/schema/translation input generations. This is an explicit compatibility cost, not assumed participation.
- **Notifications and source cleanup:** receipt-driven notification retry never reruns mutations or requires scratch sources that were already deleted. Store the small approved notification payload in the receipt. A lost Slack response yields `unknown`, not an invented exactly-once guarantee. Scratch source cleanup requires committed/derived-complete receipt and exact source generations; A's periodic age policy stays separate and unchanged.

## E handoff and trust boundary

Proposed E-owned immutable envelope (E agent has been contacted): authorization schema version, repository, PR number, reviewed head SHA, merge SHA, trusted executor SHA, normalized approved-plan SHA-256, captured normalized plan bytes, and allowed named derivation versions. E owns verification/extraction/rendering, duplicate/conflicting destination rejection, and workflow gate/handoff. F owns byte preflight, mutation receipt, execution and finalization. No E receipt is introduced.

F2/F3 recommend explicit execution modes. E's bounded approval implementation retains existing proposal payloads; adding these modes requires supervisor approval and a normalization adapter, not silent reinterpretation.

| Mode | Concrete allowed example | Required ownership / restriction |
|---|---|---|
| `complete_release` | Asset A FGB/PMTiles/sidecar/schema/manifests + A README/index/schema snapshot + global catalog JSON/CSV | One catalog-resolved A root; generated IDs reserve under A claim; complete closure and exact inputs validated; newer activation only |
| `metadata_update` | New `A.metadata.fr.ndjson.gz` for A's pinned committed release plus A index update | A claim; pin canonical IDs/schema/input generations; no new/reassigned IDs or new latest release; keys/counts must satisfy the unchanged baseline |
| `object_repair` | Restore missing A PMTiles byte-for-byte from A's pinned committed manifest digest, or correct A README | A claim for any asset target/derived state; approved complete baseline and exact target expectations; data replacement must match an already committed digest/identity set; no ID introduction, allocation, schema change, or release-date activation |
| `catalog_update` | Global `_catalog/shared-datasets-catalog.csv` or web catalog render from pinned committed indices/docs | Same receipt schema, no asset claim, exact original CAS. No asset paths or protected per-asset catalog targets. It may render only declared committed input snapshots and cannot change their release/identity facts |

A mixed complete-release + global catalog proposal remains one asset transaction with classified global derived operations. Asset B data cannot hide under A's claim. `metadata_update` and `object_repair` do not implicitly provide arbitrary data modification; changing rows/IDs belongs in a complete new release. Historical generated-ID backfills are refused in v1 until reservation semantics are separately reviewed.

**Protected catalog target classifier:** `_catalog/releases/{asset_slug}.json`, `_catalog/schema-snapshots/{asset_slug}.json`, and any future asset activation/identity authority belong to that asset's claim even though they are outside its root. An index-only or schema-snapshot-only repair uses the asset's `object_repair` claim and pinned committed baseline, never `catalog_update`. Asset README belongs to the same asset root. Global catalog CSV/web JSON are presentation outputs with fixed CAS and pinned committed inputs; they cannot be used to authorize allocation or advance protected state. Unknown `_catalog` targets fail classification. A global-only receipt path uses the **proposal key**, not a second kind of transaction receipt.

Named `finalize-promoted-release-v1` and `build-run-record-v1` transformations authorize only declared generation/size/digest/validation/index-policy fields computed from pinned inputs and this receipt's results. The manifest template hash is fixed. No arbitrary field rewrite or live stat substitution is permitted. E must bind these derivations to trusted executor code so “reviewed bytes” is not silently broadened by the existing finalizer.

## Compatibility, adoption, and concrete blockers

- Existing dataset paths, file formats, source IDs, hash canonicalization, and public release-index schema remain. New operational JSON and bounded checkpoint paths under `publications/` require explicit approval and allowlisting; no broad path exception or new IAM grants.
- Managed adoption is an explicit reviewed seed, not inferred historic correctness: capture the verified current manifest/generation/digest and B high-water evidence, existing release-index payload/generation/digest, and exact known historical entries in an immutable adoption receipt. Preserve those entries as legacy facts without claiming they were produced by this protocol. Unknown partial release objects or missing allocation history block automatic adoption. Genesis requires explicit absence evidence and a recheck after winning the initial no-clobber claim.
- **B and F2/F3 are held from merge as isolated changes.** Changes to `ingestion/common` and `release_feature_model.py` automatically trigger all three production ingestion deploy workflows. A prose warning does not stop that path. F1 is independently reviewable, but B/F2/F3 cannot be called merge-ready until the guarded rollout below is approved and tested. No production migration/seed/republish occurs in this assignment.
- Concrete proposed code gate: every affected ingestion publication adapter must require validated managed publication state and the shared claim executor before its first canonical/checkpoint mutation; missing/unadopted/malformed state is a hard `PUBLICATION_NOT_ADOPTED` refusal. There is no environment flag that falls back to legacy publication. Add adapter/deploy-fixture tests proving a newly built image without seed state performs zero canonical writes, including genesis, manual historical run dates, and existing-success/partial-release branches. Preview configuration remains a separately classified environment, not a production bypass.
- Protected rollout order: review the gate plus B/F2/F3 together; prepare exact-generation adoption payloads using B evidence; quiesce old jobs and historical/manual writers through the existing protected operational process; deploy images containing the gate; seed verified managed state through the approved publisher; enable/verify claim-based writes. No code receipt can fence a still-running old binary. Quiescence and the precise production seed remain externally verified deployment prerequisites. If this grouped protected rollout is unavailable, keep B/F2/F3 out of merge rather than trusting automatic deploy workflows to wait. No live workflow/settings/Terraform mutation occurs here.
- Direct `latest/` aliases are still multiple objects. A crash can leave mixed aliases, but the claim retains recovery ownership and the index does not advertise an uncommitted release. Atomic current pointers and complete consumer migration are a separate rollout, **not** claimed here.
- Missing native tools must block publish readiness. No install is authorized in this plan.
- Required approval decisions: accept the exact operational layout/state/receipt/checkpoint schema and retention cost; explicit managed adoption; permanent incomplete-claim blocking until reviewed recovery; v1 refusal of older activation; and managed translation/generic mutation routing/refusal. These are real contract changes, so F2/F3 cannot proceed under F1-only approval.

## Exact integration ownership and tests

F3 files: `scripts/publish_release.py`, `scripts/publish_workflow.py` (mutation functions only), `scripts/finalize_promoted_release_metadata.py`, `ingestion/common/vector_pipeline.py`, `ingestion/common/gcs.py` (publication methods only), `ingestion/common/release_index.py`, all three ingestion `run.py` files (publication adapters only), `scripts/gcs_asset.py`, `scripts/dataset_alerts.py` (separate pure schema/notification preparation from mutation), `scripts/feature_metadata_translation_pipeline.py` (managed upload boundary only); matching tests; narrow workflow execution steps in `.github/workflows/publish-dataset.yml`; operational docs and GCS skill recovery instructions. Ingestion Dockerfiles must include any new module/validator dependencies (currently scripts are copied by explicit filenames); validate their dependency closure without building/deploying images unless separately authorized.

Overlaps: B owns baseline loaders and output fields in gcs/WDPA/sea-ice; F consumes them after B acceptance and does not rewrite the allocator. E owns reviewed plan normalization, authorization and workflow gate/handoff; shared file edits need explicit hunk coordination. G owns translation source/local materialization behavior; F's distributed upload guard is separate and requires combined tests. C/D keep the current public release-index contract.

| Fault/test | Required outcome |
|---|---|
| Local original changes before freeze, during copy, or after capture | Mismatch rejects before writes, or upload uses the verified private snapshot |
| One missing/corrupt/new staged role, duplicate destination, tool absence | No claim/data mutation; exact bundle/mode error |
| Finalizer input changes before read/write or result stat | Original generation refusal; no payload derived from old data replaces new state |
| Two plans share B baseline, including genesis | Exactly one obtains claim before any allocation bytes; loser has no canonical writes |
| Crash after prepared receipt/before claim, during checkpoints, after write/before result, or after manifest/commit/derived state | Follow the explicit crash matrix; original intent/expectations persist; unknown source loss blocks without releasing IDs |
| Equal bytes without ownership tags; wrong source generation/digest/derivation | Refuse adoption as an executed step |
| Wrong asset root, B data hidden in A plan, per-asset index smuggled into catalog_update, or generic bypass | Reject before mutation |
| Same proposal redispatched with changed executor/run attempt; resume after destinations changed | Attempt provenance leaves identity stable; incompatible executor refuses; stored original expectations are reused |
| Old worker wakes after a new transaction commits schema/index/catalog updates | Original object preconditions cannot overwrite the newer generation |
| Local source/checkpoint loss; nondeterministic native rebuild | Use exact completed checkpoints; missing non-reproducible bytes yield NOT_RECOVERABLE_SOURCE and retained claim |
| Backfill or stale baseline after a newer commit | No latest activation; v1 refuses generated historical allocation |
| Managed partial release encountered by index rebuild | Not advertised; preserve the prior committed index identity |
| Late localized upload/rebuild without receipt context | Refuse managed mutation; local materialization unaffected |
| Schema/index/notification failure | State accurately records phase; notification retry does not repeat data writes |
| Retry after staged source cleanup | Committed receipt suffices for reporting/notification; no re-promotion |
| Legacy valid baseline, legacy partial data, malformed/unknown protocol schema | Explicit adoption only for valid evidence; fail closed otherwise |

Validation: first demonstrate failing regressions per phase, then run focused publication/finalizer/vector/GCS/ingestion/release-index/translation workflow tests and Ruff; run native integration only with existing tools, stating skips. Use fake storage with real generation semantics and fault injection after **each** durable transition and operation. Include installed SDK request-shape tests for atomic tags/source and destination preconditions. Final `git diff --check` and independent supervisor review are required by CHARTER.

Removal targets: stale post-read reloads, local-file reopen after validation, tool-absent `valid=True`, independent per-path publication loops, schema snapshot updates inside copy loops, content-equality-only retry exceptions, and synthesized managed-release success from file presence. Keep legacy readers explicitly until adoption; do not silently delete compatibility.

**Recommendation:** review the completed F1 independently, approve or revise this F2 schema/order/checkpoint extension next, and implement F3 adapters one at a time with the shared failure matrix. F remains incomplete until F2/F3 participation and rollout prerequisites are accounted for. No production-readiness or full atomicity claim follows from F1 acceptance.

## Implementation status update

F2 implementation and validation are frozen in `reviews/publication-recovery-f2-handoff.md`. Its source is authoritative over the earlier illustrative minimum-schema pseudocode here. F3 source changes remain unapproved; `plans/publication-recovery-f3.md` supersedes the earlier F3 sketch and approval recommendations here. F2 supervisor acceptance is recorded in `reviews/publication-recovery-f2-supervisor.md`.
