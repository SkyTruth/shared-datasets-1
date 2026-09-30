# WDPA translation reset evidence

The September 2026 rehearsal rebuilt all six locales (`es`, `fr`, `id`, `pt`,
`pt_br`, `sw`) for both WDPA assets. This replaces the ten stale non-Spanish
aliases as part of the first new-contract publication. Historical releases remain
intact. Follow the [reset runbook](feature-id-reset-installation.md) for production
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
to a canonical dataset path. The first WDPA build verifies and consumes it,
then requires complete translations before reserving IDs. Later builds reuse the
full CSV in their committed receipt and do not reload this reset input.

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
