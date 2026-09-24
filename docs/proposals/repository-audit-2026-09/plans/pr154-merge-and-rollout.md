> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# PR #154: clear the merge and production blockers

## Decision and scope

This is a work plan, not approval to implement, merge, migrate, change IAM, or deploy. Keep #154 draft until the merge gate below passes. Every implementation package first returns a detailed plan for supervisor approval; every finished branch is committed, independently reviewed, and explained to the user before a PR/merge decision.

Verified PR head: `362c2604944ab188fb27506b58b881f8059717f6`. Base: `da2f61b28d5d7e1ae10d9aa32cb416769cf90be1`. CI is green, including 94 native geospatial tests. PR #153 is merged. This does not establish production readiness.

The invariant is: an ID already allocated or exposed by an asset is never allocated to an unrelated feature again. This requires both trustworthy historical state and exclusive, durable reservation before publishing new IDs. One allocator and one publication owner must enforce this rule; do not add a second counter store or lock framework.

Affected generated-ID assets: `wdpa-marine`, `wdpa-terrestrial`, `ims-sea-ice-extent`. EAMLIS uses source IDs and needs no sequence migration, although shared-file changes currently trigger its deployment workflow too.

## Findings that drive the plan

1. B's allocator and baseline reader preserve the sequential counter, but the real publishing path does not consume `identity_baseline_snapshot`. The publisher uploads ID-bearing artifacts before committing the manifest. Concurrent stale builders and interrupted publications therefore remain unsafe without ownership/reservations.
2. Merging B triggers all three ingestion deployment workflows. The existing F3a branch blocks those new deployments, but cannot fence existing jobs or historical workflow runs.
3. The three-workflow hold does not isolate all B behavior. `publish-dataset.yml` runs the B-modified manifest validator during finalization after promotion. Its schema preflight does not validate manifest JSON. New strict rejection can therefore occur after copies. A concrete synthetic example is an unversioned generated manifest with a boolean/zero/string next counter that the old validator accepted. No claim is made that such data exists in production.
4. The checked-in IAM configuration gives the general publisher broad canonical write access and constrains federation by repository/environment, not an immutable executor alone. Old writers need an enforced permission boundary; a paused schedule or an empty execution listing is insufficient. Live effective permissions have not yet been inventoried.
5. The offline migration tool verifies supplied evidence, not whether historical evidence is authentic and complete. Current release listings cannot prove that overwritten, deleted, or partially published IDs never existed.

## Milestone A: safe source merge

### P1 — Isolate activation and validate before writes

Proposed branch: `codex/pr154-merge-isolation`. Start from current reviewed main; coordinate integration with #154 without rewriting its history.

Plan-only prompt: "Inventory every caller affected by the PR154 model/reader changes and every workflow that executes them directly from main. Re-review the existing F3a hold against current workflows. Propose the smallest patch that prevents unfinished ingestion activation and ensures every newly introduced manifest rejection occurs before any canonical write. List exact files, retained legacy contracts, affected operations, negative tests, and the operational cost of the hold. Do not implement until PLAN APPROVED."

Required implementation/result:

- Make the deployment hold mandatory before authentication, build/push, Terraform, canary, or Scheduler resume. The existing hold stops all three deployment workflows; explicitly disclose that maintenance-deploy interruption. Running schedules remain unchanged. Narrowing or reopening it needs its own reviewed readiness condition.
- Validate approved staged manifests before the first promotion and bind validation to the exact source generations/bytes subsequently copied. Validate the complete plan before writing any object, not one object at a time. Reuse the existing validator; do not create a weaker parallel validator or a fallback for bad counters.
- Explicitly refuse publishing new generated allocations or sequence-migration/adoption authority for held assets in approved publish plans, manual release tools and all other canonical mutation entry points until ownership and migration authority are ready. Include prebuilt artifacts from another tool and manifest-only migrations, even when the local allocator is never called. Validate the whole plan before writing; test mixed-object ordering and zero writes on refusal without changing unaffected source-ID/legacy publication semantics.
- Prove valid supported legacy and source-ID manifests retain their intended behavior; generated legacy data remains readable where supported but is not accepted as allocation authority. Test malformed and new-version sequence fields, mixed-object plan order, changed source generation, and zero writes on failure.
- Audit localization, manual release tools, catalog/index builders and other direct-main consumers. Record whether they only read, mutate, or allocate. Tests must exercise actual producer/consumer boundaries, not just YAML text.
- Run the full affected suite, all three ingestion suites, native CI, guardrails and lint against the combined current-main + P1 + B content.

