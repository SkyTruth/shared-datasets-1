# SkyTruth's process for resolving feature identity conflicts

Process version: 1. Applies to release-oriented feature identity decisions in
shared-datasets. The current automatic ambiguity policy is
`identity_key_corroboration_v1`. This document records the decision method and
its limits; it does not extend that implemented policy or approve future cases.

## Purpose and authority

Preserve a consumer's reference to the same feature across releases, while
keeping genuinely distinct records separately addressable. A protected-area
feature can represent a particular designation, not just the physical footprint.
Two designations can have identical geometry and different `feature_id` values.
A provider's source key, such as WDPA `SITE_PID`, and SkyTruth's published
`feature_id` are different identifiers, even when both look like integers.

Start here when a release reports an identity ambiguity. Use the
[resolution-file guide](../../catalog/feature-identity-resolutions/README.md)
for the schema, legal actions and CLI checks, and the owning ingestion runbook
for deployment and publication. [AGENTS.md](../../AGENTS.md) governs authority.

| Situation | What an agent may do without another semantic question |
| --- | --- |
| The implemented matcher finds no ambiguity, including the key-and-geometry corroboration rule below | Let the normal, validated ingestion and allocation policy run. A passing gate has the limitations described below. |
| An exact captured case already has an accepted release-scoped decision | Verify its release, key, complete hash/match sets and approval, then apply it through the existing workflow. Do not ask the same question again. |
| A new case fits a reviewed recommendation in this document | Collect evidence, classify it, propose the action and prepare a concrete review batch autonomously. A recommendation does not supply the new case's semantic approval. |
| A maintainer explicitly approves a bounded batch | Record precisely those cases and actions. Continue the already-authorized review, validation, merge and protected operations; the approval does not cover later discoveries. |
| Evidence is incomplete, contradictory, outside the rule's scope or a new edge condition | Hold publication, explain the gap and request the missing semantic choice. Extend the ruleset through review when the pattern is new. |

A human semantic decision is still required for newly escalated cases under the
current resolution contract. A future standing automatic rule requires explicit
policy approval, implementation and tests; documentation or a similar precedent
alone cannot activate it. Mechanical legality and real-world identity are
separate questions.

Human review can be an explicit choice on the captured batch or a maintainer's
review of the concrete decision PR; it need not be a separate chat question for
every row. Once the exact scope is accepted, do not request the same approval
again. Git or deployment authorization alone does not supply a new semantic
choice. An agent using maintainer credentials must not manufacture consent by
self-merging its own unapproved recommendation or naming a reviewer who did not
make the decision.

## Establish the facts before choosing an action

Capture the asset, target release, exact execution and deployed code/image. Pin
the previous release manifest, metadata and any geometry artifacts by URI,
generation, size and hash. Record the upstream version and immutable checksum or
conditional ETag. Use the deployed release's identity contract, geometry hash
algorithm and property exclusion/normalization policy on both sides.

For every case retain:

- The incoming source key, attributes, geometry hash and properties hash.
- The published ID already owned by that key, or verified absence from the
  **complete** previous baseline.
- Every geometry/property candidate, including its published ID, source key,
  attributes, stored geometry or retrievable pinned geometry artifacts, and
  complete captured match hashes.
- All changed fields, including JSON type changes; distinguish identifying
  fields from descriptive or administrative fields and explain that judgment.
- Whether candidate source keys survive anywhere in the complete incoming
  release. Document partitions, filters and row counts used to prove absence.
- The recommendation, applicable rule below, evidence, alternatives and effect
  on existing references. Preserve uncertainty rather than hiding it in a score.

Inspect the evidence retained by the deployed ingestion revision. If an older
execution did not retain a complete bundle, reconstruct read-only comparisons
from its captured hashes, pinned baseline and exact upstream bytes. Record
limitations explicitly. A sample, a search result, a missing row in one realm,
or a scan of selected partitions cannot establish global absence.

The November review used complete October metadata and conditional range reads
of all six upstream DBFs, with ZIP CRC checks and source-property hashes matching
the worker's captured cases. This avoided downloading the entire source ZIP;
range access was an evidence technique, not an identity rule. Those historical
reviews used worker-captured geometry hashes and pinned artifact references;
they did not establish that every incoming footprint had been retained in a
review bundle or visually inspected. State such gaps explicitly and obtain a
maintainer decision on the evidence available. Do not label incomplete evidence
as complete. A map overlay can help inspection, but is not an identity verdict.

