from __future__ import annotations

import concurrent.futures
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
SDK_SRC = REPO_ROOT / "api/python/src"
if str(SDK_SRC) not in sys.path:
    sys.path.insert(0, str(SDK_SRC))

from skytruth_shared_datasets import (  # noqa: E402
    Catalog,
    CatalogLoadError,
    DEFAULT_CATALOG_URL,
    DEFAULT_PMTILES_CDN_BASE_URL,
    DatasetRef,
    DatasetNotFoundError,
    FetchError,
    UnsupportedFormatError,
    UnsupportedVersionError,
    fetch_dataset,
    gs_to_catalog_url,
    gs_to_https,
    resolve_dataset,
    split_gs_uri,
)
from skytruth_shared_datasets import cli as sdk_cli  # noqa: E402
from skytruth_shared_datasets import catalog as sdk  # noqa: E402


FIXTURE_CSV = """asset_slug,title,category,subcategory,status,lifecycle_reason,lifecycle_date,successor_asset_slug,consumer_guidance,access_tier,owner,update_cadence,canonical_path,canonical_format,available_formats,metadata_paths,localized_name_locales,localized_name_review_states,has_pmtiles,has_geojson,has_csv,source,license,citation,notes
example-vector,Example Vector,100-geographic-reference,110-boundaries,active,,,,,public,SkyTruth,manual,gs://example-bucket/100-geographic-reference/110-boundaries/example-vector/latest/example-vector.fgb,fgb,fgb;pmtiles;geojson,README.md,en;es,en:source_provided;es:machine_translated,true,true,false,Example source,Example license,Example citation,Example notes
example-table,Example Table,700-non-geographic-reference,730-units-codes-lookups,deprecated,Stale source,2026-05-08,,Use example-vector for new work,public,SkyTruth,manual,gs://example-bucket/700-non-geographic-reference/730-units-codes-lookups/example-table/latest/example-table.csv,csv,csv,README.md,,,false,false,true,Example table source,Example license,Example table citation,Deprecated table
"""


class StorageError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(f"storage HTTP {code}")


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, **headers):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload)), **headers}


class FakeGcsBlob:
    def __init__(self, *, text: str = "", content: bytes = b"", generation: int = 1, error=None) -> None:
        self.text = text
        self.content = content
        self.generation = generation
        self.error = error
        self.downloads = []
        self.preconditions = []

    @property
    def size(self):
        return len(self.text.encode() if self.text else self.content)

    def reload(self, *, timeout):
        if self.error:
            raise self.error

    def download_as_text(self, timeout, *, if_generation_match=None):
        self._check(if_generation_match)
        self.downloads.append(("text", timeout))
        return self.text

    def download_to_filename(self, filename, timeout, *, if_generation_match, raw_download):
        self._check(if_generation_match)
        assert raw_download is True
        self.downloads.append(("file", timeout))
        Path(filename).write_bytes(self.content)

    def _check(self, generation):
        if self.error:
            raise self.error
        if generation is not None:
            self.preconditions.append(generation)
            if generation != self.generation:
                raise StorageError(412)


class FakeGcsBucket:
    def __init__(self, client, bucket_name: str) -> None:
        self.client = client
        self.bucket_name = bucket_name

    def blob(self, object_name: str, *, generation=None) -> FakeGcsBlob:
        self.client.requests.append((self.bucket_name, object_name))
        self.client.bindings.append((self.bucket_name, object_name, generation))
        blob = self.client.blobs.get((self.bucket_name, object_name), FakeGcsBlob(error=StorageError(404)))
        if generation is not None and generation != blob.generation:
            return FakeGcsBlob(error=StorageError(404))
        return blob


class FakeGcsClient:
    def __init__(self, blobs: dict[tuple[str, str], FakeGcsBlob]) -> None:
        self.blobs = blobs
        self.requests: list[tuple[str, str]] = []
        self.bindings = []

    def bucket(self, bucket_name: str) -> FakeGcsBucket:
        return FakeGcsBucket(self, bucket_name)


