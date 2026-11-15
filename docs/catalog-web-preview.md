# Catalog web preview

The [catalog web preview skill](../.claude/skills/static-catalog-web-preview/SKILL.md)
owns the procedure for maintainers and agents.
The skill links the runtime/output contract and protected deployment details.
For metadata-only publishing, use the publish skill's catalog refresh rule.

Use the linked CLI `--help` for current flags and output contracts.
Start with [AGENTS.md](../AGENTS.md) for authority and safety rules.


## Browser dependency policy

MapLibre 5.9.0 and PMTiles 4.3.0 retain their existing unpkg URLs, with SHA-384
Subresource Integrity and anonymous CORS. The checked hashes match the actual
CDN responses and the browser suite's locked npm packages. A changed CDN response
fails closed. The browser suite serves the original bytes and observes maps
through an initialization hook, without modifying the checked libraries.

Generated HTML carries an early CSP meta tag; viewer static responses also send
CSP and `X-Content-Type-Options: nosniff`. Both policies come from
`services/http_base.py`. Scripts are local modules or the two exact approved
library URLs; inline scripts, eval, objects, forms and base-URL overrides are
blocked. MapLibre retains blob workers and inline style attributes. Connections
are restricted to the serving origin, the configured SkyTruth tiles host, GCS,
OpenStreetMap tiles and Esri imagery. A new external data host requires a reviewed
policy change. The viewer's header also blocks embedding with `frame-ancestors`.
Use the catalog's existing meta configuration rather than adding inline scripts.

For a library upgrade, update the npm/browser locks, URLs, integrity hashes and
CSP together. Verify CDN bytes against the locked package, then run the complete
real-library browser suite, including the altered-CDN and injected-script
negative controls. Do not add `unsafe-eval` or permit arbitrary script origins.


When using the public `storage.googleapis.com/BUCKET/...` hosting URL, CSP
`'self'` covers that entire shared GCS origin, including other public buckets.
The two third-party libraries still require their exact SRI bytes, but CSP alone
does not isolate local module paths to this bucket. The dedicated viewer origin
provides the narrower origin boundary.
