# First upload decisions

Read only for new or under-specified canonical intake. The entry-point skill
owns the command sequence; `next --json` owns each step's evidence schema.

## Discovery before start

Minimal user context does not justify vague metadata, arbitrary bucket
placement, or weak naming. Before proposing or performing an upload, infer as
much as practical from:

- File names, local paths, remote prefixes, and source URLs.
- Object metadata, file metadata, layer names, embedded dataset metadata, and
  README/source documentation.
- Schemas, property names, property types, and example rows.
- Existing nearby bucket assets, asset docs, README files, and catalog rows.
- Source documentation and internet searches when the source identity is
  discoverable.

Use the evidence to propose a lowercase kebab-case asset slug, taxonomy
classification, canonical format, target asset directory, and README content. If
confidence is still low after discovery, stop before remote writes and ask for
the missing confirmation.

Use `catalog/categories.yaml` for existing choices. For an ambiguous
classification, consult `docs/standards/dataset-taxonomy.md`; for a source-only
format, consult `docs/standards/asset-layout-and-formats.md`. Spreadsheets with
geometry become vectors; geometry-free tables become CSV; raw raster sources
normally become COG. A scratch copy of the original alone is incomplete.
Source/archive promotion requires an allowed path/format and an explanation of
why consumers should use the canonical artifact.

## Read the saved plan

`start` reports `state_file`; read that JSON's `plan`, including:

- `blocking_questions`: source, terms and original publication citation still
  needing confirmation. Resolve them before remote writes.
- `asset_root`, `canonical_path`, `asset_doc_path`, `standard_work_dir`,
  `publish_dir`, `available_formats`: use these rather than recalculating paths.
- `curator_field_options`: row/column counts, profile scope, identity and search
  recommendations, full per-field stats and translation candidates.

The state `contract` is the settled input contract; `steps` stores confirmed
structured evidence. `status --json` reports progress and `state_file`, not the
plan or field options. If a source cannot be profiled, obtain a canonical-artifact
profile before confirming identity; do not convert unavailable stats to success.

At `settle-contract`, decide latest-only versus versioned layout and access tier.
Use dated releases for cron-updated, multi-project-critical, hard-to-recreate or
reproducible snapshots. `exception_flags` record actual approvals, not defaults
copied from an evidence example. New categories/formats, public access, >=10 GB
footprints, incompatible schema changes, moving/deleting releases, unsafe
writes and infrastructure changes retain the AGENTS.md decision boundaries.

At `profile-fields`, present the actual decision table before selecting identity.
[Artifacts and metadata](artifacts-and-metadata.md) owns the exact identity rules,
translation questions and admission fields; load it at preparation/documentation.
New asset admission must identify consumers, shared rationale, source/terms/
citation, named steward, updates, footprint, alternatives and exit policy.
Neither `admission_complete` nor successful CLI validation substitutes for that
recorded evidence. Do not silently approve generated IDs or translations.

At `build-artifacts` / `validate-artifacts`, execute the suggested commands with
approved decisions. Consult each command's help for its interface. The default
FGB plan includes PMTiles; dated FGB releases also require metadata sidecar,
schema and manifest. Complete requested translation CSVs and locale sidecars
before staging. Preserve tool-path/version and artifact-validation reports.

At `document-asset`, use the asset doc/template and actual canonical profile.
Then perform the shared proposal's catalog build, staging and destination-stat
commands. Preserve every required companion; do not stage an original source
for canonical promotion merely because it was supplied by the user.

At `pr-ready`, review the proposed operations before submitting
`reviewed_pr_body`; run `validate` and `render-pr`. The final proposal includes
the immutable document, matching fence and recorded source/destination generations.
Review/merge and protected publication remain separate from concierge readiness.
