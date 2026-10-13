# Reviewed deletion

When a user asks to delete canonical dataset objects, follow this ordered path
unless the user explicitly asks to stop earlier:

1. Confirm the request is explicitly destructive. Do not infer canonical
   deletion from vague cleanup, replacement, or refresh language.
2. Load `gcp-shared-datasets`, inspect the relevant asset docs/catalog row, and
   prefer non-destructive alternatives first: catalog deprecation, asset status
   changes, removing catalog visibility, or publishing a corrected replacement.
3. Enumerate exact object URIs. Prefix deletes, wildcard deletes, and
   generation-less deletes are invalid.
4. Stat every target object and record the current generation:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py stat \
  gs://skytruth-shared-datasets-1/path/to/object
```

5. Check references before deletion: `docs/assets/{asset-slug}.md`,
   `catalog/shared-datasets-catalog.csv`, bucket README content,
   `_catalog/releases/{asset-slug}.json`, run records, catalog web output, and
   known consumer paths. Update repo docs/catalog outputs first when references
   must change.
6. If deletion is part of a replacement, stage the replacement publish plan in
   the same PR and order the publish plan before the delete plan. The approved
   workflow promotes listed publish objects before it deletes listed objects.
7. Create a focused branch, stage only related repo changes, commit, push, and
   open a PR requesting review from `jonaraphael`, unless `jonaraphael` is also
   the PR author and GitHub blocks the reviewer request. The PR body must include
   exact target URIs, current generations, deletion rationale, consumer impact,
   replacement/deprecation state, validation commands, the generated immutable
   plan document, and a matching fenced
   `shared-datasets-delete-plan` JSON block:

````markdown
```shared-datasets-delete-plan
{
  "asset_slug": "example-asset",
  "proposal_id": "pr-123",
  "deletions": [
    {
      "uri": "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/example-asset/releases/2026-05-08/example-asset.fgb",
      "generation": "123456789",
      "reason": "Incorrect duplicate release superseded by approved replacement."
    }
  ]
}
```
````

8. Do not delete canonical objects from the local terminal. After a same-repo PR
   containing the delete-plan block is merged to `main`, the GitHub `Approved
   dataset mutation` workflow verifies `jonaraphael` acceptance and deletes the
   listed objects under the publisher identity using exact generation
   preconditions. If GitHub blocks self-review because `jonaraphael` authored
   the PR, the merged PR is treated as restricted self-acceptance. Deletion
   still requires that explicit PR; do not use standalone workflow dispatch or
   single-object fallback inputs to bypass the PR.
9. Verify the deletion: the live object is absent, no unintended objects were
   touched, catalog/release-index references no longer point at deleted paths,
   delete alerts/logs show the publisher identity, and any residual uncertainty
   is documented in the PR or final response.

Prepare the document and matching fence with:

```bash
uv run python scripts/reviewed_dataset_plan.py prepare --delete "$DELETE_PLAN" > "$WORK_DIR/pr-body.md"
```

Use the shared [reviewed proposal](reviewed-proposal.md) Git/preflight sequence.
