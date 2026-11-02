# Bucket hygiene compatibility fixtures

These small JSON files preserve the published bytes inspected for incident
`SD-75645532FD57` on November 2, 2026. They contain metadata only.

| Fixture | Source object | Generation | SHA-256 |
| --- | --- | --- | --- |
| `world-polygons.manifest.json` | `gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/world-polygons/latest/world-polygons.manifest.json` | `1782509030308168` | `611b7499aba73470fd677fd6870567045f70d80f634d182fe06fef6bfde130a6` |
| `world-polygons.json` | `gs://skytruth-shared-datasets-1/_catalog/releases/world-polygons.json` | `1782509032675101` | `6701ac34a947d100b70a6400662325aab02cd74d5ae0e0e0e2a86db214b449fe` |

The release index selects the byte-identical manifest in
`releases/2026-06-26/` at generation `1782509029580300`. Its geometry hashes
describe source GeoPackage geometry blobs. They are readable historical evidence;
the current canonical geometry hash function cannot reproduce them from GeoJSON.
Source filenames and build paths inside the manifest are retained provenance,
not local test dependencies.
