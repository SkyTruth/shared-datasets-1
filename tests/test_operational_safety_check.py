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
        "catalog": False, "reconcile": False, "verify": False, "snapshot": True}
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
    assert result == {"catalog": True, "reconcile": False, "verify": True, "snapshot": False}
    collect.assert_called_once()
    checks.assert_called_once()
    assert api.posts == []
    for identifier in (99, 100):
        assert any(f"/deployments/{identifier}/statuses?" in path for _method, path in api.calls)


@pytest.mark.parametrize("pending", [False, True])
def test_legacy_mode_is_idle_without_pending_execution_or_bot_identity(tmp_path, monkeypatch, pending):
    api, client = OperationalAPI(), CatalogClient()
    monkeypatch.setattr(deployments, "pending", lambda *_: [99] if pending else [])
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
    assert lines == ["catalog=false", "reconcile=false", "verify=" + str(pending).lower(), "snapshot=false"]


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
    with pytest.raises(RuntimeError, match="OPERATIONAL_SAFETY|comparison commit unavailable"):
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
    monkeypatch.setattr(incidents,'GitHub',lambda:api)
    monkeypatch.setattr(catalog,'get_client',lambda:client)
    monkeypatch.setattr(safety,'freshness',lambda *_a,**_k:[])
    monkeypatch.setattr(safety.sys,'argv',['safety','--health','--work-dir',str(directory)])
    if age > 24:
        with pytest.raises(RuntimeError, match='unconfirmed Slack claim >=24 h'):
            safety.main()
    else:
        safety.main()
    assert api.posts == []


@pytest.mark.parametrize('cadence,days', [('daily',2),('monthly',62)])
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
    problems=safety.freshness(Client(), now=NOW, scheduler=scheduler)
    assert len(problems)==5 and all('>=72 h' in item for item in problems)
    assert len(calls)==5
    pauses={job:{'owner':'jonaraphael','reason':'reviewed maintenance','until':stamp(NOW+timedelta(hours=1))} for job in safety.CRON_ASSETS}
    assert safety.freshness(Client(), now=NOW, scheduler=scheduler, pauses=pauses)==[]
    pauses['dataset-usage']['until']=stamp(NOW-timedelta(seconds=1))
    assert len(safety.freshness(Client(), now=NOW, scheduler=scheduler, pauses=pauses))==1
    with pytest.raises(RuntimeError, match='unavailable'):
        safety.freshness(Client(), now=NOW, scheduler=lambda _: (_ for _ in ()).throw(RuntimeError('unavailable')))


def test_pending_backoff_never_delays_new_execution_or_new_failure():
    api=OperationalAPI()
    source=api.source(101,'deployment-verification.yml',conclusion='success')
    source['created_at']=stamp(NOW-timedelta(hours=1))
    api.statuses[99]=[{'created_at':stamp(NOW-timedelta(hours=2))}]
    assert not safety.verification_due(api,[source],[99],now=NOW)
    api.statuses[99][0]['created_at']=stamp(NOW-timedelta(minutes=30))
    assert safety.verification_due(api,[source],[99],now=NOW)
    failed={**source,'id':102,'conclusion':'failure'}
    assert safety.verification_due(api,[source,failed],[99],now=NOW)


@pytest.mark.parametrize('filename,cpu_peak,memory_peak', [('eamlis_monthly',2.60,0.96),('mpatlas_monthly',2.32,3.36),('sea_ice_daily',0.92,0.16)])
def test_resources_retain_twice_observed_monitoring_peak(filename,cpu_peak,memory_peak):
    import hcl2
    from hcl2.utils import SerializationOptions
    config=hcl2.loads((incidents.ROOT/'terraform/envs/prod'/f'{filename}.tf').read_text(), serialization_options=SerializationOptions(strip_string_quotes=True))
    job=next(module[filename+'_job'] for module in config['module'] if filename+'_job' in module)
    memory=job['memory']
    gib=float(memory[:-2])/(1024 if memory.endswith('Mi') else 1)
    assert int(job['cpu']) >= 2*cpu_peak and gib >= 2*memory_peak
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


def test_legacy_health_surfaces_a_token_dispatched_failure(tmp_path, monkeypatch):
    api=OperationalAPI()
    source=api.source(101,'unattended-workflow-alert.yml')
    source['event']='workflow_dispatch'
    api.add_source(source)
    (tmp_path/'result.json').write_text(json.dumps({'reconcile':False}))
    (tmp_path/'claims.json').write_text('[]')
    for key,value in {'GITHUB_REPOSITORY':safety.REPOSITORY, 'GITHUB_REF':'refs/heads/main',
                      'GITHUB_WORKFLOW_REF':safety.REPOSITORY+'/'+safety.WORKFLOW+'@refs/heads/main',
                      'GITHUB_RUN_ID':'300', 'GITHUB_RUN_ATTEMPT':'1', 'SHARED_DATASETS_SLACK_INCIDENTS_ENABLED':'false'}.items():
        monkeypatch.setenv(key,value)
    monkeypatch.setattr(incidents,'GitHub',lambda:api)
    monkeypatch.setattr(catalog,'get_client',lambda:None)
    monkeypatch.setattr(safety,'freshness',lambda *_a,**_k:[])
    monkeypatch.setattr(safety.sys,'argv',['safety','--health','--work-dir',str(tmp_path)])
    with pytest.raises(RuntimeError,match='Failed dispatched run: https://github.com/.*/101'):
        safety.main()
    assert api.posts == []
