# Proposal: Release-Coupled Translation Maintenance and Debt Surfacing

Status: proposed
Owner: jonaraphael
Date: 2026-08-05

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
- Maintainers are told once per release, only when there is something to do,
  and handed something they can act on immediately.
- Consumers can distinguish a translated value from a source fallback.
- A release is never blocked by missing translations.

## Non-goals

- Performing machine translation inside the pipeline.
- A translation-management system, vendor integration, or human review workflow.
- Adding locales, or changing canonical metadata.

## Design

### 1. Translation state is derived, never decided

For each (`feature_id`, `field`, `locale`) in a release:

| State | Condition | Action |
|---|---|---|
| `current` | a row exists and its `source_value_hash` equals the current source value's hash | apply the translation |
| `stale` | a row exists, hashes differ — the source text changed | fall back to source, count as debt |
| `missing` | the field has a translatable value, no row for this locale | fall back to source, count as debt |
| `orphan` | a row exists for a `feature_id` absent from the release | retire the row, not debt |

`iter_localized_records` already computes all four. No new rules.

### 2. Translations carry forward automatically

**This is the "straight copied over" case.** A translation is keyed by the hash
of the text it was made from, so when a feature's source value is unchanged
between releases, its existing translation applies to the new release with no
human involvement. A release whose translatable text is entirely unchanged
therefore produces complete locale sidecars, zero debt, and **no notification**.

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
  "fallback_fields": ["DESIG_ENG"]
}
```

Optional, so sidecars published before this stay valid. A UI can then choose to
mark or suppress fallback values instead of presenting English as French.

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
      "machine_placeholder": 5120
    }
  }
}
```

`machine_placeholder` is counted separately from human-approved translations so
coverage never overstates human review.

### 6. One notification per release, only when there is debt

Fires after publish, only when some locale has `stale > 0` or `missing > 0`.

**Exactly-once is enforced by the data, not by the caller.** Before sending,
write a marker object at

```
{asset-root}/runs/{release}.translation-notice.json
```

with `if_generation_match=0`. If the write fails with a precondition error the
notice was already sent for that release and the send is skipped. Retries,
duplicate canaries and re-runs therefore cannot produce a second message — the
same guarantee the run records already give publishing.

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
- **`review_state` vocabulary** becomes explicit: `machine_placeholder` (usable,
  not reviewed), `needs_review` (existing), `human_approved`. Coverage reporting
  distinguishes them; publishing does not.

### 8. Never blocks a release

Unlike the identity gate, a missing translation degrades gracefully to the
source value. Debt is reported, never enforced. `fail_on_stale` remains
available for a deliberate manual run.

## Data model changes

| Where | Change | Compatibility |
|---|---|---|
| `docs/assets/{slug}.md` + catalog | `translation_locales` | new column, empty for assets without locales |
| translations CSV | documented `review_state` vocabulary | additive; existing `needs_review` unchanged |
| localized sidecar records | optional `translation` block | additive, readers unaffected |
| release manifest | optional `translations` block | additive, validated when present |
| bucket | `runs/{release}.translation-notice.json`, `_scratch/translation-debt/...` | new non-canonical objects |

## Tests

- Each of the four states classified from a fixture where one value changed, one
  is new, one is unchanged and one feature was removed.
- A release with no changed translatable text produces complete sidecars, zero
  debt and **no notification** — the carry-forward case.
- The notice marker makes a second send impossible: two consecutive notify calls
  for one release produce one message.
- The generated prompt round-trips: feeding its declared CSV header and columns
  back through `read_translation_source` yields rows that materialize.
- Tier gating: a private asset's message contains counts and a URI and none of
  its source values.
- Coverage counts in the manifest equal a full recomputation from the written
  sidecar, and `machine_placeholder` is excluded from human coverage.
- A missing translation never fails the release.

## Rollout

1. Declare `translation_locales` per asset; regenerate catalog outputs.
2. Materialize all declared locales with the release.
3. Publish the `translations` coverage block and the per-record state.
4. Add the notice, the marker and the prompt.
5. Backfill: regenerate the five stale `wdpa-terrestrial` locales against
   2026-08-01, which will surface the first real debt report.

Steps 1–3 are safe to land without 4; the debt becomes visible in the manifest
before anyone is paged about it, which is the right order for calibrating
thresholds.

## Open questions

1. **Should machine placeholders be published at all**, or should unreviewed
   values fall back to source until a human approves? Publishing them raises
   coverage and helps users of low-resource locales; it also risks a wrong name
   being displayed as authoritative. Recommendation: publish them, marked, with
   `state` visible per record so consumers can choose — but this is a
   product-facing call, not an engineering one.
2. **Who moves `machine_placeholder` to `human_approved`**, and does that need
   its own review path, or is a PR against the CSV enough?
3. **Per-locale ownership** — should the notification route to different people
   per locale, or is one channel message sufficient?
4. **Threshold** — notify on any debt, or only above a floor? Starting at "any"
   is noisier but calibrates the real volume; step 3 above answers this before
   step 4 ships.
