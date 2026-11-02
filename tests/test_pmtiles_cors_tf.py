from __future__ import annotations

import unittest
from pathlib import Path

from terraform_helpers import evaluate, load_terraform, resources


REPO_ROOT = Path(__file__).resolve().parents[1]


class PmtilesCorsTerraformTests(unittest.TestCase):
    def test_each_static_route_has_its_own_enabled_cors_policy(self):
        document = load_terraform(REPO_ROOT / "terraform/envs/prod/pmtiles_cdn.tf")
        url_map, = [attributes for kind, name, attributes in resources(document)
                    if (kind, name) == ("google_compute_url_map", "pmtiles_cdn")]
        routes = [{"paths": route["paths"], "route_action": route["route_action"]}
                  for matcher in url_map["path_matcher"] for route in matcher["path_rule"]]
        origins = ["https://viewer.example.test", "https://comparison.example.test"]
        actual = evaluate({"pmtiles_browser_allowed_origins": origins, "routes": routes}, "local.routes")
        by_paths = {frozenset(route["paths"]): route["route_action"][0] for route in actual}
        artifact_headers = ["Accept-Ranges", "Cache-Control", "Content-Encoding", "Content-Length", "Content-Range", "Content-Type", "ETag"]
        expected = {
            frozenset({"/_catalog/shared-datasets-catalog.csv", "/_catalog/web/catalog.json", "/_catalog/*"}):
                (["*"], None, ["Cache-Control", "Content-Length", "Content-Type", "ETag"]),
            frozenset({"/artifacts/*"}): (["*"], "/", artifact_headers),
            frozenset({"/private/*"}): (origins, "/", artifact_headers),
        }
        self.assertEqual(len(actual), len(expected))
        self.assertEqual(set(by_paths), set(expected))
        for paths, (allowed_origins, rewrite, exposed_headers) in expected.items():
            with self.subTest(paths=paths):
                action = by_paths[paths]
                policy, = action["cors_policy"]
                self.assertIs(policy["disabled"], False)
                self.assertIs(policy["allow_credentials"], False)
                self.assertEqual(set(policy["allow_origins"]), set(allowed_origins))
                self.assertEqual(set(policy["allow_methods"]), {"GET", "HEAD", "OPTIONS"})
                self.assertEqual(set(policy["expose_headers"]), set(exposed_headers))
                self.assertEqual(action.get("url_rewrite", []), [] if rewrite is None else [{"path_prefix_rewrite": rewrite}])

    def test_cdn_cors_splits_public_wildcard_and_private_exact_origins(self):
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
        self.assertIn('allow_credentials = split("/", path_rule.key)[0] != "public"', pmtiles_cdn_tf)
        self.assertIn(
            'allow_origins     = split("/", path_rule.key)[0] != "public" ? local.pmtiles_browser_allowed_origins : ["*"]',
            pmtiles_cdn_tf,
        )
        self.assertNotIn("allow_origin_regexes", pmtiles_cdn_tf)
        self.assertNotIn('type        = "CLOUD_ARMOR_EDGE"', pmtiles_cdn_tf)
        self.assertNotIn("edge_security_policy", pmtiles_cdn_tf)
        self.assertIn("pmtiles_redirector_count   = 1", pmtiles_cdn_tf)
        self.assertNotIn("count = local.pmtiles_redirector_enabled ? 1 : 0", pmtiles_cdn_tf)

    def test_published_preview_origin_is_bucket_only_for_signed_gcs_reads(self):
        origin = "https://feature-preview-catalog-viewer-vmust7rama-uc.a.run.app"
        bucket = (REPO_ROOT / "terraform/envs/prod/shared_bucket_public.tf").read_text()
        self.assertIn(f'"{origin}"', bucket.split("cors {", 1)[1].split("}", 1)[0])
        for name in ("variables.tf", "catalog_viewer.tf", "pmtiles_cdn.tf"):
            self.assertNotIn(origin, (REPO_ROOT / "terraform/envs/prod" / name).read_text())

    def test_tiles_endpoint_serves_catalog_without_credentials(self):
        pmtiles_cdn_tf = (REPO_ROOT / "terraform/envs/prod/pmtiles_cdn.tf").read_text()

        self.assertIn('path                = "/_catalog/shared-datasets-catalog.csv"', pmtiles_cdn_tf)
        self.assertIn('path                = "/_catalog/web/catalog.json"', pmtiles_cdn_tf)
        self.assertIn(
            'expected_output_url = "https://${var.pmtiles_cdn_host}/_catalog/shared-datasets-catalog.csv"',
            pmtiles_cdn_tf,
        )

    def test_artifact_routes_rewrite_to_bucket_object_paths(self):
        pmtiles_cdn_tf = (REPO_ROOT / "terraform/envs/prod/pmtiles_cdn.tf").read_text()

        self.assertIn(
            'path                = "/artifacts/100-geographic-reference/130-protected-areas/wdpa-marine/releases/2026-05-01/wdpa-marine.metadata.es.ndjson.gz"',
            pmtiles_cdn_tf,
        )
        self.assertIn(
            'path                = "/private/100-geographic-reference/130-protected-areas/wdpa-marine/releases/2026-05-01/wdpa-marine.metadata.es.ndjson.gz"',
            pmtiles_cdn_tf,
        )
        self.assertIn(
            'expected_output_url = "https://${var.pmtiles_cdn_host}/100-geographic-reference/130-protected-areas/wdpa-marine/releases/2026-05-01/wdpa-marine.metadata.es.ndjson.gz"',
            pmtiles_cdn_tf,
        )
        self.assertIn(
            'resource "google_secret_manager_secret_iam_member" "pmtiles_cdn_catalog_viewer_signer"',
            pmtiles_cdn_tf,
        )
        self.assertIn("member    = module.catalog_viewer_service_account.member", pmtiles_cdn_tf)


if __name__ == "__main__":
    unittest.main()
