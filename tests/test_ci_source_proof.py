import copy

import pytest

from scripts import ci_source_proof as proof
from scripts.ci_contract import SUITES, expected_tools, verify_results
from ci_result_fixtures import production_images


def plan(attempt=1):
    return {
        "schema_version": 1, "base": "a" * 40, "head": "b" * 40,
        "tested_sha": "c" * 40, "tree": "d" * 40, "contract_digest": "e" * 64,
        "suites": list(SUITES), "source": {"run_id": "123", "run_attempt": attempt},
    }


def result(validation, suite, attempt=1, status="success"):
    value = {
        **{field: validation[field] for field in ("base", "head", "tested_sha", "tree", "contract_digest")},
        "suite": suite, "source": {"run_id": "123", "run_attempt": attempt},
        "status": status, "tools": expected_tools(suite),
        "commands": [{"exit_code": 0}],
    }
    if suite == "production-images":
        value["production_images"] = production_images(validation["tested_sha"])
    return value


def source_run(attempt=1):
    return {
        "id": 123, "run_attempt": attempt, "path": ".github/workflows/ci.yml",
        "event": "pull_request", "head_sha": "b" * 40,
        "repository": {"full_name": "SkyTruth/shared-datasets-1"},
    }


def test_latest_attempt_results_reuse_only_api_proven_same_revision_successes():
    validation = plan()
    results = [result(validation, suite) for suite in SUITES]
    results[1]["status"] = "failure"
    results.append(result(validation, "tests", attempt=2))
    proofs = {1: {proof.job_name(suite) for suite in SUITES if suite != "tests"}, 2: {"tests"}}
    chosen = verify_results(validation, results, source_proofs=proofs, run_id="123", run_attempt=2)
    assert chosen["tests"]["source"]["run_attempt"] == 2
    assert chosen["sdk-node24"]["source"]["run_attempt"] == 1
    proofs[1].remove(proof.job_name("sdk-node24"))
    with pytest.raises(ValueError, match="no successful producing job"):
        verify_results(validation, results, source_proofs=proofs, run_id="123", run_attempt=2)


@pytest.mark.parametrize("defect", ["other-run", "future", "duplicate", "changed-tree", "newer-failure"])
def test_stale_unproven_or_duplicate_attempt_evidence_cannot_pass(defect):
    validation = plan()
    results = [result(validation, suite) for suite in SUITES]
    proofs = {1: {proof.job_name(suite) for suite in SUITES}, 2: {"tests"}}
    if defect == "other-run":
        results[0]["source"]["run_id"] = "456"
    elif defect == "future":
        results[0]["source"]["run_attempt"] = 3
    elif defect == "duplicate":
        results.append(copy.deepcopy(results[0]))
    elif defect == "changed-tree":
        results[0]["tree"] = "f" * 40
    else:
        results.append(result(validation, "tests", attempt=2, status="failure"))
    with pytest.raises(ValueError):
        verify_results(validation, results, source_proofs=proofs, run_id="123", run_attempt=2)


def test_latest_plan_requires_identical_comparison_and_contract():
    selected = proof.select_plan([plan(1), plan(2)], run_id="123", attempt=2)
    assert selected["source"]["run_attempt"] == 2
    for field in ("head", "base", "tree", "contract_digest", "suites"):
        changed = plan(2)
        changed[field] = "different"
        with pytest.raises(ValueError, match="plans disagree"):
            proof.select_plan([plan(1), changed], run_id="123", attempt=2)


@pytest.mark.parametrize("defect", ["wrong-path", "wrong-head", "wrong-run", "wrong-repository", "wrong-event", "wrong-attempt"])
def test_api_source_identity_must_match_exact_ci_attempt(monkeypatch, defect):
    run = source_run()
    key, value = {
        "wrong-path": ("path", ".github/workflows/publish-dataset.yml"),
        "wrong-head": ("head_sha", "f" * 40), "wrong-run": ("id", 456),
        "wrong-repository": ("repository", {"full_name": "someone/fork"}),
        "wrong-event": ("event", "pull_request_target"), "wrong-attempt": ("run_attempt", 2),
    }[defect]
    run[key] = value
    monkeypatch.setattr(proof, "api", lambda _path: run)
    with pytest.raises(ValueError, match="does not prove"):
        proof.prove_attempts("SkyTruth/shared-datasets-1", "123", {1}, plan())


def test_complete_job_pagination_proves_only_completed_success(monkeypatch):
    first = [{"id": index, "name": f"job-{index}", "status": "completed", "conclusion": "success"} for index in range(100)]
    second = [{"id": 100, "name": "tests", "status": "completed", "conclusion": "success"}, {"id": 101, "name": "cancelled-job", "status": "completed", "conclusion": "cancelled"}]
    requests = []

    def api(path):
        requests.append(path)
        if "jobs?" not in path:
            return source_run()
        return {"total_count": 102, "jobs": first if path.endswith("page=1") else second}

    monkeypatch.setattr(proof, "api", api)
    proven = proof.prove_attempts("SkyTruth/shared-datasets-1", "123", {1}, plan())
    assert "tests" in proven[1]
    assert "cancelled-job" not in proven[1]
    assert len(requests) == 3


@pytest.mark.parametrize("defect", ["incomplete", "duplicate-id", "duplicate-name", "changing-count"])
def test_ambiguous_or_incomplete_api_job_enumeration_fails(monkeypatch, defect):
    calls = 0

    def api(path):
        nonlocal calls
        if "jobs?" not in path:
            return source_run()
        calls += 1
        jobs = [{"id": 1, "name": "tests", "status": "completed", "conclusion": "success"}]
        if defect in {"duplicate-id", "duplicate-name"}:
            jobs.append({"id": 1 if defect == "duplicate-id" else 2, "name": "other" if defect == "duplicate-id" else "tests", "status": "completed", "conclusion": "success"})
            return {"total_count": 2, "jobs": jobs}
        return {"total_count": 3 if calls == 2 and defect == "changing-count" else 2, "jobs": jobs if calls == 1 else []}

    monkeypatch.setattr(proof, "api", api)
    with pytest.raises(ValueError):
        proof.prove_attempts("SkyTruth/shared-datasets-1", "123", {1}, plan())
