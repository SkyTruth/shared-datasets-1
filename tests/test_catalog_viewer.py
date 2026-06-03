from __future__ import annotations

import datetime as dt
import json
import unittest

from services.feature_preview_service import run as feature_preview_run
from services.catalog_viewer.run import CatalogJsonCache, StaticObject, StaticObjectNotFound, handle_request


PRIVATE_PATH = (
    "gs://skytruth-shared-datasets-1/400-events-observations/430-alerts-notices/"
    "acled-europe-central-asia-aggregated-weekly-admin1/latest/"
    "acled-europe-central-asia-aggregated-weekly-admin1.pmtiles"
)
PRIVATE_FGB_PATH = (
    "gs://skytruth-shared-datasets-1/400-events-observations/430-alerts-notices/"
    "acled-europe-central-asia-aggregated-weekly-admin1/latest/"
    "acled-europe-central-asia-aggregated-weekly-admin1.fgb"
)
PUBLIC_PATH = (
    "gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/"
    "wdpa-marine/latest/wdpa-marine.pmtiles"
)
PUBLIC_FGB_PATH = "gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/wdpa-marine/latest/wdpa-marine.fgb"
PUBLIC_FGB_RELEASE_PATH = (
    "gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/"
    "wdpa-marine/releases/2026-05-01/wdpa-marine.fgb"
)
PUBLIC_PMTILES_RELEASE_PATH = (
    "gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/"
    "wdpa-marine/releases/2026-05-01/wdpa-marine.pmtiles"
)
PUBLIC_FEATURE_INDEX_RELEASE_PATH = (
    "gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/"
    "wdpa-marine/releases/2026-05-01/wdpa-marine.features.ndjson.gz"
)


