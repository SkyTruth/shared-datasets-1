# Publication decision discovery

Every dataset publication flow must inventory its complete scope before
expensive artifact generation, CI or a merge/recovery cycle. This applies to
manual new assets and updates, scheduled ingestion, preview loads and recovery;
vectors, geometry-free tables, rasters and arrays all participate.

Declare every asset and partition. Gather source/license/citation, stewardship,
identity, schema compatibility, formats, access, translations, resource limits,
exception approvals and deployment prerequisites before starting expensive work.
List unresolved choices as `blockers`; do not mark an approval received because
a validator passed. The shared matcher discovers its supported identity-conflict
patterns, not every possible real-world identity change. In particular, unrelated
changes to both geometry and properties can evade partial-match detection.

## Shared command

Normalize vector inputs with the owning producer's actual CRS, precision and
field rules first. Use complete GeoJSONSeq/GeoJSON or CSV, with independently
counted rows. GeoJSON is intended for small sources; large vectors use the
streaming GeoJSONSeq reader. CSV values retain their CSV string representation.
The command never invokes cloud APIs, allocates IDs, builds tiles or changes Git.

```bash
uv run python scripts/publication_readiness.py --plan "$WORK_DIR/discovery-plan.json"
uv run python scripts/publication_runtime_check.py
```

Example plan for a source-field table and a raster:

```json
{
  "release": "2026-11-01",
  "work_dir": "/tmp/shared-datasets-1/_scratch/example-discovery",
  "assets": [
    {
      "asset_slug": "example-table",
      "source": "/tmp/shared-datasets-1/downloads/example-table/source.csv",
      "source_sha256": "REPLACE_WITH_ACTUAL_SHA256",
      "format": "csv",
      "identity_strategy": "source_field",
      "source_fields": ["source_id"],
      "expected_rows": 123,
      "blockers": []
    },
    {
      "asset_slug": "example-raster",
      "source": "/tmp/shared-datasets-1/downloads/example-raster/source.tif",
      "source_sha256": "REPLACE_WITH_ACTUAL_SHA256",
      "identity_strategy": "not_applicable",
      "identity_not_applicable_reason": "Raster cells do not use feature IDs",
      "blockers": ["License decision pending"]
    }
  ]
}
```

Use the standard workspace root override when preparing actual paths. Zarr
directories use `ingestion.common.publication_sources.input_digest`: sorted
relative member paths, NUL, each member's hexadecimal SHA256, and newline are
hashed together. Symlinks are rejected. Raster/array discovery pins source bytes
and contract choices; the native format validator still runs before publication.
Do not use `not_applicable` to bypass vector/table identity decisions.

For generated IDs, use `identity_strategy: generated`, approved `source_fields`
(empty means the stored geometry/properties hash pair), optional
`excluded_properties` and `match_properties`, plus:

```json
{
  "baseline": {
    "records": "/path/under/standard-workspace/baseline.metadata.ndjson.gz",
    "sha256": "EXACT_SIDECAR_SHA256",
    "next_feature_id": 124,
    "release": "2026-10-01",
    "contract_id": "THE_EXISTING_IDENTITY_CONTRACT",
    "snapshot": {
      "path": "gs://BUCKET/EXACT_MANIFEST_PATH",
      "generation": 123456789,
      "sha256": "EXACT_MANIFEST_SHA256"
    }
  },
  "decisions": "/path/under/standard-workspace/reviewed-resolutions.json"
}
```

The baseline must already have been downloaded/validated from its pinned
manifest using the owning publisher's adapter. The generic command validates
the supplied sidecar/hash and ID sequence; it does not independently establish
GCS provenance or human approval. For a new dataset with an approved generated-ID
contract, use `"baseline": {"genesis": true, "contract_id": "APPROVED_CONTRACT"}`.
Genesis cannot also supply previous records or sequence state. This declaration
does not replace the standard identity decision table or curator approval.

The JSON report and `readiness.json` contain every asset's result, retained
analysis location, checksum and complete unresolved case-file path. Exit codes:

| State | Exit | Required action |
| --- | --- | --- |
| READY | 0 | Continue required artifact and publication validation |
| NEEDS_DECISION | 2 | Present one complete batch and record exact reviewed choices |
| BLOCKED | 1 | Resolve listed policy/prerequisite blockers |
| INCOMPLETE | 1 | Repair failed/missing inputs or evidence; completeness is unproved |

One failed asset does not prevent independent assets from being analyzed. The
aggregate cannot be READY while any member is incomplete, blocked or undecided.
Repeated identical failures require repair, not another CI or canary execution.

## Retention and producer integration

