> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# Supervisor acceptance: C — Python release cache

Accepted for user review; not committed, published, or deployed.

I read the code, regression/adverse tests, and all three documentation diffs. I required a revision for main versus point-companion PMTiles selection: using the FGB canonical basename for all formats would have rejected a legitimate PMTiles pair. The corrected rule derives the known companion filename and still rejects ambiguity; its regression passes.

I independently ran the final SDK, redirector, and repository-guardrail suites from this worktree: **93 passed, 138 subtests passed**. `git diff --check` passed. The final post-stat index disappearance and public-403 tests pass without fallback to latest-only behavior.

Accepted invariant: fetch selects one release-index response, pins artifact generation, verifies bytes, and uses URI+generation cache identity. Local cache corruption, same-date replacement, static CSV dates, concurrency, missing explicit versions, and failed downloads are exercised. Public transport does not obtain ADC credentials; authenticated reads keep generation preconditions.

Accepted compatibility changes are explicit in the docs: latest resolve remains an unpinned local URL mapper; fetched IDs now include generation and must be recorded with URI; old cache entries are retained but ignored; cache reuse requires current identity resolution and integrity verification, with no offline fallback. Legacy missing/empty-index assets remain supported using an observed object generation, without invented release history.

Removal pass replaces date-only/existence-only cache success, arbitrary first-format selection, and unpinned downloads. Narrow legacy absence and disposable-cache repair handling are justified boundary behavior. Downloading and SHA-256 verification use bounded memory with a second disk pass; no one-pass I/O claim. No live GCS or package publication occurred, so remote integration remains unverified.

Branch: `codex/audit-python-release-cache`. Five intended files changed. Independent of other source branches; narrow consumer documentation edits may need conflict resolution with B/D. User review and any requested commit/merge remain outstanding.
