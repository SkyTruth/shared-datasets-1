> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

**Final snapshot reconciliation by the primary reviewer:** PR #146 merged during this review as `e2a29ea0a5ecd7d7688158a67f25e88400d4224f`; the final checkout is clean main. Terraform queue and wrapper-detection findings in the initial snapshot are resolved in code by that merge. Final equivalent-tree verification: 738 Python tests passed, 4 skipped, 427 subtests passed; Ruff passed. The detailed observations below retain their original snapshot context. No implementation changes were made by the review agents.

# Operations, ingestion, infrastructure, and workflow review

Read-only review on 2026-09-22, initially against clean commit `c69850b`. During the review, another actor changed the shared checkout to `fix/share-sync-lane` at `aee6fd5` and edited 19 tracked files. This reviewer made no GCP, infrastructure, Git index/history, or source-file changes. The only authored artifact is this report. Findings O3/O5 have been reconciled below against that work in progress; O4 remains open. Other line references describe the initial reviewed snapshot unless explicitly stated. Loaded invariant-first-engineering, protected-terraform-apply, deploy-scheduled-ingestion, and local-temp-workspaces instructions. Production recommendations below mean changes through reviewed PRs and protected workflows, never local apply.

The implementation already has useful boundaries: generation-preconditioned writes, immutable dated releases, explicit publish plans, separated runtime identities, protected workflow environments, release manifests, schema checks, and focused pipeline tests. The largest remaining risks arise where these boundaries are not composed into a complete transaction or lifecycle.

## Verified defects and concrete gaps

### O1 — P1: Scratch cleanup can delete a new, unpublished proposal

- Evidence: `scripts/scratch_cleanup.py:148-165` considers a proposal published if **any one** data object has the same basename, size, and CRC32C as any historical release. `:204-205` then selects deletion immediately, without age, PR status, or publication receipt. `:391-395` deletes **all** proposal objects. `.github/workflows/scratch-cleanup-audit.yml:80-95` uses `--apply` on the weekly schedule.
- Trigger: stage an unchanged FGB alongside a changed README, metadata sidecar, schema, or manifest. Before the new proposal is approved/published, cleanup sees the old matching FGB and removes the entire proposal. It can also collide with a partially completed publish.
- Reproduction: pure-function call using two brand-new scratch records (matching FGB + unpublished README) and one historical release FGB produced `age_days: 0.0, action: delete, reason: matching-release`.
- Fix: require a completed promotion receipt tied to the proposal/plan digest, exact source generations, and every intended destination. Respect pending PRs/in-progress publish leases. An artifact content match is not a transaction completion signal.

### O2 — P1: An older ingestion backfill can replace current `latest/` while the index still advertises the newer release

- Evidence: `ingestion/common/vector_pipeline.py:154-203` unconditionally replaces every latest artifact for the requested run date. `ingestion/common/release_index.py:396-405` sorts release dates and chooses the maximum for `latest_release`. The date override is a supported workflow input in `.github/workflows/wdpa-monthly-deploy.yml:31-35,273-279` and EAMLIS likewise.
- Trigger: publish a missing older release after a newer release exists. The immutable index selects the newer release, but direct latest downloads and future generated-ID baselines read the older one.
- Fix: explicit backfill/release publication separate from latest promotion, with latest advanced only under a checked monotonic release invariant (or explicit rollback operation). Resolve generated identities against a declared baseline release, not whichever mutable latest happens to exist.

### O3 — P1/P2: Terraform lanes still race between planning and applying

