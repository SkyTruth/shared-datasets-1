# Proposal: Release-Coupled Translation Maintenance and Debt Surfacing

Status: proposed
Owner: jonaraphael
Date: 2026-08-05
Product decisions recorded: 2026-09-30

## Problem

Seven assets publish locale sidecars, four of them in six locales:

| Asset | Locales |
|---|---|
| `wdpa-terrestrial`, `wdpa-marine`, `marine-regions-eez`, `iho-world-seas` | `es`, `fr`, `id`, `pt`, `pt_br`, `sw` |
| `eamlis-abandoned-mine-land-inventory`, `petrodata` | `es` |

Only `es` is materialized as part of a release. The other five come from
`metadata-localization.yml`, which runs on demand. So after `wdpa-terrestrial`
published 2026-08-01, its `fr`, `id`, `pt`, `pt_br` and `sw` sidecars were still
the 2026-06-10 files: June names beside an August feature set, with no entries
for features added since, and no signal to anyone that this had happened.

Three gaps, none of which is a missing-data problem:

1. **Locale sidecars drift from the release.** Nothing couples them to publish.
2. **Nobody is told translations need attention.** The classification data
   already exists per release but is computed only when someone runs the
   localization workflow by hand.
3. **Consumers cannot tell a translation from a fallback.** An untranslated
   field silently carries the source-language value, so a UI showing `fr` cannot
   know it is displaying English.

## What already exists

This proposal is mostly wiring, not new machinery. `scripts/feature_metadata_localization.py`
already provides:

- `TranslationRow` with `source_value_hash` — the hash of the source value the
  translation was made from, which is the entire basis for deciding staleness.
- `iter_localized_records`, which per feature and field compares the recorded
  hash against the current source value and classifies the outcome.
- `LocalizationReport`, which already counts `applied`, `stale`, `orphan`,
  `missing_field` and `untranslated`, and carries per-row detail lists.
- `materialize_locale_sidecars` (plural) and `batch_report_payload`.

What is missing is when it runs, what it publishes, and who it tells.

## Goals

- Locale sidecars are never stale relative to the feature set of the release
  they sit beside.
- Translation debt is classified deterministically, with no human judgement
  about *whether* action is needed.
- Maintainers receive at most one notice per asset release in one shared channel
  when at least one maintained locale has 1% or more missing usable translations,
  and are handed something they can act on immediately.
- Consumers can distinguish a translated value from a source fallback.
- A release is never blocked by missing translations.

## Non-goals

- Performing machine translation inside the pipeline.
- A translation-management system, vendor integration, or separate approval UI.
  Experts approve translations by manually updating and reuploading the CSV
  through the existing reviewed publication workflow.
- Adding locales, or changing canonical metadata.

## Design

### 1. Translation state is derived, never decided

For each (`feature_id`, `field`, `locale`) in a release:

| State | Condition | Action |
|---|---|---|
| `current` | a usable row exists and its `source_value_hash` equals the current source value's hash | apply the translation, including an unreviewed AI translation |
| `stale` | a row exists, hashes differ — the source text changed | fall back to source, count as debt |
| `missing` | the field has a translatable value but no usable current or stale row for this locale, including a failed translation task | fall back to source, count as debt |
| `orphan` | a row exists for a `feature_id` absent from the release | retire the row, not debt |

`iter_localized_records` already applies current translations and reports stale,
orphan, missing-field and failed rows. Extend reporting to enumerate every
eligible translatable field value per locale: a record with one translated field
can still have another field missing. Count each eligible field value exactly
once as `current`, `stale` or `missing`; prefer a usable current row when historical
stale rows also exist. Orphan rows and removed fields do not enter that denominator.
Hash equality establishes freshness, not translation accuracy or expert approval.

### 2. Translations carry forward automatically

**This is the "straight copied over" case.** A translation is keyed by the hash
of the text it was made from, so when a feature's source value is unchanged
between releases, its existing translation applies to the new release with no
human involvement. If translations were already complete and the translatable
text is unchanged, the new release has zero debt and **no notification**.
Existing gaps remain debt even when source text is unchanged, and the same 1%
notification threshold still applies.

Copying the *sidecar object* itself is not a viable shortcut and should not be
attempted: every sidecar record embeds its own `release` field, so the canonical
sidecar differs between releases even when every value is identical. Carry
forward the *translations*; regenerate the file. Materialization is a streaming
transform over the canonical sidecar, so the cost is small and bounded.

