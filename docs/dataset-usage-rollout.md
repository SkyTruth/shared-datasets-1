# Daily usage rollout inventory

The shared collector and SDK changes must land before consumer reporting is
released. This chat owns final integration/recovery; record exact heads, required
CI, approvals and rollout receipts in each PR and the retained local evidence.

## Confirmed runtime bindings (read-only inventory, 2026-10-14)

| Producer | Runtime | Verified managed identity |
| --- | --- | --- |
| Catalog production | `catalog-viewer` | `catalog-viewer@shared-datasets-1.iam.gserviceaccount.com` |
| Cerulean UI production | `cerulean-ui-production` | `734798842681-compute@developer.gserviceaccount.com` |
| Cerulean UI development | `cerulean-ui-develop`, `cerulean-ui-test` | Same compute identity as production |
| Cerulean backend production | `cerulean-cloud-prod20240903-cr-orchestrator` | `prod20240903-cr-orch@cerulean-338116.iam.gserviceaccount.com` |
| Cerulean backend development | `cerulean-cloud-test-cr-orchestrator` | `test-cr-orch@cerulean-338116.iam.gserviceaccount.com` |
| Cerulean backend staging | `cerulean-cloud-staging-cr-orchestrator` | `staging-cr-orch@cerulean-338116.iam.gserviceaccount.com` |
| 30x30 production | `x30-prod-data` | `x30-prod-data-cf-sa@x30-399415.iam.gserviceaccount.com` |
| 30x30 development | `x30-dev-data` | `x30-dev-data-cf-sa@x30-399415.iam.gserviceaccount.com` |

Consumer adapters select only these verified runtimes using Cloud Run's
server-provided `K_SERVICE`, never a browser application/environment label.
Unknown and local runtimes do not report. `DATASET_USAGE_ENABLED=false` pauses a
consumer reporter. UI environments share a runtime identity, so environment is
application-reported rather than independently attested by different principals.
See [the Cloud Run runtime contract](https://docs.cloud.google.com/run/docs/container-contract#env-vars).

## Prerequisites and pending registrations

Cerulean UI adoption depends on [PR 363](https://github.com/SkyTruth/cerulean-ui/pull/363).
Cerulean backend adoption depends on [PR 202](https://github.com/SkyTruth/cerulean-cloud/pull/202).
Do not merge or modify another owner's branch to finish this rollout. Prepare
focused follow-up changes and reconcile current main after the owners land them.
TypeScript consumers need the verified `0.13.0` registry artifact; Python
consumers need an immutable archive of the committed SDK implementation. Verify
real package installation, not just local imports, before consumer deployment.

Monitor frontend uses an Alerts App Engine API, rather than the reserved Monitor
reader identity. Both `skytruth-monitor` and `skytruth-alerts2` runtime inventory
reads returned 403; the backend repositories have no GitHub Actions deployment
records supplying an alternate live identity proof. Keep the producer registered
and unconnected until the application owner supplies a verified runtime identity
and shared-dataset consumption boundary. Do not guess an account or grant a
reserved reader account write authority.

SkyTruthTech is a project registration, not a distinct proven consumer. Its
inventory includes `dc-api`, `dc-web`, `tipg`, and `tipg-mining`; their shared-data
consumption boundary remains unverified. Environmental-cost-of-war code also
references shared datasets, but no managed reporting identity is confirmed for
its local/development usage. These registrations remain visibly unconnected;
absence of receipts never establishes zero use.

## Rollout order and evidence

1. Review/merge the shared implementation and verify protected deployment and SDK
   publication. CI deploys the compatible dashboard before the collector. The
   writer's saved-plan guard requires the latest signed, verified dashboard
   deployment to contain the current reader bytes, including for standalone
   workflow dispatches. Collection starts disabled; keep legacy exports disabled.
2. Pin real SDK bytes in focused consumer changes, finish their prerequisites,
   run each repository's checks and deploy through its normal reviewed path.
3. Review measured incremental-cost forecasts for a controlled trial; enable
   native delivery with the scheduler paused, then run a protected canary.
4. Read the committed ledger's native probe token before read-only traffic
   checks. Bind anonymous and authenticated sample reads to that known nonce so
   they cannot inflate real-use counts. Verify delivered matching CSV records.
5. Observe real consumer receipts, cached-use behavior, writer isolation and
   bounded failures. Activate each source independently using current evidence.
6. Verify scheduled publications across distinct UTC days and retain cost/health
   evidence. Run the narrow retirement only after its live readiness predicate
   passes; preserve buckets, historical aggregates and prior recovery evidence.

The native health window retains a 48-hour delivery allowance. Two healthy probe
UTC days can therefore require more than two wall-clock days after activation.
Do not replace missing evidence with a passing status or fabricate receipts to
claim all-consumer connection.

## Adjacent finding

A dependency resolution outside Cerulean UI's lockfile selected WeatherLayers
2026.9.1 (requiring Deck.gl 9.4) against the project's Deck.gl 9.3 pin. Preserve
locked dependencies for this change. Resolve that independent upgrade in a
separate task; do not force conflicting peers to make usage adoption pass.