- Current status: already being addressed in [PR #146](https://github.com/SkyTruth/shared-datasets-1/pull/146) and additional uncommitted changes. The committed PR branch shared only state-sync lanes; the newer local edits place all seven production writer jobs in literal `prod-terraform-state` with `queue: max`, holding the job across plan/check/apply. That directly addresses this race once reviewed, merged, and deployed. Do not open a duplicate fix. Root independently inspected the Cron alert policy sync failure and confirmed `Saved plan is stale` in live workflow logs.

- Evidence: `.github/workflows/prod-terraform-target-apply.yml:125-167` plans, exports, checks, then applies a saved plan. Workflows now have independent concurrency lanes. `scripts/terraform_retry.sh:26-28,47-50` retries lock acquisition/release messages only; it does not replan stale saved plans.
- Trigger: two workflows finish plans against the same state serial; one applies first. The other's apply rejects the saved plan. Retrying the same apply would not repair it. This is a distinct failure from the GCS lock-creation race that the wrapper addresses.
- Primary confirmation: [Terraform 1.8.5 source, saved-plan serial check](https://raw.githubusercontent.com/hashicorp/terraform/v1.8.5/internal/backend/local/backend_local.go) rejects a saved plan when its prior-state serial differs from current state.
- Fix: the current shared job queue is the smallest direction. GitHub now supports up to 100 waiting jobs via [documented `queue: max`](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency), which removes the previous single-pending cancellation tradeoff within that limit. Drain old-config writers during migration and observe post-merge serialization. Splitting genuinely independent states or a fully checked transaction retry remains a later alternative if queue delays become material.

### O4 — P2: WDPA's deliberately skipped canary makes the deployment fail

- Current status: still present after the concurrent edits. The watch is now at lines 286-295 and still has no `if`; the execution step alone is conditional.

- Evidence: `.github/workflows/wdpa-monthly-deploy.yml:259-283` conditionally executes and sets `WDPA_CANARY_EXECUTION`; `:285-296` unconditionally watches it under `set -u`.
- Trigger: the default in-flight execution guard chooses `run_canary=false`, which is the intended non-error behavior. The following watch expands an unset variable and exits before checking anything.
- Reproduction: parsed workflow confirmed the watch has no `if`; executing the same unset-variable expansion under Bash exited 127 with `WDPA_CANARY_EXECUTION: unbound variable`.
- Fix: carry a typed canary result (`started`, `skipped`, `cancelled/replaced`) and gate watch/post-validation on the applicable state. At minimum, apply the execute condition to watch. Add an end-to-end stubbed skipped branch test.

### O5 — P2: Production Terraform safety scanning no longer recognizes any current apply command

- Current status: addressed in uncommitted work by another actor. `scripts/repo_guardrails.py` now parses workflow jobs, recognizes wrapper commands, and checks job-level queue/guard policy. Treat the finding as a verified initial regression with a pending fix; root is validating the evolving worktree. No live deployment of that fix was claimed.

- Evidence: `scripts/repo_guardrails.py:133` only matches literal `terraform -chdir=... apply`, while all production apply workflows now invoke `bash .../terraform_retry.sh -chdir=... apply`. Therefore `:467-475` does not enforce its main-ref, concurrency, or allowlist checks.
- Reproduction: `TERRAFORM_APPLY_RE.search` returned false for all seven current production workflows: reusable target apply, three ingestion deploys, catalog viewer, metadata service, and PMTiles CDN sync.
- Fix: validate structured workflow jobs against the approved reusable apply entry point, with explicit knowledge of wrappers. Add regression cases for actual checked-in workflows and for removal of each required guard. Avoid a new brittle string alias list as the only safety boundary.

### O6 — P2: Scheduler changes trigger deploy workflows but are never included in their plans

- Evidence: `.github/workflows/wdpa-monthly-deploy.yml:17-23` watches WDPA Terraform and scheduler-module files; `:158-164,179-193` targets and permits updates only to `module.wdpa_monthly_job.google_cloud_run_v2_job.this`. EAMLIS and sea-ice deploys do the same. Schedulers are distinct sibling modules (`terraform/envs/prod/wdpa_monthly.tf:77-92`, `eamlis_monthly.tf:76-91`, `sea_ice_daily.tf:75-90`). No checked-in workflow targets these scheduler resources. The IAM sync named “scheduled ingestion deploy” only handles the custom deployer role/binding.
- Impact: a reviewed cron/timezone/retry change can merge, trigger a green rebuild, and leave live scheduling unchanged. New jobs/service accounts/owned-prefix IAM similarly lack a complete ordinary protected bootstrap path.
- Fix: create a declarative ownership/coverage map of Terraform resource classes to protected workflows; add the constrained scheduler/bootstrap path and detect uncovered changes in CI. Keep permissions least-privilege and review the planned live resources.

### O7 — P2: Advertised canary overrides/cancellation are missing from the declared deployer permissions

- Evidence: `terraform/envs/prod/scheduled_ingestion_deploy_iam.tf:6-17` grants basic run/get/update but neither `run.jobs.runWithOverrides` nor `run.executions.cancel`. Workflows execute overrides and cancellation, e.g. WDPA `:250-251,279-281`.
- Primary confirmation: [Cloud Run execution API](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs/run) requires `run.jobs.runWithOverrides`; [Cloud Run execution guide](https://docs.cloud.google.com/run/docs/execute/jobs#cancel-job-execution) states cancellation has required `run.executions.cancel` since September 2024. The older generated cancellation API reference contradicts the latter; use the maintained guide and verify with IAM permission checks.
- Scope: verified gap in repo-declared custom role, not a claim that the live principal lacks every possible inherited/external grant. No live IAM inspected.
- Fix: a reviewed, narrowly scoped role change and a workflow capability preflight that validates every exposed input path.

### O8 — P2: “Last check-in” discards the actual attempt date

- Evidence: `ingestion/common/release_index.py:333-337` sets latest-run `date` from `release_date`, ignoring `run_date`. WDPA intentionally stores actual attempt date in `run_date` and the stable first-of-month target in `release_date` (`ingestion/wdpa_monthly/run.py:1082-1084,1136-1138`). Sea-ice uses the same distinction (`ingestion/sea_ice_daily/run.py:761-762`).
- Reproduction: record with `run_date=2026-09-22` and `release_date=2026-09-01` becomes latest-run `date=2026-09-01`.
- Impact: downstream check-in displays and monitoring cannot distinguish today's healthy skip from a job that stopped running three weeks ago. Rebuild also sorts records by release date (`release_index.py:501-503`), compounding the ambiguity.
- Fix: version run/index schema with explicit `attempted_at`, `target_release`, `status`, and `execution_id`; order runs by attempt time, releases by source release date. Provide a narrow persisted-schema migration rather than silently changing the meaning of one overloaded date field.

### O9 — P2: Complete upstream sea-ice outages are returned as successful not-ready skips

- Evidence: `ingestion/sea_ice_daily/run.py:192-199` accumulates exhausted transient probe failures and continues; `:204-207` returns no source, and `:727-739` writes a skipped index and returns normally. `no_available_source_record` uses the same unavailable reason for all-404 and all-network/5xx outcomes (`:219-238`).
- Impact: a provider/network outage across all 14 dates looks like a normal successful check-in; project job-failure alerts do not fire. There is no freshness-age alert policy in `terraform/envs/prod/monitoring.tf` to catch this different failure mode.
- Fix: distinguish not-ready from exhausted transient failures. Preserve controlled grace windows for expected provider delays, but fail or raise an operational degradation state when no candidate could be assessed. Add per-asset maximum acceptable age and missing-check-in detection.

### O10 — P2: Geospatial CI does not run for some of its core implementation dependencies

- Evidence: `.github/workflows/ci.yml:156` includes `vector_asset`, localization, raster, and alerts, but excludes `scripts/release_feature_model.py`, `scripts/pmtiles_zoom.py`, and their tests. Both are copied into every ingestion image and used in FGB/PMTiles identity/validation paths.
- Impact: a change isolated to these modules can pass ordinary tests while the native geospatial suite is skipped.
- Fix: declare/test the integration dependency closure or run the inexpensive changed-module part of the native suite on all Python/core/schema changes. Native test selection should depend on what the tests exercise, not a manually drifting filename subset.

### O11 — P2: Production ingestion images ignore the Python lockfile they copy

- Evidence: `ingestion/wdpa_monthly/Dockerfile:39-40`, EAMLIS `:39-40`, sea-ice `:40-41` copy `uv.lock` but perform unversioned `pip install google-cloud-storage`. GDAL and Tippecanoe also come from mutable Debian repositories; base images use tags.
- Impact: the same reviewed commit can rebuild with different Python/native dependencies from CI or the prior deploy; upstream changes can introduce failures without a code change. Native serialization differences can change generated bytes/hashes.
- Fix: use the repo-owned locked runtime install (a production dependency group/export if keeping images small), pin/rebuild native toolchain deliberately, record image/source/tool digests. The three nearly identical Dockerfiles are a natural shared-base target.

### O12 — P2: Deployment image tags can identify a different commit than the built source

- Evidence: deployment workflows check out moving `main` but derive tags from the triggering `GITHUB_SHA`: WDPA `.github/workflows/wdpa-monthly-deploy.yml:74-77,108`, metadata service `:102-105,135`, catalog viewer `:66-70,101`.
- Trigger: main advances while a run waits for an environment approval, runner, or concurrency lane. Checkout builds newer main with the older trigger SHA in the image tag. Reruns can rebuild a different source under the same tag (especially the two service workflows without run IDs).
- Fix: resolve a trusted commit once, verify ancestry/review policy, and use that exact SHA throughout checkout, image labels/tags, plan, receipt, and canary. If deliberately deploying latest-main, derive labels from the actual checked-out HEAD and record both trigger and deployed revisions.

### O13 — P2: Streaming builders still hold full previous releases in memory

- Evidence: `ingestion/common/gcs.py:66-95` downloads compressed sidecars as one byte buffer and materializes all decoded records as a list. WDPA `run.py:1107-1109` loads **all pending assets'** baselines before source download, and keeps the dictionary live through both builds. `feature_metadata.py:418-479` creates compact identity baselines but deleting that local variable does not release the callers' full record lists.
- Impact: multi-million-row WDPA can retain full prior property payloads for both assets alongside current build intermediates, undermining the stated bounded-memory conversion design. This is a demonstrated allocation pattern, not a measured OOM claim.
- Fix: stream sidecars into a compact identity-only on-disk index; preflight decisions against that index, then build one asset at a time. Release references after each asset. Use memory-budget fixtures that exercise the actual GCS loading/orchestration boundary, not only the inner writer.

## High-value design and workflow opportunities

### O14 — P1/P2: Give ingestion publication a durable, resumable transaction

`vector_pipeline.py:131-203` writes release artifacts, then latest artifacts, then manifest, and each pipeline writes the success record afterward. `gcs.py:228-253` rejects any partial release on rerun. A crash after one latest replacement can leave a mixed current bundle and an intentionally unretryable date. Current refusal is safer than overwriting, but recovery remains a manual reviewed repair. Use a manifest-last commit marker over immutable artifacts, a small generation-CAS current-release pointer, and a checkpoint ledger proving which bytes/generations already exist. Resume only verified identical operations; do not add blind overwrite/retry. Retain public compatibility while teaching consumers to resolve the pointer once.

### O15 — P2: Move reusable deployment mechanics out of copied YAML

Three ingestion deploy workflows independently copy image build/digest resolution, Terraform allowlist Python, in-flight detection, cancellation, and execution. EAMLIS/sea-ice also contain large inline metadata/PMTiles validators. Their divergence has already produced O4. Extend the existing reusable IAM apply pattern into a constrained ingestion deploy workflow plus tested CLI helpers. Keep source-specific resource policy/data declarative; do not create an unrestricted generic deploy wrapper.

### O16 — P2: Make resource/deployment coverage executable

Keep one manifest listing each job/service: source package, image build closure, Terraform resources, runtime identity, bucket roots, release strategy, scheduler, post-deploy checks, and alert contract. Generate or validate workflow path filters, allowlists, IAM permission checks, docs, and test selection from it. This removes duplicated package/file/resource inventories and catches O6/O7/O10 together.

### O17 — P2: Make workflows test behaviors, not just their text

`tests/workflow_helpers.py` and workflow tests assert many exact names, strings, target sets, and YAML layouts. These are useful policy checks but missed unset canary state and wrapper-disappearing guards. Extract shell/Python into testable commands, run workflow script branches against stub CLIs, validate YAML with an Actions-aware validator, and use mutation tests that remove a safety boundary and expect rejection. Add two-concurrent-deploy, partial-publication, skip, changed-PR-body, and resumed-plan scenarios.

### O18 — P2: Persist machine-readable operational receipts and a single status command

The deploy workflows frequently stop at image match, raw CLI logs, or an async canary watch. Produce a receipt containing trusted commit, image digest, Terraform resource changes, canary execution ID/state, release date and generations, index status, alert verification, and remaining checkpoints. Provide a read-only `doctor/status` command and a resumable `verify --receipt` action. LLMs then inspect explicit state instead of reconstructing a workflow from stdout and chat history.

### O19 — P2: Represent source extraction as a stable snapshot

EAMLIS captures source editing metadata/stats once (`run.py:218-247`), then uses offset pagination (`:278-305`) without a final fingerprint comparison. Provider edits during extraction can mix source versions even when the final count is unchanged. Prefer a provider historic snapshot if supported; otherwise freeze object IDs, page by IDs/keyset, and verify editing timestamps/stats before and after. Abort changed snapshots before publication. This is a concrete missing consistency check, not evidence the current public source changed during a run.

### O20 — P2: Use one release/run schema and separate attempt identity from release identity

Jobs duplicate output dataclasses, success/skip record construction, read/repair logic, and validation rules. EAMLIS has a separate write-once run-record implementation (`run.py:573-607`); WDPA/sea-ice skips often only replace latest-run index, losing durable attempt history. A versioned `RunAttempt` plus `ReleaseBundle` model should own required fields, statuses, counters, baseline, and artifact roles. Source-specific jobs only produce source state and artifacts. This centralizes O8 and makes failed/blocked/unchanged/not-ready outcomes observable without conflating release dates.

### O21 — P2: Add freshness/error-budget monitoring rather than only failure logs

Current monitoring covers execution failures, scheduler failures, identity pauses, service errors, and unexpected writes/deletes. Missing executions, disabled schedulers, endless not-ready skips, and long-running stuck jobs can remain green. Establish per-asset freshness expectations, expected next attempt, last successful source release, blocked-since time, and elapsed stage. Surface these to maintainers and downstream consumers, with actionable alerts only after each declared grace period.

### O22 — P2: Turn identity decisions into a checkpointed workflow

Identity ambiguity evidence is logged and sent in Slack (`feature_metadata.py:554-634`), then the local temp build is discarded when `IdentityDecisionRequired` exits successfully. Future attempts may redownload/rebuild a huge source just to discover the same ambiguity. Persist a small deterministic decision packet keyed to source digest, baseline generation, policy version, and proposed IDs; give maintainers a CLI to validate/apply reviewed choices and resume from safe immutable build checkpoints. Deduplicate notifications by packet digest. Preserve the explicit human decision boundary.

### O23 — P3: Expose runtime progress without accumulating native stderr

`ingestion/common/runtime.py:54-59,76-77` pipes every subprocess's stderr into memory and normally logs it only at debug after completion. Large native conversions can appear silent for hours, and diagnostic output accumulates. Stream structured stage/progress logs while retaining a bounded error tail; attach durations, memory/disk peaks, and tool versions to the run receipt.

### O24 — P3: Make the local work root consistent and preserve only useful checkpoints

Jobs use bare `tempfile.TemporaryDirectory(prefix=...)` (`wdpa:1124`, EAMLIS `:814`, sea-ice `:783`) rather than the repo-specific work-root resolver. Introduce one shared work-directory utility supporting local and Cloud Run contexts, per-stage paths, capacity checks, and explicit retained diagnostic/checkpoint files. Do not broadly delete existing user temp directories. Test helper `tests/test_terraform_retry.py:42` also creates directories without cleanup; use scoped fixtures.

### O25 — P3: Retire persisted-format compatibility through an explicit migration inventory

WDPA supports legacy metadata IDs by catching a broad RuntimeError, re-downloading the sidecar, and attempting legacy adaptation (`run.py:747-774`). This may still be necessary for real persisted data, so do not delete it speculatively. Inspect/version the baseline contract once, use a precise legacy reader, record remaining legacy generations, and delete the bridge after all supported assets are migrated. The goal is fewer implicit fallback paths, not reduced compatibility by accident.

## Cross-area confirmation sent to the parent reviewer

- Scheduled ingestion updates dataset objects and `_catalog/releases/{slug}.json`, not the catalog CSV. SDK caches keyed only to static CSV dates therefore need independent release-index/generation freshness.
- Public GCS artifact URLs are not inherently broken by `shared_bucket_public_object_viewer_enabled=false`: `terraform/envs/prod/shared_bucket_public.tf:47-56,96-114` still declares per-public-asset managed-folder allUsers read access. Live access was not inspected.
- The root's mutable-PR-body issue is confirmed: publish dispatch fetches current PR JSON at `.github/workflows/publish-dataset.yml:186`, reparses body at `:198-210`, but gate outputs only plan-presence flags; `scripts/publish_workflow.py:122-133` accepts any historical approval without binding commit or plan digest. Branch protection does not cryptographically bind edited PR prose. A checked-in reviewed plan and digest-bound receipt are the right structural direction.

## Validation and limitations

- Inspected ingestion/common, all three job pipelines/Dockerfiles, production/preview Terraform and core modules, workflow inventory and major deploy paths, operational scripts, and representative tests.
- Pure local reproductions confirmed scratch cleanup deleting a fresh mixed proposal, all seven current production applies escaping the apply regex, check-in date loss, and the skipped WDPA canary unset-variable failure.
- Root ran the full suite and lint separately; this reviewer did not rerun that workload.
- Root also inspected live GitHub governance: active main ruleset `18755592` requires a PR but zero approvals, does not require CODEOWNER review, does not dismiss stale reviews, and requires `lint`, `tests`, and `geospatial-changes` but not `geospatial-integration`. The production environment has branch-policy protection but no required reviewer. These root-verified settings mean the documented human-review boundary and native-integration merge gate are not currently enforced; this reviewer did not repeat the live queries. They should be explicit, permissioned administration work, with current settings captured and post-change checks.
- No live GCP resources, bucket contents, current IAM bindings, branch protection settings, actual GitHub execution history, full production-size memory profiles, or native conversion pipelines were exercised. P1/P2 findings above describe provable source behavior and explicitly labeled runtime risks, not claimed observed production incidents.
- Invariant-first recommendation: represent **reviewed bytes**, **completed publication**, **current release**, **run attempt**, and **deployment transaction** once, with explicit immutable identity and state. Move checks to their creation/commit boundaries; then remove downstream repairs, duplicated YAML, weak string scanners, and repeated inference. No code or fallback was added/removed in this review.
