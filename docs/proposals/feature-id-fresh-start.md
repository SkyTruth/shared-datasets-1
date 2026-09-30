# Feature-ID fresh start before launch

Updated: 2026-09-30. The user approved retiring the old identity contract because
there are no service users, and requested a simpler project-scoped deployment.
This plan supersedes the original exhaustive writer audit and blanket deployment
hold. Historical audit observations remain references, not current merge gates.

## Contract

Within an asset's `generated-2026-v1` identity contract, an allocated or reserved
ID must never identify an unrelated feature. Deletions, empty releases, crashes,
and retries cannot lower the durable counter. Only the owner of a publication
claim may expose its reserved IDs.

Old releases remain readable in their original release context. The same decimal
ID in an old and new contract has no implied relationship. No historical release
is deleted. Missing state after installation is an error, never permission to
initialize again. Reconstructing every historical allocation is unnecessary for
the approved new contract.

## Implementation

- Real WDPA and sea-ice publishers use the existing recovery core for ownership,
  reservations, exact object generations, receipts, and retries. EAMLIS keeps
  provider IDs and needs no reset.
- The reviewed reset inventory binds old release/latest object generations,
  the first new release date, and WDPA's translation supplement. Installation
  creates evidence, adoption, and allocation state once, under an immutable
  reviewed plan, with a durable journal. A retry cannot rewind advanced state.
- Existing protected identities perform the installation. The deployer reads
  the affected schedule/executions; the approved publisher writes the objects.
  The schedule must be paused and every execution finished before each write.
- Current generic publishers, localization, and index rebuild paths cannot
  bypass ownership of the three managed assets.
- Deployment verifies installed publication state and receipts before image
  builds or Terraform. Both WDPA assets are required for their shared job.
  Sea ice is independent. EAMLIS has no feature-ID deployment gate.
- First WDPA publication requires all six locale outputs and the exact reviewed
  supplement before reserving IDs. Later releases reuse their committed CSV.
  Reader caches distinguish historical and new release/path/generation context.

Removed from the draft: organization allow/deny/custom-role exports, the
approved-policy snapshot registry, its expiry/digest fields, the dedicated reset
account/provider, and the always-failing three-job deployment hold. These had
never been deployed. There is no legacy compatibility path for that draft state.

## Trust boundary and rollout

The existing project and organization administrators remain trusted. This change
prevents conflicting managed jobs and failed/retried publications from corrupting
ID history; it does not promise protection from administrators deliberately
restarting obsolete writers or changing permissions. No organization-level IAM
access is required. The existing deployer role gains only the scheduler pause
permission, through its existing protected Terraform sync. The protected
`Ingestion schedule control` workflow pauses either job and permits resume only
after completed publication.

Follow the [installation runbook](../feature-id-reset-installation.md): pause and
drain the affected jobs and earlier publishing/deployment runs, capture/stage the
exact reset inventory, review and merge each reset plan, deploy through the
existing protected workflows, validate first releases, then resume scheduling.
Keep schedules paused through the first canaries. Ordinary code PR #154 does
not itself contain an immutable reset plan and cannot publish/reset data merely
by merging; its deployment preflight refuses absent state.

Do not enable the optional Firestore lookup as part of this change. Native
FGB/PMTiles, metadata, all locale joins, publication state, and historical/new
release serving checks remain first-publication validation. A failed canary keeps
its reservation and schedule pause; it does not restart allocation.

## Translation evidence

The [translation rebuild report](wdpa-translation-rebuild-2026-09-29.md) records
complete current-ID rehearsals for marine (17,648 features) and terrestrial
(304,817 features): twelve locale files, 32,891,400 requested rows, and no missing
tasks. The user approved preserving official source names and URLs; descriptions
were translated and reviewed by the agent without claiming native-speaker review.
Both reset inventories must bind the corrected supplement at the exact URI,
generation, and SHA in that report. The first new-ID release rebuilds joins under
its new canonical IDs; rehearsal artifacts are not production publications.

## Verification and operational status

Tests cover simultaneous publishers/installers, crashes after durable writes,
retry after state advancement, missing-state refusal, paused/drained job checks,
immutable review authorization, translation completeness, and release-context
cache isolation. The [readiness report](feature-id-readiness-2026-09-29.md)
preserves the observed production inventory and earlier validation evidence.

Code readiness and production cutover are separate: this PR supplies the tested
implementation and deployment sequence. Production reset plans, installations,
canaries, and schedule resumption occur through that sequence after review.
No organization audit is a merge or deployment prerequisite.
