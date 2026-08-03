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

## Most ambiguities never reach this directory

The gate applies the `identity_key_corroboration_v1` policy first: when a
record's identity key is unchanged **and** the previous feature carrying that
key has the same geometry hash, the content agrees with the key, identity is
not in question, and no decision is required. That policy exists because
sources such as WDPA file sibling designations of one place on byte-identical
footprints, which otherwise produced thousands of hash-match escalations per
release for records that had not changed at all. See
`docs/proposals/feature-identity-key-corroboration.md`.

A file here is therefore only needed for genuinely uncertain identity: a
recycled or reassigned source key, a key whose footprint moved onto another
feature, or a new key landing on an existing footprint.

## Decisions are published with the data

Every release records how its identity questions were settled inside the
published release manifest, at `identity.decisions`:

```json
{
  "schema_version": 1,
  "policy": "identity_key_corroboration_v1",
  "ambiguities_detected": 15230,
  "auto_resolved_key_corroborated": 15229,
  "escalated_for_review": 1,
  "reviewed_decisions_applied": 1,
  "reviewed_decision_actions": {"reuse_previous_feature_id": 1},
  "reviewed_decision_references": ["https://github.com/SkyTruth/shared-datasets-1/pull/133"],
  "reviewers": ["jonaraphael"]
}
```

The manifest ships next to the data in `releases/{date}/` and `latest/`, so a
consumer can see what decided a dataset's `feature_id`s — and read the reviewed
rationale in the linked PRs — without access to this repository. Publishing is
refused unless every escalated ambiguity is accounted for by a reviewed
decision, so those counts cannot silently disagree with what was published.

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
