---
asset_slug: "{asset-slug}"
title: "{Dataset title}"
category: "{top-level-category}"
subcategory: "{subcategory}"
status: "active" # active | deprecated | retired | scratch
owner: "{person-or-team}"
update_cadence: "static" # static | manual | daily | weekly | monthly | ad hoc
canonical_format: "fgb" # fgb | pmtiles | geojson | csv
last_updated: "YYYY-MM-DD"
source: "{source-name-or-url}"
license: "{license-or-terms-summary}"
---

# {Dataset title}

**Status:** active  
**Owner:** {person-or-team}  
**Last updated:** YYYY-MM-DD  
**Update cadence:** static | manual | daily | weekly | monthly | ad hoc  
**Canonical file:** `latest/{asset-slug}.{ext}`  
**Source:** {source name or URL}  
**License / terms:** {short note}

## What this is

Briefly describe the dataset in one or two paragraphs.

## When to use it

- Use this for ...
- Do not use this for ...

## Files

| File | Purpose |
|---|---|
| `latest/{asset-slug}.fgb` | Canonical vector dataset, if geographic |
| `latest/{asset-slug}.pmtiles` | Web map tiles, if applicable |
| `latest/{asset-slug}.geojson` | Small preview/interchange file, if applicable |
| `latest/{asset-slug}.csv` | Non-geometry table, if applicable |

## Schema notes

Describe important fields, join keys, units, coordinate assumptions, and any known quirks.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `{field_name}` | `{type}` | `{short meaning, units, or source-definition note}` |

If descriptions are not available from the source data or source documentation, list the names/types anyway and mark descriptions as needing source confirmation.

## Update notes

Describe how this dataset is updated. If cron-managed, link to the repo job, scheduler, or script.

## Known caveats

List limitations, source caveats, license caveats, and quality concerns.
