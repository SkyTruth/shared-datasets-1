"""A periodic probe detects work without mutating incident or delivery state."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs

import pytest
import test_deployment_revision as deployment_fixtures

from scripts import catalog_drift_guard as catalog, collect_release_indexes as indexes
from scripts import deployment_emission as emissions, deployment_revision as deployments
from scripts import operational_safety_check as safety, operational_snapshot as snapshots
from scripts import slack_incidents as incidents
from scripts.incident_verification_cache import VerificationCache
from scripts.workflow_identity import WORKFLOW_NAMES
from test_deployment_emission import run as emitter_run, verified
from test_incident_verification_cache import GH_VERSION
from test_slack_incidents import IDENTITY, signed_record

PREFIX = "repos/" + safety.REPOSITORY
NOW = datetime.now(timezone.utc).replace(microsecond=0)
START = NOW - timedelta(hours=24)


@pytest.fixture(autouse=True)
def receipt_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))


def stamp(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class OperationalAPI:
    def __init__(self):
        self.repository = {"id": 17, "full_name": safety.REPOSITORY}
        self.definitions = {
            name: {"id": identifier, "path": ".github/workflows/" + name}
            for name, identifier in (("unattended-workflow-alert.yml", 12), ("operational-safety-check.yml", 13),
                                     ("catalog-web-deploy.yml", 14), ("bucket-hygiene-audit.yml", 15),
                                     ("deployment-verification.yml", 16))
        }
        self.head = deployments.subprocess.check_output(["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1], text=True).strip()
        self.current = self.source(300, "operational-safety-check.yml", status="in_progress", conclusion=None)
        self.emitter = emitter_run(identifier=11, attempt=1, path=incidents.WORKFLOW, event="workflow_run")
        self.runs = {300: self.current, 11: self.emitter}
        self.jobs, self.statuses = {}, {}
        self.sources, self.records, self.production_records, self.calls, self.posts = [], [], [], [], []
        self.fail_on = None

    def source(self, identifier, filename="bucket-hygiene-audit.yml", *, status="completed", conclusion="failure", attempt=1):
        return {"id": identifier, "run_attempt": attempt, "workflow_id": self.definitions[filename]["id"],
                "name": WORKFLOW_NAMES[filename][0], "path": ".github/workflows/" + filename,
                "head_sha": self.head, "event": "schedule", "head_branch": "main", "status": status,
                "conclusion": conclusion, "repository": copy.deepcopy(self.repository),
                "head_repository": copy.deepcopy(self.repository), "created_at": stamp(NOW - timedelta(hours=1)),
                "updated_at": stamp(NOW - timedelta(minutes=5))}

    def add_source(self, source):
        self.sources.append(source)
        self.runs[source["id"]] = source
        self.jobs[source["id"]] = [{"id": source["id"] + 1000, "name": "audit", "status": "completed",
                                   "conclusion": source["conclusion"], "completed_at": source["updated_at"]}]

    def install_state(self, state):
        record = json.loads(json.dumps(signed_record()))
        record["payload"]["state"] = state
        self.records = [record]
        self.statuses[record["id"]] = [{"id": 12, "state": "success", "description": "applied", "environment_url": "",
                                       "log_url": emissions.invocation(safety.REPOSITORY, 11, 1)}]

    def _check(self, method, path):
        self.calls.append((method, path))
        if self.fail_on and self.fail_on in path:
            raise RuntimeError("live API unavailable: " + self.fail_on)

    def get(self, path):
        self._check("get", path)
        if path == PREFIX:
            value = self.repository
        elif "/actions/workflows/" in path:
            key = path.rsplit("/", 1)[-1]
            value = self.definitions[key] if key in self.definitions else next(v for v in self.definitions.values() if str(v["id"]) == key)
        elif "/actions/runs/" in path:
            value = self.runs[int(path.split("/actions/runs/", 1)[1].split("/", 1)[0])]
        else:
            raise AssertionError("unexpected GET " + path)
        return copy.deepcopy(value)

    def pages(self, path, field=None):
        self._check("pages", path)
        if "/actions/runs?" in path:
            value = self.sources
        elif "/jobs?" in path:
            value = self.jobs[int(path.split("/actions/runs/", 1)[1].split("/", 1)[0])]
        elif "/deployments/" in path and "/statuses?" in path:
            value = self.statuses[int(path.split("/deployments/", 1)[1].split("/", 1)[0])]
        elif "/deployments?" in path:
            value = self.records if "environment=slack-incidents" in path else self.production_records
        else:
            raise AssertionError("unexpected enumeration " + path)
        return copy.deepcopy(value)

    def post(self, path, value):
        self.posts.append((path, value))
        pytest.fail("dry reconciliation attempted a remote write")


def acked_job_state(api):
    source = api.source(101, "operational-safety-check.yml")
    job = {"id": 1101, "name": safety.JOB, "conclusion": "failure", "completed_at": stamp(NOW - timedelta(minutes=10))}
    state = incidents.fold(None, incidents.from_job(source, job, incidents.job_targets()), IDENTITY)
    state["incident"]["posts"]["parent"] = {"state": "delivered", "ts": "1791270000.000001"}
    state["incident"]["permalink"] = "https://example.slack.com/archives/" + IDENTITY["channel"] + "/p1791270000000001"
    state["incident"]["parent_view_sha256"] = incidents.parent_view_sha256(state)
    return state


def signed_transport(api):
    record = api.records[0]
    receipt = emissions.receipt(safety.REPOSITORY, record, api.statuses[record["id"]][0])
    return mock.patch.object(emissions, "verified_output",
        return_value=json.dumps(verified(receipt, emitter=api.emitter, signer=incidents.WORKFLOW)))


def dry(api, tmp_path, sources=()):
    return safety.dry_reconciliation(api, api.current, IDENTITY, list(sources), START, NOW, tmp_path, None)


def test_inventory_retains_missed_final_events_and_source_attempt_changes():
    api = OperationalAPI()
    missed = api.source(101)
    api.add_source(missed)
    api.add_source(api.source(102, "operational-safety-check.yml", conclusion="success"))
    api.add_source(api.current)
    inventory, all_sources, start, end = safety.source_inventory(api, api.current, stamp(START), now=NOW)
    assert [source["id"] for source in inventory] == [101]
    assert [source["id"] for source in all_sources] == [101, 102]
    assert (start, end) == (START, NOW)
    missed["run_attempt"] += 1
    changed, _, _, _ = safety.source_inventory(api, api.current, stamp(START), now=NOW)
    assert changed != inventory and changed[0]["run_attempt"] == 2
    assert "branch=main" in api.calls[-1][1]


def test_inventory_is_activation_limited_and_retains_unfinished_or_failed_safety_runs():
    api = OperationalAPI()
    api.add_source(api.source(101, "operational-safety-check.yml"))
    api.add_source(api.source(102, "operational-safety-check.yml", status="in_progress", conclusion=None))
    activated = NOW - timedelta(hours=2)
    inventory, _, start, _ = safety.source_inventory(api, api.current, stamp(activated), now=NOW)
    assert [row["id"] for row in inventory] == [101, 102] and start == activated
    query = parse_qs(api.calls[-1][1].split("?", 1)[1])
    assert query["created"] == [stamp(activated) + ".." + stamp(NOW)]


def test_empty_registry_is_idle_but_missed_failure_requests_normal_worker_without_post(tmp_path):
    api = OperationalAPI()
    assert dry(api, tmp_path) is True
    missed = api.source(101)
    api.add_source(missed)
    assert dry(api, tmp_path, [missed]) is False
    assert api.posts == []


@pytest.mark.parametrize("pending", ["deferred", "permalink", "unknown-post"])
def test_retained_delivery_or_deferred_work_needs_reconciliation_without_post(tmp_path, pending):
    api = OperationalAPI()
    state = acked_job_state(api)
    if pending == "deferred":
        state["deferred"] = copy.deepcopy(state["incident"]["failures"])
    elif pending == "permalink":
        state["incident"]["permalink"] = ""
    else:
        state["incident"]["posts"]["parent"] = {"state": "claimed", "ts": None}
        state["incident"]["permalink"] = ""
        state["incident"]["parent_view_sha256"] = None
    api.install_state(state)
    with signed_transport(api):
        assert dry(api, tmp_path) is False
    assert api.posts == []


def test_successful_safety_source_is_used_when_its_original_incident_needs_recovery(tmp_path):
    api = OperationalAPI()
    recovered = api.source(102, "operational-safety-check.yml", conclusion="success")
    api.add_source(recovered)
    api.jobs[102][0]["name"] = safety.JOB
    assert dry(api, tmp_path, [recovered]) is True
    api.install_state(acked_job_state(api))
    with signed_transport(api):
        assert dry(api, tmp_path, [recovered]) is False
    assert api.posts == []


@pytest.mark.parametrize("path", ["deployments?", "/actions/runs/101", "/jobs?"])
def test_failed_live_registry_source_or_jobs_read_is_never_reported_as_idle(tmp_path, path):
    api = OperationalAPI()
    source = api.source(101)
    api.add_source(source)
    api.fail_on = path
    with pytest.raises(RuntimeError, match="live API unavailable"):
        dry(api, tmp_path, [source])
    assert api.posts == []


class CatalogClient:
    def __init__(self):
        self.objects = {indexes.PREFIX + "example.json": "1", catalog.REMOTE_CSV_OBJECT: "2", catalog.REMOTE_WEB_CATALOG_OBJECT: "3"}
        self.fail = None

    def list_blobs(self, _bucket, *, prefix):
        if self.fail == "list":
            raise RuntimeError("GCS listing unavailable")
        return [SimpleNamespace(name=name, generation=value, size=100) for name, value in self.objects.items() if name.startswith(prefix)]

    def bucket(self, _name):
        return self

    def blob(self, name):
        def reload():
            if self.fail == name:
                raise RuntimeError("GCS object unavailable")
        return SimpleNamespace(generation=self.objects[name], reload=reload)


@pytest.fixture
def probe_inputs(tmp_path, monkeypatch):
    api, client = OperationalAPI(), CatalogClient()
    monkeypatch.setattr(safety.subprocess, "check_output", lambda *_args, **_kwargs: GH_VERSION)
    monkeypatch.setattr(VerificationCache, "restore", lambda *_args, **_kwargs: None)
    collect, checks = mock.Mock(), mock.Mock(return_value=[SimpleNamespace(ok=True)])
    monkeypatch.setattr(indexes, "collect", collect)
    monkeypatch.setattr(catalog, "run_checks", checks)
    monkeypatch.setattr(snapshots.Snapshot, "restore", mock.Mock(return_value=None))
    return api, client, tmp_path, collect, checks


def test_unchanged_authenticated_snapshot_skips_full_catalog_and_dry_work(probe_inputs, monkeypatch):
    api, client, directory, collect, checks = probe_inputs
    snapshot = mock.Mock()
    snapshot.matches.return_value = True
    snapshot.data = {"requests": ["one", "two"]}
    monkeypatch.setattr(snapshots.Snapshot, "restore", mock.Mock(return_value=snapshot))
    monkeypatch.setattr(safety, "dry_reconciliation", mock.Mock(side_effect=AssertionError("unexpected expensive dry run")))
    assert safety.probe(api, api.current, IDENTITY, stamp(START), client, directory) == {
        "catalog": False, "reconcile": False, "snapshot": True}
    snapshot.matches.assert_called_once_with(api)
    snapshot.save.assert_called_once_with(directory, api.current)
    collect.assert_not_called()
    checks.assert_not_called()
    assert any("environment=production-wdpa-monthly" in path for _method, path in api.calls)


def test_catalog_check_is_not_skipped_for_an_older_pending_wdpa_execution(probe_inputs):
    api, client, directory, collect, checks = probe_inputs
    api.production_records = [deployment_fixtures.ReplayTests.record("a" * 40, 99), deployment_fixtures.ReplayTests.record("b" * 40, 100)]
    api.statuses.update({99: [{"state": "in_progress", "description": "verification_pending"}],
                         100: [{"state": "success", "description": "verified"}]})
    checks.return_value = [SimpleNamespace(ok=False)]
    result = safety.probe(api, api.current, IDENTITY, stamp(START), client, directory)
    assert result == {"catalog": True, "reconcile": True, "snapshot": False}
    collect.assert_called_once()
    checks.assert_called_once()
    assert api.posts == []
    for identifier in (99, 100):
        assert any(f"/deployments/{identifier}/statuses?" in path for _method, path in api.calls)


@pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled"])
def test_completion_waits_for_final_safety_outcome_before_replay(dispatch_context, conclusion):
    api, environment, event = dispatch_context
    filename = "deployment-verification.yml"
    api.current = api.source(300, filename, status="in_progress", conclusion=None)
    api.current["event"] = "workflow_dispatch"
    api.runs[300] = api.current
    environment["GITHUB_WORKFLOW_REF"] = safety.REPOSITORY + "/.github/workflows/" + filename + "@refs/heads/main"
    sleeps = []
    def finish(seconds):
        sleeps.append(seconds)
        api.runs[200].update(status="completed", conclusion=conclusion)
    safety.wait_for_safety(api, event, environment, clock=lambda: 0, sleep=finish)
    assert sleeps == [2]
    assert sum(path.endswith("/actions/runs/200") for _method, path in api.calls) == 2
    assert api.posts == []


def test_unfinished_handoff_fails_at_bounded_deadline_instead_of_allowing_incomplete_replay(dispatch_context):
    api, environment, event = dispatch_context
    filename = "deployment-verification.yml"
    api.current = api.source(300, filename, status="in_progress", conclusion=None)
    api.current["event"] = "workflow_dispatch"
    api.runs[300] = api.current
    environment["GITHUB_WORKFLOW_REF"] = safety.REPOSITORY + "/.github/workflows/" + filename + "@refs/heads/main"
    clock = iter([0, 121])
    with pytest.raises(RuntimeError, match="safety handoff did not finish"):
        safety.wait_for_safety(api, event, environment, clock=lambda: next(clock), sleep=mock.Mock())


@pytest.mark.parametrize("change", ["branch", "path", "repository", "latest-attempt", "workflow", "head-format", "missing-attempt", "read-failure"])
def test_completion_handoff_requires_current_native_parent_provenance(dispatch_context, change):
    api, environment, event = dispatch_context
    filename = "deployment-verification.yml"
    api.current = api.source(300, filename, status="in_progress", conclusion=None)
    api.current["event"] = "workflow_dispatch"
    api.runs[300] = api.current
    environment["GITHUB_WORKFLOW_REF"] = safety.REPOSITORY + "/.github/workflows/" + filename + "@refs/heads/main"
    producer = api.runs[200]
    if change == "branch":
        producer["head_branch"] = "feature"
    elif change == "path":
        producer["path"] = ".github/workflows/ci.yml"
    elif change == "repository":
        producer["head_repository"]["id"] += 1
    elif change == "latest-attempt":
        producer["run_attempt"] = 2
    elif change == "workflow":
        producer["workflow_id"] += 1
    elif change == "head-format":
        producer["head_sha"] = "bad"
    elif change == "missing-attempt":
        event["inputs"].pop("safety_run_attempt")
    else:
        api.fail_on = "/actions/runs/200"
    sleeps = mock.Mock()
    with pytest.raises(RuntimeError):
        safety.wait_for_safety(api, event, environment, clock=lambda: 0, sleep=sleeps)
    sleeps.assert_not_called()
    assert api.posts == []


@pytest.mark.parametrize("missed", [False, True])
def test_legacy_mode_keeps_completion_polling_without_requiring_bot_identity(tmp_path, monkeypatch, missed):
    api, client = OperationalAPI(), CatalogClient()
    if missed:
        add_legacy_failure(api)
    environment = {"GITHUB_REPOSITORY": safety.REPOSITORY, "GITHUB_REF": "refs/heads/main",
                   "GITHUB_WORKFLOW_REF": safety.REPOSITORY + "/" + safety.WORKFLOW + "@refs/heads/main",
                   "GITHUB_RUN_ID": "300", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_OUTPUT": str(tmp_path / "outputs"),
                   "SHARED_DATASETS_SLACK_INCIDENTS_ENABLED": "false"}
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(incidents, "GitHub", lambda: api)
    monkeypatch.setattr(catalog, "get_client", lambda: client)
    monkeypatch.setattr(safety, "catalog_changed", mock.Mock(return_value=False))
    monkeypatch.setattr(incidents, "Slack", mock.Mock(side_effect=AssertionError("legacy mode must not require bot")))
    monkeypatch.setattr(safety, "probe", mock.Mock(side_effect=AssertionError("legacy has no reusable bot registry")))
    monkeypatch.setattr(safety.sys, "argv", [safety.__file__, "--work-dir", str(tmp_path)])
    safety.main()
    lines = (tmp_path / "outputs").read_text().splitlines()
    assert lines[:3] == ["catalog=false", "reconcile=true", "snapshot=false"]
    assert json.loads(lines[3].removeprefix("missed_handoffs=")) == ([{"id": 101, "run_attempt": 1}] if missed else [])


def test_catalog_change_during_comparison_cannot_receive_an_idle_snapshot(probe_inputs):
    api, client, directory, _collect, checks = probe_inputs
    def update(*_args, **_kwargs):
        client.objects[catalog.REMOTE_WEB_CATALOG_OBJECT] = "4"
        return [SimpleNamespace(ok=True)]
    checks.side_effect = update
    result = safety.probe(api, api.current, IDENTITY, stamp(START), client, directory)
    assert result["catalog"] is True and result["snapshot"] is False


@pytest.mark.parametrize("failure", ["sources", "catalog-list", "catalog-object"])
def test_probe_read_failure_never_becomes_unchanged(probe_inputs, failure):
    api, client, directory, _collect, _checks = probe_inputs
    if failure == "sources":
        api.fail_on = "/actions/runs?"
    else:
        client.fail = "list" if failure == "catalog-list" else catalog.REMOTE_WEB_CATALOG_OBJECT
    with pytest.raises(RuntimeError, match="unavailable"):
        safety.probe(api, api.current, IDENTITY, stamp(START), client, directory)
    assert api.posts == []


@pytest.fixture
def dispatch_context():
    api = OperationalAPI()
    api.current = api.source(300, "catalog-web-deploy.yml", status="in_progress", conclusion=None)
    api.current["event"] = "workflow_dispatch"
    producer = api.source(200, "operational-safety-check.yml", status="in_progress", conclusion=None)
    api.runs.update({300: api.current, 200: producer})
    api.jobs[200] = [{"id": 1200, "run_id": 200, "run_attempt": 1, "head_sha": api.head, "name": safety.JOB,
                     "steps": [{"name": "Check operational work", "status": "completed", "conclusion": "success"}]}]
    environment = {"GITHUB_REPOSITORY": safety.REPOSITORY, "GITHUB_REF": "refs/heads/main",
                   "GITHUB_WORKFLOW_REF": safety.REPOSITORY + "/.github/workflows/catalog-web-deploy.yml@refs/heads/main",
                   "GITHUB_RUN_ID": "300", "GITHUB_RUN_ATTEMPT": "1"}
    return api, environment, {"inputs": {"safety_run_id": "200", "safety_run_attempt": "1"}}


def test_catalog_admission_accepts_successful_inflight_probe_only_as_refresh_hint(dispatch_context):
    api, environment, event = dispatch_context
    safety.validate_catalog_dispatch(api, event, environment)
    assert api.posts == []


@pytest.mark.parametrize("change", ["environment", "producer-head", "producer-path", "producer-repository", "producer-attempt",
                                   "job-attempt", "job-head", "probe-skipped", "missing-id"])
def test_catalog_admission_rejects_forged_context_or_producer(dispatch_context, change):
    api, environment, event = dispatch_context
    producer, job = api.runs[200], api.jobs[200][0]
    if change == "environment":
        environment["GITHUB_WORKFLOW_REF"] = safety.REPOSITORY + "/.github/workflows/catalog-web-deploy.yml@refs/heads/feature"
    elif change == "producer-head":
        producer["head_sha"] = "b" * 40
    elif change == "producer-path":
        producer["path"] = ".github/workflows/ci.yml"
    elif change == "producer-repository":
        producer["repository"]["full_name"] = "Other/repository"
    elif change == "producer-attempt":
        producer["run_attempt"] = 2
    elif change == "job-attempt":
        job["run_attempt"] = 2
    elif change == "job-head":
        job["head_sha"] = "b" * 40
    elif change == "probe-skipped":
        job["steps"][0]["conclusion"] = "skipped"
    else:
        event["inputs"]["safety_run_id"] = "../200"
    with pytest.raises(RuntimeError, match="OPERATIONAL_SAFETY"):
        safety.validate_catalog_dispatch(api, event, environment)
    assert api.posts == []


def add_legacy_failure(api, identifier=101):
    source = api.source(identifier, "operational-safety-check.yml")
    api.add_source(source)
    return source


def add_legacy_observer(api, identifier=201, *, parent=101, status="completed", conclusion="success"):
    observer = api.source(identifier, "deployment-verification.yml", status=status, conclusion=conclusion)
    observer.update(event="workflow_dispatch", display_title=f"Operational follow-up: safety {parent}/1")
    api.add_source(observer)
    return observer


def test_legacy_discovery_returns_every_failed_attempt_without_an_admitted_handoff():
    api = OperationalAPI()
    for identifier in (101, 102, 103):
        add_legacy_failure(api, identifier)
    api.add_source(api.source(104, "operational-safety-check.yml", conclusion="success"))
    api.add_source(api.source(105, "operational-safety-check.yml", status="in_progress", conclusion=None))
    add_legacy_observer(api, parent=102)
    assert safety.unadmitted_safety_failures(api, api.current, now=NOW) == [
        {"id": 101, "run_attempt": 1}, {"id": 103, "run_attempt": 1}]
    enumerations = [path for method, path in api.calls if method == "pages"]
    assert len(enumerations) == 1
    assert parse_qs(enumerations[0].split("?", 1)[1])["created"] == [stamp(START) + ".." + stamp(NOW)]
    assert api.posts == []


@pytest.mark.parametrize("status,conclusion", [("queued", None), ("in_progress", None), ("completed", "failure"),
                                             ("completed", "cancelled"), ("completed", "success")])
def test_admitted_handoff_is_never_retried_for_an_unknown_webhook_outcome(status, conclusion):
    api = OperationalAPI()
    add_legacy_failure(api)
    add_legacy_observer(api, status=status, conclusion=conclusion)
    assert safety.unadmitted_safety_failures(api, api.current, now=NOW) == []
    assert not any("/jobs?" in path for _method, path in api.calls)
    assert api.posts == []


@pytest.mark.parametrize("change", ["wrong-parent", "wrong-attempt", "wrong-workflow", "wrong-event", "manual-title"])
def test_unrelated_observer_never_suppresses_a_missing_safety_handoff(change):
    api = OperationalAPI()
    add_legacy_failure(api)
    observer = add_legacy_observer(api)
    if change == "wrong-parent":
        observer["display_title"] = "Operational follow-up: safety 999/1"
    elif change == "wrong-attempt":
        observer["display_title"] = "Operational follow-up: safety 101/2"
    elif change == "wrong-workflow":
        observer["path"] = ".github/workflows/bucket-hygiene-audit.yml"
    elif change == "wrong-event":
        observer["event"] = "push"
    else:
        observer["display_title"] = "Deployment completion check"
    assert safety.unadmitted_safety_failures(api, api.current, now=NOW) == [{"id": 101, "run_attempt": 1}]


@pytest.mark.parametrize("kind", ["source", "observer"])
@pytest.mark.parametrize("change", ["branch", "repository", "workflow", "path", "event", "attempt", "head", "title"])
def test_legacy_handoff_discovery_checks_current_native_attempt_provenance(kind, change):
    api = OperationalAPI()
    source = add_legacy_failure(api)
    observer = add_legacy_observer(api)
    identifier = source["id"] if kind == "source" else observer["id"]
    fresh = copy.deepcopy(api.runs[identifier])
    api.runs[identifier] = fresh  # Keep the earlier census separate from the fresh GET.
    if change == "branch":
        fresh["head_branch"] = "feature"
    elif change == "repository":
        fresh["head_repository"]["id"] += 1
    elif change == "workflow":
        fresh["workflow_id"] += 1
    elif change == "path":
        fresh["path"] = ".github/workflows/ci.yml"
    elif change == "event":
        fresh["event"] = "push"
    elif change == "attempt":
        fresh["run_attempt"] += 1
    elif change == "head":
        fresh["head_sha"] = "invalid"
    elif kind == "observer":
        fresh["display_title"] = "Operational follow-up: safety 999/1"
    else:
        fresh.update(status="completed", conclusion="success")
    with pytest.raises(RuntimeError, match="OPERATIONAL_SAFETY"):
        safety.unadmitted_safety_failures(api, api.current, now=NOW)
    assert api.posts == []


@pytest.mark.parametrize("path", ["/actions/runs?", "/actions/runs/101", "/actions/runs/201"])
def test_failed_legacy_census_or_native_admission_read_remains_fatal(path):
    api = OperationalAPI()
    add_legacy_failure(api)
    add_legacy_observer(api)
    api.fail_on = path
    with pytest.raises(RuntimeError, match="live API unavailable"):
        safety.unadmitted_safety_failures(api, api.current, now=NOW)
    assert api.posts == []

