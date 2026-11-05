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
from scripts import operational_safety_check as safety
from scripts import operational_safety_check as snapshots
from scripts import slack_incidents as incidents, slack_notify
from scripts.incident_verification_cache import VerificationCache
from scripts.workflow_identity import WORKFLOW_NAMES
from test_deployment_emission import run as emitter_run, verified
from test_incident_verification_cache import GH_VERSION
from test_operational_snapshot import capture
from test_slack_incidents import IDENTITY, signed_record

PREFIX = "repos/" + safety.REPOSITORY
NOW = datetime.now(timezone.utc).replace(microsecond=0)
START = NOW - timedelta(hours=24)


@pytest.fixture(autouse=True)
def receipt_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_DATASETS_SLACK_INCIDENTS_ENABLED", "true")
    monkeypatch.setenv("SHARED_DATASETS_SLACK_BOT_TOKEN", "test-bot-token")
    monkeypatch.setenv("SHARED_DATASETS_SLACK_INCIDENTS_SINCE", stamp(START))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(slack_notify, "notify", mock.Mock())
    monkeypatch.setattr(safety, "audit_freshness", mock.Mock(return_value=[]))
    monkeypatch.setattr(safety.subprocess, "check_call", mock.Mock())
    monkeypatch.setattr(safety, "heartbeat_series", mock.Mock(return_value=[]))


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
        self.comparison = "diverged"

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
        record["created_at"] = stamp(NOW - timedelta(hours=1))
        self.records = [record]
        self.statuses[record["id"]] = [{"id": 12, "state": "success", "description": "applied", "environment_url": "",
                                       "log_url": emissions.invocation(safety.REPOSITORY, 11, 1)}]

    def _check(self, method, path):
        self.calls.append((method, path))
        if self.fail_on and self.fail_on in path:
            raise RuntimeError("live API unavailable: " + self.fail_on)

    def get(self, path):
        self._check("get", path)
        if "/compare/" in path:
            value = {"status": self.comparison}
        elif path == PREFIX:
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
        if "/actions/runs?" in path or "/runs?branch=main" in path:
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
    return safety.dry_reconciliation(api, api.current, IDENTITY, list(sources), START, NOW, tmp_path, None)[0]


def test_inventory_retains_missed_final_events_and_source_attempt_changes():
    api = OperationalAPI()
    missed = api.source(101)
    api.add_source(missed)
    api.add_source(api.source(102, "operational-safety-check.yml", conclusion="success"))
    api.add_source(api.current)
    inventory, start, end = safety.source_inventory(api, api.current, stamp(START), now=NOW)
    assert [source["id"] for source in inventory] == [101, 102]
    assert (start, end) == (START, NOW)
    missed["run_attempt"] += 1
    changed, _, _ = safety.source_inventory(api, api.current, stamp(START), now=NOW)
    assert changed != inventory and changed[0]["run_attempt"] == 2
    assert "branch=main" in api.calls[-1][1]


def test_inventory_is_activation_limited_and_retains_unfinished_or_failed_safety_runs():
    api = OperationalAPI()
    api.add_source(api.source(101, "operational-safety-check.yml"))
    api.add_source(api.source(102, "operational-safety-check.yml", status="in_progress", conclusion=None))
    activated = NOW - timedelta(hours=2)
    inventory, start, _ = safety.source_inventory(api, api.current, stamp(activated), now=NOW)
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


@pytest.mark.parametrize("pending", ["deferred", "permalink"])
def test_retained_delivery_or_deferred_work_needs_reconciliation_without_post(tmp_path, pending):
    api = OperationalAPI()
    state = acked_job_state(api)
    if pending == "deferred":
        state["deferred"] = copy.deepcopy(state["incident"]["failures"])
    elif pending == "permalink":
        state["incident"]["permalink"] = ""
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


def test_resolved_incident_watermark_progress_does_not_request_another_worker(tmp_path):
    api = OperationalAPI()
    state = incidents.fold(None, incidents.from_workflow(api.source(101)), IDENTITY)
    state["incident"].update(posts={"parent": {"state": "delivered", "ts": "1791270000.000001"}},
        permalink="https://example.slack.com/archives/" + IDENTITY["channel"] + "/p1791270000000001")
    recovery = {**state["incident"]["failures"][0], "conclusion": "success", "run_id": 102,
                "time": stamp(NOW - timedelta(minutes=4))}
    state = incidents.fold(state, recovery, IDENTITY, ancestor=lambda *_: True)
    state["incident"]["parent_view_sha256"] = incidents.parent_view_sha256(state)
    api.install_state(state)
    healthy = api.source(103, conclusion="success")
    healthy["updated_at"] = stamp(NOW - timedelta(minutes=1))
    api.add_source(healthy)
    with signed_transport(api):
        assert dry(api, tmp_path, [healthy]) is True
    assert api.posts == []


