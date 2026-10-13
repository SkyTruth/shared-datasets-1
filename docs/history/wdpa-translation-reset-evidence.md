Historical record, completed 2026-10-02. Not a runbook; see docs/wdpa-monthly-runbook.md.

# WDPA translation reset evidence

The September 2026 rehearsal rebuilt all six locales (`es`, `fr`, `id`, `pt`,
`pt_br`, `sw`) for both WDPA assets. This replaces the ten stale non-Spanish
aliases as part of the first new-contract publication. Historical releases remain
intact. Follow the [reset runbook](../feature-id-reset-installation.md) for production
installation and validation; these rehearsal outputs were not published.

## Reviewed result

| Asset | Canonical features | Reused rows | Supplement rows | Missing rows |
| --- | ---: | ---: | ---: | ---: |
| `wdpa-marine` | 17,648 | 1,798,680 | 1,416 | 0 |
| `wdpa-terrestrial` | 304,817 | 31,084,035 | 7,269 | 0 |

All **32,891,400 nonempty requested feature/field/locale rows** passed the full
canonical/CSV/locale join. Each of the twelve locale files has the exact canonical
feature count; both pending-task files are empty. Five empty canonical field
values per terrestrial locale produce no translation task.

Translation matching uses verified `SITE_PID` and the original source-value hash.
Old numeric feature IDs only identify rows inside their original source bundle.
Established successful translations retain precedence. Cross-site dictionary
reuse requires an unambiguous field/locale/source-text match.

The owner approved preserving official source names and translating descriptions:

- 7,273 new name tasks preserve 1,213 official names exactly.
- 690 URL tasks preserve source bytes; translated URLs are not accepted.
- All 67 distinct new descriptive strings (402 locale tasks) were reviewed and
  corrected by the agent. They carry machine provenance and are not labeled
  human-reviewed.
- `pt_br=pt` is explicit reuse of Google's Portuguese (Brazil) output, not an
  independent dialect review. Existing historical text was not linguistically
  re-reviewed.

Five Google Translate Documents workbooks supplied the gap translation input with
owner-approved transfer. Import verified all 1,368 source hashes exactly once;
56 damaged identifiers were explicitly restored against the original workbook,
without row-position matching. Raw workbooks, restoration maps, glossary, and
before/after review records remain in the local evidence directory below.
Mechanical completeness does not certify native-speaker quality.

## Exact reset input

Both WDPA inventories must bind this same supplement generation and SHA-256.
The inputs were uploaded create-only and downloaded at their exact generations
to verify bytes, size, and hashes. Common noncanonical staging prefix:

```text
gs://skytruth-shared-datasets-1/_scratch/pending-publishes/wdpa-feature-id-reset/pr-154-20260930/
```

| Object | Generation | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| `gap-supplement.ndjson` | `1790741746636085` | 3,180,459 | `83d0b86e761a2b2963f0e7b54d406242602e737835b8edeb279112067a5902b9` |
| `glossary.json` | `1790741771511871` | 16,015 | `7c6a0d6da264425ee295dddf1b3b160ee66d3cf956a82df10cfaf2bb7056f964` |
| `review-report.json` | `1790741773367347` | 2,752,288 | `17f2afeb840aa6f17cd068590777fe2fe2321ada28660ea51c5969d4dd7f779d` |

The reset installer verifies this reference but does not promote the supplement
to a canonical dataset path. The first WDPA build verifies and consumes it.
Translation gaps are reported and retain canonical text without blocking identity
reservation or publication; all identity and input-integrity checks still apply.
Later builds reuse the full CSV in their committed receipt and do not reload
this reset input.

## Source and validation evidence

The first reset build reuses the following historical source objects under
`gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/`.
These pins also appear in `ingestion/wdpa_monthly/translations.py`.

| Asset-relative object | Generation | SHA-256 |
| --- | --- | --- |
| `wdpa-marine/releases/2026-06-09/wdpa-marine.metadata.ndjson.gz` | `1781025267875035` | `7bae601c9d5643fcbcbc456e453c805c92b123e0d8dad51bf4a8bba8f0d5bbb9` |
| `wdpa-marine/releases/2026-06-09/wdpa-marine.metadata-translations.csv` | `1781542880691080` | `fb35204e7b28a9f441aa086aac180fccbe23d926a5fb9ec7c4ee8c4569f2d7f9` |
| `wdpa-terrestrial/releases/2026-06-09/wdpa-terrestrial.metadata.ndjson.gz` | `1781059584800280` | `2d5a83c6a671e7ebb719ffbfe195b6ffc61698880629b796c91f59d58cd8ebac` |
| `wdpa-terrestrial/releases/2026-06-09/wdpa-terrestrial.metadata-translations.csv` | `1781110590312857` | `0c71507f9e9244d2fcdb3be633fc2e78b391195b9cf37a26bb98bff5128bab86` |

The rehearsal canonical metadata generations were `1788256149578158` (marine)
and `1788264542038587` (terrestrial), for release `2026-09-01`. Final
`reuse-report.json` hashes bind the complete input/output digest lists:

