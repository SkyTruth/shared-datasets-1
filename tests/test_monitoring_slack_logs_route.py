"""A transport failure remains exact durable evidence without recursive Slack."""
from copy import deepcopy

import pytest

from scripts import cloud_incident_adapter as adapter, cloud_operational_observations as cloud
from scripts import deployment_emission as emissions, deployment_revision as deployments
from scripts import operational_policy as policy, slack_incidents as incidents, workflow_failure_alert as alerts
from test_cloud_incident_adapter import at, auxiliary_proof, auxiliary_source, proposal, source
from test_monitoring_slack_cloud_contract import Authority, IMAGE, JOB, cold, fact, ingest
from test_slack_incident_policy import PersistedLedger
from test_slack_incidents import FakeSlack, IDENTITY


@pytest.mark.parametrize("kind", ["execution", "runtime", "receipt-probe"])
def test_cold_signed_relay_failure_has_no_parent_and_preserves_proof(monkeypatch, tmp_path, kind):
    monkeypatch.setattr(emissions, "verify", lambda *_args, **_kwargs: None)
    api, ledger, slack = Authority(), PersistedLedger(), FakeSlack()
    value = fact("receipt" if kind == "receipt-probe" else "failure", typed=kind == "runtime")
    if kind == "runtime":
        value["fact"]["details"] = {"mutation_status": "not_started", "failure_reason": "permission_denied"}
        adapter.validate_fact(value)
    records, evaluation = ingest(ledger, api, value, at(2))
    incidents.deliver(ledger, records, slack)
    ledger = cold(ledger, api, tmp_path)
    state = ledger.states[adapter.source_scope(value)]
    original = deepcopy(state["incident"])
    assert state["first_failure_at"] == at()
    assert original["posts"] == {} and original["resolution"] is None
    assert state["escalation_at"] is None and incidents.desired_posts(state, now=at(7200)) == {}
    retained = original["failures"][0]
    assert retained["proof"]["fact"] == value
    assert retained["proof"]["correlation"]["artifact"] == IMAGE
    normalized = adapter.operational(retained)
    decision = policy.decide(normalized, first_failure_at=at(), now=at(7200))
    assert normalized.reason == policy.Reason.LOGS_ONLY
    assert decision.disposition == policy.Disposition.RECORD_ONLY and not decision.retry_safe
    assert decision.freeze_replay is (kind != "runtime")
    assert evaluation["provenance_complete"] and evaluation["cloud_counts"]["actionable"] == 0
    assert evaluation["sources"] == [value] and evaluation["observations"] == [retained]
    bundle = cloud.evaluated_collection(proposal(value), evaluation)
    assert incidents.verify_cloud_evaluation(bundle["checkpoint"], evaluation, ledger.states,
                                            shadow=False, worker=evaluation["worker"])
    records, _ = ingest(ledger, api, value, at(7200))
    incidents.deliver(ledger, records, slack)
    assert ledger.states[state["scope"]]["incident"] == original
    assert slack.calls == []


def test_unverified_relay_image_stays_logs_only_but_blocks_provenance(monkeypatch):
    monkeypatch.setattr(emissions, "verify", lambda *_args, **_kwargs: None)
    api, ledger, slack = Authority(), PersistedLedger(), FakeSlack()
    api.record["payload"]["targets"] = []
    value = fact()
    records, evaluation = ingest(ledger, api, value, at(2))
    incidents.deliver(ledger, records, slack)
    assert evaluation["sources"] == [value] and evaluation["observations"][0]["proof"]["correlation"] is None
    assert evaluation["provenance_complete"] is False
    assert evaluation["provenance_gap_source_ids"] == [value["source_id"]]
    assert evaluation["cloud_counts"]["actionable"] == 0 and slack.calls == []


