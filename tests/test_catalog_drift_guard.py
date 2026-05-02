from __future__ import annotations

import copy
import json
import unittest

from scripts import catalog_drift_guard as guard


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


if __name__ == "__main__":
    unittest.main()