### 3. Every maintained locale is materialized with the release

The set of maintained locales becomes explicit rather than inferred from
whatever happens to be in the bucket: declare `translation_locales` in
`docs/assets/{asset-slug}.md` and carry it into the generated catalog. Inferring
from existing objects cannot express "this asset should gain `fr`", and silently
drops a locale whose sidecar was never written.

The release then materializes each declared locale, in the job for assets that
build their own sidecars and in `metadata-localization.yml` for the rest. Either
way the sidecars land with the release, not days later.

### 4. Consumers can see what they are reading

Each localized sidecar record gains an additive block:

```json
"translation": {
  "locale": "fr",
  "state": "partial",
  "translated_fields": ["NAME_ENG"],
  "fallback_fields": ["DESIG_ENG"],
  "machine_fields": ["NAME_ENG"],
  "human_reviewed_fields": []
}
```

Optional, so sidecars published before this stay valid. AI translations are
published and visible to users before expert approval. `machine_fields` and
`human_reviewed_fields` identify their review provenance separately from
`translated_fields` and `fallback_fields`; missing review provenance does not
imply expert approval. A UI can mark AI-generated or fallback values explicitly.

### 5. Coverage travels with the data

The release manifest gains a `translations` block beside `identity.decisions`,
following the same principle — provenance ships next to the bytes:

```json
"translations": {
  "schema_version": 1,
  "locales": {
    "fr": {
      "translatable_values": 304816,
      "current": 298402,
      "stale": 118,
      "missing": 6296,
      "orphan": 44,
      "coverage": 0.979,
      "machine_placeholder": 5120,
      "human_reviewed": 293282
    }
  }
}
```

`coverage` measures usable current translations, including AI-generated values.
`machine_placeholder` and existing machine/document provenance are reported
separately from `human_reviewed`, so usable coverage does not imply expert
approval. `current + stale + missing = translatable_values`; orphan rows and
removed fields are counted separately. AI values awaiting approval are usable
translations and do not count as missing solely because they are unreviewed.

### 6. One channel, at most one notice per release, at a 1% threshold

After publish, compute missing usable translations separately for each maintained
locale:

```text
missing_usable = stale + missing
eligible = translatable_values > 0
           and 100 * missing_usable >= translatable_values
```

The threshold is inclusive: exactly 1% triggers a notice. Compare integer counts
rather than rounded coverage percentages. Failed tasks count as missing; usable
AI translations count as current even before approval. Orphan rows and removed
fields are excluded. A locale with zero eligible values does not trigger a notice.

Send one combined message to the single configured channel when any locale meets
the threshold, identifying the qualifying locales and their counts. No per-locale
routing or separate notices. Debt below 1% remains visible in manifests and
reports without generating a notice.

Before sending, claim a marker object at

```
{asset-root}/runs/{release}.translation-notice.json
```

with `if_generation_match=0`. A precondition error means another attempt already
claimed the notice, so skip sending. Retries, duplicate canaries and re-runs
cannot start a second send for that asset release.

This provides **at-most-once sending**, not guaranteed delivery. A crash or failed
send after the marker is created can leave the release without a notification;
surface that failure operationally rather than reporting the marker as proof of
delivery. The notification outcome never blocks or rolls back the release.

### 7. The copy-pastable prompt

The notification carries a fenced block that can be pasted into a Claude agent
to produce machine placeholders. It states the output contract exactly, so the
result can be appended to the translations CSV without editing:

````text
```
Translate the following WDPA terrestrial protected-area names into French (fr).
These are proper names of protected areas; preserve official names where one
exists in the target language, and otherwise transliterate rather than invent.

Return CSV rows only, no prose, with this header:
feature_id,field,locale,source_value_hash,value,review_state,notes

Set review_state=machine_placeholder and notes=awaiting human review.
Copy feature_id, field, locale and source_value_hash through unchanged.

feature_id,field,locale,source_value_hash,source_value
297926,NAME_ENG,fr,sha256:a1b2...,Rancho La Viga
298104,NAME_ENG,fr,sha256:c3d4...,Presa Neutla y su Zona de Influencia
```
````

Constraints that shape it:

- **Slack section limit.** At most 25 rows inline; the full set is written to
  `_scratch/translation-debt/{asset-slug}/{release}/{locale}.csv` and linked.
  `_scratch/` is non-canonical staging, so this creates no dataset contract.
