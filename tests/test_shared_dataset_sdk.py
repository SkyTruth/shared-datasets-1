from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError


REPO_ROOT = Path(__file__).resolve().parents[1]
SDK_SRC = REPO_ROOT / "api/python/src"
if str(SDK_SRC) not in sys.path:
    sys.path.insert(0, str(SDK_SRC))

from skytruth_shared_datasets import (  # noqa: E402
    Catalog,
    CatalogLoadError,
    DatasetNotFoundError,
    FetchError,
    UnsupportedFormatError,
    UnsupportedVersionError,
    gs_to_https,
    split_gs_uri,
)


FIXTURE_CSV = """asset_slug,title,category,subcategory,status,owner,update_cadence,canonical_path,canonical_format,available_formats,metadata_paths,has_pmtiles,has_geojson,has_csv,last_updated,source,license,notes
example-vector,Example Vector,100-geographic-reference,110-boundaries,active,SkyTruth,manual,gs://example-bucket/100-geographic-reference/110-boundaries/example-vector/latest/example-vector.fgb,fgb,fgb;pmtiles;geojson,README.md,true,true,false,2026-04-30,Example source,Example license,Example notes
example-table,Example Table,700-non-geographic-reference,730-units-codes-lookups,deprecated,SkyTruth,manual,gs://example-bucket/700-non-geographic-reference/730-units-codes-lookups/example-table/latest/example-table.csv,csv,csv,README.md,false,false,true,2026-04-29,Example table source,Example license,Deprecated table
"""


class FakeGcsBlob:
    def __init__(self, *, text: str = "", content: bytes = b"") -> None:
        self.text = text
        self.content = content
        self.downloads = []

    def download_as_text(self, timeout):
        self.downloads.append(("text", timeout))
        return self.text

    def download_to_filename(self, filename, timeout):
        self.downloads.append(("file", timeout))
        Path(filename).write_bytes(self.content)


class FakeGcsBucket:
    def __init__(self, client, bucket_name: str) -> None:
        self.client = client
        self.bucket_name = bucket_name

    def blob(self, object_name: str) -> FakeGcsBlob:
        self.client.requests.append((self.bucket_name, object_name))
        return self.client.blobs[(self.bucket_name, object_name)]


class FakeGcsClient:
    def __init__(self, blobs: dict[tuple[str, str], FakeGcsBlob]) -> None:
        self.blobs = blobs
        self.requests: list[tuple[str, str]] = []

    def bucket(self, bucket_name: str) -> FakeGcsBucket:
        return FakeGcsBucket(self, bucket_name)


