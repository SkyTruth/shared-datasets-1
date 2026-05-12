from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError


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


FIXTURE_CSV = """asset_slug,title,category,subcategory,status,lifecycle_reason,lifecycle_date,successor_asset_slug,consumer_guidance,access_tier,owner,update_cadence,canonical_path,canonical_format,available_formats,metadata_paths,has_pmtiles,has_geojson,has_csv,source,license,citation,notes
example-vector,Example Vector,100-geographic-reference,110-boundaries,active,,,,,public,SkyTruth,manual,gs://example-bucket/100-geographic-reference/110-boundaries/example-vector/latest/example-vector.fgb,fgb,fgb;pmtiles;geojson,README.md,true,true,false,Example source,Example license,Example citation,Example notes
example-table,Example Table,700-non-geographic-reference,730-units-codes-lookups,deprecated,Stale source,2026-05-08,,Use example-vector for new work,public,SkyTruth,manual,gs://example-bucket/700-non-geographic-reference/730-units-codes-lookups/example-table/latest/example-table.csv,csv,csv,README.md,false,false,true,Example table source,Example license,Example table citation,Deprecated table
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
        self.assertEqual(catalog.get("example-vector").citation, "Example citation")
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
        self.assertEqual([asset.slug for asset in catalog.search(access_tier="public")], ["example-vector"])
        self.assertEqual([asset.slug for asset in catalog.search(status=None)], ["example-vector", "example-table"])
        with self.assertRaises(DatasetNotFoundError):
            catalog.get("missing")

    def test_gs_to_https_converts_and_escapes_object_names(self):
        url = gs_to_https("gs://bucket/path with spaces/object.fgb")

        self.assertEqual(url, "https://storage.googleapis.com/bucket/path%20with%20spaces/object.fgb")

    def test_gs_to_catalog_url_uses_tiles_for_shared_catalog_objects(self):
        url = gs_to_catalog_url("gs://skytruth-shared-datasets-1/_catalog/releases/example asset.json")

        self.assertEqual(url, "https://tiles.skytruth.org/_catalog/releases/example%20asset.json")

    def test_resolve_supports_canonical_and_companion_formats(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV)

        fgb = catalog.resolve("example-vector", format="fgb")
        pmtiles = catalog.resolve("example-vector", format="pmtiles")
        geojson = catalog.resolve("example-vector", format="geojson")

        self.assertEqual(fgb.gs_uri, "gs://example-bucket/100-geographic-reference/110-boundaries/example-vector/latest/example-vector.fgb")
        self.assertIsNone(fgb.cache_path)
        self.assertEqual(fgb.resolved_id, "example-vector@latest")
        self.assertEqual(pmtiles.gs_uri, "gs://example-bucket/100-geographic-reference/110-boundaries/example-vector/latest/example-vector.pmtiles")
        self.assertEqual(pmtiles.url, f"{DEFAULT_PMTILES_CDN_BASE_URL}/public/example-vector.pmtiles")
        self.assertEqual(pmtiles.access_tier, "public")
        self.assertEqual(geojson.url, "https://storage.googleapis.com/example-bucket/100-geographic-reference/110-boundaries/example-vector/latest/example-vector.geojson")
        with self.assertRaises(UnsupportedFormatError):
            catalog.resolve("example-vector", format="csv")
        with self.assertRaises(UnsupportedVersionError):
            catalog.resolve("example-vector", version="2026-4-30")

    def test_resolve_cdn_url_keeps_canonical_gs_uri(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV)

        pmtiles = catalog.resolve("example-vector", format="pmtiles", web_base_url="/pmtiles")

        self.assertEqual(pmtiles.gs_uri, "gs://example-bucket/100-geographic-reference/110-boundaries/example-vector/latest/example-vector.pmtiles")
        self.assertEqual(pmtiles.url, "/pmtiles/public/example-vector.pmtiles")

    def test_resolve_pmtiles_can_force_public_gcs_url(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV)

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

    def test_public_versions_use_tiles_catalog_endpoint(self):
        catalog = Catalog.from_csv_text(FIXTURE_CSV.replace("gs://example-bucket/", "gs://skytruth-shared-datasets-1/"))
        release_index = {
            "asset_slug": "example-vector",
            "releases": [],
        }
        calls = []

        def fake_urlopen(request, timeout):
            calls.append((request.full_url, timeout))
            return io.BytesIO(json.dumps(release_index).encode())

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
            self.assertEqual(ref.resolved_id, "example-vector@2026-04-30")

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

            self.assertIsInstance(first, DatasetRef)
            self.assertEqual(first.cache_path, second.cache_path)
            self.assertEqual(first.cache_path, forced.cache_path)
            self.assertEqual(first.last_updated, "")
            self.assertEqual(first.resolved_id, "example-vector@latest")
            path = first.cache_path
            assert path is not None
            self.assertEqual(path.read_bytes(), b"dataset bytes")
            self.assertEqual(len(calls), 2)
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
            self.assertEqual(fetched_ref.resolved_id, "example-vector@latest")

        self.assertEqual(client.requests, [(bucket_name, object_name)])
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
                "pmtiles",
                client=client,
                catalog_source="gs://example-bucket/catalog.csv",
            )
            self.assertEqual(client.requests, [("example-bucket", "catalog.csv")])
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

        self.assertEqual(ref.url, f"{DEFAULT_PMTILES_CDN_BASE_URL}/public/example-vector.pmtiles")
        self.assertEqual(fetched_ref.last_updated, "")
        self.assertEqual(fetched_ref.resolved_id, "example-vector@latest")
        self.assertEqual(
            client.requests,
            [
                ("example-bucket", "catalog.csv"),
                (
                    "example-bucket",
                    "100-geographic-reference/110-boundaries/example-vector/latest/example-vector.fgb",
                ),
            ],
        )

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


if __name__ == "__main__":
    unittest.main()