class SharedDatasetSdkTests(unittest.TestCase):
    def test_packaged_catalog_source_is_treated_as_a_regular_path(self):
        with self.assertRaises(CatalogLoadError) as raised:
            Catalog.load(source="packaged")

        self.assertIn("Could not load catalog from packaged", str(raised.exception))

    def test_loads_catalog_from_local_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.csv"
            path.write_text(FIXTURE_CSV)

            catalog = Catalog.load(source=path)

        self.assertEqual(catalog.get("example-vector").title, "Example Vector")
        self.assertEqual(catalog.get("example-vector").citation, "Example citation")
        self.assertEqual(catalog.get("example-vector").localized_name_locales, ("en", "es"))
        self.assertEqual(
            dict(catalog.get("example-vector").localized_name_review_states),
            {"en": "source_provided", "es": "machine_translated"},
        )
        self.assertEqual(catalog.get("example-table").localized_name_locales, ())
        self.assertEqual(dict(catalog.get("example-table").localized_name_review_states), {})
        self.assertEqual(catalog.get("example-table").lifecycle_reason, "Stale source")
        self.assertEqual(catalog.get("example-table").consumer_guidance, "Use example-vector for new work")
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

    def test_load_gcs_failure_mentions_adc_service_account_setup(self):
        with mock.patch("skytruth_shared_datasets.catalog._read_gcs_text", side_effect=RuntimeError("denied")):
            with self.assertRaises(CatalogLoadError) as raised:
                Catalog.load_gcs("gs://example-bucket/catalog.csv")

        message = str(raised.exception)
        self.assertIn("Application Default Credentials", message)
        self.assertIn("roles/storage.objectViewer", message)
        self.assertIn("do not use service account JSON keys", message)

    def test_default_load_raises_when_public_url_fails(self):
        with mock.patch("skytruth_shared_datasets.catalog.urlopen", side_effect=OSError("offline")):
            with self.assertRaises(CatalogLoadError) as raised:
                Catalog.load()
        self.assertIn("offline", str(raised.exception))

    def test_default_catalog_url_uses_tiles_endpoint(self):
        calls = []

        def fake_urlopen(request, timeout):
            calls.append((request.full_url, timeout))
            return io.BytesIO(FIXTURE_CSV.encode())

        with mock.patch("skytruth_shared_datasets.catalog.urlopen", side_effect=fake_urlopen):
            Catalog.load()

        self.assertEqual(DEFAULT_CATALOG_URL, "https://tiles.skytruth.org/_catalog/shared-datasets-catalog.csv")
        self.assertEqual(calls, [(DEFAULT_CATALOG_URL, 10.0)])

    def test_default_load_raises_on_malformed_live_catalog(self):
        malformed_catalog = b"not,catalog\nx,y\n"
        with mock.patch("skytruth_shared_datasets.catalog.urlopen", return_value=io.BytesIO(malformed_catalog)):
            with self.assertRaises(CatalogLoadError):
                Catalog.load()

    def test_catalog_rows_require_explicit_access_tier_and_available_formats(self):
        missing_access_tier = FIXTURE_CSV.replace(
            "active,,,,,public,SkyTruth",
            "active,,,,,,SkyTruth",
            1,
        )
        with self.assertRaisesRegex(CatalogLoadError, "access_tier"):
            Catalog.from_csv_text(missing_access_tier)

        missing_canonical_format = FIXTURE_CSV.replace(
            "fgb,fgb;pmtiles;geojson",
            "fgb,pmtiles;geojson",
            1,
        )
        with self.assertRaisesRegex(CatalogLoadError, "available_formats"):
            Catalog.from_csv_text(missing_canonical_format)

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
        internal_catalog = Catalog.from_csv_text(FIXTURE_CSV.replace(",public,SkyTruth,manual", ",internal,SkyTruth,manual", 1))

        self.assertEqual(catalog.get("example-vector").slug, "example-vector")
        self.assertEqual([asset.slug for asset in catalog.search(category="100-geographic-reference")], ["example-vector"])
        self.assertEqual([asset.slug for asset in catalog.search(format="pmtiles")], ["example-vector"])
        self.assertEqual(catalog.search(format=".fgb"), [])
        self.assertEqual([asset.slug for asset in catalog.search(access_tier="public")], ["example-vector"])
        self.assertEqual([asset.slug for asset in internal_catalog.search(access_tier="internal")], ["example-vector"])
        self.assertEqual([asset.slug for asset in catalog.search(status=None)], ["example-vector", "example-table"])
        with self.assertRaises(DatasetNotFoundError):
            catalog.get("missing")

    def test_gs_to_https_converts_and_escapes_object_names(self):
        url = gs_to_https("gs://bucket/path with spaces/object.fgb")

        self.assertEqual(url, "https://storage.googleapis.com/bucket/path%20with%20spaces/object.fgb")

    def test_gs_to_catalog_url_uses_tiles_for_shared_catalog_objects(self):
        url = gs_to_catalog_url("gs://skytruth-shared-datasets-1/_catalog/releases/example asset.json")

        self.assertEqual(url, "https://tiles.skytruth.org/_catalog/releases/example%20asset.json")

    def test_resolve_derives_advertised_latest_companion_paths(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV)

        fgb = catalog.resolve("example-vector", format="fgb")
        pmtiles = catalog.resolve("example-vector", format="pmtiles")
        geojson = catalog.resolve("example-vector", format="geojson")

        self.assertEqual(fgb.gs_uri, "gs://example-bucket/100-geographic-reference/110-boundaries/example-vector/latest/example-vector.fgb")
        self.assertEqual(pmtiles.gs_uri, "gs://example-bucket/100-geographic-reference/110-boundaries/example-vector/latest/example-vector.pmtiles")
        self.assertEqual(pmtiles.url, "https://tiles.skytruth.org/pmtiles/public/example-vector.pmtiles")
        self.assertEqual(geojson.gs_uri, "gs://example-bucket/100-geographic-reference/110-boundaries/example-vector/latest/example-vector.geojson")
        self.assertIsNone(fgb.cache_path)
        self.assertEqual(fgb.resolved_id, "example-vector@latest")
        with self.assertRaises(UnsupportedFormatError):
            catalog.resolve("example-vector", format=".fgb")
        with self.assertRaises(UnsupportedFormatError):
            catalog.resolve("example-vector", format="csv")
        with self.assertRaises(UnsupportedVersionError):
            catalog.resolve("example-vector", version="2026-4-30")

    def test_latest_noncanonical_zarr_remains_non_inferable(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV.replace("fgb;pmtiles;geojson", "fgb;zarr"))

        with self.assertRaisesRegex(UnsupportedFormatError, "cannot be inferred"):
            catalog.resolve("example-vector", format="zarr")

    def test_latest_companion_requires_canonical_latest_root(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV.replace("/latest/example-vector.fgb", "/releases/2026-01-01/example-vector.fgb"))

        with self.assertRaisesRegex(ValueError, "canonical_path must contain"):
            catalog.resolve("example-vector", format="pmtiles")

    def test_every_real_catalog_pmtiles_row_resolves_to_slug_latest_path(self):
        catalog = Catalog.load(REPO_ROOT / "catalog/shared-datasets-catalog.csv")
        pmtiles_assets = catalog.search(format="pmtiles", status=None)

        self.assertTrue(pmtiles_assets)
        for asset in pmtiles_assets:
            with self.subTest(asset=asset.slug):
                ref = catalog.resolve(asset.slug, format="pmtiles")
                self.assertEqual(ref.filename, f"{asset.slug}.pmtiles")
                self.assertIn(f"/{asset.slug}/latest/{asset.slug}.pmtiles", ref.gs_uri)
                self.assertEqual(
                    ref.url,
                    f"https://tiles.skytruth.org/pmtiles/{asset.access_tier}/{asset.slug}.pmtiles",
                )

    def test_resolve_cdn_url_keeps_canonical_gs_uri(self):
        pmtiles_csv = FIXTURE_CSV.replace(
            "example-vector/latest/example-vector.fgb,fgb,fgb;pmtiles;geojson",
            "example-vector/latest/example-vector.pmtiles,pmtiles,pmtiles",
            1,
        )
        catalog = Catalog.from_csv_text(pmtiles_csv)

        pmtiles = catalog.resolve("example-vector", format="pmtiles", web_base_url="/pmtiles")

        self.assertEqual(
            pmtiles.gs_uri,
            "gs://example-bucket/100-geographic-reference/110-boundaries/example-vector/latest/example-vector.pmtiles",
        )
        self.assertEqual(pmtiles.url, "/pmtiles/public/example-vector.pmtiles")

    def test_resolve_cdn_url_uses_internal_tier_path(self):
        pmtiles_csv = FIXTURE_CSV.replace(
            "example-vector/latest/example-vector.fgb,fgb,fgb;pmtiles;geojson",
            "example-vector/latest/example-vector.pmtiles,pmtiles,pmtiles",
            1,
        ).replace(",public,SkyTruth,manual", ",internal,SkyTruth,manual", 1)
        catalog = Catalog.from_csv_text(pmtiles_csv)

        pmtiles = catalog.resolve("example-vector", format="pmtiles", web_base_url="/pmtiles")

        self.assertEqual(pmtiles.access_tier, "internal")
        self.assertEqual(pmtiles.url, "/pmtiles/internal/example-vector.pmtiles")

    def test_resolve_pmtiles_can_force_public_gcs_url(self):
        pmtiles_csv = FIXTURE_CSV.replace(
            "example-vector/latest/example-vector.fgb,fgb,fgb;pmtiles;geojson",
            "example-vector/latest/example-vector.pmtiles,pmtiles,pmtiles",
            1,
        )
        catalog = Catalog.from_csv_text(pmtiles_csv)

        pmtiles = catalog.resolve("example-vector", format="pmtiles", url_strategy="public_gcs")

        self.assertEqual(
            pmtiles.url,
            "https://storage.googleapis.com/example-bucket/100-geographic-reference/110-boundaries/example-vector/latest/example-vector.pmtiles",
        )

    def test_versions_and_dated_resolve_use_release_index(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV)
        release_index = {
            "asset_slug": "example-vector",
            "latest_release": {"date": "2026-04-30"},
            "latest_run": {"date": "2026-04-30", "status": "success"},
            "releases": [
                {
                    "date": "2026-04-30",
                    "release_path": "gs://example-bucket/100-geographic-reference/110-boundaries/example-vector/releases/2026-04-30/",
                    "files": [
                        {
                            "format": "fgb",
                            "path": "gs://example-bucket/100-geographic-reference/110-boundaries/example-vector/releases/2026-04-30/example-vector.fgb",
                        },
                        {
                            "format": "pmtiles",
                            "path": "gs://example-bucket/100-geographic-reference/110-boundaries/example-vector/releases/2026-04-30/example-vector.pmtiles",
                        },
                    ],
                }
            ],
        }
        client = FakeGcsClient(
            {
                ("example-bucket", "_catalog/releases/example-vector.json"): FakeGcsBlob(
                    text=json.dumps(release_index)
                )
            }
        )

        versions = catalog.versions("example-vector", access="gcs", client=client)
        ref = catalog.resolve(
            "example-vector",
            format="pmtiles",
            version="2026-04-30",
            access="gcs",
            client=client,
            web_base_url=None,
        )

        self.assertEqual(versions["latest_release"]["date"], "2026-04-30")
        self.assertEqual(
            ref.gs_uri,
            "gs://example-bucket/100-geographic-reference/110-boundaries/example-vector/releases/2026-04-30/example-vector.pmtiles",
        )
        self.assertEqual(
            ref.url,
            "https://storage.googleapis.com/example-bucket/100-geographic-reference/110-boundaries/example-vector/releases/2026-04-30/example-vector.pmtiles",
        )
        with self.assertRaises(ValueError):
            catalog.resolve(
                "example-vector",
                format="pmtiles",
                version="2026-04-30",
                access="gcs",
                client=client,
                web_base_url="/pmtiles",
            )

    def test_dated_resolve_requires_release_index_file_paths(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV)
        release_index = {
            "asset_slug": "example-vector",
            "releases": [
                {
                    "date": "2026-04-30",
                    "files": [{"format": "pmtiles"}],
                }
            ],
        }
        client = FakeGcsClient(
            {
                ("example-bucket", "_catalog/releases/example-vector.json"): FakeGcsBlob(
                    text=json.dumps(release_index)
                )
            }
        )

        with self.assertRaisesRegex(CatalogLoadError, "missing an explicit path"):
            catalog.resolve(
                "example-vector",
                format="pmtiles",
                version="2026-04-30",
                access="gcs",
                client=client,
            )

    def test_public_versions_use_tiles_catalog_endpoint(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV.replace("gs://example-bucket/", "gs://skytruth-shared-datasets-1/"))
        release_index = {
            "asset_slug": "example-vector",
            "releases": [],
        }
        calls = []

        def fake_urlopen(request, timeout):
            calls.append((request.full_url, timeout))
            return FakeResponse(json.dumps(release_index).encode())

        with mock.patch("skytruth_shared_datasets.catalog.urlopen", side_effect=fake_urlopen):
            versions = catalog.versions("example-vector")

        self.assertEqual(versions["asset_slug"], "example-vector")
        self.assertEqual(calls, [("https://tiles.skytruth.org/_catalog/releases/example-vector.json", 10.0)])

    def test_dated_fetch_uses_exact_version_cache_path(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV)
        release_index = {
            "asset_slug": "example-vector",
            "releases": [
                {
                    "date": "2026-04-30",
                    "files": [
                        {
                            "format": "fgb",
                            "path": "gs://example-bucket/100-geographic-reference/110-boundaries/example-vector/releases/2026-04-30/example-vector.fgb",
                        }
                    ],
                }
            ],
        }
        release_blob = FakeGcsBlob(content=b"dated bytes")
        client = FakeGcsClient(
            {
                ("example-bucket", "_catalog/releases/example-vector.json"): FakeGcsBlob(
                    text=json.dumps(release_index)
                ),
                (
                    "example-bucket",
                    "100-geographic-reference/110-boundaries/example-vector/releases/2026-04-30/example-vector.fgb",
                ): release_blob,
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            ref = catalog.fetch(
                "example-vector",
                format="fgb",
                version="2026-04-30",
                cache_dir=tmp,
                access="gcs",
                client=client,
            )
            path = ref.cache_path
            assert path is not None
            self.assertIn("2026-04-30", path.parts)
            self.assertEqual(path.name, "example-vector.fgb")
            self.assertEqual(path.read_bytes(), b"dated bytes")
            self.assertEqual(ref.last_updated, "2026-04-30")
            self.assertEqual(ref.resolved_id, "example-vector@2026-04-30#generation=1")

    def test_fetch_downloads_to_cache_and_reuses_cache(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV)
        calls = []

        def fake_urlopen(request, timeout):
            calls.append((request.full_url, request.get_method()))
            if "_catalog/releases/" in request.full_url:
                raise HTTPError(request.full_url, 404, "missing index", {}, None)
            return FakeResponse(b"dataset bytes", **{"x-goog-generation": "1"})

        with tempfile.TemporaryDirectory() as tmp, mock.patch("skytruth_shared_datasets.catalog.urlopen", side_effect=fake_urlopen):
            first = catalog.fetch("example-vector", format="fgb", cache_dir=tmp)
            second = catalog.fetch("example-vector", format="fgb", cache_dir=tmp)
            forced = catalog.fetch("example-vector", format="fgb", cache_dir=tmp, force=True)

            self.assertIsInstance(first, DatasetRef)
            self.assertEqual(first.cache_path, second.cache_path)
            self.assertEqual(first.cache_path, forced.cache_path)
            self.assertEqual(first.last_updated, "")
            self.assertEqual(first.resolved_id, "example-vector@latest#generation=1")
            path = first.cache_path
            assert path is not None
            self.assertEqual(path.read_bytes(), b"dataset bytes")
            self.assertEqual(sum("?generation=1" in url and method == "GET" for url, method in calls), 2)
            self.assertEqual(path.name, "example-vector.fgb")
            self.assertIn("latest", path.parts)

    def test_fetch_downloads_from_mocked_gcs_client(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV)
        ref = catalog.resolve("example-vector", format="fgb")
        bucket_name, object_name = split_gs_uri(ref.gs_uri)
        blob = FakeGcsBlob(content=b"gcs dataset bytes")
        client = FakeGcsClient({(bucket_name, object_name): blob})

        with tempfile.TemporaryDirectory() as tmp:
            fetched_ref = catalog.fetch("example-vector", format="fgb", cache_dir=tmp, access="gcs", client=client)
            path = fetched_ref.cache_path
            assert path is not None
            self.assertEqual(path.read_bytes(), b"gcs dataset bytes")
            self.assertEqual(fetched_ref.gs_uri, ref.gs_uri)
            self.assertEqual(fetched_ref.resolved_id, "example-vector@latest#generation=1")

        self.assertEqual(client.requests, [(bucket_name, "_catalog/releases/example-vector.json"), (bucket_name, object_name), (bucket_name, object_name)])
        self.assertEqual(blob.downloads, [("file", 60.0)])

    def test_magic_helpers_use_authenticated_gcs_client(self):
        catalog_blob = FakeGcsBlob(text=FIXTURE_CSV)
        dataset_blob = FakeGcsBlob(content=b"magic gcs bytes")
        client = FakeGcsClient(
            {
                ("example-bucket", "catalog.csv"): catalog_blob,
                (
                    "example-bucket",
                    "100-geographic-reference/110-boundaries/example-vector/latest/example-vector.fgb",
                ): dataset_blob,
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            ref = resolve_dataset(
                "example-vector",
                "fgb",
                client=client,
                catalog_source="gs://example-bucket/catalog.csv",
            )
            pmtiles_ref = resolve_dataset(
                "example-vector",
                "pmtiles",
                client=client,
                catalog_source="gs://example-bucket/catalog.csv",
            )
            self.assertEqual(
                client.requests,
                [("example-bucket", "catalog.csv"), ("example-bucket", "catalog.csv")],
            )
            client.requests.clear()
            fetched_ref = fetch_dataset(
                "example-vector",
                "fgb",
                client=client,
                catalog_source="gs://example-bucket/catalog.csv",
                cache_dir=tmp,
            )
            path = fetched_ref.cache_path
            assert path is not None
            self.assertEqual(path.read_bytes(), b"magic gcs bytes")

        self.assertEqual(
            ref.url,
            "https://storage.googleapis.com/example-bucket/100-geographic-reference/110-boundaries/example-vector/latest/example-vector.fgb",
        )
        self.assertEqual(pmtiles_ref.url, "https://tiles.skytruth.org/pmtiles/public/example-vector.pmtiles")
        self.assertEqual(fetched_ref.last_updated, "")
        self.assertEqual(fetched_ref.resolved_id, "example-vector@latest#generation=1")
        self.assertEqual(
            client.requests,
            [
                ("example-bucket", "catalog.csv"),
                ("example-bucket", "_catalog/releases/example-vector.json"),
                ("example-bucket", "100-geographic-reference/110-boundaries/example-vector/latest/example-vector.fgb"),
                ("example-bucket", "100-geographic-reference/110-boundaries/example-vector/latest/example-vector.fgb"),
            ],
        )

    def test_fetch_reports_download_failures(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV)
        with tempfile.TemporaryDirectory() as tmp, mock.patch("skytruth_shared_datasets.catalog.urlopen", side_effect=OSError("failed")):
            with self.assertRaises(CatalogLoadError):
                catalog.fetch("example-vector", format="fgb", cache_dir=tmp)

    def test_real_repo_catalog_parses_and_active_assets_resolve(self):
        catalog = Catalog.load(source=REPO_ROOT / "catalog/shared-datasets-catalog.csv")

        active_assets = catalog.search(status="active")

        self.assertGreater(len(active_assets), 0)
        for asset in active_assets:
            ref = catalog.resolve(asset.slug)
            self.assertTrue(ref.gs_uri.startswith("gs://"))
            if ref.format == "pmtiles":
                self.assertTrue(ref.url.startswith(DEFAULT_PMTILES_CDN_BASE_URL))
            else:
                self.assertTrue(ref.url.startswith("https://storage.googleapis.com/"))


class SharedDatasetCliTests(unittest.TestCase):
    def test_versions_command_prints_release_index_rows(self):
        fake_catalog = SimpleNamespace(
            versions=lambda slug, **_kwargs: {
                "releases": [
                    {
                        "date": "2026-04-30",
                        "release_path": f"gs://example-bucket/releases/{slug}/2026-04-30/",
                        "files": [{"format": "fgb"}, {"format": "pmtiles"}],
                    }
                ]
            }
        )

        with (
            mock.patch.object(sdk_cli.Catalog, "load", return_value=fake_catalog),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            exit_code = sdk_cli.main(["versions", "example-vector"])

        self.assertEqual(exit_code, 0)
        self.assertIn("2026-04-30\tfgb;pmtiles", stdout.getvalue())

    def test_url_command_passes_exact_version_to_resolver(self):
        calls = []

        def resolve(slug, requested_format, **kwargs):
            calls.append((slug, requested_format, kwargs))
            return SimpleNamespace(url="https://storage.googleapis.com/example/release.pmtiles")

        fake_catalog = SimpleNamespace(resolve=resolve)

        with (
            mock.patch.object(sdk_cli.Catalog, "load", return_value=fake_catalog),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            exit_code = sdk_cli.main(
                [
                    "url",
                    "example-vector",
                    "--format",
                    "pmtiles",
                    "--version",
                    "2026-04-30",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls[0][0], "example-vector")
        self.assertEqual(calls[0][1], "pmtiles")
        self.assertEqual(calls[0][2]["version"], "2026-04-30")
        self.assertEqual(calls[0][2]["access"], "public")
        self.assertIn("release.pmtiles", stdout.getvalue())

    def test_fetch_command_prints_cache_path(self):
        calls = []
        cache_path = Path("/tmp/shared-datasets/example-vector.fgb")

        def fetch(slug, requested_format, **kwargs):
            calls.append((slug, requested_format, kwargs))
            return SimpleNamespace(cache_path=cache_path)

        fake_catalog = SimpleNamespace(fetch=fetch)

        with (
            mock.patch.object(sdk_cli.Catalog, "load", return_value=fake_catalog),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            exit_code = sdk_cli.main(
                [
                    "fetch",
                    "example-vector",
                    "--format",
                    "fgb",
                    "--version",
                    "2026-04-30",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls[0][0], "example-vector")
        self.assertEqual(calls[0][1], "fgb")
        self.assertEqual(calls[0][2]["version"], "2026-04-30")
        self.assertEqual(calls[0][2]["access"], "public")
        self.assertEqual(stdout.getvalue().strip(), str(cache_path))


class ExactArtifactFetchTests(unittest.TestCase):
    root = "100-geographic-reference/110-boundaries/example-vector"
    index_key = ("example-bucket", "_catalog/releases/example-vector.json")

    def setUp(self):
        work_root = Path(os.environ.get("SHARED_DATASETS_WORKDIR", str(Path(tempfile.gettempdir()) / "shared-datasets-1")))
        work_root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="python-sdk-cache-test-", dir=work_root)
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name)
        # Legacy callers may still supply a static CSV date. It is never lineage.
        csv_text = FIXTURE_CSV.replace("asset_slug,title,", "asset_slug,title,last_updated,").replace(
            "example-vector,Example Vector,", "example-vector,Example Vector,2026-01-01,"
        ).replace("example-table,Example Table,", "example-table,Example Table,,")
        self.catalog = Catalog.from_csv_text(csv_text)
        self.client = FakeGcsClient({})
        self.index = {"schema_version": 1, "asset_slug": "example-vector", "latest_release": None, "releases": []}
        self.index_generation = 100

    def publish(self, date="2026-01-01", content=b"January", generation=10, *, omit=()):
        name = f"{self.root}/releases/{date}/example-vector.fgb"
        blob = FakeGcsBlob(content=content, generation=generation)
        self.client.blobs[("example-bucket", name)] = blob
        entry = {"format": "fgb", "path": f"gs://example-bucket/{name}", "generation": generation,
                 "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        for key in omit:
            entry.pop(key)
        self.index["releases"] = [release for release in self.index["releases"] if release["date"] != date]
        self.index["releases"].append({"date": date, "files": [entry]})
        self.index["latest_release"] = {"date": date}
        self.save_index()
        return blob, entry

    def save_index(self):
        self.index_generation += 1
        self.client.blobs[self.index_key] = FakeGcsBlob(text=json.dumps(self.index), generation=self.index_generation)

    def fetch(self, **kwargs):
        return self.catalog.fetch("example-vector", cache_dir=self.cache, access="gcs", client=self.client, **kwargs)

    def test_latest_follows_index_with_static_or_absent_csv_date(self):
        for with_date in (True, False):
            with self.subTest(with_date=with_date):
                if not with_date:
                    self.catalog = Catalog.from_csv_text(FIXTURE_CSV)
                old, _ = self.publish()
                first = self.fetch()
                current, entry = self.publish("2026-09-22", b"September", 20)
                second = self.fetch()
                self.assertEqual(second.cache_path.read_bytes(), b"September")
                self.assertEqual(second.last_updated, "2026-09-22")
                self.assertEqual(second.gs_uri, entry["path"])
                self.assertEqual(second.generation, 20)
                self.assertEqual(second.sha256, entry["sha256"])
                self.assertEqual(second.size, 9)
                self.assertEqual(second.release_index_generation, self.index_generation)
                self.assertEqual(second.resolved_id, "example-vector@2026-09-22#generation=20")
                self.assertNotEqual(first.cache_path, second.cache_path)
                self.assertEqual(first.cache_path.read_bytes(), b"January")
                self.assertTrue(all(value == 20 for value in current.preconditions))
                self.assertTrue(all(value == 10 for value in old.preconditions))

    def test_same_date_replacement_changes_cache_identity_and_lineage(self):
        self.publish()
        first = self.fetch()
        self.publish(content=b"Updated", generation=11)
        second = self.fetch()
        self.assertEqual(first.last_updated, second.last_updated)
        self.assertNotEqual(first.resolved_id, second.resolved_id)
        self.assertNotEqual(first.cache_path, second.cache_path)
        self.assertEqual(first.cache_path.read_bytes(), b"January")
        self.assertEqual(second.cache_path.read_bytes(), b"Updated")

    def test_same_size_tamper_and_truncation_are_repaired(self):
        blob, _ = self.publish()
        original = self.fetch()
        for content in (b"corrupt", b"short"):
            with self.subTest(content=content):
                original.cache_path.write_bytes(content)
                repaired = self.fetch()
                self.assertEqual(repaired.cache_path.read_bytes(), b"January")
        self.assertEqual(len(blob.downloads), 3)
        self.fetch()
        self.assertEqual(len(blob.downloads), 3)

    def test_unverified_or_wrong_cache_records_cannot_authorize_reuse(self):
        blob, _ = self.publish()
        ref = self.fetch()
        record_path = ref.cache_path.with_name(ref.filename + ".verified.json")
        original = record_path.read_text()
        bad_records = [None, "{", "[]", original.replace('"generation": 10', '"generation": true'),
                       original.replace('"generation": 10', '"generation": 9'),
                       original.replace('"sha256": "', '"sha256": "f')]
        for bad in bad_records:
            with self.subTest(record=bad):
                if bad is None:
                    record_path.unlink()
                else:
                    record_path.write_text(bad)
                before = len(blob.downloads)
                self.fetch()
                self.assertEqual(len(blob.downloads), before + 1)

    def test_checksum_disagreement_fails_even_with_cached_or_legacy_artifact(self):
        for omit in ((), ("generation",)):
            with self.subTest(omit=omit):
                blob, entry = self.publish(omit=omit)
                good = self.fetch()
                entry["sha256"] = "f" * 64
                self.save_index()
                with self.assertRaisesRegex(FetchError, "SHA-256"):
                    self.fetch()
                self.assertEqual(good.cache_path.read_bytes(), b"January")
                self.assertEqual(blob.generation, 10)

    def test_size_disagreement_fails_without_replacing_existing_cache(self):
        self.publish()
        good = self.fetch()
        self.index["releases"][0]["files"][0]["size"] = 1
        self.save_index()
        with self.assertRaisesRegex(FetchError, "size"):
            self.fetch()
        self.assertEqual(good.cache_path.read_bytes(), b"January")

    def test_missing_generation_observes_object_without_inventing_original_bytes(self):
        blob, _ = self.publish(omit=("generation", "sha256", "size"))
        result = self.fetch()
        self.assertEqual(result.generation, blob.generation)
        self.assertEqual(result.last_updated, "2026-01-01")
        self.assertEqual(result.sha256, hashlib.sha256(blob.content).hexdigest())
        self.assertEqual(blob.preconditions, [10])

    def test_no_index_and_empty_index_preserve_latest_only_assets(self):
        name = f"{self.root}/latest/example-vector.fgb"
        blob = FakeGcsBlob(content=b"old", generation=10)
        self.client.blobs[("example-bucket", name)] = blob
        first = self.fetch()
        self.assertEqual(first.last_updated, "")
        self.assertEqual(first.resolved_id, "example-vector@latest#generation=10")
        self.save_index()
        blob.generation = 11
        blob.content = b"new"
        second = self.fetch()
        self.assertEqual(second.cache_path.read_bytes(), b"new")
        self.assertNotEqual(first.cache_path, second.cache_path)
        with self.assertRaises(UnsupportedVersionError):
            self.fetch(version="2026-01-01")
        self.client.blobs.pop(self.index_key)
        with self.assertRaises(UnsupportedVersionError):
            self.fetch(version="2026-01-01")
        with self.assertRaises(UnsupportedVersionError):
            self.catalog.resolve("example-vector", version="2026-01-01", access="gcs", client=self.client)

    def test_offline_permission_and_malformed_index_never_fall_back(self):
        self.publish()
        self.fetch()
        for failure in (StorageError(403), StorageError(401), TimeoutError("offline"), OSError("network")):
            with self.subTest(failure=failure):
                self.client.requests.clear()
                self.client.blobs[self.index_key].error = failure
                with self.assertRaises(CatalogLoadError):
                    self.fetch()
                self.assertEqual(self.client.requests, [self.index_key])
        self.client.blobs[self.index_key].error = None
        for text in ("{", "[]", '{"releases":[],"releases":[]}', '{"releases": [], "latest_release": false}'):
            with self.subTest(text=text):
                self.client.blobs[self.index_key].text = text
                with self.assertRaises(CatalogLoadError):
                    self.fetch()

    def test_malformed_identity_metadata_and_ambiguous_releases_are_rejected(self):
        for key, value in (("generation", True), ("generation", 0), ("generation", "01"),
                           ("generation", 1.5), ("generation", None), ("size", True), ("size", -1),
                           ("sha256", None), ("sha256", "bad")):
            with self.subTest(key=key, value=value):
                _, entry = self.publish()
                entry[key] = value
                self.save_index()
                with self.assertRaises(CatalogLoadError):
                    self.fetch()
        self.publish()
        self.index["releases"].append(dict(self.index["releases"][0]))
        self.save_index()
        with self.assertRaisesRegex(CatalogLoadError, "Duplicate release date"):
            self.fetch()
        self.publish()
        self.index["releases"][0]["files"] *= 2
        self.save_index()
        with self.assertRaisesRegex(CatalogLoadError, "Duplicate release artifact"):
            self.fetch()

    def test_missing_or_inconsistent_latest_pointer_is_not_legacy(self):
        for latest in (None, {"date": "2026-12-01"}, {"date": "2026-01-01", "files": []}):
            with self.subTest(latest=latest):
                self.publish()
                self.index["latest_release"] = latest
                self.save_index()
                with self.assertRaises(CatalogLoadError):
                    self.fetch()

    def test_requested_historical_version_uses_its_own_artifact(self):
        self.publish()
        self.publish("2026-09-22", b"new", 20)
        old = self.fetch(version="2026-01-01")
        self.assertEqual(old.cache_path.read_bytes(), b"January")
        with self.assertRaises(UnsupportedVersionError):
            self.fetch(version="2026-02-01")
        with self.assertRaises(UnsupportedFormatError):
            self.fetch(format="csv")

    def test_path_boundary_and_canonical_file_selection(self):
        _, entry = self.publish()
        companion = dict(entry, path=entry["path"].replace("example-vector.fgb", "example-vector-points.fgb"))
        self.index["releases"][0]["files"].insert(0, companion)
        self.save_index()
        self.assertEqual(self.fetch().gs_uri, entry["path"])
        duplicate_canonical = dict(entry, path=entry["path"].replace("/example-vector.fgb", "/other/example-vector.fgb"))
        self.index["releases"][0]["files"].append(duplicate_canonical)
        self.save_index()
        with self.assertRaisesRegex(CatalogLoadError, "Ambiguous"):
            self.fetch()
        for bad in ("gs://other-bucket/a", entry["path"].replace("/releases/", "/latest/"),
                    entry["path"].replace("/example-vector.fgb", "/../other.fgb"), "https://untrusted.test/a"):
            with self.subTest(path=bad):
                self.publish()
                self.index["releases"][0]["files"][0]["path"] = bad
                self.save_index()
                with self.assertRaises(CatalogLoadError):
                    self.fetch()

    def test_requested_pmtiles_format_selects_main_not_points_companion(self):
        _, entry = self.publish()
        main = dict(entry, format="pmtiles", path=entry["path"].replace(".fgb", ".pmtiles"))
        points = dict(main, path=main["path"].replace("example-vector.pmtiles", "example-vector-points.pmtiles"))
        self.index["releases"][0]["files"] = [entry, points, main]
        self.save_index()
        ref = self.catalog.resolve("example-vector", "pmtiles", version="2026-01-01", access="gcs", client=self.client)
        self.assertEqual(ref.gs_uri, main["path"])
        self.index["releases"][0]["files"].append(dict(main, path=main["path"].replace("/example-vector.pmtiles", "/other/example-vector.pmtiles")))
        self.save_index()
        with self.assertRaisesRegex(CatalogLoadError, "Ambiguous"):
            self.catalog.resolve("example-vector", "pmtiles", version="2026-01-01", access="gcs", client=self.client)

    def test_default_metadata_does_not_pick_localized_companion(self):
        _, entry = self.publish()
        canonical = dict(entry, format="metadata", path=entry["path"].replace(".fgb", ".metadata.ndjson.gz"))
        localized = dict(canonical, locale="es", path=canonical["path"].replace(".metadata.", ".metadata.es."))
        self.index["releases"][0]["files"] = [localized, canonical]
        self.save_index()
        ref = self.catalog.resolve("example-vector", "metadata", version="2026-01-01", access="gcs", client=self.client)
        self.assertEqual(ref.gs_uri, canonical["path"])

    def test_generation_change_after_resolution_fails_without_alias_retry(self):
        blob, _ = self.publish()
        download = sdk._download_artifact
        def changed(ref, path, **kwargs):
            blob.generation = 11
            blob.content = b"changed"
            return download(ref, path, **kwargs)
        with mock.patch.object(sdk, "_download_artifact", side_effect=changed):
            with self.assertRaises(FetchError):
                self.fetch()
        self.assertFalse(any(self.cache.rglob("*.verified.json")))
        self.assertFalse(any("/latest/" in name for _, name in self.client.requests))

    def test_latest_change_between_index_and_download_keeps_selected_snapshot(self):
        self.publish()
        download = sdk._download_artifact
        def changed(ref, path, **kwargs):
            self.publish("2026-09-22", b"new", 20)
            return download(ref, path, **kwargs)
        with mock.patch.object(sdk, "_download_artifact", side_effect=changed):
            result = self.fetch()
        self.assertEqual(result.cache_path.read_bytes(), b"January")
        self.assertEqual(result.generation, 10)
        self.assertEqual(self.fetch().cache_path.read_bytes(), b"new")

    def test_interrupted_download_or_record_write_is_not_usable_cache(self):
        blob, _ = self.publish()
        def partial(ref, path, **kwargs):
            path.write_bytes(b"partial")
            raise OSError("interrupted download")
        with mock.patch.object(sdk, "_download_artifact", side_effect=partial):
            with self.assertRaises(FetchError):
                self.fetch()
        self.assertFalse(any(path.is_file() for path in self.cache.rglob("*")))
        with mock.patch.object(sdk, "_write_cache_record", side_effect=OSError("interrupted record write")):
            with self.assertRaises(FetchError):
                self.fetch()
        self.assertFalse(any(self.cache.rglob("*.verified.json")))
        repaired = self.fetch()
        self.assertEqual(repaired.cache_path.read_bytes(), b"January")
        self.assertEqual(len(blob.downloads), 2)
        with mock.patch.object(sdk, "_download_artifact", side_effect=partial):
            with self.assertRaises(FetchError):
                self.fetch(force=True)
        self.assertEqual(self.fetch().cache_path.read_bytes(), b"January")

    def test_concurrent_fetches_share_only_complete_immutable_cache(self):
        blob, _ = self.publish()
        barrier = threading.Barrier(2)
        download = sdk._download_artifact
        def concurrent_download(ref, path, **kwargs):
            barrier.wait(timeout=5)
            return download(ref, path, **kwargs)
        with mock.patch.object(sdk, "_download_artifact", side_effect=concurrent_download):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: self.fetch(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0].cache_path.read_bytes(), b"January")
        before = len(blob.downloads)
        self.fetch()
        self.assertEqual(len(blob.downloads), before)

    def test_old_cache_ignored_and_alias_resolution_stays_local_unpinned(self):
        alias = self.catalog.resolve("example-vector", "pmtiles")
        self.assertEqual(alias.last_updated, "")
        self.assertEqual(alias.resolved_id, "example-vector@latest")
        self.assertIsNone(alias.generation)
        self.assertEqual(alias.url, "https://tiles.skytruth.org/pmtiles/public/example-vector.pmtiles")
        old = self.cache / "example-vector/fgb/2026-01-01/example-vector.fgb"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"unverified")
        self.publish()
        new = self.fetch()
        self.assertEqual(old.read_bytes(), b"unverified")
        self.assertNotEqual(old, new.cache_path)
        self.assertIn("v2", new.cache_path.parts)

    def test_index_disappearing_or_changing_after_stat_is_not_absent(self):
        self.publish()
        blob = self.client.blobs[self.index_key]
        for code in (404, 412):
            with self.subTest(code=code), mock.patch.object(blob, "download_as_text", side_effect=StorageError(code)):
                self.client.requests.clear()
                with self.assertRaises(CatalogLoadError):
                    self.fetch()
                self.assertEqual(self.client.requests, [self.index_key])

    def test_public_index_permission_failure_does_not_trigger_legacy_head(self):
        requests = []
        def denied(request, timeout):
            requests.append(request)
            raise HTTPError(request.full_url, 403, "denied", {}, None)
        with mock.patch.object(sdk, "urlopen", side_effect=denied):
            with self.assertRaises(CatalogLoadError):
                self.catalog.fetch("example-vector", cache_dir=self.cache)
        self.assertEqual(len(requests), 1)
        self.assertIn("_catalog/releases/", requests[0].full_url)

    def test_public_http_generation_binding_without_adc(self):
        _, entry = self.publish()
        calls = []
        def fetch_http(request, timeout):
            calls.append(request)
            if "_catalog/releases/" in request.full_url:
                return FakeResponse(json.dumps(self.index).encode(), **{"x-goog-generation": "101"})
            self.assertEqual(parse_qs(urlsplit(request.full_url).query), {"generation": ["10"]})
            self.assertEqual(request.get_header("Accept-encoding"), "gzip")
            return FakeResponse(b"January", **{"x-goog-generation": "10"})
        with mock.patch.object(sdk, "urlopen", side_effect=fetch_http), mock.patch.object(sdk, "_default_storage_client", side_effect=AssertionError("unexpected ADC")):
            result = self.catalog.fetch("example-vector", cache_dir=self.cache)
        self.assertEqual(result.gs_uri, entry["path"])
        self.assertEqual(result.release_index_generation, 101)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.cache_path.read_bytes(), b"January")

    def test_public_legacy_head_and_replaced_or_truncated_response(self):
        mode = ["good"]
        calls = []
        def fetch_http(request, timeout):
            calls.append(request)
            if "_catalog/releases/" in request.full_url:
                raise HTTPError(request.full_url, 404, "no index", {}, None)
            if request.get_method() == "HEAD":
                return FakeResponse(b"January", **{"x-goog-generation": "10"})
            self.assertIn("?generation=10", request.full_url)
            if mode[0] == "replaced":
                return FakeResponse(b"Updated", **{"x-goog-generation": "11"})
            if mode[0] == "truncated":
                return FakeResponse(b"short", **{"x-goog-generation": "10", "Content-Length": "7"})
            return FakeResponse(b"January", **{"x-goog-generation": "10"})
        with mock.patch.object(sdk, "urlopen", side_effect=fetch_http):
            result = self.catalog.fetch("example-vector", cache_dir=self.cache)
            self.assertEqual(result.last_updated, "")
            self.assertEqual([request.get_method() for request in calls], ["GET", "HEAD", "GET"])
            for bad in ("replaced", "truncated"):
                mode[0] = bad
                with self.subTest(mode=bad), self.assertRaises(FetchError):
                    self.catalog.fetch("example-vector", cache_dir=self.cache, force=True)
            self.assertEqual(result.cache_path.read_bytes(), b"January")

    def test_private_adc_download_is_bound_to_generation(self):
        self.catalog = Catalog.from_csv_text(FIXTURE_CSV.replace("active,,,,,public", "active,,,,,private"))
        blob, _ = self.publish()
        with mock.patch.object(sdk, "urlopen", side_effect=AssertionError("unexpected public HTTP")):
            result = self.fetch()
        self.assertEqual(result.access_tier, "private")
        self.assertEqual(blob.preconditions, [10])
        self.assertIn(("example-bucket", f"{self.root}/releases/2026-01-01/example-vector.fgb", 10), self.client.bindings)
        self.assertEqual(self.client.blobs[self.index_key].preconditions, [101])


if __name__ == "__main__":
    unittest.main()