`ingestion.common.publication_readiness.Target` and `discover_batch` are shared
producer APIs. A producer supplies its actual normalizer/contract validator and
the complete batch. Generated-ID targets use `DiskIdentityPlan.discover/review`
before `resolve`; the same matcher handles discovery and allocation. Source-field
and non-feature targets validate their own declared contracts without allocating.

Cache identity includes source/selection/baseline pins, producer/native versions
and a digest of shared processing code. Decisions are separate: exact new
curation reuses normalization and matching, then rechecks legality and collisions.
Every retained file is checksummed. Missing, extra, altered or partially prepared
files fail; they never silently fall back to rebuilding and reporting a pass.
Changed inputs select a fresh analysis. Allocation uses a disposable copy.
Analysis is scratch evidence, not a canonical dataset, signature or approval.

The manual concierge requires a `decision-readiness` step after the identity and
translation choices and before `build-artifacts`, for both canonical and preview
routes. Submit `{"readiness_report": "/absolute/path/to/readiness.json"}`.
It revalidates scope, retained checksums, processing version and source/release
binding before artifact evidence and PR readiness. If normalized vector input
differs from the original supplied source, include `original_source_sha256` in
the discovery plan; the concierge compares it with the actual supplied source.
Older unfinished concierge proposals must complete this newly required step.

WDPA's automatic worker performs complete discovery for both realms before its
sample or complete artifact build. Each realm's review bundle is completed and
notified on a decision pause. Normalization is retained for the complete build;
the independent compatibility sample still runs. The direct WDPA publisher also
gates all planned realms before its first publication. MPAtlas and EAMLIS run
their source identity checks through the shared gate; EAMLIS checks before its
durable ID reservation. The common generated-ID writer (including sea ice)
retains matching analysis before writing enriched outputs.

On a WDPA discovery pause, the existing create-only build stager retains analysis
files and an `analysis-reference.json` descriptor, logged as
`publication_analysis_retained`. Objects remain under the originating
`_scratch/wdpa-builds/{execution}/{asset-slug}/analysis-*` paths with exact
generations, sizes and hashes. No canonical objects or ID counters are changed.
Download the descriptor with its logged generation/hash using the GCS protocol.
A local complete replay can restore it with:

```bash
uv run python scripts/local_wdpa_sample.py \
  --source "$SOURCE" --baselines "$FROZEN_BASELINES" \
  --translation-sources "$FROZEN_BASELINES/translation-sources.json" \
  --run-date "$RELEASE_DATE" --workdir "$NEW_REPLAY_DIR" \
  --discover-only --analysis-dir "$RETAINED_ANALYSIS_DIR" \
  --restore-analysis "$PINNED_ANALYSIS_REFERENCE_JSON"
```

Restoration checks managed scratch paths and downloads exact generations with
CRC32C, size and SHA256 verification. Source and baseline matching still run.
This flag authorizes no publication or automatic production replay. Normal
publication claims, reviewed curation and protected deployment rules still apply.
The scheduled builder also accepts an explicit `WDPA_ANALYSIS_REFERENCE` JSON
object containing the logged descriptor URI, generation, size and SHA256. It
restores only pinned scratch objects and rejects a different release, source,
baseline, producer or native toolchain before normalization. This selects cached
analysis only; it cannot select publication bytes or bypass the current
complete build, compatibility or publication gates. Do not persist an old
release's reference in the job template or launch a second production execution;
use only the existing authorized execution/recovery path.
If a bounded review renderer fails, verified normalized analysis is still staged
with `discovery_state: INCOMPLETE`; it can be reused after repairing that boundary
but cannot pass readiness or authorize publication.
Retained normalized geometry consumes disk/storage; existing resource budgets
still fail rather than truncating a batch. Keep useful local evidence and remote
references in the PR; cleanup requires the existing exact-path review process.

## Cheap checks and CI budget

Use complete tiny synthetic batches for publication-code changes without a
pending real release. Do not download the full bucket for a code-only change.
The 30-second shared runtime probe exercises two-asset discovery, persisted JSON,
decision application, immutable reuse and actual allocation. Focused tests cover
source-field tables, raster/Zarr sources, corrupted/stale evidence and failed
members. Native WDPA fixtures exercise normalization and spool reopening.
The probe also checks that tests directly requiring GDAL's Python bindings are
registered as required native executions before the non-native suite may skip
them. A missing registration fails before Docker builds or hosted fanout.

Run these focused checks while editing. Complete the actual affected entrypoint,
serialization and relevant runtime-resource probes before full preflight. Local
preflight and the existing hosted selection job run the shared probe before
Docker builds/fanout; this adds no independent GHA workflow. Freeze the final
commit, run required full preflight once, push once, and use existing verified
PR-to-main reuse where eligible. Any actual code/base/contract change requires
fresh final validation. Preserve required checks and genuine failure reporting.
