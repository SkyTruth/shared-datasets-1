from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class CatalogWebPmtilesJavascriptTests(unittest.TestCase):
    def test_private_pmtiles_can_use_same_origin_signer_before_cdn_cookie_fallback(self):
        map_preview = (REPO_ROOT / "web/catalog/map-preview.js").read_text()
        app = (REPO_ROOT / "web/catalog/app.js").read_text()

        self.assertIn('"/api/pmtiles/signed-url"', map_preview)
        self.assertIn("requestSignedPmtilesUrl", map_preview)
        self.assertIn('credentials: "include"', map_preview)
        self.assertIn("_pmtiles_signed_url", map_preview)
        self.assertIn("pmtilesNeedsCredentials(asset)", map_preview)
        self.assertIn("new window.pmtiles.PMTiles(asset.pmtiles_url)", map_preview)
        self.assertIn("new window.pmtiles.FetchSource(asset.pmtiles_url, new Headers(), \"include\")", map_preview)
        self.assertIn("isStorageGoogleapisHost", map_preview)
        self.assertIn("Signed PMTiles access was rejected or expired", app)


if __name__ == "__main__":
    unittest.main()