def test_unchanged_uncertain_send_waits_for_owner_without_hourly_worker(tmp_path):
    api = OperationalAPI()
    state = acked_job_state(api)
    state["incident"].update(posts={"parent": {"state": "claimed", "ts": None}},
                             permalink="", parent_view_sha256=None)
    api.install_state(state)
    with signed_transport(api):
        assert dry(api, tmp_path) is True
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
    check_output = safety.subprocess.check_output
    monkeypatch.setattr(safety.subprocess, "check_output", lambda command, *args, **kwargs:
                        GH_VERSION if command == ["gh", "--version"] else check_output(command, *args, **kwargs))
    monkeypatch.setattr(VerificationCache, "restore", lambda _api, run, identity, **_kwargs: VerificationCache(run, identity, gh_version=GH_VERSION))
    collect, checks = mock.Mock(), mock.Mock(return_value=[SimpleNamespace(ok=True)])
    monkeypatch.setattr(indexes, "collect", collect)
    monkeypatch.setattr(catalog, "run_checks", checks)
    monkeypatch.setattr(snapshots.Snapshot, "restore", mock.Mock(return_value=None))
    return api, client, tmp_path, collect, checks


def test_unchanged_catalog_snapshot_still_traverses_live_incidents_and_reuses_verified_receipts(probe_inputs, monkeypatch):
    api, client, directory, collect, checks = probe_inputs
    inventory = safety.catalog_inventory(client)
    cache = VerificationCache(api.current, IDENTITY, gh_version=GH_VERSION)
    snapshot = capture(api.current, {"head_sha": api.head, "gh_version": GH_VERSION, "since": stamp(START)},
                                       IDENTITY, inventory, cache)
    monkeypatch.setattr(safety.Snapshot, "restore", lambda *_: snapshot)
    assert safety.probe(api, api.current, IDENTITY, stamp(START), client, directory) == {
        "catalog": False, "reconcile": False, "verify": False, "snapshot": True}
    collect.assert_not_called()
    checks.assert_not_called()
    assert any("environment=slack-incidents" in path for _method, path in api.calls)
    missed = api.source(101)
    api.add_source(missed)
    assert safety.probe(api, api.current, IDENTITY, stamp(START), client, directory)['reconcile'] is True
    api.fail_on = '/actions/runs/101'
    with pytest.raises(RuntimeError, match='live API unavailable'):
        safety.probe(api, api.current, IDENTITY, stamp(START), client, directory)
    assert api.posts == []


def test_catalog_check_is_not_skipped_for_an_older_pending_wdpa_execution(probe_inputs):
    api, client, directory, collect, checks = probe_inputs
    api.production_records = [deployment_fixtures.ReplayTests.record("a" * 40, 99), deployment_fixtures.ReplayTests.record("b" * 40, 100)]
    api.statuses.update({99: [{"state": "in_progress", "description": "verification_pending"}],
                         100: [{"state": "success", "description": "verified"}]})
    checks.return_value = [SimpleNamespace(ok=False)]
    result = safety.probe(api, api.current, IDENTITY, stamp(START), client, directory)
    assert result == {"catalog": True, "reconcile": False, "verify": True, "snapshot": False}
    collect.assert_called_once()
    checks.assert_called_once()
    assert api.posts == []
    for identifier in (99, 100):
        assert any(f"/deployments/{identifier}/statuses?" in path for _method, path in api.calls)


