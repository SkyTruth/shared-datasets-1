# Synthetic point archives

The repository-authored GeoJSON points are CC0 test material. Historical IDs are
`a1/a2`; current IDs are `b1/b2`. They intentionally occupy different positions
and use different sidecar labels, so mixed release identity cannot pass by
coincidence. There is one `mapdata` MVT layer and zooms0–4.

These descend from the two-point manual historical-consumer browser QA fixtures.
They were regenerated with relative input/output names on 2026-09-24 to remove
machine-local paths from archive metadata. Resolved tools were Tippecanoe2.79.0
and the PMTiles CLI reporting `dev` (commit/build unknown). That converter is
not claimed to reproduce identical archive bytes on other versions. Tests use
the committed verified bytes and enforce their hashes.

For each of `old` and `new`, the recorded generation command was:

```sh
tippecanoe -Z0 -z4 --no-feature-limit --no-tile-size-limit --no-feature-limit \
  -l mapdata --name 'Synthetic old browser fixture' \
  --description 'Repository-authored synthetic points; CC0' \
  -o old.mbtiles old.geojson
pmtiles convert old.mbtiles old.pmtiles --tmpdir=.
pmtiles verify old.pmtiles
pmtiles show old.pmtiles
tippecanoe-decode old.pmtiles 4 7 7
```

Use `new` instead of `old`, with tile4/8/7, for the current archive. Both archives
have `PMTiles` magic and spec version3, MVT/gzip tiles, nine addressed tiles and
valid directory/content ranges. Native verification and representative tile
decode succeeded; the decoded layer exposes only compact `feature_id` values.
At zoom0 Tippecanoe retains one point; at the test's fitted zoom both are present.

| File | Bytes | SHA256 |
| --- | ---: | --- |
| old.pmtiles | 1268 | dd0fcd07c883059a6d8ec76cc9cb9088bca7904887a253aa509e11f2af673927 |
| new.pmtiles | 1268 | 1d0d868fb77f04bbe0c00704a84896db380347d8186e2d33c088374947497871 |

When changing fixtures, rerun magic/verify/show and decode validation, update
these hashes and the input generator's digest check, and inspect the rendered
screenshots. Do not commit intermediate MBTiles or generated bundles.