## Rule catalogue

### ST-ID-01: Existing key and its own unchanged geometry

**Implemented automatic policy.** If the key exists in the baseline and the
previous feature carrying that same key has the incoming geometry hash, the
content corroborates the key. The gate can suppress the partial-match ambiguity,
including shared-footprint siblings and attribute edits. Normal key-based
allocation preserves its ID. Corroborated cases are counted in release provenance.

For generated-ID assets, ordinary allocation uses the approved assignment key
and persisted sequence. A no-ambiguity result does not mean the allocator
transfers an ID merely because hashes match. New keys normally get new IDs
unless an accepted replacement decision supplies an override.

Both conditions are required. Geometry shared with a different key does not
satisfy this rule. A non-unique or inconsistent baseline mapping cannot establish
ownership. If outside evidence suggests source-key recycling or a different
entity despite identical geometry, stop and escalate that evidence.

**Limit:** an upstream key recycled onto a different entity with the same stored
footprint can evade this heuristic. Hashes cannot distinguish that event from
some legitimate attribute changes. Automatic acceptance is not proof of source
correctness, legal continuity or complete change detection.

### ST-ID-02: Existing key moves onto another record's footprint

**Human-reviewed recommendation:** `keep_previous_key_mapping` when evidence
supports continuity of the existing record. Confirm its own previous allocation;
compare name, designation, jurisdiction, status, designation year and other
identifying fields; inspect changed geometry and the other matching records.
Preserve distinct designations even when their boundaries become identical.

Use the record's **own** ID, not one of the other geometry matches. Choosing a
matched record's ID would merge identities; assigning a fresh ID would deliberately
break continuity. Neither geometry equality nor an unchanged source key alone
settles the semantic choice. Apparent key reassignment, a materially different
entity, unexplained identifying-field changes or a split/merge requires review
under ST-ID-05 instead of a blanket keep recommendation.

Approved November decisions: Bonden was to keep `15378`, rather than `11347`
or `15093`; Tjärnberget was to keep `255953`, rather than `132873`. The later
batch approved keeping 111 existing IDs after complete source checks found
unchanged names, designations, countries, statuses
and designation years. These were explicit decisions for those exact cases.

### ST-ID-03: New source key replaces the same feature's old key

**Human-reviewed recommendation:** `reuse_previous_feature_id` when the incoming
key is absent from the complete baseline, the proposed old key is absent from
all incoming source partitions, and independent identifying evidence supports
one continuing feature. Check designation, name, jurisdiction, status/history,
stored geometry and all differences; inspect every competing candidate.

The chosen ID must be an allowed captured candidate. The complete incoming
release must not contain another identity that still holds that ID. Missing an
old key is necessary evidence for these replacement examples, but does not by
itself prove a replacement: a deletion followed by an unrelated new feature is
also possible. A unique footprint match is supporting evidence, not permission
to transfer an ID automatically.

Examples: Weid's new key `555779501` was approved to preserve `282786` from
absent `555769403`. Its designation year changed from 2023 to 2025; the
maintainer explicitly judged
continuity from the full comparison. That year difference is not a generally
allowed tolerance. Aracatiaçu's new `556323574` was approved to preserve
`17642` from absent `555886532`, with matching name, reserve designation, year
and geometry.

### ST-ID-04: A new, separate designation shares an existing footprint

**Human-reviewed recommendation:** `assign_new_feature_id` when the key is new
and the evidence identifies a distinct record rather than a replacement. Retain
older matching designations and their IDs. Let the existing monotonic allocator
choose the new ID; agents do not reserve numbers or reset counters.

Examples: new 2025 Svenska Björn SPA (`555778782`) and Selmunett SPA
(`555778676`) received decisions to assign fresh IDs. Older matching
SCI/Habitats or ecological/scientific designations remained in November.

**Limit:** a different label or later year can describe a rename,
reclassification or correction of the same entity. Conversely, a shared name,
category and footprint can still describe separate entities. If the old record
is absent, or the designation's continuity is unclear, compare ST-ID-03 and
seek a semantic decision rather than using a name/year shortcut.

### ST-ID-05: Conflicting evidence or an unmodelled identity transition

**Hold and escalate.** Examples include properties matching one feature while
geometry matches another, several plausible replacement IDs, apparent source-key
recycling, an existing key whose entity has changed, splits/merges, cross-asset or
realm transfers, duplicate keys, and changes to the identity contract or hash
policy. The supported matcher also reports `same_properties_changed_geometry`
and `conflicting_partial_matches`; this November batch did not establish a
general resolution for those classes.

