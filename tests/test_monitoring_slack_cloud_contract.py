"""Purpose-bound relay health through real signed-provenance and persisted state."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from scripts import cloud_incident_adapter as adapter, cloud_operational_observations as cloud
from scripts import deployment_emission as emissions, deployment_revision as deployments, slack_incidents as incidents
from test_cloud_incident_adapter import at, proposal, source
from test_slack_incident_policy import PersistedLedger
from test_slack_incidents import FakeSlack, IDENTITY, signed_record

JOB = "monitoring-slack-relay"
IMAGE = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/monitoring-slack-relay@sha256:" + "a" * 64
REVISION = "b" * 40
DETAILS = {"mutation_status": "completed", "published_data_state": "unchanged"}


def fact(identifier="failed", *, success=False, typed=False):
    value = source(identifier, job=JOB, image=IMAGE, success=success, status="success" if success else "failed",
                   details=DETAILS if typed else {}, time=at(3) if success else at())
    execution = JOB + "-" + identifier
    value["execution"].update(name=cloud.job_parent(JOB) + "/executions/" + execution,
                              create_time=at(2) if success else at(-1), start_time=at(2) if success else at(),
                              completion_time=at(4) if success else at(1))
    value["fact"]["context"]["execution"] = execution
    if not typed:
        value.pop("fact")
        value["kind"] = "execution"
    return adapter.validate_fact(value)


class Authority:
    """Only API/crypto transport is fake; record/source/receipt checks are real."""
    def __init__(self):
        self.record = {"id": 17, "sha": REVISION, "ref": REVISION, "environment": "production-" + JOB,
                       "creator": {"login": "github-actions[bot]", "type": "Bot"},
                       "payload": {"schema": deployments.SCHEMA, "target": JOB, "ci_run_id": 10, "ci_run_attempt": 1,
                                   "execution_run_id": 20, "execution_run_attempt": 1, "artifact": IMAGE,
                                   "targets": [deployments.AUXILIARY_CLOUD_JOBS[JOB]["resource"]]}}
        self.checkpoints = []

    def get(self, path):
        repo = {"id": 41, "full_name": incidents.REPOSITORY}
        if path == "repos/" + incidents.REPOSITORY:
            return repo
        if "/actions/workflows/" in path:
            workflow = path.rsplit("/", 1)[1]
            return {"id": 1 if workflow == "ci.yml" else 2, "path": ".github/workflows/" + workflow}
        if path.endswith("/deployments/17"):
            return deepcopy(self.record)
        if "/actions/runs/" in path:
            ci = "/runs/10/" in path
            return {"id": 10 if ci else 20, "run_attempt": 1, "workflow_id": 1 if ci else 2,
                    "path": ".github/workflows/" + ("ci.yml" if ci else "monitoring-slack-relay-deploy.yml"),
                    "event": "push" if ci else "workflow_dispatch", "head_branch": "main", "head_sha": REVISION,
                    "repository": repo, "head_repository": repo}
        raise AssertionError(path)

    def pages(self, path, field=None):
        if path.endswith("/attempts/1/jobs?per_page=100"):
            assert field == "jobs"
            return [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]
        if "environment=production-" + JOB in path:
            return [deepcopy(self.record)]
        if "environment=slack-incidents" in path:
            return deepcopy(self.checkpoints)
        if "/deployments/17/statuses?" in path:
            return [{"id": 18, "description": "verified", "state": "success"},
                    {"id": 16, "description": "started", "state": "in_progress"}]
        if "/statuses?" in path:
            return [{"id": 200, "description": "applied", "state": "success"}]
        raise AssertionError(path)


def cold(ledger, api, directory):
    records = []
    for index, state in enumerate(ledger.states.values(), 100):
        record = signed_record()
        record["id"] = index
        record["payload"]["state"] = json.loads(json.dumps(state))
        records.append(record)
    api.checkpoints = json.loads(json.dumps(records))
    loaded = incidents.Ledger(api, {}, directory).load()
    return PersistedLedger(loaded)


def verifier(value, api):
    expected = value["proof"]["fact"]["execution"]
    reader = Mock()
    reader.execution.return_value = {"name": expected["name"], "uid": expected["uid"],
        "createTime": expected["create_time"], "startTime": expected["start_time"], "completionTime": expected["completion_time"],
        "template": {"containers": [{"image": image} for image in expected["images"]]},
        "conditions": [{"type": "Completed", "state": "CONDITION_SUCCEEDED"}]}
    return incidents.reverify_saved_cloud_success(reader, value, github_api=api)


def ingest(ledger, api, value, now):
    return incidents.prepare_cloud(ledger, IDENTITY, proposal(value), shadow=False, now=now, github_api=api,
                                   recovery_verifier=lambda item: verifier(item, api))


def test_explicit_completion_contract_preserves_existing_observer_behavior():
    contracts = deployments.AUXILIARY_CLOUD_JOBS
    assert contracts["wdpa-execution-observer"]["completion_evidence"] == "execution"
    assert contracts["wdpa-execution-observer"]["delivery_route"] == "incident"
    assert contracts[JOB] == {"deployment_target": JOB,
                              "resource": "module.monitoring_slack_job[0].google_cloud_run_v2_job.this",
                              "completion_evidence": "normal_live_cycle", "delivery_route": "logs",
                              "control_workflows": ("monitoring-slack-relay-receipt.yml",)}


def test_persisted_logs_only_failure_still_requires_full_live_completion_for_internal_recovery(monkeypatch, tmp_path):
    monkeypatch.setattr(emissions, "verify", lambda *_args, **_kwargs: None)  # External signing authority only.
    api, ledger, slack = Authority(), PersistedLedger(), FakeSlack()
    failure = fact()
    records, _ = ingest(ledger, api, failure, at(2))
    incidents.deliver(ledger, records, slack)
    scope = adapter.source_scope(failure)
    original = deepcopy(ledger.states[scope]["incident"])
    for name in ("api", "capability", "shadow"):
        ledger = cold(ledger, api, tmp_path)
        green = fact(name, success=True)
        records, evaluation = ingest(ledger, api, green, at(5))
        incidents.deliver(ledger, records, slack)
        assert ledger.states[scope]["incident"]["resolution"] is None
        assert evaluation["provenance_complete"] is True
        assert evaluation["cloud_counts"]["verified_observer_health"] == 0
        assert evaluation["cloud_counts"]["verified_asset_publications"] == 0
        assert adapter.operational(evaluation["observations"][0]).reason.value == "unchanged"
        bundle = cloud.evaluated_collection(proposal(green), evaluation)
        assert incidents.verify_cloud_evaluation(bundle["checkpoint"], evaluation, ledger.states,
                                                shadow=False, worker=evaluation["worker"])
    ledger = cold(ledger, api, tmp_path)
    completed = fact("normal", success=True, typed=True)
    records, evaluation = ingest(ledger, api, completed, at(5))
    incidents.deliver(ledger, records, slack)
    ledger = cold(ledger, api, tmp_path)
    current = ledger.states[scope]["incident"]
    assert current["id"] == original["id"] and current["posts"] == original["posts"]
    assert current["failures"] == original["failures"] and current["resolution"] is not None
    assert slack.calls == []
    assert evaluation["cloud_counts"]["verified_observer_health"] == 1
    assert evaluation["cloud_counts"]["verified_asset_publications"] == 0
    assert current["resolution"]["kind"] == "job" and adapter.domain(completed) is None
    deployment_failure = {"scope": "deployment:" + JOB}
    assert not adapter.covers(deployment_failure, current["resolution"])


@pytest.mark.parametrize("forgery", ["artifact", "revision", "job", "subject", "before-start", "after-completion"])
def test_forged_or_out_of_interval_completion_cannot_recover(monkeypatch, forgery):
    monkeypatch.setattr(emissions, "verify", lambda *_args, **_kwargs: None)
    api = Authority()
    failed, complete = fact(), fact("normal", success=True, typed=True)
    proof = incidents.verified_auxiliary_artifact(api, complete)
    prior = adapter.normalize(failed, correlation=incidents.verified_auxiliary_artifact(api, failed))
    if forgery == "artifact":
        proof["artifact"] = IMAGE.replace("a" * 64, "c" * 64)
        with pytest.raises(ValueError):
            adapter.normalize(complete, correlation=proof)
    elif forgery == "revision":
        proof["revision"] = "f" * 40
        with pytest.raises(incidents.IncidentError):
            incidents.verified_auxiliary_artifact(api, complete, expected=proof)
    elif forgery == "job":
        proof["execution"] = proof["execution"].replace("/jobs/" + JOB + "/", "/jobs/foreign-job/")
        with pytest.raises(ValueError):
            adapter.normalize(complete, correlation=proof)
    else:
        if forgery == "subject":
            complete["fact"]["subject"] = {"asset_slug": "a-dataset", "release": "2026-10-21"}
        else:
            complete["fact"]["observed_at"] = at(-2) if forgery == "before-start" else at(5)
        audit = adapter.normalize(complete, correlation=proof)
        assert not adapter.observational_success(complete, proof)
        assert not adapter.covers(prior, audit)


def test_unsigned_image_and_absent_exact_resource_do_not_confer_health(monkeypatch):
    api, complete = Authority(), fact("normal", success=True, typed=True)
    monkeypatch.setattr(emissions, "verify", Mock(side_effect=emissions.EmissionError("offline signature denied")))
    with pytest.raises(deployments.DeploymentError):
        incidents.verified_auxiliary_artifact(api, complete)
    api.record["payload"]["targets"] = []
    assert incidents.verified_auxiliary_artifact(api, complete) is None
    assert adapter.provenance_gap(complete)
