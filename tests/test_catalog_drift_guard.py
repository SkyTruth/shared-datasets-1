from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts import catalog_drift_guard as guard


REPO_ROOT = Path(__file__).resolve().parents[1]


def remote_object(name: str, text: str) -> guard.RemoteObject:
    return guard.RemoteObject(
        uri=f"gs://example-bucket/{name}",
        name=name,
        generation="123",
        updated="2026-05-02T00:00:00+00:00",
        size=len(text),
        text=text,
    )


def web_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "generated_at": "2026-05-02T00:00:00Z",
        "bucket": "example-bucket",
        "site_prefix": "_catalog/web",
        "source_catalog": "catalog/shared-datasets-catalog.csv",
        "categories": {"100-geographic-reference": {"110-boundaries": "Boundaries."}},
        "formats": ["fgb", "pmtiles"],
        "assets": [
            {
                "slug": "example-asset",
                "title": "Example Asset",
                "last_updated": "2026-05-01",
            }
        ],
    }


class CatalogDriftGuardTests(unittest.TestCase):
    def test_csv_contract_passes_on_exact_match(self):
        local_text = "asset_slug,title\nexample-asset,Example Asset\n"
        result = guard.check_csv_contract(
            local_text,
            remote_object(guard.REMOTE_CSV_OBJECT, local_text),
        )

        self.assertTrue(result.ok)
        self.assertIn("matches the repo catalog", result.message)
        self.assertEqual(result.diff, "")

    def test_csv_contract_diff_shows_live_and_repo_values(self):
        result = guard.check_csv_contract(
            "asset_slug,title\nexample-asset,Current Asset\n",
            remote_object(guard.REMOTE_CSV_OBJECT, "asset_slug,title\nexample-asset,Stale Asset\n"),
        )

        self.assertFalse(result.ok)
        self.assertIn("-example-asset,Stale Asset", result.diff)
        self.assertIn("+example-asset,Current Asset", result.diff)

    def test_web_catalog_contract_ignores_generated_at_only(self):
        expected = web_payload()
        live = copy.deepcopy(expected)
        live["generated_at"] = "2026-05-01T00:00:00Z"

        result = guard.check_web_catalog_contract(
            expected,
            remote_object(guard.REMOTE_WEB_CATALOG_OBJECT, json.dumps(live)),
        )

        self.assertTrue(result.ok)
        self.assertIn("after ignoring generated_at", result.message)

    def test_web_catalog_contract_catches_asset_drift(self):
        expected = web_payload()
        live = copy.deepcopy(expected)
        live["assets"][0]["title"] = "Stale Asset"

        result = guard.check_web_catalog_contract(
            expected,
            remote_object(guard.REMOTE_WEB_CATALOG_OBJECT, json.dumps(live)),
        )

        self.assertFalse(result.ok)
        self.assertIn('"title": "Stale Asset"', result.diff)
        self.assertIn('"title": "Example Asset"', result.diff)

    def test_web_catalog_contract_requires_generated_at(self):
        live = web_payload()
        live.pop("generated_at")

        with self.assertRaisesRegex(guard.CatalogDriftGuardError, "missing required generated_at"):
            guard.check_web_catalog_contract(
                web_payload(),
                remote_object(guard.REMOTE_WEB_CATALOG_OBJECT, json.dumps(live)),
            )

    def test_catalog_drift_workflow_uses_readonly_gcp_variables(self):
        workflow = (REPO_ROOT / ".github/workflows/catalog-drift-guard.yml").read_text()

        self.assertIn("GCP_READONLY_WORKLOAD_IDENTITY_PROVIDER", workflow)
        self.assertIn("GCP_READONLY_SERVICE_ACCOUNT", workflow)
        self.assertIn("Missing repository variable: GCP_READONLY_WORKLOAD_IDENTITY_PROVIDER", workflow)
        self.assertNotIn("vars.GCP_SERVICE_ACCOUNT", workflow)

    def test_catalog_drift_workflow_skips_live_bucket_check_on_pull_requests(self):
        workflow = (REPO_ROOT / ".github/workflows/catalog-drift-guard.yml").read_text()

        self.assertIn("workflow_run:", workflow)
        self.assertIn("Catalog web deploy", workflow)
        self.assertNotIn("push:", workflow)
        self.assertIn("Check pull request catalog consistency", workflow)
        self.assertIn("if: ${{ github.event_name == 'pull_request' }}", workflow)
        self.assertIn("uv run python scripts/catalog_docs.py check", workflow)
        self.assertIn("uv run python scripts/catalog_site.py --out", workflow)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", workflow)
        self.assertGreaterEqual(workflow.count("if: ${{ github.event_name != 'pull_request' }}"), 3)
        self.assertIn("Collect release indexes for drift guard", workflow)
        self.assertIn('gcloud storage cp "gs://${SHARED_DATASETS_BUCKET}/_catalog/releases/*.json"', workflow)
        self.assertIn("Check live catalog drift", workflow)
        self.assertIn("uv run python scripts/catalog_drift_guard.py", workflow)
        self.assertIn('--release-index-dir "${RUNNER_TEMP}/release-indexes"', workflow)
        self.assertIn("--latest-from-release-index", workflow)

    def test_expected_web_payload_passes_release_index_options(self):
        args = SimpleNamespace(
            catalog=Path("catalog/shared-datasets-catalog.csv"),
            categories=Path("catalog/categories.yaml"),
            docs_dir=Path("docs/assets"),
            bucket="example-bucket",
            site_prefix="_catalog/web",
            release_index_dir=Path("/tmp/release-indexes"),
            latest_from_release_index=True,
        )

        with mock.patch("scripts.catalog_drift_guard.catalog_site.build_catalog_payload", return_value={}) as build:
            guard.expected_web_payload(args)

        self.assertEqual(build.call_args.kwargs["release_index_dir"], Path("/tmp/release-indexes"))
        self.assertTrue(build.call_args.kwargs["latest_from_release_index"])

    def test_bucket_hygiene_audit_workflow_uses_readonly_gcp_variables(self):
        workflow = (REPO_ROOT / ".github/workflows/bucket-hygiene-audit.yml").read_text()

        self.assertIn("GCP_READONLY_WORKLOAD_IDENTITY_PROVIDER", workflow)
        self.assertIn("GCP_READONLY_SERVICE_ACCOUNT", workflow)
        self.assertIn("Missing repository variable: GCP_READONLY_SERVICE_ACCOUNT", workflow)
        self.assertNotIn("vars.GCP_SERVICE_ACCOUNT", workflow)

    def test_bucket_hygiene_audit_workflow_runs_production_health_profile(self):
        workflow = (REPO_ROOT / ".github/workflows/bucket-hygiene-audit.yml").read_text()

        self.assertIn("Bucket hygiene audit may only use production read-only auth from main", workflow)
        self.assertIn("--health-profile production", workflow)
        self.assertIn("--format markdown", workflow)
        self.assertNotIn("--local-only", workflow)

    def test_scratch_cleanup_audit_uses_protected_publisher_path(self):
        workflow = (REPO_ROOT / ".github/workflows/scratch-cleanup-audit.yml").read_text()

        self.assertIn("name: Scratch cleanup audit", workflow)
        self.assertIn("schedule:", workflow)
        self.assertIn("environment: shared-datasets-production", workflow)
        self.assertIn("GCP_WORKLOAD_IDENTITY_PROVIDER", workflow)
        self.assertIn("shared-datasets-publisher@shared-datasets-1.iam.gserviceaccount.com", workflow)
        self.assertIn("scripts/scratch_cleanup.py", workflow)
        self.assertIn("--apply", workflow)
        self.assertNotIn("--send-slack", workflow)
        self.assertNotIn("--strict-slack", workflow)
        self.assertNotIn("SHARED_DATASETS_SLACK_WEBHOOK_URL", workflow)


if __name__ == "__main__":
    unittest.main()
