# Feature Identity Resolutions

This directory stores reviewed, release-scoped decisions for generated
`feature_id` ambiguities. Scheduled ingestion jobs load
`{asset-slug}.json` when building a release. A decision is applied only when its
release, new identity key, hashes, and matching previous feature IDs exactly
match the ambiguity observed during that run.

Use this only after reviewing the proposed release evidence. If the new feature
is the same logical feature with changed attributes, use
`reuse_previous_feature_id`. If the new feature should intentionally receive a
new generated sequence ID, use `assign_new_feature_id`.

```json
{
  "schema_version": 1,
  "asset_slug": "example-asset",
  "decisions": [
    {
      "release": "2026-05-01",
      "action": "reuse_previous_feature_id",
      "new_identity_key": ["source-key"],
      "new_geometry_hash": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "new_properties_hash": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "matching_geometry_feature_ids": ["7"],
      "matching_properties_feature_ids": [],
      "matching_geometry_properties_hashes": [
        "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      ],
      "matching_properties_geometry_hashes": [],
      "reuse_feature_id": "7",
      "rationale": "Same footprint; source attributes changed.",
      "reviewer": "jonaraphael",
      "pr_reference": "https://github.com/SkyTruth/shared-datasets-1/pull/123"
    }
  ]
}
```

For `assign_new_feature_id`, omit `reuse_feature_id`.
