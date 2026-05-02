from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
SDK_SRC = REPO_ROOT / "api/python/src"
if str(SDK_SRC) not in sys.path:
    sys.path.insert(0, str(SDK_SRC))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from skytruth_shared_datasets import DatasetNotFoundError  # noqa: E402

from services.pmtiles_redirector.run import CatalogCache, handle_request  # noqa: E402


class FakeCatalog:
    def __init__(self, urls: dict[str, str]) -> None:
        self.urls = urls

    def resolve(self, slug: str, *, format: str, url_strategy: str):
        self.calls = getattr(self, "calls", [])
        self.calls.append((slug, format, url_strategy))
        if slug not in self.urls:
            raise DatasetNotFoundError(slug)
        return SimpleNamespace(url=self.urls[slug])


class PmtilesRedirectorTests(unittest.TestCase):
    def test_valid_asset_redirects_to_public_gcs(self):
        catalog = FakeCatalog(
            {
                "wdpa-marine": "https://storage.googleapis.com/skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/wdpa-marine/latest/wdpa-marine.pmtiles"
            }
        )
        cache = CatalogCache(loader=lambda: catalog)

        response = handle_request(
            "GET",
            "/pmtiles/wdpa-marine.pmtiles",
            {"Origin": "https://cerulean.skytruth.org"},
            catalog_cache=cache,
        )

        self.assertEqual(response.status, 307)
        self.assertEqual(response.headers["Location"], catalog.urls["wdpa-marine"])
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "https://cerulean.skytruth.org")
        self.assertEqual(catalog.calls, [("wdpa-marine", "pmtiles", "public_gcs")])

    def test_unknown_asset_returns_404(self):
        cache = CatalogCache(loader=lambda: FakeCatalog({}))

        response = handle_request("GET", "/pmtiles/missing.pmtiles", {}, catalog_cache=cache)

        self.assertEqual(response.status, 404)

    def test_malformed_path_returns_404(self):
        cache = CatalogCache(loader=lambda: FakeCatalog({}))

        response = handle_request("GET", "/pmtiles/WDPA.pmtiles", {}, catalog_cache=cache)

        self.assertEqual(response.status, 404)

    def test_options_allows_range_header(self):
        cache = CatalogCache(loader=lambda: FakeCatalog({}))

        response = handle_request(
            "OPTIONS",
            "/pmtiles/wdpa-marine.pmtiles",
            {"Origin": "https://cerulean.skytruth.org"},
            catalog_cache=cache,
        )

        self.assertEqual(response.status, 204)
        self.assertEqual(response.headers["Access-Control-Allow-Headers"], "Range")
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "https://cerulean.skytruth.org")

    def test_catalog_load_failure_returns_503_without_cached_catalog(self):
        cache = CatalogCache(loader=lambda: (_ for _ in ()).throw(RuntimeError("offline")))

        response = handle_request("GET", "/pmtiles/wdpa-marine.pmtiles", {}, catalog_cache=cache)

        self.assertEqual(response.status, 503)

    def test_catalog_load_failure_reuses_cached_catalog(self):
        catalog = FakeCatalog({"wdpa-marine": "https://storage.googleapis.com/example/wdpa-marine.pmtiles"})
        calls = iter([catalog, RuntimeError("offline")])

        def loader():
            value = next(calls)
            if isinstance(value, Exception):
                raise value
            return value

        cache = CatalogCache(loader=loader, ttl_seconds=0)

        first = handle_request("GET", "/pmtiles/wdpa-marine.pmtiles", {}, catalog_cache=cache)
        second = handle_request("GET", "/pmtiles/wdpa-marine.pmtiles", {}, catalog_cache=cache)

        self.assertEqual(first.status, 307)
        self.assertEqual(second.status, 307)


if __name__ == "__main__":
    unittest.main()
