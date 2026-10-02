from __future__ import annotations

import base64
import copy
import hashlib
import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from skytruth_shared_datasets import (
    FetchError,
    SnapshotError,
    fetch_snapshot_artifact,
    validate_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
CORPUS = json.loads((ROOT / "tests/fixtures/workspace-snapshot-v1.json").read_text())


def mutated(case):
    snapshot = copy.deepcopy(CORPUS["snapshot"])
    parent = snapshot
    for key in case["path"][:-1]:
        parent = parent[key]
    parent[case["path"][-1]] = case["value"]
    return snapshot


@pytest.mark.parametrize(
    "case", CORPUS["invalid_mutations"], ids=lambda case: case["name"]
)
def test_shared_rejection_corpus(case):
    with pytest.raises(SnapshotError):
        validate_snapshot(json.dumps(mutated(case)), bucket="example-bucket")


def test_contract_rejects_duplicate_keys_unknown_fields_and_oversize():
    snapshot = CORPUS["snapshot"]
    validated = validate_snapshot(json.dumps(snapshot), bucket="example-bucket")
    assert validated == snapshot
    assert validated is not snapshot
    assert validated["datasets"][0]["artifacts"][0]["generation"] == "9007199254740993"
    for text in [
        json.dumps(snapshot).replace(
            '"schema_version": 1', '"schema_version": 1, "schema_version": 1'
        ),
        '{"a":' * 30 + "0" + "}" * 30,
        " " * (1024 * 1024 + 1),
    ]:
        with pytest.raises(SnapshotError):
            validate_snapshot(text, bucket="example-bucket")


class ExactClient:
    def __init__(self, payload, filename="example-layer.csv"):
        self.payload = payload
        self.filename = filename
        self.requests = []
        self.available = True

    def bucket(self, bucket):
        assert bucket == "example-bucket"
        return self

    def blob(self, name, *, generation):
        self.requests.append((name, generation))
        assert name.endswith("/" + self.filename)
        assert generation == 9007199254740993
        return self

    def download_to_filename(
        self, filename, *, timeout, if_generation_match, raw_download
    ):
        assert if_generation_match == 9007199254740993 and raw_download is True
        if not self.available:
            raise FileNotFoundError("captured generation was not retained")
        Path(filename).write_bytes(self.payload)


def test_exact_fetch_never_resolves_current_release_and_distinguishes_evidence(
    tmp_path,
):
    payload = base64.b64decode(CORPUS["canonical_base64"])
    client = ExactClient(payload)
    with mock.patch(
        "skytruth_shared_datasets.catalog._read_release_index",
        side_effect=AssertionError("must not resolve current index"),
    ):
        result = fetch_snapshot_artifact(
            CORPUS["snapshot"],
            "example-layer",
            bucket="example-bucket",
            client=client,
            cache_dir=tmp_path,
        )
        assert result.cache_path.read_bytes() == payload
        lineage = result.lineage()
        assert lineage["generation"] == "9007199254740993"
        assert lineage["published_sha256"] == lineage["verified_sha256"]
        assert lineage["published_size"] == lineage["verified_size"] == len(payload)
        requests = len(client.requests)
        client.available = False
        assert (
            fetch_snapshot_artifact(
                CORPUS["snapshot"],
                "example-layer",
                bucket="example-bucket",
                client=client,
                cache_dir=tmp_path,
            ).cache_path
            == result.cache_path
        )
        assert len(client.requests) == requests
        with pytest.raises(FetchError, match="not retained"):
            fetch_snapshot_artifact(
                CORPUS["snapshot"],
                "example-layer",
                bucket="example-bucket",
                client=client,
                cache_dir=tmp_path,
                force=True,
            )


@pytest.mark.parametrize(
    "field,value,match", [("sha256", "0" * 64, "SHA-256"), ("size", 999, "size")]
)
def test_integrity_mismatch(field, value, match, tmp_path):
    snapshot = copy.deepcopy(CORPUS["snapshot"])
    snapshot["datasets"][0]["artifacts"][0][field] = value
    with pytest.raises(FetchError, match=match):
        fetch_snapshot_artifact(
            snapshot,
            "example-layer",
            bucket="example-bucket",
            client=ExactClient(base64.b64decode(CORPUS["canonical_base64"])),
            cache_dir=tmp_path,
        )
    assert not list(tmp_path.rglob("*.verified.json"))


def test_unknown_published_expectations_remain_unknown(tmp_path):
    snapshot = copy.deepcopy(CORPUS["snapshot"])
    snapshot["datasets"][0]["artifacts"][0].update(sha256=None, size=None)
    result = fetch_snapshot_artifact(
        snapshot,
        "example-layer",
        bucket="example-bucket",
        client=ExactClient(b"known locally"),
        cache_dir=tmp_path,
    )
    assert result.lineage()["published_sha256"] is None
    assert result.lineage()["published_size"] is None
    assert result.lineage()["verified_size"] == 13


def test_generated_python_runs_actual_entrypoint_with_escaped_metadata(
    tmp_path, monkeypatch
):
    generated = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {pythonSnippet} from './web/catalog/workspace.js'; let text=''; for await (const chunk of process.stdin) text+=chunk; process.stdout.write(pythonSnippet(JSON.parse(text)));",
        ],
        cwd=ROOT,
        input=json.dumps(CORPUS["snapshot"]),
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    compile(generated, "generated-integration.py", "exec")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SKYTRUTH_SHARED_DATASETS_CACHE", str(tmp_path / "cache"))
    with mock.patch(
        "skytruth_shared_datasets.catalog._default_storage_client",
        return_value=ExactClient(base64.b64decode(CORPUS["canonical_base64"])),
    ):
        exec(generated, {})
    lineage = json.loads((tmp_path / "dataset-lineage.json").read_text())
    assert lineage["generation"] == "9007199254740993"
    assert lineage["published_sha256"] == lineage["verified_sha256"]


