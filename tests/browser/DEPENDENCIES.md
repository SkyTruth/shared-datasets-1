# Catalog browser dependency assessment

Dependency assessment recorded on 2026-09-24. This records a follow-up discovered while
installing the browser test dependencies; it does not claim a clean security
audit or approve an untested production library upgrade.

The production catalog pins MapLibre GL JS 5.9.0. The browser smoke harness
deliberately uses the same version so it validates the deployed code path.
`npm audit` reports the critical
[GHSA-jrc7-96c5-q579 advisory](https://github.com/maplibre/maplibre-gl-js/security/advisories/GHSA-jrc7-96c5-q579).
The upstream advisory describes an attribution HTML sanitization bypass in
versions through 6.4.0, fixed in 6.4.1. Applications supplying untrusted style
or custom attribution strings can be affected.

The assessment inspected `web/catalog/map-preview.js` and PMTiles 4.3.0's
installed `src/adapters.ts`:

- Basemap attribution is defined in source constants, rather than supplied by
  catalog records or a remote style document.
- The app creates `new window.pmtiles.Protocol()` without enabling metadata.
  The protocol's default is `metadata=false`; its TileJSON response therefore
  does not forward archive attribution to MapLibre.
- The separate archive metadata read is used for vector-layer specifications;
  the constructed style does not copy archive attribution.

No route from untrusted attribution into the affected control was identified
in this inspection. This is a bounded source assessment, not proof that every
MapLibre vulnerability is unreachable. Do not enable remote/custom attribution
based on this assessment. Upgrade production and the test package together in
a separate reviewed change, then run this rendered suite and relevant unit
tests. The version upgrade is not silently bundled into a CI coverage change.
