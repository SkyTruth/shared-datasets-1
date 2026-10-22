"""Closed replay queries retain strict pagination and exact source admission."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import subprocess
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import deployment_revision as deployments
from scripts import github_api_read
from scripts import slack_incidents as incidents


NOW = datetime(2026, 10, 22, 15, 32, 10, 892000, tzinfo=timezone.utc)
SINCE = "2026-10-06T08:00:00Z"
REPOSITORY = {"id": 77, "full_name": incidents.REPOSITORY}


def run(identifier, created="2026-10-22T15:30:00Z", **changes):
    value = {
        "id": identifier, "name": "Bucket hygiene audit", "run_attempt": 1,
        "head_sha": "a" * 40, "head_branch": "main", "event": "schedule",
        "status": "completed", "conclusion": "failure", "workflow_id": 8,
        "path": ".github/workflows/bucket-hygiene-audit.yml",
        "repository": REPOSITORY, "head_repository": REPOSITORY,
        "created_at": created, "updated_at": "2026-10-22T15:32:00Z",
    }
    return {**value, **changes}


class CLI:
    """Only the external gh process is fake; readers and admission are real."""
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else [run(100), run(101)]
        self.arriving = None
        self.commands = []
        self.query_pages = None
        self.authoritative_changes = {}
        self.error = None

    @staticmethod
    def within(row, created):
        if created.startswith(">="):
            return row["created_at"] >= created[2:]
        start, end = created.split("..")
        return start <= row["created_at"] <= end

    def __call__(self, command, **kwargs):
        self.commands.append(deepcopy(command))
        assert command[:4] == ["gh", "api", "--method", "GET"]
        assert kwargs["stderr"] == subprocess.PIPE and 0 < kwargs["timeout"] <= 60
        path = command[-1]
        if "/actions/runs?" in path:
            assert command[4:-1] == ["-H", "Cache-Control: no-cache", "--paginate", "--slurp"]
            query = parse_qs(urlsplit(path).query)
            assert query["branch"] == ["main"] and query["per_page"] == ["100"]
            if self.error is not None:
                raise self.error
            if self.query_pages is not None:
                return self.query_pages
            before = [row for row in self.rows if self.within(row, query["created"][0])]
            first = {"total_count": len(before), "workflow_runs": before[:1]}
            if self.arriving is not None:
                self.rows.insert(0, self.arriving)
                self.arriving = None
            after = [row for row in self.rows if self.within(row, query["created"][0])]
            return json.dumps([first, {"total_count": len(after), "workflow_runs": after[1:]}]).encode()
        if path == "repos/" + incidents.REPOSITORY:
            return json.dumps(REPOSITORY).encode()
        if path.endswith("/actions/workflows/8"):
            return json.dumps({"id": 8, "path": ".github/workflows/bucket-hygiene-audit.yml"}).encode()
        if "/attempts/" in path:
            assert path.endswith("/attempts/1/jobs?per_page=100")
            return json.dumps([{"total_count": 0, "jobs": []}]).encode()
        if "/actions/runs/" in path:
            identifier = int(path.rsplit("/", 1)[1])
            current = next(row for row in self.rows if row["id"] == identifier)
            return json.dumps({**current, **self.authoritative_changes}).encode()
        raise AssertionError("Unexpected GitHub read " + path)


def execute(monkeypatch, cli, *, now=NOW, since=SINCE):
    monkeypatch.setattr(github_api_read.subprocess, "check_output", cli)
    return incidents.replay_observations(deployments.GitHub(), {}, since, now=now)


def test_real_cli_closed_window_excludes_arrivals_until_next_overlapping_pass(monkeypatch):
    cli = CLI()
    cli.arriving = run(102, "2026-10-22T15:32:10Z")
    first = execute(monkeypatch, cli)
    assert [(item["run_id"], item["attempt"], item["sha"]) for item in first] == [(100, 1, "a" * 40), (101, 1, "a" * 40)]
    query = parse_qs(urlsplit(cli.commands[0][-1]).query)
    assert query["created"] == ["2026-10-21T15:32:09Z..2026-10-22T15:32:09Z"]
    assert len([command for command in cli.commands if "/actions/runs?" in command[-1]]) == 1
    second = execute(monkeypatch, cli, now=NOW + timedelta(minutes=5))
    assert {item["run_id"] for item in second} == {100, 101, 102}


def test_closed_window_is_utc_activation_limited_and_excludes_current_second(monkeypatch):
    cli = CLI(rows=[])
    assert execute(monkeypatch, cli, now=NOW.astimezone(timezone(timedelta(hours=-4))), since="2026-10-22T15:00:00Z") == []
    assert parse_qs(urlsplit(cli.commands[0][-1]).query)["created"] == ["2026-10-22T15:00:00Z..2026-10-22T15:32:09Z"]


@pytest.mark.parametrize("since", ["2026-10-22T15:32:10Z", "2026-10-22T15:33:00Z"])
def test_partial_second_or_future_activation_cannot_construct_a_replay(monkeypatch, since):
    cli = CLI()
    with pytest.raises(incidents.IncidentError, match="replay activation"):
        execute(monkeypatch, cli, since=since)
    assert not cli.commands


@pytest.mark.parametrize("pages", [
    b"malformed JSON", b"{}",
    json.dumps([{"total_count": "1", "workflow_runs": [run(100)]}]).encode(),
    json.dumps([{"total_count": 1, "workflow_runs": [run(100)]}, {"total_count": 2, "workflow_runs": []}]).encode(),
    json.dumps([{"total_count": 2, "workflow_runs": [run(100)]}]).encode(),
    json.dumps([{"total_count": 2, "workflow_runs": [run(100), run(100)]}]).encode(),
    json.dumps([{"total_count": 1, "workflow_runs": [run(0)]}]).encode(),
    json.dumps([{"total_count": 1, "workflow_runs": [run(True)]}]).encode(),
])
def test_bad_or_partial_enumeration_is_not_retried_or_used(monkeypatch, pages):
    cli = CLI()
    cli.query_pages = pages
    with pytest.raises((ValueError, deployments.DeploymentError, incidents.IncidentError)):
        execute(monkeypatch, cli)
    assert len(cli.commands) == 1


def test_authentication_failure_does_not_retry_or_change_the_window(monkeypatch):
    cli = CLI()
    cli.error = subprocess.CalledProcessError(1, "gh", stderr=b"gh: HTTP 403")
    with pytest.raises(subprocess.CalledProcessError):
        execute(monkeypatch, cli)
    assert len(cli.commands) == 1


@pytest.mark.parametrize("changed", [{"head_sha": "b" * 40}, {"run_attempt": 0}])
def test_closed_enumeration_does_not_relax_exact_revision_or_attempt_admission(monkeypatch, changed):
    cli = CLI()
    cli.authoritative_changes = changed
    with pytest.raises(ValueError, match="trusted main|completed source attempt"):
        execute(monkeypatch, cli)
    assert not any("/attempts/" in command[-1] for command in cli.commands)


def test_obsolete_attempt_stays_a_noop_without_substituting_the_new_attempt(monkeypatch):
    cli = CLI()
    cli.authoritative_changes = {"run_attempt": 2}
    assert execute(monkeypatch, cli) == []
    assert not any("/attempts/" in command[-1] for command in cli.commands)
