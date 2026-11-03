# Accepted November terrestrial identity decisions

This record covers the 1,323 new cases reported by `wdpa-monthly-d7k4g` for
`wdpa-terrestrial`, release `2026-11-01`. It supplements the earlier accepted
November decisions; it does not change them or approve future cases.

## Maintainer decision

On November 3, 2026, `jonaraphael` explicitly answered the two scoped review
questions in the recovery chat:

- **“Approve all 1,322 recommendations.”** The accepted proposal SHA-256 is
  `e2b0fdfab0c51eb76104e8509635daa21cbc83404ddd357a06d289583ab3779e`.
- **“Preserve ID 244070 as continuity.”** This separate answer applies to
  Naufrage River Riparian Zone, new source key `556329125`, replacing globally
  absent key `555716053_1`. The maintainer chose continuity through the change
  from Provincial Natural Area (PA) to Provincial Conservation Area (OECM).

The agent prepared the comparisons and recommendations. The maintainer made
these semantic decisions. The accepted scope is these exact captured keys,
incoming hashes and complete previous-match sets, under version 1 of
[SkyTruth's identity-resolution process](../standards/feature-identity-resolution.md).
It establishes no standing tolerance for changed designation years, geometry,
names, or PA/OECM transitions.

| Accepted group | Cases | Action |
| --- | ---: | --- |
| Existing keys, unchanged identifying fields | 1,127 | Keep each key's own ID |
| Existing Brazilian keys, changed point locations | 2 | Keep IDs `201751` and `203483` |
| New keys, unchanged identifying fields and absent predecessors | 65 | Reuse each predecessor's ID |
| New keys, spelling-only corrections and absent predecessors | 2 | Reuse each predecessor's ID |
| New Swiss keys, 2023-to-2025 designation-year changes and absent predecessors | 125 | Reuse each predecessor's ID |
| Separate Serpentine recognized-reserve designation | 1 | Allocate a fresh ID |
| Naufrage PA-to-OECM continuity | 1 | Reuse `244070` |
| **Total** | **1,323** | **1,129 keep, 193 reuse, one fresh** |

Fazenda Araucária (`555682490`, ID `201751`) moves approximately 74 metres;
UFMS (`555759246`, ID `203483`) changes approximately one centimetre. Their
source keys and every property remain unchanged. These are explicit decisions
for the two captured geometries, not an automatic geometry-change rule.

The two spelling corrections are German transliteration for `555774762`,
reusing `283942` from `555770678`, and a duplicated-space correction for
`556324778`, reusing `304599` from `555887489`.

Serpentine (Romer-Nitoslawski), new key `556329036`, is a separate recognized
nature-reserve designation from 2025. The older private-stewardship record
`555799173` remains in November and retains ID `303206`. The normal monotonic
allocator chooses the new ID; this approval reserves no number.

## Evidence and limits

The failed worker used executor `711f74af09f1d8d1a34c19edef8c29c2ef6b2aec`
and image digest
`sha256:4c42877704b32f1e6ca90f61d05f1aa19630a84077a0118202cc08bb944a60d4`.
It reached complete terrestrial allocation, then failed because retaining
1,323 cases exceeded its 1,024-case capture cap. Its signed failed outcome is
status `21254231162` on deployment `7514215604`. It published neither asset.
The bounded capture repair is [PR #377](https://github.com/SkyTruth/shared-datasets-1/pull/377).

This review was reconstructed read-only from all 1,323 numbered cases in the
exact worker log window. It is not a worker-retained review bundle. No uploaded
bundle or review URL was invented or backfilled into the original alert.

All complete match sets were checked against the entire 497,914-row October
metadata sidecar, generation `1791037160099462`, SHA-256
`8bb8d0efd6d29b570c6bcf481843b94fd353d48a3b2722bd887817de4acecba6`.
Conditional range reads of all six November DBFs checked ZIP CRCs and 499,017
rows, verifying every incoming property hash and global predecessor-key absence
or presence. The source ZIP was pinned by ETag
`"c6cfb498ddbb8cc8d22c78dbffadb301-894"`, size `4684686242` bytes.

The October terrestrial FGB, generation `1791037142062116`, size `5816513152`
bytes, SHA-256
`f8e4f37133cbb1876117a3489708ee89d9e592474635219adc03eb6d00032ab8`,
was independently verified. All 2,451 needed previous footprints match their
metadata hashes. Incoming polygon footprints are represented by their exact
worker-hash-matching canonical candidates; November polygon bytes were not
downloaded or visually inspected. The two changed incoming MultiPoints were
read from the pinned November SHP and their normalized hashes independently
matched the worker's captured hashes. The maintainer accepted the choices on
this explicitly limited evidence.

The release-scoped [terrestrial resolution file](../../catalog/feature-identity-resolutions/wdpa-terrestrial.json)
records each accepted case, action, prior key ownership, rationale and reviewer.
The exact hashes and match sets must still agree during the next complete build;
changed evidence fails validation. No publication claim, ID counter, captured
intent or receipt is rewritten by these decisions.

## Integration and publication

This chat owns final curation integration and recovery. Its prerequisite is
merged PR #377; no other chat's viewer, receiver or IAM rollout is taken over.
The related activation runbook and final readiness narrative remain separate
pending work until actual publication and schedule activation.

The original review is Slack parent `1793709384.828459`. Only the existing
protected reconciliation path may record these accepted decisions there, after
merge and successful exact-head main validation. Publication and native
Monitoring closure remain separate outcomes. No manual or duplicate message is
sent. Runtime and completion incidents must be reconciled through their own
reviewed recovery paths after actual recovery.

The scheduler stays paused until a new protected canary has a signed verified
terminal outcome and both actual November publications pass the complete
activation checks. This decision record is not publication acceptance.
