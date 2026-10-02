from __future__ import annotations

import gzip
import hashlib
import io
from pathlib import Path

import pytest

from scripts import download_public_wdpa_benchmark as download


def item(data, *, compress=False):
    return {
        "uri": "gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/wdpa-marine/releases/2026-10-01/test.csv",
        "generation": 123, "target": "frozen-inputs/test.csv.gz",
        "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "compress": compress,
    }


def opener(data, *, generation="123"):
    def open_response(request, timeout):
        assert not request.has_header("Authorization")
        assert request.full_url.endswith("?generation=123")
        source = io.BytesIO(data)
        source.headers = {"x-goog-generation": generation}
        return source
    return open_response


def test_public_fetch_preserves_frozen_compressed_bytes_without_credentials(tmp_path):
    data = b"source,text,locale\n123,example,es\n"
    version = item(data, compress=True)
    stored = io.BytesIO()
    with gzip.GzipFile(filename="", fileobj=stored, mode="wb", compresslevel=1, mtime=0) as target:
        target.write(data)
    version["stored_sha256"] = hashlib.sha256(stored.getvalue()).hexdigest()
    download.fetch(version, tmp_path, opener=opener(data))
    assert (tmp_path / version["target"]).read_bytes() == stored.getvalue()


@pytest.mark.parametrize("defect", ["generation", "hash", "size", "compressed_hash"])
def test_public_fetch_rejects_mismatches_and_removes_only_partial_output(tmp_path, defect):
    data = b"example data"
    version = item(data)
    response_generation = "456" if defect == "generation" else "123"
    if defect == "hash":
        version["sha256"] = "0" * 64
    if defect == "size":
        version["size"] -= 1
    if defect == "compressed_hash":
        version["stored_sha256"] = "0" * 64
    with pytest.raises(RuntimeError):
        download.fetch(version, tmp_path, opener=opener(data, generation=response_generation))
    assert not (tmp_path / version["target"]).exists()


def test_public_fetch_does_not_replace_or_delete_existing_file(tmp_path):
    data = b"example"
    version = item(data)
    target = tmp_path / version["target"]
    target.parent.mkdir()
    target.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        download.fetch(version, tmp_path, opener=opener(data))
    assert target.read_bytes() == b"existing"


@pytest.mark.parametrize("uri", ["gs://other-bucket/data", "https://example.test/data", "gs://skytruth-shared-datasets-1/_scratch/private.tar"])
def test_public_fetch_rejects_unreviewed_origins_and_private_staging(uri):
    with pytest.raises(ValueError):
        download.download_url({**item(b"example"), "uri": uri})


def test_public_fetch_rejects_target_outside_scratch(tmp_path):
    version = {**item(b"example"), "target": "../other"}
    with pytest.raises(ValueError):
        download.fetch(version, tmp_path, opener=opener(b"example"))
    assert not Path(tmp_path / "../other").exists()
