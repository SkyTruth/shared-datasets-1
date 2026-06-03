from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class PmtilesCorsTerraformTests(unittest.TestCase):
    def test_cdn_cors_uses_exact_origins_and_redirector_keeps_regexes(self):
        pmtiles_cdn_tf = (REPO_ROOT / "terraform/envs/prod/pmtiles_cdn.tf").read_text()
        variables_tf = (REPO_ROOT / "terraform/envs/prod/variables.tf").read_text()

        self.assertIn('variable "pmtiles_cdn_allowed_origin_regexes"', variables_tf)
        self.assertIn(r"^https://(?:[A-Za-z0-9-]+\\.)+skytruth\\.org$", variables_tf)
        self.assertIn('"http://localhost:3000"', variables_tf)
        self.assertIn('"https://localhost:3000"', variables_tf)
        self.assertIn('"https://feature-three.cerulean.skytruth.org"', variables_tf)
        self.assertIn('"https://test.cerulean.skytruth.org"', variables_tf)
        self.assertIn('"https://develop.cerulean.skytruth.org"', variables_tf)
        self.assertIn('"https://cerulean.skytruth.org"', variables_tf)
        self.assertIn('"https://30x30.skytruth.org"', variables_tf)
        self.assertIn('"https://monitor.skytruth.org"', variables_tf)
        self.assertIn("PMTILES_ALLOWED_ORIGIN_REGEXES", pmtiles_cdn_tf)
        self.assertIn("value = join(\",\", var.pmtiles_cdn_allowed_origin_regexes)", pmtiles_cdn_tf)
        self.assertRegex(pmtiles_cdn_tf, r"allow_origins\s+= local\.pmtiles_browser_allowed_origins")
        self.assertNotIn("allow_origin_regexes", pmtiles_cdn_tf)
        self.assertNotIn('type        = "CLOUD_ARMOR_EDGE"', pmtiles_cdn_tf)
        self.assertNotIn("edge_security_policy", pmtiles_cdn_tf)
        self.assertIn("pmtiles_redirector_count   = 1", pmtiles_cdn_tf)
        self.assertNotIn("count = local.pmtiles_redirector_enabled ? 1 : 0", pmtiles_cdn_tf)

    def test_tiles_endpoint_serves_catalog_without_credentials(self):
        pmtiles_cdn_tf = (REPO_ROOT / "terraform/envs/prod/pmtiles_cdn.tf").read_text()

        self.assertIn('"/_catalog/shared-datasets-catalog.csv"', pmtiles_cdn_tf)
        self.assertIn('"/_catalog/web/catalog.json"', pmtiles_cdn_tf)
        self.assertIn('"/_catalog/*"', pmtiles_cdn_tf)
        self.assertIn("allow_credentials = false", pmtiles_cdn_tf)
        self.assertIn('allow_origins     = ["*"]', pmtiles_cdn_tf)
        self.assertIn('path                = "/_catalog/shared-datasets-catalog.csv"', pmtiles_cdn_tf)
        self.assertIn('path                = "/_catalog/web/catalog.json"', pmtiles_cdn_tf)
        self.assertIn(
            'expected_output_url = "https://${var.pmtiles_cdn_host}/_catalog/shared-datasets-catalog.csv"',
            pmtiles_cdn_tf,
        )

    def test_private_metadata_route_rewrites_to_bucket_object_path_without_credentials(self):
        pmtiles_cdn_tf = (REPO_ROOT / "terraform/envs/prod/pmtiles_cdn.tf").read_text()

        self.assertIn('paths   = ["/private/*"]', pmtiles_cdn_tf)
        self.assertIn("allow_credentials = false", pmtiles_cdn_tf)
        self.assertIn('allow_methods     = ["GET", "HEAD", "OPTIONS"]', pmtiles_cdn_tf)
        self.assertIn("allow_origins     = local.pmtiles_browser_allowed_origins", pmtiles_cdn_tf)
        self.assertIn(
            'expose_headers    = ["Cache-Control", "Content-Encoding", "Content-Length", "Content-Type", "ETag"]',
            pmtiles_cdn_tf,
        )
        self.assertIn('path_prefix_rewrite = "/"', pmtiles_cdn_tf)
        self.assertIn(
            'path                = "/private/100-geographic-reference/120-marine-boundaries/marine-regions-eez/releases/2026-05-16/marine-regions-eez.metadata.es.ndjson.gz"',
            pmtiles_cdn_tf,
        )
        self.assertIn(
            'expected_output_url = "https://${var.pmtiles_cdn_host}/100-geographic-reference/120-marine-boundaries/marine-regions-eez/releases/2026-05-16/marine-regions-eez.metadata.es.ndjson.gz"',
            pmtiles_cdn_tf,
        )
        self.assertIn(
            'resource "google_secret_manager_secret_iam_member" "pmtiles_cdn_catalog_viewer_signer"',
            pmtiles_cdn_tf,
        )
        self.assertIn("member    = module.catalog_viewer_service_account.member", pmtiles_cdn_tf)


if __name__ == "__main__":
    unittest.main()