Merge exit: no unguarded path can execute the new allocator or publish new generated allocations/migration authority, including prebuilt bytes; direct-main consumers pass compatibility and fail-before-write tests; all CI passes; a reviewer explicitly accepts the deployment hold's operational impact. Then rewrite #154's PR checklist to distinguish source acceptance from production activation. Only at that point may we recommend source merge behind the hold. Otherwise #154 remains draft until Milestone B is complete. The hold alone does not clear this milestone.

## Milestone B: safe production use

### P2 — Establish migration feasibility and the actual writer inventory

Read-only evidence task first; no product branch is required unless reusable repo tooling is needed. Proposed tooling branch, if justified: `codex/pr154-readiness-audit`.

Plan-only prompt: "For the three generated-ID assets, propose a generation-pinned history inventory using existing repo tools, then a live writer/impersonation/deployer inventory. Identify evidence sources, retention gaps, data volume, exact download scope, and read-only commands before collecting large exports. Produce a per-asset READY/NOT READY report without equating tool success with complete history. No source edits or remote writes."

Acceptance evidence per asset:

- True initial allocation, complete allocation-bearing write/delete history through a stated cutoff, retained generations and bytes, partial publications/reservations, prior-format lineage, and any reviewed historical reuse decisions.
- Exact latest/release/metadata anchors and deterministic audit/manifest candidates from `feature_id_sequence_audit.py`. Existing IDs, hashes, artifact references and schemas stay unchanged.
- Independent provenance/completeness review; unresolved history is NOT READY. Do not guess a large counter or infer the ceiling from currently visible rows. If evidence is unavailable, return for an explicit namespace/consumer remediation design.
- Effective writers from bucket/folder/project/inherited grants; runtime identities/images/executions; Scheduler/manual invokers; publisher and Terraform federation; service-account impersonators/keys; deployer and break-glass paths. Include each asset's release-index and schema-state objects, not only its dataset prefix.

Do this before substantial adapter work. The result determines whether rollout is feasible, which job can go first, and the precise permission changes. Repeat the final history delta after old writers are fenced; the preliminary audit is not a frozen migration authority.

### P3 — Define and implement the old-writer permission fence

Proposed branch: `codex/pr154-writer-fence`. Depends on P2's live permission inventory. Infrastructure work stays in reviewed Terraform/protected workflows.

Plan-only prompt: "Design the smallest enduring permission boundary that prevents every legacy writer from creating or overwriting adopted objects or impersonating/deploying a replacement writer. Prefer identity rotation if it yields a simpler proof. Give exact resource/grant changes, trust conditions, propagation/credential checks, adoption identity lifetime, failure behavior, and operational tradeoffs. Include historical/queued workflows and already-running jobs. A sampled readiness flag is not a fence. No implementation until PLAN APPROVED."

Preferred design, conditional on live evidence:

- Remove the old WDPA/sea-ice runtime identities' writes to the adopted roots; run reviewed replacement images under narrow replacement identities. Preserve job names, asset paths, schemas and read access.
- Exclude adopted roots and their exact per-asset catalog state from general publishers that do not participate in ownership. Restrict equivalent inherited grants too; changing one bucket binding is not sufficient.
- Give the adoption producer a separate protected, narrowly scoped authority. Restrict old federation/deployer/impersonation paths so they cannot obtain replacement authority or run old code with it. Exact principal names and policy conditions are design outputs, not invented in this plan.
- Keep legacy write authority revoked after rollout. Pause/drain jobs for operational order in addition to the permission fence.
- Handle permissions propagation and already-issued credentials explicitly. Revalidate authoritative policy and migration anchors before seeding; any invalidated protection halts the rollout. Routine privileged administration must not silently reopen legacy paths.

Acceptance: sandbox policy/integration tests cover old runtime, old workflow, generic publisher, manual principal and indirect impersonation/deployment attempts. Live verification during the later approved cutover confirms those paths are fenced. Do not test denials by attempting unreviewed canonical writes. Unknown authority paths block adoption. Document exact apply allowlists and protected execution; no local production apply.

