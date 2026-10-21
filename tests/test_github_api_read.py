"""Exercise GitHub read retries through the real gh subprocess boundary."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from scripts import dataset_mutation_authorization as authorization
from scripts import deployment_revision as deployments
from scripts import github_api_read as transport


CLIENTS = (authorization.GitHub, deployments.GitHub)
PATH = "repos/SkyTruth/shared-datasets-1/actions/runs/38508512742/attempts/1"


@pytest.fixture
def gh(tmp_path, monkeypatch):
    responses = tmp_path / "responses.json"
    log = tmp_path / "calls.jsonl"
    binary = tmp_path / "gh"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys, time\n"
        f"responses = json.loads(pathlib.Path({str(responses)!r}).read_text())\n"
        f"log = pathlib.Path({str(log)!r})\n"
        "count = len(log.read_text().splitlines()) if log.exists() else 0\n"
        "with log.open('a') as stream: stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "response = responses[min(count, len(responses) - 1)]\n"
        "sys.stdout.write(response.get('stdout', ''))\n"
        "sys.stderr.write(response.get('stderr', ''))\n"
        "if response.get('delayed_exit'):\n"
        "    sys.stdout.flush(); sys.stderr.flush(); os.close(1); os.close(2)\n"
        "    time.sleep(0.05); os._exit(response.get('code', 0))\n"
        "sys.exit(response.get('code', 0))\n"
    )
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])

    def configure(values):
        responses.write_text(json.dumps(values))
        return log

    return configure


def outage(status=502, *, stdout=""):
    return {"code": 1, "stderr": f"gh: Server Error (HTTP {status})\n", "stdout": stdout}


@pytest.mark.parametrize("client", CLIENTS)
def test_observed_502_recovers_same_exact_ci_attempt(client, gh):
    log = gh([outage(), outage(), {"stdout": '{"id": 38508512742, "run_attempt": 1}'}])
    with patch("scripts.github_api_read.sleep") as sleep:
        assert client().get(PATH) == {"id": 38508512742, "run_attempt": 1}
    assert [call.args for call in sleep.call_args_list] == [(5,), (15,)]
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls == [calls[0]] * 3
    assert calls[0] == ["api", "--method", "GET", PATH]


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_exhausted_server_errors_never_accept_stdout(client, status, gh, capsys):
    log = gh([outage(status, stdout='{"id": 38508512742}')])
    with patch("scripts.github_api_read.sleep") as sleep, pytest.raises(subprocess.CalledProcessError):
        client().get(PATH)
    assert len(log.read_text().splitlines()) == 3
    assert [call.args for call in sleep.call_args_list] == [(5,), (15,)]
    assert "attempt 3/3 failed" in capsys.readouterr().err


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("stderr", [
    *[f"gh: Rejected (HTTP {code})\n" for code in (400, 401, 403, 404, 422, 429, 501)],
    "not logged into GitHub\n",
    "gh: Server Error (HTTP 502)\nadditional unknown error\n",
])
def test_non_transient_and_unknown_errors_fail_immediately(client, stderr, gh):
    log = gh([{"code": 1, "stderr": stderr}, {"stdout": "{}"}])
    with patch("scripts.github_api_read.sleep") as sleep, pytest.raises(subprocess.CalledProcessError):
        client().get(PATH)
    sleep.assert_not_called()
    assert len(log.read_text().splitlines()) == 1


@pytest.mark.parametrize("client", CLIENTS)
def test_connection_failure_recovers(client, gh):
    # Closing output before exit exercises subprocess's separate timeout polling.
    log = gh([{"code": 1, "stderr": transport.CONNECTION_ERROR.decode()}, {"stdout": "{}", "delayed_exit": True}])
    with patch("scripts.github_api_read.sleep") as sleep:
        assert client().get(PATH) == {}
    sleep.assert_called_once_with(5)
    assert len(log.read_text().splitlines()) == 2


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("pages", [
    [{"total_count": 0, "jobs": []}],
    [{"total_count": 1, "jobs": [{"id": 1}]}],
    [{"total_count": 2, "jobs": [{"id": 1}]}, {"total_count": 2, "jobs": [{"id": 2}]}],
])
def test_pagination_discards_partial_failure_and_preserves_all_pages(client, pages, gh):
    log = gh([outage(stdout='[{"total_count": 1, "jobs": [{"id": 999}]}]'), {"stdout": json.dumps(pages)}])
    with patch("scripts.github_api_read.sleep") as sleep:
        assert client().pages(PATH, field="jobs") == [row for page in pages for row in page["jobs"]]
    sleep.assert_called_once_with(5)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls == [calls[0]] * 2
    assert calls[0] == ["api", "--method", "GET", "--paginate", "--slurp", PATH]


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("response", [
    "not JSON",
    '[{"total_count": 2, "jobs": [{"id": 1}]}]',
    '[{"total_count": 2, "jobs": []}, {"total_count": 3, "jobs": []}]',
])
def test_response_validation_after_recovery_is_not_retried(client, response, gh):
    log = gh([outage(), {"stdout": response}, {"stdout": '[{"total_count": 0, "jobs": []}]'}])
    with patch("scripts.github_api_read.sleep") as sleep, pytest.raises((ValueError, authorization.Error, deployments.DeploymentError)):
        client().pages(PATH, field="jobs")
    sleep.assert_called_once_with(5)
    assert len(log.read_text().splitlines()) == 2


@pytest.mark.parametrize("recover", [True, False])
def test_read_timeouts_are_bounded_and_never_accept_partial_output(recover):
    timeout = subprocess.TimeoutExpired(["gh", "api"], 60, output=b'{"partial": true}')
    responses = [timeout, b"{}"] if recover else [timeout] * 3
    with patch.object(transport.subprocess, "check_output", side_effect=responses) as command, patch("scripts.github_api_read.sleep") as sleep:
        if recover:
            assert deployments.GitHub().get(PATH) == {}
        else:
            with pytest.raises(subprocess.TimeoutExpired):
                deployments.GitHub().get(PATH)
    assert command.call_count == (2 if recover else 3)
    assert all(call.kwargs["timeout"] == 60 for call in command.call_args_list)
    assert len(sleep.call_args_list) == (1 if recover else 2)


def test_deployment_write_is_never_replayed(gh):
    log = gh([outage(), {"stdout": "{}"}])
    with patch("scripts.github_api_read.sleep") as sleep, pytest.raises(subprocess.CalledProcessError):
        deployments.GitHub().post("repos/SkyTruth/shared-datasets-1/deployments", {"ref": "a" * 40})
    sleep.assert_not_called()
    assert len(log.read_text().splitlines()) == 1


@pytest.mark.parametrize("wrong_revision", [False, True])
def test_ci_evidence_still_requires_exact_revision_after_transport_recovery(wrong_revision, gh):
    repository = {"id": 17, "full_name": "SkyTruth/shared-datasets-1"}
    revision = "a" * 40
    run = {
        "id": 38508512742, "run_attempt": 1, "workflow_id": 12,
        "path": ".github/workflows/ci.yml", "event": "push", "head_branch": "main",
        "head_sha": "b" * 40 if wrong_revision else revision,
        "repository": repository, "head_repository": repository,
    }
    evidence = [
        {"stdout": json.dumps(repository)},
        {"stdout": '{"id": 12, "path": ".github/workflows/ci.yml"}'},
        outage(),
        {"stdout": json.dumps(run)},
        {"stdout": '[{"total_count": 1, "jobs": [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]}]'},
    ]
    log = gh(evidence)
    with patch("scripts.github_api_read.sleep") as sleep:
        if wrong_revision:
            with pytest.raises(deployments.DeploymentError, match="executor differs"):
                deployments.verify_ci(deployments.GitHub(), repository["full_name"], revision, run["id"], 1)
        else:
            assert deployments.verify_ci(deployments.GitHub(), repository["full_name"], revision, run["id"], 1) == run
    sleep.assert_called_once_with(5)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[2] == calls[3] == ["api", "--method", "GET", PATH]
    assert len(calls) == (4 if wrong_revision else 5)
    assert all(call[:3] == ["api", "--method", "GET"] for call in calls)
