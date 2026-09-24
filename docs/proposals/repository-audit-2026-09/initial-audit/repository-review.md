> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

**Repository review — shared-datasets-1 — 22 September 2026**

The repository has good safety primitives, but it needs stronger guarantees across complete releases and workflows. The most valuable simplification is to make **release identity, reviewed publication, and operational completion explicit, durable objects**. Today, several implementations reconstruct those facts independently from mutable paths, catalog dates, matching bytes, PR prose, and agent-supplied booleans. The defects below cluster around those boundaries.

This review covers all four requested areas: complexity reduction; correctness/performance; useful product/maintainer features; and executable, checkpointed workflows for LLM-driven maintenance. Recommendations are review findings, not implemented changes.

**Scope, evidence, and current checkout**

The initial snapshot was clean main at `c69850b9337e9ab14bbcdb4267dfecdf7bd9aab6`. Its inventory contains 326 tracked files, including 34 operational Python scripts, three ingestion jobs and shared ingestion libraries, four services, Python and TypeScript SDKs, the catalog browser, 50 Terraform files, 33 GitHub-related files, 67 test/support files, and 24 asset documents. Separate reviewers examined consumers, operations/infrastructure, and data contracts while the primary reviewer traced publication and orchestration.

During the review, another task changed the checkout to `fix/share-sync-lane` at `aee6fd5163b8de6cf5d6c5acc9abd99cc2b3b09c`, then edited 19 workflow, guardrail, skill, and test files and merged them through PR #146 as `e2a29ea0a5ecd7d7688158a67f25e88400d4224f` at 21:25 UTC. The final checkout is clean main at that commit. Those changes were not made by this review. The Terraform concurrency and apply-detection guardrail findings are now **resolved in code by that merged PR**; the consumer/data-contract findings remain applicable to unaffected files. Source links resolve into the shared working tree, so line numbers in actively edited workflow files can move.

Verified baseline:

- Initial Python baseline: **732 passed, 4 skipped, 359 subtests passed**. Final code tree after the concurrent fixes: **738 passed, 4 skipped, 427 subtests passed**; Ruff also passed again. The tested branch tree exactly matches merged main (`2c848d301a05f714a5e1231c0f002e0f4b647672`). Skips require native GDAL/Tippecanoe/PMTiles tools.
- TypeScript: **28 tests passed**; ignored `dist` outputs were rebuilt by the package test command.
- Ruff passed; Terraform formatting passed; static repo guardrails passed using bundled Git.
- Catalog/docs generation check passed for all 24 asset docs, with source-confirmation/property-description warnings on six assets.
- Offline identity-resolution validation passed for all seven checked-in decisions.
- Small offline reproductions demonstrated stale SDK caches, historical/current release mismatches, ID reuse, translation corruption, cleanup misclassification, missing validation, partial publication, and concurrency amplification.
- Read-only GitHub queries confirmed effective main rules, production environment protections, relevant open PRs, and two failed Terraform sync runs on the initial commit.

No GCS data, cloud infrastructure, Git history/index, or repository source was changed by the reviewers. No deployed endpoint load test, live GCS correctness audit, production-size memory benchmark, or fresh native conversion run was performed. A source-confirmed race is distinguished below from an observed production incident.

Detailed coverage and additional evidence are retained in the consumer report (historical artifact: `${INITIAL_AUDIT_WORKDIR}/consumer-review.md`), operations report (historical artifact: `${INITIAL_AUDIT_WORKDIR}/operations-review.md`), and contracts report (historical artifact: `${INITIAL_AUDIT_WORKDIR}/contracts-review.md`). Those reports include specific triggers, local reproduction results, affected lines, and narrower caveats. Identifiers such as “consumer C1” refer to their numbered findings.

**First priorities**

P1 means a material integrity/authorization problem under the described trigger; P2 means a meaningful correctness, reliability, or efficiency issue; P3 means lower-impact hardening/maintainability. This is prioritization, not a claim that every trigger has occurred in production.

