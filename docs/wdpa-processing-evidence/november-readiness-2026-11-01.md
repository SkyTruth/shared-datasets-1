# November WDPA activation and recovery

Both November 1 datasets were published by the successful `wdpa-monthly-bschh`
canary, and protected schedule control resumed the monthly schedule after
independent signed-outcome and publication verification. The next scheduled run
is November 4 at **4:00 a.m. America/New_York** (`09:00 UTC`).
See [verified publication and live activation](#verified-publication-and-live-activation)
for the final acceptance evidence.

The chronology below preserves observations at each earlier failure and recovery
boundary. Statements that a canary was running, publication pending or the
schedule paused describe those dated observations; the final activation section
records their subsequent resolution. The original source-unavailable check-in
transaction was recovered without clearing claims, rewriting intent or resetting
feature-ID history.

## Exact attempt

- [Deployment run 41386681213, attempt 1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41386681213/attempts/1).
- Tested executor: `c43315539a496eebc84c8d888a26f8d61130ccde`.
- Main CI source: `41374742252/1`; `ci-ready` and the retained WDPA image passed.
- Deployment record: `7484046062`; terminal phase `failed`.
- Cloud Run execution: `wdpa-monthly-vfcj6`, failed with exit 1 at
  `2026-11-02T04:17:35.574263Z`.
- Installed worker and dormant observer image:
  `us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/wdpa-monthly@sha256:57d5adf51791bc669bece050476b3647cdcc9779d7cdb4bacfd99d89efe74ca0`.
- The worker no longer pins `WDPA_PROMOTION_BUNDLE` or a template `RUN_DATE`.
  The canary alone used `RUN_DATE=2026-11-01`.
- [Monitoring incident 0.oejb7pfczv4r](https://console.cloud.google.com/monitoring/alerting/alerts/0.oejb7pfczv4r?project=shared-datasets-1)
  was authoritatively `CLOSED` at `2026-11-02T05:24:11Z`. Closure alone did
  not establish dataset recovery. Its [original Slack parent](https://skytruth.slack.com/archives/C0B12LDMB1A/p1793593147771669)
  confirms actual failure delivery. [Protected reconciliation run 41496679143](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41496679143/attempts/1)
  succeeded after transaction recovery; a subsequent read verified the same
  parent changed to “Closed by Monitoring” with no new thread message.

## Failed boundary and producer correction

The source URL returned HTTP 404 for
`WDPA_WDOECM_Nov2026_Public_all_shp.zip`. That should produce a skipped
check-in while the schedule waits for upstream availability. The new producer
instead supplied `run_date`, while the release-index contract requires
`release_date`. Derivation raised `ReleaseIndexError: run record is missing
release_date` after the publisher had prepared a receipt and claimed the marine
asset.

PR #361 writes the scheduler attempt as `release_date`, retaining the
month-start `target_release_date`. Generated skip intents also validate their
complete derived index before persisting a receipt or acquiring ownership.
Regression tests first reproduced the missing field and premature writes, then
passed through the actual publisher and persisted index for both WDPA realms.
They preserve the existing release, manifests and feature-ID counters.

## Remote mutation frontier

The protected Terraform apply updated only the monthly worker and dormant
observer jobs. It did not resume either schedule. The runtime changed these two
objects under
`gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/wdpa-marine/publications/`:

| Object | Observed generation | State |
| --- | --- | --- |
| `state.json` | `1793593048719262` | Active check-in claim; reserved next feature ID remains `17943`. |
| `receipts/348383de9527e070739cfaa18074b789de67703fcf88421ba7f97ebe6de68da5.json` | `1793593048075111` | `prepared`, empty `results`, no reservation. |

Transaction:
`c1a5470ad44969e919fae04946185f1572585a830e70068236909d285bb3c02f`.
Its only proposed effect is
`gs://skytruth-shared-datasets-1/_catalog/releases/wdpa-marine.json`, captured
at generation `1793309285992493`. That generation was still current before
reviewed recovery: the failed skip never wrote it. At that boundary the
terrestrial index remained at `1793309291212844`, and state generation
`1793309293057326` had no active claim and reserved next feature ID `497919`.

Both committed releases remain `2026-10-01`. No new release, latest dataset,
run-record or release-index artifacts were published by this attempt.
Generation-pinned state, receipt and index snapshots are retained in the task's
local incident evidence directory.

## Reviewed check-in recovery

This WDPA readiness chat owns recovery. PR #361 merged the readiness, observer
retirement and future skip fixes. PR #362 adds the constrained recovery executor
and trusted-base plan schema. The immutable proposal below completed that original
claim through the protected installer.

The [immutable recovery proposal](../../.github/dataset-plans/wdpa-marine/wdpa-skip-recovery-20261102/255f7368e59828be1b71a11768bdd4f0baa5f8224730b92146d94bf4b5ad4006.json)
pins the state and receipt above and stages the exact corrected check-in at
`gs://skytruth-shared-datasets-1/_scratch/pending-publishes/wdpa-marine/wdpa-skip-recovery-20261102/recovered-index.json`,
generation `1793637440852623`, SHA-256
`844a118ad673db261c5ffd8e76c06e3833d79fbf5addd2639134a5617453f721`.
The protected control installer resumes the captured transaction: index write,
receipt result, then normal claim release. It preserves the original intent,
October current release, manifest snapshots and feature-ID high-water `17943`.
The protected installer succeeded in [main run 41492611273, attempt 1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41492611273/attempts/1),
job `125443442955` (`Publish PR #364 / Install reviewed feature-ID reset`).
Independent generation-pinned GCS reads verified all postconditions:

| Object | Recovered generation | Verified result |
| --- | --- | --- |
| Original receipt above | `1793641445980498` | `derived_complete`; original intent and transaction unchanged. |
| Marine `publications/state.json` | `1793641452518588` | `active: null`; all other fields unchanged, including October manifests and counter `17943`. |
| Marine release index | `1793641441933312` | SHA-256 `844a118ad673db261c5ffd8e76c06e3833d79fbf5addd2639134a5617453f721`; November 1 source-unavailable skip; October release history unchanged. |

Terrestrial state/index generations remained `1793309293057326` and
`1793309291212844`, with no active claim and counter `497919`. Both assets
passed the publication rollout gate with `--require-published`; both indexes
passed their release-index contract. The later canary may add fresh skipped
check-ins; it does not undo the original receipt history.

The malformed prepared intent belongs to the original execution and executor.
A new image, date or execution cannot take over its claim. Do not delete the
receipt, clear the state, rewind counters, rewrite captured intent or rerun the
failed deployment blindly. The existing generic object publisher cannot mutate
publication control objects.

Activation follows the recovered transaction with a CI-tested fixed worker, a
verified canary, both release-index checks and protected schedule resume. The
protected ten-resource retirement has removed the observer job, scheduler and service
account, and revoked its temporary grant. Live inventories verified their
absence; do not recreate the observer or renew its retirement grant. Do not
resolve the original alert until recovery is verified.

## Fixed-worker activation

The fixed worker uses reviewed main executor
`2970e28547e45cb1cb8a5f93384ee25642c2542d` and successful main CI
`41492611273/1`. [Protected deployment 41494126901, attempt 1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41494126901/attempts/1)
loaded that CI attempt's retained image without rebuilding. Deployment record
`7499302344` pins
`us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/wdpa-monthly@sha256:1b10e13894c14bad21abd387fdd4599562f23939e267e7e22e1ea929cd5e105c`.
Its allowlisted saved plan updated only
`module.wdpa_monthly_job.google_cloud_run_v2_job.this`.

That rollout's canary is `wdpa-monthly-5s4cw`, with execution-only
`RUN_DATE=2026-11-01`. Live worker-template inspection verifies the immutable
image/config pair and the absence of persistent `RUN_DATE` or
`WDPA_PROMOTION_BUNDLE`. The observer remains retired.

The canary exited zero at `2026-11-02T18:34:12Z` with a designed
`identity_decision_required` block for one marine record. No November release or
canary publication receipt was created. Source key `555729583`, already owning
feature ID `15378`, now matches the geometry of existing feature IDs `11347` and
`15093`. Their published names are Bonden, Sweden, under separate designations.
The maintainer explicitly approved `keep_previous_key_mapping` for source key
`555729583`, preserving its own feature ID `15378`. Reusing `11347` or `15093`
would merge distinct existing designations and is not legal for this key. The
[release-scoped resolution](../../catalog/feature-identity-resolutions/wdpa-marine.json)
was accepted in [PR #365](https://github.com/SkyTruth/shared-datasets-1/pull/365),
merged as `4178a37ee76bdda8a9c20d2c2353a059c817e671`. A zero exit or signed
terminal-execution outcome alone does not authorize schedule activation.
Monitoring incident `0.oejurkps0izq` tracks that review gate.

## Approved identity decision rollout

Successful main CI `41506910240/1` retained the worker containing the approved
Bonden choice. [Protected deployment 41509593022, attempt 1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41509593022/attempts/1)
installed the exact retained image
`us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/wdpa-monthly@sha256:096c52fe7186bb075072c1d09c456a0c81016be6b2da05eddd554411407c0790`.
Its signed record `7501355577` entered `verification_pending` after the bounded
watch handed off execution `wdpa-monthly-w75n5`. That execution uses an
execution-only `RUN_DATE=2026-11-01`; the automatic job has no persistent date
or promotion bundle. November's upstream ZIP is available, so this canary runs
the full sample/build/validation rather than a source-unavailable skip.

The original [Bonden decision thread](https://skytruth.slack.com/archives/C0B12LDMB1A/p1793647419860789?thread_ts=1793644451.878259&cid=C0B12LDMB1A)
has its decision-of-record reply: keep `15378`, decided by `jonaraphael`, because
the matching geometries belong to distinct existing designations. The inspected
reply timestamp is `1793647419.860789`; it must not be duplicated.
[PR #366](https://github.com/SkyTruth/shared-datasets-1/pull/366), merged as
`e1ed4994b42ffd2d0f808eb9092bc8c2fe9097de`, adds automatic accepted-decision
replies and exact correlation of related Monitoring review messages, with a
reviewed adoption of this existing reply. Live notification reconciliation,
terminal publication acceptance and schedule control retain separate evidence.


The replacement canary completed at `2026-11-02T20:17:52.230544Z` with zero
exit and an `identity_decision_required` stop for two terrestrial cases.
[Completion check 41518182208, attempt 1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41518182208/attempts/1)
recorded signed runtime status `21199909894` as `verified`. This establishes
terminal execution success only: both current releases remain October, both
claims are absent, counters remain `17943` / `497919`, and neither asset has
this canary's completed publication receipt. Schedule resume remains blocked.

The two terrestrial review cases are separate from the approved Bonden choice.
The maintainer has explicitly approved both choices:

| Source key | Captured comparison | Approved maintainer decision |
| --- | --- | --- |
| `555732174` | Tjärnberget owns `255953`; its new geometry matches separate Habitats Directive record `132873` / source `555533827`. Both source records remain present in November. | Approved by `jonaraphael`: keep existing mapping `255953`, preserving both designations. Accepted in PR #369. |
| `555779501` | New November source key for Weid forest reserve, matching October feature `282786` / source `555769403`. The old source key is absent in November; name, designation and stored geometry agree, while designation year changes from 2023 to 2025. | Approved by `jonaraphael`: preserve `282786` as a source-identifier replacement. Accepted in PR #369. |

The comparison used the generation-pinned, SHA-256-verified October terrestrial
metadata (`1791037160099462`, `8bb8d0efd6d29b570c6bcf481843b94fd353d48a3b2722bd887817de4acecba6`)
and all six November source DBFs through conditional HTTP ranges. The source
ETag is `"c6cfb498ddbb8cc8d22c78dbffadb301-894"`, last modified
`2026-11-02T13:27:07Z`. ZIP CRCs passed and both selected source-properties
hashes exactly match the captured worker evidence. Tjärnberget now has explicit maintainer approval to keep `255953`; Weid is also explicitly approved to preserve `282786`. [PR #369](https://github.com/SkyTruth/shared-datasets-1/pull/369)
binds both choices to the full captured comparison evidence and merged as
`95987bf3c1fcc41ac90975d3be57c43231ad666e`. Its exact head
`4168126eeb56e1f8a3755a58937b6e835c39336f` passed all seven local preflight
suites and hosted CI `41535729264/1`, including `ci-ready`. Protected deployment
and publication verification follow the accepted curation. The terrestrial decision parent is Slack timestamp
`1793650665.056749`; its confirmed decision reply is `1793657090.775849`, naming `jonaraphael`
and both rationales without broadcast. The reviewed adoption retains that reply
without sending a duplicate.

## Decision notification rollout

Main CI `41517776289/1` passed `ci-ready`. Its protected cron policy bootstrap,
private transport resources and receiver apply completed successfully. Live
`monitoring-slack-00011-gks` became Ready at `2026-11-02T20:41:47.797516Z`,
using tested image
`us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/monitoring-slack@sha256:97b9638e4118c50f74c3fbb548b172925c4c8b5c5ee2442295aa2da4997672d4`
and protocol `feature-identity-decisions-v1`.

The same main run's notification reconciliation job `125540720464` failed at
its source check, before cloud authentication or notification-state writes:
`GitHub.pages()` from the curation client accepts a keyword-only page field,
while the deployment verifier supplies that field positionally. The failure was
reproduced read-only against the actual CI source. [PR #368](https://github.com/SkyTruth/shared-datasets-1/pull/368)
merged as `70c8670cfcfeaaedfd54664c0dea279292cea6ba`, uses the verifier's
existing GitHub adapter and adds an actual CLI/adapter regression at the raw HTTP
boundary. No Terraform replay is needed. The exact
Bonden backfill was also rehearsed against isolated serialized state with real
reviewed PR evidence: zero new messages and one update to the inspected
Monitoring parent. That rehearsal is not evidence of a visible Slack update.

The automatic WDPA deployment in `41517776289/1` deferred before mutation at
`2026-11-02T20:46:46Z` because the schedule was paused without an explicit
canary date. It did not launch an extra worker.

Main CI `41524696362/1` completed successfully on that fix; its protected
reconciliation job `125552273155` verified the exact accepted Bonden head,
recorded its private approval and adopted the inspected existing reply. A live
Slack read confirmed the [original related review alert](https://skytruth.slack.com/archives/C0B12LDMB1A/p1793644518730859)
is green **Decision recorded**, with no thread messages, while the original
Bonden thread retains exactly one reply (`1793647419.860789`). The separate
terrestrial request now has one confirmed decision-of-record reply
(`1793657090.775849`) following explicit approval of both choices. Its reviewed
curation and adoption merged through PR #369; protected notification
reconciliation is observed separately.
The native Monitoring incident remains open; its private receipt retains
`closed=false` and separately acknowledges the reviewed decision.

The verified private notification objects in
`gs://skytruth-shared-datasets-1-monitoring-slack/` are:

| Object | Verified generation |
| --- | --- |
| `identity/approvals.json` | `1793653725145027` |
| `identity/events/9eb0e91c5f87c8e3823886ff78c5fc3c1e50bde4a0b0c93bf523b4cc1624d3b9.json` | `1793653725963793` |
| `identity/links/9eb0e91c5f87c8e3823886ff78c5fc3c1e50bde4a0b0c93bf523b4cc1624d3b9/0.oejurkps0izq.json` | `1793653726130894` |
| `incidents/0.oejurkps0izq.json` | `1793653727111086` |

The approval index binds reviewed main `70c8670` and the single approved Bonden
case. The event receipt preserves the original parent and confirmed reply;
the incident receipt acknowledges the exact updated message fingerprint.
Canonical dataset objects remain unchanged by this notification recovery.

The pre-authentication failure also created managed CI incident
`SD-6545A8A4F005`, parent `1793653798.924739`. The existing health observer
`41525684324/1` used the newer successful exact job on `70c8670` to update that
same parent to **Resolved**, with no recovery reply or replacement message.
Its confirmed-delivery attestation and durable-checkpoint verification passed.
A read-only Slack thread fetch verified the visible green parent links to
recovery run `41524696362/1` and contains no thread messages.


## Terrestrial decision deployment

Main CI `41536709760/1` completed successfully on accepted executor
`95987bf3c1fcc41ac90975d3be57c43231ad666e`, including `ci-ready`
`125593126851` and retained WDPA image artifact `13184823116`.
The protected notification reconciliation `125593413533` succeeded. A live
private-journal read verified both terrestrial approvals, three total approved
cases including Bonden, and the original terrestrial parent with exactly one
confirmed reply. Approval-index generation is `1793658565635630`; terrestrial
event generation is `1793658566704872`. A fresh Slack read confirmed no duplicate.

Main's automatic WDPA job `125593415587` deferred before mutation at
`2026-11-02T22:35:33Z` while the schedule was paused without an explicit canary
date. [Protected deployment 41537866093, attempt 1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41537866093/attempts/1)
then installed the same main CI's exact retained image through signed record
`7505209489`:
`us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/wdpa-monthly@sha256:8bfdb17e4e782e0d252f77df56a9909fbc4b25ee71df5f058c9464cde3508bb7`.
Its allowlisted plan updates only the monthly worker. Live inspection verified
that image and no persistent `RUN_DATE` or `WDPA_PROMOTION_BUNDLE`.

The publication canary is `wdpa-monthly-2d7xj`, created at
`2026-11-02T22:39:01.471421Z`, with execution-only `RUN_DATE=2026-11-01`.
Before deployment, fresh reads verified both assets remained October, no claims
or active workers, unchanged counters `17943` / `497919`, valid receipts/indexes,
and preservation of the recovered original intent. The canary is running;
terminal signed verification, actual publication receipts for both assets and
protected schedule activation remain pending.


Deployment `41537866093/1` completed successfully after its bounded watch and
recorded signed nonterminal status `21209248099`, `verification_pending`, at
`2026-11-02T22:49:25Z`. Independent attestation verification confirmed the
exact deployment/image/execution binding. At `22:58:54Z` the same canary was
still running, its translation index progressing, with both assets still on
October, no claims or counter changes, and the scheduler paused. This signed
handoff is not a terminal publication acceptance.


## Full-build marine identity review

The complete marine build then stopped with `identity_decision_required` for
114 additional cases that were absent from the compatibility sample. Execution
`wdpa-monthly-2d7xj` ended at `2026-11-03T00:19:02.594096Z`, exit zero.
[Completion observer 41552247011/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41552247011/attempts/1)
succeeded and independently verified signed runtime status `21214212436`.
That runtime success does not establish publication: both candidate publication
receipts are absent, both current assets remain October, claims are absent,
counters remain `17943` / `497919`, and the scheduler is paused.

Generation-pinned October marine metadata (`1790866699087905`, SHA-256
`0d8263e527b1bcecc0835c894397677d8bc6e834631508d3b1cce16c898c5383`)
contains 17,938 records. All six November source DBFs were scanned against the
same conditional ETag; ZIP CRCs and all 114 captured source-properties hashes
passed. Of these cases, 111 already own IDs and retain their names, designations,
countries, statuses and designation years. Three keys are new: Aracatiaçu is a
proposed source-key replacement preserving `17642`; Svenska Björn SPA and
Selmunett SPA are proposed separate new 2025 designations requiring fresh IDs.
The exact 114-case recommendation batch, proposal SHA-256
`6d38b2078c28bbd6027bd3ea792f2bd014d03a1b9578aa77fee256191a805d5a`,
was explicitly approved by `jonaraphael` with **Approve all 114 recommendations
(Recommended)**. [PR #370](https://github.com/SkyTruth/shared-datasets-1/pull/370)
binds every approved case to its full captured hashes and reviewer. The final
head `537fb87a20f9e8c9808f4e4ca2e49696b83ae6d2` passed all seven local
preflight suites and 60 focused tests plus seven subtests. Hosted
`41556855050/1` passed, including `ci-ready` `125659690146`; PR #370 merged
as `36de9edfaaa0de6d813a9c4a56c710a6af9e3393`. Main validation and the next
protected publication canary follow that acceptance. These approvals cover only
the captured cases.

The typed decision event is
`9a353d07485339a26633d68ac380b2551c777150f07676d0a4f20af9f7532fca`;
its original Slack parent is `1793665143.751309`, with no reply while review is
pending. The related Monitoring incident `0.oekjmpkauoa1` carries explicit
execution, asset and release labels; its private correlation points to this exact
event. Accepted reviewed curation will reply and settle these same messages
through the existing protocol. Do not replay the worker or activate the schedule
without reviewed decisions and a new verified publication canary.


## Approved full-build batch rollout

Main `36de9edfaaa0de6d813a9c4a56c710a6af9e3393` passed `ci-ready`
`125662187268` in `41557597276/1`, retaining WDPA image artifact `13195134233`.
Protected reconciliation `125662433125` succeeded. An independent private-journal
read at `2026-11-03T01:14:59Z` verified all 114 accepted PR #370 proofs and 117
total approved cases. The original marine decision parent `1793665143.751309`
is green **Decision recorded** and has exactly one automatic reply
`1793668427.085829`, naming `jonaraphael` and linking the complete PR rationale.
The exact related review parent `1793665172.569269` is also green, with no thread
reply. Direct Slack reads confirmed both updates. Private index/event/incident
generations are `1793668425745986`, `1793668427469055` and
`1793668428153193`. Native incident `0.oekjmpkauoa1` retains `closed=false`;
review resolution does not establish native closure or publication success.

Fresh deployment checks passed against that exact tested main revision, the
retained image, signed prior deployment and publication state. Both assets
remained October, unclaimed, with unchanged counters and the original recovered
intent preserved. [Protected deployment 41558514062/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41558514062/attempts/1)
was dispatched with `canary_run_date=2026-11-01`. Main’s automatic WDPA job
deferred before mutation at `2026-11-03T01:20:48Z`; the explicit deployment
then applied only the monthly worker using signed record `7507861878`.
The exact tested image is
`us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/wdpa-monthly@sha256:ec0d8dc15d7371efbd8f741b21a31ac64b00be6b6c8aa812422f79dd07b394a4`.
Its signed initial claim `21217265989` passed independent attestation
verification. Live inspection confirms that image and no persistent date or
promotion bundle. The new publication canary is `wdpa-monthly-nzptt`, created
at `2026-11-03T01:24:36.852597Z`. Terminal publication acceptance and protected
schedule activation remain pending; no extra manual worker is started.


## Compatibility-sample decision scope failure

Canary `wdpa-monthly-nzptt` failed at `2026-11-03T02:05:54Z` during the
compatibility sample, before full-build publication. The strict legacy writer
received all November marine decisions, including source key `46`, which was
outside the deterministic fractional source filter. Pinned source evidence has
`SITE_ID=46`; its sample modulus is `709189`, outside the threshold `1000`.
The resulting stale-decision rejection is a replay-producer bug, not a new
identity question or permission to relax the validator.

[Protected completion 41564783412/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41564783412/attempts/1)
observed that exact failure and successfully attested signed failed status
`21219539072` on record `7507861878`. Independent local signature verification
passed. Both canary-specific publication receipts are absent; both assets remain
October, with no claims or active workers, counters `17943` / `497919` unchanged,
and the original repaired intent preserved. The scheduler remains paused.

[PR #373](https://github.com/SkyTruth/shared-datasets-1/pull/373) scopes fractional
curation by actual keys from the exact source filter before either allocator.
Complete builds retain the entire decision list; included hashes, duplicate and
illegal decisions, unresolved cases and full-build staleness still fail. Frozen
head `412e4b0c8431220548a290f51db77e8d7ee7f88b` passed 28 focused tests and all
four selected complete local preflight suites, including native OGR filtering.
Hosted CI `41566108006/1` passed, including `ci-ready` `125689860560`.
PR #373 merged as `cbc1cb80d73abbe8e4ced85604c975cffd9bddd5` at
`2026-11-03T02:33:35Z`; main CI `41566875628/1` is running. Its tested image,
fresh replay checks, one protected canary and actual publication verification
are still required.
The failed execution is not replayed and no further completion observer is needed
for it.


## Scoped-sample repair deployment

Main CI `41566875628/1` completed successfully on accepted executor
`cbc1cb80d73abbe8e4ced85604c975cffd9bddd5`, including `ci-ready`
`125692228092`, retaining WDPA image artifact `13199277741`. Its automatic
WDPA deployment job was unselected; no additional worker or deployment record
was created. Fresh read-only checks at `2026-11-03T02:40:59Z` verified the exact
accepted PR head, tested main, signed prior failed record/status, replay eligibility,
paused/drained controls, both existing publication gates and original intent.
The actual pre-canary state/history was retained for subsequent skip comparisons.

[Protected deployment 41567681540/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41567681540/attempts/1)
was dispatched once from that main with an explicit `2026-11-01` canary date and
default `cancel_running_canary=false`. Installation, exact-image execution and
actual publication verification remain pending. No local apply or extra manual
worker was started.


The saved-plan deployment installed the exact tested image
`us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/wdpa-monthly@sha256:0e9b06f18c373b59b63fa539858fc810b63bbbdaad33e1245e6d2e47311669a4`
using signed record `7509007122`, scoped only to
`module.wdpa_monthly_job.google_cloud_run_v2_job.this`. Initial signed status
`21220962934` passed independent attestation verification. Live worker-template
inspection confirms the image, required resources and no persistent `RUN_DATE`
or `WDPA_PROMOTION_BUNDLE`.

Its sole canary is `wdpa-monthly-7zscj`, created at
`2026-11-03T02:45:27.627985Z` with execution-only `RUN_DATE=2026-11-01`.
At `02:48:39Z` execution startup remained pending, with no logs yet; both
publications remained October, claims absent and counters unchanged. The
scheduler remains paused. The protected workflow is still watching; neither
terminal runtime success nor publication acceptance is established.

Read-only Slack inspection also identified the new failure parents:
Monitoring `0.oekm3455j6dq` at `1793671628.441279`, and completion-check
incident `SD-7977CD17789F` at `1793672229.079189`. Both are open. They are
separate from the completed identity-review reconciliation and must not be
marked recovered before verified recovery through the existing paths.


At `2026-11-03T02:51:58Z`, the exact new canary has `Started=True` and
`runningCount=1`; the Started condition transitioned at `02:47:34.232317Z`.
It is the sole active WDPA execution. Both current releases remain October,
with no publication claims and unchanged counters, while the scheduler is paused.
Actual November publication and terminal signed verification are still pending.


Deployment `41567681540/1` completed successfully after its bounded watch.
Its signed handoff status `21221458975`, `verification_pending`, was recorded
at `2026-11-03T02:55:56Z` and independently verified at `02:58:27Z`, binding
the same image and `wdpa-monthly-7zscj`. The canary continues asynchronously;
this handoff does not establish terminal execution or publication acceptance.

The independent review-evidence PR #371 subsequently merged as
`2e84fb0ba64cfc07162fc468cb2bbf5762e8a4aa`. This running canary still uses the
accepted PR #373 executor `cbc1cb8`; its image does not contain that later
review-bundle capture feature. No replacement worker was started for that merge.


## Descendant-process cache-sampling failure

Canary `wdpa-monthly-7zscj` failed at `2026-11-03T05:03:45.218934Z` with
exit code 1. Marine full-build normalization, allocation, metadata, FGB and
PMTiles production had completed, but the resource monitor raised
`PermissionError` while enumerating `/proc/580/fd`. Its phase resource record
incorrectly reported `succeeded` before propagating the sampler error. The
observed kernel lifetime memory peak was `7941455872` bytes; the execution
record and traceback establish a monitor error, not an OOM classification.
The failure occurred before publication, with no new identity decision.

[Protected completion 41582724615/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41582724615/attempts/1)
observed only that execution and failed as required for its terminal runtime
failure. Its attestation step succeeded; independent local verification passed
for signed failed status `21227637126` on deployment `7509007122` at
`2026-11-03T05:25:57Z`. Direct reads at `05:26:15Z` confirmed both
canary-specific publication receipts absent. The authoritative state snapshot
at `05:06:28Z` retained October releases, no claims or workers, counters
`17943` / `497919`, valid existing receipts and indexes, and the original
repaired receipt intent. The scheduler remains paused. No failed worker or
deployment is replayed.

The new runtime Monitoring incident is `0.oekq5fa5pnha`, at original Slack
parent `1793682300.687429`. It is separate from the older sample failure and
its completion-check alert; no recovery or native closure is claimed.

Focused repair commit `6f0d5c1d26f69d54a266ea04e105662b78424a8a` distinguishes
descendant process visibility from mandatory resource measurements and owned
file operations. Inaccessible descendant metadata or descriptor lookup may be
skipped; own-process access, linked scratch/input access, pinned-inode advice,
scope and inode checks, and cgroup telemetry remain strict. Sampler errors now
persist a failed phase record even when the native producer exits zero.
Regression tests reproduced the original boundary error and false success record
before the fix; 45 focused tests pass after it. All four selected complete local preflight suites passed on the frozen commit,
including native geospatial integration and the Linux AMD64 production image
(`_scratch/ci-preflight-da1dx59l/preflight.json`).
[PR #375](https://github.com/SkyTruth/shared-datasets-1/pull/375) is open;
hosted CI `41584108486/1` is running. No image deployment or publication
recovery is claimed from these checks.

PR #375 merged as `3c5b809418a9bd6f48cfdd54c37bab281f4b6595` at
`2026-11-03T05:34:17Z`. Accepted head `6f0d5c1d26f69d54a266ea04e105662b78424a8a`
passed hosted CI `41584108486/1`, including `ci-ready` `125746951793`.
Main CI `41584681472/1` is running; fresh replay/baseline verification and
actual protected deployment remain required. The failed execution is not replayed.

Main CI `41584681472/1` completed successfully with `ci-ready`
`125748377797` and retained WDPA image artifact `13206734613`. Its automatic
WDPA job deferred before mutation at `2026-11-03T05:39:03.8857588Z` while
paused. Fresh read-only checks passed at `05:40:37Z`, verifying the accepted
PR/head, tested main, exact artifact, signed prior failed outcome, replay
eligibility, paused/drained controls, both publication gates and original intent.
The actual pre-canary state/history was captured for any later skip comparison.

[Protected replacement deployment 41585333413/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41585333413/attempts/1)
was dispatched once from `3c5b809418a9bd6f48cfdd54c37bab281f4b6595`, using
that exact CI attempt, explicit `canary_run_date=2026-11-01` and
`cancel_running_canary=false`. Installation, execution and publication acceptance
remain pending. No additional manual worker was started.

The saved-plan replacement installed exact tested image
`us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/wdpa-monthly@sha256:4a86dca3aacef1dc591fcc2ae398729c12daeff5ee91964da50a51f48b44187d`
under signed record `7511416631`, limited to
`module.wdpa_monthly_job.google_cloud_run_v2_job.this`. Initial signed status
`21228870674`, `started`, passed independent attestation and source verification
at `2026-11-03T05:45:31Z`. The live job matches that image and retains no
persistent `RUN_DATE` or `WDPA_PROMOTION_BUNDLE`.

Its sole canary `wdpa-monthly-47c69` was created at
`2026-11-03T05:44:57.121525Z` with execution-only `RUN_DATE=2026-11-01`.
At `05:46:34Z`, `Started=True` (transition `05:45:59.987897Z`) and
`runningCount=1`. Both publications remain October, with absent claims,
unchanged counters and preserved original repaired intent; the scheduler
remains paused. No startup logs were requested by that state-only snapshot.
The protected workflow is watching; terminal execution, signed terminal outcome
and actual two-asset publication remain pending.

The activation checker now binds this actual canary, record and image to its
real fresh pre-canary history. Failed-candidate helpers remain archived locally;
the original-intent, exact-image, receipt, release-index, control and skip-history
requirements are unchanged. No readiness result is inferred from current
October receipts or a started deployment.

Protected deployment `41585333413/1` completed successfully after its bounded
watch. Signed handoff status `21229330760`, `verification_pending`, was recorded
at `2026-11-03T05:55:25Z`, binding the exact `wdpa-monthly-47c69` execution.
Independent attestation verification passed at `06:04:43Z`. The state-only
snapshot at `06:04:28Z` confirms that sole worker still running, both releases
on October with no claims or counter changes, original intent preserved and
the scheduler paused. This bounded handoff is not terminal runtime or
publication acceptance; no completion observer or additional worker is started.


## Complete-build report serialization failure

Canary `wdpa-monthly-47c69` failed at `2026-11-03T08:02:50.540343Z`,
exit code 1. Full marine artifact production and validation passed at
`08:02:44Z`, crossing the earlier resource-monitor failure boundary. Its kernel
peak was `7955836928` bytes and scratch peak `25815183360` bytes. The next
step, assembling the per-realm report in `local_wdpa_sample.py`, referenced
`legacy` even though a complete `--stage-build` invocation does not run the
legacy sample. The resulting `UnboundLocalError` stopped before staging or
publication; no new identity decision was requested.

[Protected completion 41600796640/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41600796640/attempts/1)
correctly failed for the exact failed runtime. Its attestation succeeded;
independent verification passed for signed failed status `21235776358` on
deployment `7511416631` at `08:08:10Z`. Both canary publication receipts were
independently absent at `08:11:19Z`. Both assets remain October, with no
claims or workers and counters `17943` / `497919` unchanged; the original
repaired intent remains intact and the scheduler is paused. The failed
canary/deployment is not replayed.

The related runtime Monitoring incident is `0.oeku8bmqups0`, original Slack
parent `1793693023.693619`; the completion-check incident is
`SD-4F31AFBFBBD0`, original parent `1793693513.152389`. Both Slack parents
were open on the bounded read. Native closure and data recovery remain
separate checks.

Focused repair commit `6acf0d9fd67272fa587468f8119e2f0b8482d075` derives
per-realm compatibility status from the selected invocation mode after the
optional comparison succeeds, matching the top-level report field. It removes
the optional legacy-result dependency without a default value or exception
fallback. Real CLI/report/staging regression coverage reproduced the original
error in complete staging and a no-comparison sample before the fix. After it,
49 focused tests pass, including successful comparison and a real comparison
mismatch that retains a failed report. Full preflight is running on the frozen
commit. No replacement execution or publication recovery is claimed from
these local tests.

The initial preflight on `6acf0d9` passed six selected suites but failed an
existing browser assertion that read control rectangles from different layout
frames. A test-only prerequisite commit `3d85d940bb7e0c446d74de1ff1f094e3a7b7f2bf`
reads both rectangles atomically, preserving identical pixel thresholds and
zero test retries. No catalog application code or deployment was changed.
The complete preflight was rerun for that new frozen head against current main
`3c5b809418a9bd6f48cfdd54c37bab281f4b6595`: all seven selected suites passed,
including native geospatial integration, production images, Node 22/24 and
all 42 browser tests without skips, failures or retries. Evidence:
`_scratch/ci-preflight-vjvrband/preflight.json`; the earlier failed evidence
remains retained at `_scratch/ci-preflight-on0_d55q/`.

[PR #376](https://github.com/SkyTruth/shared-datasets-1/pull/376) is open on
that exact validated head; hosted checks and accepted main image/deployment
verification remain required. No failed operation is replayed.

PR #376 merged as `711f74af09f1d8d1a34c19edef8c29c2ef6b2aec` at
`2026-11-03T08:34:07Z`. Accepted head `3d85d940bb7e0c446d74de1ff1f094e3a7b7f2bf`
passed hosted CI `41603414840/1`, including `ci-ready` `125809496636`,
after all seven selected local suites passed. Actual main CI, exact retained
image and fresh protected recovery remain required; no failed canary is replayed.

Main CI `41604247245/1` completed successfully on executor
`711f74af09f1d8d1a34c19edef8c29c2ef6b2aec`, including `ci-ready`
`125812000290` and retained WDPA image artifact `13215194429`. Automatic
WDPA deployment was unselected; tested-image validation is separate from
deployment selection. Fresh read-only checks passed at `2026-11-03T08:41:20Z`,
verifying accepted PR/head, current tested main/exact artifact, signed prior
failed record/status, replay eligibility, paused/drained controls, both current
publication gates and original repaired intent. The actual pre-canary history
was retained for subsequent verification.

[Protected replacement 41605170363/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41605170363/attempts/1)
was dispatched once with that exact executor/CI attempt, explicit November 1
canary date and `cancel_running_canary=false`. Installation, execution and
actual publication acceptance remain pending. No failed operation or old
predeploy helper is replayed, and no additional worker is started locally.

The replacement installed exact tested image
`us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/wdpa-monthly@sha256:4c42877704b32f1e6ca90f61d05f1aa19630a84077a0118202cc08bb944a60d4`
under signed record `7514215604`, applying only
`module.wdpa_monthly_job.google_cloud_run_v2_job.this`. Initial signed status
`21237950467`, `started`, passed independent actual attestation/source
verification at `2026-11-03T08:45:58Z`. The live job matches that image,
required resources and absence of persistent `RUN_DATE`/`WDPA_PROMOTION_BUNDLE`.

Its sole canary `wdpa-monthly-d7k4g` was created at
`2026-11-03T08:45:27.389590Z`, with execution-only `RUN_DATE=2026-11-01`.
At `08:47:01Z` it has `Started=True` (transition `08:46:27.321987Z`) and
`runningCount=1`. Both current releases remain October with no claims or
counter changes, valid existing receipts/indexes and preserved original
repaired intent; the scheduler is paused. The protected workflow is watching.
No startup logs were requested in that state-only snapshot. Signed terminal
runtime outcome and actual two-asset publication remain pending.

Readiness checks now bind this actual execution, record and image to its own
real fresh pre-canary history, retaining original-intent, exact receipt/executor,
release-index version, control and skip-history requirements. Failed-candidate
helpers are archived locally; no current October receipt substitutes for a
missing candidate receipt.

Protected replacement `41605170363/1` completed successfully after its bounded
watch. Signed handoff status `21238588874`, `verification_pending`, was recorded
at `2026-11-03T08:55:54Z`, binding `wdpa-monthly-d7k4g` and the same exact
image. Independent actual attestation/source verification passed at `09:06:40Z`.
The state-only snapshot at `09:06:24Z` confirms that execution remains the sole
active worker, both releases remain October with no claims or counter changes,
the original repaired intent is preserved, and the scheduler remains paused.
No runtime logs were requested. This asynchronous handoff establishes neither
terminal execution success nor actual publication acceptance. No completion
observer or additional worker is started while the canary runs.

## Full-terrestrial review stop and capture capacity

Canary `wdpa-monthly-d7k4g` failed at `2026-11-03T12:36:27.250648Z`, exit
code 1. The complete build advanced through the earlier marine report boundary
to full terrestrial allocation. It found 1,323 new identity cases: 1,321
`same_geometry_changed_properties` and two `same_properties_changed_geometry`.
Before the typed review stop could finish retaining its evidence, capture
rejected the count above its explicit 1,024-case cap. No complete review bundle
was staged. This is not approval of the new cases or publication success.

[Protected completion 41639498652/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41639498652/attempts/1)
correctly failed for that terminal runtime outcome; its attestation succeeded.
Signed failed status `21254231162` on record `7514215604` was independently
verified at `2026-11-03T12:56:50Z`. Direct reads at `12:53:23Z` found both
canary-specific publication receipts absent. The state snapshot at `12:51:06Z`
retained October releases, no claims or workers, counters `17943` / `497919`,
valid current receipts/indexes and the original repaired intent. The scheduler
remains paused. The failed deployment and worker are not replayed; no additional
completion observer is needed for that record.

The original typed review parent is `1793709384.828459`. Runtime Monitoring
`0.oel0h5pa5szu` has original Slack parent `1793709430.211649`; completion-check
incident `SD-542B9B72FEF0` has parent `1793710748.857879`. All three Slack
parents were open on the bounded read. Native Monitoring closure is not
established, and no manual recovery message or replacement parent is sent.

Read-only reconstruction recovered every numbered case from its exact
three-second log window in two bounded pages. All complete match sets agree
with the pinned 497,914-row October metadata. Conditional ETag-bound reads of
all six November DBFs, 499,017 rows with ZIP CRC checks, verify every incoming
property hash and global previous-key presence. The canonical October FGB
generation `1791037142062116`, size `5816513152`, SHA-256
`f8e4f37133cbb1876117a3489708ee89d9e592474635219adc03eb6d00032ab8`
was independently downloaded and verified. All 2,451 needed canonical
footprints match their metadata hashes. Incoming polygon footprints are
represented by their exact hash-matching canonical candidates; the November
polygon bytes were not downloaded or visually inspected. The two changed
incoming MultiPoints were independently extracted from the pinned November SHP
and their normalized hashes match the worker logs. This reconstructed review
is not a worker-retained bundle and is not backfilled into the alert.

The new proposal contains 1,322 cases: keep 1,129 existing-key IDs, reuse 192
globally absent predecessors' IDs, and assign a fresh ID for the additional
Serpentine recognized-reserve designation while preserving its surviving
private-stewardship sibling. The reuse group includes 125 Swiss 2023-to-2025
year changes and two spelling-only corrections. Its exact proposal SHA-256 is
`e2b0fdfab0c51eb76104e8509635daa21cbc83404ddd357a06d289583ab3779e`.
The remaining Naufrage key `556329125` requires a separate choice: preserve
`244070` as continuity or assign a fresh ID for its PA-to-OECM reclassification.
The maintainer subsequently answered “Approve all 1,322 recommendations” for
that exact digest and “Preserve ID 244070 as continuity” for Naufrage. The
accepted additions therefore contain 1,129 keeps, 193 predecessor reuses and one
fresh allocation. [PR #379](https://github.com/SkyTruth/shared-datasets-1/pull/379)
has merged these exact release-scoped decisions. The
earlier 117 accepted cases remain unchanged; there is no broader standing
policy approval. Schedule activation awaits protected recovery and actual
signed terminal and publication verification.

PR #379's accepted head `cd4fe5de345be9f42ed1a0351edf02b656776cab` passed
all seven local suites at `_scratch/ci-preflight-d4042dhb/preflight.json`,
including seven actual Linux AMD64 images and 42 browser tests with no skips,
unexpected results, flakes or retries. All 1,323 declarations and complete
captured cases matched the pinned 497,914-row previous allocation baseline;
the real validator accepted them with no unresolved cases or keep/reuse target
collisions. The focused checks passed 52 tests and 40 subtests. The shipped-file
legality test now exercises the offline CLI instead of supplying the expanded
file an incomplete two-key fixture baseline. Runtime legality checks are unchanged.
Hosted `41652461233/1` passed, including `ci-ready` `125976836302`.
The normal merge produced `4c4184693752ae7e4810cc871eca03d65f372db1` at
`2026-11-03T14:25:58Z`. Main CI `41653631612/1` completed successfully,
including `ci-ready` `125981691906` and exact WDPA image artifact `13238747496`.
Automatic WDPA job `125982069709` deferred before mutation at
`2026-11-03T14:33:56.6250968Z`; it did not install an image or create a worker.

Fresh read-only eligibility checks at `2026-11-03T14:42:12Z` verified the
accepted curation and tested current main, the retained image artifact, signed
failed prior deployment `7514215604` / `21254231162`, replay eligibility,
paused and drained controls, both current publication gates and the original
repaired receipt intent. Both assets remained October releases with no claims,
counters `17943` / `497919`, and valid current receipts/indexes. The resulting
`pr379-pre-canary-baseline.json` is the actual history before the next operation.
One [protected deployment 41656494930/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41656494930/attempts/1)
was dispatched at `2026-11-03T14:43:26Z` with executor
`4c4184693752ae7e4810cc871eca03d65f372db1`, source CI `41653631612/1`,
`canary_run_date=2026-11-01` and `cancel_running_canary=false`.

Actual signed record `7521868392`, initial status `21262456550` (**started**
at `2026-11-03T14:46:11Z`), pins image
`us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/wdpa-monthly@sha256:020bb9264a7a10bf67b12dcab264ccfd9658f4e39ba6ae67132827be7eb3e86d`
and applies only `module.wdpa_monthly_job.google_cloud_run_v2_job.this`.
Independent actual GitHub attestation and source verification passed at
`2026-11-03T14:48:18Z`. The actual canary `wdpa-monthly-bschh` was created at
`2026-11-03T14:46:48.530788Z`; its native **Started=True** transition is
`2026-11-03T14:48:20.913115Z`. At `14:50:03Z`, it had `runningCount=1` and
was the only active WDPA execution, using the exact signed image and an
execution-only `RUN_DATE=2026-11-01`. Live worker resource and environment
checks passed, including no persistent monthly date or promotion-bundle pin.
The scheduler remained paused; both October publications, absent claims,
counters, valid current receipts/indexes and the original repaired intent were
preserved. These are pre-publication observations, not November readiness.
No execution logs were requested, so no log-health conclusion is drawn.

The protected workflow completed its bounded watch successfully. Its signed
handoff status `21263316372`, **verification_pending** at
`2026-11-03T14:57:12Z`, explicitly binds `wdpa-monthly-bschh`; the signed
record retains the exact `020bb` image above. Independent actual GitHub
attestation and source verification passed again at `14:59:02Z`. A state-only
refresh at `14:59:05Z` confirmed the same sole running execution, paused
scheduler and unchanged pre-publication safety state. This is the normal
asynchronous handoff. Signed terminal acceptance and both actual November
publications remain unestablished.
The active follow-up observes this exact execution; no additional deployment
or worker is dispatched. Failed-candidate helpers are archived, and activation
checks now bind the actual `bschh` execution, signed record, exact image and
its own fresh PR #379 pre-canary history. Every original-intent, receipt,
publication, runtime and schedule guard remains intact. Those checks run only
after a signed verified terminal result.

Main job `125982068836` completed protected decision reconciliation successfully.
Independent private-journal reads at `2026-11-03T14:37:35Z` verified all 1,323
new proofs against the accepted head and merge, bringing the approved index to
1,440 November cases. Index generation `1793716411580077` and event generation
`1793716415796358` retain event
`b238cb59f39c30d472230a34fb21e0ba2fb44ea011c494576e5192a26dd8d8b9`.
The same original parent `1793709384.828459` is green **Decision recorded**,
with exactly one confirmed reply `1793716415.191629` naming `jonaraphael`,
PR #379 and the accepted rationale. Direct Slack reads confirmed that result.
This proves decision reconciliation, not publication or native Monitoring closure.

The same read confirmed an older display gap: adopted legacy parents
`1793644451.878259` and `1793650665.056749` still have their original
**Decision needed** text despite retaining exactly their existing confirmed
one-case and two-case replies. Their journal parent owner is `legacy`; the
current protected reconciler updates primary text only for owner `bot`.
Matching Monitoring review messages and the accepted decisions remain separate
facts. No duplicate reply or local parent mutation was attempted.

The maintainer then corrected the instruction: preserve the original request,
case summaries, context and links when adding the decision reply. Any status
marker must retain that original content; a **Decision recorded** summary must
not replace the request body. [PR #381](https://github.com/SkyTruth/shared-datasets-1/pull/381)
merged this rule in `AGENTS.md`, the incident-recovery skill and the identity
process as `d5f68cd550a76967d99271ca75b83c5bc959c5e0` at
`2026-11-03T15:11:52Z`. Both selected local preflight suites passed at
`_scratch/ci-preflight-aqewr2o1/preflight.json`; hosted `41660955489/1`
passed with `ci-ready` `126004388736`. The earlier legacy parents retaining
their requests and confirmed replies do not require destructive replacement.
The maintainer then authorized this recovery owner to merge the alert rollout
when safe. [PR #380](https://github.com/SkyTruth/shared-datasets-1/pull/380)
merged as `0148eabe259e309dbb210737c55a01de72d4d7c9` at
`2026-11-03T15:59:43Z`, accepting exact head
`e7329814cb3b6c9330a0f160dd454d29244c4b90` after all seven local preflight
suites and hosted `41667882160/1` passed (`ci-ready` `126031541868`).
Browser validation passed 42 tests with zero skips, unexpected/flaky results or
retries. The runtime correction retains original request text, case summaries
and pinned links, and records the decision in its existing reply. For bot-owned
parents already shortened by the former renderer, protected reconciliation
restores the request from the frozen event without repeating confirmed replies.
The original author's worktree remains untouched; integration used an isolated
worktree. Main CI [41669552634/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41669552634)
completed successfully at `2026-11-03T16:18:54Z`; `ci-ready` `126037078597`,
protected alert apply `126042937645` and decision reconciliation
`126045356753` succeeded. WDPA deployment `126045359868` was unselected.
Independent cryptographic/source verification at `16:20:17Z` accepted signed
applied records `7524014025` / status `21269309588` (Monitoring IAM),
`7524088767` / `21269479281` (transport) and `7524173200` / `21269786059`
(alert sync). The ready receiver is `monitoring-slack-00013-cbr`, image
`sha256:023db1e1c16cfca9a6dba355fbb54992d1d77c5ac73aa1df6e2895e2d6cdf9fe`;
its registry manifest config matches the exact main-tested config
`sha256:b7ddf498bb67abc0d480baf3f48dbda96ffc73767c46f8107c6054afce9a1459`.
The failure policy has exact execution/error extractors while its coverage and
grace strategy are unchanged; the reader role contains only
`monitoring.alerts.get` and `logging.logEntries.list`.

Direct Slack and generation-pinned journal reads at `16:20:17Z` verified the
original requests were restored exactly from their unchanged frozen events:
1,323-case parent `1793709384.828459` retains its one confirmed reply
`1793716415.191629`, and 114-case parent `1793665143.751309` retains its one
confirmed reply `1793668427.085829`. Their event generations are now
`1793722730151206` and `1793722728470221`. The two adopted legacy requests
also remain intact with their original single confirmed replies. Their retained
**Decision needed** request title is historical context, not a new approval
requirement. No local Slack mutation, duplicate reply or synthetic production
alert was used. The separate `bschh` operation retained its tested `020bb` image.
Its subsequent publication acceptance is recorded below; native Monitoring
closure remains a separately verified fact.

[PR #377](https://github.com/SkyTruth/shared-datasets-1/pull/377) raises bounded
review capacity to 2,048 while retaining all existing byte, previous-record,
integrity, staging and publication checks. The 1,323-case regression reproduced
the original rejection before the fix, then passed through real disk capture,
persisted validation, create-only staging and offline rendering with every case
present. A 2,049-case review still fails before capture is written.

The repair also found a validation dependency gap: the catalog-viewer image
copies the shared review validator and eight other ingestion files, but their
changes did not select its image/browser checks. A Dockerfile-derived regression
reproduced all nine omissions. The shared dependency declaration now includes
those consumers while keeping deployment selection separate. All 238 focused
tests and Ruff passed. Intermediate head `469b541` passed its four selected
local suites; final head `beed5875bcb8f66246a980459d55bcb3a42558ab` passed
all seven required local suites after the dependency prerequisite, including
all seven Linux AMD64 production images and 42 browser tests with no skips,
unexpected results, flakes or retries. Final evidence is
`_scratch/ci-preflight-4d27ilvo/preflight.json`.

Hosted CI `41646294461/1` passed, including `ci-ready` `125955024761`.
PR #377 merged as `873d6cb53aa2a2888b59559a034b764333cff409` at
`2026-11-03T13:46:13Z`. [Main CI 41647315555/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41647315555/attempts/1)
completed successfully, including `ci-ready` `125958614416` and WDPA image
artifact `13235164058`. Automatic WDPA job `125958862615` deferred before
mutation at `2026-11-03T13:52:37.9585868Z` because runtime verification is
unavailable while the schedule is paused or an execution is active. Image
validation did not install the repair or start a worker. No semantic curation
was changed for this repair.


## Complete decision discovery added on current main

Independent [PR #382](https://github.com/SkyTruth/shared-datasets-1/pull/382)
merged as `02cfb77ae8d3b44b31d20b372397302c9e75a885` at
`2026-11-03T19:17:12Z`. Its shared publication-readiness contract inventories
all declared assets and partitions before expensive artifact builds or CI.
The new WDPA producer discovers both complete realms before its compatibility
sample or complete artifact build and retains verified analysis for reuse.
This applies to future recovery work; it did not change the then-running
`bschh` image or transfer its ownership.

[Main CI 41701840527/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41701840527)
completed successfully with `ci-ready` `126150545516` and retained WDPA image
artifact `13264571294`. Its selected automatic WDPA job `126152577927`
deferred before mutation at `2026-11-03T19:41:51.7179614Z` because the
schedule was paused or an execution active. Independent post-job state and
signed-record reads at `19:53:44Z` and `19:53:50Z` still bind only the existing
`bschh` / `020bb` / record `7521868392`, with verification pending, a paused
schedule, unchanged October publications and counters, and the original
repaired intent preserved. Tested-image validation did not install that image,
create a WDPA deployment record or start another worker. This recovery did not
take over PR #382's implementation or deployment.


## Verified publication and live activation

The exact `wdpa-monthly-bschh` execution completed successfully at
`2026-11-03T23:07:53.494159Z`, after 8 hours 20 minutes 45.76 seconds.
It retained the PR #379 executor
`4c4184693752ae7e4810cc871eca03d65f372db1` and exact tested image
`us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/wdpa-monthly@sha256:020bb9264a7a10bf67b12dcab264ccfd9658f4e39ba6ae67132827be7eb3e86d`.
No later main revision replaced the worker or started another canary.

[Completion observer 41738263689/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41738263689/attempts/1)
succeeded on reviewed main `02cfb77ae8d3b44b31d20b372397302c9e75a885`.
Signed deployment `7521868392` received verified successful status
`21298495740` at `2026-11-03T23:20:51Z`, explicitly binding that execution.
Independent actual GitHub attestation, source and outcome verification passed
at `2026-11-04T01:46:27Z`; a workflow success alone was not substituted for
those checks.

The read-only activation gate then verified both actual canary-specific
`derived_complete` receipts, executor identity, current release-index result
generations, November success records, no active claims or workers and the
paused/drained schedule. Both existing publication validators passed.

| Asset | November rows | Receipt ID | Receipt generation | Current index generation | State generation | Next feature ID |
| --- | ---: | --- | --- | --- | --- | ---: |
| `wdpa-marine` | 17,994 | `3fe2371ac67256a3420b6d09ee626238b7ccf8819cb123a1317bbf68462ba0c6` | `1793745835453510` | `1793745835130906` | `1793745837119166` | `18370` |
| `wdpa-terrestrial` | 481,023 | `2756a9761e5a4614b081e6a11d7bca9106d190f25496148c1dd7866fdc47f637` | `1793747145818720` | `1793747145476576` | `1793747147664845` | `511914` |

Receipt SHA-256 values, in the same order:

- `wdpa-marine`: `ea82af600ba4bd2a671cf0b6ad0d7244dad8e51efa69356f6351b528c502dcfe`.
- `wdpa-terrestrial`: `2902e6efa9c67345b7ce5517431a81b96a7ebc1f7cbef04d47b2a2a9d336e099`.

Both release indexes report `latest_release=2026-11-01` and a successful
November 1 run. Their object versions exactly match the corresponding receipt's
`release-index` result. Feature-ID counters advanced through normal allocation;
no reservation or history was reset. The original check-in receipt
`348383de9527e070739cfaa18074b789de67703fcf88421ba7f97ebe6de68da5`
remains `derived_complete` at generation `1793641445980498`, SHA-256
`6e8ccf69ad61a0cd66c726acf0fd1c5ca531cf625c429d3b87bd6e68813ac9ac`,
with its original intent unchanged.

[Protected schedule resume 41756494303/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41756494303/attempts/1)
ran from that reviewed main with `job=wdpa-monthly`, `action=resume` and
completed successfully. Independent live verification at
`2026-11-04T01:50:18Z` confirmed **ENABLED**, cron `0 9 1-10 * *`, timezone
UTC, and the actual next scheduled time `2026-11-04T09:00:00Z`: November 4 at
**4:00 a.m. America/New_York**. The live worker retains 4 CPU / 8 GiB /
100 GiB DISK / zero retries, without persistent `RUN_DATE` or
`WDPA_PROMOTION_BUNDLE`. No extra worker was started for activation.

Canonical changes were made by the scheduled publisher under both asset roots
`gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/{wdpa-marine,wdpa-terrestrial}/`: dated
`releases/2026-11-01/`, matching `latest/`, run records and owned publication
controls. The two `_catalog/releases/{asset}.json` indexes were derived through
that same publication transaction. No canonical object was written locally.

Retained local machine evidence includes `verified-activation-readiness.json`,
`verified-wdpa-live-activation.json`, `heartbeat-signed-deployment.json`, the
exact schedule-control run and job log, and generation-pinned publication
snapshots in the recovery workspace. These local evidence filenames are audit
artifacts, not public dataset contracts. Earlier source and decision evidence
remains preserved; no full-source rescan was performed for activation.


## Alert reconciliation after activation

An authoritative Monitoring read confirmed the four failed-worker incidents
were CLOSED before their original Slack parents were reconciled. Protected
`resolve-workflow-alert.yml` runs updated those same parents quietly. Direct
Slack reads verified green **Closed by Monitoring** state and no thread replies:

| Failed worker | Monitoring incident | Original Slack parent | Protected reconciliation |
| --- | --- | --- | --- |
| `nzptt` | `0.oekm3455j6dq` | `1793671628.441279` | [41756742448/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41756742448/attempts/1) |
| `7zscj` | `0.oekq5fa5pnha` | `1793682300.687429` | [41756746300/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41756746300/attempts/1) |
| `47c69` | `0.oeku8bmqups0` | `1793693023.693619` | [41756749452/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41756749452/attempts/1) |
| `d7k4g` | `0.oel0h5pa5szu` | `1793709430.211649` | [41756753174/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41756753174/attempts/1) |

At initial activation, the four separate failed completion-check CI parents
remained open. The existing
legacy resolver rejected their observer jobs with
`every failed job must have a registered deployment recovery`, before any
Slack update. Those verification jobs are classified as CI scopes rather than
registered deployment targets; the guarded legacy path could not resolve them
from the newer successful WDPA deployment. Actual failed resolution attempts
were `41756725199/1`, `41756728835/1`, `41756732689/1` and
`41756738823/1`, corresponding to original completion attempts
`41564783412/1`, `41582724615/1`, `41600796640/1` and
`41639498652/1`. Direct Slack reads confirmed their original parents
`1793672229.079189`, `1793683265.658249`, `1793693513.152389` and
`1793710748.857879` were open with no replies at that observation.

The subsequent resolver repair verifies each exact observer job and its signed
failed WDPA runtime outcome against a newer signed `verified` WDPA deployment.
The normal notification workflow retains the original incident identities,
parents, failure history and delivery claims while recording recovery in its
signed ledger. Read-only verification of all four original attempts matched
deployment `7521868392`, status `21298495740` and the existing successful
completion check `41738263689/1`. The operational contract is documented in
[Slack incidents](../slack-incidents.md).
No failed worker or observer was replayed to make it green, no guard
was bypassed, and no manual Slack message or replacement parent was sent.
Earlier accepted-decision requests and confirmed replies remain separate from
these failure parents.
