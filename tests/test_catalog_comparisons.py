import copy
import gzip
import http.client
import json
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from services.catalog_viewer import comparisons, run as viewer
from scripts import compare_releases as engine
from scripts import release_feature_model as model
from tests.comparison_fixtures import bundle, record, generated

A, B = "2026-01-01", "2026-02-01"
HEADERS = {"X-Goog-Authenticated-User-Email": "accounts.google.com:person@skytruth.org"}


class Store:
    def __init__(self, a, b, tier="private"):
        self.asset = {
            "slug": "example",
            "access_tier": tier,
            "canonical_format": "fgb",
            "canonical_path": "gs://example-bucket/category/subcategory/example/latest/example.fgb",
            "pmtiles_path": "gs://example-bucket/category/subcategory/example/latest/example.pmtiles",
            "has_pmtiles": True,
        }
        self.index = {
            "schema_version": 1,
            "asset_slug": "example",
            "latest_release": {"date": B},
            "releases": [
                {
                    "date": ref["release"],
                    "files": [
                        {"format": role, "role": role, **file}
                        for role, file in ref["files"].items()
                    ],
                }
                for ref, _ in (a, b)
            ],
        }
        self.paths = {
            (file["path"], file["generation"]): paths[role]
            for ref, paths in (a, b)
            for role, file in ref["files"].items()
        }

    def read_static(self, name):
        if name != "releases/example.json":
            raise viewer.StaticObjectNotFound(name)
        return viewer.StaticObject(json.dumps(self.index).encode(), "application/json")

    def read_catalog_json(self):
        return {"assets": [self.asset]}

    def reader(self, ref, target, *, bucket_name, comparison):
        path = self.paths.get((ref["path"], ref["generation"]))
        if path is None:
            raise OSError("Historical generation is unavailable")
        target.write_bytes(path.read_bytes())
        comparison.verify_bytes(target, ref)


@pytest.fixture
def context(tmp_path):
    a = bundle(tmp_path / "a", A, [record(1, A)])
    b = bundle(tmp_path / "b", B, [record(1, B, name="changed")], generation=20)
    store = Store(a, b)
    jobs = comparisons.ComparisonJobs(root=tmp_path / "jobs", reader=store.reader)
    request = {
        "slug": "example",
        "baseline": A,
        "target": B,
        "expected": {
            side: {
                role: {"path": f["path"], "generation": f["generation"]}
                for role, f in ref["files"].items()
            }
            for side, (ref, _) in zip(("baseline", "target"), (a, b))
        },
    }
    yield store, jobs, request
    jobs.pool.shutdown(wait=True)


def call(
    context,
    method="POST",
    path="/api/comparisons",
    payload=None,
    headers=HEADERS,
    require_iap=True,
):
    store, jobs, request = context
    response = viewer.handle_request(
        method,
        path,
        headers,
        json.dumps(payload or request).encode(),
        catalog_cache=viewer.CatalogJsonCache(loader=store.read_catalog_json),
        object_store=store,
        signer=None,
        bucket_name="example-bucket",
        comparison_jobs=jobs,
        feature_require_iap=require_iap,
    )
    return response.status, json.loads(response.body)


def complete(context, job_id):
    for _ in range(100):
        status, result = call(context, "GET", f"/api/comparisons/{job_id}")
        assert status == 200
        if result["state"] != "running":
            return result
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def map_index(context, job_id, side):
    store, jobs, _ = context
    response = comparisons.handle_request(
        "GET",
        f"/api/comparisons/{job_id}/map-index?side={side}",
        HEADERS,
        b"",
        object_store=store,
        bucket_name="example-bucket",
        allowed_email_domains=("skytruth.org",),
        require_iap=True,
        jobs=jobs,
    )
    assert isinstance(response, comparisons.MapIndexResponse)
    data = gzip.decompress(b"".join(response.chunks()))
    records = [json.loads(line) for line in data.splitlines()]
    assert records[0]["side"] == side
    assert records[-1] == {"complete": True, "rows": len(records) - 2}
    return records[1:-1]


def test_real_viewer_start_page_inspect_report_and_current_authorization(context):
    status, started = call(context)
    assert status == 202
    result = complete(context, started["job_id"])
    assert result["state"] == "complete"
    assert result["summary"]["counts"]["properties_only"] == 1
    assert result["page"]["total"] == 1
    assert result["page"]["rows"][0]["map_before"]["change"] == "metadata_changed"
    status, inspected = call(
        context, "GET", f"/api/comparisons/{started['job_id']}?feature_id=1"
    )
    assert status == 200
    assert inspected["feature"]["before"]["properties"]["name"] == "value"
    status, report = call(
        context, "GET", f"/api/comparisons/{started['job_id']}/report"
    )
    assert status == 200 and len(report["features"]) == 1
    context[0].asset["access_tier"] = "invalid"
    assert call(context, "GET", f"/api/comparisons/{started['job_id']}")[0] == 400


