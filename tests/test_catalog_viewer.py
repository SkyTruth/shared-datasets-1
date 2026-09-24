from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from services.feature_preview_service import run as feature_preview_run
from services.catalog_viewer.run import (
    CatalogJsonCache,
    CloudCdnSignedUrlSigner,
    LocalCatalogWebStore,
    StaticObject,
    StaticObjectNotFound,
    decode_cdn_signing_key,
    handle_request,
    secret_manager_version_name,
)


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
PUBLIC_METADATA_RELEASE_PATH = (
    "gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/"
    "wdpa-marine/releases/2026-05-01/wdpa-marine.metadata.ndjson.gz"
)
PUBLIC_METADATA_ES_RELEASE_PATH = (
    "gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/"
    "wdpa-marine/releases/2026-05-01/wdpa-marine.metadata.es.ndjson.gz"
)
PUBLIC_SCHEMA_RELEASE_PATH = (
    "gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/"
    "wdpa-marine/releases/2026-05-01/wdpa-marine.schema.json"
)
PRIVATE_SCHEMA_RELEASE_PATH = (
    "gs://skytruth-shared-datasets-1/400-events-observations/430-alerts-notices/"
    "acled-europe-central-asia-aggregated-weekly-admin1/releases/2026-05-01/"
    "acled-europe-central-asia-aggregated-weekly-admin1.schema.json"
)