| Asset | Report SHA-256 |
| --- | --- |
| marine | `5c6b1240980a60fd344c7877bad111415d935d7f8b7f6c03a4987525edc12145` |
| terrestrial | `e8437157e7e6f4427684c6d79ad92900217284af3db56f5b1839eacdb128f404` |

Native integration tests exercise the conversion path with fixtures. The full
production build still needs peak-memory and native artifact validation; the
metadata-only rehearsal does not establish that the full terrestrial build fits
Cloud Run's 32 GiB memory limit.

## Retained files and implementation

Local evidence is under the standard work root at
`vector-assets/wdpa-translation-rebuild/` (approximately 19 GB).
`source/` and the inventories retain pinned input objects; `gap-documents/`
retains workbook/import evidence. `review-20260930/` contains the corrected
supplement, glossary, before/after review, staged pins, and final
`completed/{asset}/` CSVs, six locale files, empty pending tasks, and digest reports.

`feature_metadata_translation_reuse.py` builds a disposable SQLite lookup and
streams both asset joins. `feature_metadata_translation_gap_documents.py`
exports/imports the compact gap workbook. Use their CLI help for local rebuilding.
The scheduled integration is `ingestion/wdpa_monthly/translations.py`; it never
calls Google at runtime. New text in later releases remains an explicit gap until
a reviewed translation update supplies it. Canonical formats remain CSV and gzip
NDJSON; the SQLite index and workbooks are local processing artifacts.

## Archived ingestion README context

The following sections were retained from `ingestion/wdpa_monthly/README.md`
at revision `aa77eb89a93c54029c4ef003fe4096a196bda63b` when operations and rollout history were separated.
They record the prior guidance; use the monthly runbook for current operations.

### Translation reuse

The job rebuilds `es`, `fr`, `id`, `pt`, `pt_br`, and `sw` metadata sidecars
together with each release. The two asset documents declare the same
`translation_locales` and 17 `translation_fields`; the job reads these through
the generated catalog. The translation-source CSV and all six sidecars are
part of the same owned publication as the geometry and canonical metadata,
and the final manifest records their exact generations and translation coverage.

Translations are matched first by the exact `SITE_PID` and original source-value
hash. Numeric `feature_id` values never join translations across releases or
identity contracts. An unambiguous existing translation of the same source text,
field, and locale can fill another record. Conflicting text translations remain
site-specific. Both direct matches and phrase reuse retain the original review
states and notes. Usable machine translations and `needs_review` rows are current;
editorial review is reported separately from availability.

The job downloads the exact canonical metadata and translation CSV generations
recorded in its committed release manifest, including reviewed translation edits.
Persisted v1 receipts remain the source only for extras omitted by their old manifests. While either asset is in the
explicit first-reset state, both assets use the verified June 9 legacy source
bundles pinned in `translations.py`. Keeping that pair fixed across retries
preserves translations for protected areas that move between marine and terrestrial.
That state also binds a reviewed gap supplement by staged object path, generation,
and SHA-256. Both assets must approve the same supplement; the first build verifies
and consumes it without replacing established translations. Translation gaps are
nonblocking for the first release and later releases; identity reservation,
generation checks, and bundle validation still apply.
Once both first publications finish, later builds use their committed CSV and
do not reload the reset supplement.
Missing state or an incomplete committed bundle fails; it does not select arbitrary
`latest/` files. One disposable SQLite index serves both WDPA assets. Large CSVs
are streamed and the downloaded copies are locally compressed.

New or changed text without a usable current translation retains the canonical
value. A successful older row keeps its old source hash, so the next build still
recognizes stale text. With no successful history, its CSV row is empty with
`review_state=translation_failed`. A failed current row counts as missing even
when older successful rows exist. The report and final manifest partition each
locale's nonblank approved string values into `current`, `stale`, and `missing`.
Local `*.translation-debt.{locale}.csv` files contain only unresolved current
feature/field keys and source text. They are build outputs, not canonical artifacts.
The job does not contact a translation provider or claim
that fallback text is translated. Follow the [translation evidence and tooling](wdpa-translation-reset-evidence.md)
to fill these tasks without retranslating the full dataset. New machine results
remain labeled as machine output rather than human review.

### Generated-ID publication

Both assets use `OwnedGeneratedPublisher` under `generated-2026-v1`. The
[reset runbook](../feature-id-reset-installation.md) defines the one-time
transition and first-publication checks. Historical releases remain readable;
their IDs do not seed the new contract. Both assets require installed reset state
before this shared job deploys. Missing state is an error.

The publisher reserves IDs and claims the asset before exposing artifacts.
Later builds read the exact committed manifest/metadata generations and retain
the counter when features disappear. Runtime identity binds `CLOUD_RUN_EXECUTION`
and the image's embedded `SHARED_DATASETS_EXECUTOR_SHA`. Retries resume the
captured intent; an interrupted checkpoint holds its claim and reservation.
Starting another execution does not abandon them.

The first new release replaces every captured latest object, including all six
locale sidecars. It consumes the [verified translation supplement](wdpa-translation-reset-evidence.md)
and requires complete joins before reserving IDs. Resume scheduling only after
both asset publications finish and their native artifacts and joins validate.
