# Review a generated feature-ID sequence migration

Old generated manifests can contain a regressed next-ID after deletion. Their
live records and numeric sequence fields cannot independently establish the
historic allocation ceiling. Existing IDs and hash algorithms must be preserved;
never choose a large arbitrary seed or reset the sequence from latest records.

`scripts/feature_id_sequence_audit.py` prepares an offline review candidate. It
makes no network calls or remote writes. Its successful parser result establishes
consistency of the supplied evidence only. It does **not** establish that the
files are authentic or that the inventory contains every historic allocation.

## Required evidence

Prepare one asset-scoped JSON inventory under the standard task temp directory.
Its schema is intentionally a small normalized inventory, not a GCS log parser:

- `schema_version`: `1`; `asset_slug`; `asset_root`: the canonical asset GCS URI.
- `coverage`: timezone-aware ISO `genesis` and `cutoff` timestamps and `evidence`, an array of
  local authoritative journal/event exports. Each export has `local_path`,
  `size`, SHA-256 `sha256`, and `provenance` identifying its source and acquisition.
- `objects`: all allocation-bearing generations, including overwritten/deleted
  history and partial publications. Each has canonical `path`, positive integer
  `generation`, `sha256`, `size`, `local_path`, `release`, `publication`
  (`committed` or `partial`), and `kind` (`metadata`, `legacy_metadata`,
  `manifest`, or `allocation_artifact`). Relative local paths resolve beside the inventory. Files are
  checked without copying them. WDPA legacy projection reads `SITE_PID`/`ext_id`;
  other legacy formats require explicit conversion evidence.
- `events`: a reconciled inventory of `write`/`delete` operations, each with
  `path`, `generation`, and timezone-aware ISO `observed_at`. Times are normalized
  to UTC before ordering, coverage checks, and evidence hashing. Write events and supplied object
  generations must match exactly; delete events must also have historical bytes.
  Review the raw exports against this normalized inventory. The tool does not
  independently parse or authenticate arbitrary log formats. Duplicate JSON keys,
  malformed inventory shapes, boolean versions/generations, and naive timestamps
  are rejected.
- `anchor`: `latest_manifest`, `release_manifest`, and `metadata`, each containing
  the exact `path`, `generation`, `sha256`, and `size` of the current migration
  baseline. Both manifest generations must contain identical bytes and reference
  the supplied metadata generation/hash. Legacy-only data needs a reviewed,
  coherent converted release bundle before manifest preparation.
- Optional `decision_evidence`: hash/size/local-path descriptors of exact reviewed
  decision JSON files. A historic reuse explanation names `release`,
  `action: reuse_previous_feature_id`, `reuse_feature_id`, `previous_identity_key`,
  `new_identity_key`, `reviewer`, `pr_reference`, and `rationale`. The reviewer must
  verify that this evidence actually authorizes the observed logical transition.

A full release must account for its FGB, PMTiles, schema, metadata, and manifest
writes. Represent FGB/PMTiles/schema objects as `allocation_artifact`, with their
actual generation, bytes, and hash, plus `allocation_evidence` naming the exact
sidecar `path`, `generation`, and `sha256` for that release. Also supply
`mapping_evidence_sha256`, the digest of a supplied `coverage.evidence` export
that records the build/conversion lineage between those exact bytes and that
sidecar. The reviewer must verify this mapping; the tool does not decode native
geospatial formats. Every artifact referenced by a supplied manifest must have
matching generation/hash evidence, and each manifest's sidecar release/count is
validated. The same mapping requirement applies to partial artifact writes; a
partial with no provable sidecar/reservation mapping remains NOT READY. Do not
exclude it to make the inventory pass. The normalized scope covers all
allocation-bearing writes, not unrelated README/catalog/notification writes;
review that scope against the complete raw exports.

Current object listings or release indexes alone cannot prove that older
allocations were never overwritten or deleted. An explicit `complete` boolean
has no authority. A partial FGB/PMTiles upload without a readable identity sidecar
or an independently verified allocation record remains unresolved; this tool
rejects unsupported reservation evidence rather than guessing IDs. It does not
introduce a competing publication receipt format.

## Offline preparation

Use the existing repo uv environment. Keep evidence and outputs outside the repo:

```bash
uv run python scripts/feature_id_sequence_audit.py audit \
  --inventory "$WORK_DIR/inventory.json" --output "$WORK_DIR/sequence-seed.json"
uv run python scripts/feature_id_sequence_audit.py prepare-manifest \
  --inventory "$WORK_DIR/inventory.json" --seed "$WORK_DIR/sequence-seed.json" \
  --output "$WORK_DIR/manifest-candidate.json"
```

The audit returns `not_ready` (exit 2) for missing or inconsistent evidence,
unexplained ID reuse, unknown reservations, malformed IDs, or mismatched anchors.
Otherwise it returns `prepared_for_review`, the conservatively computed next-ID,
evidence digest, counts, and human review requirements. This status never means
allocation-ready. The seed is deterministic for the same evidence regardless of
inventory order or local file placement.

Preparation reruns the audit and refuses an altered seed or changed input bytes.
The output is a **candidate envelope**, not an upload-ready canonical manifest.
It contains `candidate_manifest`, its exact pretty-printed JSON SHA-256
(`json.dumps(sort_keys=True, indent=2) + "\n"`, UTF-8), and destination
preconditions for both original manifest generations. Candidate identity metadata
records the seed digest and anchor once in `sequence_migration`, adds versioned
before/after state, and preserves IDs, hashes, schemas, and artifact references.
No job accepts the candidate envelope or seed file as a runtime override.

## Review and deployment gates

Before approving any canonical promotion, independently establish the asset's
true first allocation, authoritative export provenance, complete generation and
write/delete coverage through the cutoff, uninterrupted logging/journaling and
retention, all partial reservations, and prior format migrations. Verify every
reviewed reuse explanation. If these facts cannot be established, the migration
is not ready even when all supplied files parse. A namespace/consumer remediation
decision may be necessary; that is not a sequence-only repair.

Include the seed, report, complete evidence references, candidate manifest bytes,
and exact destination generation expectations in the explicit reviewed publish
PR. Use the approved publisher after review. Re-audit if the anchor changes.
Never mutate canonical objects locally. No production migration is performed by
this code change.

Before deployment, publication must atomically claim or otherwise enforce
exclusive ownership of the captured baseline before exposing newly allocated IDs.
A current-generation preflight is not a lock. The generated builders expose one
`GeneratedIdentitySnapshot(path, generation, sha256)` plus the before/after
sequence; publication enforcement is a separate prerequisite. Until both that
requirement and each existing asset's reviewed migration are satisfied, deployment
remains blocked.