@pytest.mark.parametrize("enabled", ["true", "false"])
def test_probe_main_requires_bot_identity_only_when_incidents_configured(tmp_path, monkeypatch, enabled):
    api, client = OperationalAPI(), CatalogClient()
    environment = {"GITHUB_REPOSITORY": safety.REPOSITORY, "GITHUB_REF": "refs/heads/main",
                   "GITHUB_WORKFLOW_REF": safety.REPOSITORY + "/" + safety.WORKFLOW + "@refs/heads/main",
                   "GITHUB_RUN_ID": "300", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_OUTPUT": str(tmp_path / "outputs"),
                   "SHARED_DATASETS_SLACK_INCIDENTS_ENABLED": enabled, "SHARED_DATASETS_SLACK_INCIDENTS_SINCE": stamp(START)}
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(incidents, "GitHub", lambda: api)
    monkeypatch.setattr(safety, "GitHub", lambda: api)
    monkeypatch.setattr(catalog, "get_client", lambda: client)
    bot = mock.Mock()
    bot.identity.return_value = IDENTITY
    monkeypatch.setattr(incidents, "Slack", lambda *_: bot)
    result = {"catalog": False, "reconcile": True, "verify": True, "snapshot": False}
    probe = mock.Mock(return_value=result)
    monkeypatch.setattr(safety, "probe", probe)
    monkeypatch.setattr(safety.sys, "argv", [safety.__file__, "--work-dir", str(tmp_path)])
    safety.main()
    if enabled == "true":
        bot.identity.assert_called_once_with()
    else:
        bot.identity.assert_not_called()
    probe.assert_called_once_with(api, api.current, IDENTITY if enabled == "true" else None, stamp(START), client, tmp_path)
    assert json.loads((tmp_path / "result.json").read_text()) == result
    assert (tmp_path / "outputs").read_text().splitlines() == ["catalog=false", "reconcile=true", "verify=true", "snapshot=false"]


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


@pytest.fixture(params=["catalog-web-deploy.yml", "unattended-workflow-alert.yml"])
def dispatch_context(request):
    api = OperationalAPI()
    api.current = api.source(300, request.param, status="in_progress", conclusion=None)
    api.current["event"] = "workflow_dispatch"
    producer = api.source(200, "operational-safety-check.yml", status="in_progress", conclusion=None)
    api.runs.update({300: api.current, 200: producer})
    api.jobs[200] = [{"id": 1200, "run_id": 200, "run_attempt": 1, "head_sha": api.head, "name": safety.JOB,
                     "steps": [{"name": "Check operational work", "status": "completed", "conclusion": "success"}]}]
    environment = {"GITHUB_REPOSITORY": safety.REPOSITORY, "GITHUB_REF": "refs/heads/main",
                   "GITHUB_WORKFLOW_REF": safety.REPOSITORY + "/.github/workflows/" + request.param + "@refs/heads/main",
                   "GITHUB_RUN_ID": "300", "GITHUB_RUN_ATTEMPT": "1"}
    return api, environment, {"inputs": {"safety_run_id": "200", "safety_run_attempt": "1"}}


def test_catalog_admission_accepts_successful_inflight_probe_only_as_refresh_hint(dispatch_context):
    api, environment, event = dispatch_context
    safety.validate_safety_dispatch(api, event, environment, workflow=api.current["path"])
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
    with pytest.raises((RuntimeError, ValueError), match="OPERATIONAL_SAFETY|comparison commit unavailable|trusted main source|unknown unattended"):
        safety.validate_safety_dispatch(api, event, environment, workflow=api.current["path"])
    assert api.posts == []


@pytest.mark.parametrize("defect", [None, "wrong-operation", "acknowledgement", "wrong-incident", "unfinished-probe"])
def test_batch_cli_authenticates_exact_inflight_probe_without_parent_wait(tmp_path, monkeypatch, defect):
    api = OperationalAPI()
    api.current = api.source(300, "unattended-workflow-alert.yml", status="in_progress", conclusion=None)
    api.current["event"] = "workflow_dispatch"
    api.runs[300] = api.current
    api.runs[200] = api.source(200, "operational-safety-check.yml", status="in_progress", conclusion=None)
    api.jobs[200] = [{"run_id": 200, "run_attempt": 1, "head_sha": api.head, "name": safety.JOB,
                     "steps": [{"name": "Check operational work", "status": "completed", "conclusion": "success"}]}]
    inputs = {"delivery_result": "batch", "incident_id": "operational-batch", "operation": "safety:200/1"}
    if defect == "wrong-operation":
        inputs["operation"] = "safety:200/0"
    elif defect == "acknowledgement":
        inputs["message_ts"] = "1791270000.000001"
    elif defect == "wrong-incident":
        inputs["incident_id"] = "SD-123456789ABC"
    elif defect == "unfinished-probe":
        api.jobs[200][0]["steps"][0]["status"] = "in_progress"
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"inputs": inputs}))
    environment = {"GITHUB_REPOSITORY": safety.REPOSITORY, "GITHUB_REF": "refs/heads/main",
                   "GITHUB_WORKFLOW_REF": safety.REPOSITORY + "/" + incidents.WORKFLOW + "@refs/heads/main",
                   "GITHUB_RUN_ID": "300", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_OUTPUT": str(tmp_path / "outputs"),
                   "GITHUB_EVENT_PATH": str(event_path), "SHARED_DATASETS_SLACK_INCIDENTS_SINCE": stamp(START)}
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(incidents, "GitHub", lambda: api)
    monkeypatch.setattr(safety, "GitHub", lambda: api)
    slack = mock.Mock()
    slack.identity.return_value = IDENTITY
    monkeypatch.setattr(incidents, "Slack", lambda *_: slack)
    monkeypatch.setattr(VerificationCache, "restore", lambda *_: VerificationCache(api.current, IDENTITY, gh_version=GH_VERSION))
    monkeypatch.setattr(safety.sys, "argv", ["slack_incidents.py", "prepare", "--work-dir", str(tmp_path)])
    if defect:
        with pytest.raises((RuntimeError, incidents.IncidentError)):
            incidents.main()
    else:
        incidents.main()
        assert json.loads((tmp_path / "prepared.json").read_bytes()) == []
        assert json.loads((tmp_path / "cache.json").read_bytes())["producer"]["id"] == 300
        assert "verification_cache=true" in (tmp_path / "outputs").read_text()
    assert api.posts == []
    slack.post.assert_not_called()
    slack.update.assert_not_called()