Do not choose the smallest ID, first match, closest name, largest overlap or
most convenient action. An existing key can deliberately receive a fresh ID only
when the maintainer decides continuity should break and records why.

If external evidence contradicts an automatic suppression, the current matcher
may expose no admissible case for a resolution file. Do not fabricate an
ambiguity or force a stale override; obtain a reviewed producer/policy change.
A lineage transition needing one-to-many or many-to-one semantics may require
a new reviewed model; do not force it into the current three actions.

## Mechanical action constraints

| Evidence about the incoming key | Canonical decision and constraint |
| --- | --- |
| It already owns an ID and represents the same feature | `keep_previous_key_mapping`; retain its own allocation. Never move that key to a different record's ID. |
| It already owns an ID but represents a different feature | Explicitly reviewed `assign_new_feature_id`; explain the intentional break in continuity. |
| It is new and replaces a captured previous feature | `reuse_previous_feature_id`; choose a captured candidate, with no surviving competing holder in this release. |
| It is new and represents a separate feature | `assign_new_feature_id`; preserve the older records' allocations. |

Record `previous_feature_id_for_key`, including explicit `null` for a new key.
Check declarations offline, then verify them against the actual baseline in the
owning workflow. A legal action is not evidence that it is the correct action.
Never invent an allocation, recycle a deleted ID, merge records through an
override conflict, or repair a disagreement by resetting publication state.

## Review, recording and transparency

1. **Freeze evidence.** Bind the report to the release, baseline, upstream input,
   full case hashes and execution. Label sample and full-build evidence.
2. **Classify and recommend.** Apply the catalogue's preconditions to every case.
   Show exceptions and alternatives. Missing evidence is a blocker, not a default
   recommendation.
3. **Obtain the scoped decision.** The maintainer can approve a bounded batch
   with the complete case table, action for each case and a digest or exact case
   identities. Record the actual answer and decision maker, and distinguish the
   recommendation author/tool from that decision maker in the report or PR.
   A preselected option,
   silence or an earlier similar approval supplies no new authority.
4. **Record accepted curation.** Use the real PR reference, reviewer, rationale,
   prior key ownership and complete captured hash/match sets in the release-scoped
   resolution file. Identify the rule and its revision in the rationale or PR;
   do not invent unsupported schema fields. Preserve old release history.
5. **Validate and integrate.** Follow the existing legality, identity/allocation,
   complete preflight and exact-head review/merge contracts. Changed inputs or
   match sets require re-evaluation; stale decisions are rejected, not silently
   generalized. Read [CI preflight](../ci-preflight.md).
6. **Verify the actual release.** Run the complete build and acceptance path.
   A compatibility sample is a useful early check, not coverage of all cases.
   This thread's sample approvals were followed by 114 additional full-marine
   cases; full terrestrial processing can reveal more. A zero-exit review stop
   or signed runtime success is not publication acceptance. Verify actual
   receipts, indexes and the owning runbook's activation conditions.
   Fractional replays scope curation to keys present in their exact source
   filter. Decisions for included keys retain every hash and legality check;
   a complete build must still validate the entire release-scoped decision list.
7. **Reconcile the original review.** The existing protected notification path
   records the decision in the original Slack thread, names its decision maker,
   explains the rationale and links the reviewed PR. Large batches can summarize
   examples and link the complete record. Settle only review messages correlated
   to the same execution, asset and release after all captured cases are covered.
   Partial decisions stay open. Avoid duplicates and replacement parents.

