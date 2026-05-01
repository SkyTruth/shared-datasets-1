from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SDK_SRC = REPO_ROOT / "api/python/src"
if str(SDK_SRC) not in sys.path:
    sys.path.insert(0, str(SDK_SRC))

from skytruth_shared_datasets import (  # noqa: E402
    Catalog,
    DatasetNotFoundError,
    FetchError,
    UnsupportedFormatError,
    UnsupportedVersionError,
    gs_to_https,
)


FIXTURE_CSV = """asset_slug,title,category,subcategory,status,owner,update_cadence,canonical_path,canonical_format,available_formats,metadata_paths,has_pmtiles,has_geojson,has_csv,last_updated,source,license,notes
example-vector,Example Vector,100-geographic-reference,110-boundaries,active,SkyTruth,manual,gs://example-bucket/100-geographic-reference/110-boundaries/example-vector/latest/example-vector.fgb,fgb,fgb;pmtiles;geojson,README.md,true,true,false,2026-04-30,Example source,Example license,Example notes
example-table,Example Table,700-non-geographic-reference,730-units-codes-lookups,deprecated,SkyTruth,manual,gs://example-bucket/700-non-geographic-reference/730-units-codes-lookups/example-table/latest/example-table.csv,csv,csv,README.md,false,false,true,2026-04-29,Example table source,Example license,Deprecated table
"""


class SharedDatasetSdkTests(unittest.TestCase):
    def test_loads_catalog_from_packaged_snapshot(self):
        catalog = Catalog.load(source="packaged")

        asset = catalog.get("wdpa-marine")

        self.assertEqual(asset.canonical_format, "fgb")
        self.assertIn("pmtiles", asset.available_formats)
        self.assertEqual(catalog.source, "packaged")

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

    def test_default_load_falls_back_to_packaged_snapshot_when_public_url_fails(self):
        with mock.patch("skytruth_shared_datasets.catalog.urlopen", side_effect=OSError("offline")):
            catalog = Catalog.load()

        self.assertEqual(catalog.source, "packaged")
        self.assertIn("wdpa-marine", catalog.slugs)

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

    def test_fetch_reports_download_failures(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV)
        with tempfile.TemporaryDirectory() as tmp, mock.patch("skytruth_shared_datasets.catalog.urlopen", side_effect=OSError("failed")):
            with self.assertRaises(FetchError):
                catalog.fetch("example-vector", format="fgb", cache_dir=tmp)

    def test_real_repo_catalog_parses_and_active_assets_resolve(self):
        catalog = Catalog.load(source=REPO_ROOT / "catalog/shared-datasets-catalog.csv")
        packaged = Catalog.load(source="packaged")

        active_assets = catalog.search(status="active")

        self.assertEqual(packaged.slugs, catalog.slugs)
        self.assertGreater(len(active_assets), 0)
        for asset in active_assets:
            ref = catalog.resolve(asset.slug)
            self.assertTrue(ref.gs_uri.startswith("gs://"))
            self.assertTrue(ref.url.startswith("https://storage.googleapis.com/"))


if __name__ == "__main__":
    unittest.main()
