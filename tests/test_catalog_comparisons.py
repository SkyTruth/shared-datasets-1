import copy
import json
import threading
import time

import pytest

from services.catalog_viewer import comparisons, run as viewer
from scripts import compare_releases as engine
from tests.comparison_fixtures import bundle, record

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


def test_expired_jobs_and_resource_limit_produce_no_partial_counts(context):
    context[1].limits = engine.Limits(max_rows=1, max_input_bytes=1)
    _, start = call(context)
    result = complete(context, start["job_id"])
    assert result["state"] == "failed" and "max_input_bytes" in result["error"]
    assert "summary" not in result
    context[1].jobs[start["job_id"]].created -= comparisons.JOB_TTL_SECONDS + 1
    assert call(context, "GET", f"/api/comparisons/{start['job_id']}")[0] == 404