### P4 — Connect actual generated publishers to durable ownership

Proposed branch: `codex/pr154-owned-publication`. Depend on the reviewed F2 core and B interfaces; re-review that core against current main before reuse. If the patch becomes too large, split pure bundle validation from runtime adapters, with separate plan approval and tests. One agent owns shared publishing files at a time.

Plan-only prompt: "Trace actual WDPA/sea-ice build, publish, skip, run-record, manifest and release-index paths. Reuse B for allocation and F2 for ownership/reservations; submit exact producer/consumer interfaces and deletions. Claim the captured baseline and durably reserve the ID range before the first ID-bearing write. Preserve reservations across crashes and refuse stale or incompatible replays. Keep all unintegrated mutation paths to adopted state unavailable. No second ledger, generic orchestration framework, or optional legacy fallback. No implementation until PLAN APPROVED."

Minimum scope:

- Actual builder→publisher integration with semantic artifact/manifest closure, fixed original generations, durable range reservation, manifest-last completion, and release-index/run/schema effects covered by the same ownership where applicable.
- Stable request identity before upstream discovery; a retry uses its original receipt/executor/configuration and does not silently become a fresh allocation. A new execution is not automatically a retry of an incomplete older publication.
- Define the configured adoption-required asset set independently of state-file existence. For those assets, a missing, partial or invalid adoption record must refuse allocation/publication; it must never select legacy or genesis behavior because `publications/state.json` has not been created yet.
- Conservative failure is acceptable: keep a reservation/claim held and report reviewed recovery instructions if source recovery is impossible. No timeout-based claim stealing or counter rollback.
- For the first rollout, other writers to adopted assets either participate or refuse. Prefer temporary refusal of manual publish/delete/repair, localization, independent index rebuild and generic writes over implementing every F3 adapter. Cover shared catalog-derived effects by explicit routing/ownership, rather than assuming every `_catalog` object has the same role.
- Match those routing refusals to P3 permissions. Explain these temporary feature restrictions to the user before accepting the branch. Source-ID assets and unaffected read paths remain outside this adoption scope.

Required adversarial tests through actual entry points:

1. Two builders observe one baseline: only one can publish; the loser exposes no new IDs.
2. Crash/lost response at each durable transition and after each ID-bearing artifact write: the reserved range persists; a later date cannot reuse it.
3. Delayed workers, original-generation races, changed executor/source/configuration, and repeated requests cannot refresh expectations or take over another claim.
4. Deletion, empty releases and returning identities preserve existing approved behavior and monotonic allocation; source-ID behavior is unchanged.
5. Missing/invalid adoption evidence, changed anchors, false genesis and every bypass entry point refuse before mutation.
6. Retained-version reads and release manifest/index identities agree after completion. No claim of atomic replacement of multiple `latest/` objects.

### P5 — Reviewable adoption producer and rollout rehearsal

Proposed branch: `codex/pr154-adoption-workflow`. Depends on approved P2/P3 authority and P4 persisted contracts. Reuse immutable authorization from #153 and the F2 evidence/receipt/state model; do not invent a second approval system.

Plan-only prompt: "Define an asset-scoped adoption document bound to reviewed historical evidence, exact baseline generations/hashes, counter values, executor, and the enforced writer fence. Prepare a protected seed producer that records evidence→receipt→state with create-only/preconditioned writes and deterministic recovery. Keep the callable production path disabled until its full readiness contract is approved. Show invalidated readiness, competing seeds, partial responses and source-change refusal. No live seeding in implementation."

Acceptance:

- A deterministic dry-run emits exact intended objects, generations, counter changes, identity/grant prerequisites and verification steps. Missing evidence produces NOT READY, never a warning followed by success.
- Before implementation approval, specify the exact ordering and recovery contract for B's two migration-manifest replacements and F2's evidence→receipt→state creation. Record resulting generations under the original approved authority; adoption must pin the final migrated manifest generations. Test interruptions before, between and after the two manifest writes and every seed transition. Partial migration cannot enable allocation, choose genesis, or justify silently refreshed approval/preconditions. The permission fence and adoption-required routing stay effective throughout. This contract is a required design output, not something delegated to an operator to improvise.
- Crash/race tests cover every evidence/receipt/state transition and retry; unchanged approved bytes and original authority are required.
- Rehearse builder, publisher, cutover and recovery in a nonproduction environment using synthetic history and the same reviewed immutable image/toolchain. Verify native artifacts and downstream exact-release reads.
- Produce an operator packet with checkpoint evidence, immutable code/image identities, exact objects, stop conditions, recovery steps, temporary unavailable operations, and expected canary/runtime cost.