@pytest.mark.parametrize('filename', ['unattended-workflow-alert.yml', 'deployment-verification.yml', 'catalog-web-deploy.yml', 'catalog-viewer-deploy.yml'])
def test_token_dispatched_failure_is_replayed_without_native_event(tmp_path, filename):
    api = OperationalAPI()
    api.definitions.setdefault(filename, {'id': 99, 'path': '.github/workflows/' + filename})
    source = api.source(101, filename)
    source['event'] = 'workflow_dispatch'
    api.add_source(source)
    assert dry(api, tmp_path, [source]) is False
    assert api.posts == []


@pytest.mark.parametrize('age,conclusion,expected', [(1,'success',False), (7,'success',True), (1,'failure',True), (7,None,False)])
def test_pending_checks_back_off_unchanged_success_but_not_failure(age, conclusion, expected):
    api = OperationalAPI()
    source = api.source(101, 'deployment-verification.yml', status='completed' if conclusion else 'in_progress', conclusion=conclusion)
    source['created_at'] = stamp(NOW-timedelta(hours=age))
    assert safety.verification_due(api, [source], [], now=NOW) is expected
    assert safety.verification_due(api, [], [], now=NOW)


@pytest.mark.parametrize('count', [0, 1, 2])
@pytest.mark.parametrize('previous', ['success', 'failure', 'skipped'])
def test_reconciliation_escalates_on_third_consecutive_probe_only(count, previous):
    api = OperationalAPI()
    for identifier in range(100, 100+count):
        run = api.source(identifier, 'operational-safety-check.yml')
        api.add_source(run)
        api.jobs[identifier][0].update(name=safety.JOB, steps=[{'name':'Request operational reconciliation', 'status':'completed', 'conclusion':previous}])
    assert safety.reconciliation_stalled(api, api.current) is (count == 2 and previous != 'skipped')


@pytest.mark.parametrize('age', [1, 25])
def test_uncertain_claim_never_receives_idle_snapshot_and_health_cli_bounds_its_age(probe_inputs, monkeypatch, age):
    api, client, directory, _, _ = probe_inputs
    state = acked_job_state(api)
    state['incident'].update(posts={'parent':{'state':'claimed','ts':None}}, permalink='', parent_view_sha256=None)
    api.install_state(state)
    api.records[0]['created_at'] = stamp(NOW - timedelta(hours=age))
    with signed_transport(api):
        result = safety.probe(api, api.current, IDENTITY, stamp(START), client, directory)
    assert result == {'catalog':False, 'reconcile':False, 'verify':False, 'snapshot':False}
    assert json.loads((directory/'claims.json').read_bytes()) == [{'incident':state['incident']['id'], 'since':api.records[0]['created_at']}]
    (directory/'result.json').write_text(json.dumps(result))
    for k,v in {'GITHUB_REPOSITORY':safety.REPOSITORY, 'GITHUB_REF':'refs/heads/main',
                'GITHUB_WORKFLOW_REF':safety.REPOSITORY+'/'+safety.WORKFLOW+'@refs/heads/main',
                'GITHUB_RUN_ID':'300','GITHUB_RUN_ATTEMPT':'1'}.items():
        monkeypatch.setenv(k,v)
    monkeypatch.setattr(safety,'GitHub',lambda:api)
    monkeypatch.setattr(catalog,'get_client',lambda:client)
    monkeypatch.setattr(safety,'freshness',lambda *_a,**_k:[])
    monkeypatch.setattr(safety.sys,'argv',['safety','--health','--work-dir',str(directory)])
    safety.main()
    problems = json.loads((directory/'health/cache.json').read_bytes())['problems']
    assert any('unconfirmed Slack claim >=24 h' in item for item in problems) is (age > 24)
    assert api.posts == []


