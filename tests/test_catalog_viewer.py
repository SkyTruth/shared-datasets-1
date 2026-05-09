from __future__ import annotations

import datetime as dt
import json
import unittest

from services.catalog_viewer.run import CatalogJsonCache, StaticObject, StaticObjectNotFound, handle_request


PRIVATE_PATH = (
    "gs://skytruth-shared-datasets-1/400-events-observations/430-alerts-notices/"
    "acled-europe-central-asia-aggregated-weekly-admin1/latest/"
    "acled-europe-central-asia-aggregated-weekly-admin1.pmtiles"
)
PUBLIC_PATH = (
    "gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/"
    "wdpa-marine/latest/wdpa-marine.pmtiles"
)


class FakeStore:
    def __init__(self, catalog):
        self.catalog = catalog
        self.static = {
            "index.html": StaticObject(b"<html></html>", "text/html"),
            "releases/wdpa-marine.json": StaticObject(
                b'{"asset_slug":"wdpa-marine","releases":[]}',
                "application/json; charset=utf-8",
            ),
        }

    def read_static(self, object_name: str) -> StaticObject:
        if object_name not in self.static:
            raise StaticObjectNotFound(object_name)
        return self.static[object_name]

    def read_catalog_json(self):
        return self.catalog


class FakeSigner:
    def __init__(self) -> None:
        self.calls = []

    def sign(self, gs_uri: str, expires_at: dt.datetime) -> str:
        self.calls.append((gs_uri, expires_at))
        return "https://storage.googleapis.com/signed-private.pmtiles?X-Goog-Signature=abc"


def catalog_payload():
    return {
        "assets": [
            {
                "slug": "wdpa-marine",
                "access_tier": "public",
                "available_formats": ["fgb", "pmtiles"],
                "has_pmtiles": True,
                "pmtiles_path": PUBLIC_PATH,
                "pmtiles_url": "https://tiles.skytruth.org/pmtiles/public/wdpa-marine.pmtiles",
            },
            {
                "slug": "acled-europe-central-asia-aggregated-weekly-admin1",
                "access_tier": "private",
                "available_formats": ["fgb", "pmtiles"],
                "has_pmtiles": True,
                "pmtiles_path": PRIVATE_PATH,
                "pmtiles_url": "https://tiles.skytruth.org/pmtiles/private/acled-europe-central-asia-aggregated-weekly-admin1.pmtiles",
            },
            {
                "slug": "table-only",
                "access_tier": "public",
                "available_formats": ["csv"],
                "has_pmtiles": False,
                "pmtiles_path": None,
            },
        ]
    }


def signed_url_request(slug: str, headers=None, signer=None, catalog=None):
    store = FakeStore(catalog or catalog_payload())
    signer = signer or FakeSigner()
    return handle_request(
        "GET",
        f"/api/pmtiles/signed-url?slug={slug}",
        headers or {},
        catalog_cache=CatalogJsonCache(loader=store.read_catalog_json),
        object_store=store,
        signer=signer,
        now=lambda: dt.datetime(2026, 5, 9, 12, 0, tzinfo=dt.UTC),
    ), signer


class CatalogViewerTests(unittest.TestCase):
    def test_unknown_slug_returns_404(self):
        response, _signer = signed_url_request("missing")

        self.assertEqual(response.status, 404)

    def test_asset_without_pmtiles_returns_400(self):
        response, _signer = signed_url_request("table-only")

        self.assertEqual(response.status, 400)

    def test_public_pmtiles_returns_direct_https_url_without_signing(self):
        response, signer = signed_url_request("wdpa-marine")

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["pmtiles_url"], "https://storage.googleapis.com/skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/wdpa-marine/latest/wdpa-marine.pmtiles")
        self.assertIsNone(payload["expires_at"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(signer.calls, [])

    def test_private_pmtiles_requires_authenticated_iap_identity(self):
        response, _signer = signed_url_request("acled-europe-central-asia-aggregated-weekly-admin1")

        self.assertEqual(response.status, 401)

    def test_private_pmtiles_rejects_non_skytruth_identity(self):
        response, _signer = signed_url_request(
            "acled-europe-central-asia-aggregated-weekly-admin1",
            {"X-Goog-Authenticated-User-Email": "accounts.google.com:user@example.com"},
        )

        self.assertEqual(response.status, 403)

    def test_private_pmtiles_returns_no_store_signed_gcs_url_for_iap_identity(self):
        response, signer = signed_url_request(
            "acled-europe-central-asia-aggregated-weekly-admin1",
            {"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
        )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertTrue(payload["pmtiles_url"].startswith("https://storage.googleapis.com/"))
        self.assertEqual(payload["expires_at"], "2026-05-09T12:15:00Z")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(signer.calls[0][0], PRIVATE_PATH)
        self.assertNotIn("tiles.skytruth.org", payload["pmtiles_url"])
        self.assertNotIn("Location", response.headers)

    def test_static_root_serves_generated_index(self):
        store = FakeStore(catalog_payload())
        response = handle_request(
            "GET",
            "/",
            {},
            catalog_cache=CatalogJsonCache(loader=store.read_catalog_json),
            object_store=store,
            signer=FakeSigner(),
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-cache, max-age=0, must-revalidate")
        self.assertEqual(response.body, b"<html></html>")

    def test_release_index_route_serves_catalog_release_object(self):
        store = FakeStore(catalog_payload())
        response = handle_request(
            "GET",
            "/releases/wdpa-marine.json",
            {},
            catalog_cache=CatalogJsonCache(loader=store.read_catalog_json),
            object_store=store,
            signer=FakeSigner(),
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Type"], "application/json; charset=utf-8")
        self.assertIn(b'"asset_slug":"wdpa-marine"', response.body)

    def test_release_index_route_rejects_path_traversal(self):
        store = FakeStore(catalog_payload())
        response = handle_request(
            "GET",
            "/releases/../catalog.json",
            {},
            catalog_cache=CatalogJsonCache(loader=store.read_catalog_json),
            object_store=store,
            signer=FakeSigner(),
        )

        self.assertEqual(response.status, 404)


if __name__ == "__main__":
    unittest.main()
