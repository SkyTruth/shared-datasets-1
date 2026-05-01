# Dataset Taxonomy

Use this document when classifying shared dataset assets for the bucket.

`catalog/categories.yaml` is the durable category/subcategory source. Do not
maintain a second full taxonomy tree in prose. Update the YAML, generated docs,
and review guidance together when the taxonomy changes.

## Classification Principle

Classify by what the dataset is, not by the project that first needed it.

Examples:

| Asset identity | Category direction |
|---|---|
| Reusable geography, boundaries, grids, or place names | `100-geographic-reference` |
| Imagery or remote-sensing derivatives | `200-imagery-derived` |
| Physical assets, facilities, permits, leases, or infrastructure | `300-infrastructure-industrial` |
| Incidents, detections, observations, alerts, or feeds | `400-events-observations` |
| Land cover, habitats, ecosystems, conservation, disturbance, or recovery | `500-conservation-ecosystems` |
| Vessels, AIS-derived products, fishing, or ocean activity | `600-maritime-ocean` |
| Non-spatial lookup/crosswalk/reference tables | `700-non-geographic-reference` |
| Labels, features, predictions, benchmarks, or model-ready data | `800-derived-ml-products` |

## Common Placements

| Asset | Typical placement |
|---|---|
| Country boundaries | `100-geographic-reference/110-boundaries/` |
| EEZ boundaries | `100-geographic-reference/120-marine-boundaries/` |
| WDPA | `100-geographic-reference/130-protected-areas/` |
| H3 or S2 helper grids | `100-geographic-reference/140-grids-indexes/` |
| Offshore platforms | `300-infrastructure-industrial/330-offshore-platforms/` |
| Mine footprints | `300-infrastructure-industrial/320-mining/` or `500-conservation-ecosystems/540-disturbance-recovery/`, depending on long-term identity |
| Oil slick detections | `400-events-observations/410-pollution-spills-slicks/` |
| VIIRS flare detections | `400-events-observations/420-flaring-thermal-events/` |
| AIS vessel registry | `600-maritime-ocean/620-vessel-registries/` |
| ISO country code lookup | `700-non-geographic-reference/710-country-admin-crosswalks/` |
| Cerulean training labels | `800-derived-ml-products/810-labels/` |
| Reusable model predictions | `800-derived-ml-products/830-predictions/` |

If two categories seem plausible, choose the one that describes the dataset's
long-term identity and document the rationale in the asset doc.

## Bucket Root

The bucket root should contain only intentional system-level entries:

```text
README.md
_catalog/
_templates/
_scratch/
_deprecated/
000-system/
100-geographic-reference/
200-imagery-derived/
300-infrastructure-industrial/
400-events-observations/
500-conservation-ecosystems/
600-maritime-ocean/
700-non-geographic-reference/
800-derived-ml-products/
```

`README.md` at the bucket root is the human landing page. Do not place routine
documents or data files at the bucket root.

## Change Control

Ask before creating a new top-level category. For subcategory changes, update
`catalog/categories.yaml`, affected asset docs, generated catalog outputs, and
any validation or review checklist that enforces the taxonomy.
