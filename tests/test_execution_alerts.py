"""Cross-producer execution identity, signed observer routing and exact error reads."""
import copy
import json
from unittest.mock import Mock

import pytest

from scripts import slack_incidents as incidents
from services.monitoring_slack import run as relay
from services.monitoring_slack import executions, decisions
from test_monitoring_slack import Bucket, decision_event
import test_deployment_revision as provenance
from test_slack_incidents import MemoryLedger, FakeSlack, IDENTITY


@pytest.mark.parametrize("change", [None, "unsigned", "wrong-attempt", "unknown-outcome", "wrong-execution", "attestation-failed"])
def test_only_signed_worker_failure_avoids_a_second_slack_parent(monkeypatch, change):
    fixture = provenance.DeploymentProvenanceTests()
    fixture.setUp()
    api = fixture.api
    repo = {"id": 17, "full_name": incidents.REPOSITORY}
    source = {"id": 100, "run_attempt": 1, "workflow_id": 77,
              "name": "Deployments / Completion check", "path": ".github/workflows/deployment-verification.yml",
              "event": "workflow_dispatch", "head_branch": "main", "head_sha": provenance.CURRENT,
              "status": "completed", "conclusion": "failure", "updated_at": "2026-11-03T12:54:57Z",
              "repository": repo, "head_repository": repo}
    job = {"id": 200, "name": "verify (1)", "status": "completed", "conclusion": "failure",
           "completed_at": source["updated_at"], "steps": [{"name": "Attest verified execution outcome",
             "status": "completed", "conclusion": "success"}]}
    record = provenance.ReplayTests.record(provenance.CURRENT)
    failed = {"id": 2, "state": "failure", "description": "failed",
              "log_url": incidents.run_link({"run_id": 100, "attempt": 1}),
              "environment_url": "https://console.cloud.google.com/run/jobs/executions/details/us-central1/wdpa-monthly-abcde?project=shared-datasets-1"}
    if change == "wrong-attempt":
        failed["log_url"] = incidents.run_link({"run_id": 100, "attempt": 2})
    elif change == "unknown-outcome":
        failed.update(state="error", description="unknown")
    elif change == "wrong-execution":
        failed["environment_url"] = failed["environment_url"].replace("wdpa-monthly", "another-job")
    elif change == "attestation-failed":
        job["steps"][0]["conclusion"] = "failure"
    def get(path):
        if path.endswith("/deployments/1"):
            return record
        if path.endswith("/actions/runs/100"):
            return source
        if path.endswith("/actions/workflows/77"):
            return {"id": 77, "path": source["path"]}
        return fixture.route(path)
    def pages(path, field=None):
        if "/runs/100/" in path:
            return [job]
        if "/deployments/1/statuses?" in path:
            return [failed, {"id": 1, "description": "started"}]
        return [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]
    api.get.side_effect, api.pages.side_effect = get, pages
    verifier = Mock(side_effect=incidents.emissions.EmissionError("invalid signature") if change == "unsigned" else None)
    monkeypatch.setattr(incidents.emissions, "verify", verifier)
    observed = incidents.source_observations(api, {"workflow_run": source, "repository": repo}, {})
    ledger, slack = MemoryLedger(), FakeSlack()
    prepared = incidents.prepare(ledger, IDENTITY, json.loads(json.dumps(observed)), success=lambda *_: (None, None))
    incidents.deliver(ledger, prepared, slack)
    if change is None:
        assert slack.calls == []  # Cloud Monitoring owns the failed worker's parent.
        assert verifier.call_count == 2  # Original claim and this exact failed outcome.
    else:
        assert len([call for call in slack.calls if call[0] == "post"]) == 1
        assert "WDPA execution verification" in slack.calls[0][1]["text"]
        assert "receipt-signing step" in slack.calls[0][1]["text"]


