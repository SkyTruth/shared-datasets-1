# CI and production execution contracts

[CI preflight](ci-preflight.md) owns validation/reruns; [recovery](ci-recovery.md)
owns failure handling. The [historical mapping](history/ci-failure-guardrails.md) retains audit evidence.
Reviewed publication follows main-push `ci-ready`, using complete Git/PR discovery
and same-repository merged PRs with immutable plans. Publisher/consumer checks bind
original acceptance, head/merge, caller/attempt, executor, bytes and generations;
body edits cannot change authority. Persisted v1 envelopes remain readable; v2
names the CI caller. Catalog refresh requires exact authorization and mutation
completion even when later notification fails. Owner-dispatched authorization
rehearsals verify acceptance/evidence without credentials or mutation; they never
waive live prerequisites. See the publisher/authorization commands for the schema.