def test_viewer_capability_exposes_larger_budget_without_changing_cli_defaults(context):
    status, capability = call(context, "GET")
    assert status == 200 and capability["map_index_version"] == 1
    assert capability["limits"]["max_rows"] == 1_000_000
    assert capability["limits"]["max_disk_bytes"] == 512 * 1024 * 1024
    assert capability["limits"]["max_expanded_bytes"] == 1024 * 1024 * 1024
    assert engine.Limits().max_rows == 100_000


def test_denied_restricted_access_and_job_ownership(context):
    assert call(context, headers={})[0] == 401
    assert (
        call(context, headers={"X-Goog-Authenticated-User-Email": "person@other.org"})[
            0
        ]
        == 403
    )
    _, result = call(context)
    assert (
        call(
            context,
            "GET",
            f"/api/comparisons/{result['job_id']}",
            headers={"X-Goog-Authenticated-User-Email": "other@skytruth.org"},
        )[0]
        == 404
    )
    assert call(context, require_iap=False, headers={})[0] == 401


def test_no_uri_selection_and_changed_generation_rejected(context):
    bad = copy.deepcopy(context[2])
    bad["target"] = "gs://arbitrary/uri"
    assert call(context, payload=bad)[0] == 400
    bad = copy.deepcopy(context[2])
    bad["expected"]["baseline"]["manifest"]["generation"] = "999"
    assert call(context, payload=bad)[0] == 409
    context[0].index["releases"].pop(0)
    assert call(context)[0] == 404


def test_missing_historical_bytes_never_substitutes_latest(context):
    ref = context[2]["expected"]["baseline"]["metadata"]
    context[0].paths.pop((ref["path"], ref["generation"]))
    _, start = call(context)
    result = complete(context, start["job_id"])
    assert result["state"] == "failed" and "unavailable" in result["error"]
    assert "summary" not in result and "page" not in result


def test_cancellation_and_explicit_capacity_limit(context):
    gate = threading.Event()
    original = context[1].reader

    def held(*args, **kwargs):
        gate.wait(1)
        kwargs["comparison"].check()
        return original(*args, **kwargs)

    context[1].reader = held
    _, first = call(context)
    _, second = call(context)
    assert call(context)[0] == 429
    status, _ = call(context, "POST", f"/api/comparisons/{first['job_id']}/cancel")
    assert status == 200
    gate.set()
    assert complete(context, first["job_id"])["state"] == "cancelled"
    assert complete(context, second["job_id"])["state"] == "complete"


def test_replacing_queued_jobs_reuses_capacity_without_starting_their_reader(context):
    gate, entered = threading.Event(), threading.Event()
    original = context[1].reader
    reads = []

    def held(*args, **kwargs):
        reads.append(kwargs["comparison"].directory.name)
        entered.set()
        assert gate.wait(5)
        kwargs["comparison"].check()
        return original(*args, **kwargs)

    context[1].reader = held
    try:
        _, first = call(context)
        assert entered.wait(1)
        for _ in range(4):
            status, queued = call(context)
            assert status == 202 and queued["progress"]["phase"] == "queued"
            job = context[1].jobs[queued["job_id"]]
            # Queue time cannot consume the execution deadline.
            job.comparison.started -= context[1].limits.max_seconds + 1
            assert call(context, "POST", f"/api/comparisons/{job.id}/cancel")[0] == 200
            assert complete(context, job.id)["state"] == "cancelled"
            assert job.future.cancelled()
        _, final = call(context)
        assert reads == [first["job_id"]]
        gate.set()
        assert complete(context, first["job_id"])["state"] == "complete"
        assert complete(context, final["job_id"])["state"] == "complete"
    finally:
        gate.set()


