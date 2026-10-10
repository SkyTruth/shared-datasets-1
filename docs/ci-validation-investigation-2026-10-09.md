# Concurrent PR validation investigation, October 9, 2026

The strongest waste evidence is premature final validation and changing a
validating checkout, with five simultaneous local drivers on one small Docker
VM. Hosted image failures are an external boundary; available evidence cannot
attribute Docker Hub's throttling to these agents.

Scope: PRs [247](https://github.com/SkyTruth/shared-datasets-1/pull/247),
[248](https://github.com/SkyTruth/shared-datasets-1/pull/248),
[249](https://github.com/SkyTruth/shared-datasets-1/pull/249) and
[250](https://github.com/SkyTruth/shared-datasets-1/pull/250), plus merged
[246](https://github.com/SkyTruth/shared-datasets-1/pull/246) as a terminal control.
The cohort Actions snapshot includes runs created through 21:26:51 UTC.
All four cohort PRs remained open when rechecked; observed time to merge is
therefore unavailable, not zero. No other chat was messaged, PR merged,
automation changed, workflow retried or production operation performed by this
investigation. Raw API responses, image-job logs and local traces are retained
outside the repository. Portable [measurement data](ci-validation-investigation-2026-10-09.json)
records the exact heads, bases, trees, attempts and evidence directory names.

## Trace and baseline

| PR | Local full-preflight attempts / successful final records | Attempts on final observed head / successes | Hosted CI runs + retries | Unique runner minutes |
| --- | ---: | ---: | ---: | ---: |
| 247 | 3 / 3 | 1 / 1 | 2 + 1 | 19.417 |
| 248 | 2 / 1 | 2 / 1 | 1 + 1 | 11.867 |
| 249 | 3 / 1 | 2 / 1 | 1 + 1 | 11.300 |
| 250 | 3 / 1 | 1 / 1 | 1 + 1 | 11.817 |
| Total | 11 / 6 | 6 / 4 | 5 + 4 | 54.401 |

These are retained invocations that produced a plan, including interrupted or
failed attempts. A suite pass alone is not a successful preflight. One additional
attempt (#250, head `282d3a37`) passed all seven suites but failed the final
source-identity check; it correctly produced no passing final record.
Unretained or pre-plan invocations cannot be exhaustively counted.

Runner minutes sum unique job execution intervals from
`actions/runs/{run}/attempts/{attempt}/jobs`, excluding skipped jobs.
GitHub repeats earlier successful intervals under new job IDs on selective
reruns; deduplication uses run ID, job name and both timestamps. Summing every
attempt's displayed jobs would incorrectly report 90.233 minutes.
These are observed cumulative execution minutes, not billed rounded minutes
or CPU-seconds. The four selective retries consumed 4.917 additional runner
minutes; they did not rerun the five previously passing validation suites.

- **#246:** opened 20:18:34 UTC, first hosted run
  [37986109269](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37986109269)
  passed on `7acdc165`. Strict current-main checks required a main merge,
  producing `1c9ba462`; its local lint/Python preflight and hosted
  [37987070893](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37987070893)
  passed. The PR merged normally at 20:32:39, **14m05s after opening**.
  Main CI [37987706619](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37987706619)
  passed on `a302940e`. This advanced other PRs' comparison base from
  `aa77eb89`.
- **#247:** began full preflight on `55d1b357` against `aa77eb89`
  before #246 merged; all seven suites passed in 17.49 local elapsed minutes.
  A second complete run against `a302940e` took 17.79 minutes.
  After the first push/opening at 20:58:14, hosted
  [37990503028](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37990503028)
  and selective attempt 2 failed on image metadata requests. A strict-main
  branch merge produced `65716526`; its tree exactly matched the previous
  tested prospective merge, but its head identity changed. A third local
  run took 14.99 minutes and passed before the second push. Hosted
  [37993173600](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37993173600)
  failed at the same external image boundary. No successful merge was observed.
- **#248:** `1e3454d9` initially validated against `aa77eb89`; six suites
  passed and browser failed. Current-base preflight against `a302940e`
  passed all seven in 15.63 minutes before push/opening at 21:02:38.
  Hosted [37990986397](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37990986397)
  attempts 1/2 failed image jobs; merge work stopped at required failing checks.
- **#249:** old-base `2962e651` preflight completed lint/Python but was
  interrupted after a documentation commit created `c78c9beb`.
  Another old-base run on that head was interrupted without suite results.
  The current-base complete run passed all seven in 19.69 minutes before
  push/opening at 21:04:15. Hosted
  [37991153574](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37991153574)
  attempts 1/2 failed image jobs. The owner then created the authorized
  15-minute heartbeat “Merge CI selection after other PRs,” explicitly waiting
  for #247/#248/#250 and the identity chat (subsequently #251).
- **#250:** old-base `1175fc20` preflight found a real Python failure after
  lint/native checks. Focused test migration was approved and committed.
  Current-base `282d3a37` ran every suite successfully, then failed with
  `base or head changed during preflight; evidence is stale` because HEAD
  had advanced to `c835999f`. The exact final-head run passed all seven
  in 14.16 minutes before push/opening at 21:17:06. Hosted
  [37992502111](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37992502111)
  attempts 1/2 failed before image tests, including token-service 504/timeouts.

Local elapsed numbers use host plan/final-record timestamps and agree closely
with retained command durations. There is no reliable cumulative local CPU
measurement. The failed #250 full run's command lasted about 16.20 minutes.
The #247 current-base run plus subsequent administrative head update would
have cost one run instead of two had synchronization preceded final validation:
**17.79 local process-minutes potentially avoided**. Keeping #250's checkout
frozen would potentially avoid another **16.20 process-minutes**. The combined
**33.99-minute estimate** concerns serial elapsed time spent by local processes,
not measured CPU savings, hosted savings or reduced time to merge. These exact
old-head/base records cannot be reused after incompatible changes.

## What caused waste

1. **Exact duplicate evidence:** no completed full run with an identical
   base/head/tree/contract tuple was established. The repeated work above used
   different bases or heads, or was interrupted. Required local and hosted
   validation are separate confidence boundaries, not interchangeable duplicates.
2. **Premature work:** all four chats began final validation while #246 was
   pending; #249 validated before the overlapping removal PRs were ready.
   Its own heartbeat now waits for them. That work may still have supplied useful
   early feedback, but cannot satisfy their later integrated revision.
3. **Invalidation:** #246 advanced main, #247 synchronized after validating its
   prospective merge, #249 edited during validation, and #250 changed HEAD
   before final evidence. Source guards correctly rejected stale #250 evidence.
   These are ordering failures; weakening identity checks would conceal them.
4. **Local contention:** a retained `ps`/Docker-plan snapshot in the #249 chat
   showed **five Python preflight drivers**: #247 old and current base, #248,
   #249 and #250. Docker reports 4 CPUs and 8,337,670,144 bytes memory.
   The #249 trace explicitly investigated other active fixture containers.
   This establishes overlapping heavy work and resource competition; historical
   CPU, memory pressure, disk peaks and bytes transferred were not retained, so
   no slowdown or OOM is attributed to that competition.
5. **Repeated setup:** each local run builds the two validation images when
   selected and runs isolated dependency installs and all five production
   recipes. Successful seven-suite runs request seven Docker builds, even when
   layers are cached. Six successful final records therefore account for
   **42 build invocations**; this is not 42 full downloads or compilations.
   Native hosted jobs already use a GHA layer cache. Failed logs still show
   repeated base metadata resolution and builder setup; #250 attempt 2 failed
   while obtaining the BuildKit image itself.
6. **Retries:** all four hosted retries were already selective. The repeated
   failures cost 4.917 runner minutes and made no recovery progress. There is
   no proof of a permanent Docker failure; repeated external signatures warrant
   stopping until service recovery evidence, not a larger retry loop.
7. **Overlap/ownership:** #247/#249/#250 touch shared CI contract/preflight/docs
   areas. Separate chats each owned their PR, with no visible group integration
   order before full validation. The #249 follow-up has a useful dependency
   order and stays within its own merge authorization; no evidence shows two
   automations merging the same PR.
8. **Follow-ups:** only one relevant active local automation was found. Its
   saved heads are stale observations by design and its prompt refreshes current
   main after prerequisites. It says to stop after merge, but “retry after a
   cooldown when useful” lacks an attempt budget. There is no evidence that it
   continued after completion. Other schedules were left untouched.

## Docker traffic and limits

The 18 distinct failed hosted image-job logs show manifest **HEAD** requests
returning 429 for `golang:1.25-bookworm` or Python base tags, except #250's
second attempt which shows token-service timeout/504 failures. These are
pre-test dependency failures. A failed metadata lookup does not establish a
download, and a layer-cache hit does not eliminate metadata traffic.

Docker distinguishes IP-scoped abuse limiting across all Hub requests from
pull limiting. A simple 429 is consistent with abuse limiting; these logs lack
response bodies, limit headers and source-IP attribution sufficient to prove
which limit applied. Docker also documents that HEAD limit checks do not count
as pulls, and third-party platforms can share outbound IPs. See
[usage/abuse limits](https://docs.docker.com/docker-hub/usage/) and
[pull accounting and shared-IP attribution](https://docs.docker.com/docker-hub/usage/pulls/).
Repository hosted traffic, this workstation's local traffic and possible other
customers' shared-IP traffic cannot be combined into a known quota exhaustion
cause. No provider usage report or network capture was available; no credentials
were forwarded and no artificial pulls were generated.

## Selected changes and verification

Invariant: one cooperating full local driver owns expensive work per user/host;
only a complete compatible suite set can produce final evidence. The kernel
establishes local ownership before clone/build. CLI admission consumes it.
Hosted cancellation owns an entire PR graph; the existing persisted-evidence
and Actions-provenance boundaries still establish gate success.

- `scripts/ci_preflight.py` enforces a nonblocking kernel slot across worktrees
  and temporary roots. A loser exits with failure before work and cannot write
  passing evidence. The lock releases after failure, cancellation or crash;
  stale diagnostic text is never an ownership lease.
- `.github/workflows/ci.yml` replaces eight job cancellation blocks with one
  PR-only whole-workflow policy. This prevents obsolete downstream allocation
  and old/new graph cancellation races. Main/dispatch group identities are
  unique per run, preserving deployment-source runs. GitHub documents this
  [workflow concurrency boundary](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).
  No workflow paths, suites, fixtures, SDK versions, gate requirements, images,
  deployment selectors or permission boundaries were removed.
- `AGENTS.md`, `docs/ci-preflight.md`, `docs/agent-integration.md` and
  `ci-incident-recovery` route future agents to explicit integration ownership,
  prerequisite sequencing, frozen validation heads and bounded selective
  recovery. These procedural rules do not grant cross-chat or merge authority.
- `tests/test_ci_coordination.py` exercises actual subprocess/kernel
  contention, different temp overrides, TERM/KILL, exceptions and stale owner
  text. The existing operational-outcome test now checks graph-wide ownership and
  distinct PR/main/dispatch identities while retaining the always-evaluated
  gate, both SDK entries and protected production queues. Existing preflight/source-proof/deployment tests
  cover stale revision/base/tree/contract/tool versions, missing/failed/cancelled
  results and matrix entries; cancelled work cannot create a passing gate.

A safe real-entrypoint reproduction substituted a marker for costly execution.
With one owner held, four subprocess CLI contenders all started the marker on
the original code; all four failed before the marker with the new code.
**Admitted heavy drivers: 5 → 1 (80% lower peak admission).** No actual CI,
Docker download or validation suite was run for this reproduction. It measures
concurrency control, not removal of four necessary PR validations. Peak resource
contention should fall; cumulative compute and elapsed merge savings after
adoption remain unmeasured.

Focused boundary validation:
`uv run pytest tests/test_ci_coordination.py tests/test_ci_preflight.py tests/test_ci_source_proof.py tests/test_ci_deployments.py -q`
passed 115 tests; focused Ruff passed. The PR records final committed-head full
preflight evidence and hosted status separately.
The expanded check including gate artifact transport and repository guardrails
passed 170 tests and 97 subtests; full repository Ruff also passed.
Full preflight on initial head `9d23b18` then caught the old operational-outcome
test requiring per-job locks. Migrating that test to whole-graph ownership and
removing the duplicate new workflow test produced 182 passing focused tests
and 97 subtests, including all operational-outcome controls. The initial full
run correctly failed (2,518 passed, five permitted native skips) and is retained;
its evidence cannot qualify the corrected head.

Deletion pass: eight now-redundant job ownership blocks removed; no internal
evidence handling removed, no fallback or retry loop added. Rejected evidence
reuse across administrative head changes. Existing artifact/provenance checks
stay because they protect trust boundaries.

## Follow-ups

- Integrate this reviewed change with #247/#249/#250 through one human-authorized
  group owner. Their shared-file overlap is explicit; this PR does not finalize
  or mutate them.
- Evaluate provider-supported mirrors, immutable base-image policy and
  authenticated organization infrastructure only with source-IP/usage evidence.
  No personal credential forwarding or speculative registry switch.
- Benchmark suitable dependency caches separately, preserving locked versions,
  isolated credentials, native architecture and tested-image identity. Native
  GHA layer caching already exists; it did not prevent these metadata failures.
- Consider one shared recovery helper if bounded procedural retries still
  duplicate after adoption. A coordination service, PR merge queue/ruleset
  change, global hosted serialization and automatic workflow retry were deferred:
  they add authority/ordering complexity without measured benefit here.
- Keep #249's suite-selection work independent; narrowing consumers or changing
  deployment selection belongs to its own proof. Full validation remains intact.
- Inspect crash-orphan Docker work before restarting. A kernel lock cannot stop
  legacy entrypoints, direct builds, other users or another workstation.
- No original Slack incident was reconciled: these validation failures and open
  PRs did not reach verified recovery. Alert reconciliation remains each incident
  owner's separately authorized work. No duplicate notification was sent.