| Priority | Finding | Practical impact | First action |
|---|---|---|---|
| P1 | Main/production review enforcement is weaker than the documented contract | Changes can reach production workflows without a CODEOWNER approval | Reconcile effective GitHub rules with the explicit self-authored exception and required reviews |
| P1 | PR-body plans are not bound to approval | A later edit to a merged PR body can supply a different mutation plan on redispatch | Execute immutable plan bytes bound to reviewed commit and digest |
| P1 | Scratch cleanup equates one matching file with completed publication | A fresh proposal with one unchanged artifact can be deleted before approval | Require exact completed-promotion receipts and account for every source object |
| P1 | Generated IDs reuse deleted historical IDs | A downstream reference can identify a different feature after later releases | Persist and consume a monotonic high-water mark; define tombstones |
| P1 | Python latest cache uses static catalog dates | Daily/monthly updates can remain invisible and lineage can be wrong | Resolve latest to exact release/generation before caching |
| P1 | Historical maps and TS layers can combine different releases | Current geometry can be joined to historical metadata | Resolve tiles and metadata as one immutable bundle |
| P1/P2 | Publication is partially applied and not generally resumable | Failures leave mixed latest files and retries blocked by existing release objects | Add commit semantics and a durable per-object receipt |
| P1/P2 | Older backfills overwrite current latest | Direct latest bytes and release index disagree | Separate backfill creation from monotonic current-release activation |
| P2 | Publisher validation differs across entry paths | Reviewed copies can bypass the stronger local bundle checks | One validator over exact staged generations before any mutation |
| P2 | Localization can corrupt files, misassign translations, or overwrite newer output | Incorrect or empty metadata can appear valid | Atomic output, strict identity matching, input-generation binding |
| P2 | Metadata lookup scans gzip under a global lock and caches forever | Small requests cause large serialized work and unbounded memory | Per-release bounded indexing and cache ownership |
| P2 | Deploy/CI coverage has holes | Green runs can miss scheduler changes, skip branches, or native regressions | Executable resource ownership and behavior tests |

