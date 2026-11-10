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

Use [validation before push](ci-preflight.md#validate-before-push) as the single
ordered checklist. Before handing a frozen head to that procedure, the
integration owner must finish prerequisites, reconcile overlapping changes and
perform any authorized current-main merge required by strict checks. Complete
[publication readiness](publication-readiness.md) before expensive publication
builds/validation; an early checkpoint stays provisional.

The local full-preflight slot is exclusive across worktrees. Use `--wait` to queue
on a busy slot and record the wait; do not edit or commit in the validating
checkout. Different task work directories do not create extra slots. The
[preflight isolation contract](ci-preflight.md#isolation-and-tools) owns lock
release and surviving process-owned Docker work after interruption.

Only the authorized integration owner may merge with the exact head guard,
required checks and review boundaries intact. This guide grants no merge
authority. Observe main CI and any separately authorized deployment/incident
reconciliation as distinct terminal outcomes. Read-only evidence/PR-body updates
need no new commit or hosted run when qualifying validation remains unchanged.

## Failure and follow-up ownership

Follow [CI/deployment recovery](ci-recovery.md) for classification and the
single bounded retry policy. Reproducible defects, missing prerequisites,
denied authority and stale evidence require repair, not unchanged reruns.

A requested follow-up belongs to the existing owner. Store its PR number,
prerequisites, purpose, terminal condition and retry budget. On each wake, read
the live PR head, main and run attempt; saved SHAs are observations, not authority.
Stay quiet while prerequisites are unchanged. Stop acting when the PR is merged,
closed or superseded, and disable its own completed schedule when authorized.
Never mutate an unrelated automation. Run `uv run ruff check .` before every
commit. A new commit requires local evidence for that head: use a full run for
the first push and after contract or toolchain changes, or `--since <validated-sha>` with passing evidence on the same
base and contract. Conflict-free base-only movement, PR-body edits and hosted
waits need no local rerun. Hosted CI still runs the full PR selection; do not
retry an old hosted run for a new commit.

## Preserved boundaries

Local and hosted validation remain separate. Hosted evidence may not cross
incompatible heads, bases, tested trees, toolchains, contracts or Actions provenance. Local
`--since` explicitly records prior suite coverage without rebinding prior
receipts to the new SHA; it requires the same base, contract and toolchain. Missing,
failed, cancelled and unexpectedly skipped evidence still fails the gate.
Full-history checks, native fixtures, Node 22/24, browser no-skips/no-retries,
exact tested images and protected deployment authorization remain required.
Test selection and deployment selection are separate contracts.

See the [October investigation](history/ci-validation-investigation-2026-10-09.md) for
the historical trace, measurements and deliberately deferred work.
