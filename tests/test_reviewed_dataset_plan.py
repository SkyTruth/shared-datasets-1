from __future__ import annotations

import json
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from scripts import reviewed_dataset_plan


BUCKET = "skytruth-shared-datasets-1"


def event_path(body: str) -> Path:
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    with tmp:
        json.dump({"pull_request": {"body": body}}, tmp)
    return Path(tmp.name)


class ReviewedDatasetPlanTests(unittest.TestCase):
    def test_detect_finds_publish_and_delete_plan_fences(self):
        body = """
```shared-datasets-publish-plan
{"asset_slug":"example-asset","proposal_id":"pr-123","promotions":[]}
```

```json shared-datasets-delete-plan
{"asset_slug":"example-asset","proposal_id":"pr-123","deletions":[]}
```
"""

        self.assertIsNotNone(reviewed_dataset_plan.find_fenced_json(body, "shared-datasets-publish-plan"))
        self.assertIsNotNone(reviewed_dataset_plan.find_fenced_json(body, "shared-datasets-delete-plan"))

    def test_normalize_publish_plan_accepts_reviewed_scratch_to_canonical_copy(self):
        normalized = reviewed_dataset_plan.normalize_publish_plan(
            {
                "asset_slug": "example-asset",
                "proposal_id": "pr-123",
                "release_index_asset_slugs": ["example-asset", "other-asset", "example-asset"],
                "promotions": [
                    {
                        "source_uri": (
                            f"gs://{BUCKET}/_scratch/pending-publishes/"
                            "example-asset/pr-123/example-asset.fgb"
                        ),
                        "source_generation": 123,
                        "destination_uri": (
                            f"gs://{BUCKET}/100-geographic-reference/130-protected-areas/"
                            "example-asset/latest/example-asset.fgb"
                        ),
                        "destination_generation": "",
                        "content_type": "application/octet-stream",
                        "cache_control": None,
                        "compatibility_waiver": {
                            "asset_slug": "example-asset",
                            "blocked_changes": [{"kind": "removed", "field": "retired"}],
                            "rationale": "Source retired the field and reviewer approved the break.",
                            "consumer_impact": "Known consumers do not use the retired field.",
                            "reviewer": "jonaraphael",
                            "pr_reference": "https://github.com/SkyTruth/shared-datasets-1/pull/123",
                            "migration_path": "Consumers that need the field should pin the prior release.",
                        },
                    }
                ],
                "breaking_changes": [
                    {
                        "category": "feature_identity",
                        "summary": "feature_id changed from source IDs to generated IDs.",
                        "consumer_action": "Refresh joins that use feature_id before reading latest.",
                        "affected_surfaces": ["latest/example-asset.fgb", "latest/example-asset.metadata.ndjson.gz"],
                    }
                ],
            }
        )

        promotion = normalized["promotions"][0]
        self.assertEqual(promotion["source_generation"], "123")
        self.assertEqual(promotion["cache_control"], "")
        self.assertEqual(promotion["compatibility_waiver"]["blocked_changes"][0]["field"], "retired")
        self.assertEqual(normalized["breaking_changes"][0]["category"], "feature_identity")
        self.assertEqual(normalized["release_index_asset_slugs"], ["example-asset", "other-asset"])

    def test_normalize_publish_plan_rejects_malformed_release_index_asset_slug(self):
        with self.assertRaisesRegex(reviewed_dataset_plan.PlanValidationError, "release_index_asset_slugs"):
            reviewed_dataset_plan.normalize_publish_plan(
                {
                    "asset_slug": "example-asset",
                    "proposal_id": "pr-123",
                    "release_index_asset_slugs": ["BadSlug"],
                    "promotions": [
                        {
                            "source_uri": (
                                f"gs://{BUCKET}/_scratch/pending-publishes/"
                                "example-asset/pr-123/example-asset.fgb"
                            ),
                            "source_generation": "123",
                            "destination_uri": (
                                f"gs://{BUCKET}/100-geographic-reference/130-protected-areas/"
                                "example-asset/latest/example-asset.fgb"
                            ),
                        }
                    ],
                }
            )

    def test_normalize_publish_plan_rejects_malformed_breaking_change(self):
        base_plan = {
            "asset_slug": "example-asset",
            "proposal_id": "pr-123",
            "promotions": [
                {
                    "source_uri": (
                        f"gs://{BUCKET}/_scratch/pending-publishes/"
                        "example-asset/pr-123/example-asset.fgb"
                    ),
                    "source_generation": "123",
                    "destination_uri": (
                        f"gs://{BUCKET}/100-geographic-reference/130-protected-areas/"
                        "example-asset/latest/example-asset.fgb"
                    ),
                }
            ],
        }

        with self.assertRaisesRegex(reviewed_dataset_plan.PlanValidationError, "category"):
            reviewed_dataset_plan.normalize_publish_plan(
                {
                    **base_plan,
                    "breaking_changes": [
                        {
                            "category": "not-real",
                            "summary": "Bad category.",
                            "consumer_action": "Update consumers.",
                            "affected_surfaces": ["latest/example-asset.fgb"],
                        }
                    ],
                }
            )

        with self.assertRaisesRegex(reviewed_dataset_plan.PlanValidationError, "summary"):
            reviewed_dataset_plan.normalize_publish_plan(
                {
                    **base_plan,
                    "breaking_changes": [
                        {
                            "category": "schema",
                            "summary": "",
                            "consumer_action": "Update consumers.",
                            "affected_surfaces": ["latest/example-asset.fgb"],
                        }
                    ],
                }
            )

        with self.assertRaisesRegex(reviewed_dataset_plan.PlanValidationError, "affected_surfaces"):
            reviewed_dataset_plan.normalize_publish_plan(
                {
                    **base_plan,
                    "breaking_changes": [
                        {
                            "category": "schema",
                            "summary": "Schema changed.",
                            "consumer_action": "Update consumers.",
                            "affected_surfaces": [],
                        }
                    ],
                }
            )

    def test_normalize_publish_plan_rejects_malformed_compatibility_waiver(self):
        with self.assertRaisesRegex(reviewed_dataset_plan.PlanValidationError, "consumer_impact"):
            reviewed_dataset_plan.normalize_publish_plan(
                {
                    "asset_slug": "example-asset",
                    "proposal_id": "pr-123",
                    "promotions": [
                        {
                            "source_uri": (
                                f"gs://{BUCKET}/_scratch/pending-publishes/"
                                "example-asset/pr-123/example-asset.fgb"
                            ),
                            "source_generation": "123",
                            "destination_uri": (
                                f"gs://{BUCKET}/100-geographic-reference/130-protected-areas/"
                                "example-asset/latest/example-asset.fgb"
                            ),
                            "compatibility_waiver": {
                                "asset_slug": "example-asset",
                                "blocked_changes": [{"kind": "removed", "field": "retired"}],
                                "rationale": "Approved break.",
                                "reviewer": "jonaraphael",
                                "pr_reference": "https://github.com/SkyTruth/shared-datasets-1/pull/123",
                                "migration_path": "Pin the prior release.",
                            },
                        }
                    ],
                }
            )

    def test_normalize_publish_plan_rejects_source_outside_pending_publish_prefix(self):
        with self.assertRaisesRegex(reviewed_dataset_plan.PlanValidationError, "source_uri must start"):
            reviewed_dataset_plan.normalize_publish_plan(
                {
                    "asset_slug": "example-asset",
                    "proposal_id": "pr-123",
                    "promotions": [
                        {
                            "source_uri": f"gs://{BUCKET}/_scratch/other/example-asset.fgb",
                            "source_generation": "123",
                            "destination_uri": (
                                f"gs://{BUCKET}/100-geographic-reference/130-protected-areas/"
                                "example-asset/latest/example-asset.fgb"
                            ),
                        }
                    ],
                }
            )

    def test_normalize_publish_plan_requires_no_cache_for_pmtiles(self):
        with self.assertRaisesRegex(reviewed_dataset_plan.PlanValidationError, "cache_control"):
            reviewed_dataset_plan.normalize_publish_plan(
                {
                    "asset_slug": "example-asset",
                    "proposal_id": "pr-123",
                    "promotions": [
                        {
                            "source_uri": (
                                f"gs://{BUCKET}/_scratch/pending-publishes/"
                                "example-asset/pr-123/example-asset.pmtiles"
                            ),
                            "source_generation": "123",
                            "destination_uri": (
                                f"gs://{BUCKET}/100-geographic-reference/130-protected-areas/"
                                "example-asset/latest/example-asset.pmtiles"
                            ),
                        }
                    ],
                }
            )

    def test_normalize_publish_plan_accepts_pmtiles_no_cache_metadata(self):
        normalized = reviewed_dataset_plan.normalize_publish_plan(
            {
                "asset_slug": "example-asset",
                "proposal_id": "pr-123",
                "promotions": [
                    {
                        "source_uri": (
                            f"gs://{BUCKET}/_scratch/pending-publishes/"
                            "example-asset/pr-123/example-asset.pmtiles"
                        ),
                        "source_generation": "123",
                        "destination_uri": (
                            f"gs://{BUCKET}/100-geographic-reference/130-protected-areas/"
                            "example-asset/latest/example-asset.pmtiles"
                        ),
                        "content_type": "application/vnd.pmtiles",
                        "cache_control": reviewed_dataset_plan.NO_CACHE_CONTROL,
                    }
                ],
            }
        )

        self.assertEqual(normalized["promotions"][0]["cache_control"], reviewed_dataset_plan.NO_CACHE_CONTROL)

    def test_normalize_publish_plan_requires_no_cache_for_web_catalog(self):
        with self.assertRaisesRegex(reviewed_dataset_plan.PlanValidationError, "catalog.json"):
            reviewed_dataset_plan.normalize_publish_plan(
                {
                    "asset_slug": "catalog-web",
                    "proposal_id": "pr-123",
                    "promotions": [
                        {
                            "source_uri": (
                                f"gs://{BUCKET}/_scratch/pending-publishes/"
                                "catalog-web/pr-123/catalog.json"
                            ),
                            "source_generation": "123",
                            "destination_uri": f"gs://{BUCKET}/_catalog/web/catalog.json",
                            "content_type": "application/json",
                        }
                    ],
                }
            )

    def test_normalize_delete_plan_accepts_exact_canonical_object_generation(self):
        normalized = reviewed_dataset_plan.normalize_delete_plan(
            {
                "asset_slug": "example-asset",
                "proposal_id": "pr-123",
                "deletions": [
                    {
                        "uri": (
                            f"gs://{BUCKET}/100-geographic-reference/130-protected-areas/"
                            "example-asset/releases/2026-05-08/example-asset.fgb"
                        ),
                        "generation": 123,
                        "reason": "Incorrect duplicate release superseded by approved replacement.",
                    }
                ],
                "breaking_changes": [
                    {
                        "category": "lifecycle_delete",
                        "summary": "Removed an obsolete latest companion.",
                        "consumer_action": "Stop reading the deleted companion.",
                        "affected_surfaces": ["latest/example-asset.pmtiles"],
                    }
                ],
            }
        )

        deletion = normalized["deletions"][0]
        self.assertEqual(deletion["generation"], "123")
        self.assertIn("duplicate release", deletion["reason"])
        self.assertEqual(normalized["breaking_changes"][0]["category"], "lifecycle_delete")

    def test_normalize_delete_plan_accepts_exact_gcloud_composite_temp_object(self):
        normalized = reviewed_dataset_plan.normalize_delete_plan(
            {
                "asset_slug": "cleanup",
                "proposal_id": "pr-123",
                "deletions": [
                    {
                        "uri": (
                            f"gs://{BUCKET}/gcloud/tmp/parallel_composite_uploads/"
                            "see_gcloud_storage_cp_help_for_details/123_part"
                        ),
                        "generation": 123,
                        "reason": "Remove orphaned gcloud composite upload part created by an aborted scratch upload.",
                    }
                ],
            }
        )

        deletion = normalized["deletions"][0]
        self.assertEqual(deletion["generation"], "123")
        self.assertIn("orphaned gcloud composite", deletion["reason"])

    def test_normalize_delete_plan_rejects_prefix_delete(self):
        with self.assertRaisesRegex(reviewed_dataset_plan.PlanValidationError, "not a prefix"):
            reviewed_dataset_plan.normalize_delete_plan(
                {
                    "asset_slug": "example-asset",
                    "proposal_id": "pr-123",
                    "deletions": [
                        {
                            "uri": (
                                f"gs://{BUCKET}/100-geographic-reference/130-protected-areas/"
                                "example-asset/releases/2026-05-08/"
                            ),
                            "generation": "123",
                            "reason": "Remove bad release prefix after replacement.",
                        }
                    ],
                }
            )

    def test_normalize_delete_plan_rejects_wildcards(self):
        with self.assertRaisesRegex(reviewed_dataset_plan.PlanValidationError, "wildcard"):
            reviewed_dataset_plan.normalize_delete_plan(
                {
                    "asset_slug": "example-asset",
                    "proposal_id": "pr-123",
                    "deletions": [
                        {
                            "uri": (
                                f"gs://{BUCKET}/100-geographic-reference/130-protected-areas/"
                                "example-asset/releases/2026-05-08/*.fgb"
                            ),
                            "generation": "123",
                            "reason": "Remove bad release objects after replacement.",
                        }
                    ],
                }
            )

    def test_normalize_delete_plan_rejects_scratch_target(self):
        with self.assertRaisesRegex(reviewed_dataset_plan.PlanValidationError, "approved delete prefixes"):
            reviewed_dataset_plan.normalize_delete_plan(
                {
                    "asset_slug": "example-asset",
                    "proposal_id": "pr-123",
                    "deletions": [
                        {
                            "uri": f"gs://{BUCKET}/_scratch/pending-publishes/example-asset/pr-123/file.fgb",
                            "generation": "123",
                            "reason": "Remove bad scratch object through the wrong path.",
                        }
                    ],
                }
            )

    def test_normalize_delete_plan_rejects_nested_gcloud_temp_prefix(self):
        with self.assertRaisesRegex(reviewed_dataset_plan.PlanValidationError, "approved delete prefixes"):
            reviewed_dataset_plan.normalize_delete_plan(
                {
                    "asset_slug": "cleanup",
                    "proposal_id": "pr-123",
                    "deletions": [
                        {
                            "uri": (
                                f"gs://{BUCKET}/gcloud/tmp/parallel_composite_uploads/"
                                "see_gcloud_storage_cp_help_for_details/nested/part"
                            ),
                            "generation": "123",
                            "reason": "Reject nested gcloud temp prefixes that are too broad.",
                        }
                    ],
                }
            )

    def test_extract_delete_plan_from_event_file(self):
        path = event_path(
            """
```shared-datasets-delete-plan
{
  "asset_slug": "example-asset",
  "proposal_id": "pr-123",
  "deletions": [
    {
      "uri": "gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/example-asset/latest/example-asset.fgb",
      "generation": "123",
      "reason": "Remove wrong latest object after approved replacement."
    }
  ]
}
```
"""
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                code = reviewed_dataset_plan.main(["extract", "delete", "--event-path", str(path)])
        finally:
            path.unlink()

        self.assertEqual(code, 0)

    def test_extract_publish_plan_with_output_prints_compact_summary(self):
        path = event_path(
            """
```shared-datasets-publish-plan
{
  "asset_slug": "example-asset",
  "proposal_id": "pr-123",
  "promotions": [
    {
      "source_uri": "gs://skytruth-shared-datasets-1/_scratch/pending-publishes/example-asset/pr-123/example-asset.fgb",
      "source_generation": "123",
      "destination_uri": "gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/example-asset/latest/example-asset.fgb",
      "destination_generation": "456",
      "content_type": "application/octet-stream",
      "cache_control": ""
    }
  ]
}
```
"""
        )
        output = Path(tempfile.NamedTemporaryFile(suffix=".json", delete=False).name)
        try:
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = reviewed_dataset_plan.main(["extract", "publish", "--event-path", str(path), "--output", str(output)])
            printed = json.loads(stdout.getvalue())
            written = json.loads(output.read_text())
        finally:
            path.unlink()
            output.unlink(missing_ok=True)

        self.assertEqual(code, 0)
        self.assertEqual(printed["promotion_count"], 1)
        self.assertEqual(printed["replacement_count"], 1)
        self.assertNotIn("source_uri", stdout.getvalue())
        self.assertEqual(written["promotions"][0]["source_generation"], "123")

    def test_extract_publish_plan_print_plan_keeps_full_stdout(self):
        path = event_path(
            """
```shared-datasets-publish-plan
{
  "asset_slug": "example-asset",
  "proposal_id": "pr-123",
  "promotions": [
    {
      "source_uri": "gs://skytruth-shared-datasets-1/_scratch/pending-publishes/example-asset/pr-123/example-asset.fgb",
      "source_generation": "123",
      "destination_uri": "gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/example-asset/latest/example-asset.fgb"
    }
  ]
}
```
"""
        )
        output = Path(tempfile.NamedTemporaryFile(suffix=".json", delete=False).name)
        try:
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = reviewed_dataset_plan.main(
                    ["extract", "publish", "--event-path", str(path), "--output", str(output), "--print-plan"]
                )
        finally:
            path.unlink()
            output.unlink(missing_ok=True)

        self.assertEqual(code, 0)
        self.assertIn("source_uri", stdout.getvalue())

    def test_pr_api_payload_to_event_accepts_open_same_repo_default_branch_pr(self):
        event = reviewed_dataset_plan.pr_api_payload_to_event(
            {
                "state": "open",
                "merged": False,
                "body": "reviewed plan",
                "head": {"repo": {"full_name": "SkyTruth/shared-datasets-1"}},
                "base": {"repo": {"full_name": "SkyTruth/shared-datasets-1"}, "ref": "main"},
            },
            repository="SkyTruth/shared-datasets-1",
            default_branch="main",
        )

        self.assertEqual(event["pull_request"]["body"], "reviewed plan")

    def test_pr_api_payload_to_event_accepts_merged_pr_when_allowed(self):
        event = reviewed_dataset_plan.pr_api_payload_to_event(
            {
                "state": "closed",
                "merged": True,
                "body": "reviewed plan",
                "head": {"repo": {"full_name": "SkyTruth/shared-datasets-1"}},
                "base": {"repo": {"full_name": "SkyTruth/shared-datasets-1"}, "ref": "main"},
            },
            repository="SkyTruth/shared-datasets-1",
            default_branch="main",
            allow_merged=True,
        )

        self.assertEqual(event["pull_request"]["body"], "reviewed plan")

    def test_workflow_run_pr_resolution_uses_commit_associated_prs_when_event_list_is_empty(self):
        event = {
            "workflow_run": {
                "pull_requests": [],
                "head_sha": "7169d5c32889ddbaf258e9f829abaf0ca2fd1d83",
                "head_branch": "codex/wdpa-name-eng-mutation",
            }
        }
        commit_prs = [
            {
                "number": 88,
                "state": "closed",
                "merged_at": "2026-06-15T17:00:33Z",
                "head": {
                    "ref": "codex/wdpa-name-eng-mutation",
                    "sha": "7169d5c32889ddbaf258e9f829abaf0ca2fd1d83",
                    "repo": {"full_name": "SkyTruth/shared-datasets-1"},
                },
                "base": {
                    "ref": "main",
                    "repo": {"full_name": "SkyTruth/shared-datasets-1"},
                },
            }
        ]

        pr_number = reviewed_dataset_plan.resolve_workflow_run_pr_number(
            event,
            repository="SkyTruth/shared-datasets-1",
            default_branch="main",
            commit_prs=commit_prs,
        )

        self.assertEqual(pr_number, "88")

    def test_workflow_run_pr_resolution_falls_back_to_head_branch_candidates(self):
        event = {
            "workflow_run": {
                "pull_requests": [],
                "head_sha": "7169d5c32889ddbaf258e9f829abaf0ca2fd1d83",
                "head_branch": "codex/wdpa-name-eng-mutation",
            }
        }
        branch_prs = [
            {
                "number": 88,
                "baseRefName": "main",
                "headRefName": "codex/wdpa-name-eng-mutation",
                "headRefOid": "7169d5c32889ddbaf258e9f829abaf0ca2fd1d83",
                "headRepository": {"nameWithOwner": "SkyTruth/shared-datasets-1"},
                "mergeCommit": {"oid": "d59b5bce28998d3e1f0003c27fa327884cbead37"},
                "mergedAt": "2026-06-15T17:00:33Z",
                "state": "MERGED",
            }
        ]

        pr_number = reviewed_dataset_plan.resolve_workflow_run_pr_number(
            event,
            repository="SkyTruth/shared-datasets-1",
            default_branch="main",
            branch_prs=branch_prs,
        )

        self.assertEqual(pr_number, "88")

    def test_workflow_run_pr_resolution_keeps_non_translation_runs_noop_when_no_pr_matches(self):
        event = {
            "workflow_run": {
                "pull_requests": [],
                "head_sha": "abc123",
                "head_branch": "docs-only",
            }
        }
        branch_prs = [
            {
                "number": 77,
                "baseRefName": "other-branch",
                "headRefName": "docs-only",
                "headRefOid": "abc123",
                "headRepository": {"nameWithOwner": "SkyTruth/shared-datasets-1"},
                "state": "MERGED",
            }
        ]

        pr_number = reviewed_dataset_plan.resolve_workflow_run_pr_number(
            event,
            repository="SkyTruth/shared-datasets-1",
            default_branch="main",
            branch_prs=branch_prs,
        )

        self.assertIsNone(pr_number)

    def test_pr_api_payload_to_event_rejects_closed_unmerged_pr_when_allowing_merged(self):
        with self.assertRaisesRegex(reviewed_dataset_plan.PlanValidationError, "open or merged"):
            reviewed_dataset_plan.pr_api_payload_to_event(
                {
                    "state": "closed",
                    "merged": False,
                    "head": {"repo": {"full_name": "SkyTruth/shared-datasets-1"}},
                    "base": {"repo": {"full_name": "SkyTruth/shared-datasets-1"}, "ref": "main"},
                },
                repository="SkyTruth/shared-datasets-1",
                default_branch="main",
                allow_merged=True,
            )

    def test_event_from_pr_cli_allows_merged_pr_with_flag(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        with tmp:
            json.dump(
                {
                    "state": "closed",
                    "merged": True,
                    "body": "reviewed plan",
                    "head": {"repo": {"full_name": "SkyTruth/shared-datasets-1"}},
                    "base": {"repo": {"full_name": "SkyTruth/shared-datasets-1"}, "ref": "main"},
                },
                tmp,
            )
        path = Path(tmp.name)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                code = reviewed_dataset_plan.main(
                    [
                        "event-from-pr",
                        "--pr-json",
                        str(path),
                        "--repository",
                        "SkyTruth/shared-datasets-1",
                        "--default-branch",
                        "main",
                        "--allow-merged",
                    ]
                )
        finally:
            path.unlink()

        self.assertEqual(code, 0)

    def test_pr_api_payload_to_event_rejects_fork_pr(self):
        with self.assertRaisesRegex(reviewed_dataset_plan.PlanValidationError, "head repository"):
            reviewed_dataset_plan.pr_api_payload_to_event(
                {
                    "state": "open",
                    "head": {"repo": {"full_name": "other/shared-datasets-1"}},
                    "base": {"repo": {"full_name": "SkyTruth/shared-datasets-1"}, "ref": "main"},
                },
                repository="SkyTruth/shared-datasets-1",
                default_branch="main",
            )


if __name__ == "__main__":
    unittest.main()
