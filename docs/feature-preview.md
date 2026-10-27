# Feature preview

Held EAMLIS source downloads can be inspected using the main-only **Inspect held
EAMLIS candidate** workflow. It copies the exact candidate and published baseline
into this private environment and refreshes its catalog without deploying
infrastructure or changing production publication authority. See the
[source-quality review procedure](eamlis-source-quality.md).

The [feature preview skill](../.claude/skills/feature-preview/SKILL.md)
owns the procedure for maintainers and agents.
Upload, deployment and viewer QA instructions live there; load only the selected route.
Architecture and IAM/state facts are linked from its environment reference.

Use the linked CLI `--help` for current flags and output contracts.
Start with [AGENTS.md](../AGENTS.md) for authority and safety rules.

The catalog viewer has an amber **FEATURE PREVIEW** heading, browser title and
persistent test-environment notice. It initially lists only preview releases.
Enable **Include published datasets** to load the published catalog alongside
them; Ctrl/⌘-click entries to overlay datasets on the map. Preview and Published
labels distinguish entries, including matching asset slugs. Turning the toggle
off removes published selections from the map and preserves preview selections.
Release comparisons within either source use that source's release history.

Published catalog entries, docs, release indexes, downloads, metadata lookups and
release comparisons use authenticated `/published/` routes. Each selection keeps
its original bucket, slug, release and artifact generations; private and internal
artifacts still require authorized signed URLs. A published-catalog failure is
shown next to the toggle and leaves preview browsing available. Production
viewers do not enable these routes or show the toggle.

The protected **Preview Terraform IAM sync** must provision the conditional
`feature_preview_published_reader` grant after merge, before deploying the new
preview viewer. The protected **PMTiles CDN route sync** must also apply the
production bucket CORS allowlist, which includes the preview viewer origin
`https://feature-preview-catalog-viewer-vmust7rama-uc.a.run.app`. Preview signs
GCS URLs directly, so this origin belongs only in `shared_bucket_public.tf`, not
in the CDN/redirector's `pmtiles_cdn_allowed_origins`. If that service origin
changes, update the bucket CORS through the same reviewed workflow before testing
published private tiles or metadata. CI runs CDN sync before dataset usage when
both are selected, because the usage deployment may only change bucket logging.
The IAM grant provides read-only access to the production catalog and canonical
dataset prefixes, excluding scratch and system objects. The preview root only
sets `CATALOG_VIEWER_INCLUDE_PUBLISHED=true`; it does not manage production IAM.