@pytest.mark.parametrize('cadence,days', [('daily',2),('monthly',45)])
@pytest.mark.parametrize('status,reason', [('success',''), ('skipped','source fingerprint unchanged'), ('skipped','source unavailable')])
def test_freshness_separates_unchanged_source_from_missing_data(cadence, days, status, reason):
    payload = {'latest_release':{'date':stamp(NOW-timedelta(days=365))[:10]},
               'latest_run':{'status':status,'reason':reason,'date':stamp(NOW-timedelta(days=days-1))[:10]}}
    assert safety.fresh_activity(payload, cadence, now=NOW) is (status=='success' or reason=='source fingerprint unchanged')
    payload['latest_run']['date'] = stamp(NOW-timedelta(days=days))[:10]
    assert not safety.fresh_activity(payload, cadence, now=NOW)
    payload['updated_at'] = stamp(NOW)  # Schema/metadata repair cannot reset freshness.
    assert not safety.fresh_activity(payload, cadence, now=NOW)


def test_freshness_reads_exact_generations_and_bounds_unapproved_pause():
    calls=[]
    class Client:
        def bucket(self, name):
            assert name == catalog.DEFAULT_BUCKET
            return self
        def blob(self, name):
            slug=name.removeprefix(indexes.PREFIX).removesuffix('.json')
            def download(**kwargs):
                assert kwargs == {'if_generation_match':7}
                calls.append(slug)
                return json.dumps({'schema_version':1,'asset_slug':slug,'latest_release':{'date':stamp(NOW)[:10]}, 'releases':[]}).encode()
            return SimpleNamespace(reload=lambda:None, size=100, generation='7', download_as_bytes=download)
    def scheduler(job):
        return {'name':f'projects/shared-datasets-1/locations/us-central1/jobs/{job}', 'state':'PAUSED',
                'userUpdateTime':stamp(NOW-timedelta(hours=72))}
    problems=safety.freshness(Client(), now=NOW, scheduler=scheduler, pauses={})
    assert len(problems)==5 and all('>=72 h' in item for item in problems)
    assert len(calls)==5
    pauses={job:{'owner':'jonaraphael','reason':'reviewed maintenance','expires_at':stamp(NOW+timedelta(hours=1))} for job in safety.CRON_ASSETS}
    assert safety.freshness(Client(), now=NOW, scheduler=scheduler, pauses=pauses)==[]
    pauses['dataset-usage']['expires_at']=stamp(NOW-timedelta(seconds=1))
    assert len(safety.freshness(Client(), now=NOW, scheduler=scheduler, pauses=pauses))==1
    with pytest.raises(RuntimeError, match='unavailable'):
        safety.freshness(Client(), now=NOW, scheduler=lambda _: (_ for _ in ()).throw(RuntimeError('unavailable')))


def test_pending_backoff_never_delays_new_execution_or_new_failure(monkeypatch):
    api=OperationalAPI()
    monkeypatch.setattr(safety.subprocess, 'check_output', lambda *_a, **_k: '{"status":{}}')
    source=api.source(101,'deployment-verification.yml',conclusion='success')
    source['created_at']=stamp(NOW-timedelta(hours=1))
    api.statuses[99]=[{'created_at':stamp(NOW-timedelta(hours=2)), 'environment_url': 'https://console.cloud.google.com/run/jobs/executions/details/us-central1/wdpa-monthly-abcde?project=shared-datasets-1'}]
    assert not safety.verification_due(api,[source],[99],now=NOW)
    api.statuses[99][0]['created_at']=stamp(NOW-timedelta(minutes=30))
    assert safety.verification_due(api,[source],[99],now=NOW)
    failed={**source,'id':102,'conclusion':'failure'}
    assert safety.verification_due(api,[source,failed],[99],now=NOW)


