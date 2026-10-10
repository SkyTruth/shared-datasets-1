from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class CatalogWebPmtilesJavascriptTests(unittest.TestCase):
    def test_catalog_viewer_javascript_is_syntactically_valid(self):
        for path in (REPO_ROOT / "web/catalog/app.js", REPO_ROOT / "web/catalog/map-preview.js"):
            with self.subTest(path=path.name):
                result = subprocess.run(
                    ["node", "--check", str(path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_restricted_pmtiles_declares_fetch_credentials(self):
        # The pinned PMTiles API ignores this third argument. Preserve the
        # existing declaration check until the credential behavior is repaired.
        self.assertIn(
            'new window.pmtiles.FetchSource(asset.pmtiles_url, new Headers(), "include")',
            (REPO_ROOT / "web/catalog/map-preview.js").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
