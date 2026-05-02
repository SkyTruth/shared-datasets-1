from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


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

    def test_legacy_sentinel_footprints_prefix_is_not_required_as_catalog_asset(self):
        categories = {"200-imagery-derived": {"210-satellite-indexes"}}
        current_root = "200-imagery-derived/210-satellite-indexes/cerulean-s1-envelope"
        legacy_root = "200-imagery-derived/210-satellite-indexes/sentinel-1-footprints"
        row = {
            "asset_slug": "cerulean-s1-envelope",
            "category": "200-imagery-derived",
            "subcategory": "210-satellite-indexes",
            "canonical_path": f"gs://skytruth-shared-datasets-1/{current_root}/latest/cerulean-s1-envelope.fgb",
            "canonical_format": "fgb",
            "available_formats": "fgb;pmtiles",
            "metadata_paths": "README.md",
        }

        findings = audit.validate_asset_roots(
            "skytruth-shared-datasets-1",
            [
                blob_info(f"{current_root}/README.md"),
                blob_info(f"{current_root}/latest/cerulean-s1-envelope.fgb"),
                blob_info(f"{legacy_root}/latest/sentinel-1-footprints.fgb"),
            ],
            categories,
            [row],
            {"cerulean-s1-envelope": row},
            skip_readme_content=True,
            prefix="",
        )

        self.assertNotIn("catalog-row", {finding.check for finding in findings})
        self.assertNotIn(legacy_root, {finding.path for finding in findings})

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

    def test_release_integrity_flags_missing_index_for_versioned_asset(self):
        row = catalog_row(update_cadence="monthly")
        release_blob = blob_info(
            "100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.fgb"
        )

        findings = audit.validate_release_integrity(
            bucket="skytruth-shared-datasets-1",
            blobs=[release_blob],
            catalog_rows=[row],
            mode="warn",
            today=dt.date(2026, 5, 2),
        )

        self.assertEqual([finding.check for finding in findings], ["release-index-exists"])
        self.assertEqual(findings[0].severity, "WARN")
        self.assertFalse(audit.finding_blocks_exit(findings[0], release_integrity_mode="warn"))

    def test_release_integrity_flags_missing_release_object(self):
        row = catalog_row(update_cadence="manual")
        index_name = "_catalog/releases/example-asset.json"
        run_name = "100-geographic-reference/110-boundaries/example-asset/runs/2026-05-01.json"
        payloads = {
            index_name: {
                "asset_slug": "example-asset",
                "latest_release": {"date": "2026-05-01"},
                "latest_run": {"date": "2026-05-01", "status": "success"},
                "releases": [
                    {
                        "date": "2026-05-01",
                        "run_record_path": f"gs://skytruth-shared-datasets-1/{run_name}",
                        "files": [
                            {
                                "format": "fgb",
                                "path": "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.fgb",
                            }
                        ],
                    }
                ],
            },
            run_name: {"run_date": "2026-05-01", "status": "success"},
        }

        with mock.patch.object(audit, "download_object_text", side_effect=download_from(payloads)):
            findings = audit.validate_release_integrity(
                bucket="skytruth-shared-datasets-1",
                blobs=[blob_info(index_name), blob_info(run_name)],
                catalog_rows=[row],
                mode="enforce",
                today=dt.date(2026, 5, 2),
            )

        self.assertIn("release-index-file-exists", {finding.check for finding in findings})
        self.assertTrue(all(finding.severity == "ERROR" for finding in findings))

    def test_release_integrity_flags_missing_indexed_run_record(self):
        row = catalog_row(update_cadence="manual")
        index_name = "_catalog/releases/example-asset.json"
        release_name = "100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.fgb"
        payloads = {
            index_name: {
                "asset_slug": "example-asset",
                "latest_release": {"date": "2026-05-01"},
                "releases": [
                    {
                        "date": "2026-05-01",
                        "run_record_path": "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/example-asset/runs/2026-05-01.json",
                        "files": [{"format": "fgb", "path": f"gs://skytruth-shared-datasets-1/{release_name}"}],
                    }
                ],
            }
        }

        with mock.patch.object(audit, "download_object_text", side_effect=download_from(payloads)):
            findings = audit.validate_release_integrity(
                bucket="skytruth-shared-datasets-1",
                blobs=[blob_info(index_name), blob_info(release_name)],
                catalog_rows=[row],
                mode="warn",
                today=dt.date(2026, 5, 2),
            )

        self.assertIn("release-index-run-record-exists", {finding.check for finding in findings})

    def test_release_integrity_allows_manual_release_without_run_record(self):
        row = catalog_row(update_cadence="manual")
        index_name = "_catalog/releases/example-asset.json"
        release_name = "100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.fgb"
        payloads = {
            index_name: {
                "asset_slug": "example-asset",
                "latest_release": {"date": "2026-05-01"},
                "releases": [
                    {
                        "date": "2026-05-01",
                        "files": [{"format": "fgb", "path": f"gs://skytruth-shared-datasets-1/{release_name}"}],
                    }
                ],
            }
        }

        with mock.patch.object(audit, "download_object_text", side_effect=download_from(payloads)):
            findings = audit.validate_release_integrity(
                bucket="skytruth-shared-datasets-1",
                blobs=[blob_info(index_name), blob_info(release_name)],
                catalog_rows=[row],
                mode="warn",
                today=dt.date(2026, 5, 2),
            )

        self.assertNotIn("release-index-run-record-exists", {finding.check for finding in findings})

    def test_release_integrity_flags_stale_scheduled_latest_run(self):
        row = catalog_row(update_cadence="daily")
        index_name = "_catalog/releases/example-asset.json"
        payloads = {
            index_name: {
                "asset_slug": "example-asset",
                "latest_release": None,
                "latest_run": {"date": "2026-04-20", "status": "skipped"},
                "releases": [],
            }
        }

        with mock.patch.object(audit, "download_object_text", side_effect=download_from(payloads)):
            findings = audit.validate_release_integrity(
                bucket="skytruth-shared-datasets-1",
                blobs=[blob_info(index_name)],
                catalog_rows=[row],
                mode="warn",
                today=dt.date(2026, 5, 2),
            )

        self.assertIn("release-index-latest-run-fresh", {finding.check for finding in findings})

    def test_release_integrity_recent_source_unchanged_skip_is_not_stale(self):
        row = catalog_row(update_cadence="monthly")
        index_name = "_catalog/releases/example-asset.json"
        release_name = "100-geographic-reference/110-boundaries/example-asset/releases/2026-02-01/example-asset.fgb"
        run_name = "100-geographic-reference/110-boundaries/example-asset/runs/2026-02-01.json"
        payloads = {
            index_name: {
                "asset_slug": "example-asset",
                "latest_release": {"date": "2026-02-01"},
                "latest_run": {"date": "2026-05-01", "status": "skipped", "reason": "source fingerprint unchanged"},
                "releases": [
                    {
                        "date": "2026-02-01",
                        "run_record_path": f"gs://skytruth-shared-datasets-1/{run_name}",
                        "files": [{"format": "fgb", "path": f"gs://skytruth-shared-datasets-1/{release_name}"}],
                    }
                ],
            },
            run_name: {"run_date": "2026-02-01", "status": "success"},
        }

        with mock.patch.object(audit, "download_object_text", side_effect=download_from(payloads)):
            findings = audit.validate_release_integrity(
                bucket="skytruth-shared-datasets-1",
                blobs=[blob_info(index_name), blob_info(release_name), blob_info(run_name)],
                catalog_rows=[row],
                mode="warn",
                today=dt.date(2026, 5, 2),
            )

        self.assertEqual(findings, [])


def blob_info(name: str) -> audit.BlobInfo:
    return audit.BlobInfo(
        name=name,
        size=1,
        generation="1",
        updated="2026-05-01T00:00:00+00:00",
        content_type="application/json" if name.endswith(".json") else "application/octet-stream",
        metadata={},
    )


def catalog_row(*, update_cadence: str) -> dict[str, str]:
    return {
        "asset_slug": "example-asset",
        "category": "100-geographic-reference",
        "subcategory": "110-boundaries",
        "status": "active",
        "update_cadence": update_cadence,
        "canonical_path": "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb",
        "canonical_format": "fgb",
        "available_formats": "fgb",
        "metadata_paths": "README.md",
    }


def download_from(payloads: dict[str, object]):
    def _download(_bucket: str, blob_name: str, _generation: str) -> str:
        return json.dumps(payloads[blob_name])

    return _download


if __name__ == "__main__":
    unittest.main()
