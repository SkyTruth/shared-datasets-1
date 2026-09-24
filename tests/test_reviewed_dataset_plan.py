from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from scripts import reviewed_dataset_plan


BUCKET = "skytruth-shared-datasets-1"


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

    def test_normalize_publish_plan_accepts_empty_release_index_asset_slugs(self):
        normalized = reviewed_dataset_plan.normalize_publish_plan(
            {
                "asset_slug": "example-asset",
                "proposal_id": "pr-123",
                "release_index_asset_slugs": [],
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

        self.assertEqual(normalized["release_index_asset_slugs"], [])

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

    def test_canonical_document_roundtrip_and_ambiguity_refusals(self):
        from test_dataset_mutation_authorization import document
        from copy import deepcopy

        doc = document()
        raw = reviewed_dataset_plan.canonical_bytes(doc)
        path = reviewed_dataset_plan.document_path(doc)
        self.assertEqual(reviewed_dataset_plan.read_document(raw, path=path), doc)
        for bad_raw, bad_path in (
            (raw + b" ", path),
            (raw, path.replace(".json", "0.json")),
            (b'{"plan_version":1,"plan_version":1}', path),
            (b'{"value":NaN}', path),
        ):
            with (
                self.subTest(raw=bad_raw),
                self.assertRaises(reviewed_dataset_plan.PlanValidationError),
            ):
                reviewed_dataset_plan.read_document(bad_raw, path=bad_path)
        for mutate in (
            lambda d: d["publish"]["promotions"].append(
                deepcopy(d["publish"]["promotions"][0])
            ),
            lambda d: d["publish"]["promotions"][0].update(source_generation=True),
            lambda d: d["publish"]["promotions"][0].update(source_generation="0"),
            lambda d: d["publish"]["promotions"][0].update(extra="hidden"),
            lambda d: d.update(plan_version=True),
            lambda d: d.update(finalization_version="arbitrary-command"),
            lambda d: d["publish"].update(unknown=True),
        ):
            bad = deepcopy(doc)
            mutate(bad)
            with (
                self.subTest(mutate=mutate),
                self.assertRaises(reviewed_dataset_plan.PlanValidationError),
            ):
                reviewed_dataset_plan.normalize_document(bad)

    def test_combined_disjoint_plan_and_conflicting_targets(self):
        from test_dataset_mutation_authorization import document

        doc = document()
        target = doc["publish"]["promotions"][0]["destination_uri"]
        doc["delete"] = {
            "asset_slug": "demo",
            "proposal_id": "pr-7",
            "deletions": [
                {
                    "uri": target + ".old",
                    "generation": "9",
                    "reason": "Remove explicitly obsolete data",
                }
            ],
        }
        reviewed_dataset_plan.normalize_document(doc)
        doc["delete"]["deletions"][0]["uri"] = target
        with self.assertRaisesRegex(
            reviewed_dataset_plan.PlanValidationError, "conflict"
        ):
            reviewed_dataset_plan.normalize_document(doc)

    def test_prepare_cli_creates_identical_file_and_fence_without_overwriting_tampering(
        self,
    ):
        from test_dataset_mutation_authorization import document
        from scripts import reviewed_dataset_plan as p

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = root / "publish.json"
            payload.write_bytes(p.canonical_bytes(document()["publish"]))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(
                    p.main(
                        ["prepare", "--publish", str(payload), "--repo-root", str(root)]
                    ),
                    0,
                )
            path = root / p.document_path(document())
            self.assertEqual(path.read_bytes(), p.canonical_bytes(document()))
            p.check_rendered_body(stdout.getvalue(), document())
            path.write_text("tampered")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(
                    p.main(
                        ["prepare", "--publish", str(payload), "--repo-root", str(root)]
                    ),
                    2,
                )

    def test_producer_rejects_dot_proposals_and_escaping_symlink_before_mkdir(self):
        from test_dataset_mutation_authorization import document
        from scripts import reviewed_dataset_plan as p

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / ".github").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(p.PlanValidationError, "escapes"):
                p.write_document(document(), repo_root=root)
            self.assertEqual(list(outside.iterdir()), [])
        for proposal in (".", ".."):
            for kind in ("publish", "delete"):
                doc = document()
                if kind == "delete":
                    target = doc.pop("publish")["promotions"][0]["destination_uri"]
                    doc["delete"] = {
                        "asset_slug": "demo",
                        "proposal_id": proposal,
                        "deletions": [
                            {
                                "uri": target,
                                "generation": "1",
                                "reason": "Reviewed obsolete object deletion",
                            }
                        ],
                    }
                else:
                    doc["publish"]["proposal_id"] = proposal
                with (
                    self.subTest(proposal=proposal, kind=kind),
                    self.assertRaisesRegex(p.PlanValidationError, "proposal_id"),
                ):
                    p.normalize_document(doc)


if __name__ == "__main__":
    unittest.main()
