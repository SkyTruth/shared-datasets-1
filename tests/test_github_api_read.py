"""Exercise GitHub read retries through the real gh subprocess boundary."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from scripts import dataset_mutation_authorization as authorization
from scripts import deployment_revision as deployments
from scripts import deployment_emission as emission
from scripts import github_api_read as transport
from scripts import publish_workflow
from scripts import operational_retry as retry


CLIENTS = (authorization.GitHub, deployments.GitHub)
PATH = "repos/SkyTruth/shared-datasets-1/actions/runs/38508512742/attempts/1"


@pytest.fixture
def gh(tmp_path, monkeypatch):
    responses = tmp_path / "responses.json"
    log = tmp_path / "calls.jsonl"
    binary = tmp_path / "gh"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys, time\n"
        f"responses = json.loads(pathlib.Path({str(responses)!r}).read_text())\n"
        f"log = pathlib.Path({str(log)!r})\n"
        "count = len(log.read_text().splitlines()) if log.exists() else 0\n"
        "with log.open('a') as stream: stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "response = responses[min(count, len(responses) - 1)]\n"
        "sys.stdout.write(response.get('stdout', ''))\n"
        "sys.stderr.write(response.get('stderr', ''))\n"
        "sys.stdout.flush(); sys.stderr.flush()\n"
        "time.sleep(response.get('delay', 0))\n"
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
    with patch.object(retry, "sleep") as sleep:
        assert client().get(PATH) == {"id": 38508512742, "run_attempt": 1}
    assert [call.args for call in sleep.call_args_list] == [(5,), (15,)]
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls == [calls[0]] * 3
    assert calls[0] == ["api", "--method", "GET", PATH]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
@pytest.mark.parametrize("reader", [lambda: emission.ReadOnlyGitHub().get(PATH), lambda: publish_workflow.gh_api_json(PATH)])
def test_plain_cli_http_errors_recover_for_operational_readers(status, reader, gh):
    log = gh([{"code": 1, "stderr": f"gh: HTTP {status}\n", "stdout": '{"partial": true}'}, {"stdout": "{}"}])
    with patch.object(retry, "sleep") as sleep:
        assert reader() == {}
    sleep.assert_called_once_with(5)
    assert len(log.read_text().splitlines()) == 2


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_exhausted_server_errors_never_accept_stdout(client, status, gh, capsys):
    log = gh([outage(status, stdout='{"id": 38508512742}')])
    with patch.object(retry, "sleep") as sleep, pytest.raises(subprocess.CalledProcessError):
        client().get(PATH)
    assert len(log.read_text().splitlines()) == 3
    assert [call.args for call in sleep.call_args_list] == [(5,), (15,)]
    assert "attempt 3/3 failed" in capsys.readouterr().err


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("stderr", [
    *[f"gh: Rejected (HTTP {code})\n" for code in (400, 401, 403, 404, 422, 429, 501)],
    "not logged into GitHub\n",
    *[f"gh: HTTP {code}\n" for code in (400, 401, 403, 404, 422, 429, 501)],
    "gh: Server Error (HTTP 502)\nadditional unknown error\n",
])
def test_non_transient_and_unknown_errors_fail_immediately(client, stderr, gh):
    log = gh([{"code": 1, "stderr": stderr}, {"stdout": "{}"}])
    with patch.object(retry, "sleep") as sleep, pytest.raises(subprocess.CalledProcessError):
        client().get(PATH)
    sleep.assert_not_called()
    assert len(log.read_text().splitlines()) == 1


@pytest.mark.parametrize("client", CLIENTS)
def test_connection_failure_recovers(client, gh):
    log = gh([{"code": 1, "stderr": transport.CONNECTION_ERROR.decode()}, {"stdout": "{}"}])
    with patch.object(retry, "sleep") as sleep:
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
    with patch.object(retry, "sleep") as sleep:
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
    with patch.object(retry, "sleep") as sleep, pytest.raises((ValueError, authorization.Error, deployments.DeploymentError)):
        client().pages(PATH, field="jobs")
    sleep.assert_called_once_with(5)
    assert len(log.read_text().splitlines()) == 2


@pytest.mark.parametrize("recover", [True, False])
def test_read_timeouts_are_bounded_and_never_accept_partial_output(recover):
    timeout = subprocess.TimeoutExpired(["gh", "api"], 60, output=b'{"partial": true}')
    responses = [timeout, b"{}"] if recover else [timeout] * 3
    with patch.object(transport.subprocess, "check_output", side_effect=responses) as command, patch.object(retry, "sleep") as sleep:
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
    with patch.object(retry, "sleep") as sleep, pytest.raises(subprocess.CalledProcessError):
        deployments.GitHub().post("repos/SkyTruth/shared-datasets-1/deployments", {"ref": "a" * 40})
    sleep.assert_not_called()
    assert len(log.read_text().splitlines()) == 1


def test_pr_receipt_deduplication_reads_all_comment_pages(gh, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "SkyTruth/shared-datasets-1")
    log = gh([outage(stdout='[[{"body": "old"}]]'), {"stdout": '[[{"body": "old"}], [{"body": "wanted-marker"}]]'}])
    with patch.object(retry, "sleep"):
        assert publish_workflow.pr_comment_has_marker("17", "wanted-marker")
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls == [["api", "--method", "GET", "--paginate", "--slurp",
                      "repos/SkyTruth/shared-datasets-1/issues/17/comments?per_page=100"]] * 2


def test_binary_artifact_read_retries_transport_but_not_bytes_validation(gh):
    log = gh([outage(stdout="truncated ZIP"), {"stdout": "complete ZIP"}])
    with patch.object(retry, "sleep"):
        assert authorization.GitHub().archive("SkyTruth/shared-datasets-1", 17) == b"complete ZIP"
    assert len(log.read_text().splitlines()) == 2


def test_streamed_artifact_retries_with_fresh_exclusive_destination(gh, tmp_path):
    log = gh([outage(stdout="truncated artifact"), {"stdout": "complete"}])
    destination = tmp_path / "image.zip"
    with patch.object(retry, "sleep") as sleep:
        transport.download(PATH, destination, max_bytes=100)
    sleep.assert_called_once_with(5)
    assert destination.read_bytes() == b"complete"
    assert len(log.read_text().splitlines()) == 2


def test_stream_timeout_kills_the_reader_and_discards_partial_bytes(gh, tmp_path, monkeypatch):
    log = gh([{"stdout": "partial", "delay": 10}, {"stdout": "complete"}])
    monkeypatch.setattr(transport, "READ_TIMEOUT_SECONDS", 1)
    destination = tmp_path / "image.zip"
    clock = Clock()
    native_sleep, native_clock = time.sleep, time.monotonic
    started = native_clock()
    with patch.object(retry, "monotonic", clock.monotonic), patch.object(retry, "sleep", clock.sleep):
        transport.download(PATH, destination, max_bytes=100)
        assert time.sleep is native_sleep and time.monotonic is native_clock
    assert native_clock() - started < 5
    assert clock.sleeps == [5]
    assert destination.read_bytes() == b"complete"
    assert len(log.read_text().splitlines()) == 2


def test_stream_limit_fails_immediately_and_removes_partial_artifact(gh, tmp_path):
    log = gh([{"stdout": "oversized bytes"}, {"stdout": "small"}])
    destination = tmp_path / "image.zip"
    with pytest.raises(ValueError, match="byte limit"):
        transport.download(PATH, destination, max_bytes=5)
    assert not destination.exists()
    assert len(log.read_text().splitlines()) == 1


def test_streamed_artifact_never_overwrites_or_removes_existing_bytes(gh, tmp_path):
    log = gh([{"stdout": "new"}])
    destination = tmp_path / "image.zip"
    destination.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        transport.download(PATH, destination, max_bytes=100)
    assert destination.read_bytes() == b"existing"
    assert not log.exists()


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_unattended_outage_has_eight_absolute_attempts_and_discards_partial_pages(gh):
    clock = Clock()
    log = gh([outage(stdout='[{"partial": true}]')])
    with patch.object(retry, "monotonic", clock.monotonic), patch.object(retry, "sleep", clock.sleep):
        with retry.operation(profile=retry.UNATTENDED), pytest.raises(subprocess.CalledProcessError):
            transport.read(PATH, paginate=True)
    assert len(log.read_text().splitlines()) == 8
    assert clock.sleeps == [5, 15, 40, 120, 240, 480, 840]
    assert clock.now == 1740


def test_nested_operations_and_later_reads_cannot_replenish_retry_budget(gh):
    clock = Clock()
    log = gh([outage(), {"stdout": "{}"}, outage()])
    with patch.object(retry, "monotonic", clock.monotonic), patch.object(retry, "sleep", clock.sleep):
        with retry.operation(profile=retry.UNATTENDED) as outer:
            assert transport.read(PATH) == b"{}"
            with retry.operation(profile=retry.UNATTENDED) as nested:
                assert nested is outer
                with pytest.raises(subprocess.CalledProcessError):
                    transport.read(PATH)
    assert len(log.read_text().splitlines()) == 9  # Eight failed attempts and one successful read.
    assert clock.sleeps == [5, 15, 40, 120, 240, 480, 840]


def test_slow_requests_cannot_extend_unattended_deadline():
    clock = Clock()
    attempts = []

    def action():
        attempts.append(clock.now)
        clock.now += 601
        raise TimeoutError("safe external read")

    with patch.object(retry, "monotonic", clock.monotonic), patch.object(retry, "sleep", clock.sleep):
        with retry.operation(profile=retry.UNATTENDED), pytest.raises(TimeoutError):
            retry.run_read(action, classify=lambda error: "timeout" if isinstance(error, TimeoutError) else None, label="read")
    assert attempts == [0, 601, 1202]
    assert clock.sleeps == [0, 0]


def test_validation_failure_is_not_retried_by_shared_budget():
    clock = Clock()
    with patch.object(retry, "monotonic", clock.monotonic), patch.object(retry, "sleep", clock.sleep):
        with retry.operation(profile=retry.UNATTENDED), pytest.raises(ValueError):
            retry.run_read(lambda: json.loads("wrong artifact bytes"), classify=transport.transport_failure, label="read")
    assert clock.sleeps == []


def test_transport_timeout_uses_only_remaining_operation_window():
    clock = Clock()
    with patch.object(retry, "monotonic", clock.monotonic):
        with retry.operation(profile=retry.UNATTENDED):
            clock.now = 1799
            assert retry.request_timeout(60) == 1
            clock.now = 1800
            with pytest.raises(TimeoutError, match="deadline"):
                retry.request_timeout(60)


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
    with patch.object(retry, "sleep") as sleep:
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
