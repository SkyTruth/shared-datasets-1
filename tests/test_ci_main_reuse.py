"""Exercise the API/ZIP -> serialized plan -> job skips -> ci-ready boundary."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
from time import monotonic
import zipfile

import pytest

from scripts import ci_main_reuse as reuse, ci_preflight as preflight, ci_source_proof as source
from scripts.ci_contract import REUSABLE_SUITES, expected_tools, verify_results
from scripts.deployment_revision import DeploymentError
from test_ci_preflight import ROOT, fixture_repo, jobs_for, plan, results_for
from workflow_helpers import load_workflow

REPOSITORY = "SkyTruth/shared-datasets-1"
NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)


class Actions:
    """Only external transport is substituted; JSON, ZIP and digest checks run."""
    def __init__(self, validation):
        self.repository = {"id": 17, "full_name": REPOSITORY}
        self.pr = {"number": 42, "merged": True, "merged_at": "2026-11-10T00:00:00Z", "state": "closed", "draft": False,
                   "merge_commit_sha": validation["tested_sha"],
                   "head": {"sha": "b" * 40, "ref": "feature", "repo": dict(self.repository)},
                   "base": {"sha": validation["base"], "ref": "main", "repo": dict(self.repository)}}
        self.pulls = [{"number": 42}]
        self.run = {"id": 123, "run_attempt": 2, "workflow_id": 7, "path": reuse.WORKFLOW,
                    "event": "pull_request", "head_sha": self.pr["head"]["sha"], "status": "completed", "conclusion": "success",
                    "repository": dict(self.repository), "head_repository": dict(self.repository)}
        self.runs = [self.run]
        self.gate = {"name": "ci-ready", "status": "completed", "conclusion": "success",
                     "started_at": "2026-11-09T00:00:00Z", "completed_at": "2026-11-09T00:01:00Z"}
        self.jobs = [self.gate]
        self.evidence = {**copy.deepcopy(validation), "head": self.pr["head"]["sha"], "tested_sha": "f" * 40,
                         "source": {"run_id": "123", "run_attempt": 2}, "status": "success",
                         "validation_context": {"event": "pull_request", "repository": REPOSITORY, "pr_number": 42},
                         "toolchains": {suite: expected_tools(suite) for suite in validation["suites"]},
                         "validation_results": {suite: {"status": "success", "tools": expected_tools(suite),
                                                       "source": {"run_id": "123", "run_attempt": 1}}
                                                for suite in validation["suites"]}}
        self.artifact = {"id": 81, "name": "ci-ready-evidence-attempt2", "workflow_run": {"id": 123}, "expired": False,
                         "created_at": "2026-11-09T00:00:30Z", "expires_at": "2026-11-23T00:00:30Z"}
        self.artifacts = [self.artifact]
        self.requests = []
        self.pack()

    def pack(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("evidence.json", json.dumps(self.evidence))
        self.archive_bytes = buffer.getvalue()
        self.artifact.update(size_in_bytes=len(self.archive_bytes), digest="sha256:" + hashlib.sha256(self.archive_bytes).hexdigest())

    def get(self, path):
        self.requests.append(path)
        if "/pulls/" in path:
            return copy.deepcopy(self.pr)
        if "/actions/workflows/" in path:
            return {"id": 7, "path": reuse.WORKFLOW}
        assert "/runs/123/attempts/2" in path
        return copy.deepcopy(self.run)

    def pages(self, path, field=None):
        self.requests.append(path)
        if "/commits/" in path:
            rows = self.pulls
        elif field == "workflow_runs":
            rows = self.runs
        elif field == "jobs":
            rows = self.jobs
        else:
            assert field == "artifacts"
            rows = self.artifacts
        return copy.deepcopy(rows)

    def archive(self, repository, identifier):
        assert repository == REPOSITORY and identifier == 81
        return self.archive_bytes


@pytest.fixture
def main_push():
    validation = plan()
    validation.update(head=validation["tested_sha"], source={"run_id": "789", "run_attempt": 1})
    current = {"event": "push", "ref": "refs/heads/main", "repository": REPOSITORY,
               "sha": validation["tested_sha"], "before": validation["base"]}
    return validation, current, Actions(validation)


def test_successful_exact_tree_pr_evidence_survives_json_and_gate_verification(main_push):
    validation, current, api = main_push
    receipt = reuse.select_reuse(api, validation, current, attempt=1, now=NOW)
    validation["main_reuse"] = json.loads(json.dumps(receipt))
    covered = reuse.verified_reuse(validation, current, run_id="789", attempt=1)
    assert covered == REUSABLE_SUITES
    assert receipt["run"]["id"] == 123 and receipt["run"]["run_attempt"] == 2 and receipt["pr"]["number"] == 42
    assert receipt["evidence"]["tested_sha"] != validation["tested_sha"]  # Squash/merge SHA can differ; tree cannot.
    assert len(json.dumps(receipt)) < 16 * 1024
    assert any("/commits/" in path and "/pulls?" in path for path in api.requests)
    results = [dict(result, source=validation["source"]) for result in results_for(validation) if result["suite"] not in covered]
    jobs = jobs_for(validation)
    for suite in covered:
        jobs[suite]["result"] = "skipped"
    proofs = {1: {source.job_name(result["suite"]) for result in results}}
    chosen = verify_results(validation, results, jobs, source_proofs=proofs, run_id="789", run_attempt=1, reused_suites=covered)
    assert set(chosen) == {"production-images", "sdk-node22", "sdk-node24"}
    for job in ("production-images", "sdk-validation"):
        broken = copy.deepcopy(jobs)
        broken[job]["result"] = "skipped"
        with pytest.raises(ValueError):
            verify_results(validation, results, broken, source_proofs=proofs, run_id="789", run_attempt=1, reused_suites=covered)
    with pytest.raises(ValueError, match="missing suite results"):
        verify_results(validation, results, jobs, source_proofs=proofs, run_id="789", run_attempt=1)


@pytest.mark.parametrize("defect", [
    "tree", "base", "contract", "tools", "result-tools", "result-failed", "result-source", "selection", "head",
    "failed-run", "failed-gate", "missing-gate", "ambiguous-gate", "missing-evidence", "expired", "expires-at",
    "ambiguous-evidence", "wrong-digest", "wrong-artifact-run", "wrong-gate-execution", "fork", "run-fork",
    "multiple-prs", "no-pr", "not-merged", "merge-sha", "dispatch-run", "dispatch-evidence", "other-pr", "wrong-workflow", "api-error",
    "in-progress", "no-runs", "bad-zip",
])
def test_lookup_refusals_fall_back_to_the_unchanged_full_selection(main_push, defect, monkeypatch):
    validation, current, api = main_push
    original = copy.deepcopy(validation)
    if defect in {"tree", "base", "contract", "head"}:
        api.evidence[{"contract": "contract_digest"}.get(defect, defect)] = "0" * 40
    elif defect == "tools":
        api.evidence["toolchains"]["tests"]["python"] = "0.0.0"
    elif defect == "result-tools":
        api.evidence["validation_results"]["tests"]["tools"] = {}
    elif defect == "result-failed":
        api.evidence["validation_results"]["tests"]["status"] = "failure"
    elif defect == "result-source":
        api.evidence["validation_results"]["tests"]["source"]["run_id"] = "456"
    elif defect == "selection":
        api.evidence["suites"].remove("browser")
    elif defect == "failed-run":
        api.run["conclusion"] = "failure"
    elif defect == "in-progress":
        api.run["status"] = "in_progress"
    elif defect == "no-runs":
        api.runs.clear()
    elif defect == "failed-gate":
        api.gate["conclusion"] = "failure"
    elif defect == "missing-gate":
        api.jobs.clear()
    elif defect == "ambiguous-gate":
        api.jobs.append(api.gate)
    elif defect == "missing-evidence":
        api.artifacts.clear()
    elif defect == "expired":
        api.artifact["expired"] = True
    elif defect == "expires-at":
        api.artifact["expires_at"] = "2026-11-10T00:00:00Z"
    elif defect == "ambiguous-evidence":
        api.artifacts.append(api.artifact)
    elif defect == "wrong-artifact-run":
        api.artifact["workflow_run"]["id"] = 456
    elif defect == "wrong-gate-execution":
        api.artifact["created_at"] = "2026-11-09T00:02:00Z"
    elif defect == "fork":
        api.pr["head"]["repo"]["full_name"] = "someone/fork"
    elif defect == "run-fork":
        api.run["head_repository"]["id"] = 18
    elif defect == "multiple-prs":
        api.pulls.append({"number": 43})
    elif defect == "no-pr":
        api.pulls.clear()
    elif defect == "not-merged":
        api.pr["merged"] = False
    elif defect == "merge-sha":
        api.pr["merge_commit_sha"] = "0" * 40
    elif defect == "dispatch-run":
        api.run["event"] = "workflow_dispatch"
    elif defect == "dispatch-evidence":
        api.evidence["validation_context"]["event"] = "workflow_dispatch"
    elif defect == "other-pr":
        api.evidence["validation_context"]["pr_number"] = 43
    elif defect == "wrong-workflow":
        api.run["workflow_id"] = 8
    elif defect == "api-error":
        monkeypatch.setattr(api, "get", lambda path: (_ for _ in ()).throw(subprocess.CalledProcessError(1, ["gh", "api", path])))
    api.pack()
    if defect == "wrong-digest":
        api.archive_bytes += b"substituted"
    elif defect == "bad-zip":
        api.archive_bytes = b"invalid ZIP"
        api.artifact["digest"] = "sha256:" + hashlib.sha256(api.archive_bytes).hexdigest()
    assert reuse.select_reuse(api, validation, current, attempt=1, now=NOW) is None
    assert validation == original


@pytest.mark.parametrize("event,ref,attempt", [("pull_request", "refs/pull/42/merge", 1), ("workflow_dispatch", "refs/heads/main", 1),
                                               ("push", "refs/heads/feature", 1), ("push", "refs/heads/main", 2)])
def test_other_events_and_all_job_reruns_validate_without_a_lookup(main_push, event, ref, attempt):
    validation, current, api = main_push
    current.update(event=event, ref=ref)
    assert reuse.select_reuse(api, validation, current, attempt=attempt, now=NOW) is None
    assert api.requests == []


@pytest.mark.parametrize("defect", ["expired", "tree", "attempt", "source", "image-reuse", "missing-admission", "late-admission"])
def test_gate_refuses_stale_or_rebound_selector_proof(main_push, defect):
    validation, current, api = main_push
    validation["main_reuse"] = reuse.select_reuse(api, validation, current, attempt=1, now=NOW)
    attempt = 1
    if defect == "expired":
        validation["main_reuse"]["artifact"]["expires_at"] = "2026-11-09T00:00:00Z"
    elif defect == "tree":
        validation["main_reuse"]["evidence"]["tree"] = "0" * 40
    elif defect == "attempt":
        attempt = 2
    elif defect == "source":
        validation["main_reuse"]["main_source"] = {"run_id": "456", "run_attempt": 1}
    elif defect == "missing-admission":
        validation["main_reuse"].pop("admitted_at")
    elif defect == "late-admission":
        validation["main_reuse"]["admitted_at"] = validation["main_reuse"]["artifact"]["expires_at"]
    else:
        validation["main_reuse"]["reused_suites"].append("production-images")
    with pytest.raises((DeploymentError, KeyError)):
        reuse.verified_reuse(validation, current, run_id="789", attempt=attempt)


def test_gate_keeps_proof_admitted_30_seconds_before_expiry_after_60_seconds(main_push, monkeypatch):
    validation, current, api = main_push
    api.artifact["expires_at"] = (NOW + timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    validation["main_reuse"] = json.loads(json.dumps(reuse.select_reuse(api, validation, current, attempt=1, now=NOW)))

    class GateClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW + timedelta(seconds=60)

    monkeypatch.setattr(reuse, "datetime", GateClock)
    assert reuse.verified_reuse(validation, current, run_id="789", attempt=1) == REUSABLE_SUITES
    assert validation["main_reuse"]["admitted_at"] == "2026-11-10T00:00:00Z"
    validation["main_reuse"]["evidence"]["tree"] = "0" * 40
    with pytest.raises(DeploymentError, match="tree"):
        reuse.verified_reuse(validation, current, run_id="789", attempt=1)


def test_admission_checks_freshness_after_download_not_before_lookup(main_push, monkeypatch):
    validation, current, api = main_push
    api.artifact["expires_at"] = (NOW + timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    clock = [NOW]
    archive = api.archive

    def download(repository, identifier):
        clock[0] = NOW + timedelta(seconds=60)
        return archive(repository, identifier)

    class AdmissionClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(reuse, "datetime", AdmissionClock)
    monkeypatch.setattr(api, "archive", download)
    assert reuse.select_reuse(api, validation, current, attempt=1) is None


@pytest.mark.parametrize("stall", ["metadata", "retry", "archive"])
def test_cli_overall_deadline_stops_transport_and_emits_unchanged_full_plan(main_push, tmp_path, stall):
    validation, current, api = main_push
    prefix = f"repos/{REPOSITORY}"
    responses = {
        f"{prefix}/commits/{current['sha']}/pulls?per_page=100": [api.pulls],
        f"{prefix}/pulls/42": api.pr,
        f"{prefix}/actions/workflows/ci.yml": {"id": 7, "path": reuse.WORKFLOW},
        f"{prefix}/actions/workflows/ci.yml/runs?event=pull_request&head_sha={api.pr['head']['sha']}&per_page=100":
            [{"total_count": 1, "workflow_runs": api.runs}],
        f"{prefix}/actions/runs/123/attempts/2": api.run,
        f"{prefix}/actions/runs/123/attempts/2/jobs?per_page=100": [{"total_count": 1, "jobs": api.jobs}],
        f"{prefix}/actions/runs/123/artifacts?per_page=100": [{"total_count": 1, "artifacts": api.artifacts}],
    }
    # A shell transport keeps each metadata call to milliseconds, so the 1 s
    # deadline always reaches the stalled call, even on a loaded runner.
    fixtures = tmp_path / "responses"
    fixtures.mkdir()
    names = {path: path.translate(str.maketrans("/?&=", "____")) for path in responses}
    assert len(set(names.values())) == len(names)
    for path, value in responses.items():
        (fixtures / names[path]).write_text(json.dumps(value) + "\n")
    shim = tmp_path / "gh"
    shim.write_text("""#!/bin/sh
