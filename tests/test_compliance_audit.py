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

    def test_release_metadata_bundle_files_are_approved_under_latest_and_releases(self):
        root = "100-geographic-reference/110-boundaries/example-asset"
        row = catalog_row(update_cadence="manual")
        suffixes = (
            ".metadata.ndjson.gz",
            ".metadata.es.ndjson.gz",
            ".metadata.pt_br.ndjson.gz",
            ".schema.json",
            ".manifest.json",
        )

        findings = []
        for suffix in suffixes:
            findings.extend(audit.validate_object_layout(root, blob_info(f"{root}/latest/example-asset{suffix}"), row))
            findings.extend(
                audit.validate_object_layout(
                    root,
                    blob_info(f"{root}/releases/2026-05-01/example-asset{suffix}"),
                    row,
                )
            )

        self.assertNotIn("approved-format", {finding.check for finding in findings})

    def test_legacy_feature_sidecars_are_rejected_under_latest_and_releases(self):
        root = "100-geographic-reference/110-boundaries/example-asset"
        row = catalog_row(update_cadence="manual")

        findings = []
        for suffix in (".features.ndjson.gz", ".metadata.pt-BR.ndjson.gz"):
            findings.extend(audit.validate_object_layout(root, blob_info(f"{root}/latest/example-asset{suffix}"), row))
            findings.extend(
                audit.validate_object_layout(
                    root,
                    blob_info(f"{root}/releases/2026-05-01/example-asset{suffix}"),
                    row,
                )
            )

        self.assertIn("approved-format", {finding.check for finding in findings})

    def test_readme_validation_flags_generic_properties_placeholder(self):
        readme = blob_info("100-geographic-reference/110-boundaries/example-asset/README.md")
        text = """# Example Asset

**Status:** active
**Owner:** SkyTruth
**Update cadence:** manual
**Canonical file:** `latest/example-asset.fgb`
**Source:** Example source
**License / terms:** Example terms

## What this is

Example.

## Files

| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/example-asset.fgb` | `fgb` | `canonical` | Example file |

## Schema notes

Source fields are preserved.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| Source fields | varies | Source fields are preserved from the upstream layer. |

## Update notes

Initial upload.
"""

        findings = audit.validate_readme("100-geographic-reference/110-boundaries/example-asset", readme, text)

        self.assertIn("readme-properties-placeholder", {finding.check for finding in findings})

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

    def test_production_profile_makes_release_integrity_findings_blocking(self):
        row = catalog_row(update_cadence="monthly")
        release_blob = blob_info(
            "100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.fgb"
        )

        findings = audit.validate_release_integrity(
            bucket="skytruth-shared-datasets-1",
            blobs=[release_blob],
            catalog_rows=[row],
            mode="enforce",
            today=dt.date(2026, 5, 2),
        )

        self.assertEqual([finding.check for finding in findings], ["release-index-exists"])
        self.assertEqual(findings[0].severity, "ERROR")
        self.assertTrue(
            audit.finding_blocks_exit(
                findings[0],
                release_integrity_mode="enforce",
                health_profile="production",
            )
        )

    def test_production_profile_keeps_warnings_nonblocking(self):
        finding = audit.Finding("WARN", "example", "release-index-rows", "missing rows")

        self.assertFalse(
            audit.finding_blocks_exit(
                finding,
                release_integrity_mode="enforce",
                health_profile="production",
            )
        )

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

        missing_file = next(finding for finding in findings if finding.check == "release-index-file-exists")
        self.assertEqual(missing_file.severity, "ERROR")

    def test_release_integrity_flags_release_missing_canonical_file(self):
        row = catalog_row(update_cadence="manual")
        index_name = "_catalog/releases/example-asset.json"
        pmtiles_name = "100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.pmtiles"
        payloads = {
            index_name: {
                "asset_slug": "example-asset",
                "latest_release": {"date": "2026-05-01"},
                "releases": [
                    {
                        "date": "2026-05-01",
                        "files": [
                            {
                                "format": "pmtiles",
                                "path": f"gs://skytruth-shared-datasets-1/{pmtiles_name}",
                                "sha256": "a" * 64,
                            }
                        ],
                    }
                ],
            }
        }

        with mock.patch.object(audit, "download_object_text", side_effect=download_from(payloads)):
            findings = audit.validate_release_integrity(
                bucket="skytruth-shared-datasets-1",
                blobs=[blob_info(index_name), blob_info(pmtiles_name)],
                catalog_rows=[row],
                mode="enforce",
                today=dt.date(2026, 5, 2),
            )

        self.assertIn("release-index-canonical-file", {finding.check for finding in findings})
        canonical = next(finding for finding in findings if finding.check == "release-index-canonical-file")
        self.assertEqual(canonical.severity, "ERROR")
        self.assertIn("catalog canonical format file", canonical.codex_prompt)

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
                        "rows": 2,
                        "run_record_path": f"gs://skytruth-shared-datasets-1/{run_name}",
                        "files": [
                            {
                                "format": "fgb",
                                "path": f"gs://skytruth-shared-datasets-1/{release_name}",
                                "sha256": "a" * 64,
                            }
                        ],
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

    def test_feature_metadata_readiness_flags_missing_latest_bundle(self):
        row = catalog_row(update_cadence="manual")

        findings = audit.validate_feature_metadata_readiness(
            bucket="skytruth-shared-datasets-1",
            blobs=[],
            catalog_rows=[row],
            feature_metadata_docs={"example-asset": {"storage": "metadata_sidecar_v1"}},
        )

        self.assertEqual([finding.check for finding in findings], ["feature-metadata-contract-ready"])
        self.assertEqual(findings[0].severity, "ERROR")
        self.assertIn("missing latest metadata object", findings[0].message)
        self.assertIn("docs/assets/example-asset.md advertises feature_metadata", findings[0].codex_prompt)

    def test_feature_metadata_readiness_flags_missing_successful_index_load(self):
        row = catalog_row(update_cadence="manual")
        root = "100-geographic-reference/110-boundaries/example-asset"
        release = "2026-05-01"
        metadata_uri = f"gs://skytruth-shared-datasets-1/{root}/releases/{release}/example-asset.metadata.ndjson.gz"
        schema_uri = f"gs://skytruth-shared-datasets-1/{root}/releases/{release}/example-asset.schema.json"
        manifest_uri = f"gs://skytruth-shared-datasets-1/{root}/releases/{release}/example-asset.manifest.json"
        schema = {
            "schema_version": 1,
            "asset_slug": "example-asset",
            "release": release,
            "fields": [{"name": "name", "type": "String", "nullable": True, "projectable": True}],
        }
        manifest = {
            "schema_version": 1,
            "asset_slug": "example-asset",
            "release": release,
            "release_feature_model_schema_version": 1,
            "source_inputs": [],
            "artifacts": [
                {
                    "role": "fgb",
                    "path": f"gs://skytruth-shared-datasets-1/{root}/releases/{release}/example-asset.fgb",
                    "sha256": "a" * 64,
                    "generation": 10,
                },
                {
                    "role": "pmtiles",
                    "path": f"gs://skytruth-shared-datasets-1/{root}/releases/{release}/example-asset.pmtiles",
                    "sha256": "b" * 64,
                    "generation": 11,
                },
                {"role": "metadata", "path": metadata_uri, "sha256": "c" * 64, "generation": 12},
                {"role": "schema", "path": schema_uri, "sha256": "d" * 64, "generation": 13},
                {"role": "manifest", "path": manifest_uri},
            ],
            "schema": schema,
            "id_strategy": {},
            "feature_hash_algorithm": audit.release_feature_model.FEATURE_HASH_ALGORITHM,
            "validation": {},
            "index_status_policy": {"mode": "external_index_load_records", "path": f"index-loads/{release}/"},
        }
        release_index = {
            "asset_slug": "example-asset",
            "latest_release": {
                "date": release,
                "files": [
                    {"format": "metadata", "path": metadata_uri, "generation": 12},
                    {"format": "schema", "path": schema_uri, "generation": 13},
                    {"format": "manifest", "path": manifest_uri, "generation": 14},
                ],
            },
            "releases": [],
        }
        payloads = {
            "_catalog/releases/example-asset.json": release_index,
            f"{root}/releases/{release}/example-asset.schema.json": schema,
            f"{root}/releases/{release}/example-asset.manifest.json": manifest,
        }
        blobs = [
            blob_info("_catalog/releases/example-asset.json"),
            blob_info(f"{root}/latest/example-asset.metadata.ndjson.gz"),
            blob_info(f"{root}/latest/example-asset.schema.json"),
            blob_info(f"{root}/latest/example-asset.manifest.json"),
            blob_info(f"{root}/releases/{release}/example-asset.metadata.ndjson.gz", generation="12"),
            blob_info(f"{root}/releases/{release}/example-asset.schema.json", generation="13"),
            blob_info(f"{root}/releases/{release}/example-asset.manifest.json", generation="14"),
        ]

        with mock.patch.object(audit, "download_object_text", side_effect=download_from(payloads)):
            findings = audit.validate_feature_metadata_readiness(
                bucket="skytruth-shared-datasets-1",
                blobs=blobs,
                catalog_rows=[row],
                feature_metadata_docs={"example-asset": {"storage": "metadata_sidecar_v1"}},
            )

        self.assertEqual([finding.check for finding in findings], ["feature-metadata-contract-ready"])
        self.assertIn("no successful matching index-load record", findings[0].message)

    def test_assets_without_feature_metadata_do_not_require_sidecars(self):
        row = catalog_row(update_cadence="manual")

        findings = audit.validate_feature_metadata_readiness(
            bucket="skytruth-shared-datasets-1",
            blobs=[],
            catalog_rows=[row],
            feature_metadata_docs={},
        )

        self.assertEqual(findings, [])

    def test_markdown_and_json_include_codex_repair_prompt(self):
        finding = audit.Finding(
            "ERROR",
            "example",
            "example-check",
            "example finding",
            codex_prompt="Copy this prompt.",
        )

        markdown = audit.render_markdown([finding], "test-bucket", "", 1)
        payload = audit.asdict(finding)

        self.assertIn("Codex repair prompt", markdown)
        self.assertIn("Copy this prompt.", markdown)
        self.assertEqual(payload["codex_prompt"], "Copy this prompt.")


def blob_info(name: str, *, generation: str = "1") -> audit.BlobInfo:
    return audit.BlobInfo(
        name=name,
        size=1,
        generation=generation,
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
