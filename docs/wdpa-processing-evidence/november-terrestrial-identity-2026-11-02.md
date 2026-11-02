# November terrestrial identity decisions

The maintainer `jonaraphael` explicitly approved both identity choices captured
by `wdpa-monthly-w75n5` for `wdpa-terrestrial`, release `2026-11-01`:

| Source key | Decision | Reason |
| --- | --- | --- |
| `555732174` | Keep its existing feature ID `255953`. | Tjärnberget's new geometry matches separate Habitats Directive designation `132873` / source `555533827`. Both records remain in November. Keeping the existing mapping preserves the distinct designations. |
| `555779501` | Preserve feature ID `282786` from previous source `555769403`. | Weid's old source key is absent from all November DBFs; the new key is absent from the complete October baseline. Name, Forest Reserves designation and stored geometry agree. The designation year changes from 2023 to 2025; the maintainer approved continuity as a source-key replacement. |

Tjärnberget's exact response was **Keep 255953 (Recommended)**. The subsequent
instruction was **Preserve 282786 for Weid. Merge the decisions and unpause WDPA
once publication verification passes.** These choices do not establish dataset
publication or authorize bypassing the protected schedule readiness checks.

## Pinned comparison

The October terrestrial metadata has 497,914 rows, generation
`1791037160099462`, SHA-256
`8bb8d0efd6d29b570c6bcf481843b94fd353d48a3b2722bd887817de4acecba6`.
The November source ETag is `"c6cfb498ddbb8cc8d22c78dbffadb301-894"`, last
modified `2026-11-02T13:27:07Z`. Conditional range reads scanned all six DBFs
and 499,017 rows with verified ZIP CRCs. Both selected source-properties hashes
exactly match the captured execution evidence.

| Source key | New geometry hash | New properties hash | Matching previous feature |
| --- | --- | --- | --- |
| `555732174` | `sha256:c2e2a8acf89e34d3d479fea756745968877938e83a8e6f1f70296cf94231e752` | `sha256:7a3f971f234bcf8827428a27474973fbddecb894f33b37d5d9dfea8b79e1d8e6` | `132873`, a separate designation. |
| `555779501` | `sha256:654291119790f0e40a3409f1b9ab0a25029d8ccd8364ac286670f66ed025323f` | `sha256:ac0b96325339bcd5b95b85646bc3c4ae2de91ff9345e32840972c3809a837dc1` | `282786`, the same reserve under the previous source key. |

## Integration and activation boundary

This WDPA readiness chat owns the focused curation PR and its protected
deployment, publication verification and schedule activation. Notification
prerequisites PRs #366 and #368 are merged. The separate catalog feature lookup
PR #367 has no overlapping files and is outside this recovery.

The release-scoped curation must retain the full captured comparison hashes,
the real decision PR reference and reviewer. The original terrestrial Slack
request is `C0B12LDMB1A` / `1793650665.056749`; its decision-of-record reply must
identify the maintainer and both rationales without duplication or broadcast.
Related review alerts settle only with evidence for the same event.

After merge, deploy the retained exact tested worker image through the existing
protected deployment with a November 1 canary. Require actual completed
publication receipts and valid current release indexes for both assets before
protected schedule resume. The previous canary's zero exit and signed runtime
success represented an identity review stop, not publication acceptance.
