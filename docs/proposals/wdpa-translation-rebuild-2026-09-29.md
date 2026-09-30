# WDPA translation rebuild: 2026-09-29

Status: twelve local locale sidecars rebuilt and fully join-validated against
the September 1 metadata snapshot. Requested-row coverage is **100%**, with zero
pending tasks. New machine wording still needs language review; coverage is not
linguistic approval. Nothing has been published, deleted,
merged, or deployed. The implementation and this report are on draft PR #154;
generated data remains outside the repository. These are current-ID rehearsal candidates, not
the new-contract reset release.

The user chose rebuilding rather than retiring the ten old French, Indonesian,
Portuguese, Brazilian Portuguese, and Swahili aliases. Spanish is also rebuilt,
so each WDPA asset has six matching locale sidecars. Historical releases remain
intact. The [feature-ID readiness report](feature-id-readiness-2026-09-29.md)
continues to mark both assets not ready for production cutover.

## Results

| Asset | Current features | Reused translation rows | Supplement rows | Unresolved rows | Requested-row coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| `wdpa-marine` | 17,648 | 1,798,680 | 1,416 | 0 | 100% |
| `wdpa-terrestrial` | 304,817 | 31,084,035 | 7,269 | 0 | 100% |
| Combined | 322,465 | 32,882,715 | 8,685 | 0 | 100% |

The denominator is 32,891,400 nonempty requested feature/field/locale rows.
Five empty/missing canonical field values per terrestrial locale produce no
translation task. Every output sidecar has exactly the canonical feature count.

The historical CSVs contain 32,867,358 accepted translation rows. Every accepted
row's original source hash was checked against its historical canonical metadata;
none of these inputs was stale or rejected. The local index has 1,842,738 distinct
field/locale/source-text keys. Of those, 3,598 have conflicting translated values:
verified same-site translations are retained, but a conflicting dictionary entry
cannot spread to another site. Only one conflicting key affected the initial gaps:
Spanish `NAME_ENG` for `La Campana`, covering three terrestrial records; the
verified supplement supplies these rows.

The initial 8,365 distinct pending field/locale/text tasks collapsed to 1,368
unique source strings. A 96 KB `hash,text` workbook was translated into five
languages (`es`, `fr`, `id`, `pt`, `sw`); the existing explicit `pt_br=pt`
convention is preserved. The new results fill all 8,685 affected feature rows.
This is not a claim of dialect-specific Portuguese editing.

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

All six sidecars were streamed from each current canonical snapshot. Both final
reports have `requested_rows_complete: true`, `unique_pending_tasks: 0`, and
`valid: true`; both pending-task files are empty. Validation
checked every row's identity/provenance, release, property keys, unchanged
non-translatable properties, and row count. Each candidate report records input
and output SHA-256 hashes separately from translation completeness. This checks
file consistency, not linguistic quality or native FGB/PMTiles correctness.

Final report SHA-256 values bind the input and output digests recorded locally:

| Asset | `completed/{asset}/reuse-report.json` SHA-256 |
| --- | --- |
| marine | `eb8b0219086ca21968a12dea516829c09c3fa01c3409da93dd4d11e3c56b4ea5` |
| terrestrial | `6ede2747745c51e6df7248bd3b639722db7e80a2d3b84b74c01848bf8ab8eeae` |

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
until a reviewed translation update is incorporated. The required
`translation_supplement` in each WDPA reset inventory binds the staged object's
exact path, generation, and SHA-256. Both assets must approve the same supplement.
The pending-reset build verifies and consumes those bytes; incomplete translations
block the first publication before any ID reservation. The
[reset installer](../feature-id-reset-installation.md) only installs allocation
state and does not promote a translation supplement. Later runs reuse the full
CSV from their committed publication and do not reload the reset input. Missing
or conflicting established translations may be filled; existing successful
translations retain precedence.

The integration still needs a native image run and peak-memory verification with
the full geospatial build. The configured Cloud Run memory is 32 GiB; adding the
approximately 5.6 GB terrestrial CSV and local index cannot be certified from the
metadata-only rehearsal.

## Document import and retained evidence

The user approved the workbook transfer to Google Translate Documents and use
of Chrome. Five translated workbooks were retrieved and imported. Earlier
failed downloads and unchanged-output retries were rejected; only the verified
results below were used. Reloading the page before each language avoided the
stale-page downloads that returned the original workbook bytes. The URL-wrapped
identifier experiment was abandoned without importing its output.

Each accepted workbook contains all 1,368 original source hashes exactly once.
Google translated some headers and hash fragments or changed repeated hex
characters. Header/whitespace/case repairs and 56 explicitly inspected key
restorations were checked against the retained original workbook; each damaged
key uniquely retained at least 60 of its original 64 hex digits. No row-position
matching was used. Raw downloads, rejected attempts, explicit restoration maps,
and normalized workbooks remain available for review.