@pytest.mark.parametrize("kind", ["execution_request", "scheduler_dispatch"])
def test_relay_dispatch_failure_cannot_recurse_or_hide_unverified_coverage(kind, monkeypatch, tmp_path):
    monkeypatch.setattr(emissions, "verify", lambda *_args, **_kwargs: None)
    original = fact()
    value = {key: original[key] for key in ("source_id", "observed_at", "resource_type", "job", "evidence")}
    value.update(kind=kind, mutation_status="uncertain",
                 coverage_gaps=["execution_request_outcome_unverified" if kind == "execution_request"
                                else "scheduler_dispatch_outcome_unverified"])
    if kind == "execution_request":
        value["request"] = {"method": "google.cloud.run.v1.Jobs.RunJob", "resource_name": cloud.job_parent(JOB),
                            "request_name": cloud.job_parent(JOB), "status": {"code": 7}}
    else:
        value["resource_type"] = "cloud_scheduler_job"
    api, ledger, slack = Authority(), PersistedLedger(), FakeSlack()
    records, evaluation = ingest(ledger, api, value, at(2))
    incidents.deliver(ledger, records, slack)
    ledger = cold(ledger, api, tmp_path)
    observation = ledger.states[adapter.source_scope(value)]["incident"]["failures"][0]
    assert observation["revision"].startswith("cloudlog-source:")
    decision = policy.decide(adapter.operational(observation), first_failure_at=at(), now=at(2))
    assert decision.disposition == policy.Disposition.RECORD_ONLY and decision.freeze_replay
    assert not evaluation["provenance_complete"] and evaluation["provenance_gap_source_ids"] == [value["source_id"]]
    assert slack.calls == []


def test_other_observer_and_unknown_job_failures_still_escalate():
    observer = auxiliary_source(status="failed", reason="permission_denied")
    proof = auxiliary_proof(observer)
    normalized = adapter.normalize(observer, correlation=proof)
    assert adapter.operational(normalized).reason == policy.Reason.PERMISSION_DENIED
    ledger, slack = PersistedLedger(), FakeSlack()
    incidents.deliver(ledger, incidents.prepare(ledger, IDENTITY, [normalized], now=at(2),
                                              success=lambda *_: None, cloud_delivery_enabled=True), slack)
    assert [call[0] for call in slack.calls] == ["post"]
    unknown = source("unknown", job="new-unregistered-job")
    ledger, slack = PersistedLedger(), FakeSlack()
    records, evaluation = incidents.prepare_cloud(ledger, IDENTITY, proposal(unknown), shadow=False,
                                                 now=at(2), github_api=None)
    incidents.deliver(ledger, records, slack)
    assert evaluation["provenance_gap_source_ids"] == [unknown["source_id"]]
    assert evaluation["cloud_counts"]["actionable"] == 1
    assert [call[0] for call in slack.calls] == ["post"]


def test_logs_only_is_not_a_general_suppression_or_retry_permission():
    value = adapter.operational(adapter.normalize(fact()))
    assert value.reason == policy.Reason.LOGS_ONLY
    for changes in (
        {"target": "cloud:cloud_run_job:new-unregistered-job", "operation_id": "cloud:cloud_run_job:new-unregistered-job"},
        {"target": "cloud:identity:" + "a" * 64, "operation_id": "cloud:identity:" + "a" * 64},
        {"target": "cloud:translation:" + "a" * 64, "operation_id": "cloud:translation:" + "a" * 64},
        {"target": "cron-alert-policy", "operation_id": "deployment:cron-alert-policy"},
        {"source_id": "github:1:1:0:0"},
        {"recovery": "read_only_retry"},
    ):
        with pytest.raises(policy.PolicyError):
            policy.OperationalObservation.from_dict({**value.to_dict(), **changes})
    for reason in ("security_risk", "data_integrity_risk", "unapproved_mutation"):
        security = policy.OperationalObservation.from_dict({**value.to_dict(), "reason": reason})
        decision = policy.decide(security, first_failure_at=at(), now=at())
        assert decision.disposition == policy.Disposition.INDEPENDENT_SECURITY
        assert decision.freeze_replay and not decision.retry_safe


def test_exact_transport_deployment_can_retain_responsible_pr_metadata():
    value = incidents.operational_fact({"scope": "deployment:" + JOB, "kind": "deployment", "label": JOB,
        "run_id": 100, "attempt": 1, "sha": "a" * 40, "time": at(), "job_id": 1,
        "job_name": "Exact registered deployment", "conclusion": "failure", "proof": None})
    bound = policy.OperationalObservation.from_dict({**value.to_dict(), "pr_number": 17})
    assert bound.binding.pr_number == 17
    decision = policy.decide(bound, first_failure_at=at(), now=at())
    assert decision.disposition == policy.Disposition.RECORD_ONLY and decision.freeze_replay