for path; do :; done
if [ "$STALL" != archive ] || [ "${path%/zip}" != "$path" ]; then
  echo $$ > "$TRANSPORT_FIXTURE/transport-pid"
  if [ "$STALL" = retry ]; then echo 'gh: Service Unavailable (HTTP 503)' >&2; exit 1; fi
  exec sleep 30
fi
exec cat "$TRANSPORT_FIXTURE/responses/$(printf '%s' "$path" | tr '/?&=' '____')"
""")
    shim.chmod(0o755)
    plan_path, output = tmp_path / "plan.json", tmp_path / "outputs"
    plan_path.write_text(json.dumps(validation))
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"before": current["before"]}))
    environment = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
                   "TRANSPORT_FIXTURE": str(tmp_path), "STALL": stall, "GITHUB_EVENT_NAME": "push",
                   "GITHUB_REF": current["ref"], "GITHUB_REPOSITORY": REPOSITORY, "GITHUB_SHA": current["sha"],
                   "GITHUB_EVENT_PATH": str(event), "GITHUB_RUN_ATTEMPT": "1"}
    started = monotonic()
    with subprocess.Popen([sys.executable, "-c", "from scripts import ci_main_reuse as m; m.LOOKUP_TIMEOUT_SECONDS = 1; m.main()",
                           "--plan", str(plan_path), "--github-output", str(output)],
                          cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(timeout=5)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=5)
    assert process.returncode == 0, stderr
    assert monotonic() - started < 4
    assert "overall deadline" in stderr
    if stall == "retry":
        assert "retrying in 5s" in stderr
    assert json.loads(plan_path.read_text()) == validation
    assert output.read_text() == "reuse_validation=false\n"
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "transport-pid").read_text()), 0)


def test_new_all_job_plan_discards_attempt_specific_reuse_without_changing_selection(main_push):
    validation, current, api = main_push
    first = copy.deepcopy(validation)
    first["main_reuse"] = reuse.select_reuse(api, first, current, attempt=1, now=NOW)
    second = copy.deepcopy(validation)
    second["source"]["run_attempt"] = 2
    assert source.select_plan([first, second], run_id="789", attempt=2) == second


def test_workflow_reuses_only_pure_jobs_and_retains_pr_required_checks():
    workflow = load_workflow(ROOT / reuse.WORKFLOW)
    jobs = workflow["jobs"]
    for suite in REUSABLE_SUITES:
        expression = jobs[suite]["if"].replace("needs.geospatial-changes", 'needs["geospatial-changes"]')
        for reused, expected in (("false", "true"), ("true", "false")):
            needs = {"geospatial-changes": {"outputs": {"reuse_validation": reused, "should_run": "true", "browser": "true", "control_plane": "true"}}}
            result = subprocess.run(["node", "-e", "console.log(Function('needs', 'return ' + process.argv[1])(JSON.parse(process.argv[2])))",
                                     expression, json.dumps(needs)], capture_output=True, text=True, check=True)
            assert result.stdout.strip() == expected
    assert all("reuse_validation" not in jobs[job]["if"] for job in ("production-images", "sdk-validation"))
    assert jobs["ci-ready"]["if"] == "always()"
    for job in ("lint", "tests", "geospatial-changes", "ci-ready"):
        assert jobs[job].get("name", job) == job
    upload = jobs["ci-ready"]["steps"][-1]
    assert upload["with"]["retention-days"] >= 14
    selector = jobs["geospatial-changes"]
    assert selector["permissions"] == {"contents": "read", "actions": "read", "pull-requests": "read"}
    assert any("ci_main_reuse.py" in step.get("run", "") for step in selector["steps"])


@pytest.mark.parametrize("scope", ["core", "full"])
def test_serialized_selector_plan_and_real_gate_emit_pr_and_main_provenance(tmp_path, monkeypatch, scope):
    root, git = fixture_repo(tmp_path)
    base = git("rev-parse", "HEAD")
    (root / ("guide.md" if scope == "core" else "new-component.bin")).write_bytes(b"changed")
    git("add", ".")
    git("commit", "-m", "fixture full selection")
    validation = preflight.make_plan(root, base, "HEAD")
    validation["source"] = {"run_id": "789", "run_attempt": 1}
    api = Actions(validation)
    # Use a real future expiry for the CLI clock while keeping the source window.
    api.artifact["expires_at"] = "2099-01-01T00:00:00Z"
    api.pack()
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"before": base}))
    for key, value in {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main", "GITHUB_REPOSITORY": REPOSITORY,
                       "GITHUB_SHA": validation["tested_sha"], "GITHUB_RUN_ID": "789", "GITHUB_RUN_ATTEMPT": "1",
                       "GITHUB_EVENT_PATH": str(event)}.items():
        monkeypatch.setenv(key, value)
    plan_path, output = tmp_path / "plan.json", tmp_path / "outputs"
    plan_path.write_text(json.dumps(validation))
    monkeypatch.setattr(reuse, "GitHub", lambda: api)
    monkeypatch.setattr(sys, "argv", ["ci_main_reuse.py", "--plan", str(plan_path), "--github-output", str(output)])
    reuse.main()
    assert output.read_text() == "reuse_validation=true\n"
    selected = json.loads(plan_path.read_text())
    assert selected["suites"] == validation["suites"]
    results = tmp_path / "results"
    results.mkdir()
    jobs = jobs_for(validation)
    for suite in REUSABLE_SUITES:
        jobs[suite]["result"] = "skipped"
    for result in results_for(validation):
        if result["suite"] not in REUSABLE_SUITES:
            path = results / result["suite"] / "result.json"
            path.parent.mkdir()
            path.write_text(json.dumps(dict(result, source=validation["source"])))
    jobs_path, evidence = tmp_path / "jobs.json", tmp_path / "evidence.json"
    jobs_path.write_text(json.dumps(jobs))
    monkeypatch.chdir(root)
    monkeypatch.setattr(preflight, "prove_attempts", lambda *args: {1: {"geospatial-changes", "production-images", "sdk-validation"}})
    monkeypatch.setattr(sys, "argv", ["ci_preflight.py", "verify", "--plan", str(plan_path), "--results", str(results),
                                      "--jobs", str(jobs_path), "--output", str(evidence)])
    assert preflight.main() == 0
    admitted = json.loads(evidence.read_text())
    assert admitted["source"] == validation["source"]
    assert admitted["main_reuse"]["run"]["id"] == 123
    assert admitted["main_reuse"]["evidence"]["head"] == "b" * 40
    assert set(admitted["suite_artifacts"]) == ({"production-images", "sdk-node22", "sdk-node24"} if scope == "full" else set())
    assert admitted["validation_results"]["tests"]["source"]["run_id"] == "123"
    if scope == "full":
        assert admitted["validation_results"]["sdk-node24"]["source"]["run_id"] == "789"
