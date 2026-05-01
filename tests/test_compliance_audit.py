from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = REPO_ROOT / ".claude/skills/shared-datasets-compliance-audit/scripts/audit_shared_datasets.py"
SPEC = importlib.util.spec_from_file_location("audit_shared_datasets_local", AUDIT_PATH)
audit = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


class LocalComplianceAuditTests(unittest.TestCase):
    def test_root_readme_is_ignored_as_intentional_bucket_landing_doc(self):
        blob = audit.BlobInfo(
            name="README.md",
            size=1,
            generation="1",
            updated="2026-05-01T00:00:00+00:00",
            content_type="text/markdown",
            metadata={},
        )

        findings = audit.validate_asset_roots(
            "skytruth-shared-datasets-1",
            [blob],
            {"100-geographic-reference": {"110-boundaries"}},
            [],
            {},
            skip_readme_content=True,
            prefix="",
        )

        self.assertEqual(findings, [])

    def test_local_catalog_validation_accepts_current_catalog(self):
        categories = audit.load_categories(REPO_ROOT / "catalog/categories.yaml")
        rows, _ = audit.load_catalog(REPO_ROOT / "catalog/shared-datasets-catalog.csv")

        findings = audit.validate_local_catalog(
            bucket="skytruth-shared-datasets-1",
            categories=categories,
            catalog_rows=rows,
        )

        self.assertEqual([finding.message for finding in findings], [])

    def test_local_catalog_validation_flags_bad_taxonomy_and_format(self):
        categories = {"100-geographic-reference": {"110-boundaries"}}
        rows = [
            {
                "asset_slug": "Bad_Slug",
                "category": "100-geographic-reference",
                "subcategory": "999-wrong",
                "canonical_path": "gs://skytruth-shared-datasets-1/100-geographic-reference/999-wrong/Bad_Slug/file.bin",
                "canonical_format": "bin",
                "available_formats": "fgb",
                "metadata_paths": "",
            }
        ]

        checks = {
            finding.check
            for finding in audit.validate_local_catalog(
                bucket="skytruth-shared-datasets-1",
                categories=categories,
                catalog_rows=rows,
            )
        }

        self.assertIn("catalog-slug", checks)
        self.assertIn("catalog-subcategory", checks)
        self.assertIn("catalog-format", checks)
        self.assertIn("catalog-available-formats", checks)


if __name__ == "__main__":
    unittest.main()
