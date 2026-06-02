from __future__ import annotations

import json
import unittest

from services.feature_preview_service import run


class FakeResolver:
    def resolve(self, asset_slug: str, release: str) -> run.ResolvedRelease:
        if asset_slug != "wdpa-marine":
            raise run.ApiError(404, "not_found", "missing asset")
        resolved = "2026-06-01" if release == "latest" else release
        return run.ResolvedRelease(release, resolved, 7)


class FakeIndex:
    def lookup(self, asset_slug: str, release: str, feature_ids: list[str]):
        self.last_lookup = (asset_slug, release, feature_ids)
        return {
            "src:id:1": {
                "feature_id": "src:id:1",
                "feature_hash": "sha256:a",
                "properties": {"name": "A", "status": "active"},
                "provenance": {"source": "test"},
            }
        }


def lookup(body, headers=None):
    index = FakeIndex()
    response = run.handle_request(
        "POST",
        "/v1/assets/wdpa-marine/releases/latest:lookup",
        headers or {"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
        json.dumps(body).encode("utf-8"),
        release_resolver=FakeResolver(),
        feature_index=index,
    )
    return response, index


class FeaturePreviewServiceTests(unittest.TestCase):
    def test_lookup_requires_iap_identity(self):
        response = run.handle_request(
            "POST",
            "/v1/assets/wdpa-marine/releases/latest:lookup",
            {},
            b'{"ids":["src:id:1"]}',
            release_resolver=FakeResolver(),
            feature_index=FakeIndex(),
        )

        self.assertEqual(response.status, 401)

    def test_lookup_resolves_latest_filters_fields_and_marks_missing_ids(self):
        response, index = lookup({"ids": ["src:id:1", "src:id:2"], "fields": ["name"], "include_provenance": True})

        self.assertEqual(response.status, 200)
        self.assertEqual(index.last_lookup, ("wdpa-marine", "2026-06-01", ["src:id:1", "src:id:2"]))
        payload = json.loads(response.body)
        self.assertEqual(payload["resolved_release"], "2026-06-01")
        self.assertEqual(payload["items"][0]["properties"], {"name": "A"})
        self.assertEqual(payload["items"][0]["provenance"], {"source": "test"})
        self.assertEqual(payload["items"][1], {"id": "src:id:2", "found": False})
        self.assertIn("ETag", response.headers)

    def test_lookup_supports_etag_not_modified(self):
        response, _index = lookup({"ids": ["src:id:1"]})
        cached, _index = lookup({"ids": ["src:id:1"]}, headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org", "If-None-Match": response.headers["ETag"]})

        self.assertEqual(cached.status, 304)
        self.assertEqual(cached.body, b"")


if __name__ == "__main__":
    unittest.main()