@pytest.mark.parametrize(
    "fmt,extension",
    [
        ("csv", ".csv"),
        ("geojson", ".geojson"),
        ("ndgeojson", ".ndgeojson"),
        ("cog", ".tif"),
    ],
)
def test_viewer_authorizes_only_indexed_canonical_formats(fmt, extension):
    import datetime as dt
    from services.catalog_viewer.run import (
        CatalogJsonCache,
        StaticObject,
        handle_request,
    )

    slug = "example-layer"
    root = "gs://skytruth-shared-datasets-1/category/subcategory/example-layer"
    uri = f"{root}/releases/2026-01-01/{slug}{extension}"
    asset = dict(
        slug=slug,
        access_tier="private",
        canonical_format=fmt,
        canonical_path=f"{root}/latest/{slug}{extension}",
        available_formats=[fmt],
    )
    file = dict(format=fmt, path=uri, generation="9007199254740993")
    index = dict(
        schema_version=1,
        asset_slug=slug,
        latest_release={"date": "2026-01-01"},
        releases=[dict(date="2026-01-01", files=[file])],
    )

    class Store:
        def read_static(self, name):
            assert name == "releases/example-layer.json"
            return StaticObject(json.dumps(index).encode(), "application/json")

    class Signer:
        def __init__(self):
            self.calls = []

        def sign(self, uri, expires_at, *, generation):
            self.calls.append((uri, generation))
            return "https://app.test/authorized?Signature=ephemeral"

    signer = Signer()

    def request(extra="", headers=None):
        return handle_request(
            "GET",
            f"/api/download-url?slug={slug}&format={fmt}&version=2026-01-01&generation=9007199254740993{extra}",
            headers
            or {
                "X-Goog-Authenticated-User-Email": "accounts.google.com:viewer@skytruth.org"
            },
            catalog_cache=CatalogJsonCache(loader=lambda: {"assets": [asset]}),
            object_store=Store(),
            signer=signer,
            now=lambda: dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        )

    response = request()
    assert response.status == 200
    assert json.loads(response.body)["generation"] == "9007199254740993"
    assert signer.calls == [(uri, "9007199254740993")]
    signer.calls.clear()
    assert request("&path=gs://skytruth-shared-datasets-1/secrets/key").status == 400
    file["generation"] = "9007199254740994"
    assert request().status == 409
    assert not signer.calls