class FakeStore:
    def __init__(self, catalog):
        self.catalog = catalog
        self.static = {
            "index.html": StaticObject(b"<html></html>", "text/html"),
            "releases/wdpa-marine.json": StaticObject(
                json.dumps(
                    {
                        "schema_version": 1,
                        "asset_slug": "wdpa-marine",
                        "latest_release": {
                            "date": "2026-05-01",
                            "files": [
                                {"format": "fgb", "path": PUBLIC_FGB_RELEASE_PATH, "generation": 1000},
                                {"format": "pmtiles", "path": PUBLIC_PMTILES_RELEASE_PATH, "generation": 1003},
                                {"format": "metadata", "path": PUBLIC_METADATA_RELEASE_PATH, "generation": 1001},
                                {"format": "schema", "path": PUBLIC_SCHEMA_RELEASE_PATH, "generation": 1002},
                                {
                                    "format": "metadata",
                                    "role": "metadata",
                                    "locale": "es",
                                    "path": PUBLIC_METADATA_ES_RELEASE_PATH,
                                    "generation": 1004,
                                },
                            ],
                        },
                        "releases": [
                            {
                                "date": "2026-05-01",
                                "files": [
                                    {"format": "fgb", "path": PUBLIC_FGB_RELEASE_PATH, "generation": 1000},
                                    {"format": "pmtiles", "path": PUBLIC_PMTILES_RELEASE_PATH, "generation": 1003},
                                    {"format": "metadata", "path": PUBLIC_METADATA_RELEASE_PATH, "generation": 1001},
                                    {"format": "schema", "path": PUBLIC_SCHEMA_RELEASE_PATH, "generation": 1002},
                                    {
                                        "format": "metadata",
                                        "role": "metadata",
                                        "locale": "es",
                                        "path": PUBLIC_METADATA_ES_RELEASE_PATH,
                                        "generation": 1004,
                                    },
                                ],
                            }
                        ],
                    },
                    separators=(",", ":"),
                ).encode("utf-8"),
                "application/json; charset=utf-8",
            ),
            "releases/acled-europe-central-asia-aggregated-weekly-admin1.json": StaticObject(
                json.dumps(
                    {
                        "schema_version": 1,
                        "asset_slug": "acled-europe-central-asia-aggregated-weekly-admin1",
                        "latest_release": {
                            "date": "2026-05-01",
                            "files": [
                                {"format": "fgb", "path": PRIVATE_FGB_PATH.replace("/latest/", "/releases/2026-05-01/"), "generation": 1000},
                                {"format": "pmtiles", "path": PRIVATE_PATH.replace("/latest/", "/releases/2026-05-01/"), "generation": 1003},
                                {"format": "schema", "path": PRIVATE_SCHEMA_RELEASE_PATH, "generation": 1002},
                            ],
                        },
                        "releases": [
                            {
                                "date": "2026-05-01",
                                "files": [
                                    {"format": "fgb", "path": PRIVATE_FGB_PATH.replace("/latest/", "/releases/2026-05-01/"), "generation": 1000},
                                    {"format": "pmtiles", "path": PRIVATE_PATH.replace("/latest/", "/releases/2026-05-01/"), "generation": 1003},
                                    {"format": "schema", "path": PRIVATE_SCHEMA_RELEASE_PATH, "generation": 1002},
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

    def sign(self, gs_uri: str, expires_at: dt.datetime, *, generation=None) -> str:
        self.calls.append((gs_uri, expires_at, generation))
        return "https://storage.googleapis.com/signed-private.pmtiles?X-Goog-" + "Signature=abc"


class FakeCdnSigner:
    def __init__(self) -> None:
        self.calls = []

    def sign(self, gs_uri: str, expires_at: dt.datetime, *, generation=None) -> str:
        self.calls.append((gs_uri, expires_at, generation))
        object_name = gs_uri.removeprefix("gs://skytruth-shared-datasets-1/")
        expires = int(expires_at.timestamp())
        return f"https://tiles.skytruth.org/private/{object_name}?Expires={expires}&KeyName=test-key&Signature=abc"


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
                "wdpa-marine/releases/2026-05-01/wdpa-marine.metadata.ndjson.gz"
            ),
            sidecar_generation=1001,
        )


class FakeFeatureIndex:
    def __init__(self) -> None:
        self.calls = []
        self.documents = {
            "48943": {
                "feature_id": "48943",
                "properties_hash": "hash-48943",
                "properties": {
                    "feature_id": "48943",
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
    locale: str = "",
    headers=None,
    signer=None,
    metadata_cdn_signer=None,
    metadata_cdn_ttl_seconds=None,
    catalog=None,
    store=None,
):
    store = store or FakeStore(catalog or catalog_payload())
    signer = signer or FakeSigner()
    return handle_request(
        "GET",
        f"/api/download-url?slug={slug}&format={fmt}&version={version}" + (f"&locale={locale}" if locale else ""),
        headers or {},
        catalog_cache=CatalogJsonCache(loader=store.read_catalog_json),
        object_store=store,
        signer=signer,
        metadata_cdn_signer=metadata_cdn_signer,
        metadata_cdn_ttl_seconds=metadata_cdn_ttl_seconds,
        now=lambda: dt.datetime(2026, 5, 9, 12, 0, tzinfo=dt.UTC),
    ), signer


def feature_lookup_request(body, headers=None, resolver=None, index=None, feature_require_iap=True):
    store = FakeStore(catalog_payload())
    resolver = resolver or FakeFeatureReleaseResolver()
    index = index or FakeFeatureIndex()
    response = handle_request(
        "POST",
        "/v1/assets/wdpa-marine/releases/latest:lookup",
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
        feature_require_iap=feature_require_iap,
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
        self.assertEqual(payload["pmtiles_url"], "https://tiles.skytruth.org/artifacts/100-geographic-reference/130-protected-areas/wdpa-marine/releases/2026-05-01/wdpa-marine.pmtiles?generation=1003")
        self.assertIsNone(payload["expires_at"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(signer.calls, [])

    def test_private_pmtiles_requires_authenticated_iap_identity(self):
        response, _signer = signed_url_request("acled-europe-central-asia-aggregated-weekly-admin1")

        self.assertEqual(response.status, 401)

    def test_private_pmtiles_requires_iap_header_not_forwarded_email(self):
        response, _signer = signed_url_request(
            "acled-europe-central-asia-aggregated-weekly-admin1",
            {"X-Forwarded-Email": "jona@skytruth.org"},
        )

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
        self.assertEqual(signer.calls[0][0], PRIVATE_PATH.replace("/latest/", "/releases/2026-05-01/"))
        self.assertNotIn("tiles.skytruth.org", payload["pmtiles_url"])
        self.assertNotIn("Location", response.headers)

    def test_internal_pmtiles_returns_no_store_signed_gcs_url_for_iap_identity(self):
        catalog = catalog_payload()
        catalog["assets"][1]["access_tier"] = "internal"
        catalog["assets"][1]["pmtiles_url"] = (
            "https://tiles.skytruth.org/pmtiles/internal/acled-europe-central-asia-aggregated-weekly-admin1.pmtiles"
        )
        response, signer = signed_url_request(
            "acled-europe-central-asia-aggregated-weekly-admin1",
            {"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
            catalog=catalog,
        )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertTrue(payload["pmtiles_url"].startswith("https://storage.googleapis.com/"))
        self.assertEqual(signer.calls[0][0], PRIVATE_PATH.replace("/latest/", "/releases/2026-05-01/"))

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

    def test_local_catalog_web_store_serves_generated_bundle_files(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "releases").mkdir()
            (root / "index.html").write_text("<html>local</html>", encoding="utf-8")
            (root / "catalog.json").write_text('{"assets":[]}', encoding="utf-8")
            (root / "releases" / "wdpa-marine.json").write_text('{"asset_slug":"wdpa-marine"}', encoding="utf-8")

            store = LocalCatalogWebStore(root)
            index_object = store.read_static("index.html")
            release_object = store.read_static("releases/wdpa-marine.json")

            self.assertEqual(index_object.body, b"<html>local</html>")
            self.assertEqual(release_object.content_type, "application/json; charset=utf-8")
            self.assertEqual(store.read_catalog_json(), {"assets": []})

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
            "https://tiles.skytruth.org/artifacts/100-geographic-reference/130-protected-areas/wdpa-marine/releases/2026-05-01/wdpa-marine.fgb?generation=1000",
        )
        self.assertEqual(payload["expires_at"], None)
        self.assertEqual(payload["gs_uri"], PUBLIC_FGB_RELEASE_PATH)
        self.assertEqual(payload["filename"], "wdpa-marine.fgb")
        self.assertEqual(signer.calls, [])

    def test_private_latest_fgb_download_requires_authenticated_iap_identity(self):
        response, _signer = download_url_request("acled-europe-central-asia-aggregated-weekly-admin1")

        self.assertEqual(response.status, 401)

    def test_private_latest_fgb_download_returns_signed_gcs_url(self):
        metadata_cdn_signer = FakeCdnSigner()
        response, signer = download_url_request(
            "acled-europe-central-asia-aggregated-weekly-admin1",
            headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
            metadata_cdn_signer=metadata_cdn_signer,
        )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertTrue(payload["download_url"].startswith("https://storage.googleapis.com/"))
        self.assertEqual(payload["expires_at"], "2026-05-09T12:15:00Z")
        self.assertEqual(payload["gs_uri"], PRIVATE_FGB_PATH.replace("/latest/", "/releases/2026-05-01/"))
        self.assertEqual(payload["filename"], "acled-europe-central-asia-aggregated-weekly-admin1.fgb")
        self.assertEqual(signer.calls[0][0], PRIVATE_FGB_PATH.replace("/latest/", "/releases/2026-05-01/"))
        self.assertEqual(metadata_cdn_signer.calls, [])

    def test_internal_latest_fgb_download_returns_signed_gcs_url(self):
        catalog = catalog_payload()
        catalog["assets"][1]["access_tier"] = "internal"
        response, signer = download_url_request(
            "acled-europe-central-asia-aggregated-weekly-admin1",
            headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
            catalog=catalog,
        )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertTrue(payload["download_url"].startswith("https://storage.googleapis.com/"))
        self.assertEqual(payload["gs_uri"], PRIVATE_FGB_PATH.replace("/latest/", "/releases/2026-05-01/"))
        self.assertEqual(signer.calls[0][0], PRIVATE_FGB_PATH.replace("/latest/", "/releases/2026-05-01/"))

    def test_historical_fgb_download_uses_release_index(self):
        response, _signer = download_url_request("wdpa-marine", version="2026-05-01")

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["gs_uri"], PUBLIC_FGB_RELEASE_PATH)
        self.assertEqual(
            payload["download_url"],
            "https://tiles.skytruth.org/artifacts/100-geographic-reference/130-protected-areas/wdpa-marine/releases/2026-05-01/wdpa-marine.fgb?generation=1000",
        )

    def test_historical_fgb_download_rejects_release_missing_from_index(self):
        response, _signer = download_url_request("wdpa-marine", version="2026-04-01")

        self.assertEqual(response.status, 404)

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

    def test_metadata_download_uses_release_sidecar(self):
        response, signer = download_url_request("wdpa-marine", version="2026-05-01", fmt="metadata")

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["gs_uri"], PUBLIC_METADATA_RELEASE_PATH)
        self.assertEqual(
            payload["download_url"],
            "https://tiles.skytruth.org/artifacts/100-geographic-reference/130-protected-areas/wdpa-marine/releases/2026-05-01/wdpa-marine.metadata.ndjson.gz?generation=1001",
        )
        self.assertEqual(payload["filename"], "wdpa-marine.metadata.ndjson.gz")
        self.assertEqual(signer.calls, [])

    def test_metadata_download_rejects_old_feature_index_sidecar(self):
        store = FakeStore(catalog_payload())
        release_index = json.loads(store.static["releases/wdpa-marine.json"].body)
        release_index["releases"][0]["files"] = [
            file_entry
            for file_entry in release_index["releases"][0]["files"]
            if file_entry.get("locale") != "es" and file_entry.get("format") != "metadata"
        ]
        release_index["releases"][0]["files"].append(
            {
                "format": "feature_index",
                "path": (
                    "gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/"
                    "wdpa-marine/releases/2026-05-01/wdpa-marine.metadata.ndjson.gz"
                ),
            }
        )
        store.static["releases/wdpa-marine.json"] = StaticObject(
            json.dumps(release_index, separators=(",", ":")).encode("utf-8"),
            "application/json; charset=utf-8",
        )

        response, _signer = download_url_request(
            "wdpa-marine",
            version="2026-05-01",
            fmt="metadata",
            store=store,
        )

        self.assertEqual(response.status, 404)

    def test_metadata_download_latest_uses_release_index_latest(self):
        response, _signer = download_url_request("wdpa-marine", fmt="metadata")

        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.body)["gs_uri"], PUBLIC_METADATA_RELEASE_PATH)

    def test_public_schema_download_uses_release_schema(self):
        response, signer = download_url_request("wdpa-marine", version="2026-05-01", fmt="schema")

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["gs_uri"], PUBLIC_SCHEMA_RELEASE_PATH)
        self.assertEqual(
            payload["download_url"],
            "https://tiles.skytruth.org/artifacts/100-geographic-reference/130-protected-areas/wdpa-marine/releases/2026-05-01/wdpa-marine.schema.json?generation=1002",
        )
        self.assertEqual(payload["filename"], "wdpa-marine.schema.json")
        self.assertEqual(signer.calls, [])

    def test_private_schema_download_returns_signed_url(self):
        response, signer = download_url_request(
            "acled-europe-central-asia-aggregated-weekly-admin1",
            fmt="schema",
            headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
        )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertTrue(payload["download_url"].startswith("https://storage.googleapis.com/"))
        self.assertEqual(payload["expires_at"], "2026-05-09T12:15:00Z")
        self.assertEqual(payload["gs_uri"], PRIVATE_SCHEMA_RELEASE_PATH)
        self.assertEqual(payload["filename"], "acled-europe-central-asia-aggregated-weekly-admin1.schema.json")
        self.assertEqual(signer.calls[0][0], PRIVATE_SCHEMA_RELEASE_PATH)

    def test_metadata_download_uses_locale_sidecar_when_available(self):
        response, _signer = download_url_request("wdpa-marine", fmt="metadata", locale="es")

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["gs_uri"], PUBLIC_METADATA_ES_RELEASE_PATH)
        self.assertEqual(payload["filename"], "wdpa-marine.metadata.es.ndjson.gz")
        self.assertEqual(payload["requested_locale"], "es")
        self.assertEqual(payload["resolved_locale"], "es")
        self.assertFalse(payload["metadata_locale_fallback"])

    def test_metadata_download_falls_back_to_canonical_sidecar_when_locale_is_missing(self):
        response, _signer = download_url_request("wdpa-marine", fmt="metadata", locale="fr")

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["gs_uri"], PUBLIC_METADATA_RELEASE_PATH)
        self.assertEqual(payload["requested_locale"], "fr")
        self.assertIsNone(payload["resolved_locale"])
        self.assertTrue(payload["metadata_locale_fallback"])

    def test_private_metadata_download_returns_signed_url(self):
        catalog = catalog_payload()
        catalog["assets"][0]["access_tier"] = "private"
        response, signer = download_url_request(
            "wdpa-marine",
            version="2026-05-01",
            fmt="metadata",
            headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
            catalog=catalog,
        )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertTrue(payload["download_url"].startswith("https://storage.googleapis.com/signed-private.pmtiles"))
        self.assertEqual(payload["gs_uri"], PUBLIC_METADATA_RELEASE_PATH)
        self.assertEqual(signer.calls[0][0], PUBLIC_METADATA_RELEASE_PATH)

    def test_private_localized_metadata_download_uses_signed_cdn_url_when_configured(self):
        catalog = catalog_payload()
        catalog["assets"][0]["access_tier"] = "private"
        metadata_cdn_signer = FakeCdnSigner()
        response, signer = download_url_request(
            "wdpa-marine",
            version="2026-05-01",
            fmt="metadata",
            locale="es",
            headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
            catalog=catalog,
            metadata_cdn_signer=metadata_cdn_signer,
            metadata_cdn_ttl_seconds=120,
        )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertTrue(payload["download_url"].startswith("https://tiles.skytruth.org/private/"))
        self.assertIn("wdpa-marine.metadata.es.ndjson.gz", payload["download_url"])
        self.assertEqual(payload["expires_at"], "2026-05-09T12:02:00Z")
        self.assertEqual(payload["gs_uri"], PUBLIC_METADATA_ES_RELEASE_PATH)
        self.assertEqual(payload["filename"], "wdpa-marine.metadata.es.ndjson.gz")
        self.assertEqual(payload["requested_locale"], "es")
        self.assertEqual(payload["resolved_locale"], "es")
        self.assertFalse(payload["metadata_locale_fallback"])
        self.assertEqual(signer.calls, [])
        self.assertEqual(metadata_cdn_signer.calls[0][0], PUBLIC_METADATA_ES_RELEASE_PATH)

    def test_private_missing_locale_metadata_fallback_signs_canonical_cdn_url(self):
        catalog = catalog_payload()
        catalog["assets"][0]["access_tier"] = "private"
        metadata_cdn_signer = FakeCdnSigner()
        response, signer = download_url_request(
            "wdpa-marine",
            version="2026-05-01",
            fmt="metadata",
            locale="fr",
            headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
            catalog=catalog,
            metadata_cdn_signer=metadata_cdn_signer,
        )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertTrue(payload["download_url"].startswith("https://tiles.skytruth.org/private/"))
        self.assertIn("wdpa-marine.metadata.ndjson.gz", payload["download_url"])
        self.assertNotIn("metadata.fr.ndjson.gz", payload["download_url"])
        self.assertEqual(payload["gs_uri"], PUBLIC_METADATA_RELEASE_PATH)
        self.assertEqual(payload["requested_locale"], "fr")
        self.assertIsNone(payload["resolved_locale"])
        self.assertTrue(payload["metadata_locale_fallback"])
        self.assertEqual(signer.calls, [])
        self.assertEqual(metadata_cdn_signer.calls[0][0], PUBLIC_METADATA_RELEASE_PATH)

    def test_public_metadata_download_returns_artifact_cdn_url_without_private_signer(self):
        metadata_cdn_signer = FakeCdnSigner()
        response, signer = download_url_request(
            "wdpa-marine",
            version="2026-05-01",
            fmt="metadata",
            locale="es",
            metadata_cdn_signer=metadata_cdn_signer,
        )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(
            payload["download_url"],
            "https://tiles.skytruth.org/artifacts/100-geographic-reference/130-protected-areas/wdpa-marine/releases/2026-05-01/wdpa-marine.metadata.es.ndjson.gz?generation=1004",
        )
        self.assertEqual(payload["gs_uri"], PUBLIC_METADATA_ES_RELEASE_PATH)
        self.assertEqual(signer.calls, [])
        self.assertEqual(metadata_cdn_signer.calls, [])

    def test_private_metadata_cdn_download_rejects_invalid_locale_before_signing(self):
        catalog = catalog_payload()
        catalog["assets"][0]["access_tier"] = "private"
        metadata_cdn_signer = FakeCdnSigner()
        response, signer = download_url_request(
            "wdpa-marine",
            version="2026-05-01",
            fmt="metadata",
            locale="../es",
            headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
            catalog=catalog,
            metadata_cdn_signer=metadata_cdn_signer,
        )

        self.assertEqual(response.status, 400)
        self.assertEqual(signer.calls, [])
        self.assertEqual(metadata_cdn_signer.calls, [])

    def test_private_metadata_cdn_download_rejects_outside_bucket_sidecar_before_signing(self):
        catalog = catalog_payload()
        catalog["assets"][0]["access_tier"] = "private"
        store = FakeStore(catalog)
        release_index = json.loads(store.static["releases/wdpa-marine.json"].body)
        for file_entry in release_index["releases"][0]["files"]:
            if file_entry.get("locale") == "es":
                file_entry["path"] = "gs://other-bucket/wdpa-marine.metadata.es.ndjson.gz"
        store.static["releases/wdpa-marine.json"] = StaticObject(
            json.dumps(release_index, separators=(",", ":")).encode("utf-8"),
            "application/json; charset=utf-8",
        )
        metadata_cdn_signer = FakeCdnSigner()

        response, signer = download_url_request(
            "wdpa-marine",
            version="2026-05-01",
            fmt="metadata",
            locale="es",
            headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
            signer=FakeSigner(),
            metadata_cdn_signer=metadata_cdn_signer,
            catalog=catalog,
            store=store,
        )

        self.assertEqual(response.status, 502)
        self.assertEqual(signer.calls, [])
        self.assertEqual(metadata_cdn_signer.calls, [])

    def test_cloud_cdn_signed_url_signer_builds_private_metadata_url(self):
        signer = CloudCdnSignedUrlSigner(
            bucket_name="skytruth-shared-datasets-1",
            base_url="https://tiles.skytruth.org/artifacts",
            key_name="shared-datasets-pmtiles-v1",
            key=b"0123456789abcdef",
        )

        signed = signer.sign(PUBLIC_METADATA_ES_RELEASE_PATH, dt.datetime(2026, 5, 9, 12, 0, tzinfo=dt.UTC))

        self.assertTrue(
            signed.startswith(
                "https://tiles.skytruth.org/artifacts/100-geographic-reference/130-protected-areas/"
                "wdpa-marine/releases/2026-05-01/wdpa-marine.metadata.es.ndjson.gz?Expires=1778328000"
                "&KeyName=shared-datasets-pmtiles-v1&Signature="
            )
        )
        signature = urlsplit(signed).query.rsplit("Signature=", 1)[1]
        self.assertTrue(signature.endswith("="))
        self.assertNotIn("%3D", signature)
        self.assertNotIn("storage.googleapis.com", signed)

    def test_cloud_cdn_signed_url_signer_rejects_outside_bucket_paths(self):
        signer = CloudCdnSignedUrlSigner(
            bucket_name="skytruth-shared-datasets-1",
            base_url="https://tiles.skytruth.org/artifacts",
            key_name="shared-datasets-pmtiles-v1",
            key=b"0123456789abcdef",
        )

        with self.assertRaisesRegex(ValueError, "must be in gs://skytruth-shared-datasets-1/"):
            signer.sign(
                "gs://other-bucket/path/example.metadata.es.ndjson.gz",
                dt.datetime(2026, 5, 9, 12, 0, tzinfo=dt.UTC),
            )

    def test_cloud_cdn_signing_key_helpers_require_full_secret_version(self):
        self.assertEqual(decode_cdn_signing_key("MDEyMzQ1Njc4OWFiY2RlZg=="), b"0123456789abcdef")
        self.assertEqual(
            secret_manager_version_name("projects/shared-datasets-1/secrets/pmtiles-cdn-signed-request-key/versions/7"),
            "projects/shared-datasets-1/secrets/pmtiles-cdn-signed-request-key/versions/7",
        )

        with self.assertRaisesRegex(ValueError, "full Secret Manager version resource"):
            secret_manager_version_name("pmtiles-cdn-signed-request-key")

        with self.assertRaisesRegex(ValueError, "16 raw bytes"):
            decode_cdn_signing_key("c2hvcnQ=")

    def test_feature_metadata_lookup_requires_authenticated_iap_identity(self):
        response, resolver, index = feature_lookup_request({"ids": ["48943"]})

        self.assertEqual(response.status, 401)
        self.assertEqual(resolver.calls, [])
        self.assertEqual(index.calls, [])

    def test_feature_metadata_lookup_rejects_forwarded_email_without_iap_header(self):
        response, resolver, index = feature_lookup_request(
            {"ids": ["48943"]},
            {"X-Forwarded-Email": "jona@skytruth.org"},
        )

        self.assertEqual(response.status, 401)
        self.assertEqual(resolver.calls, [])
        self.assertEqual(index.calls, [])

    def test_feature_metadata_lookup_can_disable_iap_for_local_development(self):
        response, resolver, index = feature_lookup_request(
            {"ids": ["48943"], "include_provenance": True},
            feature_require_iap=False,
        )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["items"][0]["properties"]["GEONAME"], "Overlapping claim: Canada / United States")
        self.assertEqual(resolver.calls, [("wdpa-marine", "latest")])
        self.assertEqual(index.calls[0][2], ["48943"])

    def test_feature_metadata_lookup_delegates_to_preview_sidecar_index(self):
        response, resolver, index = feature_lookup_request(
            {"ids": ["48943"], "include_provenance": True},
            {"X-Goog-Authenticated-User-Email": "accounts.google.com:jona@skytruth.org"},
        )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["asset_slug"], "wdpa-marine")
        self.assertEqual(payload["requested_release"], "latest")
        self.assertEqual(payload["resolved_release"], "2026-05-01")
        self.assertEqual(payload["release_index_generation"], 12345)
        self.assertEqual(resolver.calls, [("wdpa-marine", "latest")])
        self.assertEqual(
            index.calls,
            [
                (
                    "wdpa-marine",
                    "2026-05-01",
                    ["48943"],
                    "gs://skytruth-shared-datasets-1-preview/100-geographic-reference/130-protected-areas/wdpa-marine/releases/2026-05-01/wdpa-marine.metadata.ndjson.gz",
                    1001,
                )
            ],
        )
        self.assertEqual(payload["items"][0]["feature_id"], "48943")
        self.assertTrue(payload["items"][0]["found"])
        self.assertEqual(payload["items"][0]["feature_id"], "48943")
        self.assertEqual(payload["items"][0]["properties_hash"], "hash-48943")
        self.assertEqual(payload["items"][0]["properties"]["GEONAME"], "Overlapping claim: Canada / United States")
        self.assertEqual(payload["items"][0]["properties"]["MRGID"], 48943)
        self.assertEqual(payload["items"][0]["provenance"]["source"], "preview sidecar")


if __name__ == "__main__":
    unittest.main()


def test_exact_release_service_shared_fixture_and_adverse_cases():
    import copy
    from services.catalog_viewer import run

    fixture = json.loads((Path(__file__).parent / "fixtures/historical-consumers.json").read_text())
    asset = fixture["asset"]
    index = fixture["index"]

    class Store:
        def read_catalog_json(self):
            return {"assets": [asset]}

        def read_static(self, name):
            assert name == "releases/example-layer.json"
            return StaticObject(json.dumps(index).encode(), "application/json")

    store = Store()
    signer = FakeSigner()

    def request(query, *, auth=True, download=False):
        endpoint = "/api/download-url" if download else "/api/pmtiles/signed-url"
        return handle_request("GET", endpoint + "?slug=example-layer&" + query,
                              {"X-Goog-Authenticated-User-Email": "accounts.google.com:tester@skytruth.org"} if auth else {},
                              catalog_cache=CatalogJsonCache(loader=store.read_catalog_json), object_store=store,
                              signer=signer, bucket_name="example-bucket")

    for tier in ["public", "private", "internal"]:
        asset["access_tier"] = tier
        response = request("version=2026-01-01&generation=101")
        assert response.status == 200
        payload = json.loads(response.body)
        assert payload["resolved_release"] == "2026-01-01"
        assert payload["generation"] == "101"
        assert "/releases/2026-01-01/" in payload["gs_uri"]
        if tier == "public":
            assert payload["pmtiles_url"].endswith("?generation=101")
        else:
            assert signer.calls[-1][2] == "101"
            before = len(signer.calls)
            assert request("version=2026-01-01&generation=101", auth=False).status == 401
            assert len(signer.calls) == before
    assert request("version=2025-01-01").status == 404
    for query in ["version=2026-01-01&generation=999", "version=latest&generation=101"]:
        assert request(query).status == 409
    for query in ["version=bad", "version=2026-02-30", "generation=true", "generation=01", "generation=1&generation=2", "uri=gs://example-bucket/secret"]:
        assert request(query).status == 400
    for fmt, generation in [("fgb", 100), ("metadata", 102), ("schema", 103)]:
        assert request(f"version=2026-01-01&format={fmt}&generation={generation}", download=True).status == 200
        assert signer.calls[-1][2] == str(generation)
        assert request(f"version=2026-01-01&format={fmt}&generation=999", download=True).status == 409

    pristine = copy.deepcopy(index)
    for bad in [True, False, None, 0, -1, 1.5, "", "01", 2**64]:
        index["releases"][1]["files"][1]["generation"] = bad
        assert request("version=2026-01-01").status == 502
    index = copy.deepcopy(pristine)
    # Later publication replaces one object at the same date.
    index["releases"][1]["files"][1]["generation"] = 901
    assert request("version=2026-01-01&generation=101").status == 409
    assert request("version=2026-01-01&generation=901").status == 200
    index = copy.deepcopy(pristine)
    extra = {**index["releases"][1]["files"][1], "path": index["releases"][1]["files"][1]["path"].replace(".pmtiles", "-points.pmtiles")}
    index["releases"][1]["files"].insert(0, extra)
    assert request("version=2026-01-01&generation=101").status == 200
    asset["pmtiles_path"] = asset["pmtiles_path"].replace("example-layer.pmtiles", "missing.pmtiles")
    assert request("version=2026-01-01").status == 502
    index = copy.deepcopy(pristine)
    index["releases"][1]["files"][1]["path"] = "gs://example-bucket/secret.pmtiles"
    assert request("version=2026-01-01").status == 502
    index = copy.deepcopy(pristine)
    index["releases"][1]["files"] = [f for f in index["releases"][1]["files"] if f["format"] != "metadata"]
    response = request("version=2026-01-01&format=metadata", download=True)
    assert response.status == 404 and "does not include metadata" in response.body.decode()
    assert run.static_object_name("/release-reference.js") == "release-reference.js"


def test_signed_generation_is_inside_gcs_and_cdn_signature():
    import base64
    import hashlib
    import hmac
    from types import SimpleNamespace
    from unittest.mock import patch
    from services.catalog_viewer import run

    calls = []
    blob = SimpleNamespace(generate_signed_url=lambda **kwargs: calls.append(kwargs) or "https://signed.test/exact")
    client = SimpleNamespace(bucket=lambda name: SimpleNamespace(blob=lambda name: blob))
    gcs = run.GcsV4UrlSigner(bucket_name="example-bucket", client=client,
                             credentials=SimpleNamespace(service_account_email="signer@example.test"))
    instant = dt.datetime(2026, 9, 22, tzinfo=dt.UTC)
    with patch.object(run, "credentials_support_direct_signing", return_value=True):
        gcs.sign("gs://example-bucket/a.pmtiles", instant, generation="101")
    assert calls[0]["query_parameters"] == {"generation": "101"}
    cdn = run.CloudCdnSignedUrlSigner(bucket_name="example-bucket", base_url="https://tiles.test/private", key_name="key", key=b"0123456789abcdef")
    signed = cdn.sign("gs://example-bucket/a.metadata.ndjson.gz", instant, generation="102")
    unsigned, signature = signed.rsplit("&Signature=", 1)
    assert "?generation=102&Expires=" in unsigned
    assert signature == base64.urlsafe_b64encode(hmac.new(b"0123456789abcdef", unsigned.encode(), hashlib.sha1).digest()).decode()
