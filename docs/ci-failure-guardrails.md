# Audit failure families and their guardrails

`ci-failure-baseline.json` retains 1,683 distinct workflow runs and 1,688 run
attempts from the original window. It contains 77 failed attempts and three
cancelled attempts, with 81 unsuccessful job observations across those 80
inspected attempts. A rerun is another attempt of the same run; several
unsuccessful jobs in one attempt are not several failed attempts. Preserve the
baseline and its diagnosis limits, including runtime causes inferred from later
repair PRs. All 15 recorded families have a validation or operational contract
below.

| Recorded failure family | Guardrail and delivery step |
| --- | --- |
| Production environment rejected PR ref | PR 1: enumerate immutable reviewed plans after passing main CI; production uses the tested main SHA and original PR acceptance, including the existing restricted self-acceptance rule. |
| Delayed PR event treated as invalid current state | PR 4: verify repository/PR identity and snapshot shape before treating closed, draft or superseded advisory events as no-ops, including open PR head/base drift and changes during routing or preview. Stale events never authorize mutation. |
| PR workflow referenced a script absent from its trusted base checkout | Preserve the merged advisory helper bootstrap and trusted-base checkout contract; land trusted helpers before consumers. Production consumers separately verify an immutable main bootstrap before candidate checkout. |
| PR validation caught content, policy, test or browser defects | PR 2: clean isolated preflight and an always-evaluated `ci-ready` cover every selected suite with pinned tools; failures, cancellations and unexpected skips cannot pass. Migrate required checks only after positive and negative gate verification. |
| Browser teardown race after merge | PR 2: retain the merged teardown fix and execute the browser regression suite in preflight and CI, without skips or retries. |
| Manual probe exposed secret-scan or container packaging problems | PRs 2–3: full-history secret scanning and actual-image entrypoint/native interpreter fixtures run before release. |
| Manual benchmark had insufficient host disk | Preserve the manual benchmark's explicit 100 GiB scratch admission check. Preflight fails when tools or validation commands cannot run; WDPA admission independently checks retained acceptance and approved production resources. Local validation does not promise future runner capacity. |
| Deployment started without required rollout prerequisites | PR 3: offline reset/evidence/fingerprint/dependency contracts and trusted live readiness checks precede dependent mutation; paused or busy ingestion targets defer before mutation. |
| Missing IAM permissions or incomplete propagation/dependency contract | PR 3: shared ingestion IAM bootstrap runs once, followed by live dependency checks. Narrow target bootstraps and saved-plan action permission probes precede dependent mutation; preserve the existing metadata-retirement deletion-permission probe. |
| Deployment verifier assumed the wrong checkout/plan representation | PR 3: full checkout history, explicit tested SHA, real provider bucket representations and saved-plan lineage have regression fixtures. |
| Publication/reset/canary state contract mismatch | PRs 1 and 3: preserve immutable plan authorization, persisted transaction ownership and generation preconditions; test completed, interrupted, partial-realm and same-day outcomes. |
| SDK publisher tried to push version edits directly to protected main | PRs 2–3: preserve the merged version-policy fix, validate Node 22/24 and package contents, and publish only the exact tested tarball. |
| Cancelled or timed-out run | PRs 3–4: superseded PR validation may cancel; production queues never cancel active mutations automatically. Owner-cancelled benchmarks and setup/runtime timeouts remain separate outcomes. Incomplete records block replay until reconciliation. |
| Controlled alert probe deliberately stopped for human verification | PR 4: separate the negative control from ordinary deployment; prove the expected terminal failure and require explicit delivery evidence. |
| External service or runner failure | PR 4: retain visible external failures and selective recovery guidance; inspect mutation state before recovery and never blanket-retry production. |

## October 7 boundary regressions

The original audit baseline remains unchanged. The tested revision passed all
selected validation suites before main CI run
[37647645882](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37647645882)
and its alert run
[37651180028](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37651180028)
exposed three missing contracts:

| Failure | Required regression boundary |
| --- | --- |
| A disabled logging block introduced a telemetry bucket into a narrow ingestion IAM plan | Pinned Terraform's actual dependency graph must keep every narrow target inside its reviewed ownership/prerequisite closure. Both disabled and enabled historical configurations must reject the leaked edge. The apply allowlist remains narrow; selected logging-owner deployment completes before shared-bucket IAM consumers. |
| The usage worker called `.get()` on the SDK's valid absent-logging `None` value | Real SDK representations and the actual collection-disabled entrypoint run in Python tests and the immutable production image. Disabled collection remains unverified; malformed configuration, failed reads and incompatible persisted state remain errors. |
| Slack permalink lookup used JSON POST instead of GET parameters | Offline HTTP-boundary tests verify posting, lookup, thread routing and parent updates. An acknowledged message survives permalink failure and resumes without reposting. Recovery edits the original parent; thread replies cannot broadcast into the channel. |

After main CI [37692734702](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37692734702)
passed all seven selected suites, notification run
[37693399887](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37693399887)
rejected an unchanged signed claim because posting its status advanced GitHub's
deployment `updated_at` by one second. CLI delivery tests now exercise that
prepare/status/read boundary: only `updated_at` may differ, every other claim
field remains exact, and signed status verification still precedes Slack writes.
State-machine tests alone did not cover this CLI comparison.

Passing a fake service response or reconciling zero incidents does not prove the
corresponding live boundary. Preserve the observed failure states in fixtures,
exercise startup with deliberately incomplete operational configuration, and
keep expected negative controls separate from ordinary deployment outcomes.

