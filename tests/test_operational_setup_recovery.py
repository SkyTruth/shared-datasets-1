"""A larger reviewed setup scope recovers its original signed incident quietly."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import yaml

from scripts import deployment_emission as emissions, deployment_revision as deployments
from scripts import slack_incidents as incidents
from test_slack_incidents import FakeSlack, IDENTITY


ROOT = Path(__file__).resolve().parents[1]
SYNC = "Operational observation setup"
TARGET = "terraform-" + hashlib.sha256(SYNC.encode()).hexdigest()[:16]
SCOPE = "deployment:" + TARGET
OLD_SHA = "18b2884cc4937e0802fb68f85fba1d3765ebfb62"
NEW_SHA = "e3af4b3efa460cc4e78886f6e4153e90d218256a"
PARENT_TS = "1792602564.172379"
PARENT_URL = "https://example.slack.com/archives/" + IDENTITY["channel"] + "/p1792602564172379"
# Exact historical scope, including the four view resources never persisted by
# the failed protected apply. Do not reinterpret it as the current 35 targets.
OLD_TARGETS = """google_iam_workload_identity_pool_provider.operational_observer
google_project_iam_audit_config.operational_run_data_write
module.operational_observer_service_account.google_service_account.this
google_service_account_iam_member.operational_observer_wif
google_logging_log_view.operational_observations["_Default"]
google_logging_log_view.operational_observations["_Required"]
google_logging_log_view_iam_member.operational_observer["_Default"]
google_logging_log_view_iam_member.operational_observer["_Required"]
google_project_iam_custom_role.operational_observer_reader
google_project_iam_member.operational_observer_reader
google_project_iam_custom_role.operational_observer_receipts
google_storage_bucket_iam_member.operational_observer_receipts["0"]
google_storage_bucket_iam_member.operational_observer_receipts["1"]
google_storage_bucket_iam_member.operational_observer_receipts["2"]
google_storage_bucket_iam_member.operational_observer_receipts["3"]
google_storage_bucket_iam_member.operational_observer_receipts["4"]
google_storage_bucket_iam_member.operational_observer_manifests["0"]
google_storage_bucket_iam_member.operational_observer_manifests["1"]
google_storage_bucket_iam_member.operational_observer_manifests["2"]
google_storage_bucket_iam_member.operational_observer_manifests["3"]
google_storage_bucket_iam_member.operational_observer_manifests["4"]
google_monitoring_metric_descriptor.operational_reconciliation_heartbeat
google_monitoring_metric_descriptor.operational_reconciliation_heartbeat_v2""".splitlines()


def wire(value):
    return json.loads(json.dumps(value))


class DurableAPI:
    """Serialized external API storage, not an in-memory Ledger replacement."""

    def __init__(self):
        self.repository = {"id": 1, "full_name": incidents.REPOSITORY}
        self.runs, self.records, self.statuses, self.signed = {}, {}, {}, {}
        self.signatures, self.writes = [], []
        self.workflows = {
            ".github/workflows/ci.yml": 10, incidents.WORKFLOW: 20,
        }

    def run(self, identifier, sha, *, worker=False):
        path = incidents.WORKFLOW if worker else ".github/workflows/ci.yml"
        self.runs[identifier] = wire({
            "id": identifier, "run_attempt": 1, "head_sha": sha,
            "path": path, "workflow_id": self.workflows[path], "head_branch": "main",
            "event": "schedule" if worker else "push", "status": "completed",
            "repository": self.repository, "head_repository": self.repository,
        })

    def get(self, path):
        prefix = "repos/" + incidents.REPOSITORY
        if path == prefix:
            return wire(self.repository)
        if path.startswith(prefix + "/actions/workflows/"):
            name = path.rsplit("/", 1)[-1]
            workflow = ".github/workflows/" + name
            return {"id": self.workflows[workflow], "path": workflow}
        if path.startswith(prefix + "/actions/runs/"):
            assert path.endswith("/attempts/1")
            return wire(self.runs[int(path.split("/actions/runs/")[1].split("/")[0])])
        return wire(self.records[int(path.rsplit("/", 1)[-1])])

    def pages(self, path, field=None):
        assert field in {None, "jobs"}
        if "/statuses?" in path:
            return wire(self.statuses[int(path.split("/deployments/")[1].split("/")[0])])
        if "/jobs?" in path:
            return [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]
        if "task=slack-incident" in path:
            return wire([record for record in self.records.values() if record.get("task") == "slack-incident"])
        assert "environment=production-" + TARGET in path
        return wire([record for record in self.records.values() if record["environment"] == "production-" + TARGET])

    def post(self, path, payload):
        payload = wire(payload)
        self.writes.append((path, payload))
        if path.endswith("/deployments"):
            identifier = max(self.records) + 1
            value = {**payload, "id": identifier, "sha": payload["ref"],
                     "creator": {"login": "github-actions[bot]", "type": "Bot"}}
            self.records[identifier], self.statuses[identifier] = wire(value), []
        else:
            identifier = int(path.split("/deployments/")[1].split("/")[0])
            value = {**payload, "id": max(status["id"] for rows in self.statuses.values() for status in rows) + 1}
            self.statuses[identifier].insert(0, wire(value))
        return wire(value)

    def sign(self, record, status):
        self.signed[(record["id"], status["id"])] = emissions.canonical(emissions.receipt(incidents.REPOSITORY, record, status))

    def attest_checkpoints(self):
        for record in self.records.values():
            if record.get("task") == "slack-incident":
                for status in self.statuses[record["id"]]:
                    self.sign(record, status)

    def crypto(self, command, repository, digest):
        # Only the external cryptographic authority is replaced. Production
        # receipt construction, run/source checks and exact subject verification
        # still execute, including old/new scope bytes and cold Ledger loading.
        value = json.loads(Path(command[3]).read_bytes())
        key = value["deployment_id"], value["status"]["id"]
        raw = emissions.canonical(value)
        if self.signed.get(key) != raw:
            raise emissions.EmissionError("unsigned or changed receipt")
        assert hashlib.sha256(raw).hexdigest() == digest
        run = self.runs[int(value["status"]["log_url"].split("/runs/")[1].split("/")[0])]
        signer = command[command.index("--signer-workflow") + 1].removeprefix(repository + "/")
        self.signatures.append(wire(value))
        return json.dumps([{"verificationResult": {
            "signature": {"certificate": emissions.policy(repository, 1, value, run, signer)},
            "verifiedTimestamps": ["verified"],
            "statement": {"_type": "https://in-toto.io/Statement/v1", "predicateType": emissions.PREDICATE,
                          "subject": [{"name": emissions.name(value), "digest": {"sha256": digest}}]},
        }}])


def deployment(api, identifier, sha, run_id, targets, phase):
    value = {"id": identifier, "sha": sha, "ref": sha, "environment": "production-" + TARGET,
             "creator": {"login": "github-actions[bot]", "type": "Bot"},
             "payload": {"schema": deployments.SCHEMA, "target": TARGET, "ci_run_id": run_id, "ci_run_attempt": 1,
                         "execution_run_id": run_id, "execution_run_attempt": 1,
                         "artifact": "terraform@sha256:" + ("a" if phase == "failed" else "b") * 64,
                         "targets": list(targets)}}
    api.records[identifier] = wire(value)
    statuses = [{"id": identifier * 10 + 1, "description": "started", "state": "in_progress",
                 "log_url": emissions.invocation(incidents.REPOSITORY, run_id, 1), "created_at": "2026-10-22T03:00:00Z"}]
    if phase != "started":
        statuses.insert(0, {**statuses[0], "id": identifier * 10 + 2, "description": phase,
                           "state": emissions.PHASES[phase], "created_at": "2026-10-22T03:30:00Z"})
    api.statuses[identifier] = wire(statuses)
    for status in statuses:
        api.sign(value, status)
    return value


@pytest.fixture
def recovery_cli(tmp_path, monkeypatch):
    api = DurableAPI()
    api.run(100, OLD_SHA)
    api.run(200, NEW_SHA)
    api.run(11, OLD_SHA, worker=True)
    api.run(12, NEW_SHA, worker=True)
    current = yaml.safe_load((ROOT / ".github/workflows/cron-alert-policy-sync.yml").read_text())["jobs"]["observations"]["with"]
    targets = current["targets"].splitlines()
    assert current["sync_name"] == SYNC and current["block_deletes"] is True
    assert TARGET == "terraform-559478561bc2dd2c"
    assert len(OLD_TARGETS) == 23 and len(targets) == 35
    assert len(set(OLD_TARGETS) & set(targets)) == 19
    assert len(set(targets) - set(OLD_TARGETS)) == 16
    assert set(targets) == set(current["allowed_exact"].splitlines())
    old = deployment(api, 1000, OLD_SHA, 100, OLD_TARGETS, "failed")
    new = deployment(api, 1001, NEW_SHA, 200, targets, "applied")
    failure = {"scope": SCOPE, "kind": "deployment", "label": SYNC, "run_id": 100, "attempt": 1,
               "sha": OLD_SHA, "time": "2026-10-22T02:00:00Z", "job_id": 119483573270,
               "job_name": "cron-alert-policy / Apply operational observation setup / Apply Operational observation setup",
               "conclusion": "failure", "proof": None}
    state = incidents.fold(None, failure, IDENTITY)
    retry = {**failure, "run_id": 101, "time": "2026-10-22T02:30:00Z"}
    state = incidents.fold(state, retry, IDENTITY)
    incidents.latch_escalation(state, "2026-10-22T03:00:00Z")
    state["incident"]["posts"] = {
        "parent": {"state": "delivered", "ts": PARENT_TS},
        "attempt:" + incidents.observation_key(retry): {"state": "claimed", "ts": None},
    }
    state["incident"]["permalink"] = PARENT_URL
    state["incident"]["parent_view_sha256"] = incidents.parent_view_sha256(state)
    record = {"id": 9000, "sha": OLD_SHA, "ref": OLD_SHA, "task": "slack-incident",
              "environment": incidents.ENVIRONMENT, "production_environment": False,
              "creator": {"login": "github-actions[bot]", "type": "Bot"},
              "payload": {"schema": incidents.SCHEMA, "state": state, "execution_run_id": 11, "execution_run_attempt": 1}}
    api.records[9000] = wire(record)
    api.statuses[9000] = [{"id": 90000, "state": "success", "description": "applied",
                           "log_url": emissions.invocation(incidents.REPOSITORY, 11, 1)}]
    api.attest_checkpoints()
    slack = FakeSlack()
    (tmp_path / "event.json").write_text("{}\n")
    monkeypatch.setattr(deployments, "GitHub", lambda: api)
    monkeypatch.setattr(incidents, "Slack", lambda *_: slack)
    monkeypatch.setattr(emissions, "verified_output", api.crypto)
    for key, value in {"GITHUB_REPOSITORY": incidents.REPOSITORY, "GITHUB_REF": "refs/heads/main",
                       "GITHUB_WORKFLOW_REF": incidents.REPOSITORY + "/" + incidents.WORKFLOW + "@refs/heads/main",
                       "GITHUB_RUN_ID": "12", "GITHUB_RUN_ATTEMPT": "1", "RUNNER_TEMP": str(tmp_path),
                       "GITHUB_EVENT_PATH": str(tmp_path / "event.json"),
                       "GITHUB_OUTPUT": str(tmp_path / "outputs")}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    def invoke(command):
        monkeypatch.setattr(sys, "argv", ["slack_incidents.py", command, "--work-dir", str(tmp_path)])
        incidents.main()

    return SimpleNamespace(api=api, old=old, new=new, original=wire(state), slack=slack,
                           invoke=invoke, path=tmp_path)


def test_current35_target_success_preserves_historical23_receipt_and_updates_only_original_parent(recovery_cli):
    case = recovery_cli
    assert deployments.verify_record(case.api, incidents.REPOSITORY, wire(case.old))["targets"] == OLD_TARGETS
    case.invoke("prepare")
    prepared = json.loads((case.path / "prepared.json").read_bytes())
    assert len(prepared) == 1
    case.api.attest_checkpoints()  # The workflow signs claims before delivery.
    case.invoke("deliver")
    case.api.attest_checkpoints()
    case.invoke("verify")  # A fresh real Ledger reads serialized durable bytes.
    states = incidents.Ledger(case.api, case.api.runs[12], case.path).load()
    assert set(states) == {SCOPE}
    state = states[SCOPE]
    assert state["incident"]["id"] == case.original["incident"]["id"]
    assert state["incident"]["posts"] == case.original["incident"]["posts"]
    assert state["incident"]["permalink"] == PARENT_URL
    assert state["incident"]["failures"] == case.original["incident"]["failures"]
    assert state["incident"]["resolution"]["proof"]["record_id"] == case.new["id"]
    assert [call[0] for call in case.slack.calls] == ["update"]
    assert case.slack.calls[0][2] == {"ts": PARENT_TS}
    assert "Resolved" in case.slack.calls[0][1]["text"]
    assert case.api.records[case.old["id"]] == case.old
    assert any(proof["deployment_id"] == case.old["id"] and proof["payload"]["targets"] == OLD_TARGETS
               for proof in case.api.signatures)
    assert any(proof["deployment_id"] == case.new["id"] and len(proof["payload"]["targets"]) == 35
               for proof in case.api.signatures)


@pytest.mark.parametrize("phase", ["failed", "unknown", "started"])
def test_newer_nonterminal_or_failed_attempt_never_falls_back_to_earlier_success(recovery_cli, phase):
    case = recovery_cli
    deployment(case.api, 1002, NEW_SHA, 200, case.new["payload"]["targets"], phase)
    case.invoke("prepare")
    assert json.loads((case.path / "prepared.json").read_bytes()) == []
    case.invoke("deliver")
    assert case.slack.calls == [] and case.api.writes == []
    assert incidents.Ledger(case.api, case.api.runs[12], case.path).load()[SCOPE] == case.original


@pytest.mark.parametrize("tamper", ["unsigned-claim", "unsigned-outcome", "changed-scope"])
def test_unsigned_or_changed_latest_receipt_cannot_recover_or_broadcast(recovery_cli, tamper):
    case = recovery_cli
    if tamper.startswith("unsigned-"):
        index = -1 if tamper == "unsigned-claim" else 0
        del case.api.signed[(case.new["id"], case.api.statuses[case.new["id"]][index]["id"])]
    else:
        case.api.records[case.new["id"]]["payload"]["targets"].pop()
    with pytest.raises((deployments.DeploymentError, emissions.EmissionError), match="unsigned or changed receipt"):
        case.invoke("prepare")
    assert case.slack.calls == [] and case.api.writes == []
    assert incidents.Ledger(case.api, case.api.runs[12], case.path).load()[SCOPE] == case.original
