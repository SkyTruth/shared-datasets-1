"""The Guard's release snapshot owns every baseline read and repair boundary."""
from copy import deepcopy
import datetime as dt
from unittest import mock

import pytest

from ingestion.common import published_baseline, source_quality
from ingestion.eamlis_monthly import run as eamlis
from ingestion.mpatlas_monthly import run as mpatlas
from tests.test_shared_hold_flow import seed
from tests.test_source_loss import Client, execute


@pytest.fixture(params=[mpatlas, eamlis], ids=["mpatlas", "eamlis"])
def publication(request, tmp_path):
    job = request.param
    if job is mpatlas:
        publisher, _ = seed(Client(), tmp_path / "baseline")
        flow = job.quality
    else:
        client = Client()
        execute(client, 2, "2026-04-02")
        publisher = job.GcsPublisher(client, source_quality.BUCKET)
        flow = job.source_loss
    return job, publisher, flow.baseline(publisher.bucket)["release"]


def read(job, publisher, release):
    if job is mpatlas:
        return job.load_baseline(publisher, release)
    refresh = job.Refresh(publisher, None, layer_url=job.DEFAULT_LAYER_URL,
        where=job.DEFAULT_WHERE, page_size=2000)
    with job.source_loss.Guard(publisher.bucket) as refresh.gate:
        refresh.gate.before["release"] = release
        return refresh.baseline()


def replace_indexed_json(publisher, release, suffix, value):
    ref = next(ref for ref in release["files"] if ref["path"].endswith(suffix))
    name = ref["path"].split("/", 3)[3]
    blob = publisher.bucket.blob(name)
    blob.upload_from_string(source_quality.encode(value), if_generation_match=blob.generation)
    ref.update(generation=blob.generation, size=blob.size, sha256=source_quality.digest(blob.data))


@pytest.mark.parametrize("value", [None, [], 7, "invalid", {"schema_version": []}])
@pytest.mark.parametrize("suffix", [".manifest.json", ".schema.json"])
def test_malformed_pinned_json_shapes_require_reviewed_repair(publication, suffix, value):
    job, publisher, release = publication
    if suffix == ".schema.json":
        # Re-pin the manifest too so shape validation, not hash mismatch, rejects it.
        manifest = published_baseline.read(publisher, job.ASSET, release).manifest
    replace_indexed_json(publisher, release, suffix, value)
    if suffix == ".schema.json":
        ref = next(ref for ref in release["files"] if ref["path"].endswith(suffix))
        artifact = next(ref for ref in manifest["artifacts"] if ref["role"] == "schema")
        artifact.update({key: ref[key] for key in ("generation", "size", "sha256")})
        replace_indexed_json(publisher, release, ".manifest.json", manifest)
    with pytest.raises(RuntimeError, match="baseline.*reviewed repair required"):
        read(job, publisher, release)


@pytest.mark.parametrize("suffix", [".metadata.ndjson.gz", ".schema.json"])
@pytest.mark.parametrize("field", ["generation", "size", "sha256", "path"])
def test_manifest_canonical_references_must_match_index(publication, suffix, field):
    job, publisher, release = publication
    manifest = deepcopy(published_baseline.read(publisher, job.ASSET, release).manifest)
    artifact = next(ref for ref in manifest["artifacts"] if ref["path"].endswith(suffix))
    artifact[field] = {"generation": 999, "size": 999, "sha256": "0" * 64,
                       "path": f"gs://{publisher.bucket.name}/unrelated/object"}[field]
    replace_indexed_json(publisher, release, ".manifest.json", manifest)
    with pytest.raises(RuntimeError, match="baseline.*reviewed repair required.*differs from its indexed object"):
        read(job, publisher, release)


@pytest.mark.parametrize("suffix", [".manifest.json", ".metadata.ndjson.gz", ".schema.json"])
@pytest.mark.parametrize("failure", ["missing_generation", "missing_size", "corrupt_bytes", "not_found", "precondition"])
def test_bad_pinned_objects_always_require_reviewed_repair(publication, suffix, failure):
    job, publisher, release = publication
    ref = next(ref for ref in release["files"] if ref["path"].endswith(suffix))
    blob = publisher.bucket.blob(ref["path"].split("/", 3)[3])
    if failure.startswith("missing_"):
        del ref[failure.removeprefix("missing_")]
    elif failure == "corrupt_bytes":
        blob.data = b"corrupt"
    elif failure == "not_found":
        blob.exists = False
    else:
        blob.upload_from_string(blob.data, if_generation_match=blob.generation)
    with pytest.raises(RuntimeError, match="baseline.*reviewed repair required"):
        read(job, publisher, release)


def test_generation_only_manifest_pin_and_stale_run_hash_are_valid(publication):
    job, publisher, release = publication
    ref = next(ref for ref in release["files"] if ref["path"].endswith(".manifest.json"))
    del ref["sha256"]
    blob = publisher.bucket.blob(job.ASSET.run_record_object(dt.date.fromisoformat(release["date"])))
    record = publisher.load_json(blob.name)
    del record["sha256"]["manifest"]
    blob.upload_from_string(source_quality.encode(record), if_generation_match=blob.generation)
    baseline = read(job, publisher, release)
    assert (baseline.rows if job is eamlis else baseline.row_count) == release["rows"]


def test_latest_manifest_is_irrelevant_to_baseline_authority(publication):
    job, publisher, release = publication
    blob = publisher.bucket.blob(job.ASSET.latest_object(".manifest.json"))
    blob.upload_from_string(b"untrusted latest bytes", if_generation_match=blob.generation)
    assert published_baseline.read(publisher, job.ASSET, release).manifest["release"] == release["date"]
    read(job, publisher, release)


def test_shared_reader_uses_only_supplied_snapshot(publication):
    job, publisher, release = publication
    with mock.patch.object(publisher, "load_json", side_effect=AssertionError("index/run reread")):
        assert len(published_baseline.read(publisher, job.ASSET, release).records) == release["rows"]


@pytest.mark.parametrize("suffix", [".metadata.ndjson.gz", ".schema.json"])
def test_canonical_index_references_require_sha256(publication, suffix):
    job, publisher, release = publication
    ref = next(ref for ref in release["files"] if ref["path"].endswith(suffix))
    del ref["sha256"]
    with pytest.raises(RuntimeError, match="baseline.*reviewed repair required"):
        read(job, publisher, release)


def test_valid_metadata_count_must_equal_index(publication):
    job, publisher, release = publication
    release["rows"] += 1
    with pytest.raises(RuntimeError, match="baseline.*reviewed repair required.*count differs"):
        read(job, publisher, release)


def test_standalone_mpatlas_schedule_check_captures_an_index_snapshot(tmp_path):
    publisher, _ = seed(Client(), tmp_path / "baseline")
    assert mpatlas.load_baseline(publisher).row_count == 40
