"""Real API JSON and gate comparisons; only the read transport/clock is offline."""
from copy import deepcopy
import io
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import ci_source_proof as proof
from scripts.ci_contract import SUITES, verify_results
from test_ci_source_proof import plan, result, source_run


def jobs():
    return [{"id": index, "name": name, "status": "completed", "conclusion": "success", "run_id": 123,
             "run_attempt": 1, "head_sha": "b" * 40}
            for index, name in enumerate(["geospatial-changes", *(proof.job_name(suite) for suite in SUITES)], 1)]


@pytest.fixture
def boundary(monkeypatch):
    elapsed, sleeps, calls = [0.0], [], []
    snapshots, runs = [], []
    generation = [-1]
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-token")
    monkeypatch.setenv("GITHUB_API_URL", "https://api.github.com")

    def open_request(request, *, timeout):
        path = urlsplit(request.full_url)
        if "/jobs" not in path.path:
            generation[0] += 1
            value = runs[min(generation[0], len(runs) - 1)] if runs else source_run()
        else:
            selected = snapshots[min(generation[0], len(snapshots) - 1)]
            page = int(parse_qs(path.query)["page"][0])
            value = selected[page - 1] if isinstance(selected, list) else selected
        calls.append({"url": request.full_url, "headers": dict(request.header_items()), "timeout": timeout})
        return io.BytesIO(json.dumps(value).encode())

    def sleep(seconds):
        sleeps.append(seconds)
        elapsed[0] += seconds

    monkeypatch.setattr(proof.urllib.request, "urlopen", open_request)
    validation = plan()
    results = [result(validation, suite) for suite in SUITES]
    needs = {proof.job_key(suite): {"result": "success"} for suite in SUITES}
    needs["geospatial-changes"] = {"result": "success"}

    def execute(**kwargs):
        return proof.prove_attempts("SkyTruth/shared-datasets-1", "123", {1}, validation,
            current_attempt=1, results=results, needs=needs, clock=lambda: elapsed[0], sleep=sleep, **kwargs)

    return execute, snapshots, runs, elapsed, sleeps, calls, validation, results, needs


@pytest.mark.parametrize("status", ["absent", "in_progress", "queued"])
def test_authenticated_current_success_waits_for_fresh_terminal_snapshot(boundary, status):
    execute, snapshots, _, elapsed, sleeps, calls, validation, results, needs = boundary
    initial = jobs()
    target = next(job for job in initial if job["name"] == "production-images")
    if status == "absent":
        initial.remove(target)
    else:
        target.update(status=status, conclusion=None)
    snapshots.extend([{"total_count": len(initial), "jobs": initial}, {"total_count": len(jobs()), "jobs": jobs()}])
    proofs = execute()
    assert verify_results(validation, results, needs, source_proofs=proofs, run_id="123", run_attempt=1)
    assert sleeps == [5] and elapsed[0] == 5
    assert sum("/jobs?" in request["url"] for request in calls) == 2
    assert all(request["headers"]["Cache-control"] == "no-cache" for request in calls)
    assert all(0 < request["timeout"] <= 30 for request in calls)


def test_nonterminal_visibility_exhausts_fixed_budget_with_exact_status_evidence(boundary, capsys):
    execute, snapshots, _, elapsed, sleeps, calls, *_ = boundary
    pending = jobs()
    next(job for job in pending if job["name"] == "production-images").update(status="in_progress", conclusion=None)
    snapshots.append({"total_count": len(pending), "jobs": pending})
    with pytest.raises(ValueError, match="production-images: id=.*status=in_progress.*120-second"):
        execute()
    assert elapsed[0] == 120 and sum(sleeps) == 120
    assert "awaiting terminal source visibility" in capsys.readouterr().err
    assert sleeps == [5, 10, 20, 30, 45, 10]
    assert len(calls) == 12  # Six complete fresh run/job snapshots, no final late read.


