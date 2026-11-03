# WDPA identity review evidence

Automatic monthly WDPA ingestion retains review evidence when either its
compatibility sample or complete build stops for an identity decision. The
evidence does not assign IDs, approve a decision, or authorize publication.
Reviewed decisions still belong in
`catalog/feature-identity-resolutions/{asset-slug}.json`.

## Capture and verification

The normalized producer copies escalated incoming records while its geometry
spool and disk allocation plan are open. It records the original source row,
all source properties, stored normalized geometry, the exact ambiguity hashes,
and the previous feature ID already owned by that source key (or explicit
absence). Candidate IDs whose old source keys remain in the incoming input are
also retained, since their IDs cannot be reused for another record.

The parent worker completes that internal capture before disposable inputs are
removed. It reuses the frozen, validated baseline loader and independently
checks that the incoming hashes and complete previous match sets agree. It
loads candidate and key-owner properties from the pinned canonical metadata
sidecar and their footprints from the pinned canonical FGB. Downloads require
the exact generation, size and SHA-256. Extracted FGB identity/hash columns and
geometry hashes must agree with the sidecar. Missing candidates, changed input
bytes, wrong ownership, incomplete match sets and unavailable generations fail
explicitly; they are never replaced with current `latest/` bytes.

The compatibility check now runs the normalized sample before the legacy
sample. Both paths must still agree before a complete build can proceed. An
unexpected legacy-only identity stop is a compatibility failure, rather than
a second source of review evidence. The complete build's conversion and
publication acceptance requirements are unchanged.

## Storage, access and retention

Only a complete, validated `wdpa-identity-review-v1` bundle is uploaded:

```text
gs://skytruth-shared-datasets-1/_scratch/wdpa-builds/{execution}/{asset-slug}/identity-review.json
```

The scheduled `wdpa-monthly-*` execution uses its existing create-only build
stager identity and prefix. Uploads use `if_generation_match=0`, CRC32C
verification, SHA-256 metadata and `Cache-Control: no-store`. An existing object
cannot be replaced. The producer logs one `wdpa_identity_review` event containing
the bundle URI, generation, size and SHA-256, then preserves the typed identity
pause. No publication descriptor is committed for the paused build and the
published dataset remains unchanged. A failure to create complete evidence is
a real execution error, not a successful evidence capture.

These objects are noncanonical, access-controlled operational evidence. This
change adds no public grants or reader permissions. Maintainers need existing
read access to the exact object; catalog-viewer access and alert links belong
to the next PR. Do not cite scratch URIs as dataset contracts.

Retention is **until explicit reviewed cleanup**. There is no automatic expiry
for this prefix in the current Terraform or scratch-cleanup policy. The
pending-publishes abandonment policy does not apply to `wdpa-builds/` evidence.
Keep the exact bundle reference with the decision PR so later retries or a new
baseline cannot replace its review context. Any eventual cleanup must identify
individual objects and generations through the existing reviewed delete path.

Limits are checked before accepting or staging evidence: at most 1,024 escalated
cases, 10,000 previous records, 64 MiB of bundle/capture JSON, 256 MiB of compressed
baseline metadata, and a 16 GiB canonical FGB download. Reads and indexing stream
to disk. Normal releases incur no new canonical FGB download; that download
happens only after an identity pause. Offline HTML is capped at 128 MiB and
exported footprint GeoJSON at 64 MiB. Exceeding a limit requires an explicit
follow-up design; it never produces a sampled or incomplete review bundle.

## Offline review

Copy `review_bundle.uri`, `generation` and `sha256` from the execution's
`wdpa_identity_review` log event. Download that generation using the existing
repo tool, verify its SHA-256 against the event, then render it locally:

```bash
WORK_ROOT="${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}"
REVIEW_DIR="$WORK_ROOT/downloads/wdpa-identity-review/{execution}"
mkdir -p "$REVIEW_DIR"
uv run python scripts/gcs_asset.py download \
  "$REVIEW_URI" "$REVIEW_DIR/identity-review.json" \
  --generation "$REVIEW_GENERATION"
uv run python -m ingestion.wdpa_monthly.identity_review \
  "$REVIEW_DIR/identity-review.json" --out "$REVIEW_DIR/report"
```

The output directory must be new. Open `report/report.html` to review source
attributes, highlighted changes, key ownership, eligible reuse targets and
exact inputs. `report/footprints.geojson` contains the stored geometries for
QGIS or another GeoJSON reader. Separate features sharing a footprint keep
separate IDs; a previous record used by several cases is exported once with
all of its `case_ids`. Incoming features have no assigned `feature_id`.

The report is inert HTML with no JavaScript, external resources or decision
submission controls. Provider strings are escaped. Rendering validates content
hashes and internal consistency; it does not establish a local file's trusted
origin. Retain and verify the original generation/SHA-256 reference. A report
from a compatibility sample is labelled as such and does not enumerate the full
release. Source-language metadata is used throughout.

## Bundle contract

The outer schema is `wdpa-identity-review-v1`. Its `capture` is the internal
`wdpa-identity-review-capture-v1` payload: asset/date, pinned baseline manifest,
case IDs, exact ambiguity fields, incoming records, prior key ownership and
previous keys present in the input. Case IDs reuse the existing decision-event
`case_key`, so the future catalog route can join the same case without guessing.

`baseline` records the previous release, identity contract and generation-pinned
metadata/FGB references. `previous_records` is keyed by feature ID and contains
source identity keys, attributes, hashes and geometry. `source` records the
download URL, size and SHA-256 of the upstream ZIP. `producer` identifies the
Cloud Run execution, executor revision, source-tree digest and installed image.
`sample_fraction`, `sample_seed` and `native_versions` preserve build scope and
resolved native tooling. Readers reject unsupported schema versions.