@pytest.mark.parametrize('filename,cpu_peak,memory_peak', [('eamlis_monthly',2.60,0.96),('mpatlas_monthly',2.32,3.36),('sea_ice_daily',0.92,0.16)])
def test_resources_retain_cpu_headroom_and_twice_observed_memory_peak(filename,cpu_peak,memory_peak):
    import hcl2
    from hcl2.utils import SerializationOptions
    config=hcl2.loads((incidents.ROOT/'terraform/envs/prod'/f'{filename}.tf').read_text(), serialization_options=SerializationOptions(strip_string_quotes=True))
    job=next(module[filename+'_job'] for module in config['module'] if filename+'_job' in module)
    memory=job['memory']
    gib=float(memory[:-2])/(1024 if memory.endswith('Mi') else 1)
    assert int(job['cpu']) >= 1.5*cpu_peak and gib >= 2*memory_peak
    assert gib >= {1:0.5,2:0.5,4:2,6:4,8:4}[int(job['cpu'])]


def test_later_main_revision_accepts_only_ancestral_successful_probe_hint(dispatch_context):
    api, environment, event=dispatch_context
    previous=deployments.subprocess.check_output(['git','rev-parse','7bd972e'],cwd=incidents.ROOT,text=True).strip()
    api.comparison='ahead'
    api.runs[200]['head_sha']=previous
    api.jobs[200][0]['head_sha']=previous
    safety.validate_safety_dispatch(api,event,environment,workflow=api.current['path'])


def test_daily_absence_uses_checkin_timestamp_at_exact_two_cadences():
    checked=NOW-timedelta(hours=47)
    payload={'updated_at':stamp(checked),'latest_run':{'date':stamp(checked)[:10],'status':'success'}}
    assert safety.fresh_activity(payload,'daily',now=NOW)
    assert not safety.fresh_activity(payload,'daily',now=NOW+timedelta(hours=1))



def test_health_report_posts_changes_and_daily_reminders_without_changing_problem_identity(tmp_path, monkeypatch):
    previous = {'problems': [], 'warnings': [], 'notified_at': None}
    monkeypatch.setattr(safety, 'previous_health', lambda *_: previous)
    for hour, problems, sends in [(0, ['paused'], 1), (3, ['paused'], 1), (24, ['paused'], 2),
                                  (25, ['expired', 'paused'], 3), (26, [], 3), (27, ['paused'], 4)]:
        safety.report_health(None, {'id': 300}, tmp_path, problems, [], NOW + timedelta(hours=hour))
        previous = json.loads((tmp_path / 'health/cache.json').read_text())
        assert previous['problems'] == sorted(problems)
        assert slack_notify.notify.call_count == sends
    assert 'paused' in slack_notify.notify.call_args.kwargs['body']
    assert previous['scope'] == slack_notify.notify.call_args.kwargs['fields']['Incident scope'] == safety.HEALTH_SCOPE


@pytest.mark.parametrize('failure', [RuntimeError('scheduled asset inventory differs from catalog'), safety.subprocess.CalledProcessError(1, ['gcloud', 'scheduler', 'jobs', 'describe', 'wdpa-monthly'])])
def test_health_cli_captures_read_errors_and_expired_policy_without_red_runs(tmp_path, monkeypatch, failure):
    api = OperationalAPI()
    for key, value in {'GITHUB_REPOSITORY': safety.REPOSITORY, 'GITHUB_REF': 'refs/heads/main',
                       'GITHUB_WORKFLOW_REF': safety.REPOSITORY + '/' + safety.WORKFLOW + '@refs/heads/main',
                       'GITHUB_RUN_ID': '300', 'GITHUB_RUN_ATTEMPT': '1'}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(safety, 'GitHub', lambda: api)
    monkeypatch.setattr(catalog, 'get_client', lambda: None)
    monkeypatch.setattr(safety, 'policy_warnings', lambda _: ([], ['hold expired']))
    monkeypatch.setattr(safety, 'freshness', mock.Mock(side_effect=failure))
    monkeypatch.setattr(safety.sys, 'argv', ['safety', '--health', '--work-dir', str(tmp_path)])
    (tmp_path / 'result.json').write_text('{"reconcile":false}')
    (tmp_path / 'claims.json').write_text('[]')
    safety.main()
    report = json.loads((tmp_path/'health/cache.json').read_text())
    assert report['scope'] == safety.HEALTH_SCOPE
    assert report['problems'] == sorted(['hold expired', f'Health check incomplete (freshness): {type(failure).__name__}: {failure}'])
    monkeypatch.setattr(safety, 'previous_health', lambda *_: report)
    safety.main()
    assert slack_notify.notify.call_count == 1