def test_source_visibility_after_provider_cache_window_uses_one_original_budget(boundary):
    execute, snapshots, _, elapsed, sleeps, calls, *_ = boundary
    pending = jobs()
    next(job for job in pending if job["name"] == "production-images").update(status="in_progress", conclusion=None)
    snapshots.extend([{"total_count": len(pending), "jobs": pending}] * 4)
    snapshots.append({"total_count": len(jobs()), "jobs": jobs()})
    assert "production-images" in execute()[1]
    assert elapsed[0] == 65 and sleeps == [5, 10, 20, 30] and len(calls) == 10


@pytest.mark.parametrize("outcome", ["failure", "cancelled", "skipped", "timed_out", None])
def test_terminal_unsuccessful_source_is_fatal_without_waiting(boundary, outcome):
    execute, snapshots, _, _, sleeps, *_ = boundary
    rows = jobs()
    next(job for job in rows if job["name"] == "production-images")["conclusion"] = outcome
    # Another producer is pending, but a real failure must prevent any waiting.
    next(job for job in rows if job["name"] == "browser").update(status="in_progress", conclusion=None)
    snapshots.extend([{"total_count": len(rows), "jobs": rows}, {"total_count": len(jobs()), "jobs": jobs()}])
    with pytest.raises(ValueError, match="terminal source outcome"):
        execute()
    assert sleeps == []


@pytest.mark.parametrize("defect", ["run-head", "job-head", "job-attempt", "job-run", "unknown-status", "contradictory-status", "duplicate-id", "duplicate-name", "malformed-list"])
def test_provenance_or_malformed_source_never_enters_convergence(boundary, defect):
    execute, snapshots, runs, _, sleeps, *_ = boundary
    rows = jobs()
    target = next(job for job in rows if job["name"] == "production-images")
    if defect == "run-head":
        changed = source_run()
        changed["head_sha"] = "f" * 40
        runs.append(changed)
    elif defect in {"job-head", "job-attempt", "job-run"}:
        key, value = {"job-head": ("head_sha", "f" * 40), "job-attempt": ("run_attempt", 2), "job-run": ("run_id", 999)}[defect]
        target[key] = value
    elif defect == "unknown-status":
        target.update(status="unknown", conclusion=None)
    elif defect == "contradictory-status":
        target.update(status="in_progress", conclusion="success")
    elif defect == "duplicate-id":
        target["id"] = rows[0]["id"]
    elif defect == "duplicate-name":
        target["name"] = rows[0]["name"]
    snapshots.extend([{"total_count": len(rows), "jobs": {} if defect == "malformed-list" else rows}, {"total_count": len(jobs()), "jobs": jobs()}])
    with pytest.raises(ValueError):
        execute()
    assert sleeps == []


@pytest.mark.parametrize("defect", ["wrong-result-head", "other-result-run", "future-result", "failed-result", "duplicate-result", "needs-failure", "needs-absent"])
def test_invalid_artifacts_or_absent_current_needs_success_cannot_authorize_wait(boundary, defect):
    execute, snapshots, _, _, sleeps, _, _, results, needs = boundary
    rows = jobs()
    next(job for job in rows if job["name"] == "production-images").update(status="in_progress", conclusion=None)
    target = next(value for value in results if value["suite"] == "production-images")
    if defect == "wrong-result-head":
        target["head"] = "f" * 40
    elif defect == "other-result-run":
        target["source"]["run_id"] = "456"
    elif defect == "future-result":
        target["source"]["run_attempt"] = 2
    elif defect == "failed-result":
        target["status"] = "failure"
    elif defect == "duplicate-result":
        results.append(deepcopy(target))
    elif defect == "needs-failure":
        needs["production-images"]["result"] = "failure"
    else:
        del needs["production-images"]
    snapshots.append({"total_count": len(rows), "jobs": rows})
    with pytest.raises(ValueError):
        execute()
    assert sleeps == []


