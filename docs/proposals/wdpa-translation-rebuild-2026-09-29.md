# WDPA translation rebuild: 2026-09-29

Status: twelve local locale sidecars rebuilt and structurally validated against
the September 1 metadata snapshot. Translation content is **99.9736% complete
by requested row**; new-text gaps remain. Nothing has been published, deleted,
merged, committed, or deployed. These are current-ID rehearsal candidates, not
the new-contract reset release.

The user chose rebuilding rather than retiring the ten old French, Indonesian,
Portuguese, Brazilian Portuguese, and Swahili aliases. Spanish is also rebuilt,
so each WDPA asset has six matching locale sidecars. Historical releases remain
intact. The [feature-ID readiness report](feature-id-readiness-2026-09-29.md)
continues to mark both assets not ready for production cutover.

## Results

| Asset | Current features | Reused translation rows | Unresolved rows | Distinct pending field/locale/text tasks | Reused |
| --- | ---: | ---: | ---: | ---: | ---: |
| `wdpa-marine` | 17,648 | 1,798,680 | 1,416 | 1,410 | 99.9213% |
| `wdpa-terrestrial` | 304,817 | 31,084,035 | 7,269 | 6,961 | 99.9766% |
| Combined | 322,465 | 32,882,715 | 8,685 | 8,365 after cross-asset deduplication | 99.9736% |

The denominator is 32,891,400 nonempty requested feature/field/locale rows.
Five empty/missing canonical field values per terrestrial locale produce no
translation task. Every output sidecar has exactly the canonical feature count.

The historical CSVs contain 32,867,358 accepted translation rows. Every accepted
row's original source hash was checked against its historical canonical metadata;
none of these inputs was stale or rejected. The local index has 1,842,738 distinct
field/locale/source-text keys. Of those, 3,598 have conflicting translated values:
verified same-site translations are retained, but a conflicting dictionary entry
cannot spread to another site. Only one conflicting key affects the current gaps:
Spanish `NAME_ENG` for `La Campana`, covering three terrestrial records.

The pending work collapses to 1,368 unique source strings. A 96 KB `hash,text`
workbook was exported with the existing document translation helper. It needs
five translated results (`es`, `fr`, `id`, `pt`, `sw`); the existing explicit
`pt_br=pt` convention is preserved. This is not a claim of dialect-specific
Brazilian Portuguese editing.

## Evidence and matching

All source objects are under
`gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/{asset}/`.
Downloads used exact generations and recorded SHA-256 hashes and sizes.

| Asset | Object relative to asset root | Generation | Raw object SHA-256 |
| --- | --- | --- | --- |
| marine | `releases/2026-06-09/wdpa-marine.metadata.ndjson.gz` | `1781025267875035` | `7bae601c9d5643fcbcbc456e453c805c92b123e0d8dad51bf4a8bba8f0d5bbb9` |
| marine | `releases/2026-06-09/wdpa-marine.metadata-translations.csv` | `1781542880691080` | `fb35204e7b28a9f441aa086aac180fccbe23d926a5fb9ec7c4ee8c4569f2d7f9` |
| terrestrial | `releases/2026-06-09/wdpa-terrestrial.metadata.ndjson.gz` | `1781059584800280` | `2d5a83c6a671e7ebb719ffbfe195b6ffc61698880629b796c91f59d58cd8ebac` |
| terrestrial | `releases/2026-06-09/wdpa-terrestrial.metadata-translations.csv` | `1781110590312857` | `0c71507f9e9244d2fcdb3be633fc2e78b391195b9cf37a26bb98bff5128bab86` |
| marine | `latest/wdpa-marine.metadata.ndjson.gz` | `1788256149578158` | `7ee122e1527db8735504aef4502858278e76a175dbdc3e4153df3222a3ce8711` |
| terrestrial | `latest/wdpa-terrestrial.metadata.ndjson.gz` | `1788264542038587` | `ceaf7f402ebc4fcedf9aa93fb9e3468b21664f9632be3bf3c5fc9e14adbc2c2f` |

The marine CSV was updated June 15 but remains under the June 9 release path.
Current schema generations are `1788256150861949` and `1788264544473715`.
Local CSV download copies are gzip-compressed for space; snapshot files retain
the original uncompressed object's hash and generation.

The rebuild uses the exact nonempty, unique `SITE_PID`, then the original source
value hash. Old numeric feature IDs are only keys within their original source
bundle. They never establish cross-release identity. Exact matches preserve
review state and notes. Unambiguous same-field/locale/text reuse is marked
`reused_translation`. Missing/conflicting results are empty `translation_failed`
CSV rows; localized sidecars retain the canonical value for those fields.

All six sidecars were streamed from each current canonical snapshot. Validation
checked every row's identity/provenance, release, property keys, unchanged
non-translatable properties, and row count. Each candidate report records input
and output SHA-256 hashes separately from translation completeness. This checks
file consistency, not linguistic quality or native FGB/PMTiles correctness.

## Implemented monthly behavior

`scripts/feature_metadata_translation_reuse.py` builds one disposable SQLite
index from streamed sources and reuses it for both assets. SQLite is local scratch
state; canonical artifacts remain CSV and gzip NDJSON. It can accept a verified
compact supplement to fill gaps without replacing established translations.

`ingestion/wdpa_monthly/translations.py` loads exact metadata/CSV versions from
the publisher's committed receipt. The explicit pending-reset state alone uses
the verified legacy pins above. Both first and subsequent builds emit all six
locale sidecars and the full translation-source CSV. The owned publisher refuses
a missing locale and includes all twelve release/latest artifacts in its durable
publication. The old Spanish-only placeholder writer has been removed.

