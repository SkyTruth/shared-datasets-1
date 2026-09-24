> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# F2 supervisor review

Decision: source accepted as a frozen core/layout increment. HOLD merge and deployment pending F3 adapters, executable rollout fence, reviewed adoption and old-writer quiescence. Existing publication races are not resolved by an unused core.

Frozen patch SHA-256: `acbe1eae35c61272c923c59424ee33ce39b87dd38c28f0eb83d9b1603be566e9`. Independently verified every final file against the handoff manifest and shared E fixture byte equality. Read core, GCS adapter, layout/namespace changes, docs and behavioral tests; reviewed revisions for current/active/adoption state integrity, reserved high-water monotonicity, terminal old replay, user metadata, strict JSON and explicit failed-notification retry.

Independent focused suite: **112 passed, 175 subtests** across publication/core adapter/path/F1/guardrails. Combined accepted A+B+C+E+F1+F2: **891 passed, 4 skipped, 1 deselected, 770 subtests**. The four skips require native tools/opt-in. The unchanged location-sensitive Terraform fixture was explicitly deselected because `/private/tmp` violates its private-executable premise; it passes on root main. `git diff --check` passed. Exact results: `evidence/integration-after-f2.txt`; exact input hashes: `evidence/publication-recovery/f2-final-inputs.json`.

Accepted invariant: one immutable proposal receipt and one asset owner, fixed source/destination generations, generated allocation reservation before publication, manifest-last commit, protected per-asset derivations before ownership release, separately journaled notification attempts. Lost acknowledgments can adopt only owned exact bytes/metadata. A missing uncheckpointed source retains ownership/reservation and reports nonrecoverability.

Boundary/removal: generic GCS mutations cannot enter the operational namespace; ordinary scratch proposal names remain valid. Historical-equality adoption, refreshed destination expectations, untrusted old receipt state and automatic uncertain-notification retries are rejected. Actual writer loops remain temporarily because core-only routing would be incomplete.

Remaining limits: F3 must provide actual semantic bundle closure, generation-complete finalization, all writer adapters and deployment gate. Callbacks in core tests are deliberately small and are not product-level validation evidence. No multi-object atomic latest guarantee, claim expiry/reset, managed delete recovery, live GCS test, adoption, migration, remote write, commit or merge occurred.