@pytest.mark.parametrize('terminal', [{}, {'completionTime': stamp(NOW)}, {'failedCount': 1}, {'cancelledCount': 1}])
def test_pending_completion_is_probed_before_backoff_and_retry_is_capped(monkeypatch, terminal):
    api = OperationalAPI()
    source = api.source(101, 'deployment-verification.yml', conclusion='success')
    source['created_at'] = stamp(NOW - timedelta(hours=1))
    api.statuses[99] = [{'created_at': stamp(NOW - timedelta(hours=2)), 'environment_url': 'https://console.cloud.google.com/run/jobs/executions/details/us-central1/wdpa-monthly-abcde?project=shared-datasets-1'}]
    probe = mock.Mock(return_value=json.dumps({'status': terminal}))
    monkeypatch.setattr(safety.subprocess, 'check_output', probe)
    assert safety.verification_due(api, [source], [99], now=NOW) is bool(terminal)
    assert safety.verification_due(api, [source], [99], now=NOW + timedelta(hours=2))
    assert probe.call_count == 2 and 'wdpa-monthly-abcde' in probe.call_args.args[0]


def test_health_only_failures_never_create_reconciliation_work_but_infrastructure_failures_do(tmp_path):
    api = OperationalAPI()
    source = api.source(101, 'operational-safety-check.yml')
    api.add_source(source)
    api.jobs[101][0]['steps'] = [{'name': 'Check freshness and stalled operations', 'conclusion': 'failure'}, {'name': 'Retain safety health report', 'conclusion': 'success'}]
    assert dry(api, tmp_path, [source]) is True
    api.jobs[101][0]['steps'].pop()
    assert dry(api, tmp_path, [source]) is False
    api.jobs[101][0]['steps'] = [{'name': 'actions/checkout@v4', 'conclusion': 'failure'}]
    assert dry(api, tmp_path, [source]) is False


def test_expiry_warns_at_72_hours_and_alerts_afterwards_without_releasing_holds():
    holds = json.loads((safety.ROOT / safety.INGESTION_ROLLOUT_HOLDS).read_text())
    expiry = datetime.fromisoformat(next(iter(holds.values()))['expires_at'].replace('Z', '+00:00'))
    assert len(safety.policy_warnings(expiry - timedelta(hours=75))[0]) == len(holds)
    warnings, problems = safety.policy_warnings(expiry)
    assert len(problems) == len(holds) and not warnings
    assert set(safety.validate_suspensions(holds, now=expiry + timedelta(days=99))) == set(holds)


def test_reviewed_usage_pause_is_bounded_and_matches_pending_activation():
    pauses = json.loads((safety.ROOT / '.github/scheduler-pauses.json').read_text())
    activation = json.loads((safety.ROOT / 'catalog/dataset-usage-activation.json').read_text())
    assert activation['sources']['gcs_usage']['verified_at'] is None
    assert pauses['dataset-usage']['owner'] == 'jonaraphael'
    expiry = datetime.fromisoformat(pauses['dataset-usage']['expires_at'].replace('Z', '+00:00'))
    assert any('dataset-usage' in item for item in safety.policy_warnings(expiry - timedelta(hours=72))[0])


def test_independent_heartbeat_has_seven_hour_absence_and_step_only_webhooks():
    import hcl2
    from workflow_helpers import load_workflow
    workflow = load_workflow(safety.ROOT/'.github/workflows/operational-safety-check.yml')
    job = workflow['jobs']['safety']
    assert 'SHARED_DATASETS_SLACK_WEBHOOK_URL' not in job['env']
    assert {step.get('name') for step in job['steps'] if 'SHARED_DATASETS_SLACK_WEBHOOK_URL' in step.get('env', {})} == {'Check freshness and stalled operations', 'Report safety failure directly'}
    config = hcl2.loads((safety.ROOT/'terraform/envs/prod/monitoring.tf').read_text(), serialization_options=hcl2.utils.SerializationOptions(strip_string_quotes=True))
    resources = {name: values for group in config['resource'] for entries in group.values() for name, values in entries.items()}
    absence = resources['operational_safety_absent']['conditions'][0]['condition_absent'][0]
    assert absence['duration'] == '25200s' and 'operational_safety_heartbeat' in absence['filter']
    assert 'operational-safety-check.yml' in resources['operational_safety_heartbeat']['filter']
    assert 'operational-safety-heartbeat' in Path(safety.__file__).read_text()