The Terraform stale-plan race is real: [Cron alert policy sync](https://github.com/SkyTruth/shared-datasets-1/actions/runs/31068313249) failed with “Saved plan is stale”; [metadata index-loader IAM sync](https://github.com/SkyTruth/shared-datasets-1/actions/runs/31068313296) also failed. [PR #146](https://github.com/SkyTruth/shared-datasets-1/pull/146) merged during this review and addresses the queue/guardrail issue. These are historical findings resolved in the final code snapshot, not outstanding recommendations. The next live deployment outcomes were not awaited as part of this review.

**1. Opportunities to reduce complexity while improving robustness**

**One validated release bundle should own every consumer reference.** Define a versioned contract containing asset slug, release identity, identity-baseline version, schema, access tier, declared locales, and artifacts with exact URI, generation, checksum, size, and role. `latest` should resolve once to that bundle. The Python SDK, TS SDK, map signer, browser inspector, downloads, caches, and lookup API should consume the same result. This removes date/path guessing and fixes several independent current bugs. Keep existing direct `latest/` URLs as a compatibility view while migrating consumers; do not silently break public paths. Evidence: consumer C1–C3; [Python resolution](../../../../api/python/src/skytruth_shared_datasets/catalog.py#L381), [TS layer resolution](../../../../api/typescript/src/metadata-records.ts#L130), and [browser signing](../../../../web/catalog/map-preview.js#L560).

**Unify publication semantics before adding more commands.** There are materially different paths: local `publish_release`, reviewed object-by-object promotion, shared ingestion publication, post-promotion manifest finalization, and localization materialization. Each knows some portion of release validity and completion. Use one pure planner/validator and one resumable executor with narrow adapters for local files, staged GCS generations, and scheduled source output. Keep source-specific acquisition separate. A completed immutable release manifest plus an explicit current-release pointer is much simpler than independent repair logic across latest files, run records, indexes, schema snapshots, and catalogs. Evidence: [publish_release](../../../../scripts/publish_release.py#L345), [reviewed promotion](../../../../scripts/publish_workflow.py#L278), [ingestion pipeline](../../../../ingestion/common/vector_pipeline.py#L131).

**Centralize persisted contracts and generate their projections.** Format names, roles, locale grammar, identity rules, generation validation, schema waivers, content types, and metadata paths recur across scripts, SDKs, templates, docs, and tests. This is already causing disagreement, not merely aesthetic duplication. A small dependency-light contracts package with versioned schemas and cross-language fixtures should own those rules. Generate TypeScript types, examples, data dictionaries, and catalog projections where practical. Keep discovery metadata and release artifact metadata distinct types instead of accepting loosely related dictionaries. Evidence: contracts C9–C11 and [catalog roles](../../../../scripts/catalog_docs.py#L170).

**Separate run attempts from releases.** A source-period release date, wall-clock attempt, scheduled execution, publish transaction, and last successful refresh are different facts. Use `RunAttempt` and `ReleaseBundle` types with explicit timestamps/statuses rather than overloading `date`. This removes repeated success/skip record assembly and makes freshness accurate. Evidence: operations O8/O20 and [run index construction](../../../../ingestion/common/release_index.py#L328).

**Make deployment policy declarative and deployment mechanics shared.** The three ingestion workflows duplicate build, digest selection, plan allowlists, in-flight detection, cancellation, canary execution, and verification. A constrained reusable workflow plus tested commands should implement those mechanics. A small manifest should map each component to source closure, Dockerfile, runtime permissions, Terraform resources, scheduler, bucket roots, alerting, and verification. Generate/check filters and allowlists from that manifest; do not create an unrestricted generic deployment executor. Evidence: operations O4/O6/O7/O10/O15/O16.

**Reduce dormant serving branches after confirming compatibility.** Firestore serving is explicitly inactive, yet the repository retains a large inactive service, Firestore adapters in the active sidecar service, loaders, IAM/workflow knobs, and validation piggybacking on those loaders. Extract the useful pure bundle validation, preserve any externally required inactive endpoint response, and remove or quarantine unreachable internals with an explicit reactivation/migration plan. Likewise replace production `TypeError` fallbacks for fake/older GCS clients with one supported adapter contract. Do not delete real persisted-format readers without an inventory of remaining generations. Evidence: consumer complexity items 2–3; contracts O2; operations O25.

**Make the concierge an executor of evidence collection.** Its state machine is a useful foundation, but many “completed” steps attest strings and booleans rather than bind output bytes to actual checks. Keep humans/LLMs responsible for source suitability, licensing interpretation, identity decisions, and semantic changes. Let code run profiling, build validation, staging inventories, receipt creation, PR rendering, and post-publish reconciliation. Consolidate existing helpers under that flow instead of inventing another orchestrator. Evidence: [artifact validation evidence](../../../../scripts/publishing_concierge.py#L1449), [confirmation](../../../../scripts/publishing_concierge.py#L2836), [PR readiness](../../../../scripts/publishing_concierge.py#L2694).

**Delete brittle tests when stronger behavior tests replace them.** Many tests assert literal YAML/JavaScript strings and call sequences. Some encode unsafe behavior: copying malformed catalog JSON, positional translation import, and admission bypass. Keep policy checks where syntax is itself the contract, but test externally observable behavior and state transitions. This permits simplification without having to preserve incidental implementation text. Evidence: [malformed catalog copy test](../../../../tests/test_publish_workflow.py#L225), [browser marker tests](../../../../tests/test_catalog_web_pmtiles_js.py#L35), and domain reports.

**2. Bugs and inefficient structures**

**Review and publication integrity**

**R1 — P1, live configuration: the documented human review requirement is not enforced.** The active [main ruleset](https://github.com/SkyTruth/shared-datasets-1/rules/18755592) requires a PR but sets `required_approving_review_count=0`, `require_code_owner_review=false`, and `dismiss_stale_reviews_on_push=false`. Required status checks are `lint`, `tests`, and `geospatial-changes`; the native `geospatial-integration` result is not required. The production environment restricts branches to main but has no required-reviewer protection rule. These settings were read from GitHub during this review. CODEOWNERS alone cannot supply the guarantee described in AGENTS. Encode the intended distinction between self-authored acceptance and someone else's changes; require the appropriate review/checks and continuously audit those settings. This review did not change them.

**R2 — P1, verified code path: approval is not bound to the plan that executes.** [The approval check](../../../../scripts/publish_workflow.py#L122) accepts any historical APPROVED record from the required reviewer without matching the reviewed commit or evaluating the latest decision. On dispatch, [the workflow fetches the current PR body again](../../../../.github/workflows/publish-dataset.yml#L171) and extracts new plan bytes; the gate outputs only presence flags. A merged approved PR body can change after approval, and the apply job can execute that changed plan. Put the canonical plan in the reviewed commit or an immutable, explicitly approved artifact; bind approval to its SHA-256, exact source generations, destination expectations, and repository revision. Pass the same captured bytes into execution. A mutable PR description should render the plan, not be its only authority.

**R3 — P1/P2, locally reproduced: partial publication is not safely resumable.** [Local publication](../../../../scripts/publish_release.py#L391) uploads release artifacts, then independently replaces latest artifacts, then writes the manifest and success record. A simulated conflict replacing latest PMTiles left the new latest FGB plus release files, but no completed manifest. Replanning immediately failed because the release FGB already existed. Reviewed promotion has the same composition issue: [its loop](../../../../scripts/publish_workflow.py#L343) performs each copy and schema-snapshot update in sequence without a durable completion receipt or general idempotent replay. An alert/finalization failure after data writes also makes a rerun collide with old preconditions. Cleanup can remove the staged sources needed for later reconciliation. Fix with explicit prepared/validated/committed/derived-complete states; recognize an already completed step only by the approved bytes and exact resulting generations. Keep notifications outside the data commit and retry them independently.

**R4 — P2, verified boundary gap: reviewed promotion bypasses the strongest bundle checks.** [Local bundle validation](../../../../scripts/publish_release.py#L653) checks sidecar/schema/manifest shape, manifest hashes, and lookup tile properties. The reviewed workflow performs path validation plus schema checks only for replacement targets, then copies objects; it does not require the same complete bundle validation on staged generations. [New targets are skipped](../../../../scripts/publish_workflow.py#L242). [Plan normalization](../../../../scripts/reviewed_dataset_plan.py#L412) accepts duplicate destinations and preserves arbitrary promotion order. An offline example accepted two identical latest-only promotions; the first copy would make the second fail. Require unique destinations, explicit operation type (complete release, metadata-only update, reviewed repair), artifact closure, deterministic commit order, cross-artifact IDs/counts/schema consistency, and exact staged-byte checks before any mutation. Retain intentional metadata-only/repair use cases as explicit modes.

**R5 — P2, locally reproduced: local plan checksums are not bound to uploaded bytes.** `PublishArtifact` records SHA-256 and size during planning, but [execution reopens the file](../../../../scripts/publish_release.py#L395) without rechecking those facts. Changing FGB bytes after planning was accepted: the final manifest claimed the original nine-byte checksum/size while the uploaded file contained 28 bytes. This can occur with overlapping builds in the same output directory. Build into immutable per-run outputs, finalize/lock the validated bundle, and verify checksums immediately before consuming it. Hash/validate the actual staged object generation as the authoritative publish candidate.

**R6 — P2, source-confirmed race: metadata finalization rereads a newer generation instead of protecting the one it read.** [Finalization reads a JSON object](../../../../scripts/finalize_promoted_release_metadata.py#L270), computes a payload, then [replacement reloads the destination](../../../../scripts/finalize_promoted_release_metadata.py#L172) and uses that newly observed generation. If a concurrent publisher changes the object between those operations, the finalizer can overwrite the newer object with a payload derived from the older one. Artifact generation fields are also derived from current stats rather than a promotion receipt. Carry the original read generation through compute/write and derive artifact facts from the exact completed copies. No live race was induced.

**R7 — P2, locally reproduced: generic vector validation can report corrupt artifacts as valid when tools are absent.** [Native checks are conditional on executable discovery](../../../../scripts/vector_asset.py#L743); missing decode is fatal only when required property options were supplied. With `shutil.which` returning none, an arbitrary text FGB and a 128-byte file containing only PMTiles v3 magic were accepted with `valid=True`, `pmtiles_verify=None`, and no decoded tile. Missing mandatory validation must be an explicit incomplete/failed result, and publish readiness must require all required checks. A lightweight inspect mode may still report what it could determine, but must not claim publish validity.

**R8 — P2, workflow checkpoint gap: saved concierge completion can outlive its evidence.** [State writes are direct truncating writes](../../../../scripts/publishing_concierge.py#L554) without atomic replacement or locking. Completion records primarily store paths, descriptions, commands, and booleans; [readiness checks completion flags](../../../../scripts/publishing_concierge.py#L2694) and rebuilds the plan rather than invalidating downstream steps when source bytes, toolchain, schema, or decisions change. Use atomic state persistence, a process/state lock, per-step input/output fingerprints, deterministic dependency invalidation, and recorded command results. Keep review decisions as explicitly attributed decisions, not agent-invented boolean approval claims.

**R9 — P2 opportunity: CSV compatibility is inferred from a small sample instead of a declared schema.** [Schema inspection samples the first 200 rows](../../../../scripts/dataset_alerts.py#L193), defaults empty columns to String, and infers types from values. Reordering data or changing which values occur early can change the apparent schema without changing the intended contract; later incompatible values can be missed. The sampled schema is useful profiling evidence, but a publish gate should validate against a steward-owned schema and stream-check the full data where necessary. It should also reject malformed rows with clear diagnostics instead of calling `.strip()` on a missing value. This is an algorithmic limitation, not a claim that the current table assets are malformed.

**Additional confirmed data and consumer findings**

| Finding | Trigger and effect | Recommended fix / detailed evidence |
|---|---|---|
| P1: scratch proposal deletion | Any one unchanged artifact matches an older release; all staged proposal files are deleted even at age zero | Promotion receipt plus all-object reconciliation; operations O1, [cleanup classifier](../../../../scripts/scratch_cleanup.py#L148) |
| P1: historical ID reuse | R1 A=1/B=2 → R2 A=1 → R3 A=1/C=2 | Persist high-water/tombstones and consume them; contracts C1, [allocator](../../../../scripts/release_feature_model.py#L335) |
| P1: stale Python latest cache | Cron refresh changes bucket/index but static CSV date is unchanged; cache returns old bytes and false resolved date | Cache pinned release/generation/checksum; consumer C1, [fetch](../../../../api/python/src/skytruth_shared_datasets/catalog.py#L422) |
| P1: restricted historical maps use latest tiles | Signer receives only slug, signs top-level latest path; sidecar uses selected historical date | Version-aware signer and coherent bundle; consumer C2, [signer](../../../../services/catalog_viewer/run.py#L429) |
| P1/P2: TS layer version mismatch | Version option pins sidecar but leaves tile ref latest; nonexistent release silently falls back to latest | Exact bundle resolution, explicit not-found; consumer C3, [layer helper](../../../../api/typescript/src/metadata-records.ts#L155) |
| P1/P2: backfill/current disagreement | Older backfill replaces latest while index chooses maximum release date | Explicit monotonic activation/rollback semantics; operations O2 |
| P2: geometry hash changes on numeric representation | `[1,1]` versus `[1.0,1.0]` produce different identity hashes; polygon ordering also affects them | Define/version equivalence contract and migrate deliberately; contracts C2 |
| P2: invalid JSON admitted | Python accepts/emits NaN/infinity; browser parsing rejects the published record | Strict producer JSON and cross-language fixtures; contracts C3 |
| P2: localization truncates input | Output aliases canonical sidecar; writer truncates before lazy read, reports valid zero rows | Alias rejection, atomic write and independently validated counts; contracts C4 |
| P2: failed translation never retried | Provider failure saves source fallback as current successful row; later runs skip it | Explicit failed/pending state and selective retries; contracts C5 |
| P2: reordered workbook corrupts translation assignment | Import counts hash mismatch but accepts row-position pairing | Exact hash/shard matching, reviewed positional recovery only; contracts C6 |
| P2: old localization run overwrites newer output | Runs do not serialize; destination expectation is read after derivation | Pin input bundle and original destination expectation before work; contracts C7 |
| P2: removed locales remain served | Current CSV lacks locale but previous sidecar remains advertised | Declared locale set and explicit reconciliation; contracts C8 |
| P2: HTTP request body is unbounded | Negative/malformed/huge Content-Length is read before request validation | Shared strict bounded transport parser and deadlines; consumer C8 |
| P2: lookup API coercions disagree | Active API accepts booleans/null IDs via str and string 'false' as true provenance; invalid UTF-8 becomes 500 | One strict request contract; consumer C9 |
| P2: unknown-size autoload bypass | `Number(null)` / `Number('')` becomes zero | Validate size before conversion, budget actual memory; consumer C10 |
| P3: inherited-property asset lookup | Unknown slug `constructor` resolves to Object from a plain `{}` map | Map/null-prototype dictionaries and own-property checks; consumer C11 |
| P2: native/metadata errors are obscured | Unexpected lookup failures are discarded or signing errors escape the JSON boundary | Structured logs and one transport exception boundary; consumer C12 |
| P2: templates instruct rejected contracts | Both README templates use unsupported roles; standards contain incompatible translation CSV headers | Generate/test examples and remove obsolete duplicate text; contracts C9 |
| P2: admission enforcement does not match policy | New asset docs alone are not validated; one complete unrelated doc can cover multiple new jobs | Validate each asset/job mapping at enforced boundary; contracts C10 |
| P2/P3: validators disagree | Catalog accepts multi-field source ID while release builder rejects it; generation checks admit bool/nonpositive int | One typed persisted-contract owner; contracts C11 |

**Operational correctness and efficiency**

| Finding | Impact | Recommendation / evidence |
|---|---|---|
| P2: WDPA skipped canary fails watch | Intended skip leaves `WDPA_CANARY_EXECUTION` unset; unconditional watch exits under `set -u` | Gate dependent steps on explicit result; operations O4. Unchanged by concurrent queue edits |
| P2: scheduler Terraform changes are not deployed | Relevant changes trigger job deploy, but plan targets/allowlists omit sibling scheduler resources | Protected scheduler/bootstrap path and ownership coverage check; operations O6 |
| P2: advertised cancel/override permission gap | Declared deploy role lacks `run.executions.cancel` and `run.jobs.runWithOverrides` | Narrow reviewed permissions plus capability preflight; operations O7. Effective live inherited grants were not inspected |
| P2: actual check-in date lost | Index stores source release date in latest-run date, not attempt date | Versioned attempt/release fields and correct ordering; operations O8 |
| P2: sea-ice outage looks like normal skip | Exhausted network/server failures across all candidates return no-source success | Distinguish unavailable from unassessable, monitor freshness; operations O9 |
| P2: native test selection misses core modules | Changes only to release model/PMTiles zoom can skip geospatial integration | Dependency-closure test selection plus required gate; operations O10 |
| P2: images ignore copied lockfile | Unpinned pip/native installs make builds of one commit differ | Locked runtime and deliberate native image/tool versions; operations O11 |
| P2: deployment labels can misidentify source | Checkout moving main, tag with triggering SHA | Resolve and use one actual trusted commit everywhere; operations O12 |
| P2: metadata lookup serializes unrelated assets | Global lock held through GCS gzip scan, including cold misses | Per-bundle work, bounded index, no lock during remote scan; consumer C4 |
| P2: positive/negative caches are unbounded | Arbitrary missing IDs and obsolete generations remain forever | Separate size-bounded positive/negative caches and eviction; consumer C5 |
| P2: cold permission filtering multiplies requests | 100 parallel rows generated 100 catalog loads in local reproduction | Shared pending promise and distinct-slug pass; consumer C6 |
| P2: catalog rendering awaits every index | One unresolved fetch holds all interactivity | Render discovery first, bounded lazy hydration/timeouts; consumer C7 |
| P2: previous releases defeat streaming memory limits | Entire compressed/decompressed records for multiple WDPA assets retained by caller | Compact on-disk identity baseline, one asset at a time; operations O13 |
| P2: translation tasks are unbounded/nonresumable | All records/tasks/Futures retained, successful work saved only at end | Durable task ledger, bounded worker queue, incremental writes; contracts O1 |
| P2 consistency gap: live EAMLIS pagination | Provider can change between offset pages without final version recheck | Stable source snapshot/IDs and before-after fingerprint; operations O19. No mixed live extraction was observed |
| P3: native work appears silent and accumulates stderr | Long conversions buffer stderr until completion | Stream progress with bounded diagnostic tail; operations O23 |

**3. Features or structure that would materially improve the experience**

These are prioritized additions to existing capabilities, not a recommendation to grow an unrelated platform.

| Feature | User benefit | Smallest useful implementation |
|---|---|---|
| Exact-release resolution and a consumer lockfile | Reproducible analyses, reliable caching, correct citations and joins | Both SDKs return/persist exact bundle generations and hashes; `latest` is resolved before use |
| Machine-readable release comparison | Consumers can judge whether to refresh or migrate | Counts of added/removed/retained/rekeyed IDs, schema diffs, bounds changes, access/lifecycle changes, translation coverage |
| Freshness and health status | Distinguish old source data from a stalled or intentionally blocked pipeline | Per-asset expected cadence/grace period, last attempt, last success, blocked reason, current release |
| Generic artifact downloads | CSV/COG and other approved formats are first-class, including private/historical files | Role-driven download list/signer with exact version, size, checksum, format and access policy; current UI focuses on FGB/metadata/schema |
| Data dictionaries generated from schema | Consumers understand units, codes, nulls, source fields and translation eligibility | Add steward-owned field descriptions/units/enums to schema and generate README/UI views |
| Translation coverage and explicit fallback | A locale choice no longer implies that every value was translated/reviewed | Requested/resolved locale plus fallback/review state, counts and stale/missing coverage; build on PR #145 |
| Capability discovery | Integrations know whether click metadata, locales, signing, or preview is supported | Explicit per-asset/service capability fields instead of hostname inference or assuming every vector has a sidecar |
| Shareable catalog state | Reproduce a support case or analytical view | URL state for asset, exact release, locale, filters/map extent and a citation-copy action, without credentials |
| Indexed bounded metadata lookup | A click or batch request has predictable cost/latency | Sharded/on-disk indexed data keyed by release/generation; evaluate using measured sizes before selecting storage technology |
| One maintainer status/doctor command | Faster diagnosis and agent handoff | Read-only report of toolchain, local workflow checkpoints, approved plan, promotion receipt, derived outputs, freshness and deployment state |

[PR #145](https://github.com/SkyTruth/shared-datasets-1/pull/145) is already a **specification-only** proposal for release-coupled declared locales, coverage/debt surfacing, per-record fallback state, and deduplicated notices. Reuse that work. Its product decisions about machine placeholders, human review ownership, notification routing and thresholds remain separate from fixing current corruption/race defects.

The repository's 24 active asset docs include six dictionaries with source-confirmation placeholders, four without declared feature identity/metadata contracts, and fifteen without structured bounds. Treat this as a capability/stewardship inventory, not proof that remote artifacts are missing. Fourteen assets lack admission frontmatter but may be explicitly grandfathered; do not retroactively classify them as invalid solely for that reason.

**4. Operationalize LLM-driven maintenance with executable checkpoints**

The existing concierge, generation preconditions, identity decision files, and focused skills are the right starting point. Strengthen them around the following lifecycle; the names below describe proposed states, not existing CLI commands.

| State | What code should establish | Human/LLM judgment that remains |
|---|---|---|
| Intake recorded | Source identity/hash, taxonomy candidates, formats, estimated footprint, steward/consumers/citation fields | Suitability, license interpretation, admission rationale |
| Identity decided | Full candidate profiling and unique/null/legality checks; baseline/high-water and deterministic decision packet | Ambiguous continuity, approved assignment key, intentional rekeys |
| Build validated | Locked toolchain receipt, immutable outputs, complete bundle hashes/counts/IDs/schema validation | Explicit exceptions and meaningful quality interpretation |
| Plan reviewed | Unique destinations, exact staged generations, expected destination generations, immutable plan digest/commit | Consumer impact and approval of the concrete change |
| Release committed | Idempotent copy receipts, manifest completion, one checked activation of current release | Explicit rollback choice if activation would go backward |
| Derived views reconciled | Index/catalog/locales rebuilt against committed bundle, checksummed outputs and independent statuses | New locale/review-policy decisions |
| Verified complete | Consumer smoke checks, freshness, receipt, notification outcome, retained work directories | Any unresolved source/data quality decision |

The code should report `pending`, `blocked-on-decision`, `running`, `validated`, `partially-applied`, `committed`, `derived-pending`, `complete`, and `failed` explicitly. A notification failure must not turn committed data into an unknown state. A translation fallback must not become successful translated work. A matching file must not imply a completed proposal.

Every checkpoint should bind source generations/content hashes, baseline release, schema/policy version, approved decisions, toolchain, input/output digests, and exact operation receipt. Changing an upstream input invalidates dependent checkpoints automatically. Persist atomically; lock concurrent writers; provide a machine-readable status and concise human explanation. Recovery should consume these facts, not require reconstruction from chat history or logs.

**Concrete workflows worth scripting next:**

- Extend the concierge to execute deterministic profiling/build/validate/stage/verify steps and collect evidence itself. Reserve prompts for actual decisions.
- Add a release validator that works identically on local candidates and generation-pinned GCS bundles. Verify actual cross-file contents rather than only their filenames or a user-supplied `valid: true`.
- Add a dry-run reconciliation command that explains incomplete promotions, stale derived views, and safe resumable actions using receipts. Keep mutation execution behind the established reviewed path.
- Persist identity decision packets keyed by source hash and baseline generation so a blocked giant build can resume without redownloading/recomputing everything.
- Persist translation successes/failures by source hash, locale, provider/model/configuration and context; retry failed work selectively. Strictly bind document shards and returned translations to source identity.
- Generate/check deployment paths, permissions, resource coverage, and test selection from one component manifest. Add a read-only check of effective GitHub rules/environment protection.
- Add read-only health checks based on elapsed freshness and missing attempts, not only failed executions. Surface actionable state to downstream users as well as maintainers.
- Treat scratch cleanup as garbage collection over a receipt graph. Eligible source generations are those proven no longer needed by any pending/recoverable plan. Use exact deletions and preserve historical releases.

**Tests that would catch the important failures**

These add confidence across boundaries rather than mirroring implementation:

1. Three or more identity releases: deletion of maximum ID, new feature, return-after-absence, intentional reuse decisions, representation-only geometry differences.
2. One cross-language fixture set: exact dated/current/missing releases, each access tier, changed IDs/geometries, missing/large/localized metadata, invalid JSON, generation changes. Feed it to both SDKs, services, and browser behavior tests.
3. Fault injection after every publication step; retry the same approved plan; confirm no mixed activated bundle, no schema snapshot advanced before commit, and no valid source lost to cleanup.
4. Concurrent publish/backfill/localization runs with barriers at read/compute/commit boundaries; reject stale activation instead of blessing new generations late.
5. Real browser flows with fake services: historical restricted map, feature click, private download, slow index, expired credentials, locale fallback. Source-string assertions cannot verify these joins.
6. Workflow script branches with stubbed CLIs: skipped canary, overrides, cancellation, plan conflict, dropped/queued work, missing role permission and changed-source checkout.
7. Template/schema examples executed through actual parsers, plus contract acceptance/rejection fixtures shared by every validator.
8. Scale tests at real orchestration boundaries: bounded memory loading previous sidecars, bounded translation queues/cache bytes, one cold catalog request, cached lookup unaffected by another asset's scan.

The native suite should be selected whenever its actual dependencies change, and the effective branch gate should require its result when applicable. The baseline's passing test count is meaningful but does not establish these currently untested properties.

**Recommended implementation sequence**

| Work package | Scope and dependencies | Completion evidence |
|---|---|---|
| A — Close immediate integrity holes | Scratch cleanup eligibility, generated-ID high-water, exact historical SDK/signer behavior, input/output alias guard, strict JSON | Reproductions above fail before the fix and pass afterward; no incompatible unplanned ID migration |
| B — Close remaining operational gaps | PR #146 queue/guardrail fix is merged; address WDPA skip, required native gate, and human-review enforcement | Trusted revision/image receipt, negative policy tests, intended canary skip succeeds, effective GitHub settings checked |
| C — Establish release contract and publication commit | Shared validated bundle, immutable reviewed plan, receipt/resume model, monotonic latest, finalize generation binding | Fault/concurrency tests prove coherent activation and safe replay; every published bundle validates identically |
| D — Make serving predictably bounded | Exact bundle cache keys, lookup indexing/lock scope, bounded caches/bodies, SDK single-flight, incremental catalog loading | Measured latency/memory ceilings and no cross-asset head-of-line blocking |
| E — Complete translation lifecycle | Atomic import/materialization, strict row/shard identity, retryable task states, declared locales/coverage from PR #145 | Stale runs cannot activate; failed tasks retry; reordered workbooks rejected; fallback visible |
| F — Consolidate maintainership | Shared deployment/component manifest, generated contracts/templates/data dictionaries, status/doctor and checkpoint integration | Reduced duplicate logic/instructions, coverage checks catch deliberately omitted resources, restart from recorded state works |

Do not begin with a broad directory reshuffle or rewrite. Fix the demonstrated failures, then centralize the invariants those fixes require; delete the duplicate validators, fallbacks, and prose only once their owner is explicit. Retain source-specific ingestion logic, generation preconditions, canonical/staging separation, deterministic gzip, compact lookup tiles, citation/lifecycle metadata, and real public/persisted compatibility.

**Verification record and retained artifacts**

Baseline commands: `UV_CACHE_DIR=.uv-cache uv run --no-sync pytest -q -p no:cacheprovider`; `UV_CACHE_DIR=.uv-cache uv run --no-sync ruff check .`; `npm test` under `api/typescript`; `terraform fmt -check -recursive terraform/`; `catalog_docs.py check`; `repo_guardrails.py check-static`; `check_identity_resolutions.py --offline`. Existing uv environment was used; no dependency/environment migration was performed. The system Git failed because of the local Xcode license; the bundled Git worked, and guardrails passed when its directory was put first in PATH.

Read-only GitHub queries inspected exact-head Actions runs, main branch ruleset 18755592, production environment branch/reviewer rules, open PRs #145/#146, and the two failed sync logs. No workflow was dispatched, no review/comment was posted, and no settings were changed.

Offline publication/validator reproduction script (historical artifact: `${INITIAL_AUDIT_WORKDIR}/root-repros.py`) and results (historical artifact: `${INITIAL_AUDIT_WORKDIR}/root-repro-results.txt`) are retained with synthetic fixtures and the tracked-file inventory. All review artifacts live under `${INITIAL_AUDIT_WORKDIR}`.

The central invariant recommendation is: **each approved operation consumes known bytes, produces one coherent immutable release, and records exactly what completed; every consumer resolves that same release before using its artifacts.** No code, fallbacks, compatibility paths, or remote objects were added/removed by this review. Remaining uncertainty is actual deployed data quality, effective GCP IAM outside repository declarations, native full-scale resource usage, and live deployment behavior after the newly merged concurrency fix.