### P6 — Approved cutover, one ready job at a time

This is a later execution phase requiring concrete reviewed PRs and protected workflows. Planning it here is not permission to run it.

1. Review/merge infrastructure and software prerequisites while holds remain effective. Account for old/queued workflow versions before opening any new authority.
2. Enforce and verify the permission fence; pause/drain the affected job through the approved procedure. Do not restore old identities on failure.
3. Refresh historical evidence through the now-stable cutoff and verify exact live anchors. Review migration candidates and adoption authority in an explicit publish/adoption PR; use the approved protected publisher, never local canonical writes.
4. Apply the reviewed migration/adoption using exact generations and record every output generation/receipt. An anchor or protection change stops the operation and requires renewed evidence/review.
5. Deploy the reviewed immutable image with its replacement identity under a separately reviewed activation change. Run one canary for the selected job; WDPA's complete enabled asset set must be ready, not just one of its two assets.
6. Verify preserved IDs, the new reserved/committed counters, artifact contents, manifest/index consistency, latest metadata, run record, exact-release consumption and alert coverage. Resume that job's schedule only after acceptance; then repeat for the other job.
7. On failure keep writes/scheduling held, preserve reservations and use the original recovery receipt. Rolling back to the old allocator or resetting counters is prohibited. Keep old releases readable.

## Supervision and branch protocol

- P1 and P2 planning can run in parallel. P3 planning uses P2 authority evidence; P4 can proceed after its exact interfaces and the feasible migration approach are approved. P5 consumes P3/P4 contracts. P6 depends on all rollout acceptance gates.
- Each agent first provides the charter's Gate 1 plan: invariant, exact ownership, minimal solution/alternative, files, compatibility, migration, tests, dependencies, removals and completion evidence. Only a supervisor message beginning `PLAN APPROVED` authorizes implementation. Scope growth returns to review.
- Each implementation uses its own branch/worktree. Dependent branches may be stacked on accepted prerequisites with the dependency disclosed; source remains independently reviewable. Never have two agents editing the same shared workflow/helper concurrently.
- Finish each branch with regression evidence, relevant lint/full affected tests/native CI, a removal pass, clean committed state, exact commit and a handoff. The supervisor reviews the diff and independently challenges failures/concurrency. Explain problem, solution, pros/cons and strongest alternative to the user before the PR decision. No automatic merging.

## What is deliberately deferred

Generalized log ingestion, automatic cross-execution recovery, notification recovery enhancements, adoption of EAMLIS/source-ID assets, broad repair modes, and global multi-object atomicity are not prerequisites for this bounded rollout. Reuse existing mechanisms; accepting a safe stop with durable reservation is preferable to adding a broad recovery framework.

The best simpler alternative is to keep #154 draft until the entire bounded rollout is ready. It avoids a temporary deployment freeze but leaves the existing ID-reuse defect unresolved longer. An offline-auditor-only split is possible, but requires disentangling its model dependency and does not itself fix production allocation. Choose early source merge only if P1 proves it is safe and the user accepts the hold.

## Current unresolved facts

Historical coverage, effective live IAM/federation/deployer permissions, credential propagation, precise cutover resource changes and production canary results are unverified. The planning review made no cloud calls, source changes, production mutations or deployment. The CI fix is already committed/pushed separately. This plan and the supporting text review records are preserved in this archive. Raw execution evidence and temporary worktrees remain outside the repository.

## Independent plan review

The allocator/publisher reviewer confirmed the source-merge contract after requiring an explicit hold on publishing prebuilt generated allocations and manifest-only migration authority. The cutover reviewer accepted the bounded scope after requiring adoption-required routing independent of state existence and explicit migration-manifest/seed ordering. Both corrections are incorporated above. Their review approves this planning direction only; package-level implementation plans and live operational evidence are still required.