@pytest.mark.parametrize('defect', [None, 'digest', 'attempt', 'upload'])
def test_health_history_authenticates_exact_attempt_and_actual_archive_bytes(defect):
    import hashlib
    import io
    import zipfile
    from scripts.incident_verification_cache import canonical, CacheError
    api = OperationalAPI()
    producer = api.source(101, 'operational-safety-check.yml')
    api.add_source(producer)
    job = api.jobs[101][0]
    job.update(name=safety.JOB, started_at=producer['created_at'], completed_at=producer['updated_at'],
               steps=[{'name': 'Retain safety health report', 'status': 'completed', 'conclusion': 'failure' if defect == 'upload' else 'success'}])
    report = {'problems': ['paused'], 'warnings': [], 'notified_at': NOW.isoformat()}
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as stream:
        stream.writestr('cache.json', canonical(report))
    raw = archive.getvalue()
    artifact = {'id': 9, 'name': 'safety-health-101-1', 'expired': False, 'size_in_bytes': len(raw),
                'created_at': producer['updated_at'], 'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(),
                'workflow_run': {'id': 101, 'head_sha': api.head, 'head_branch': 'main'}}
    if defect == 'digest':
        artifact['digest'] = 'sha256:' + '0' * 64
    if defect == 'attempt':
        api.runs[101] = {**producer, 'run_attempt': 2}
    pages = api.pages
    api.pages = lambda path, field=None: [artifact] if '/artifacts?' in path else pages(path, field=field)
    api.archive = lambda *_: raw
    if defect:
        with pytest.raises((RuntimeError, CacheError)):
            safety.previous_health(api, api.current)
    else:
        assert safety.previous_health(api, api.current) == report


def test_failed_probe_health_uses_change_plus_daily_deduplication_without_result_files(tmp_path, monkeypatch):
    api = OperationalAPI()
    monkeypatch.setattr(safety, 'GitHub', lambda: api)
    monkeypatch.setattr(safety, 'context', lambda *_: api.current)
    monkeypatch.setattr(safety, 'policy_warnings', lambda _: ([], []))
    monkeypatch.setattr(catalog, 'get_client', lambda: None)
    monkeypatch.setattr(safety, 'freshness', lambda *_a, **_k: [])
    monkeypatch.setenv('SAFETY_PROBE_OUTCOME', 'failure')
    monkeypatch.setattr(safety.sys, 'argv', ['safety', '--health', '--work-dir', str(tmp_path)])
    previous = {'problems': [], 'warnings': [], 'notified_at': None}
    for hour, sends in ((0, 1), (3, 1), (24, 2)):
        class Clock(datetime):
            @classmethod
            def now(cls, _zone):
                return NOW + timedelta(hours=hour)
        monkeypatch.setattr(safety, 'datetime', Clock)
        monkeypatch.setattr(safety, 'previous_health', lambda *_: previous)
        safety.main()
        previous = json.loads((tmp_path / 'health/cache.json').read_text())
        assert previous['problems'] == ['Operational discovery failure; inspect Check operational work']
        assert slack_notify.notify.call_count == sends
    safety.subprocess.check_call.assert_not_called()  # No false success heartbeat.


def test_settled_probe_reuses_signature_verification_after_real_snapshot_serialization(probe_inputs, monkeypatch):
    api, client, directory, collect, checks = probe_inputs
    api.install_state(acked_job_state(api))
    with signed_transport(api) as crypto:
        assert safety.probe(api, api.current, IDENTITY, stamp(START), client, directory)['snapshot']
        assert crypto.call_count == 1
    retained = safety.Snapshot(json.loads((directory / 'cache.json').read_bytes()))
    assert len(retained.cache.entries) == 1
    monkeypatch.setattr(safety.Snapshot, 'restore', lambda *_: retained)
    with mock.patch.object(emissions, 'verified_output', side_effect=AssertionError('unchanged receipt reverified')):
        result = safety.probe(api, api.current, IDENTITY, stamp(START), client, directory)
    assert result == {'catalog': False, 'reconcile': False, 'verify': False, 'snapshot': True}
    assert retained.cache.hits == 1 and retained.cache.misses == 0
    assert collect.call_count == checks.call_count == 1
    assert api.posts == []