class FakeStore:
    def __init__(self, catalog):
        self.catalog = catalog
        self.static = {
            "index.html": StaticObject(b"<html></html>", "text/html"),
            "releases/wdpa-marine.json": StaticObject(
                json.dumps(
                    {
                        "asset_slug": "wdpa-marine",
                        "releases": [
                            {
                                "date": "2026-05-01",
                                "files": [
                                    {"format": "fgb", "path": PUBLIC_FGB_RELEASE_PATH},
                                    {"format": "pmtiles", "path": PUBLIC_PMTILES_RELEASE_PATH},
                                    {"format": "ndgeojson", "role": "feature_index", "path": PUBLIC_FEATURE_INDEX_RELEASE_PATH},
                                ],
                            }
                        ],
                    },
                    separators=(",", ":"),
                ).encode("utf-8"),
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
        return "https://storage.googleapis.com/signed-private.pmtiles?X-Goog-" + "Signature=abc"


class FakeFeatureReleaseResolver:
    def __init__(self) -> None:
        self.calls = []

    def resolve(self, asset_slug: str, release: str) -> feature_preview_run.ResolvedRelease:
        self.calls.append((asset_slug, release))
        return feature_preview_run.ResolvedRelease(
            requested_release=release,
            resolved_release="2026-05-01" if release == "latest" else release,
            release_index_generation=12345,
            sidecar_uri=(
                "gs://skytruth-shared-datasets-1-preview/100-geographic-reference/130-protected-areas/"
                "marine-regions-eez/releases/2026-05-01/marine-regions-eez.features.ndjson.gz"
            ),
            sidecar_generation=1001,
        )


class FakeFeatureIndex:
    def __init__(self) -> None:
        self.calls = []
        self.documents = {
            "src:MRGID:48943": {
                "feature_id": "src:MRGID:48943",
                "feature_hash": "hash-48943",
                "properties": {
                    "GEONAME": "Overlapping claim: Canada / United States",
                    "MRGID": 48943,
                    "SOVEREIGN1": "Canada",
                },
                "provenance": {"source": "preview sidecar"},
            }
        }

    def lookup(
        self,
        asset_slug: str,
        release: str,
        feature_ids: list[str],
        *,
        sidecar_uri: str,
        sidecar_generation: int | None,
    ) -> dict[str, dict]:
        self.calls.append((asset_slug, release, list(feature_ids), sidecar_uri, sidecar_generation))
        return {feature_id: self.documents[feature_id] for feature_id in feature_ids if feature_id in self.documents}


def catalog_payload():
    return {
        "assets": [
            {
                "slug": "wdpa-marine",
                "access_tier": "public",
                "available_formats": ["fgb", "pmtiles"],
                "canonical_format": "fgb",
                "canonical_path": PUBLIC_FGB_PATH,
                "has_pmtiles": True,
                "pmtiles_path": PUBLIC_PATH,
                "pmtiles_url": "https://tiles.skytruth.org/pmtiles/public/wdpa-marine.pmtiles",
                "versions": [
                    {
                        "date": "2026-04-01",
                        "canonical_path": "gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/wdpa-marine/releases/2026-04-01/wdpa-marine.fgb",
                    }
                ],
            },
            {
                "slug": "acled-europe-central-asia-aggregated-weekly-admin1",
                "access_tier": "private",
                "available_formats": ["fgb", "pmtiles"],
                "canonical_format": "fgb",
                "canonical_path": PRIVATE_FGB_PATH,
                "has_pmtiles": True,
                "pmtiles_path": PRIVATE_PATH,
                "pmtiles_url": "https://tiles.skytruth.org/pmtiles/private/acled-europe-central-asia-aggregated-weekly-admin1.pmtiles",
            },
            {
                "slug": "table-only",
                "access_tier": "public",
                "available_formats": ["csv"],
                "canonical_format": "csv",
                "canonical_path": "gs://skytruth-shared-datasets-1/900-example/table-only/latest/table-only.csv",
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


def download_url_request(
    slug: str,
    *,
    version: str = "latest",
    fmt: str = "fgb",
    headers=None,
    signer=None,
    catalog=None,
    store=None,
):
    store = store or FakeStore(catalog or catalog_payload())
    signer = signer or FakeSigner()
    return handle_request(
        "GET",
        f"/api/download-url?slug={slug}&format={fmt}&version={version}",
        headers or {},
        catalog_cache=CatalogJsonCache(loader=store.read_catalog_json),
        object_store=store,
        signer=signer,
        now=lambda: dt.datetime(2026, 5, 9, 12, 0, tzinfo=dt.UTC),
    ), signer


def feature_lookup_request(body, headers=None, resolver=None, index=None):
    store = FakeStore(catalog_payload())
    resolver = resolver or FakeFeatureReleaseResolver()
    index = index or FakeFeatureIndex()
    response = handle_request(
        "POST",
        "/v1/assets/marine-regions-eez/releases/latest:lookup",
        headers or {},
        json.dumps(body).encode("utf-8"),
        catalog_cache=CatalogJsonCache(loader=store.read_catalog_json),
        object_store=store,
        signer=FakeSigner(),
        bucket_name="skytruth-shared-datasets-1-preview",
        feature_release_resolver=resolver,
        feature_index=index,
        feature_max_ids=10,
        feature_max_fields=10,
        feature_max_response_bytes=100_000,
    )
    return response, resolver, index


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

    def test_public_latest_fgb_download_returns_direct_gcs_url(self):
        response, signer = download_url_request("wdpa-marine")

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(
            payload["download_url"],
            "https://storage.googleapis.com/skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/wdpa-marine/latest/wdpa-marine.fgb",
        )
        self.assertEqual(payload["expires_at"], None)
        self.assertEqual(payload["gs_uri"], PUBLIC_FGB_PATH)
        self.assertEqual(payload["filename"], "wdpa-marine.fgb")
        self.assertEqual(signer.calls, [])

    def test_private_latest_fgb_download_requires_authenticated_iap_identity(self):
        response, _signer = download_url_request("acled-europe-central-asia-aggregated-weekly-admin1")

        self.assertEqual(response.status, 401)

    def test_private_latest_fgb_download_returns_signed_gcs_url(self):
        response, signer = download_url_request(
            "acled-europe-central-asia-aggregated-weekly-admin1",
            headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
        )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertTrue(payload["download_url"].startswith("https://storage.googleapis.com/"))
        self.assertEqual(payload["expires_at"], "2026-05-09T12:15:00Z")
        self.assertEqual(payload["gs_uri"], PRIVATE_FGB_PATH)
        self.assertEqual(payload["filename"], "acled-europe-central-asia-aggregated-weekly-admin1.fgb")
        self.assertEqual(signer.calls[0][0], PRIVATE_FGB_PATH)

    def test_historical_fgb_download_uses_release_index_before_catalog_fallback(self):
        response, _signer = download_url_request("wdpa-marine", version="2026-05-01")

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["gs_uri"], PUBLIC_FGB_RELEASE_PATH)
        self.assertEqual(
            payload["download_url"],
            "https://storage.googleapis.com/skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/wdpa-marine/releases/2026-05-01/wdpa-marine.fgb",
        )

    def test_historical_fgb_download_falls_back_to_catalog_versions(self):
        response, _signer = download_url_request("wdpa-marine", version="2026-04-01")

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(
            payload["gs_uri"],
            "gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/wdpa-marine/releases/2026-04-01/wdpa-marine.fgb",
        )

    def test_fgb_download_rejects_invalid_version_and_unknown_slug(self):
        bad_version, _signer = download_url_request("wdpa-marine", version="yesterday")
        unknown, _signer = download_url_request("missing", version="latest")

        self.assertEqual(bad_version.status, 400)
        self.assertEqual(unknown.status, 404)

    def test_fgb_download_rejects_non_fgb_and_outside_bucket_paths(self):
        table_response, _signer = download_url_request("table-only")
        outside_catalog = catalog_payload()
        outside_catalog["assets"][0]["canonical_path"] = "gs://other-bucket/wdpa-marine/latest/wdpa-marine.fgb"
        outside_response, _signer = download_url_request("wdpa-marine", catalog=outside_catalog)

        self.assertEqual(table_response.status, 400)
        self.assertEqual(outside_response.status, 502)

    def test_feature_index_download_uses_release_sidecar(self):
        response, signer = download_url_request("wdpa-marine", version="2026-05-01", fmt="feature_index")

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["gs_uri"], PUBLIC_FEATURE_INDEX_RELEASE_PATH)
        self.assertEqual(
            payload["download_url"],
            "https://storage.googleapis.com/skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/wdpa-marine/releases/2026-05-01/wdpa-marine.features.ndjson.gz",
        )
        self.assertEqual(payload["filename"], "wdpa-marine.features.ndjson.gz")
        self.assertEqual(signer.calls, [])

    def test_feature_index_download_accepts_legacy_sidecar_format(self):
        store = FakeStore(catalog_payload())
        release_index = json.loads(store.static["releases/wdpa-marine.json"].body)
        release_index["releases"][0]["files"][-1] = {
            "format": "features_ndjson_gzip",
            "path": PUBLIC_FEATURE_INDEX_RELEASE_PATH,
        }
        store.static["releases/wdpa-marine.json"] = StaticObject(
            json.dumps(release_index, separators=(",", ":")).encode("utf-8"),
            "application/json; charset=utf-8",
        )

        response, _signer = download_url_request(
            "wdpa-marine",
            version="2026-05-01",
            fmt="feature_index",
            store=store,
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.body)["gs_uri"], PUBLIC_FEATURE_INDEX_RELEASE_PATH)

    def test_feature_index_download_accepts_features_sidecar_format(self):
        store = FakeStore(catalog_payload())
        release_index = json.loads(store.static["releases/wdpa-marine.json"].body)
        release_index["releases"][0]["files"][-1] = {
            "format": "features",
            "path": PUBLIC_FEATURE_INDEX_RELEASE_PATH,
        }
        store.static["releases/wdpa-marine.json"] = StaticObject(
            json.dumps(release_index, separators=(",", ":")).encode("utf-8"),
            "application/json; charset=utf-8",
        )

        response, _signer = download_url_request(
            "wdpa-marine",
            version="2026-05-01",
            fmt="feature_index",
            store=store,
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.body)["gs_uri"], PUBLIC_FEATURE_INDEX_RELEASE_PATH)

    def test_feature_index_download_latest_uses_release_index_latest(self):
        response, _signer = download_url_request("wdpa-marine", fmt="feature_index")

        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.body)["gs_uri"], PUBLIC_FEATURE_INDEX_RELEASE_PATH)

    def test_private_feature_index_download_returns_signed_url(self):
        catalog = catalog_payload()
        catalog["assets"][0]["access_tier"] = "private"
        response, signer = download_url_request(
            "wdpa-marine",
            version="2026-05-01",
            fmt="feature_index",
            headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
            catalog=catalog,
        )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertTrue(payload["download_url"].startswith("https://storage.googleapis.com/signed-private.pmtiles"))
        self.assertEqual(payload["gs_uri"], PUBLIC_FEATURE_INDEX_RELEASE_PATH)
        self.assertEqual(signer.calls[0][0], PUBLIC_FEATURE_INDEX_RELEASE_PATH)

    def test_feature_metadata_lookup_requires_authenticated_iap_identity(self):
        response, resolver, index = feature_lookup_request({"ids": ["src:MRGID:48943"]})

        self.assertEqual(response.status, 401)
        self.assertEqual(resolver.calls, [])
        self.assertEqual(index.calls, [])

    def test_feature_metadata_lookup_delegates_to_preview_sidecar_index(self):
        response, resolver, index = feature_lookup_request(
            {"ids": ["src:MRGID:48943"], "include_provenance": True},
            {"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
        )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["asset_slug"], "marine-regions-eez")
        self.assertEqual(payload["requested_release"], "latest")
        self.assertEqual(payload["resolved_release"], "2026-05-01")
        self.assertEqual(payload["release_index_generation"], 12345)
        self.assertEqual(resolver.calls, [("marine-regions-eez", "latest")])
        self.assertEqual(
            index.calls,
            [
                (
                    "marine-regions-eez",
                    "2026-05-01",
                    ["src:MRGID:48943"],
                    "gs://skytruth-shared-datasets-1-preview/100-geographic-reference/130-protected-areas/marine-regions-eez/releases/2026-05-01/marine-regions-eez.features.ndjson.gz",
                    1001,
                )
            ],
        )
        self.assertEqual(payload["items"][0]["id"], "src:MRGID:48943")
        self.assertTrue(payload["items"][0]["found"])
        self.assertEqual(payload["items"][0]["feature_hash"], "hash-48943")
        self.assertEqual(payload["items"][0]["properties"]["GEONAME"], "Overlapping claim: Canada / United States")
        self.assertEqual(payload["items"][0]["properties"]["MRGID"], 48943)
        self.assertEqual(payload["items"][0]["provenance"]["source"], "preview sidecar")


if __name__ == "__main__":
    unittest.main()
