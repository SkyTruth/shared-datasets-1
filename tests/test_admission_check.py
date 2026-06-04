from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import admission_check


FULL_ADMISSION = """---
schema_version: 1
asset_slug: example-asset
title: Example Asset
category: 100-geographic-reference
subcategory: 110-boundaries
status: active
access_tier: public
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/example-asset.fgb
available_formats:
- fgb
metadata_paths:
- README.md
source: Example source
license: Example license
citation: Example citation
notes: Example notes
admission:
  intended_consumers:
  - Monitor
  shared_rationale: Shared reference layer for repeatable analysis.
  steward: SkyTruth data team
  update_expectations: Manual refresh when the source publishes a material update.
  estimated_published_size_gb: 1.5
  large_data_exception: ""
  alternatives_considered: Project bucket and direct upstream access.
  deprecation_policy: Keep existing releases and point users to any successor asset.
---

# Example Asset
"""


def write_doc(root: Path, text: str = FULL_ADMISSION, slug: str = "example-asset") -> str:
    path = root / "docs/assets" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return f"docs/assets/{slug}.md"


def check(root: Path, changes, exists_at_base=None):
    return admission_check.check_admission(
        repo_root=root,
        changes=changes,
        base_ref="base",
        path_exists_at_base=exists_at_base or (lambda _path: False),
    )


class AdmissionCheckTests(unittest.TestCase):
    def test_added_asset_doc_with_full_admission_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_doc(root)

            result = check(root, [admission_check.ChangedFile("A", path)])

        self.assertEqual(result.errors, ())

    def test_missing_citation_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_doc(root, FULL_ADMISSION.replace("citation: Example citation\n", "citation: TBD\n"))

            result = check(root, [admission_check.ChangedFile("A", path)])

        self.assertIn("missing citation", "\n".join(result.errors))

    def test_missing_steward_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_doc(root, FULL_ADMISSION.replace("  steward: SkyTruth data team\n", "  steward: TODO\n"))

            result = check(root, [admission_check.ChangedFile("A", path)])

        self.assertIn("missing admission.steward", "\n".join(result.errors))

    def test_nonnumeric_footprint_estimate_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_doc(
                root,
                FULL_ADMISSION.replace("  estimated_published_size_gb: 1.5\n", "  estimated_published_size_gb: unknown\n"),
            )

            result = check(root, [admission_check.ChangedFile("A", path)])

        self.assertIn("missing numeric admission.estimated_published_size_gb", "\n".join(result.errors))

    def test_small_footprint_allows_blank_large_data_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_doc(root)

            result = check(root, [admission_check.ChangedFile("A", path)])

        self.assertEqual(result.errors, ())

    def test_large_footprint_requires_large_data_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_doc(root, FULL_ADMISSION.replace("  estimated_published_size_gb: 1.5\n", "  estimated_published_size_gb: 10\n"))

            result = check(root, [admission_check.ChangedFile("A", path)])

        self.assertIn("missing admission.large_data_exception", "\n".join(result.errors))

    def test_new_ingestion_pipeline_without_asset_doc_admission_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            result = check(
                root,
                [admission_check.ChangedFile("A", "ingestion/example_monthly/run.py")],
            )

        self.assertIn("new ingestion pipeline", "\n".join(result.errors))
        self.assertIn("asset-doc frontmatter", "\n".join(result.errors))

    def test_new_ingestion_pipeline_with_complete_asset_doc_admission_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_doc(root)

            result = check(
                root,
                [
                    admission_check.ChangedFile("A", "ingestion/example_monthly/run.py"),
                    admission_check.ChangedFile("M", path),
                ],
            )

        self.assertEqual(result.errors, ())

    def test_existing_asset_docs_without_admission_are_not_checked_when_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_doc(root, FULL_ADMISSION.replace("admission:\n", "legacy: true\n# admission removed:\n"))

            result = check(root, [])

        self.assertEqual(result.errors, ())

if __name__ == "__main__":
    unittest.main()
