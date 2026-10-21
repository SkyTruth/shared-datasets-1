"""The scheduled transaction never turns partial persistence into coverage."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, Mock
import json

import pytest

from scripts import operational_reconcile as worker


CONTEXT = {"execution_run_id": 50, "execution_run_attempt": 1, "source_sha": "a" * 40,
           "policy_sha256": "b" * 64, "collector_contract_sha256": "c" * 64}
SHADOW = {"mode": "shadow", "policy_deployment_id": None, "policy_status_id": None, "config_sha256": "d" * 64}


@pytest.fixture
def cycle(tmp_path, monkeypatch):
    bundle = {"checkpoint": {"test": "source facts", "last_cycle": {"completed_at": "2026-10-21T12:05:00.000000Z"}}, "evaluation": {
        "complete": True, "provenance_complete": True, **{key: 0 for key in worker.TRANSPORT_FIELDS},
        "collection_completed_at": "2026-10-21T12:05:00.000000Z", "worker": {
            "id": 50, "attempt": 1, "head_sha": CONTEXT["source_sha"], "evaluated_at": "2026-10-21T12:05:00Z"}}}
    value = {"schema": worker.PROPOSAL_SCHEMA, "context": deepcopy(CONTEXT), "activation": deepcopy(SHADOW),
             "mode": "shadow", "previous_cycle_id": 10, "bundle": bundle}
    (tmp_path / worker.PROPOSAL).write_text(json.dumps(value))
    (tmp_path / worker.DELIVERY_CONTEXT).write_text(json.dumps({key: value[key] for key in ("context", "activation")}))
    ledger = SimpleNamespace(directory=tmp_path, run={"id": 50, "run_attempt": 1, "head_sha": CONTEXT["source_sha"]},
                             load=Mock(return_value={}), api=Mock(), verify=Mock())
    ledger.api.pages.return_value = []
    cycles = SimpleNamespace(load=Mock(return_value={"manifest_id": 10}), context=Mock(return_value=deepcopy(CONTEXT)),
                             save=Mock(return_value={"manifest_id": 11}),
                             local_cycle_id=Mock(return_value=11), verify_cycle_id=Mock())
    monkeypatch.setattr(worker.incidents, "verify_cloud_evaluation", Mock(return_value=True))
    monkeypatch.setattr(worker.incidents, "needs_parent_delivery", Mock(return_value=False))
    monkeypatch.setattr(worker, "github_escalation", Mock(return_value=False))
    monkeypatch.setattr(worker, "routing", Mock(return_value=deepcopy(SHADOW)))
    monkeypatch.setattr(worker, "utc_now", lambda: worker.datetime(2026, 10, 21, 12, 5, tzinfo=worker.timezone.utc))
    return value, ledger, cycles


def test_finalization_requires_fresh_signed_incident_state_before_advancing(cycle):
    value, ledger, cycles = cycle
    ledger.load.side_effect = RuntimeError("signature unavailable")
    with pytest.raises(RuntimeError, match="signature unavailable"):
        worker.finalize_cycle(ledger, cycles)
    cycles.save.assert_not_called()
    ledger.load.side_effect = None
    assert worker.finalize_cycle(ledger, cycles) == {"manifest_id": 11}
    cycles.save.assert_called_once_with(value["bundle"]["checkpoint"], value["bundle"]["evaluation"],
                                       mode="shadow", previous_cycle_id=10, before_commit=ANY)


def test_reconciliation_finish_clock_is_captured_after_all_verified_reads(cycle):
    _, ledger, cycles = cycle
    finished = worker.datetime(2026, 10, 21, 12, 35, tzinfo=worker.timezone.utc)

    def clock():
        assert ledger.load.called and cycles.load.called and ledger.api.pages.called
        assert not cycles.save.called
        return finished

    worker.finalize_cycle(ledger, cycles, clock=clock)
    checkpoint = cycles.save.call_args.args[0]
    assert checkpoint["last_cycle"]["completed_at"] == "2026-10-21T12:35:00.000000Z"
    final = json.loads((ledger.directory / worker.PROPOSAL).read_text())
    assert final["bundle"]["evaluation"]["collection_completed_at"] == "2026-10-21T12:05:00.000000Z"


@pytest.mark.parametrize("evaluated_at", ["2026-10-21T12:04:59Z", "2026-10-21T12:05:01Z"])
def test_policy_evaluation_cannot_precede_collection_or_postdate_reconciliation(cycle, evaluated_at):
    value, ledger, cycles = cycle
    value["bundle"]["evaluation"]["worker"]["evaluated_at"] = evaluated_at
    (ledger.directory / worker.PROPOSAL).write_text(json.dumps(value))
    with pytest.raises(worker.incidents.IncidentError, match="outside the verified"):
        worker.finalize_cycle(ledger, cycles)
    cycles.save.assert_not_called()


@pytest.mark.parametrize("change", ["predecessor", "public_cloud_post", "wrong_mode", "nonzero_transport"])
def test_shadow_cannot_advance_after_delivery_or_predecessor_conflict(cycle, change):
    value, ledger, cycles = cycle
    if change == "predecessor":
        cycles.load.return_value = {"manifest_id": 12}
    elif change == "public_cloud_post":
        ledger.load.return_value = {"runtime:x": {"scope": "cloud:runtime:x", "incident": {"failures": [], "posts": {"parent": {"state": "claimed"}}}}}
    elif change == "wrong_mode":
        value["mode"] = "unified"
    else:
        value["bundle"]["evaluation"]["public_delivery_count"] = 1
    (ledger.directory / worker.PROPOSAL).write_text(json.dumps(value))
    with pytest.raises(worker.incidents.IncidentError):
        worker.finalize_cycle(ledger, cycles)
    cycles.save.assert_not_called()


def test_heartbeat_reloads_signed_cycle_and_preserves_original_proposal(cycle, monkeypatch):
    value, ledger, cycles = cycle
    cycles.verify_cycle_id.return_value = {"manifest": {**CONTEXT, "mode": "shadow", "previous_cycle_id": 10},
                                          **deepcopy(value["bundle"])}
    cloud_reader = SimpleNamespace(heartbeat=Mock())
    confirm = Mock(return_value=True)
    monkeypatch.setattr(worker.cloud, "confirm_heartbeat", confirm)
    assert worker.verify_heartbeat(ledger, cycles, cloud_reader)
    cycles.verify_cycle_id.assert_called_once_with(11)
    ledger.load.assert_called_once()
    confirm.assert_called_once_with(value["bundle"], value["bundle"], delivery_verified=True,
                                    heartbeat=cloud_reader.heartbeat)


@pytest.mark.parametrize("transition_at", ["before_finalize", "during_finalize_reads", "before_heartbeat", "during_heartbeat_reads"])
def test_native_receipt_transition_cannot_commit_coverage_or_emit_shadow_health(cycle, monkeypatch, transition_at):
    from scripts import operational_delivery_activation as selector
    from test_operational_delivery_activation import context
    from test_cloud_operational_observations import reader as empty_reader
    from test_operational_cutover import rollout

    value, ledger, cycles = cycle
    root, api, original_source, records, payload, statuses = context(ledger.directory, mode="native", prior_mode="native")
    monkeypatch.setattr(selector.deployments, "verify_record", Mock(return_value=payload))
    monkeypatch.setattr(selector.deployments, "verified_statuses", Mock(return_value=statuses))

    def source(root, revision, path):
        if revision == "c" * 40 and path == selector.cutover.CONFIG:
            return (json.dumps(rollout("unified"), sort_keys=True) + "\n").encode()
        return original_source(root, revision, path)

    monkeypatch.setattr(worker, "routing", lambda *_: selector.activation(api, "SkyTruth/shared-datasets-1", root, CONTEXT["source_sha"],
                                                                        read_source=source, ancestor=lambda *_: True))
    value["activation"] = worker.write_context(ledger, cycles)["activation"]
    now = worker.utc_now()
    value["bundle"] = worker.cloud.evaluated_collection(worker.cloud.collect(None, empty_reader(), now=now), value["bundle"]["evaluation"])
    (ledger.directory / worker.PROPOSAL).write_text(json.dumps(value))
    cloud_reader = SimpleNamespace(heartbeat=Mock())

    def transition():
        records.append({"id": 101, "sha": "c" * 40})

    if transition_at.startswith("before_heartbeat") or transition_at == "during_heartbeat_reads":
        worker.finalize_cycle(ledger, cycles)
        value = json.loads((ledger.directory / worker.PROPOSAL).read_text())
        durable = {"manifest": {**CONTEXT, "mode": "shadow", "previous_cycle_id": 10}, **value["bundle"]}
        cycles.verify_cycle_id.return_value = durable
        if transition_at == "during_heartbeat_reads":
            def signed_read(_):
                transition()
                return durable
            cycles.verify_cycle_id.side_effect = signed_read
        else:
            transition()
        def action():
            worker.verify_heartbeat(ledger, cycles, cloud_reader)
    else:
        if transition_at == "during_finalize_reads":
            def signed_read():
                transition()
                return {}
            ledger.load.side_effect = signed_read
        else:
            transition()
        def action():
            worker.finalize_cycle(ledger, cycles)
    with pytest.raises(ValueError, match="native worker.*unified|activation changed"):
        action()
    if "finalize" in transition_at:
        cycles.save.assert_not_called()
    cloud_reader.heartbeat.assert_not_called()


def test_foreign_worker_or_failed_verification_cannot_emit_heartbeat(cycle):
    value, ledger, cycles = cycle
    cycles.verify_cycle_id.return_value = {"manifest": {**CONTEXT, "execution_run_id": 49, "mode": "shadow", "previous_cycle_id": 10},
                                          **value["bundle"]}
    cloud_reader = SimpleNamespace(heartbeat=Mock())
    with pytest.raises(worker.incidents.IncidentError, match="another worker"):
        worker.verify_heartbeat(ledger, cycles, cloud_reader)
    cloud_reader.heartbeat.assert_not_called()
    cycles.verify_cycle_id.side_effect = ValueError("unsigned manifest")
    with pytest.raises(ValueError, match="unsigned manifest"):
        worker.verify_heartbeat(ledger, cycles, cloud_reader)
    cloud_reader.heartbeat.assert_not_called()


def test_noncloud_commands_preserve_existing_cli_serialization(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("SHARED_DATASETS_CLOUD_OBSERVATIONS_ENABLED", "true")
    monkeypatch.setattr(worker.sys, "argv", ["operational_reconcile.py", "prepare", "--work-dir", str(tmp_path)])
    existing = Mock()
    monkeypatch.setattr(worker.incidents, "main", existing)
    context = Mock()
    monkeypatch.setattr(worker, "write_context", context)
    monkeypatch.setattr(worker.incidents, "worker_context", Mock(return_value={"id": 50, "run_attempt": 1}))
    worker.main()
    existing.assert_called_once_with()
    context.assert_called_once()


@pytest.mark.parametrize("field", list(CONTEXT))
def test_proposal_context_cannot_change_before_finalization_or_heartbeat(cycle, field):
    value, ledger, cycles = cycle
    value["context"][field] = 999 if field in {"execution_run_id", "execution_run_attempt"} else "f" * len(value["context"][field])
    (ledger.directory / worker.PROPOSAL).write_text(json.dumps(value))
    cloud_reader = SimpleNamespace(heartbeat=Mock())
    for action in (lambda: worker.finalize_cycle(ledger, cycles), lambda: worker.verify_heartbeat(ledger, cycles, cloud_reader)):
        with pytest.raises(worker.incidents.IncidentError, match="another worker, source or protocol"):
            action()
    cycles.save.assert_not_called()
    cloud_reader.heartbeat.assert_not_called()


@pytest.mark.parametrize("field", [*CONTEXT, "mode", "previous_cycle_id"])
def test_verified_manifest_must_match_current_source_and_exact_proposal(cycle, field):
    value, ledger, cycles = cycle
    manifest = {**CONTEXT, "mode": "shadow", "previous_cycle_id": 10}
    manifest[field] = "unified" if field == "mode" else 999
    cycles.verify_cycle_id.return_value = {"manifest": manifest, **value["bundle"]}
    cloud_reader = SimpleNamespace(heartbeat=Mock())
    with pytest.raises(worker.incidents.IncidentError, match="another worker, source, protocol or mode"):
        worker.verify_heartbeat(ledger, cycles, cloud_reader)
    cloud_reader.heartbeat.assert_not_called()


def test_preexisting_uncertain_delivery_prevents_cursor_and_heartbeat(cycle):
    _, ledger, cycles = cycle
    ledger.load.return_value = {"workflow:ci": {"scope": "workflow:ci", "incident": {"failures": [], "posts": {"parent": {"state": "claimed"}}}}}
    cloud_reader = SimpleNamespace(heartbeat=Mock())
    for action in (lambda: worker.finalize_cycle(ledger, cycles), lambda: worker.verify_heartbeat(ledger, cycles, cloud_reader)):
        with pytest.raises(worker.incidents.IncidentError, match="unconfirmed Slack delivery"):
            action()
    cycles.save.assert_not_called()
    cycles.verify_cycle_id.assert_not_called()
    cloud_reader.heartbeat.assert_not_called()


def test_failed_prepare_retains_exact_partial_delivery_plan_without_a_cycle_proposal(cycle, monkeypatch):
    value, ledger, cycles = cycle
    cycles.load.return_value = {"manifest_id": 10, "checkpoint": {"original": "checkpoint"}}
    candidate = {"last_cycle": {"completed_at": "2026-10-21T12:05:00Z"}}
    clock, reader = Mock(), Mock()
    collect = Mock(return_value=candidate)
    monkeypatch.setattr(worker.cloud, "collect", collect)
    monkeypatch.setattr(worker.incidents, "replay_observations", Mock(return_value=[]))
    record = {"id": 77, "payload": {"execution_run_id": 50, "execution_run_attempt": 1}}

    def fail_after_claim(actual_ledger, identity, proposal, *, shadow, now, on_saved, github_api, github_observations, recovery_verifier, clock, translation_reader):
        assert actual_ledger is ledger and identity == {"bot": "B1"} and proposal is candidate
        assert shadow is True and now == candidate["last_cycle"]["completed_at"]
        assert github_api is ledger.api and callable(recovery_verifier)
        assert github_observations == []
        assert callable(translation_reader)
        on_saved(record)
        raise RuntimeError("next claim persistence failed")

    monkeypatch.setattr(worker.incidents, "prepare_cloud", fail_after_claim, raising=False)
    with pytest.raises(RuntimeError, match="next claim persistence failed"):
        worker.prepare_cycle(ledger, cycles, {"bot": "B1"}, reader, now="scan-clock", clock=clock)
    assert json.loads((ledger.directory / "prepared.json").read_text()) == [record]
    assert not (ledger.directory / worker.PROPOSAL).exists()
    collect.assert_called_once_with({"original": "checkpoint"}, reader, now="scan-clock", clock=clock)
    cycles.save.assert_not_called()


def test_failure_before_collection_cannot_reuse_a_prior_local_proposal_or_plan(cycle):
    _, ledger, cycles = cycle
    (ledger.directory / "prepared.json").write_text('[{"id":9}]')
    cycles.load.side_effect = ValueError("unsigned latest checkpoint")
    with pytest.raises(ValueError, match="unsigned latest checkpoint"):
        worker.prepare_cycle(ledger, cycles, {}, Mock(), now="unused")
    assert json.loads((ledger.directory / "prepared.json").read_text()) == []
    assert not (ledger.directory / worker.PROPOSAL).exists()
    cycles.save.assert_not_called()


def transport_state(*, parent="claimed", view=None, permalink="", scope="cloud:job:test"):
    post = {"state": parent, "ts": "123.456" if parent == "delivered" else None}
    return {"scope": scope, "recoveries": {}, "incident": {"id": "SD-TEST", "resolution": None, "failures": [{"source": "cloud"}],
            "posts": {"parent": post}, "parent_view_sha256": view, "permalink": permalink}}


def transport_record(identifier, state):
    return {"id": identifier, "sha": CONTEXT["source_sha"], "ref": CONTEXT["source_sha"],
            "payload": {"schema": worker.incidents.SCHEMA, "execution_run_id": 50, "execution_run_attempt": 1,
                        "state": deepcopy(state)}}


def records_for(ledger, records):
    ledger.api.pages.return_value = deepcopy(records)
    ledger.api.get.side_effect = lambda path: deepcopy(next(record for record in records if record["id"] == int(path.rsplit("/", 1)[-1])))
    ledger.verify.side_effect = lambda record: deepcopy(json.loads(record["payload"])["state"] if isinstance(record["payload"], str)
                                                       else record["payload"]["state"])


def test_actual_public_transport_count_uses_signed_transitions_not_local_plan(cycle):
    _, ledger, _ = cycle
    claim = transport_state()
    delivered = transport_state(parent="delivered", view="first")
    permalink = transport_state(parent="delivered", view="first", permalink="https://slack/parent")
    updated = transport_state(parent="delivered", view="updated", permalink="https://slack/parent")
    records = [transport_record(index, state) for index, state in enumerate((claim, delivered, permalink, updated), 10)]
    records[1]["payload"] = json.dumps(records[1]["payload"])
    records_for(ledger, records + [deepcopy(records[0])])
    (ledger.directory / "prepared.json").write_text("[]")
    assert worker.public_delivery_counts(ledger, {updated["scope"]: updated})["public_delivery_count"] == 2
    assert ledger.verify.call_count == 4


def test_existing_parent_uses_signed_prior_worker_as_baseline(cycle):
    _, ledger, _ = cycle
    baseline = transport_state(parent="delivered", view="first", permalink="https://slack/parent")
    old = transport_record(9, baseline)
    old["payload"]["execution_run_id"] = 49
    updated = transport_state(parent="delivered", view="updated", permalink="https://slack/parent")
    records_for(ledger, [old, transport_record(10, baseline), transport_record(11, updated)])
    assert worker.public_delivery_counts(ledger, {updated["scope"]: updated})["public_delivery_count"] == 1


def test_a_confirmed_parent_without_a_signed_claim_or_predecessor_is_not_zero(cycle):
    _, ledger, _ = cycle
    delivered = transport_state(parent="delivered", view="first")
    records_for(ledger, [transport_record(10, delivered)])
    with pytest.raises(worker.incidents.IncidentError, match="signed predecessor or claim"):
        worker.public_delivery_counts(ledger, {delivered["scope"]: delivered})


@pytest.mark.parametrize("change", ["wrong-source", "conflicting-duplicate", "signature", "pagination", "not-latest", "parent-changed"])
def test_invalid_current_worker_transport_evidence_cannot_finalize_count(cycle, change):
    _, ledger, _ = cycle
    claim = transport_state()
    delivered = transport_state(parent="delivered", view="first")
    states = {delivered["scope"]: delivered}
    records = [transport_record(10, claim), transport_record(11, delivered)]
    if change == "wrong-source":
        records[0]["sha"] = records[0]["ref"] = "e" * 40
    elif change == "conflicting-duplicate":
        records.append({**deepcopy(records[0]), "extra": "forged"})
    elif change == "not-latest":
        states = {claim["scope"]: claim}
    elif change == "parent-changed":
        updated = transport_state(parent="delivered", view="second")
        updated["incident"]["posts"]["parent"]["ts"] = "999.000"
        records.append(transport_record(12, updated))
    records_for(ledger, records)
    if change == "signature":
        ledger.verify.side_effect = ValueError("invalid signature")
    elif change == "pagination":
        ledger.api.pages.side_effect = ValueError("incomplete enumeration")
    with pytest.raises((worker.incidents.IncidentError, ValueError)):
        worker.public_delivery_counts(ledger, states)


@pytest.mark.parametrize("mode", ["shadow", "unified"])
def test_partial_prepare_delivery_requires_same_verified_activation(cycle, monkeypatch, mode):
    _, ledger, cycles = cycle
    selected = {**SHADOW, "mode": mode}
    if mode == "unified":
        selected.update(policy_deployment_id=77, policy_status_id=88)
    worker.routing.return_value = selected
    (ledger.directory / worker.DELIVERY_CONTEXT).write_text(json.dumps({"context": CONTEXT, "activation": selected}))
    (ledger.directory / worker.PROPOSAL).unlink()
    record = transport_record(10, transport_state())
    records_for(ledger, [record])
    (ledger.directory / "prepared.json").write_text(json.dumps([record]))
    deliver = Mock()
    monkeypatch.setattr(worker.incidents, "deliver", deliver)
    if mode == "shadow":
        with pytest.raises(worker.incidents.IncidentError, match="verified unified activation"):
            worker.deliver_messages(ledger, cycles, Mock())
        deliver.assert_not_called()
    else:
        slack = Mock()
        worker.deliver_messages(ledger, cycles, slack)
        assert deliver.call_args.args == (ledger, [record], slack)
        assert callable(deliver.call_args.kwargs["recovery_verifier"])


@pytest.mark.parametrize("event,enabled", [("workflow_run", "true"), ("schedule", "false")])
def test_observer_dependent_green_waits_for_authenticated_schedule_while_github_delivers(cycle, monkeypatch, event, enabled):
    _, ledger, cycles = cycle
    ledger.run["event"] = event
    monkeypatch.setenv("SHARED_DATASETS_CLOUD_OBSERVATIONS_ENABLED", enabled)
    green = {"source": "cloud", "proof": {"fact": {"kind": "translation_manifest"}}}
    state = transport_state(parent="delivered")
    state["incident"]["resolution"] = green
    state["recoveries"] = {state["scope"]: green}
    github = transport_state(scope="job:github")
    github["incident"]["failures"] = [{"source": "github"}]
    records = [transport_record(10, state), transport_record(11, github)]
    records_for(ledger, records)
    (ledger.directory / "prepared.json").write_text(json.dumps(records))
    deliver, credentials = Mock(), Mock(side_effect=AssertionError("No observer auth in this worker"))
    monkeypatch.setattr(worker.incidents, "deliver", deliver)
    monkeypatch.setattr(worker, "reader", credentials)
    worker.deliver_messages(ledger, cycles, Mock())
    credentials.assert_not_called()
    assert deliver.call_args.args[1] == [records[1]]


def test_delivery_auth_capability_uses_the_exact_protected_workflow_condition():
    import yaml
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/unattended-workflow-alert.yml").read_text())
    steps = next(job["steps"] for job in workflow["jobs"].values() if "steps" in job
                 and any(step.get("id") == "deliver" for step in job["steps"]))
    auth = next(step for step in steps if step.get("name") == "Authenticate read-only cloud observer")
    delivery = next(step for step in steps if step.get("id") == "deliver")
    assert auth["if"] == "${{ github.event_name == 'schedule' && vars.SHARED_DATASETS_CLOUD_OBSERVATIONS_ENABLED == 'true' }}"
    assert delivery["env"]["SHARED_DATASETS_CLOUD_OBSERVATIONS_ENABLED"] == "${{ vars.SHARED_DATASETS_CLOUD_OBSERVATIONS_ENABLED }}"


@pytest.mark.parametrize("command", ["finalize", "heartbeat"])
def test_new_debt_cannot_advance_a_green_checkpoint_or_emit_heartbeat(cycle, command):
    from test_translation_incident_recovery import ingest, maintenance, proof
    from test_slack_incident_policy import PersistedLedger
    from test_cloud_incident_adapter import proposal, at
    from test_slack_incidents import IDENTITY
    value, ledger, cycles = cycle
    local = PersistedLedger()
    worker.incidents.prepare_cloud(local, IDENTITY, proposal(maintenance()), shadow=True, now=at(10))
    _, evaluation, _ = ingest(local, proof(), shadow=True)
    ledger.load.return_value = local.states
    value["bundle"]["evaluation"]["observations"] = evaluation["observations"]
    (ledger.directory / worker.PROPOSAL).write_text(json.dumps(value))
    source = SimpleNamespace(translation_manifest=Mock(return_value=proof(generation=21, debt=2, created=at(2))), heartbeat=Mock())
    with pytest.raises(worker.incidents.IncidentError, match="current canonical"):
        if command == "finalize":
            worker.finalize_cycle(ledger, cycles, cloud_reader=source)
        else:
            worker.verify_heartbeat(ledger, cycles, source)
    cycles.save.assert_not_called()
    source.heartbeat.assert_not_called()


def test_changed_activation_blocks_delivery_even_when_claim_is_signed(cycle, monkeypatch):
    _, ledger, cycles = cycle
    worker.routing.return_value = {**SHADOW, "config_sha256": "f" * 64}
    record = transport_record(10, transport_state())
    records_for(ledger, [record])
    (ledger.directory / "prepared.json").write_text(json.dumps([record]))
    deliver = Mock()
    monkeypatch.setattr(worker.incidents, "deliver", deliver)
    with pytest.raises(worker.incidents.IncidentError, match="activation changed"):
        worker.deliver_messages(ledger, cycles, Mock())
    deliver.assert_not_called()


def test_native_mode_mixed_parent_requires_and_attributes_independent_github_escalation(cycle, monkeypatch):
    _, ledger, cycles = cycle
    claim = transport_state()
    claim["incident"]["failures"].append({"source": "github"})
    record = transport_record(10, claim)
    records_for(ledger, [record])
    (ledger.directory / "prepared.json").write_text(json.dumps([record]))
    worker.github_escalation.return_value = True
    deliver = Mock()
    monkeypatch.setattr(worker.incidents, "deliver", deliver)
    worker.deliver_messages(ledger, cycles, Mock())
    deliver.assert_called_once()
    delivered = deepcopy(claim)
    delivered["incident"]["posts"]["parent"] = {"state": "delivered", "ts": "123.456"}
    delivered["incident"]["parent_view_sha256"] = "first"
    records_for(ledger, [record, transport_record(11, delivered)])
    assert worker.public_delivery_counts(ledger, {claim["scope"]: delivered}, shadow=True) == {
        "public_delivery_count": 0, "historical_parent_update_count": 0,
        "independent_github_delivery_count": 1, "public_transport_count": 1}
    worker.github_escalation.return_value = False
    with pytest.raises(worker.incidents.IncidentError, match="cannot create a cloud-only parent"):
        worker.public_delivery_counts(ledger, {claim["scope"]: delivered}, shadow=True)


@pytest.mark.parametrize("mode", ["shadow", "unified", "rollback"])
def test_serialized_full_cycle_verifies_real_policy_receipts_outcomes_and_heartbeat(tmp_path, monkeypatch, mode):
    from datetime import datetime, timedelta, timezone
    from urllib.parse import parse_qs, urlsplit
    from scripts import cloud_observation_ledger as cycle_records, deployment_emission as emissions
    from test_cloud_observation_ledger import API, RUN
    from test_cloud_incident_adapter import source
    from test_slack_incidents import FakeSlack, IDENTITY

    class CompleteAPI(API):
        def post(self, path, payload):
            record = super().post(path, payload)
            if path.endswith("/deployments"):
                record["payload"] = json.loads(record["payload"])
                self.records[record["id"]] = deepcopy(record)
            return record

        def pages(self, path, field=None):
            if "/statuses?" in path:
                return super().pages(path)
            if "/actions/runs?" in path:
                assert field == "workflow_runs"
                return []
            query = parse_qs(urlsplit(path).query)
            return [deepcopy(record) for record in self.records.values()
                    if all(record.get(key) == query[key][0] for key in ("environment", "task") if key in query)]

    protocol = tmp_path / "protocol"
    for relative in cycle_records.CONTRACT_FILES:
        path = protocol / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("jobs: {}\n" if relative.endswith((".yml", ".yaml")) else "actual protocol fixture: " + relative)
    api, run = CompleteAPI(), deepcopy(RUN)
    monkeypatch.setattr(emissions, "verify", api.verify_signature)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    monkeypatch.setenv("SHARED_DATASETS_SLACK_INCIDENTS_SINCE", "2026-10-01T00:00:00Z")
    initial_mode = "unified" if mode == "rollback" else mode
    selected = {**SHADOW, "mode": initial_mode}
    if initial_mode == "unified":
        selected.update(policy_deployment_id=77, policy_status_id=88)
    monkeypatch.setattr(worker, "routing", lambda *_: deepcopy(selected))
    workdir = tmp_path / "work"
    workdir.mkdir()
    ledger = worker.incidents.Ledger(api, run, workdir)
    cycles = cycle_records.CycleLedger(api, run, workdir, root=protocol)
    fact = source()
    raw_execution = {"name": fact["execution"]["name"], "uid": fact["execution"]["uid"],
                     "template": {"containers": [{"image": fact["execution"]["images"][0]}]},
                     "completionTime": fact["execution"]["completion_time"],
                     "conditions": [{"type": "Completed", "state": "CONDITION_FAILED"}]}
    entry = {"logName": fact["evidence"]["log_name"], "insertId": fact["evidence"]["insert_id"], "timestamp": fact["observed_at"],
             "resource": {"type": "cloud_run_job", "labels": {"project_id": worker.cloud.PROJECT, "location": worker.cloud.REGION, "job_name": fact["job"]}},
             "labels": {"run.googleapis.com/execution_name": fact["execution"]["name"].rsplit("/", 1)[-1]}, "jsonPayload": fact["fact"]}
    reader = SimpleNamespace(entries=lambda view, **_: iter([deepcopy(entry)] if view == worker.cloud.VIEWS[0] else []),
                             execution=lambda _: deepcopy(raw_execution), heartbeat=Mock())
    now = datetime(2026, 10, 21, 12, 12, tzinfo=timezone.utc)
    finished = datetime(2026, 10, 21, 12, 13, tzinfo=timezone.utc)
    proposal = worker.prepare_cycle(ledger, cycles, IDENTITY, reader, now=now, clock=lambda: finished)
    api.sign()
    slack = FakeSlack()
    worker.deliver_messages(ledger, cycles, slack)
    api.sign()
    saved = worker.finalize_cycle(ledger, cycles, clock=lambda: finished)
    api.sign()
    assert worker.verify_heartbeat(ledger, cycles, reader, clock=lambda: finished)
    durable = cycles.verify_cycle_id(saved["manifest_id"])
    assert durable["evaluation"]["public_delivery_count"] == int(initial_mode == "unified")
    assert durable["evaluation"]["cloud_counts"]["actionable"] == 1
    assert durable["manifest"]["mode"] == initial_mode
    assert len(slack.calls) == int(initial_mode == "unified")
    assert proposal["bundle"]["checkpoint"]["covered_through"] == worker.cloud.timestamp(now - worker.cloud.INGESTION_LAG)
    reader.heartbeat.assert_called_once_with(finished)
    if mode == "rollback":
        selected.update(SHADOW)
        next_run = {**run, "id": 101, "head_sha": "b" * 40}
        api.runs[101, 1] = deepcopy(next_run)
        ledger.run = cycles.run = next_run
        later = source("second", time="2026-10-21T12:01:00Z")
        entry.update(insertId=later["evidence"]["insert_id"], timestamp=later["observed_at"], jsonPayload=later["fact"])
        worker.prepare_cycle(ledger, cycles, IDENTITY, reader, now=now + timedelta(minutes=5), clock=lambda: finished + timedelta(minutes=5))
        api.sign()
        worker.deliver_messages(ledger, cycles, slack)
        api.sign()
        saved = worker.finalize_cycle(ledger, cycles, clock=lambda: finished + timedelta(minutes=5))
        api.sign()
        assert worker.verify_heartbeat(ledger, cycles, reader, clock=lambda: finished + timedelta(minutes=5))
        durable = cycles.verify_cycle_id(saved["manifest_id"])
        assert durable["manifest"]["mode"] == "shadow"
        assert durable["evaluation"]["public_delivery_count"] == 0
        assert durable["evaluation"]["historical_parent_update_count"] == 1
        assert durable["evaluation"]["public_transport_count"] == 1
        assert [call[0] for call in slack.calls] == ["post", "update"]


def test_worker_commands_stay_inside_notification_boundary():
    import yaml
    from scripts.deployment_receipt_contracts import notification_boundary
    path = Path(__file__).resolve().parents[1] / ".github/workflows/unattended-workflow-alert.yml"
    workflow = yaml.safe_load(path.read_text())
    assert notification_boundary(path.name, "incidents", workflow, workflow["jobs"]["incidents"])
