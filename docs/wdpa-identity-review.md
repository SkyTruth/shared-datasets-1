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

These objects are noncanonical, access-controlled operational evidence. The
production catalog viewer has a narrow read grant for identity-review JSON
under the monthly WDPA staging prefix, in addition to its existing catalog and
canonical dataset access. No public grants or write permissions are added;
other build bytes remain outside that grant. The API further restricts the
execution, asset, filename and exact generation. Preview readers retain their
existing permissions. Do not cite scratch URIs as dataset contracts.

Retention is **until explicit reviewed cleanup**. There is no automatic expiry
for this prefix in the current Terraform or scratch-cleanup policy. The
pending-publishes abandonment policy does not apply to `wdpa-builds/` evidence.
Keep the exact bundle reference with the decision PR so later retries or a new
baseline cannot replace its review context. Any eventual cleanup must identify
individual objects and generations through the existing reviewed delete path.

Limits are checked before accepting or staging evidence: at most 2,048 escalated
cases, 10,000 previous records, 64 MiB of bundle/capture JSON, 256 MiB of compressed
baseline metadata, and a 16 GiB canonical FGB download. Reads and indexing stream
to disk. Normal releases incur no new canonical FGB download; that download
happens only after an identity pause. Offline HTML is capped at 128 MiB and
exported footprint GeoJSON at 64 MiB. Exceeding a limit requires an explicit
follow-up design; it never produces a sampled or incomplete review bundle.

The case capacity covers the 1,323-case full-terrestrial November review that
exceeded the former 1,024-case limit. Its read-only reconstruction, including
2,451 verified previous footprints and complete field comparisons, occupies
about 18.5 MB. The larger count remains subordinate to every byte and
previous-record bound above; a large geometry or excessive match set still
fails rather than truncating evidence. Capture, persisted bundle validation,
create-only staging and offline rendering use the same case bound. This change
does not approve any case or alter identity matching, allocation or publication.
Existing catalog-reader byte limits remain unchanged; larger valid bundles use
the offline report. A reader running the older validator rejects a bundle above
its previous case cap until its normal reviewed image rollout.

## Review from the alert

Once the verified bundle is staged, the worker queues a
`feature-identity-review-ready-v1` event on the existing private decision topic.
It contains the same captured cases and context as the original review stop,
plus the exact bundle reference. The receiver adds **Compare this case** links
to the existing bot-owned Slack parent; it does not post another parent.
An older event arriving later cannot remove the evidence reference, and a
different bundle or case set for the same execution is rejected. The receipt
retains the viewer origin and reference for later retries and reconciliation.

Open a link in the authenticated production catalog. The read-only dialog
shows the incoming source key, its previously owned ID or verified absence,
all matching previous records, all source fields with changes highlighted,
and exact stored footprints. Select a previous candidate to inspect it; orange
and blue map controls let you distinguish coincident footprints. Published
candidate links use the baseline date and canonical metadata generation from
the evidence. A missing published generation never switches to latest.

**Choices to review** explains available actions and their effects. It does
not recommend or submit a decision. Sample evidence is labelled; a sample
cannot prove global source-key absence, so it does not offer reuse targets.
Complete asset input still requires the source/continuity checks in
[SkyTruth's identity process](standards/feature-identity-resolution.md).
Accepted decisions still go through the resolution-file PR workflow.

`GET /api/identity-review` always requires the viewer's SkyTruth IAP identity,
even for a public asset or a viewer with public feature lookup enabled. Its
strict query parameters are `review_case`, `review_asset`, `review_release`,
`review_execution`, `review_generation`, `review_size` and `review_sha256`.
It derives the managed object path, reads the pinned generation with a bounded
range, checks exact length/checksum and bundle validity, then returns only the
selected case, its previous records and a case-selector summary. It never
accepts a caller-supplied URI. All responses use `Cache-Control: no-store`.
The viewer reads bundles up to 16 MiB and limits serialized case responses to
16 MiB, charging each candidate against the budget before expanding the next.
Each instance processes one evidence read at a time and returns 429 while busy.
Larger bundles remain valid for the offline report within the 64 MiB producer
limit. Missing, inaccessible, changed and invalid evidence fail
explicitly. Close the dialog to continue ordinary published browsing.

The UI module loads only when a review link is opened. Ordinary startup remains
compatible with the previous viewer's static module allowlist. During rollout,
an unavailable module or reader gives an explicit review error. Older executions
without a retained bundle do not acquire invented links or reconstructed data.

Activation uses reviewed main CI and the existing protected paths:
`catalog-viewer-deploy.yml` installs the reader image and its existing IAM
binding with the narrowed evidence condition; `cron-alert-policy-sync.yml`
updates the receiver and `CATALOG_VIEWER_URL` from the existing viewer URI;
`wdpa-monthly-deploy.yml` installs the ready-event producer. The private Pub/Sub
queue retains delivery failures during rollout. No local infrastructure apply,
public scratch route or new notification channel is needed.

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
