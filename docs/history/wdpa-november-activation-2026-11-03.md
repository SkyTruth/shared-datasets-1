# WDPA November activation evidence (2026-11-03)

This completed incident chain is historical. Use
[the monthly runbook](../wdpa-monthly-runbook.md) for current operation and
[CI/deployment recovery](../ci-recovery.md) for new failures. The observations
below are bound to their recorded attempts and do not establish current live state.

November canary `wdpa-monthly-nzptt` failed in the compatibility sample because
full-release curation included unsampled keys. Its signed failed terminal outcome
and unchanged publication state are recorded in the
[November evidence](../wdpa-processing-evidence/november-readiness-2026-11-01.md#compatibility-sample-decision-scope-failure).
The scoped replay repair is [PR #373](https://github.com/SkyTruth/shared-datasets-1/pull/373);
its replacement canary `wdpa-monthly-7zscj` subsequently failed before publication
when its cache sampler was denied access to a descendant process FD directory.
The exact failed signed outcome and absent candidate receipts are recorded in the
[resource-monitor failure evidence](../wdpa-processing-evidence/november-readiness-2026-11-01.md#descendant-process-cache-sampling-failure).
The resource-monitor repair [PR #375](https://github.com/SkyTruth/shared-datasets-1/pull/375)
has merged. Its replacement `wdpa-monthly-47c69` passed full marine validation,
then failed before publication while serializing the complete-build report.
The [report failure evidence](../wdpa-processing-evidence/november-readiness-2026-11-01.md#complete-build-report-serialization-failure)
records its signed failed outcome and absent candidate receipts. The report-path
repair [PR #376](https://github.com/SkyTruth/shared-datasets-1/pull/376) is merged;
its protected replacement canary `wdpa-monthly-d7k4g` reached full terrestrial
allocation, then failed when 1,323 new identity cases exceeded the 1,024-case
review-capture cap. Its signed failed outcome and absent candidate receipts are
recorded in the [terrestrial review evidence](../wdpa-processing-evidence/november-readiness-2026-11-01.md#full-terrestrial-review-stop-and-capture-capacity).
The prior 117 approvals did not cover these new cases. The maintainer has now
explicitly accepted all 1,323 choices, including Naufrage continuity as ID
`244070`; [PR #379](https://github.com/SkyTruth/shared-datasets-1/pull/379)
has merged their release-scoped curation after all required checks passed.
Main validation passed; the next protected deployment was dispatched as
[41656494930/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41656494930).
Its exact canary `wdpa-monthly-bschh` completed successfully at
`2026-11-03T23:07:53.494159Z` and published both November realms.
The bounded review-capacity repair
[PR #377](https://github.com/SkyTruth/shared-datasets-1/pull/377) is merged;
its main CI passed and automatic WDPA deployment deferred before mutation
while the schedule was paused. That repair alone did not supply semantic decisions.

Independent verification accepted signed deployment `7521868392`, terminal status
`21298495740`, both actual canary receipts and their current release-index
generations. Marine published 17,994 rows and terrestrial 481,023 rows for
`2026-11-01`; no claims or active workers remained. The original repaired
check-in intent and receipt were preserved.

[Protected schedule control 41756494303/1](https://github.com/SkyTruth/shared-datasets-1/actions/runs/41756494303/attempts/1)
then resumed only `wdpa-monthly` after all readiness checks passed. Live
verification confirmed `ENABLED`, `0 9 1-10 * *`, UTC, with the next run at
`2026-11-04T09:00:00Z` (November 4 at **4:00 a.m. America/New_York**).
The installed worker has no persistent date or promotion-bundle pin.
See [the final activation evidence](../wdpa-processing-evidence/november-readiness-2026-11-01.md#verified-publication-and-live-activation)
for exact receipts, generations and verification provenance.
The four failed-worker Monitoring parents were quietly reconciled after
authoritative closure. At initial activation, four older completion-check CI
parents stayed open because the resolver lacked recovery coverage for their
observer-job scopes. The evidence retains the rejected attempts. Historical
completion incidents now use the signed worker-failure and recovery checks
documented in [Slack incidents](../slack-incidents.md) to update their original
parents and persist closure in the notification ledger.