- **Access tier.** Per the tier rule already adopted for identity evidence,
  public assets may carry source values inline; for `private` and `internal`
  assets the message carries counts and the object URI only, never the values.
- **Review provenance.** `machine_placeholder`, `machine_translated` and
  `document_translated` are usable without expert approval. Preserve existing
  `needs_review` and `source_provided` provenance. `human_reviewed` is the existing
  repository value for expert approval; do not introduce a second
  `human_approved` label. `translation_failed` has an empty value and is never
  applied. Coverage distinguishes provenance; approval is not a publication gate.

### Expert approval by manual update and reupload

An expert reviews the translation source CSV, corrects values where necessary,
sets reviewed rows to `review_state=human_reviewed`, and reuploads the updated
source through the existing reviewed dataset publication workflow. Preserve the
feature, field, locale and current source-value hash keys; when the source changed,
review against the new source value and update its hash and translation together.

Regenerate and publish the affected locale sidecars and coverage from that source
file. There is no separate approval service or new review workflow, and no direct
local write to canonical bucket objects. Expert approval changes review provenance;
it does not by itself change usable coverage or trigger missing-translation alerts.

### 8. Never blocks a release

Unlike the identity gate, a missing translation degrades gracefully to the
source value. Debt is reported, never enforced. `fail_on_stale` remains
available for a deliberate manual run.

## Data model changes

| Where | Change | Compatibility |
|---|---|---|
| `docs/assets/{slug}.md` + catalog | `translation_locales` | new column, empty for assets without locales |
| translations CSV | machine provenance retained; experts set `human_reviewed` after manual review | additive; existing provenance unchanged |
| localized sidecar records | optional `translation` block | additive, readers unaffected |
| release manifest | optional `translations` block | additive, validated when present |
| bucket | `runs/{release}.translation-notice.json`, `_scratch/translation-debt/...` | new non-canonical objects |

## Tests

- Each of the four states classified from a fixture where one value changed, one
  is new, one is unchanged and one feature was removed.
- A release with complete existing translations and no changed translatable
  text produces complete sidecars, zero debt and **no notification**.
- Unchanged source text with existing gaps retains those gaps and applies the
  same notification threshold.
- Below 1% emits no notice; exactly 1% and above emit one combined channel
  message. Cover small denominators, zero eligible values, and one undercovered
  locale among otherwise complete locales.
- Count missing and stale values per eligible field, including partially
  translated records; exclude orphan rows and count failed tasks as missing.
- A current AI translation is visible before expert approval and does not count
  as missing. After an expert CSV update and reupload, materialization records
  `human_reviewed` provenance and corrected values.
- Two eligible notify calls for one asset release start at most one send; a
  failed send after claiming the marker is reported without a duplicate send.
- The generated prompt round-trips: feeding its declared CSV header and columns
  back through `read_translation_source` yields rows that materialize.
- Tier gating: a private asset's message contains counts and a URI and none of
  its source values.
- Coverage counts in the manifest equal a full recomputation from the written
  sidecar; AI provenance is included in usable coverage and excluded from expert
  approval counts.
- A missing translation never fails the release.

## Rollout

1. Declare `translation_locales` per asset; regenerate catalog outputs.
2. Materialize all declared locales with the release.
3. Publish the `translations` coverage block and the per-record state.
4. Add the notice, the marker and the prompt.
5. Backfill: regenerate the five stale `wdpa-terrestrial` locales against
   2026-08-01, which will surface the first real debt report and trigger a notice
   only if at least one locale meets the 1% threshold.

Steps 1–3 can land before 4; debt and review provenance become visible in the
manifest before channel notifications are enabled. Apply the agreed inclusive
1% threshold when step 4 ships.

## Product decisions

The maintainer resolved these decisions on 2026-09-30:

1. **Publish AI translations before approval.** Users may see usable AI-generated
   values immediately; retain their machine provenance so they are distinguishable
   from expert-reviewed values.
2. **Expert approval is a manual update and reupload.** An expert corrects the
   translation CSV, marks reviewed rows `human_reviewed`, and republishes it
   through the existing reviewed upload path. No separate approval system.
3. **One notification channel.** Send one combined notice per qualifying asset
   release rather than routing notices to separate locale owners.
4. **A minimum of 1% missing usable translations.** Evaluate each maintained
   locale independently using `(stale + missing) / translatable_values >= 0.01`.
   AI values awaiting approval are not missing; below-threshold debt stays visible
   in coverage reporting without an alert.

