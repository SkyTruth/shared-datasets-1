# Deploy and viewer QA

- Deploy or replace the preview with the GitHub Actions workflow named
  `Deploy Feature Branch to Preview`.
- Select the feature branch or tag to deploy from the GitHub **Run workflow**
  branch dropdown.
- Select `preview_data_mode=preserve` when redeploying preview service or
  catalog viewer code against already loaded preview data. This is the default
  and rebuilds the preview catalog from existing preview release indexes.
- Select `preview_data_mode=reset` only when a clean preview slot is intended;
  it destroys disposable preview bucket contents, publishes an
  empty preview catalog shell, and requires reloading preview data.
- For code-only preview fixes, push the branch, dispatch the deploy workflow
  with `preview_data_mode=preserve`, wait for the workflow to finish, and
  validate the live preview URL before reporting that the issue is fixed.
- The deploy plans and applies `preview-source/terraform/envs/preview` from the
  selected branch through the preview resource-change allowlist, while stable
  production-scoped IAM bootstrap remains in the separate main-only sync
  workflow.
- The deploy reports both the preview API URL and the preview catalog viewer
  URL.
- Destroy the preview with `Destroy Preview Environment`.
- Do not run local preview Terraform applies unless the user explicitly requests
  a break-glass path and the protected Terraform skill permits it.

## Preview Viewer Cache And Click Validation

Use this section when troubleshooting feature lookup, sidecar cache behavior,
PMTiles preview clicks, or click-to-display performance in the preview catalog
viewer.

1. Inspect the exact asset release index before assuming a data reload is
   needed. Check `_catalog/releases/{asset-slug}.json` in
   `gs://skytruth-shared-datasets-1-preview/` and confirm the relevant
   `.metadata.ndjson.gz` entry, URI, generation, path, role, and format. Compare
   the exact live entry with the resolver and browser-side detection logic
   before reloading data.
2. Validate the asset that failed, not only a similar preview asset. A
   successful click on one release does not prove every preserved release index
   has the same `files[]` conventions.
3. For live viewer validation, open the deployed preview catalog viewer with a
   fresh cachebuster query string, select the target asset, wait for the map to
   render, and confirm the selected dataset metadata matches the intended asset
   and release date.
4. Before judging sidecar lookup as broken, confirm the map click hit a rendered
   feature. Use visual inspection, cursor state, or a known visible polygon or
   point. A blank map click should clear selection and is not evidence that the
   sidecar cache failed.
5. For feature metadata checks, confirm selected-feature details include fields
   from the sidecar, not just compact PMTiles properties such as `feature_id`.
   For WDPA-like assets, useful proof fields include provider IDs, names,
   designation/status fields, and reported/GIS area fields.
6. When measuring click-to-display performance, measure from click to enriched
   selected-feature content for a changed feature selection. Separate cold
   sidecar download timing from warm cached clicks, and report when browser
   automation or DOM snapshot polling overhead makes the measurement
   conservative.
7. The live HTTP lookup endpoint may be IAP-protected from a local terminal.
   If direct terminal calls return IAP credential errors, validate through the
   authenticated browser session or clearly report that endpoint smoke testing
   was blocked by IAP.
