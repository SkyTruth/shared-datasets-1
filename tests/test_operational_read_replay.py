"""Replay reviewed alert signatures through a real gh subprocess boundary."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from scripts.github_api_read import read
from scripts import operational_retry


@pytest.fixture
def gh_replay(tmp_path, monkeypatch):
    executable = tmp_path / "gh"
    executable.write_text("#!" + sys.executable + "\n" + '''
import json, os, pathlib, sys
counter = pathlib.Path(os.environ["REPLAY_COUNTER"])
attempt = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(attempt + 1))
scenario = json.loads(os.environ["REPLAY_SCENARIO"])
result = scenario[min(attempt, len(scenario) - 1)]
assert sys.argv[1:4] == ["api", "--method", "GET"]
assert "--paginate" in sys.argv and "--slurp" in sys.argv
sys.stdout.write(result["stdout"])
sys.stderr.write(result.get("stderr", ""))
sys.exit(result.get("exit", 0))
''')
    executable.chmod(0o700)
    counter = tmp_path / "attempts"
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("REPLAY_COUNTER", str(counter))
    now, waits = [0.0], []

    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(operational_retry, "monotonic", lambda: now[0])
    monkeypatch.setattr(operational_retry, "sleep", sleep)
    return counter, waits


@pytest.mark.parametrize("signature", ["gh: HTTP 500", "gh: Server Error (HTTP 500)", "gh: Bad Gateway (HTTP 502)"])
def test_reviewed_github_outages_discard_partial_pagination(signature, gh_replay, monkeypatch):
    counter, waits = gh_replay
    pages = [{"jobs": [{"id": 2}]}, {"jobs": [{"id": 3}]}]
    monkeypatch.setenv("REPLAY_SCENARIO", json.dumps([
        {"stdout": '[{"jobs":[{"id":1}]}]', "stderr": signature, "exit": 1},
        {"stdout": json.dumps(pages)},
    ]))
    with operational_retry.operation(profile=operational_retry.UNATTENDED):
        result = json.loads(read("repos/SkyTruth/shared-datasets-1/actions/runs/39278377548/jobs", paginate=True))
    assert result == pages
    assert counter.read_text() == "2"
    assert waits == [5]


def test_unresolved_outage_exhausts_one_operation_budget(gh_replay, monkeypatch):
    counter, waits = gh_replay
    monkeypatch.setenv("REPLAY_SCENARIO", json.dumps([{"stdout": "partial", "stderr": "gh: HTTP 503", "exit": 1}]))
    with operational_retry.operation(profile=operational_retry.UNATTENDED), pytest.raises(subprocess.CalledProcessError):
        read("repos/SkyTruth/shared-datasets-1/actions/runs/39278377548/jobs", paginate=True)
    assert counter.read_text() == "8"
    assert waits == [5, 15, 40, 120, 240, 480, 840]


@pytest.mark.parametrize("signature", ["gh: HTTP 401", "gh: HTTP 403", "unsupported response"])
def test_replay_cannot_convert_permissions_or_unknown_errors_to_transient(signature, gh_replay, monkeypatch):
    counter, waits = gh_replay
    monkeypatch.setenv("REPLAY_SCENARIO", json.dumps([{"stdout": "partial", "stderr": signature, "exit": 1}]))
    with operational_retry.operation(profile=operational_retry.UNATTENDED), pytest.raises(subprocess.CalledProcessError):
        read("repos/SkyTruth/shared-datasets-1/actions/runs/38495751381/jobs", paginate=True)
    assert counter.read_text() == "1"
    assert waits == []
