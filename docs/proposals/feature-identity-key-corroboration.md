# Proposal: Identity-Key Corroboration in Feature Identity Ambiguity Detection

Status: implemented (2026-08-03). The rule ships as policy
`identity_key_corroboration_v1`; releases record how their identity questions
were settled in the published manifest at `identity.decisions`. The stale
`wdpa-marine.json` 2026-08-01 decisions were removed with the rule, per the
migration note below.
Owner: jonaraphael
Date: 2026-08-03

## Problem

The reviewed feature-identity ambiguity gate (PR #84) escalates partial hash
matches for maintainer review before a release can publish. It compares only
hashes and never consults the identity key. A new record is allowed through
silently in exactly one situation: a single previous feature matches on both the
geometry hash and the properties hash, and is the only match on either.

That condition assumes each hash is a near-unique fingerprint. For WDPA it is
not. WDPA files sibling designations of the same physical place on byte-identical
footprints — for example `Isla Contoy` (National Park, `SITE_PID` 12884) and
`Parque Nacional Isla Contoy` (Ramsar Site, `SITE_PID` 902275) share one
boundary polygon. The geometry hash therefore matches several previous features,
the properties hash matches exactly one, the two sets disagree, and the gate
escalates — even when nothing about the record's identity changed.

Measured on the `wdpa-marine` 2026-08-01 release attempt (canary execution
`wdpa-monthly-p56w9`):

- 1047 of 17,657 features (5.9%) were escalated for maintainer review.
- 983 of those were **byte-identical to their own previous selves** — geometry
  hash and properties hash both unchanged. They were escalated purely because
  1–4 sibling designation records share their footprint.
- 64 had genuine upstream attribute edits with unchanged geometry.
- In **all 1047**, the source `SITE_PID` was unchanged from the 2026-06-09
  baseline, and the previous feature carrying that same `SITE_PID` was among the
  geometry matches. No record's identity was actually in question.

The cost of this false-positive class was concrete:

- Publishing was blocked from 2026-08-01, with a failure alert every day the
  scheduler fired.
- Unblocking required a hand-reviewed 1047-decision file
  (`catalog/feature-identity-resolutions/wdpa-marine.json`, PR #133) whose
  every decision reduces to "keep the ID you already had" — the same ID that
  ordinary key-based assignment would have produced.
- Decisions are release-scoped, and the duplicate-footprint condition is
  structural rather than a one-time upstream event. Without a change, the same
  ~983 unchanged records re-escalate on **every** subsequent release and require
  a fresh decision file each month.
- It produced a near-miss. A decision generator that classified on hash-match
  shape alone — the same blind spot the detector has — chose
  `assign_new_feature_id` for the 6 records whose footprint is shared *and*
  whose attributes changed. Those `SITE_PID`s were unchanged, so that would have
  issued new `feature_id`s to six named protected areas (Isla Contoy, Banco
  Chinchorro, Yum Balam, Arrecife Alacranes, Isla San Pedro Mártir, Ría
  Celestún), breaking exactly the continuity the gate exists to protect. It was
  caught in review and corrected before merge, but the gate's own framing of the
  evidence invited the error.

## The current rule is internally inconsistent

For a record whose identity key is unchanged, today's behavior is:

| What changed upstream | Today |
|---|---|
| Nothing; unique footprint | quiet |
| Nothing; footprint shared with a sibling | **escalate** |
| Attributes only | **escalate** |
| Geometry only | **escalate** |
| Geometry *and* attributes, matching nothing | **quiet** — ID reused via key |

The most drastic change passes silently, because `find_identity_ambiguities`
returns early when neither hash matches anything, while the mildest changes
escalate. The gate is not expressing a coherent policy about when identity is
uncertain; it is expressing an artifact of hash-collision bookkeeping.

## Proposal

Add one suppression rule to `find_identity_ambiguities`
(`scripts/release_feature_model.py`):

> When the new record's identity key exists in the previous release **and** that
> same-key previous feature's `geometry_hash` equals the new record's
> `geometry_hash`, do not escalate. The content corroborates the key, so
> identity is not in question.

Both halves are load-bearing. The key alone is too weak — a recycled or
reassigned source key must still be caught. The geometry match alone is too weak
— that is the current false-positive source. Together they say: *this key
claimed to be this feature last month, and the footprint agrees.*

Implementation note: the function currently indexes previous records by
geometry hash and by properties hash only. It needs one additional index,
identity key → `(feature_id, geometry_hash, properties_hash)`, which is
available from the same sidecar records already passed in as `previous_records`.

## What remains escalated

Validated with a prototype of the rule against the real matcher
(`scripts/release_feature_model.find_identity_ambiguities`) plus synthetic
baselines containing a shared-footprint sibling pair:

| Case | Today | Proposed |
|---|---|---|
| Unchanged record, footprint shared with sibling (the 983) | escalate | suppress |
| Attributes edited, footprint shared with sibling (the 6) | escalate | suppress |
| Attributes edited, unique footprint (the 58) | escalate | suppress |
| Recycled key: same `SITE_PID`, different geometry matching another feature | escalate | **escalate** |
| New key reusing an existing footprint | escalate | **escalate** |
| Key unchanged, geometry moved, properties identical to its old self | escalate | **escalate** |

Applied to the real evidence, the rule suppresses **1047 of 1047** August
`wdpa-marine` ambiguities, leaving zero for human review, while still catching
every scenario in which identity is genuinely uncertain.

## Rollout

1. **Land the August publish first, with PR #133 as written.** Do not change
   gate behavior mid-recovery; get `wdpa-marine` 2026-08-01 published under
   reviewed decisions, then change the rule.
2. Implement the rule with the tests below.
3. Re-evaluate `wdpa-terrestrial`. Its ambiguity set (expected to be larger, and
   the same shape, since terrestrial shares the sibling-designation pattern)
   should largely or entirely disappear, avoiding a second hand-reviewed
   thousand-row decision file.
4. Handle the now-stale decision files — see migration below.

## Migration: stale decision files

`validate_identity_resolutions` raises `ReleaseFeatureModelError` for any
decision that does not match a *current* ambiguity ("stale feature identity
resolution does not match a current ambiguity"). Once the rule suppresses these
ambiguities, `catalog/feature-identity-resolutions/wdpa-marine.json` for release
2026-08-01 becomes stale by construction.

This is harmless while the release stays published, because decisions are loaded
only for the release being built and a published release is skipped. But any
later repair run targeting 2026-08-01 would hard-fail on stale decisions.

Recommended: remove the 2026-08-01 `wdpa-marine` decisions in the same PR as the
rule change, once that release is published and verified, and add a test
asserting a decisions file for a no-longer-ambiguous record is either absent or
tolerated. The alternative — relaxing the stale check to a warning — weakens a
useful guard against drifted decision files and is not recommended.

## Risks

- **Upstream recycles a `SITE_PID` onto a different feature that happens to have
  a byte-identical footprint.** The rule would then reuse the ID. This requires
  key reassignment *and* geometric identity simultaneously; identical geometry is
  strong corroboration, and the case is not distinguishable from a legitimate
  attribute rewrite by any means available to the gate.
- **Less human review volume.** That is the intent. The gate retains coverage of
  the situations where an ID could actually be misassigned: key changes, key
  reuse, geometry moves, and new keys landing on existing footprints.

## Tests

- Unit tests for all six cases in the table above.
- A regression fixture reproducing the real WDPA pattern: two sibling
  designations on one footprint, one unchanged record and one attribute-edited
  record, asserting neither escalates while a recycled key on the same footprint
  does.
- A test asserting the inconsistency is resolved: "attributes changed" and
  "geometry and attributes both changed" are treated consistently for an
  unchanged key.
- A test that the suppression requires *both* halves (key present, geometry
  equal), by flipping each independently.

## Open questions

1. **Should "geometry and attributes both changed, matching nothing" escalate?**
   It passes silently today. Symmetry argues for escalating it, but that adds
   review volume for what is usually a normal upstream edit. Recommendation:
   leave the behavior unchanged, and revisit only if a real misassignment is
   observed.
2. **Should suppressions be recorded for auditability?** Recommendation: yes —
   emit a count of key-corroborated suppressions in the run record, so the
   volume of automatically-resolved ambiguity stays visible instead of becoming
   invisible.
