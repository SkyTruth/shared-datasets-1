> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# F2 publication core/layout handoff

Status: implementation complete for supervisor review; F3 adapters and rollout are **not implemented or approved**. Branch `codex/audit-publication-recovery`, base `1bf095d861d921e2378203495bd9a0da0bdf650c`, worktree `worktrees/publication-recovery`. F1 remains separately frozen and unchanged.

## Reviewable scope

Eight F2 files only: `ingestion/common/publication.py`, `ingestion/common/publication_gcs.py`, `scripts/gcs_asset.py`, `tests/test_publication.py`, `tests/test_publication_gcs.py`, `tests/test_gcs_asset.py`, `tests/fixtures/dataset-mutation-authorization-v1.json`, and `docs/standards/asset-layout-and-formats.md`.

Frozen patch: `reviews/publication-recovery-f2.patch`, 107485 bytes, SHA-256 `acbe1eae35c61272c923c59424ee33ce39b87dd38c28f0eb83d9b1603be566e9`. File hashes, branch/base, exact E fixture equality and unchanged F1 hash are in `evidence/publication-recovery/f2-final-inputs.json`. Patch includes new untracked files without staging. F1 patch remains SHA-256 `2deeb2050d2d8b4be056000d7d40201899afa79f7584851e19d35e0920e3e3c4`.

## Implemented contract

The dependency-light core persists one immutable intent/receipt per stable proposal before claiming one asset. Resume finds that receipt before preparation; a changed original executor contract refuses. Claim CAS compares the B-compatible identity snapshot and reserves the generated high-water before allocation-bearing checkpoint or dataset bytes. Generated backfill/stale predecessor refuses. Metadata/repair claims preserve allocation state without creating ranges. Managed deletes are unsupported.

Every operation retains original source identity, destination generation, content/cache headers, approved user metadata, and ownership tags. Receipt parsing revalidates shape, operation order, target ownership, result metadata and dependency closure. Mandatory semantic validation runs on every resume; mandatory source preflight runs before first preparation is persisted. Neither callback has a permissive default. **The tests inject deliberately small semantic/derivation callbacks; actual bundle closure and finalization implementations remain F3 responsibilities.**

Commit is manifest-last and receipt-before-current-state. State is checked against its actual own-asset adoption/current/active receipts, including committed high-water monotonicity. Crashes reconcile tagged outputs and original expectations; equal unrelated bytes confer no ownership. Completed older receipts support status/notification without reacquiring current. Delayed workers cannot refresh CAS after a newer transaction. No automatic claim expiry or allocation cancellation exists.

Local checkpoints become durable only after claim. A missing uncheckpointed source is explicitly `NOT_RECOVERABLE_SOURCE` and leaves ownership/reservation held. Completed checkpoints permit exact generation-pinned recovery; native rebuild reproducibility is not assumed. Notification journal has independent pending → sending → sent/failed/unknown transitions. A known failed delivery can explicitly retry with a new attempt ID and receipt CAS; uncertain sends refuse retry and stale callbacks cannot finish a later attempt.

The GCS adapter strictly parses JSON authority records, rejecting duplicate keys and nonfinite numbers. It hashes generation-pinned downloads, freezes upload bytes, and sends copy ownership metadata with both source and destination preconditions in the same SDK rewrite request. Unrelated destination metadata is inspected before any large download.

Layout validation permits only approved operational state/receipt/checkpoint paths. Generic mutations into that namespace refuse; malformed operational paths also refuse. Existing scratch behavior is preserved, including a pending proposal ID named `publications`. Existing canonical writers have **not** been migrated and are not coordinated by this core.

## Validation and evidence

Run from the assigned worktree with the charter's existing uv environment, without syncing/installing:

- Baseline path regression: 9 failures captured in `evidence/publication-recovery/f2-baseline-paths.txt`. Original publication defect reproducers and F1 failures remain alongside it.
- Supervisor scratch namespace regression failed before its correction: `f2-scratch-namespace-before.txt`.
- Focused final core/adapter/path tests: **44 passed, 91 subtests** (`f2-final-focused-tests.txt`). These inject loss after every durable transition and cover source loss, competing claims, corrupt receipts/state, all reservation boundaries, old delayed index writes, E replay identities, notification failure retry and uncertainty refusal, SDK preconditions/metadata, and strict JSON.
- Full suite initially: **779 passed, 503 subtests, 4 skipped, 1 failed**. The failure is the unchanged Terraform test creating its private executable under this `/private/tmp` worktree, which the unchanged production resolver intentionally rejects. Both files equal base (hash record); this is unrelated to F2.
- Final full suite after namespace correction, excluding that single location-dependent test: **781 passed, 509 subtests, 4 skipped, 1 deselected** (`f2-final-full-tests.txt`). Four native integration skips explicitly require runnable GDAL/native tooling or opt-in; no native claim follows from them.
- Ruff on the six F2 Python files, catalog docs check (24 current assets, existing source placeholder warnings), and `git diff --check`: pass.

## Removal and compatibility review

The generic operational namespace bypass is removed; scratch remains unchanged. No existing writer loop is removed in this core-only phase because doing so without approved adapters would leave split semantics. The core owns one receipt/state schema and delegates domain closure explicitly; no second allocator, approval verifier, notification sender, pointer protocol, or delete-recovery mechanism was added. New operational JSON is a bounded layout extension; checkpoint suffixes reuse approved formats. IAM and deployment code are unchanged.

F2 alone does not resolve existing writer races. B/F2/F3 must remain held from isolated merge/deployment until the executable rollout gate and actual adapters are accepted. Direct multi-object `latest/` remains non-atomic; unclaimed legacy/historical binaries require external quiescence before adoption. No migration, seed, external write, environment change, index/history mutation, commit, push, PR, or merge occurred. Retained local evidence is limited to this remediation directory.

Review the F2 patch, then independently run the three focused test files. The forthcoming `plans/publication-recovery-f3.md` is plan-only and must be separately approved before source changes.
