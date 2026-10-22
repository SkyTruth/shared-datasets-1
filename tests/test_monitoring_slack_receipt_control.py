"""Offline exact CLI, deployment, execution and persisted receipt boundaries."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml
from google.api_core import exceptions as api_errors

from scripts import deployment_revision as deployments
from scripts import monitoring_slack_receipt_control as control
from scripts import repo_guardrails
from services.monitoring_slack import model, receipts
from services.monitoring_slack.store import Snapshot

SHA = "a" * 40
IMAGE = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/monitoring-slack-relay@sha256:" + "b" * 64
NOW = "2026-10-22T13:10:00Z"
PREPARED = "2026-10-22T13:04:00Z"
RUN_URL = "https://github.com/SkyTruth/shared-datasets-1/actions/runs/777"
OLD = control.JOB + "/executions/monitoring-slack-relay-abc12"
NEW = control.JOB + "/executions/monitoring-slack-relay-def34"


def config():
    registry = control.deploy.registry()
    return model.Config.parse({
        "schema": model.CONFIG_SCHEMA, "project": control.deploy.PROJECT,
        "policies": [{"name": name, "kind": "health" if kind == "health" else "security"}
                     for kind, name in registry["policies"].items()],
        "health_metric": "custom.googleapis.com/shared_datasets/operational_reconciliation_heartbeat_v2",
        "health_policy_created_at": "2026-10-22T11:00:00Z", "state_bucket": control.deploy.BUCKET,
        "slack_secret_version": control.deploy.SECRET + "/versions/1",
        **{key: registry[key] for key in ("slack_channel", "slack_team", "slack_user")},
        "activated_at": "2026-10-22T12:00:00Z", "relay_job": control.JOB, "relay_image": IMAGE, "mode": "live"})


def execution(name=OLD, *, args=None, active=False):
    cfg = config()
    current = name == NEW
    value = {
        "name": name, "job": "monitoring-slack-relay", "uid": ("22222222-2222-4222-8222-222222222222" if current else "11111111-1111-4111-8111-111111111111"),
        "createTime": "2026-10-22T13:05:00Z" if current else "2026-10-22T13:00:00Z",
        "startTime": "2026-10-22T13:05:01Z" if current else "2026-10-22T13:01:00Z",
        "taskCount": 1, "parallelism": 1, "generation": "1", "observedGeneration": "1", "runningCount": int(active),
        "conditions": [{"type": "Completed", "state": "CONDITION_PENDING" if active else "CONDITION_SUCCEEDED"}],
        "template": {"serviceAccount": control.deploy.RUNTIME, "maxRetries": 0, "containers": [{
            "image": IMAGE, "command": ["python", "-m", "services.monitoring_slack.run"], "args": args or [],
            "env": [{"name": "MONITORING_SLACK_CONFIG", "value": model.canonical(cfg.value).decode()}]}]}}
    if not active:
        value.update(completionTime="2026-10-22T13:06:00Z" if current else "2026-10-22T13:02:00Z", succeededCount=1)
    if current:
        value["creator"] = control.deploy.DEPLOYER.removeprefix("serviceAccount:")
        value["template"]["containers"][0]["env"].append({"name": "SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL", "value": RUN_URL})
    return value


def job():
    raw = execution()
    return {"name": control.JOB, "template": {"taskCount": 1, "parallelism": 1, "template": raw["template"]}}


def environment():
    return {"GITHUB_REPOSITORY": control.REPOSITORY, "GITHUB_REF": "refs/heads/main",
            "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_WORKFLOW_REF": f"{control.REPOSITORY}/{control.WORKFLOW}@refs/heads/main",
            "GITHUB_SHA": SHA, "GITHUB_WORKFLOW_SHA": SHA, "BOOTSTRAP_WORKFLOW_SHA": SHA,
            "GITHUB_RUN_ID": "777", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_ACTOR": "operator"}


class GitHub:
    def __init__(self):
        self.repo = {"id": 1, "full_name": control.REPOSITORY}
        self.actor = {"login": "operator", "id": 9, "type": "User"}
        self.records = [{"id": 100, "sha": SHA, "ref": SHA, "environment": "production-monitoring-slack-relay",
                         "creator": {"login": "github-actions[bot]", "type": "Bot"},
                         "payload": {"schema": deployments.SCHEMA, "target": "monitoring-slack-relay", "artifact": IMAGE,
                                     "ci_run_id": 202, "ci_run_attempt": 1, "execution_run_id": 303, "execution_run_attempt": 1}}]
        self.statuses = [{"id": 11, "state": "success", "description": "verified"},
                         {"id": 10, "state": "in_progress", "description": "started"}]
        self.calls = []
        self.overrides = {}

    def get(self, path):
        self.calls.append(path)
        if path in self.overrides:
            return deepcopy(self.overrides[path])
        if path == f"repos/{control.REPOSITORY}":
            return deepcopy(self.repo)
        if "/actions/workflows/" in path:
            name = path.rsplit("/", 1)[1]
            return {"id": {"ci.yml": 2, "monitoring-slack-relay-deploy.yml": 3, "monitoring-slack-relay-receipt.yml": 4}[name],
                    "path": ".github/workflows/" + name}
        run_id = int(path.split("/runs/")[1].split("/")[0])
        name = {202: "ci.yml", 303: "monitoring-slack-relay-deploy.yml", 777: "monitoring-slack-relay-receipt.yml"}[run_id]
        return {"id": run_id, "run_attempt": 1, "workflow_id": {202: 2, 303: 3, 777: 4}[run_id],
                "path": ".github/workflows/" + name, "event": "push" if run_id == 202 else "workflow_dispatch",
                "head_sha": SHA, "head_branch": "main", "status": "completed" if run_id == 202 else "in_progress",
                "repository": self.repo, "head_repository": self.repo, "actor": self.actor, "triggering_actor": self.actor}

    def pages(self, path, field=None):
        self.calls.append(path)
        if "/jobs?" in path:
            assert field == "jobs"
            return [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]
        if "/statuses?" in path:
            return deepcopy(self.statuses)
        assert path.endswith("deployments?environment=production-monitoring-slack-relay&per_page=100")
        return deepcopy(self.records)


class MemoryStore:
    def __init__(self, value):
        self.body, self.generation = model.canonical(value), 1

    def load(self):
        return Snapshot(model.parse_json(self.body), self.generation)

    def save(self, value, generation):
        assert generation == self.generation
        self.body, self.generation = model.canonical(value), self.generation + 1


class Reader:
    def __init__(self):
        self.job = job()
        self.executions = {OLD: execution()}
        self.pages = None
        self.calls = []

    def read(self, path, *, api):
        assert api == "run" and path == control.JOB
        self.calls.append(path)
        return deepcopy(self.job)

    def _read(self, path, *, api):
        assert api == "run"
        self.calls.append(path)
        if self.pages is not None:
            value = self.pages.pop(0)
            if isinstance(value, Exception):
                raise value
            return deepcopy(value)
        return {"executions": list(deepcopy(self.executions).values())}

    def execution(self, name):
        self.calls.append(name)
        return deepcopy(self.executions[name])


@pytest.fixture
def boundary(monkeypatch):
    api, reader, cfg = GitHub(), Reader(), config()
    # Only external cryptographic verification is replaced; real deployment,
    # source-CI, actor, status and serialization checks run for every phase.
    signatures = []
    monkeypatch.setattr(deployments, "verify_receipt", lambda *values: signatures.append(values[-1]["id"]))
    monkeypatch.setattr(control.subprocess, "check_output", lambda *args, **kwargs: SHA + "\n")
    monkeypatch.setattr(deployments, "git_ancestor", lambda old, new: old == SHA and new == "origin/main")
    identity = control.admit(api, SHA, 202, 1, environment=environment())
    raw_alert = {"name": "projects/shared-datasets-1/alerts/incident1", "state": "OPEN", "openTime": "2026-10-22T12:30:00Z",
                 "policy": {"name": control.deploy.registry()["policies"]["health"]},
                 "resource": {"type": "global", "labels": {"project_id": "shared-datasets-1"}}, "metric": {"type": cfg["health_metric"]}}
    alert = model.normalize(raw_alert, cfg, NOW)
    state = model.merge(model.new_state(cfg), {alert["name"]: alert}, {}, cfg, PREPARED, now=NOW)
    payload = model.render(alert, None)
    state["episodes"][alert["name"]]["operation"] = {
        "id": "c" * 32, "kind": "post", "phase": "claimed", "sha256": model.digest(payload), "payload": payload,
        "started_at": "2026-10-22T13:01:30Z", "writer": receipts.execution_proof(reader.executions[OLD], cfg, OLD, NOW, args=[], terminal=True)}
    store = MemoryStore(state)
    wanted = {"schema": "monitoring-slack-receipt-request-v1", "request_id": "33333333-3333-4333-8333-333333333333",
              "config_binding": cfg.binding, "alert_name": alert["name"], "operation_id": "c" * 32,
              "operation_sha256": model.digest(payload), "expected_generation": 1, "outcome": "confirm_not_delivered", "message_ts": None}
    return api, reader, store, identity, wanted, signatures


def prepared(boundary):
    api, reader, store, identity, wanted, _ = boundary
    value = model.canonical(wanted).decode()
    return value, control.prepare(api, reader, store, identity, value, clock=lambda: PREPARED)


def complete(boundary, value):
    _, reader, store, _, _, _ = boundary
    reader.executions[NEW] = execution(NEW, args=["--reconcile-receipt-json", value], active=True)
    env = {"CLOUD_RUN_JOB": "monitoring-slack-relay", "CLOUD_RUN_EXECUTION": NEW.rsplit("/", 1)[1],
           "CLOUD_RUN_TASK_INDEX": "0", "CLOUD_RUN_TASK_ATTEMPT": "0", "SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL": RUN_URL}
    class ReadOnlyBot:
        def identity(self):
            return config().identity
        def message(self, ts):
            wanted = model.parse_json(value.encode())
            operation = store.load().value["episodes"][wanted["alert_name"]]["operation"]
            return {**operation["payload"], "ts": ts, "type": "message", "user": config()["slack_user"],
                    "team": config()["slack_team"], "bot_id": "B12345678"}
    reader.token = lambda: "xoxb-synthetic"
    result = receipts.reconcile(reader, store, config(), value, environment=env, clock=lambda: "2026-10-22T13:05:30Z",
                                slack_factory=lambda *args: ReadOnlyBot())
    reader.executions[NEW] = execution(NEW, args=["--reconcile-receipt-json", value])
    return result


@pytest.mark.parametrize("outcome", ["confirm_not_delivered", "confirm_message"])
def test_full_persisted_receipt_only_boundary_rechecks_source_and_acceptance(boundary, outcome, capsys):
    api, reader, store, identity, _, signatures = boundary
    if outcome == "confirm_message":
        boundary[4].update(outcome=outcome, message_ts="1792673065.918709")
        state = store.load().value
        state["episodes"][boundary[4]["alert_name"]]["operation"]["phase"] = "sending"
        store.body = model.canonical(state)
    value, proof = prepared(boundary)
    result = complete(boundary, value)
    # Cold serialized proof and state are the actual post-execution boundary.
    verified = control.verify(api, reader, store, identity, value, model.parse_json(model.canonical(proof)), clock=lambda: NOW)
    assert verified["verified"] and verified["public_operations"] == 0
    assert verified["receipt"] == result["receipt"] and verified["state_generation"] == 2
    assert signatures == [10, 11, 10, 11]
    assert sum("environment=production-monitoring-slack-relay" in path for path in api.calls) == 2
    episode = store.load().value["episodes"][boundary[4]["alert_name"]]
    assert episode["operation"] is None and episode["alert"]["state"] == "OPEN" and episode["recovery"] is None
    assert '"kind":"job_outcome"' not in capsys.readouterr().err


def test_confirmed_uuid_never_reexecutes_an_already_accepted_receipt(boundary):
    value, _ = prepared(boundary)
    complete(boundary, value)
    before = boundary[2].body, boundary[2].generation
    with pytest.raises(model.Invalid, match="already confirmed"):
        control.prepare(*boundary[:3], boundary[3], value, clock=lambda: NOW)
    assert (boundary[2].body, boundary[2].generation) == before


@pytest.mark.parametrize("field,value", [("GITHUB_REF", "refs/pull/1/merge"), ("GITHUB_EVENT_NAME", "push"),
                                        ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_ACTOR", "forged"),
                                        ("GITHUB_WORKFLOW_SHA", "b" * 40), ("GITHUB_REPOSITORY", "other/repo")])
def test_admission_rejects_wrong_identity_and_reruns_before_any_execution(boundary, field, value):
    api = boundary[0]
    with pytest.raises(model.Invalid):
        control.admit(api, SHA, 202, 1, environment={**environment(), field: value})


def test_ci_failed_gate_is_not_replaced_by_image_receipt(boundary):
    api = boundary[0]
    original = api.pages
    api.pages = lambda path, field=None: [{"name": "ci-ready", "status": "completed", "conclusion": "failure"}] if field == "jobs" else original(path, field)
    with pytest.raises(deployments.DeploymentError, match="unsuccessful"):
        control.admit(api, SHA, 202, 1, environment=environment())


@pytest.mark.parametrize("change", ["newer_partial", "source", "signature", "image", "generation", "active_writer", "sending", "uid"])
def test_prepare_refuses_unproven_source_or_claim_without_mutation(boundary, monkeypatch, change):
    api, reader, store, identity, wanted, _ = boundary
    if change == "newer_partial":
        api.records.append({**deepcopy(api.records[0]), "id": 101})
        api.statuses = [{"id": 20, "description": "started", "state": "in_progress"}]
    elif change == "source":
        identity["executor_sha"] = "d" * 40
    elif change == "signature":
        monkeypatch.setattr(deployments, "verify_receipt", lambda *args: (_ for _ in ()).throw(deployments.DeploymentError("invalid signature")))
    elif change == "image":
        reader.job["template"]["template"]["containers"][0]["image"] = IMAGE[:-1] + "c"
    elif change == "generation":
        store.generation = 2
    elif change == "active_writer":
        reader.executions[OLD] = execution(active=True)
    elif change == "sending":
        state = store.load().value
        state["episodes"][wanted["alert_name"]]["operation"]["phase"] = "sending"
        store.body = model.canonical(state)
    elif change == "uid":
        reader.executions[OLD]["uid"] = "44444444-4444-4444-8444-444444444444"
    before = store.body, store.generation
    with pytest.raises((model.Invalid, deployments.DeploymentError)):
        control.prepare(api, reader, store, identity, model.canonical(wanted).decode(), clock=lambda: PREPARED)
    assert (store.body, store.generation) == before


@pytest.mark.parametrize("change", ["no_receipt", "failed", "wrong_args", "wrong_marker", "wrong_image", "wrong_creator", "duplicate", "wrong_receipt_run", "replaced_deployment"])
def test_verification_requires_terminal_exact_controller_and_durable_same_request(boundary, change):
    api, reader, store, identity, wanted, _ = boundary
    value, proof = prepared(boundary)
    complete(boundary, value)
    if change in {"no_receipt", "wrong_receipt_run"}:
        state = store.load().value
        if change == "no_receipt":
            state["receipts"] = {}
        else:
            state["receipts"][wanted["request_id"]]["github_run_url"] = RUN_URL + "1"
        store.body = model.canonical(state)
    elif change == "failed":
        reader.executions[NEW]["conditions"][0]["state"] = "CONDITION_FAILED"
        reader.executions[NEW].update(succeededCount=0, failedCount=1)
    elif change == "wrong_args":
        reader.executions[NEW]["template"]["containers"][0]["args"] = ["--verify-capabilities"]
    elif change == "wrong_marker":
        reader.executions[NEW]["template"]["containers"][0]["env"][-1]["value"] = RUN_URL + "1"
    elif change == "wrong_image":
        reader.executions[NEW]["template"]["containers"][0]["image"] = IMAGE[:-1] + "c"
    elif change == "wrong_creator":
        reader.executions[NEW]["creator"] = control.deploy.SCHEDULER
    elif change == "duplicate":
        other = control.JOB + "/executions/monitoring-slack-relay-ghi56"
        reader.executions[other] = {**deepcopy(reader.executions[NEW]), "name": other}
    elif change == "replaced_deployment":
        api.records[0]["id"] += 1
    with pytest.raises(model.Invalid):
        control.verify(api, reader, store, identity, value, proof, clock=lambda: NOW)


@pytest.mark.parametrize("raw", ['{"schema":"fake"}', '{"schema":"fake","schema":"duplicate"}', '{}\n', '{}|injected'])
def test_noncanonical_or_unknown_request_is_rejected(boundary, raw):
    with pytest.raises(model.Invalid):
        control.prepare(*boundary[:3], boundary[3], raw, clock=lambda: PREPARED)


def test_valid_but_noncanonical_request_is_not_silently_rewritten(boundary):
    with pytest.raises(model.Invalid, match="canonical"):
        control.prepare(*boundary[:3], boundary[3], json.dumps(boundary[4], indent=2), clock=lambda: PREPARED)


@pytest.mark.parametrize("tail", [{"error": {"code": 403}}, {"executions": {}}, {"nextPageToken": "second"}])
def test_full_execution_pagination_never_treats_bad_tail_as_empty(tail):
    reader = Reader()
    reader.pages = [{"executions": [execution()], "nextPageToken": "second"}, tail]
    with pytest.raises(model.Invalid):
        control.inventory(reader)
    query = parse_qs(urlsplit(reader.calls[0]).query)
    assert query == {"pageSize": ["1000"], "showDeleted": ["true"]}
    assert parse_qs(urlsplit(reader.calls[1]).query)["pageToken"] == ["second"]


def test_documented_empty_inventory_is_distinct_from_failure():
    reader = Reader()
    reader.pages = [{}]
    assert control.inventory(reader) == []


@pytest.mark.parametrize("retry", [None, False, 1, 3])
def test_job_retry_policy_must_explicitly_disable_retries(retry):
    raw = job()
    task = raw["template"]["template"]
    if retry is None:
        del task["maxRetries"]  # TaskTemplate omission uses the service default of 3.
    else:
        task["maxRetries"] = retry
    with pytest.raises(model.Invalid, match="execution contract"):
        control.job_config(raw, IMAGE)


def test_recognized_partial_page_read_restarts_complete_snapshot(boundary, monkeypatch):
    reader = boundary[1]
    reader.pages = [{"executions": [execution()], "nextPageToken": "second"}, api_errors.ServiceUnavailable("outage"),
                    {"executions": [execution()]}]
    monkeypatch.setattr(control.operational_retry, "sleep", lambda delay: None)
    assert [row["name"] for row in control.inventory(reader)] == [OLD]
    assert "pageToken=" not in reader.calls[2]


def test_authorization_read_failure_is_not_retried(boundary):
    reader = boundary[1]
    reader.pages = [api_errors.Forbidden("denied")]
    with pytest.raises(api_errors.Forbidden):
        control.inventory(reader)
    assert len(reader.calls) == 1


def test_real_cli_serializes_request_without_production_capability(boundary, monkeypatch, tmp_path):
    api, reader, store, _, wanted, _ = boundary
    for key, value in environment().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(control, "GitHub", lambda: api)
    monkeypatch.setattr(control, "Monitoring", lambda *args: reader)
    monkeypatch.setattr(control, "Store", lambda *args: store)
    monkeypatch.setattr(control.deploy, "session", object)
    from google.cloud import storage
    monkeypatch.setattr(storage, "Client", lambda **kwargs: object())
    monkeypatch.setattr(model, "utc_now", lambda: NOW)
    outputs = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    proof = tmp_path / "proof.json"
    value = model.canonical(wanted).decode()
    control.main(["prepare", "--executor-sha", SHA, "--source-run-id", "202", "--source-run-attempt", "1",
                  "--request-json", value, "--prepared-proof", str(proof)])
    assert outputs.read_text() == "request_json=" + value + "\n"
    assert json.loads(proof.read_bytes())["request"] == wanted
    assert store.generation == 1


def test_workflow_protects_exact_receipt_override_and_uses_admission_before_auth():
    root = Path(__file__).parents[1]
    path = root / control.WORKFLOW
    source = path.read_text()
    document = yaml.safe_load(source)
    job = document["jobs"]["reconcile"]
    assert repo_guardrails.has_guarded_main_workflow_checkout(document, control.WORKFLOW)
    assert job["environment"] == "shared-datasets-production"
    assert job["concurrency"] == {"group": "prod-terraform-state", "queue": "max", "cancel-in-progress": False}
    assert set(document.get("on", document.get(True))["workflow_dispatch"]["inputs"]) == {
        "executor_sha", "source_run_id", "source_run_attempt", "receipt_request_json"}
    assert "terraform apply" not in source and "tested_image_authorization.py" not in source
    assert "secrets." not in source and "--reconcile-receipt-json|${CONTROL_REQUEST_JSON}" in source
    assert "--update-env-vars=\"SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL=" in source
    assert source.index(" admit --bootstrap") < source.index("google-github-actions/auth@")
    assert source.count("uses: actions/checkout@") == 1
    assert source.count("gcloud run jobs execute monitoring-slack-relay") == 1
    for step in job["steps"]:
        if "run" in step:
            result = subprocess.run(["bash", "-n"], input=step["run"], text=True, capture_output=True)
            assert result.returncode == 0, result.stderr


def test_actual_workflow_bash_keeps_canonical_json_in_one_cloud_run_argument(boundary, tmp_path):
    source = yaml.safe_load((Path(__file__).parents[1] / control.WORKFLOW).read_text())
    step = next(row for row in source["jobs"]["reconcile"]["steps"] if row["name"] == "Execute only the exact receipt request")
    binary = tmp_path / "gcloud"
    binary.write_text("#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n")
    binary.chmod(0o755)
    value = model.canonical(boundary[4]).decode()
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"], "RUNNER_TEMP": str(tmp_path),
           "CONTROL_REQUEST_JSON": value, "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": control.REPOSITORY,
           "GITHUB_RUN_ID": "777"}
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", step["run"]], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    arguments = json.loads((tmp_path / "monitoring-slack-receipt-execution.json").read_bytes())
    assert arguments == ["run", "jobs", "execute", "monitoring-slack-relay", "--region=us-central1", "--project=shared-datasets-1", "--wait",
                         "--args=^|^--reconcile-receipt-json|" + value,
                         "--update-env-vars=SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL=" + RUN_URL, "--format=json"]


def test_real_monitoring_reader_receives_verified_config_before_exact_execution_get(boundary):
    api, _, store, identity, wanted, _ = boundary
    class Response:
        status_code = 200
        def __init__(self, value):
            self.value = value
        def iter_content(self, size):
            yield model.canonical(self.value)
        def close(self):
            pass
    class Session:
        def __init__(self):
            self.urls = []
        def get(self, url, **kwargs):
            self.urls.append(url)
            assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
            path = url.removeprefix("https://run.googleapis.com/v2/")
            if path == control.JOB:
                return Response(job())
            if path.startswith(control.JOB + "/executions?"):
                return Response({"executions": [execution()]})
            assert path == OLD
            return Response(execution())
    session = Session()
    reader = control.Monitoring(session, None)
    proof = control.prepare(api, reader, store, identity, model.canonical(wanted).decode(), clock=lambda: PREPARED)
    assert proof["writer"]["execution"] == OLD and reader.config.value == config().value
    assert len(session.urls) == 3