@pytest.mark.parametrize("truncate", [False, True])
def test_map_index_real_http_gzip_chunks_are_pinned_complete_and_fail_closed(
    context, monkeypatch, truncate
):
    _, started = call(context)
    result = complete(context, started["job_id"])
    job = context[1].jobs[started["job_id"]]
    original = job.comparison.iter_map_index
    rows = list(original("target"))
    # Exercise several bounded chunks, rather than just a buffered tiny response.
    many = [[str(i).zfill(5), *rows[0][1:]] for i in range(2000)]
    job.comparison.summary["feature_counts"]["target"] = len(many)
    monkeypatch.setattr(
        job.comparison,
        "iter_map_index",
        lambda side: iter(many[:-1] if truncate else many),
    )
    store = context[0]
    handler = viewer.make_handler(
        catalog_cache=viewer.CatalogJsonCache(loader=store.read_catalog_json),
        object_store=store,
        signer=None,
        bucket_name="example-bucket",
        signed_url_ttl_seconds=900,
        allowed_email_domains=("skytruth.org",),
        comparison_jobs=context[1],
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    try:
        connection.request(
            "GET", f"/api/comparisons/{job.id}/map-index?side=target", headers=HEADERS
        )
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("Content-Encoding") == "gzip"
        assert response.getheader("Transfer-Encoding") == "chunked"
        if truncate:
            with pytest.raises(http.client.IncompleteRead) as exc:
                response.read()
            import zlib

            partial = zlib.decompressobj(wbits=31).decompress(exc.value.partial)
            assert b'"complete": true' not in partial
        else:
            data = gzip.decompress(response.read())
            records = [json.loads(line) for line in data.splitlines()]
            assert records[0] == {
                "schema_version": 1,
                "side": "target",
                "inputs": result["inputs"],
                "rows": 2000,
            }
            assert records[1:-1] == many
            assert records[-1] == {"complete": True, "rows": 2000}
        # The stream released its lease, even when it failed halfway through.
        acquired = []

        # RLocks must be released by their owner.
        def probe_lock():
            if job.lock.acquire(timeout=1):
                acquired.append(True)
                job.lock.release()

        probe = threading.Thread(target=probe_lock)
        probe.start()
        probe.join()
        assert acquired == [True]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join()


def test_expired_jobs_and_resource_limit_produce_no_partial_counts(context):
    context[1].limits = engine.Limits(max_rows=1, max_input_bytes=1)
    _, start = call(context)
    result = complete(context, start["job_id"])
    assert result["state"] == "failed" and "max_input_bytes" in result["error"]
    assert "summary" not in result
    context[1].jobs[start["job_id"]].accessed -= comparisons.JOB_TTL_SECONDS + 1
    assert call(context, "GET", f"/api/comparisons/{start['job_id']}")[0] == 404


def test_map_endpoint_is_release_scoped_even_without_compatible_feature_ids(tmp_path):
    a = bundle(tmp_path / "a", A, [record(1, A)], identity=generated("before"))
    b = bundle(
        tmp_path / "b",
        B,
        [record(1, B, geometry={"type": "Point", "coordinates": [9, 0]})],
        identity=generated("after"),
    )
    store = Store(a, b)
    jobs = comparisons.ComparisonJobs(root=tmp_path / "jobs", reader=store.reader)
    request = {
        "slug": "example",
        "baseline": A,
        "target": B,
        "expected": {
            side: {
                role: {"path": f["path"], "generation": f["generation"]}
                for role, f in ref["files"].items()
            }
            for side, ref in [("baseline", a[0]), ("target", b[0])]
        },
    }
    context = (store, jobs, request)
    try:
        _, started = call(context)
        assert complete(context, started["job_id"])["summary"]["counts"] is None
        path = f"/api/comparisons/{started['job_id']}/map"
        for side, color in [("baseline", "removed"), ("target", "novel")]:
            assert list(
                jobs.jobs[started["job_id"]].comparison.iter_map_index(side)
            ) == map_index(context, started["job_id"], side)
            assert map_index(context, started["job_id"], side)[0][:2] == ["1", color]
            status, result = call(
                context, path=path, payload={"side": side, "feature_ids": ["1"]}
            )
            assert status == 200
            assert result["map_features"] == [
                {
                    "feature_id": "1",
                    "change": color,
                    "geometry_hash": record(
                        1,
                        A if side == "baseline" else B,
                        geometry={"type": "Point", "coordinates": [9, 0]}
                        if side == "target"
                        else None,
                    )["geometry_hash"],
                }
            ]
        assert call(context, path=path, headers={})[0] == 401
        assert (
            call(
                context,
                path=path,
                headers={"X-Goog-Authenticated-User-Email": "other@skytruth.org"},
            )[0]
            == 404
        )
        assert (
            call(
                context,
                path=path,
                payload={"side": "target", "feature_ids": ["1"] * 201},
            )[0]
            == 400
        )
        assert (
            call(
                context,
                path=path,
                payload={
                    "side": "target",
                    "feature_ids": ["1"],
                    "padding": "x" * 16384,
                },
            )[0]
            == 413
        )
    finally:
        jobs.pool.shutdown(wait=True)


def test_historical_coral_completes_through_viewer_and_colors_legacy_handles(tmp_path):
    from tests.comparison_fixtures import historical_bundle

    a, geom = historical_bundle(tmp_path / "before", A)
    b = bundle(tmp_path / "after", B, [record(1, B, geometry=geom)])
    store = Store(a, b)
    jobs = comparisons.ComparisonJobs(
        root=tmp_path / "jobs",
        reader=store.reader,
        geometry_opener=lambda ref, **kwargs: store.paths[
            (ref["path"], ref["generation"])
        ].open("rb"),
    )
    request = {
        "slug": "example",
        "baseline": A,
        "target": B,
        "expected": {
            side: {
                role: {"path": f["path"], "generation": f["generation"]}
                for role, f in ref["files"].items()
            }
            for side, (ref, _) in zip(("baseline", "target"), (a, b))
        },
    }
    ctx = (store, jobs, request)
    try:
        status, started = call(ctx)
        assert status == 202
        result = complete(ctx, started["job_id"])
        assert result["state"] == "complete", result
        assert result["summary"]["counts"] is None
        status, colors = call(
            ctx,
            "POST",
            f"/api/comparisons/{started['job_id']}/map",
            {"side": "baseline", "feature_ids": ["gen:coral-example"]},
        )
        assert status == 200
        assert colors["map_features"] == [
            {
                "feature_id": "gen:coral-example",
                "change": "metadata_changed",
                "geometry_hash": model.geometry_hash(geom),
            }
        ]
    finally:
        jobs.pool.shutdown(wait=True)
