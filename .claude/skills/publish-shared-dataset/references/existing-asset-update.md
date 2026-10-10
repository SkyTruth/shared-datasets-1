# Existing-asset update decisions

Read only for a verified existing asset. The entry-point skill owns the ordered
commands; [reviewed proposal](reviewed-proposal.md) owns staging and PR review.
Initialize the shared workspace variables there before running commands.

1. Identify the exact asset, then inspect its asset doc, generated catalog row,
   existing releases, run records and `_catalog/releases/{asset-slug}.json`.
   Preserve slug, category, canonical path, formats and identity contract.
2. Check scheduled ownership. A normal cron refresh uses the owning job and
   deploy skill; manual correction requires the user's explicit instruction.
3. Decide new dated release versus latest-only update versus existing-release
   metadata/display repair. Record the source version and consumer impact.
4. Compare source/license/citation, schema, CRS, counts, bounds and formats.
   Ask before incompatible schema changes, slug/path changes, new
   formats, or unclear license/citation changes.
5. Build replacements in the task workspace using the existing contract and
   [artifact reference](artifacts-and-metadata.md) when needed. Preserve prior
   identity mappings. If leaving a companion unchanged, identify it explicitly;
   `publish-release --help` documents `--allow-stale-format` for dry-run checks.
6. Run the entry point's dry-run command for supported formats. Its report
   contains release/latest operations, observed generations, metadata uploads,
   validation and stale-format notes. It does not authorize canonical writes.
   For a same-release repair, enumerate exact replacement objects rather than
   treating an existing dated release as a new no-clobber release.
7. Update asset docs for durable contract changes only: source/terms/citation,
   schema, file table, formats, access/cadence or useful consumer update notes.
   Cron freshness belongs in bucket release indexes/run records, not tracked
   date churn. Regenerate/check/export docs and rebuild the catalog locally.
8. Build the promotion payload from observed staging evidence. Its shape is
   `asset_slug`, `proposal_id`, `promotions[]`; each promotion has `source_uri`,
   `source_generation`, `destination_uri`, `destination_generation`, and needed
   `content_type`/`cache_control`. Empty destination generation means verified
   absence. Order dated objects, latest copies, then run/index metadata.
   Use `reviewed_dataset_plan.py prepare --help` and the shared proposal command
   to validate it and write the immutable document and fence.
9. Follow the shared proposal commit/preflight/push/PR sequence. Record dry-run
   results, unchanged companions and unresolved consumer impacts in the body.
   Protected promotion and its verification happen after review/merge.

Do not delete old releases during refresh. For an explicit destructive request,
load [reviewed deletion](reviewed-deletion.md) instead of treating deletion as
an update side effect. Owned claims/receipts cannot be repaired by ad hoc manual
promotion; route interrupted owned publications to their original owner.