def test_error_lookup_queries_and_validates_only_the_exact_execution(monkeypatch):
    resource = {"type": "cloud_run_job", "labels": {"project_id": relay.PROJECT, "location": "us-central1", "job_name": "wdpa-monthly"}}
    alert = {"resource": resource, "log": {"extractedLabels": {"execution": "wdpa-monthly-abcde"}}}
    row = {"resource": copy.deepcopy(resource), "labels": {"run.googleapis.com/execution_name": "wdpa-monthly-abcde"},
           "textPayload": "Traceback (most recent call last):\nRuntimeError: identity review exceeds case limit"}
    session = Mock()
    system = {**row, "textPayload": "Task exited with code 1."}
    session.post.return_value.json.return_value = {"entries": [system, row]}
    monkeypatch.setattr("google.auth.default", lambda **_: (object(), relay.PROJECT))
    monkeypatch.setattr("google.auth.transport.requests.AuthorizedSession", lambda _: session)
    assert relay.read_failure(alert) == "RuntimeError: identity review exceeds case limit"
    request = session.post.call_args.kwargs
    assert request["json"]["pageSize"] == 20
    assert 'labels."run.googleapis.com/execution_name"="wdpa-monthly-abcde"' in request["json"]["filter"]
    assert request["json"]["resourceNames"] == [f"projects/{relay.PROJECT}"]
    row["labels"]["run.googleapis.com/execution_name"] = "wdpa-monthly-zzzzz"
    with pytest.raises(ValueError, match="foreign failure log"):
        relay.read_failure(alert)


def test_failure_policy_extracts_identity_without_weakening_coverage():
    from pathlib import Path
    import hcl2
    from hcl2.utils import SerializationOptions
    document = hcl2.loads(Path("terraform/envs/prod/monitoring.tf").read_text(),
                         serialization_options=SerializationOptions(strip_string_quotes=True))
    policy = next(resource["google_monitoring_alert_policy"]["scheduled_ingestion_cloud_run_failure"]
                  for resource in document["resource"]
                  if "scheduled_ingestion_cloud_run_failure" in resource.get("google_monitoring_alert_policy", {}))
    condition = policy["conditions"][0]["condition_matched_log"][0]
    assert condition["label_extractors"]["execution"] == 'EXTRACT(protoPayload.response.metadata.name)'
    assert "protoPayload.status.code>0" in condition["filter"]
    assert "SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL" in condition["filter"]


def test_competing_updates_retry_the_current_parent_even_after_a_stale_slack_write():
    from google.api_core.exceptions import PreconditionFailed
    store = relay.Store(Bucket())
    ctx = {"project": relay.PROJECT, "job": "wdpa-monthly", "execution": "wdpa-monthly-abcde"}
    slack = Mock()
    slack.post.return_value = "1792830681.000001"
    executions.report(store, slack, IDENTITY, ctx, failure=("0.first", False, "first error"))
    delivered, competing = [], False
    def update(ts, payload):
        nonlocal competing
        if not competing:
            competing = True
            executions.report(store, slack, IDENTITY, ctx, failure=("0.third", False, "third error"))
        delivered.append(payload["text"])
    slack.update.side_effect = update
    with pytest.raises(PreconditionFailed):
        executions.report(store, slack, IDENTITY, ctx, failure=("0.second", False, "second error"))
    assert "third error" not in delivered[-1]  # The stale writer reached Slack last.
    executions.report(store, slack, IDENTITY, ctx, failure=("0.second", False, "second error"))
    assert "third error" in delivered[-1]
    slack.post.assert_called_once()


def test_interrupted_decision_receipt_preserves_immutable_cases_and_reuses_execution_parent():
    store, slack = relay.Store(Bucket()), Mock()
    slack.post.return_value = "1792830681.000001"
    source = decision_event()
    write = store.write
    def interrupt_ack(path, value, generation):
        if path.startswith("identity/events/") and value["parent"]["ts"] is not None:
            raise OSError("lost event receipt acknowledgement")
        return write(path, value, generation)
    store.write = interrupt_ack
    with pytest.raises(OSError):
        decisions.deliver(source, store, slack, IDENTITY)
    store.write = write
    changed = copy.deepcopy(source)
    changed["cases"][0]["new_identity_key"] = ["changed-source-key"]
    with pytest.raises(ValueError, match="content changed"):
        decisions.deliver(changed, store, slack, IDENTITY)
    decisions.deliver(source, store, slack, IDENTITY)
    slack.post.assert_called_once()
    assert decisions.load(store, decisions.wire.event_key(source), IDENTITY)[0]["parent"]["ts"] == slack.post.return_value


def test_unconfirmed_historical_decision_claim_never_gets_a_new_execution_parent():
    store, slack = relay.Store(Bucket()), Mock()
    source = decision_event()
    decisions.save(store, decisions.wire.event_key(source), {"schema": decisions.EVENT_SCHEMA, "event": source,
        "identity": IDENTITY, "parent": {"owner": "bot", "ts": None}, "parent_view": None, "replies": {}}, 0)
    with pytest.raises(ValueError, match="unconfirmed historical"):
        decisions.deliver(source, store, slack, IDENTITY)
    slack.post.assert_not_called()
