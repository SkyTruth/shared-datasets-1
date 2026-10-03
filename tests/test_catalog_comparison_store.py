"""Real comparison routes on independent instances, with only GCS transport faked."""

import io
import threading
import time
from pathlib import Path

import pytest
from google.api_core.exceptions import NotFound, PreconditionFailed

from services.catalog_viewer import comparisons, run as viewer
from services.catalog_viewer.comparison_store import GcsComparisonStore
from tests import test_catalog_comparisons as comparison_fixtures

call, complete, context = (
    comparison_fixtures.call,
    comparison_fixtures.complete,
    comparison_fixtures.context,
)


class Storage:
    def __init__(self):
        self.objects, self.downloads = {}, []
        self.lock = threading.Lock()
        self.generation = 0

    def bucket(self, name):
        assert name == "private-comparison-cache"
        return self

    def blob(self, name, generation=None):
        return Blob(self, name, generation)


class Blob:
    def __init__(self, storage, name, generation):
        self.storage, self.name, self.generation = storage, name, generation

    def exists(self, **kwargs):
        return self.name in self.storage.objects

    def download_as_bytes(
        self, *, start=None, end=None, if_generation_match=None, **kwargs
    ):
        with self.storage.lock:
            if self.name not in self.storage.objects:
                raise NotFound(self.name)
            generation, data = self.storage.objects[self.name]
            if if_generation_match is not None and if_generation_match != generation:
                raise PreconditionFailed(self.name)
            if self.generation is not None and self.generation != generation:
                raise NotFound(self.name)
            self.generation = generation
            self.storage.downloads.append(self.name)
            return data[(start or 0) : end + 1 if end is not None else None]

    def upload_from_string(self, data, *, if_generation_match, **kwargs):
        with self.storage.lock:
            current = self.storage.objects.get(self.name, (0, b""))[0]
            if current != if_generation_match:
                raise PreconditionFailed(self.name)
            self.storage.generation += 1
            self.generation = self.storage.generation
            self.storage.objects[self.name] = (
                self.generation,
                data.encode() if isinstance(data, str) else data,
            )

    def upload_from_filename(self, path, **kwargs):
        self.upload_from_string(Path(path).read_bytes(), **kwargs)

    def open(self, mode, **kwargs):
        assert mode == "rb"
        return io.BytesIO(self.download_as_bytes(**kwargs))


@pytest.fixture
def distributed(context, tmp_path):
    transport = Storage()
    shared = GcsComparisonStore("private-comparison-cache", client=transport)
    worker = context[1]
    worker.store = shared

    def no_recompute(*args, **kwargs):
        raise AssertionError(
            "A reader must restore the published index, never recompute it"
        )

    reader = comparisons.ComparisonJobs(
        root=tmp_path / "other-instance", store=shared, reader=no_recompute
    )
    remote = context[0], reader, context[2]
    yield context, remote, shared, transport
    reader.pool.shutdown(wait=True)


def test_other_instance_and_restart_serve_same_pages_map_and_properties(
    distributed, tmp_path
):
    local, remote, shared, transport = distributed
    _, started = call(local)
    job_id = started["job_id"]
    original = complete(local, job_id)
    assert original["state"] == "complete"
    for reader in [
        remote[1],
        comparisons.ComparisonJobs(root=tmp_path / "restarted", store=shared),
    ]:
        try:
            reading = remote[0], reader, remote[2]
            status, result = call(reading, "GET", f"/api/comparisons/{job_id}")
            assert status == 200
            assert result["summary"] == original["summary"]
            assert result["page"] == original["page"]
            for side in ("baseline", "target"):
                status, result = call(
                    reading,
                    path=f"/api/comparisons/{job_id}/map",
                    payload={"side": side, "feature_ids": ["1"]},
                )
                assert (
                    status == 200
                    and result["map_features"][0]["change"] == "metadata_changed"
                )
            assert (
                call(reading, "GET", f"/api/comparisons/{job_id}?feature_id=1")[1][
                    "feature"
                ]["after"]["properties"]["name"]
                == "changed"
            )
            assert (
                call(reading, "GET", f"/api/comparisons/{job_id}?query=changed")[1][
                    "page"
                ]["total"]
                == 1
            )
        finally:
            if reader is not remote[1]:
                reader.pool.shutdown(wait=True)
    assert (
        len([name for name in transport.objects if name.endswith("comparison.sqlite")])
        == 1
    )