Steps 6 and 7 have separate evidence and can complete in either order. A green
**Decision recorded** message establishes review resolution, not data publication
or native Monitoring closure. See [alert routing](../alert-routing.md#identity-decision-threads).

## Known limits and new edge conditions

- Equal geometry hashes mean equality under the recorded normalization and hash
  algorithm, not necessarily identical raw bytes or legal boundaries. Different
  hashes can result from precision, representation, CRS or algorithm changes.
- Property hashes cover the published field/exclusion policy. Equality can hide
  changes in excluded fields; differences can reflect routine metadata edits.
  A name comparison or aggregate hash cannot replace the field-level evidence.
- The matcher finds particular partial-match patterns. If both geometry and
  properties change and neither matches a baseline record, the current matcher
  can report no ambiguity and ordinary key-based allocation proceeds. This is a
  documented detection gap, not proof of continuity. Escalate externally observed
  reassignment; do not claim that every identity error will raise a review.
- A source key's stability, category, name or designation year is evidence, not
  a universal identity guarantee. Use the dataset's declared unit of identity.
  WDPA designation precedents must not silently become policy for other datasets.
- Missing, expired, generation-mismatched or incompatible baseline/source evidence
  prevents a sound comparison. Do not fall back to unpinned current bytes.
- Sampling and partial partition searches cannot prove completeness or absence.
  Batch approval covers its exact set, not unseen rows or later source revisions.

When a rule's preconditions fail, give the maintainer the exact missing or
contradictory facts and the effect of each legal alternative. If the same issue
reveals a new pattern, add or version a rule through a reviewed PR with its scope,
positive examples, counterexamples, evidence requirements, remaining uncertainty
and named decision authority. Preserve previous decision references; do not
rewrite old approvals to fit a new rule.

To make a recommendation automatic, explicitly approve that broader policy,
implement it at the shared producer/validation boundary, add meaningful positive
and negative regression cases, version its recorded policy/provenance and assess
existing decisions/baselines for compatibility. Verify both memory and disk paths
where used. A prose change or approval of one batch does not change deployed
behavior or create a standing autonomous exception.

## Other conflicts encountered during this recovery

| Conflict | Response and authority boundary |
| --- | --- |
| Upstream release unavailable | The owning job may record its verified unavailable-source skip and preserve the committed dataset. A forced skip is not an identity resolution. See the [WDPA runbook](../wdpa-monthly-runbook.md). |
| Malformed derived record or an interrupted publication claim | Fix the producer boundary and use the reviewed original-transaction recovery path. Preserve captured intent, receipts, counters and generation conditions; no claim clearing or blind replay. See [publication recovery](../feature-id-reset-installation.md). |
| CI success, deployed image and publication state disagree | Verify each separately: exact tested image, signed deployment/canary, actual publication receipts/indexes and scheduler. Runtime success alone cannot activate an unpublished release. See the [WDPA runbook](../wdpa-monthly-runbook.md). |
| A client/API contract fails before an operation | Reproduce and fix the actual serialization/CLI boundary; retain negative controls. Do not classify a reproducible adapter error as a transient cloud failure or retry it blindly. See [CI recovery](../ci-recovery.md). |
| Slack review resolution and a Monitoring incident disagree | Preserve event/message identity, verify the original reply and related message updates, and report native incident state separately. See [Slack incidents](../slack-incidents.md). |
| An obsolete observer or deployment conflicts with the current owner | Follow reviewed retirement and serialization paths; never resurrect retired workers, renew an expired temporary grant implicitly or start an extra worker to bypass ownership. See the [deployment skill](../../.claude/skills/deploy-scheduled-ingestion/SKILL.md). |

## Sources of truth and worked decisions

- Runtime definitions and safeguards: [release feature model](../../scripts/release_feature_model.py),
  especially `classify_identity_matches`, `find_identity_ambiguities`,
  `identity_decision_legality`, `validate_identity_resolutions` and
  `assign_generated_feature_ids`; [common feature metadata](../../ingestion/common/feature_metadata.py)
  and [disk identity index](../../ingestion/common/identity_index.py).
- Executable checks: [resolution checker](../../scripts/check_identity_resolutions.py),
  [legality tests](../../tests/test_identity_decision_legality.py) and
  [corroboration tests](../../tests/test_identity_key_corroboration.py).
- Exact accepted records: [marine decisions](../../catalog/feature-identity-resolutions/wdpa-marine.json)
  and [terrestrial decisions](../../catalog/feature-identity-resolutions/wdpa-terrestrial.json).
- November examples: Bonden in [PR #365](https://github.com/SkyTruth/shared-datasets-1/pull/365),
  Tjärnberget/Weid in [the terrestrial evidence](../wdpa-processing-evidence/november-terrestrial-identity-2026-11-02.md)
  and [PR #369](https://github.com/SkyTruth/shared-datasets-1/pull/369), and the
  111/1/2 marine batch in [its full case table](../wdpa-processing-evidence/november-marine-identity-2026-11-02.md)
  and [PR #370](https://github.com/SkyTruth/shared-datasets-1/pull/370).
- Historical rationale, not a current runbook: [key-corroboration policy history](../history/feature-identity-key-corroboration.md).
  Evidence/UI plans and reports explain observations; they do not grant decision
  or publication authority.