| Locale | Raw downloaded workbook SHA-256 | Hash-restored workbook SHA-256 |
| --- | --- | --- |
| `es` | `1619d02f78f45ec67b3cf5d5df619fbcdfb8c4ebaccb4ef22f0c85731ccd3fdc` | `81c3f42b5950825cad038e2fffd88e3d16712e9903c97ced73080c7c37043275` |
| `fr` | `a5c71ad2dcf0a182d852645f4590bfbd20420c8f2fcfd96ad92fbc491d1a9f77` | `580717d3bd7081a284d0a2160b5964277838cd3b31a9b447f629628156c5c0c0` |
| `id` | `629a3ef40f84abb32a0987543b48d0c6335a3ec6de5c7b7c01a5d6aaa4ff7316` | `07fc5f30d5c58b8599d3d2519af45dc160304b249f740c1e979e855571de371e` |
| `pt` | `1bb4b78d26aa6c2a776397bba4b1edab2c7dca3e5067c65c350f65a87e1fb092` | `4e59b6058fa081679622e1f59447c9c56e0860e70ec71281caf838e50865444c` |
| `sw` | `46713bfbdd522ef08de462cce1e48866cf382115899053a91f14494abe2d8086` | `89e020b6f59c7a80466bc7f1b184ecbb3298994ea23a5240801eddd69a50a13b` |

The original workbook SHA-256 is
`16e26630ec2469ffedf508ba8f57547e89311d51c61f3269a24f18b395d8977f`.
Portuguese used Google's Portuguese (Brazil) target; `pt_br=pt` explicitly reuses
that output. There was no independent Portuguese dialect review.

Strict document import verified the v2 manifest, unchanged source/projection
snapshots, hashes, task identities, and coverage. It produced 8,365 supplement
tasks. A separately recorded normalization trimmed provider-added outer
whitespace on 6,450 task values and preserved exact source URLs on 690 tasks
across `MANG_PLAN` and `SUPP_INFO`. This prevents invented translated URL paths
or hostnames; it does not repair pre-existing malformed source links. These URL
rows are `source_provided`; other new rows remain `document_translated`, with
machine provenance and no claim of human language review.

The final `gap-supplement.ndjson` SHA-256 is
`33c5aa2766179b1e75dce1f20b346c4d0d449ccfec0086a639a6baf4f6638acd`.
The raw document-import supplement SHA-256 is
`cc5adb3696c6d0b25789c76ae5cdcfaf26d7c91cd153732ad8123e2b68c98a0c`;
the normalization report records every changed value and both digests.

Coverage and source matching do not certify language quality. Spot checks found
machine wording that needs review, including Portuguese rendering of
`Área de Gestión de Hábitat de Especies` as housing for people with special
needs. Preserve this distinction when reviewing the supplement: the files are
complete machine-translation candidates, not approved human translations.

The local task directory is
`${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}/vector-assets/wdpa-translation-rebuild/`.
It retains approximately 14 GB of task data:

- `source/`, `source-inventory.json`, and `download-manifest.json`: pinned inputs.
- `reuse-config.json`, `reuse.sqlite`, and `index-report.json`: reusable source index.
- `candidates/{asset}/` and `candidate-join-validation.json`: earlier partial
  rehearsal results, preserved separately from the completed candidates.
- `gap-documents/`: source workbook, original pending-file bindings, request
  projection, v2 manifest, five normalized workbooks, raw downloads, identifier
  repair evidence, `import-report.json`, and `value-normalization-report.json`.
  Projection IDs identify local requests, never WDPA features.
- `gap-supplement.document-output.ndjson` and `gap-supplement.ndjson`: raw
  imported machine output and the source-URL/whitespace-normalized build input.
- `completed/{asset}/`: full translation CSV, six locale sidecars, pending-task
  list, and `reuse-report.json` with exact input/output digests and coverage.

The rebuild command is, with `TASK` set to that retained directory:

```bash
UV_CACHE_DIR=.uv-cache uv run --no-sync python scripts/feature_metadata_translation_reuse.py rebuild \
  --database "$TASK/reuse.sqlite" --supplement "$TASK/gap-supplement.ndjson" \
  --canonical-sidecar "$TASK/source/wdpa-marine/current/wdpa-marine.metadata.ndjson.gz" \
  --schema "$TASK/source/wdpa-marine/current/wdpa-marine.schema.json" \
  --asset-slug wdpa-marine --release 2026-09-01 \
  --output-dir "$TASK/completed/wdpa-marine"
```

The terrestrial command changes the asset slug and corresponding paths. Output
directories must be new. Existing successful translations take precedence over
the supplement. Both rebuilds stream and validate the entire canonical/CSV/locale
join, including identity, original source hash, review state, and translated value.

For the actual cutover, review the language output, stage the accepted supplement,
and pin its exact generation/hash in both WDPA reset inventories. Rebuild again
against the approved new-contract canonical metadata and release date, validate
the complete native release and serving context, then use the protected reviewed
publisher. These local commands do not authorize GCS publication or resolve the
remaining writer/reset safeguards.

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
Full-suite results after binding the approved reset supplement: **1,084 passed,
4 opt-in native skips, 1,248 subtests passed**. A separate opt-in native run passed
all **90 geospatial tests** after isolating a local PROJ database conflict; see
the [readiness report](feature-id-readiness-2026-09-29.md) for tool versions and
limits. The full production reset bundle still needs native verification.

Python tooling used the existing `uv` environment. A temporary `uv --with
deep-translator` dependency cache was created for the provider check; project
dependency files and the lockfile were unchanged. No Slack notification was sent:
there was no dataset publication.
