# Concurrent agent integration

Parallel editing can proceed in focused worktrees. Final integration has one
accountable owner per PR; a related group also names the owner who decides its
integration order. Ownership is procedural and visible in the PR body, not an
expiring permission to merge or deploy.

Record the owner chat, branch, PR, overlapping files and prerequisite PR numbers.
Use explicit dependencies when another PR changes a shared validation contract,
removes a consumer, or supplies a required deployment prerequisite. Unknown
dependencies remain conservative. Do not message another chat or merge its PR
without human authorization. If an owner abandons work, report the gap and obtain
an explicit handoff; do not invent a second owner or an indefinite automatic wait.

## Final boundary

1. During editing, run focused checks for the changed behavior and its negative
   controls. Verify tools and external prerequisites with cheap read-only probes.
   Keep full preflight for the final boundary. An explicitly requested early
   checkpoint remains provisional and cannot replace final validation.
2. Finish known prerequisites and reconcile overlapping changes. Fetch current
   main and perform any main merge required by strict branch checks **before**
   final preflight. Commit only task files, then freeze that checkout and head.
   Do not start a second preflight or keep editing while the first is running.
3. Run the complete clean-checkout command in `docs/ci-preflight.md` against the
   exact committed head and current main. Its local kernel slot admits one full
   driver per user/host. A busy result fails before expensive work; inspect the
   named owner rather than running a polling or automatic retry loop. Different
   task work directories do not create additional slots. Focused checks remain
   available while another owner validates.
4. Inspect the final evidence and recheck remote main, source head and cleanliness
   before push. If any required identity changed, resolve it and rerun complete
   preflight. An unchanged qualifying completed run need not be repeated solely
   to prepare a PR body or wait for hosted checks. Record its exact evidence path;
   local evidence is never a hosted pass or a review/deployment authorization.
5. Push the final revision once. Read-only PR-body/evidence updates need no code
   push. Wait for required hosted checks on that head and current base. The
   workflow cancels obsolete PR graphs together; main runs and dispatches retain
   independent evidence. Do not manufacture a push to recover an external outage.
6. Only the authorized integration owner may perform a normal merge, with the
   exact head guard, required checks and review boundaries intact. This runbook
   does not grant merge authority. Observe main CI and any separately authorized
   deployment/incident reconciliation as distinct terminal outcomes.

## Failure and follow-up ownership

Classify the failed boundary before retrying. Reproducible tests, missing
prerequisites, denied authority, stale evidence and incompatible contracts need
repair. For transient read-only CI failures, the same owner may retry only failed
jobs on the live revision. Bound recovery to two additional attempts, with
15-minute then 60-minute cooldowns (or a longer provider interval). If the same
outage repeats, wait for new recovery evidence rather than spending the remaining
budget automatically. Never increase retries to defeat throttling.

A requested follow-up belongs to the existing owner. Store its PR number,
prerequisites, purpose, terminal condition and retry budget. On each wake, read
the live PR head, main and run attempt; saved SHAs are observations, not authority.
Stay quiet while prerequisites are unchanged. Stop acting when the PR is merged,
closed or superseded, and disable its own completed schedule when authorized.
Never mutate an unrelated automation. A new commit invalidates old validation;
it is not a reason to retry the old run.

The kernel releases the local slot on normal failure, cancellation and process
death. A stale PID record never blocks a new owner, and no TTL or lock-file
deletion is needed. A crash may leave Docker processes or containers behind:
the recovery owner inspects exact surviving work before another heavy run and
cleans up only process-owned resources within existing authorization. The slot
does not claim to coordinate legacy checkouts, direct Docker commands, other
users or other hosts.

## Preserved boundaries

Local and hosted validation remain separate. No evidence may cross incompatible
heads, bases, tested trees, toolchains, contracts or Actions provenance. Missing,
failed, cancelled and unexpectedly skipped evidence still fails the gate.
Full-history checks, native fixtures, Node 22/24, browser no-skips/no-retries,
exact tested images and protected deployment authorization remain required.
Test selection and deployment selection are separate contracts.

See the [October investigation](ci-validation-investigation-2026-10-09.md) for
the historical trace, measurements and deliberately deferred work.