def test_complete_pagination_is_restarted_and_old_rows_never_supply_new_proof(boundary):
    execute, snapshots, _, _, sleeps, calls, *_ = boundary
    rows = jobs()
    rows.extend({"id": 100 + index, "name": f"unrelated-{index}", "status": "completed", "conclusion": "skipped"} for index in range(100))
    pending = deepcopy(rows)
    next(job for job in pending if job["name"] == "production-images").update(status="in_progress", conclusion=None)
    snapshots.extend([[{"total_count": len(pending), "jobs": pending[:100]}, {"total_count": len(pending), "jobs": pending[100:]}],
                      [{"total_count": len(rows), "jobs": rows[:100]}, {"total_count": len(rows), "jobs": rows[100:]}]])
    assert "production-images" in execute()[1]
    assert sleeps == [5]
    assert [parse_qs(urlsplit(request["url"]).query)["page"][0] for request in calls if "/jobs?" in request["url"]] == ["1", "2", "1", "2"]


def test_partial_or_changing_pagination_is_fatal_not_a_convergence_trigger(boundary):
    execute, snapshots, _, _, sleeps, *_ = boundary
    rows = jobs()
    snapshots.append([{"total_count": 100, "jobs": rows}, {"total_count": 101, "jobs": []}])
    with pytest.raises(ValueError, match="enumeration changed"):
        execute()
    assert sleeps == []


def test_historical_nonterminal_attempt_cannot_borrow_current_needs_success(monkeypatch):
    validation = plan()
    rows = jobs()
    next(job for job in rows if job["name"] == "production-images").update(status="in_progress", conclusion=None)
    monkeypatch.setattr(proof, "api", lambda path: {"total_count": len(rows), "jobs": rows} if "/jobs?" in path else source_run())
    sleeps = []
    with pytest.raises(ValueError, match="no successful producing job"):
        proof.prove_attempts("SkyTruth/shared-datasets-1", "123", {1}, validation, current_attempt=2,
            results=[result(validation, suite) for suite in SUITES],
            needs={proof.job_key(suite): {"result": "success"} for suite in SUITES}, sleep=sleeps.append)
    assert sleeps == []


def test_real_gate_cli_rechecks_source_json_without_reexecuting_any_suite(boundary, monkeypatch, tmp_path):
    from scripts import ci_preflight as preflight
    _, snapshots, _, elapsed, sleeps, _, validation, results, needs = boundary
    rows = jobs()
    next(job for job in rows if job["name"] == "production-images").update(status="in_progress", conclusion=None)
    snapshots.extend([{"total_count": len(rows), "jobs": rows}, {"total_count": len(jobs()), "jobs": jobs()}])
    plan_path, jobs_path, output = tmp_path / "plan.json", tmp_path / "jobs.json", tmp_path / "evidence.json"
    plan_path.write_text(json.dumps(validation))
    jobs_path.write_text(json.dumps(needs))
    reports = tmp_path / "results"
    for value in results:
        directory = reports / value["suite"]
        directory.mkdir(parents=True)
        (directory / "result.json").write_text(json.dumps(value))
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_REPOSITORY", "SkyTruth/shared-datasets-1")
    monkeypatch.setattr(preflight, "validate_checkout", lambda *_: None)
    monkeypatch.setattr(preflight, "make_plan", lambda *_args, **_kwargs: {key: value for key, value in validation.items() if key != "source"})
    def sleep(seconds):
        sleeps.append(seconds)
        elapsed[0] += seconds
    def observe(*args, **kwargs):
        return proof.prove_attempts(*args, **kwargs, clock=lambda: elapsed[0], sleep=sleep)
    monkeypatch.setattr(preflight, "prove_attempts", observe)
    monkeypatch.setattr(preflight.sys, "argv", ["ci_preflight.py", "verify", "--plan", str(plan_path), "--results", str(reports), "--jobs", str(jobs_path), "--output", str(output)])
    assert preflight.main() == 0
    evidence = json.loads(output.read_text())
    assert evidence["status"] == "success" and evidence["source"] == {"run_id": "123", "run_attempt": 1}
    assert set(evidence["suite_artifacts"]) == set(SUITES) and sleeps == [5]