def test_remote_running_poll_and_cancellation_find_worker(distributed):
    local, remote, _, _ = distributed
    gate, entered = threading.Event(), threading.Event()
    original = local[1].reader

    def held(*args, **kwargs):
        entered.set()
        assert gate.wait(5)
        kwargs["comparison"].check()
        return original(*args, **kwargs)

    local[1].reader = held
    _, start = call(local)
    assert entered.wait(1)
    job_id = start["job_id"]
    try:
        status, response = call(remote, "GET", f"/api/comparisons/{job_id}")
        assert status == 200 and response["state"] == "running"
        remote[1].jobs[job_id].accessed -= comparisons.JOB_TTL_SECONDS + 1
        remote[1]._prune()
        assert job_id not in remote[1].jobs and job_id in local[1].jobs
        assert call(remote, path=f"/api/comparisons/{job_id}/cancel")[0] == 200
        # The worker checks the shared marker at most once per second.
        time.sleep(1.01)
    finally:
        gate.set()
    assert complete(remote, job_id)["state"] == "cancelled"


def test_other_owner_and_current_catalog_denial_cannot_download_cache(distributed):
    local, remote, _, transport = distributed
    _, started = call(local)
    job_id = started["job_id"]
    assert complete(local, job_id)["state"] == "complete"
    downloads = len(transport.downloads)
    assert (
        call(
            remote,
            "GET",
            f"/api/comparisons/{job_id}",
            headers={
                "X-Goog-Authenticated-User-Email": "accounts.google.com:another@skytruth.org"
            },
        )[0]
        == 404
    )
    assert not any(
        name.endswith("comparison.sqlite") for name in transport.downloads[downloads:]
    )
    remote[0].read_catalog_json = lambda: {"assets": []}
    # Fresh entitlement is checked before any cached bytes are hydrated.
    assert call(remote, "GET", f"/api/comparisons/{job_id}")[0] == 404
    assert not any(
        name.endswith("comparison.sqlite") for name in transport.downloads[downloads:]
    )


def test_active_lease_extends_retention_but_idle_expiration_is_real(
    distributed, monkeypatch
):
    local, remote, shared, _ = distributed
    _, started = call(local)
    job_id = started["job_id"]
    assert complete(local, job_id)["state"] == "complete"
    state, _ = shared.read_json(job_id, "state.json")
    now = state["updated"]
    monkeypatch.setattr(comparisons.time, "time", lambda: now)
    for _ in range(20):
        now += 100
        assert call(remote, "GET", f"/api/comparisons/{job_id}")[0] == 200
    now += comparisons.JOB_TTL_SECONDS + 1
    assert call(remote, "GET", f"/api/comparisons/{job_id}")[0] == 404


def test_partial_or_corrupt_result_is_never_exposed_as_complete(distributed):
    local, remote, shared, transport = distributed
    _, started = call(local)
    job_id = started["job_id"]
    assert complete(local, job_id)["state"] == "complete"
    key = f"jobs/{job_id}/comparison.sqlite"
    generation, data = transport.objects[key]
    transport.objects[key] = generation, b"!" + data[1:]
    status, result = call(remote, "GET", f"/api/comparisons/{job_id}")
    assert status == 400 and "checksum" in result["error"]
    assert "summary" not in result and "page" not in result


def test_failed_cache_publication_exposes_no_partial_summary(distributed, monkeypatch):
    local, remote, shared, _ = distributed

    def interrupted_upload(*args):
        raise OSError("Cache upload interrupted")

    monkeypatch.setattr(shared, "publish_files", interrupted_upload)
    _, started = call(local)
    result = complete(remote, started["job_id"])
    assert result["state"] == "failed" and "Cache upload interrupted" in result["error"]
    assert "summary" not in result and "page" not in result


def test_cache_lease_conflicts_and_repeated_cancel_preserve_worker_state(distributed):
    local, _, shared, _ = distributed
    _, started = call(local)
    job_id = started["job_id"]
    assert complete(local, job_id)["state"] == "complete"
    state, generation = shared.read_json(job_id, "state.json")
    shared.touch(job_id, time.time(), 0)
    shared.touch(job_id, time.time(), 0)
    shared.cancel(job_id)
    shared.cancel(job_id)
    assert shared.read_json(job_id, "state.json") == (state, generation)


def test_cloud_run_never_silently_uses_process_local_jobs(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "catalog-viewer")
    monkeypatch.delenv("CATALOG_VIEWER_COMPARISON_BUCKET", raising=False)
    with pytest.raises(ValueError, match="require CATALOG_VIEWER_COMPARISON_BUCKET"):
        viewer.comparison_jobs_from_env()


def test_progress_counts_actual_checked_rows_across_both_sides(tmp_path):
    comparison = comparisons.engine.Comparison(tmp_path)
    job = comparisons.Job(
        "id",
        "owner",
        "slug",
        {},
        comparison,
        totals={"baseline": 64386, "target": 64400},
    )
    job.update("baseline", 38500)
    assert job.payload()["progress"] == {
        "phase": "baseline",
        "rows": 38500,
        "completed": 38500,
        "total": 128786,
    }
    job.update("baseline", 64386)
    job.update("target", 500)
    assert job.payload()["progress"]["completed"] == 64886
    job.update("classifying", 128786)
    assert job.payload()["progress"]["total"] is None
