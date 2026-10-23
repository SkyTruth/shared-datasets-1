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
        "import json, pathlib, sys\n"
        f"responses = json.loads(pathlib.Path({str(responses)!r}).read_text())\n"
        f"log = pathlib.Path({str(log)!r})\n"
        "count = len(log.read_text().splitlines()) if log.exists() else 0\n"
        "with log.open('a') as stream: stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "response = responses[min(count, len(responses) - 1)]\n"
        "sys.stdout.write(response.get('stdout', ''))\n"
        "sys.stderr.write(response.get('stderr', ''))\n"
        "sys.exit(response.get('code', 0))\n"
    )
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])

    def configure(values):
        responses.write_text(json.dumps(values))
        return log

    return configure


def outage(status=502, *, stdout="", plain=False):
    stderr = f"gh: HTTP {status}\n" if plain else f"gh: Server Error (HTTP {status})\n"
    return {"code": 1, "stderr": stderr, "stdout": stdout}


@pytest.mark.parametrize("client", CLIENTS)
def test_observed_502_recovers_same_exact_ci_attempt(client, gh):
    log = gh([outage(), outage(), {"stdout": '{"id": 38508512742, "run_attempt": 1}'}])
    with patch.object(transport, "time") as clock:
        assert client().get(PATH) == {"id": 38508512742, "run_attempt": 1}
    assert [call.args for call in clock.sleep.call_args_list] == [(5,), (15,)]
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls == [calls[0]] * 3
    assert calls[0] == ["api", "--method", "GET", PATH]


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("path,paginate,response", [
    # Repository read observed in 38495751381/2.
    ("repos/SkyTruth/shared-datasets-1", False, {"id": 1213606315}),
    # Deployment-history read observed in 38496427534/1.
    ("repos/SkyTruth/shared-datasets-1/deployments?environment=production-typescript-sdk&per_page=100",
     True, [[{"id": 7}]]),
])
def test_observed_plain_500_recovers_same_read(client, path, paginate, response, gh, capsys):
    log = gh([outage(500, plain=True, stdout='{"untrusted": true}'), {"stdout": json.dumps(response)}])
    with patch.object(transport, "time") as clock:
        result = client().pages(path) if paginate else client().get(path)
    assert result == ([{"id": 7}] if paginate else response)
    clock.sleep.assert_called_once_with(5)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    command = ["api", "--method", "GET"] + (["--paginate", "--slurp"] if paginate else []) + [path]
    assert calls == [command, command]
    assert "attempt 1/3 failed (HTTP 500); retrying in 5s" in capsys.readouterr().err


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("status", [500, 502, 503, 504])
@pytest.mark.parametrize("plain", [False, True])
def test_exhausted_server_errors_never_accept_stdout(client, status, plain, gh, capsys):
    log = gh([outage(status, plain=plain, stdout='{"id": 38508512742}')])
    with patch.object(transport, "time") as clock, pytest.raises(subprocess.CalledProcessError):
        client().get(PATH)
    assert len(log.read_text().splitlines()) == 3
    assert [call.args for call in clock.sleep.call_args_list] == [(5,), (15,)]
    assert "attempt 3/3 failed" in capsys.readouterr().err


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("stderr", [
    *[f"gh: Rejected (HTTP {code})\n" for code in (400, 401, 403, 404, 422, 429, 501)],
    *[f"gh: HTTP {code}\n" for code in (400, 401, 403, 404, 422, 429, 501)],
    "gh: HTTP 500)\n",
    "gh: Server Error (HTTP 500\n",
    "gh: HTTP 500\nadditional unknown error\n",
    "not logged into GitHub\n",
    "gh: Server Error (HTTP 502)\nadditional unknown error\n",
])
def test_non_transient_and_unknown_errors_fail_immediately(client, stderr, gh):
    log = gh([{"code": 1, "stderr": stderr}, {"stdout": "{}"}])
    with patch.object(transport, "time") as clock, pytest.raises(subprocess.CalledProcessError):
        client().get(PATH)
    clock.sleep.assert_not_called()
    assert len(log.read_text().splitlines()) == 1


