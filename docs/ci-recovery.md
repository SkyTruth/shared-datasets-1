# Selective CI and deployment recovery

Classify a failure before retrying it. A legitimate PR rejection demonstrates a
working gate. A reproducible failure after merge is a coverage or release-contract
defect that needs a regression test. Hosted-runner allocation, network, registry
and authentication outages are operational failures; preserve their original
run and attempt when recording recovery. Controlled alert probes belong in their
explicit workflow and do not count as ordinary deployment failures.

## Validation and runner failures

Inspect the failed job, its annotations and the first failing command. Verify
that the revision and comparison base still match the intended change. Fix
reproducible code or contract failures, then repeat preflight on the resulting
revision. Do not turn missing tests, missing tools or unexpected skips into
success to clear the check.

For a transient runner or network failure in a job that cannot mutate production,
rerun that specific job after the dependency recovers. Keep the failed attempt in
the reliability ledger. Retrying a whole workflow is unnecessary when its other
jobs passed. If the base or source revision changed, validate the new integration
result instead of relying on an old successful attempt.

## Production mutations

Never blanket-rerun a production workflow or automatically cancel an active
mutation. First inspect the exact source SHA, artifact identity, authorization,
deployment record, Terraform state and any publication receipts. A GitHub timeout
or cancelled worker does not prove that its Cloud Run execution or remote writes
stopped. An asynchronous execution remains pending until its terminal observer
reports success, failure or cancellation; unknown status requires investigation.

If no mutation began, repeat the relevant protected prerequisite checks before
selective recovery. If a mutation began, reconcile the recorded attempt with live
state. Preserve the production-state lock and the saved-plan/allowlist sequence.
Never reuse a stale Terraform plan, retry an apply error blindly, weaken
permissions or regenerate an unchecked plan during recovery. The bounded wrapper
retries only lock-acquisition failures, before any mutation could begin. A
lock-release failure can follow partial or completed writes; it retains the
original failure and requires reconciliation before another apply.

Dataset publication recovery must preserve the original reviewed plan, captured
inputs, exact executor contract, generations, ownership and checkpoints. A newer
executor, different release date or mutable PR-body edit cannot authorize replay.
Completed mutations are verified no-ops; partial publications resume only through
their existing persisted protocol and restricted recovery path. Do not delete a
claim, reset allocation state or overwrite canonical objects to bypass it.

After reconciliation, report which job is safe to retry and why, the terminal
result, and any remote generations or paths changed. If the original executor or
required evidence is unavailable, leave recovery blocked and name the missing
contract rather than starting a new mutation.

See [alert routing](alert-routing.md),
[feature-ID reset installation](feature-id-reset-installation.md), and the owning
ingestion README for the target's existing recovery rules.
