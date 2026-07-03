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

    def test_added_asset_doc_without_citation_is_not_admission_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_doc(root, FULL_ADMISSION.replace("citation: Example citation\n", "citation: TBD\n"))

            result = check(root, [admission_check.ChangedFile("A", path)])

        self.assertEqual(result.errors, ())

    def test_added_asset_doc_without_admission_is_not_admission_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_doc(root, FULL_ADMISSION.replace("admission:\n", "legacy: true\n# admission removed:\n"))

            result = check(root, [admission_check.ChangedFile("A", path)])

        self.assertEqual(result.errors, ())

    def test_added_asset_doc_with_nonnumeric_footprint_is_not_admission_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_doc(
                root,
                FULL_ADMISSION.replace("  estimated_published_size_gb: 1.5\n", "  estimated_published_size_gb: unknown\n"),
            )

            result = check(root, [admission_check.ChangedFile("A", path)])

        self.assertEqual(result.errors, ())

    def test_small_footprint_allows_blank_large_data_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_doc(root)

            result = check(root, [admission_check.ChangedFile("A", path)])

        self.assertEqual(result.errors, ())

    def test_added_asset_doc_with_large_footprint_is_not_admission_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_doc(root, FULL_ADMISSION.replace("  estimated_published_size_gb: 1.5\n", "  estimated_published_size_gb: 10\n"))

            result = check(root, [admission_check.ChangedFile("A", path)])

        self.assertEqual(result.errors, ())

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

class ParseFootprintGbTests(unittest.TestCase):
    def test_numeric_values_are_gb(self):
        self.assertEqual(admission_check.parse_footprint_gb(0.17), 0.17)
        self.assertEqual(admission_check.parse_footprint_gb(3), 3.0)
        self.assertEqual(admission_check.parse_footprint_gb("0.5"), 0.5)

    def test_unit_suffixes_are_converted_not_read_as_gb(self):
        self.assertAlmostEqual(admission_check.parse_footprint_gb("Roughly 90 MB of artifacts"), 0.09)
        self.assertAlmostEqual(admission_check.parse_footprint_gb("Below 50 MB"), 0.05)
        self.assertAlmostEqual(admission_check.parse_footprint_gb("Roughly 3.2 GB"), 3.2)
        self.assertAlmostEqual(admission_check.parse_footprint_gb("2 GB of FGB plus 500 MB of tiles"), 2.5)

    def test_missing_or_unparseable_values_are_none(self):
        self.assertIsNone(admission_check.parse_footprint_gb("unknown"))
        self.assertIsNone(admission_check.parse_footprint_gb(""))
        self.assertIsNone(admission_check.parse_footprint_gb("small"))
        self.assertIsNone(admission_check.parse_footprint_gb(-1))

    def test_comma_grouped_values_keep_their_full_magnitude(self):
        self.assertAlmostEqual(admission_check.parse_footprint_gb("10,240 MB"), 10.24)
        self.assertEqual(admission_check.parse_footprint_gb("10,240"), 10240.0)

    def test_garbled_numbers_are_rejected_not_misread(self):
        self.assertIsNone(admission_check.parse_footprint_gb("1.5.2 GB"))

    def test_comma_grouped_footprint_still_requires_large_data_exception(self):
        evidence = {
            "citation": "c",
            "intended_consumers": ["x"],
            "shared_rationale": "r",
            "steward": "s",
            "update_expectations": "u",
            "alternatives_considered": "a",
            "deprecation_policy": "d",
            "estimated_published_size_gb": "10,240 MB",
            "large_data_exception": None,
        }
        errors = admission_check.validate_admission_evidence(evidence, label="doc")
        self.assertEqual(errors, ["doc: missing admission.large_data_exception for footprint >= 10 GB"])


if __name__ == "__main__":
    unittest.main()
