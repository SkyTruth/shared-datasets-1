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

The `union-before`/`union-after` CC0 fixtures cover one removed point (`a1`),
one novel point (`b1`), identical geometry with changed metadata (`common`),
an unchanged point (`fixed`), and a moved point (`move`). The `changes` layer
contains only `feature_id`; all four records survive zoom 0. Source geometry
hashes and canonical properties live in the generated test sidecars, independently
of these simplified display tiles. Another 101 sidecar records deliberately lack
display tiles, proving comparison counts use complete inputs.

Built on 2026-10-01 with `/usr/local/bin/tippecanoe` v2.79.0 and
`/usr/local/bin/pmtiles` reporting `dev, commit none, built at unknown`. Relative
source/output names keep archive metadata portable. The recorded command for
each side, using its matching name and description, is:

```sh
tippecanoe -f -Z0 -z4 --drop-rate=1 --no-feature-limit --no-tile-size-limit \
  -l changes --name 'Synthetic comparison before' \
  --description 'Repository-authored synthetic points; CC0' \
  -o union-before.mbtiles union-before.geojson
pmtiles convert union-before.mbtiles union-before.pmtiles
pmtiles verify union-before.pmtiles
pmtiles show union-before.pmtiles
tippecanoe-decode union-before.pmtiles 0 0 0
```

Both archives have PMTiles v3 magic, gzip MVT tiles, verified directory/content
ranges, and four decoded zoom-0 features with only `feature_id`. Before has
17 addressed tiles; after has 13. Tests use these checked-in bytes rather than
requiring native build tools or claiming tool-version-independent reproduction.

| File | Bytes | SHA256 |
| --- | ---: | --- |
| union-before.pmtiles | 2316 | d3f55632463ab954c1eb611fb58ddde25f2ba0926899db351588d5aa36c7ac99 |
| union-after.pmtiles | 2050 | f2ed884416a9e502ee949141a029d2d3b967e3c598b919e60770db3494e6f5d2 |


The `union-polygons-before`/`union-polygons-after` CC0 fixtures contain four
polygons per release: an old/new shape with the same reset numeric ID, shared
geometry with altered metadata, unchanged geometry, and a moved shape. Each
release uses a different generated identity contract. The browser test checks
actual fill pixels for red/green/yellow and reduced unchanged opacity while
feature-ID classifications remain withheld.

Built on 2026-10-02 with resolved tools `/usr/local/bin/tippecanoe` v2.79.0,
`/usr/local/bin/pmtiles` reporting `dev, commit none, built at unknown`, and
`/usr/local/bin/tippecanoe-decode` from the same Tippecanoe installation. Each
command ran in a named task workspace with relative filenames:

```sh
tippecanoe -f -Z0 -z6 --no-tiny-polygon-reduction --no-feature-limit --no-tile-size-limit \
  -l changes --name 'Synthetic comparison polygons before' \
  --description 'Repository-authored polygons; CC0' \
  -o union-polygons-before.mbtiles union-polygons-before.geojson
pmtiles convert union-polygons-before.mbtiles union-polygons-before.pmtiles
pmtiles verify union-polygons-before.pmtiles
pmtiles show union-polygons-before.pmtiles
tippecanoe-decode union-polygons-before.pmtiles 0 0 0
```

Repeat with `after` for the other archive. Both have PMTiles v3 magic,
verified gzip MVT contents, 25 addressed tiles, and four decoded zoom-0 polygons
with only `feature_id`. Metadata contains no machine-local workspace paths.

| File | Bytes | SHA256 |
| --- | ---: | --- |
| union-polygons-before.pmtiles | 3407 | 0c372994eb305e3e52e88f1ba138cc5c38d463c9d70ef125bd687c5990ea24b2 |
| union-polygons-after.pmtiles | 3016 | 8c4603fb7bb5db14ee051d00ab48c4d04f9ff2ecc30545aea343fa6d91dd1d40 |