## Additional guards established during implementation

The audit records eight advisory routing failures under a combined identity/state
error; it does not distinguish closure from head or base drift. Advisory routing
and preview now recheck validated PR snapshots around file enumeration and plan
discovery. A verified obsolete event emits false mutation/plan outputs and writes
no plan or authorization files. Malformed API data, incomplete enumeration and
invalid plans on an unchanged current PR still fail. These advisory no-ops do not
relax production's exact-head approval, immutable-plan or authorization checks.

Playwright's optional Git capture can fetch depth-one history in CI and leave a
passing browser run with a shallow checkout. Disable that optional capture;
preflight owns revision evidence. Browser validation runs with `CI=true`, and
complete-history and clean-checkout checks remain before and after each selected
suite. Diff guards, secret scanning and ancestry authorization retain their
history requirement.

Actual production-image fixtures exercise container entrypoints before release.
The geospatial fixtures check GDAL's actual interpreter, NumPy and pinned GDAL,
Tippecanoe and PMTiles versions; the viewer fixture checks its default entrypoint
and health endpoint. EAMLIS, sea ice and viewer deployments consume retained
tested archives rather than rebuilding after validation. The archive's config
digest and ordered root filesystem hashes prove its runtime bytes. A Docker
daemon's local image ID may identify a manifest, so it serves only as a local
test/load/tag reference. Retained proof, registry config checks and deployment
records use the canonical config digest. Strict single-image normalization and
cross-daemon verification preserve the config and layers. WDPA retains its
separately accepted private producer and staging evidence and exact-image checks.

Retained acceptance also requires available bytes. Selected `wdpa_processing`
validation must check live staging-artifact availability and reviewed ZIP identity
before dependent deployment; static acceptance metadata alone is insufficient.
Its accepted benchmark image archive, artifact 11263851699, expires on October 10,
2026 at 04:03:21 UTC. Admission requires more than 24 hours remaining for the queue
and download. Its verified uncompressed archive is 1,381,262,848 bytes, within the
2 GiB archive bound. An unavailable, near-expiry, expired or mismatched staging
input must fail admission visibly. Monthly WDPA promotion instead uses its
approved persistent registry image; this staging-archive expiry does not apply to
that image. Renewed staging evidence must retain the review, source fingerprint
and acceptance requirements; rebuilding or replacing it implicitly cannot
preserve acceptance. These availability checks grant no mutation authority.

## Seven-day comparison

Start the observation window when the guardrails and required-check migration
are active. Preserve the two-week baseline and collect the seven-day results
separately. The baseline's window uses original run `created_at`, including for
reruns; it does not contain each attempt's start time. Use that convention for
comparable workflow counts. An attempt-start window requires a separate inventory.

- **Workflow attempts:** identity `(run_id, run_attempt)`. Count every conclusion
  and rerun; report unique runs and latest conclusions separately. First-attempt
  outcomes use attempt 1 of every eligible run, never its post-retry conclusion.
  Show success, failure, skipped, cancelled and pending separately.
- **Preventable main validation failures:** failed eligible main-push validation
  attempts with a reproducible repository cause, divided by all eligible
  main-push validation attempts in the same window. Retain raw all-cause counts
  beside the classified rate.
- **Deployment outcomes:** each selected target job attempt, recording target,
  executor SHA, source CI attempt, execution run/attempt and job ID. An eligible
  attempt has passed source validation and entered release admission; show jobs
  skipped by an earlier gate separately. Keep automatic main releases and manual
  recovery in separate cohorts. Divide preventable failures by eligible target
  deployment attempts in that cohort. Consolidation can
  combine several targets in one CI attempt, so raw workflow failure rates alone
  are not a comparable deployment metric. Report successful mutation,
  superseded/no-op, prerequisite rejection and terminal runtime verification
  separately; do not claim target success from the enclosing CI conclusion.
- **Recurring signatures:** repeated diagnosed signatures by family and target,
  alongside affected workflow attempts and unsuccessful-job observations.
  Several bad jobs in one attempt must not inflate its workflow failure count.
- **Jobs:** [the main-push allocation companion](ci-job-allocation-baseline.json)
  covers all 357 original main-push runs, 358 attempts and 67 distinct main head
  SHAs: 755 API-created jobs and 553 jobs that acquired a runner and started within
  the window. That is 11.27 created jobs and 8.25 runner-allocated jobs per observed
  main head. Creation and runner-start timestamps were checked; no post-cutoff
  attempts, job creations or runner starts enter those counts. One allocated job
  completed after cutoff, so its captured conclusion is retrospective. The
  original baseline's 81 unsuccessful observations remain unchanged. PR events,
  closed-PR publishers, schedules and `workflow_run` cascades are outside this
  companion; their allocation baseline remains unavailable. Compare the same
  event cohort per head SHA and per day, rather than raw 14-day and 7-day totals.
  Head SHA is a push-event proxy, and allocated jobs do not measure distinct
  machines or billable minutes.

Keep legitimate premerge rejections, external outages/no-runner incidents,
owner cancellations/timeouts, expected probes and asynchronous runtime failures
visible as cause/outcome categories. A cancelled job inside a failed workflow
is not automatically external: classify its observed cause. A controlled probe
is expected only after matching its exact negative outcome and delivery evidence;
unexpected failures or missing delivery remain failures. A started execution is
verification pending until its terminal outcome is proven. Classification does
not turn raw failures into success.
