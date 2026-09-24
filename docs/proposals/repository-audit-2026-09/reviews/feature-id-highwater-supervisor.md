> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# Supervisor review — generated feature-ID sequence

Status: source accepted after revisions; **hold from merge/deployment** pending reviewed per-asset migration and publication ownership enforcement. Ingestion changes trigger deployment workflows on merge, so this is a material integration gate.

Branch: `codex/audit-feature-id-highwater`, base `1bf095d861d921e2378203495bd9a0da0bdf650c`.

I reviewed the allocator, compact baseline/snapshot model, GCS loader, actual WDPA/sea-ice producer propagation, offline migration boundary, documentation and regression evidence. I required revisions to add a usable evidence-bound migration path, select exact retained sidecar generations, reject coerced observed generations, avoid retaining/copying full prior payloads, normalize aware timestamps, reject ambiguous JSON and malformed inventory, verify manifest release/count, and reconcile native artifact writes with explicit allocation evidence.

Independent final validation from this worktree: eight affected suites — **206 passed, 2 native skips, 129 subtests passed**. Earlier independent seven-suite run also passed. The baseline evidence demonstrates ID reuse in both the allocator and actual WDPA builder. Six-release writer evidence shows next-ID 3,3,4,5,5,6 across deletion, return and an empty release.

The agent's broader checkpoint passed 777 tests but hit one unchanged private-executable fixture failure because this worktree resides under /private/tmp. The same test passes on main. No safety guard was weakened. Native integrations and live GCS were not exercised.

Invariant enforced: new generated allocations start at the persisted verified next-ID and cannot reuse a retired number merely because it disappeared from current records. Boundary changed: validated baseline construction and generation/hash-bound loading. Removed: max-live-ID inference, collision-skipping loop, output-max tracking and automatic broad WDPA legacy reinterpretation. No guessed seed, numeric override or automatic legacy trust fallback was added. Historical readers remain for explicit compatibility; returning absent keys receive new IDs.

The offline tool establishes consistency only of supplied evidence. It does not authenticate or certify globally complete historical allocation coverage. No production asset was audited or migrated. F must claim the captured baseline before newly allocated bytes become visible. These limits prevent calling B production-ready or independently merge-ready.