class SourceAPI:
    def __init__(self, name, filename, jobs, *, trigger="workflow_run"):
        self.repo = {"id": 41, "full_name": incidents.REPOSITORY}
        self.run = {"id": 100, "run_attempt": 1, "name": name, "workflow_id": 7,
                    "path": ".github/workflows/" + filename, "event": trigger, "head_sha": "a" * 40,
                    "status": "completed", "conclusion": "failure", "head_branch": "main", "updated_at": at(),
                    "repository": self.repo, "head_repository": self.repo}
        self.jobs, self.reads = jobs, []

    def get(self, path):
        self.reads.append(path)
        if path.endswith("/actions/runs/100"):
            return deepcopy(self.run)
        if path.endswith("/actions/workflows/7"):
            return {"id": 7, "path": self.run["path"]}
        assert path == "repos/" + incidents.REPOSITORY
        return deepcopy(self.repo)

    def pages(self, path, field=None):
        assert path.endswith("/runs/100/attempts/1/jobs?per_page=100") and field == "jobs"
        return deepcopy(self.jobs)


def job(identifier, name, conclusion="failure"):
    return {"id": identifier, "name": name, "status": "completed", "conclusion": conclusion, "completed_at": at()}


@pytest.mark.parametrize("name,filename", [
    ("Monitoring Slack relay deploy", "monitoring-slack-relay-deploy.yml"),
    ("Monitoring Slack relay receipt control", "monitoring-slack-relay-receipt.yml"),
])
def test_exact_github_relay_failures_are_retained_without_public_delivery(monkeypatch, tmp_path, name, filename):
    monkeypatch.setattr(emissions, "verify", lambda *_args, **_kwargs: None)
    api = SourceAPI(name, filename, [job(1, "Unrelated-looking step")])
    event = {"workflow_run": api.run, "repository": api.repo}
    assert alerts.alert_for_run(event) is None
    values = incidents.source_observations(api, event, {})
    assert len(values) == 1 and values[0]["scope"] == "workflow:" + filename
    assert api.reads == ["repos/" + incidents.REPOSITORY + suffix for suffix in
                         ("/actions/runs/100", "/actions/workflows/7", "")]
    ledger, slack = PersistedLedger(), FakeSlack()
    incidents.deliver(ledger, incidents.prepare(ledger, IDENTITY, values, now=at(7200), success=lambda *_: None), slack)
    ledger = cold(ledger, Authority(), tmp_path)
    state = ledger.states[values[0]["scope"]]
    assert state["incident"]["failures"] == values and state["incident"]["posts"] == {}
    classified = incidents.operational_fact(values[0])
    assert classified.reason == policy.Reason.LOGS_ONLY
    assert classified.mutation_status == policy.MutationStatus.UNCERTAIN
    assert policy.decide(classified, first_failure_at=at(), now=at(7200)).freeze_replay
    assert slack.calls == []


@pytest.mark.parametrize("target", sorted(deployments.LOGS_ONLY_TARGETS))
def test_future_ci_caller_uses_exact_target_graph_without_quieting_other_failures(target):
    jobs = [job(1, "Exact mapped relay"), job(2, "Cron policy"), job(3, "Monitoring Slack relay deploy"),
            job(4, "ci-ready", "success")]
    api = SourceAPI("CI", "ci.yml", jobs, trigger="push")
    targets = {("ci.yml", jobs[0]["name"]): target, ("ci.yml", jobs[1]["name"]): "terraform-c34cc7"}
    values = incidents.source_observations(api, {"workflow_run": api.run, "repository": api.repo}, targets)
    assert len(values) == 3
    assert incidents.operational_fact(values[0]).reason == policy.Reason.LOGS_ONLY
    assert all(incidents.operational_fact(value).reason == policy.Reason.UNKNOWN for value in values[1:])
    ledger, slack = PersistedLedger(), FakeSlack()
    incidents.deliver(ledger, incidents.prepare(ledger, IDENTITY, values, now=at(7200), success=lambda *_: None), slack)
    posted_scopes = [call[1]["metadata"]["event_payload"]["scope"] for call in slack.calls if call[0] == "post"]
    assert len(posted_scopes) == 2 and "deployment:" + target not in posted_scopes


def test_forged_relay_workflow_identity_does_not_bypass_source_verification():
    api = SourceAPI("Monitoring Slack relay deploy", "ci.yml", [])
    with pytest.raises(ValueError, match="trusted main source"):
        incidents.source_observations(api, {"workflow_run": api.run, "repository": api.repo}, {})