The scheduled job reuses existing translations and records gaps; it does not call
Google at runtime. Future new text therefore remains visible as unresolved work
until a reviewed translation update is incorporated. Local completed supplements
must be bound into the reviewed first-release build inputs; the unfinished reset
installer does not yet provide that promotion path. Later runs reuse the full CSV
from their committed publication.

The integration still needs a native image run and peak-memory verification with
the full geospatial build. The configured Cloud Run memory is 32 GiB; adding the
approximately 5.6 GB terrestrial CSV and local index cannot be certified from the
metadata-only rehearsal.

## Finishing the remaining gaps

The direct Google helper's first request failed with `TooManyRequests`; no new
translation from that attempt was accepted. Google Translate's document UI
reached `Download translation` for Spanish, but supported browser download
attempts returned no file. The translated bytes have **not** been retrieved or
validated. The other four target results have not been requested. No new gap
result has been imported or misclassified as completed.

On continuation, a single direct-helper probe still returned `TooManyRequests`,
and a fresh visible download attempt still returned no file. The completed
Spanish translation page is retained. Switching the document workflow to Chrome
is awaiting the user's browser choice; no new translation-provider result has
been accepted.

The local task directory is
`${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}/vector-assets/wdpa-translation-rebuild/`.
It retains approximately 8.30 GB of sources, the index, candidates, and evidence:

- `source/`, `source-inventory.json`, and `download-manifest.json`: pinned inputs.
- `reuse-config.json`, `reuse.sqlite`, and `index-report.json`: reusable source index.
- `candidates/{asset}/`: translation CSV, six locale sidecars, pending task list,
  and `reuse-report.json`. Candidates are valid partial results.
- `candidate-join-validation.json`: both existing candidate bundles passed the
  complete CSV/sidecar join audit, including every translated/fallback value;
  input and output files retained their recorded hashes throughout validation.
- `gap-documents/`: compact workbook, original pending-file bindings, request
  projection, and v2 document manifest. Request projection IDs are scratch task
  IDs, never WDPA feature IDs or publishable dataset records.

Resume by translating `gap-documents/translation-gaps.for-translate.xlsx` with
Google Translate Documents for the five target languages. Preserve every hash
and the exact `hash,text` header. Then run, with `TASK` set to the retained task
directory and each translated workbook saved under `gap-documents/`:

```bash
UV_CACHE_DIR=.uv-cache uv run --no-sync python scripts/feature_metadata_translation_gap_documents.py import \
  --manifest "$TASK/gap-documents/gap-manifest.json" \
  --translated-file "es=$TASK/gap-documents/es.xlsx" \
  --translated-file "fr=$TASK/gap-documents/fr.xlsx" \
  --translated-file "id=$TASK/gap-documents/id.xlsx" \
  --translated-file "pt=$TASK/gap-documents/pt.xlsx" \
  --translated-file "sw=$TASK/gap-documents/sw.xlsx" \
  --reuse-locale pt_br=pt \
  --output "$TASK/gap-supplement.ndjson"

UV_CACHE_DIR=.uv-cache uv run --no-sync python scripts/feature_metadata_translation_reuse.py rebuild \
  --database "$TASK/reuse.sqlite" --supplement "$TASK/gap-supplement.ndjson" \
  --canonical-sidecar "$TASK/source/wdpa-marine/current/wdpa-marine.metadata.ndjson.gz" \
  --schema "$TASK/source/wdpa-marine/current/wdpa-marine.schema.json" \
  --asset-slug wdpa-marine --release 2026-09-01 \
  --output-dir "$TASK/completed/wdpa-marine"
```

Repeat rebuild for terrestrial. Import verifies source snapshots, document
manifest, task identities, intact hashes, and coverage. Reordered rows are allowed;
missing, duplicated, damaged, or extra hashes fail. New results are marked
`document_translated` with machine provenance. Do not mark them human-reviewed.
Existing successful or human translations take precedence over supplements.

For the actual cutover, rebuild again against the approved new-contract canonical
metadata and release date, inspect the complete new release and its serving
context, then use the protected reviewed publisher. These local commands do not
authorize GCS publication or resolve the remaining writer/reset safeguards.

## Verification

Regression coverage exercises renumbered IDs, exact source matching, changed
text, conflicting phrases, stale/failed source rows, duplicate identities,
cross-asset reuse, supplement precedence, document reordering/corruption,
generation-pinned inputs, both monthly asset builds, and six-locale publication
and cleanup. The rebuild validator now streams every CSV task in the declared
feature/field/locale order, checks its original source hash and review state,
and compares every localized record with the full canonical/CSV join. This also
detects incorrect text in otherwise structurally valid locale files. Corrupted
IDs, hashes, extra rows, translated values, and failed-row values are covered.
Full-suite results after integrating the deployment hold: **1,061 passed,
4 opt-in native skips, 1,208 subtests passed**. A separate opt-in native run passed
all **90 geospatial tests** after isolating a local PROJ database conflict; see
the [readiness report](feature-id-readiness-2026-09-29.md) for tool versions and
limits. The full production reset bundle still needs native verification.

Python tooling used the existing `uv` environment. A temporary `uv --with
deep-translator` dependency cache was created for the provider check; project
dependency files and the lockfile were unchanged. No Slack notification was sent:
there was no dataset publication.
