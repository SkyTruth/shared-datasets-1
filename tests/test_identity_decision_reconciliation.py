"""Accepted-head authority, legacy acknowledgement and fixed event correlation."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ingestion.common import decision_events as wire
from scripts import reconcile_identity_decisions as command
from scripts import ci_contract
from services.monitoring_slack import decisions, run as relay
from test_monitoring_slack import Bucket, decision_event, proof
from workflow_helpers import load_workflow

IDENTITY = {"team": "T12345678", "user": "U12345678", "channel": "C12345678"}


@pytest.mark.parametrize("ready", [True, False])
def test_actual_cli_transport_preserves_the_verifier_pagination_contract(monkeypatch, capsys, ready):
    sha = "a" * 40
    repository = {"id": 42, "full_name": command.REPOSITORY}
    prefix = f"repos/{command.REPOSITORY}"
    responses = {
        prefix: repository,
        prefix + "/actions/workflows/ci.yml": {"id": 9, "path": ".github/workflows/ci.yml"},
        prefix + "/actions/runs/7/attempts/1": {
            "id": 7, "run_attempt": 1, "workflow_id": 9, "path": ".github/workflows/ci.yml",
            "event": "push", "head_branch": "main", "head_sha": sha,
            "repository": repository, "head_repository": repository},
        prefix + "/actions/runs/7/attempts/1/jobs?per_page=100": [{"total_count": 1, "jobs": [{
            "name": "ci-ready", "status": "completed", "conclusion": "success" if ready else "failure"}]}],
        prefix + "/git/ref/heads/main": {"object": {"sha": sha}},
    }
    calls = []
    def read(path, *, paginate=False):
        calls.append((path, paginate))
        return json.dumps(responses[path]).encode()
    # Keep real pagination and verifier entrypoints; replace only HTTP reads.
    monkeypatch.setattr(command.auth, "read", read)
    monkeypatch.setattr(command.subprocess, "check_output", lambda *args, **kwargs: sha + "\n")
    monkeypatch.setenv("GITHUB_REPOSITORY", command.REPOSITORY)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_WORKFLOW_REF", f"{command.REPOSITORY}/.github/workflows/ci.yml@refs/heads/main")
    args = ["--executor-sha", sha, "--source-run-id", "7", "--source-run-attempt", "1", "--check-only"]
    if ready:
        assert command.main(args) == 0
        assert "Tested main decision executor verified" in capsys.readouterr().out
    else:
        with pytest.raises(command.deployment.DeploymentError, match="ci-ready"):
            command.main(args)
    assert (prefix + "/actions/runs/7/attempts/1/jobs?per_page=100", True) in calls


@pytest.fixture
def authority(monkeypatch):
    source = decision_event()
    record = proof(source, source["cases"][0])
    decision = record["decision"]
    pr = {"number": 365, "state": "closed", "merged": True, "merge_commit_sha": "b" * 40,
          "user": {"login": "jonaraphael"}, "base": {"ref": "main", "repo": {"full_name": command.REPOSITORY}},
          "head": {"sha": "a" * 40, "repo": {"full_name": command.REPOSITORY}}}
    document = {"schema_version": 1, "asset_slug": source["asset_slug"], "decisions": [decision]}
    reviews = []
    api = Mock()
    api.get.side_effect = lambda path: (pr if "/pulls/" in path else {"object": {"sha": "c" * 40}})
    api.pages.side_effect = lambda path: (reviews if "/reviews?" in path else [{"filename": "catalog/feature-identity-resolutions/wdpa-marine.json", "status": "modified"}])
    monkeypatch.setattr(command.auth, "git_file", lambda *args: (json.dumps(document).encode(), "d" * 40))
    monkeypatch.setattr(command.deployment, "git_ancestor", lambda *args: True)
    return api, pr, document, reviews, source, decision


def test_curator_acceptance_verifies_actual_merged_head_and_runtime_legal_choice(authority):
    api, pr, document, reviews, source, decision = authority
    result = command.accepted_proof(api, "c" * 40, source["asset_slug"], decision, {})
    assert result["acceptance"] == {"kind": "self_authored_merge", "reviewed_commit": pr["head"]["sha"]}
    assert result["decision"]["previous_feature_id_for_key"] == "15378"
    # Head bytes, not a PR body assertion, are the decision authority.
    document["decisions"] = [{**decision, "rationale": "changed after review"}]
    with pytest.raises(ValueError, match="accepted head"):
        command.accepted_proof(api, "c" * 40, source["asset_slug"], decision, {})


@pytest.mark.parametrize("change", ["open", "fork", "no-review", "wrong-head-review", "illegal-merge", "outside-history"])
def test_unaccepted_or_unsafe_choice_never_becomes_notification_authority(authority, monkeypatch, change):
    api, pr, document, reviews, source, decision = authority
    if change == "open":
        pr["merged"] = False
    elif change == "fork":
        pr["head"]["repo"]["full_name"] = "other/fork"
    elif change in {"no-review", "wrong-head-review"}:
        pr["user"]["login"] = "contributor"
        if change == "wrong-head-review":
            reviews.append({"id": 123, "pull_request_url": f"https://api.github.com/repos/{command.REPOSITORY}/pulls/365",
                "user": {"login": "jonaraphael"}, "state": "APPROVED", "commit_id": "e" * 40,
                "submitted_at": "2026-11-02T19:00:00Z"})
    elif change == "illegal-merge":
        decision.update(action="reuse_previous_feature_id", reuse_feature_id="11347")
    else:
        monkeypatch.setattr(command.deployment, "git_ancestor", lambda *args: False)
    with pytest.raises((ValueError, command.auth.Error)):
        command.accepted_proof(api, "c" * 40, source["asset_slug"], decision, {})


def test_legacy_adoption_preserves_confirmed_reply_and_finishes_interrupted_link(authority):
    api, pr, document, reviews, source, decision = authority
    approved = command.accepted_proof(api, "c" * 40, source["asset_slug"], decision, {})
    store = relay.Store(Bucket())
    case_id = decisions.proof_key(approved)
    store.write("identity/approvals.json", {"schema": decisions.APPROVAL_SCHEMA, "revision": "c" * 40,
                                          "known": {}, "records": {case_id: approved}}, 0)
    store.save("0.review", {"schema": relay.SCHEMA, "identity": IDENTITY, "ts": "1234567890.000003", "closed": False}, 0)
    adoption = {"schema": "identity-decision-adoption-v1", "decision_pr": 365, "event": source,
                "channel": IDENTITY["channel"], "parent_ts": "1234567890.000001", "reply_ts": "1234567890.000002",
                "monitoring": [{"incident_id": "0.review", "parent_ts": "1234567890.000003"}]}
    alert = {"name": f"projects/{wire.PROJECT}/alerts/0.review", "policy": {
        "displayName": decisions.POLICY, "userLabels": {"kind": "decision-required"}},
        "resource": {"type": "cloud_run_job", "labels": {"project_id": wire.PROJECT, "location": "us-central1", "job_name": source["job"]}}}
    slack = Mock()
    original_write = store.write
    def fail_link(path, value, generation):
        if path.startswith("identity/links/"):
            raise OSError("interrupted link")
        return original_write(path, value, generation)
    store.write = fail_link
    with pytest.raises(OSError):
        command.install_adoption(adoption, store, slack, IDENTITY, lambda _: alert)
    store.write = original_write
    for _ in range(2):
        command.install_adoption(adoption, store, slack, IDENTITY, lambda _: alert)
        command.reconcile_all(store, slack, IDENTITY)
    slack.post.assert_not_called()
    slack.update.assert_called_once()
    assert slack.update.call_args.args[0] == "1234567890.000003"
    receipt, _ = store.load("0.review")
    assert receipt["schema"] == relay.DECISION_SCHEMA and not receipt["closed"]
    # Another incident's timestamp cannot be substituted by a migration document.
    wrong = {**adoption, "monitoring": [{"incident_id": "0.review", "parent_ts": "1234567890.000004"}]}
    with pytest.raises(ValueError, match="parent differs"):
        command.install_adoption(wrong, store, slack, IDENTITY, lambda _: alert)


def test_changed_main_or_acceptance_stops_before_index_write(authority, tmp_path, monkeypatch):
    api, pr, document, reviews, source, decision = authority
    path = tmp_path / "catalog/feature-identity-resolutions/wdpa-marine.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(document))
    store = relay.Store(Bucket())
    store.write("identity/approvals.json", {"schema": decisions.APPROVAL_SCHEMA, "revision": "b" * 40, "known": {}, "records": {}}, 0)
    before = copy.deepcopy(store.bucket.objects)
    api.get.side_effect = lambda path: pr if "/pulls/" in path else {"object": {"sha": "d" * 40}}
    with pytest.raises(ValueError, match="superseded"):
        command.sync(SimpleNamespace(executor_sha="c" * 40), api, tmp_path, store, Mock(), IDENTITY, Mock())
    assert store.bucket.objects == before


def test_late_event_uses_indexed_review_and_rejects_changed_capture():
    source = decision_event()
    store = relay.Store(Bucket())
    record = proof(source, source["cases"][0])
    store.write("identity/approvals.json", {"schema": decisions.APPROVAL_SCHEMA, "revision": "b" * 40,
                                          "known": {}, "records": {decisions.proof_key(record): record}}, 0)
    slack = Mock()
    slack.post.side_effect = ["1234567890.000001", "1234567890.000002"]
    decisions.deliver(source, store, slack, IDENTITY)
    assert slack.post.call_count == 2
    changed = copy.deepcopy(source)
    changed["cases"][0]["new_geometry_hash"] = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match="changed after capture"):
        decisions.deliver(changed, store, slack, IDENTITY)
    assert slack.post.call_count == 2


def test_changed_decision_history_never_repeats_an_already_acknowledged_choice():
    source = decision_event()
    first = source["cases"][0]
    second = {**first, "new_identity_key": ["second"]}
    source = wire.event({**source, "cases": [first, second]})
    current = proof(source, first)
    first_key = decisions.proof_key(current)
    new_records = {first_key: current}
    old = copy.deepcopy(current)
    for index in range(100):
        old["decision"]["rationale"] = f"Superseded rationale {index}"
        if wire.digest({first_key: old}) > wire.digest(new_records):
            break
    else:
        raise AssertionError("test must reproduce receipt key order, not decision chronology")
    store = relay.Store(Bucket())
    record = {"schema": decisions.EVENT_SCHEMA, "event": source, "identity": IDENTITY,
              "parent": {"owner": "bot", "ts": "1234567890.000001"}, "parent_view": None, "replies": {}}
    for item, ts in (({first_key: old}, "1234567890.000002"), (new_records, "1234567890.000003")):
        record["replies"][wire.digest(item)] = {"records": item, "owner": "bot", "ts": ts}
    decisions.save(store, wire.event_key(source), record, 0)
    second_proof = proof(source, second)
    records = {**new_records, decisions.proof_key(second_proof): second_proof}
    store.write("identity/approvals.json", {"schema": decisions.APPROVAL_SCHEMA, "revision": "b" * 40,
                                          "known": {}, "records": records}, 0)
    slack = Mock()
    slack.post.return_value = "1234567890.000004"
    for _ in range(2):
        decisions.reconcile(store, wire.event_key(source), slack, IDENTITY)
    slack.post.assert_called_once()
    assert "second" in slack.post.call_args.args[0]["text"]
    assert "555729583" not in slack.post.call_args.args[0]["text"]


@pytest.mark.parametrize("overwritten", [False, True])
def test_standalone_request_is_preserved_or_restored_without_reposting_decision(overwritten):
    source = decision_event()
    accepted = proof(source, source["cases"][0])
    records = {decisions.proof_key(accepted): accepted}
    original = decisions.primary(source)
    old_summary = {"text": ":white_check_mark: Decision recorded: wdpa-marine release 2026-11-01\n"
                   "The reviewed identity decisions are recorded in this thread.", "blocks": []}
    store = relay.Store(Bucket())
    journal = {"schema": decisions.EVENT_SCHEMA, "event": source, "identity": IDENTITY,
        "parent": {"owner": "bot", "ts": "1234567890.000001"},
        "parent_view": wire.digest(old_summary if overwritten else original),
        "replies": {wire.digest(records): {"records": records, "owner": "bot", "ts": "1234567890.000002"}}}
    decisions.save(store, wire.event_key(source), journal, 0)
    store.write("identity/approvals.json", {"schema": decisions.APPROVAL_SCHEMA,
        "revision": "b" * 40, "known": {}, "records": records}, 0)
    slack = Mock()
    for _ in range(2):
        decisions.reconcile(store, wire.event_key(source), slack, IDENTITY)
    slack.post.assert_not_called()
    if overwritten:
        slack.update.assert_called_once_with(journal["parent"]["ts"], original)
    else:
        slack.update.assert_not_called()
    persisted, _ = decisions.load(store, wire.event_key(source), IDENTITY)
    assert persisted["event"] == source and persisted["replies"] == journal["replies"]


def test_foreign_producers_and_local_diagnostics_cannot_post():
    source = decision_event()
    session = Mock()
    assert wire.publish(asset_slug=source["asset_slug"], release=source["release"], ambiguities=source["cases"], env={}, session=session) is False
    with pytest.raises(ValueError, match="foreign decision producer"):
        wire.publish(asset_slug=source["asset_slug"], release=source["release"], ambiguities=source["cases"],
                     env={"CLOUD_RUN_JOB": "eamlis-monthly", "CLOUD_RUN_EXECUTION": "eamlis-monthly-abcde"}, session=session)
    session.post.assert_not_called()


def test_workflow_waits_for_transport_and_existing_review_authority():
    workflow = load_workflow(Path(".github/workflows/identity-decision-reconcile.yml"))
    assert workflow["concurrency"] == {"group": "identity-decision-reconciliation", "queue": "max", "cancel-in-progress": False}
    job = workflow["jobs"]["reconcile"]
    assert job["environment"] == "shared-datasets-production"
    assert "github.actor == 'jonaraphael'" in job["if"]
    ci = load_workflow(Path(".github/workflows/ci.yml"))
    for name in ("mpatlas", "wdpa", "sea-ice", "eamlis", "identity-decision-reconcile"):
        assert "cron-alert-policy" in ci["jobs"][name]["needs"]
        assert "needs.cron-alert-policy.result == 'success'" in ci["jobs"][name]["if"]
    assert "cron_alert_policy" in ci_contract.select_deployments(["ingestion/common/decision_events.py"])
    terraform = Path("terraform/envs/prod/monitoring_slack.tf").read_text()
    assert 'name  = "IDENTITY_DECISION_PROTOCOL"' in terraform
    assert '"roles/pubsub.publisher"' in terraform
    reader = Path("terraform/envs/prod/monitoring_alert_policy_iam.tf").read_text().split('"monitoring_incident_reader"', 1)[1].split('\nresource ', 1)[0]
    assert 'permissions = ["monitoring.alerts.get", "logging.logEntries.list"]' in reader