@pytest.mark.parametrize("fmt", ["geojson", "ndgeojson", "fgb", "cog", "pmtiles"])
def test_generated_python_consumes_each_supported_format(fmt, tmp_path, monkeypatch):
    feature = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [10, 11]},
        "properties": {"feature_id": "b2", "name": "雪"},
    }
    extensions = {
        "geojson": ".geojson",
        "ndgeojson": ".ndgeojson",
        "fgb": ".fgb",
        "cog": ".tif",
        "pmtiles": ".pmtiles",
    }
    filename = "example-layer" + extensions[fmt]
    source = tmp_path / filename
    if fmt == "geojson":
        source.write_text(
            json.dumps({"type": "FeatureCollection", "features": [feature]})
        )
    elif fmt == "ndgeojson":
        source.write_text(json.dumps(feature) + "\n")
    elif fmt == "fgb":
        import geopandas as gpd

        gpd.GeoDataFrame.from_features([feature], crs="EPSG:4326").to_file(
            source, driver="FlatGeobuf"
        )
    elif fmt == "cog":
        # The pinned wheel owns its PROJ data; ignore inherited environments.
        monkeypatch.delenv("PROJ_DATA", raising=False)
        monkeypatch.delenv("PROJ_LIB", raising=False)
        import numpy as np
        import rasterio
        from affine import Affine

        with rasterio.open(
            source,
            "w",
            driver="COG",
            width=4,
            height=4,
            count=1,
            dtype="uint8",
            crs="EPSG:4326",
            transform=Affine(0.1, 0, 10, 0, -0.1, 12),
        ) as raster:
            raster.write(np.ones((1, 4, 4), dtype=np.uint8))
    else:
        source.write_bytes((ROOT / "tests/browser/fixtures/old.pmtiles").read_bytes())
    payload = source.read_bytes()
    snapshot = copy.deepcopy(CORPUS["snapshot"])
    dataset = snapshot["datasets"][0]
    dataset["canonical_format"] = fmt
    dataset["artifacts"] = [
        {
            **dataset["artifacts"][0],
            "format": fmt,
            "gs_uri": "gs://example-bucket/category/subcategory/example-layer/releases/2026-01-01/"
            + filename,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    ]
    generated = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {pythonSnippet} from './web/catalog/workspace.js'; let text=''; for await (const chunk of process.stdin) text+=chunk; process.stdout.write(pythonSnippet(JSON.parse(text)));",
        ],
        cwd=ROOT,
        input=json.dumps(snapshot),
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SKYTRUTH_SHARED_DATASETS_CACHE", str(tmp_path / "cache"))
    namespace = {}
    client = ExactClient(payload, filename)
    with mock.patch(
        "skytruth_shared_datasets.catalog._default_storage_client", return_value=client
    ):
        exec(generated, namespace)
    assert namespace["path"].read_bytes() == payload
    assert (
        namespace["lineage"]["verified_sha256"] == hashlib.sha256(payload).hexdigest()
    )
    assert client.requests == [
        (
            "category/subcategory/example-layer/releases/2026-01-01/" + filename,
            9007199254740993,
        )
    ]
    if fmt == "fgb":
        assert namespace["features"].iloc[0]["name"] == "雪"
    elif fmt == "geojson":
        assert namespace["data"]["features"][0]["properties"]["feature_id"] == "b2"