class SharedDatasetSdkTests(unittest.TestCase):
    def test_packaged_catalog_source_is_not_supported(self):
        with self.assertRaises(CatalogLoadError) as raised:
            Catalog.load(source="packaged")

        self.assertIn("Packaged catalog snapshots are no longer shipped", str(raised.exception))

    def test_loads_catalog_from_local_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.csv"
            path.write_text(FIXTURE_CSV)

            catalog = Catalog.load(source=path)

        self.assertEqual(catalog.get("example-vector").title, "Example Vector")
        self.assertEqual(catalog.source, str(path))

    def test_loads_catalog_from_mocked_public_url(self):
        with mock.patch("skytruth_shared_datasets.catalog.urlopen", return_value=io.BytesIO(FIXTURE_CSV.encode())):
            catalog = Catalog.load(source="https://example.test/catalog.csv")

        self.assertEqual(catalog.get("example-vector").canonical_format, "fgb")

    def test_loads_catalog_from_mocked_gcs_client(self):
        client = FakeGcsClient({("example-bucket", "catalog.csv"): FakeGcsBlob(text=FIXTURE_CSV)})

        catalog = Catalog.load_gcs("gs://example-bucket/catalog.csv", client=client)

        self.assertEqual(catalog.get("example-vector").canonical_format, "fgb")
        self.assertEqual(catalog.source, "gs://example-bucket/catalog.csv")
        self.assertEqual(client.requests, [("example-bucket", "catalog.csv")])

    def test_default_load_raises_when_public_url_fails(self):
        with mock.patch("skytruth_shared_datasets.catalog.urlopen", side_effect=OSError("offline")):
            with self.assertRaises(CatalogLoadError) as raised:
                Catalog.load()
        self.assertIn("offline", str(raised.exception))

    def test_default_load_raises_on_malformed_live_catalog(self):
        malformed_catalog = b"not,catalog\nx,y\n"
        with mock.patch("skytruth_shared_datasets.catalog.urlopen", return_value=io.BytesIO(malformed_catalog)):
            with self.assertRaises(CatalogLoadError):
                Catalog.load()

    def test_default_load_raises_on_catalog_permission_failures(self):
        for status_code in (403, 404):
            with self.subTest(status_code=status_code):
                error = HTTPError(
                    url="https://storage.googleapis.com/example/catalog.csv",
                    code=status_code,
                    msg="blocked",
                    hdrs=None,
                    fp=None,
                )
                with mock.patch("skytruth_shared_datasets.catalog.urlopen", side_effect=error):
                    with self.assertRaises(CatalogLoadError) as raised:
                        Catalog.load()
                self.assertIn("Catalog.load_gcs()", str(raised.exception))

    def test_get_and_search_filter_catalog_assets(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV)

        self.assertEqual(catalog.get("example-vector").slug, "example-vector")
        self.assertEqual([asset.slug for asset in catalog.search(category="100-geographic-reference")], ["example-vector"])
        self.assertEqual([asset.slug for asset in catalog.search(format="pmtiles")], ["example-vector"])
        self.assertEqual([asset.slug for asset in catalog.search(status=None)], ["example-vector", "example-table"])
        with self.assertRaises(DatasetNotFoundError):
            catalog.get("missing")

    def test_gs_to_https_converts_and_escapes_object_names(self):
        url = gs_to_https("gs://bucket/path with spaces/object.fgb")

        self.assertEqual(url, "https://storage.googleapis.com/bucket/path%20with%20spaces/object.fgb")

    def test_resolve_supports_canonical_and_companion_formats(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV)

        fgb = catalog.resolve("example-vector", format="fgb")
        pmtiles = catalog.resolve("example-vector", format="pmtiles")
        geojson = catalog.resolve("example-vector", format="geojson")

        self.assertEqual(fgb.gs_uri, "gs://example-bucket/100-geographic-reference/110-boundaries/example-vector/latest/example-vector.fgb")
        self.assertEqual(pmtiles.gs_uri, "gs://example-bucket/100-geographic-reference/110-boundaries/example-vector/latest/example-vector.pmtiles")
        self.assertEqual(geojson.url, "https://storage.googleapis.com/example-bucket/100-geographic-reference/110-boundaries/example-vector/latest/example-vector.geojson")
        with self.assertRaises(UnsupportedFormatError):
            catalog.resolve("example-vector", format="csv")
        with self.assertRaises(UnsupportedVersionError):
            catalog.resolve("example-vector", version="2026-04-30")

    def test_resolve_cdn_url_keeps_canonical_gs_uri(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV)

        pmtiles = catalog.resolve("example-vector", format="pmtiles", web_base_url="/pmtiles")

        self.assertEqual(pmtiles.gs_uri, "gs://example-bucket/100-geographic-reference/110-boundaries/example-vector/latest/example-vector.pmtiles")
        self.assertEqual(pmtiles.url, "/pmtiles/example-vector.pmtiles")

    def test_fetch_downloads_to_cache_and_reuses_cache(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV)
        calls = []

        def fake_urlopen(request, timeout):
            calls.append((request.full_url, timeout))
            return io.BytesIO(b"dataset bytes")

        with tempfile.TemporaryDirectory() as tmp, mock.patch("skytruth_shared_datasets.catalog.urlopen", side_effect=fake_urlopen):
            first = catalog.fetch("example-vector", format="fgb", cache_dir=tmp)
            second = catalog.fetch("example-vector", format="fgb", cache_dir=tmp)
            forced = catalog.fetch("example-vector", format="fgb", cache_dir=tmp, force=True)

            self.assertEqual(first, second)
            self.assertEqual(first, forced)
            self.assertEqual(first.read_bytes(), b"dataset bytes")
            self.assertEqual(len(calls), 2)
            self.assertEqual(first.name, "example-vector.fgb")
            self.assertIn("2026-04-30", first.parts)

    def test_fetch_downloads_from_mocked_gcs_client(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV)
        ref = catalog.resolve("example-vector", format="fgb")
        bucket_name, object_name = split_gs_uri(ref.gs_uri)
        blob = FakeGcsBlob(content=b"gcs dataset bytes")
        client = FakeGcsClient({(bucket_name, object_name): blob})

        with tempfile.TemporaryDirectory() as tmp:
            path = catalog.fetch("example-vector", format="fgb", cache_dir=tmp, access="gcs", client=client)
            self.assertEqual(path.read_bytes(), b"gcs dataset bytes")

        self.assertEqual(client.requests, [(bucket_name, object_name)])
        self.assertEqual(blob.downloads, [("file", 60.0)])

    def test_fetch_reports_download_failures(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV)
        with tempfile.TemporaryDirectory() as tmp, mock.patch("skytruth_shared_datasets.catalog.urlopen", side_effect=OSError("failed")):
            with self.assertRaises(FetchError):
                catalog.fetch("example-vector", format="fgb", cache_dir=tmp)

    def test_real_repo_catalog_parses_and_active_assets_resolve(self):
        catalog = Catalog.load(source=REPO_ROOT / "catalog/shared-datasets-catalog.csv")

        active_assets = catalog.search(status="active")

        self.assertGreater(len(active_assets), 0)
        for asset in active_assets:
            ref = catalog.resolve(asset.slug)
            self.assertTrue(ref.gs_uri.startswith("gs://"))
            self.assertTrue(ref.url.startswith("https://storage.googleapis.com/"))


if __name__ == "__main__":
    unittest.main()