@pytest.mark.parametrize("client", CLIENTS)
def test_connection_failure_recovers(client, gh):
    log = gh([{"code": 1, "stderr": transport.CONNECTION_ERROR.decode()}, {"stdout": "{}"}])
    with patch.object(transport, "time") as clock:
        assert client().get(PATH) == {}
    clock.sleep.assert_called_once_with(5)
    assert len(log.read_text().splitlines()) == 2


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("pages", [
    [{"total_count": 0, "jobs": []}],
    [{"total_count": 1, "jobs": [{"id": 1}]}],
    [{"total_count": 2, "jobs": [{"id": 1}]}, {"total_count": 2, "jobs": [{"id": 2}]}],
])
@pytest.mark.parametrize("plain", [False, True])
def test_pagination_discards_partial_failure_and_preserves_all_pages(client, pages, plain, gh):
    log = gh([outage(500, plain=plain, stdout='[{"total_count": 1, "jobs": [{"id": 999}]}]'), {"stdout": json.dumps(pages)}])
    with patch.object(transport, "time") as clock:
        assert client().pages(PATH, field="jobs") == [row for page in pages for row in page["jobs"]]
    clock.sleep.assert_called_once_with(5)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls == [calls[0]] * 2
    assert calls[0] == ["api", "--method", "GET", "--paginate", "--slurp", PATH]


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("response", [
    "not JSON",
    '[{"total_count": 2, "jobs": [{"id": 1}]}]',
    '[{"total_count": 2, "jobs": []}, {"total_count": 3, "jobs": []}]',
])
@pytest.mark.parametrize("plain", [False, True])
def test_response_validation_after_recovery_is_not_retried(client, response, plain, gh):
    log = gh([outage(500, plain=plain), {"stdout": response}, {"stdout": '[{"total_count": 0, "jobs": []}]'}])
    with patch.object(transport, "time") as clock, pytest.raises((ValueError, authorization.Error, deployments.DeploymentError)):
        client().pages(PATH, field="jobs")
    clock.sleep.assert_called_once_with(5)
    assert len(log.read_text().splitlines()) == 2


@pytest.mark.parametrize("recover", [True, False])
def test_read_timeouts_are_bounded_and_never_accept_partial_output(recover):
    timeout = subprocess.TimeoutExpired(["gh", "api"], 60, output=b'{"partial": true}')
    responses = [timeout, b"{}"] if recover else [timeout] * 3
    with patch.object(transport.subprocess, "check_output", side_effect=responses) as command, patch.object(transport, "time") as clock:
        if recover:
            assert deployments.GitHub().get(PATH) == {}
        else:
            with pytest.raises(subprocess.TimeoutExpired):
                deployments.GitHub().get(PATH)
    assert command.call_count == (2 if recover else 3)
    assert all(call.kwargs["timeout"] == 60 for call in command.call_args_list)
    assert len(clock.sleep.call_args_list) == (1 if recover else 2)


@pytest.mark.parametrize("plain", [False, True])
def test_deployment_write_is_never_replayed(plain, gh):
    log = gh([outage(500, plain=plain), {"stdout": "{}"}])
    with patch.object(transport, "time") as clock, pytest.raises(subprocess.CalledProcessError):
        deployments.GitHub().post("repos/SkyTruth/shared-datasets-1/deployments", {"ref": "a" * 40})
    clock.sleep.assert_not_called()
    assert len(log.read_text().splitlines()) == 1


@pytest.mark.parametrize("wrong_revision", [False, True])
@pytest.mark.parametrize("plain", [False, True])
def test_ci_evidence_still_requires_exact_revision_after_transport_recovery(wrong_revision, plain, gh):
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
        outage(500, plain=plain),
        {"stdout": json.dumps(run)},
        {"stdout": '[{"total_count": 1, "jobs": [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]}]'},
    ]
    log = gh(evidence)
    with patch.object(transport, "time") as clock:
        if wrong_revision:
            with pytest.raises(deployments.DeploymentError, match="executor differs"):
                deployments.verify_ci(deployments.GitHub(), repository["full_name"], revision, run["id"], 1)
        else:
            assert deployments.verify_ci(deployments.GitHub(), repository["full_name"], revision, run["id"], 1) == run
    clock.sleep.assert_called_once_with(5)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[2] == calls[3] == ["api", "--method", "GET", PATH]
    assert len(calls) == (4 if wrong_revision else 5)
    assert all(call[:3] == ["api", "--method", "GET"] for call in calls)
