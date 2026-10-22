"""Manual publication repairs are observed from canonical GCS, not runtime logs."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import json

import pytest

from scripts import cloud_incident_adapter as adapter, cloud_operational_observations as cloud
from scripts import operational_reconcile as worker, slack_incidents as incidents, translation_recovery as translations
from test_cloud_incident_adapter import at, proposal, source
from test_slack_incident_policy import PersistedLedger
from test_slack_incidents import IDENTITY, FakeSlack
from test_translation_recovery import SLUG, URI, RELEASE, manifest, session_for


def maintenance(*, status="required", generation=10, identifier="debt", time=None):
    return source(identifier, kind="translation_maintenance", status=status,
        job="wdpa-monthly", image="registry/wdpa@sha256:" + "a" * 64,
        subject={"asset_slug": SLUG, "release": RELEASE, "manifest_uri": URI},
        details={"manifest_generation": generation}, time=time or at())


def proof(*, generation=20, debt=0, created=None):
    return translations.read(session_for(manifest(debt=debt), generation=generation, created=created or at(1)), URI)


def verify(current, observation, *, now=None):
    return incidents.reverify_saved_cloud_success(SimpleNamespace(translation_manifest=lambda uri: current), observation,
        clock=lambda: incidents.policy.instant(now or at(100)))


def ingest(ledger, current, *facts, now=None, shadow=False):
    read = Mock(return_value=deepcopy(current))
    records, evaluation = incidents.prepare_cloud(ledger, IDENTITY, proposal(*facts), now=now or at(10), shadow=shadow,
        translation_reader=read,
        recovery_verifier=lambda observation: verify(read(URI), observation, now=now or at(10)))
    checkpoint = cloud.evaluated_collection(proposal(*facts), evaluation)["checkpoint"]
    states = json.loads(json.dumps(ledger.states))
    assert incidents.verify_cloud_evaluation(checkpoint, json.loads(json.dumps(evaluation)), states,
                                            shadow=shadow, worker=evaluation["worker"])
    return records, evaluation, read


def posted():
    ledger, slack = PersistedLedger(), FakeSlack()
    # No canonical recovery read is required to establish the original source.
    records, _ = incidents.prepare_cloud(ledger, IDENTITY, proposal(maintenance()), shadow=False, now=at(10))
    incidents.deliver(ledger, records, slack)
    return ledger, slack, next(iter(ledger.states))


@pytest.mark.parametrize("shadow", [True, False])
def test_manual_fix_without_runtime_completion_quietly_resolves_exact_scope(shadow):
    ledger, slack = PersistedLedger(), FakeSlack()
    records, _ = incidents.prepare_cloud(ledger, IDENTITY, proposal(maintenance()), shadow=shadow, now=at(10))
    if not shadow:
        incidents.deliver(ledger, records, slack)
    before = deepcopy(next(iter(ledger.states.values())))
    current = proof()
    records, evaluation, read = ingest(ledger, current, shadow=shadow)
    state = next(iter(ledger.states.values()))
    assert read.call_args.args == (URI,)
    assert state["incident"]["resolution"]["proof"]["fact"]["kind"] == "translation_manifest"
    assert state["first_failure_at"] == before["first_failure_at"]
    assert state["incident"]["posts"] == before["incident"]["posts"]
    assert evaluation["cloud_counts"]["verified_translation_recoveries"] == 1
    assert evaluation["cloud_counts"]["verified_asset_publications"] == 0
    fact = evaluation["sources"][0]
    assert set(fact) == {"kind", "resource_type", "source_id", "observed_at", "manifest"}
    assert fact["source_id"] == "gcsmanifest:" + cloud.digest({"uri": URI, "generation": current["generation"]})
    assert fact["observed_at"] == current["created_at"]
    assert not cloud.unresolved_pending(cloud.evaluated_collection(proposal(), evaluation)["checkpoint"], evaluation)
    if shadow:
        assert records == [] and slack.calls == []
    else:
        parent = before["incident"]["posts"]["parent"]
        incidents.deliver(ledger, records, slack, recovery_verifier=lambda observation: verify(current, observation))
        assert [call[0] for call in slack.calls].count("post") == 1
        assert next(iter(ledger.states.values()))["incident"]["posts"]["parent"] == parent


def test_unchanged_valid_debt_is_a_complete_quiet_read_with_durable_identity():
    ledger = PersistedLedger()
    incidents.prepare_cloud(ledger, IDENTITY, proposal(maintenance()), shadow=True, now=at(10))
    records, evaluation, _ = ingest(ledger, proof(generation=10, debt=2, created=at(-1)), shadow=True)
    state = next(iter(ledger.states.values()))
    assert records == [] and state["incident"]["resolution"] is None
    assert state["first_failure_at"] == at() and len(state["incident"]["failures"]) == 1
    assert evaluation["sources"][0]["source_id"].startswith("gcsmanifest:")
    assert evaluation["observations"] == [] and evaluation["provenance_complete"]


@pytest.mark.parametrize("delay", [2, 60])
def test_new_debt_after_queued_green_reopens_original_parent_without_deadlock(delay):
    ledger, slack, scope = posted()
    original = deepcopy(ledger.states[scope])
    green = proof()
    records, _, _ = ingest(ledger, green)
    newer = proof(generation=21, debt=2, created=at(delay))
    with pytest.raises(incidents.IncidentError, match="current canonical"):
        incidents.deliver(ledger, records, slack, recovery_verifier=lambda observation: verify(newer, observation))
    records, evaluation, _ = ingest(ledger, newer, now=at(delay + 1))
    state = ledger.states[scope]
    assert state["incident"]["resolution"] is None and not state["deferred"]
    assert state["incident"]["id"] == original["incident"]["id"]
    assert state["incident"]["posts"] == original["incident"]["posts"]
    assert state["first_failure_at"] == original["first_failure_at"]
    assert evaluation["cloud_counts"]["actionable"] == 1
    incidents.deliver(ledger, records, slack)
    assert [call[0] for call in slack.calls].count("post") == 1
    fixed = proof(generation=22, created=at(delay + 2))
    records, _, _ = ingest(ledger, fixed, now=at(delay + 3))
    incidents.deliver(ledger, records, slack, recovery_verifier=lambda observation: verify(fixed, observation))
    assert ledger.states[scope]["incident"]["resolution"] is not None
    assert [call[0] for call in slack.calls].count("post") == 1


@pytest.mark.parametrize("delay", [2, 60])
def test_new_complete_generation_refreshes_pending_green_without_renewing_stable_clock(delay):
    ledger, slack, scope = posted()
    first = proof()
    records, _, _ = ingest(ledger, first)
    stable = ledger.states[scope]["stable_since"]
    newer = proof(generation=21, created=at(delay))
    with pytest.raises(incidents.IncidentError):
        incidents.deliver(ledger, records, slack, recovery_verifier=lambda observation: verify(newer, observation))
    records, _, _ = ingest(ledger, newer, now=at(delay + 1))
    state = ledger.states[scope]
    assert state["stable_since"] == stable and state["incident"]["resolution"]["proof"]["fact"]["manifest"] == first
    assert state["recoveries"][scope]["proof"]["fact"]["manifest"] == newer and not state["deferred"]
    assert "Current verified translation coverage" in json.dumps(incidents.render(state))
    incidents.deliver(ledger, records, slack, recovery_verifier=lambda observation: verify(newer, observation))
    records, evaluation, read = ingest(ledger, proof(generation=22, created=at(delay + 70)), now=at(delay + 80))
    assert not read.called and records == [] and evaluation["sources"] == []
    worker.reverify_translation_recoveries(ledger, ledger.states, set(), SimpleNamespace(translation_manifest=Mock(side_effect=AssertionError)))


def test_uncertain_delivery_claim_and_other_target_failures_cannot_be_resolved_by_coverage():
    ledger = PersistedLedger()
    facts = [maintenance(), maintenance(status="already_claimed", identifier="uncertain")]
    incidents.prepare_cloud(ledger, IDENTITY, proposal(*facts), shadow=False, now=at(10))
    before = deepcopy(next(iter(ledger.states.values()))["incident"]["posts"])
    records, _, _ = ingest(ledger, proof())
    state = next(iter(ledger.states.values()))
    assert state["incident"]["resolution"] is None and state["incident"]["posts"] == before
    assert incidents.pending_posts(state) and not records
    green = adapter.normalize({"kind": "translation_manifest", "resource_type": "gcs_object", "manifest": proof(),
        "observed_at": proof()["created_at"], "source_id": "gcsmanifest:" + cloud.digest({"uri": URI, "generation": 20})})
    for unrelated in [source("unknown"), source("identity", kind="identity_decision", status="required", subject={"asset_slug": SLUG, "release": RELEASE})]:
        assert not adapter.covers(adapter.normalize(unrelated), green)
    unknown_job = maintenance(identifier="unregistered")
    unknown_job["job"] = "unregistered-job"
    unknown_job["execution"]["name"] = unknown_job["execution"]["name"].replace("/jobs/wdpa-monthly/", "/jobs/unregistered-job/")
    unknown_job["fact"]["context"]["job"] = "unregistered-job"
    assert not adapter.covers(adapter.normalize(unknown_job), green)


def test_invalid_or_future_current_proof_fails_before_any_checkpoint_is_saved():
    ledger = PersistedLedger()
    incidents.prepare_cloud(ledger, IDENTITY, proposal(maintenance()), shadow=True, now=at(10))
    before = deepcopy(ledger.states)
    invalid = proof()
    invalid["sha256"] = "0" * 64
    for value in [invalid, proof(created=at(11))]:
        with pytest.raises((ValueError, incidents.IncidentError)):
            ingest(ledger, value, shadow=True)
        assert ledger.states == before


def test_gcs_source_has_no_log_execution_or_deployment_identity():
    value = proof()
    fact = {"kind": "translation_manifest", "resource_type": "gcs_object", "manifest": value,
            "observed_at": value["created_at"], "source_id": "gcsmanifest:" + cloud.digest({"uri": URI, "generation": value["generation"]})}
    observation = adapter.normalize(json.loads(json.dumps(fact)))
    assert observation["scope"].startswith("cloud:translation:") and observation["conclusion"] == "success"
    for field, replacement in [("source_id", "cloudlog:" + "a" * 64), ("resource_type", "cloud_run_job"), ("observed_at", at(3))]:
        forged = deepcopy(fact)
        forged[field] = replacement
        with pytest.raises(ValueError):
            adapter.normalize(forged)
    forged = deepcopy(observation)
    forged["kind"] = "deployment"
    with pytest.raises(ValueError):
        adapter.validate_observation(forged)


def test_cosmetic_metadata_of_latest_refreshed_generation_preserves_source_identity():
    ledger, slack, scope = posted()
    ingest(ledger, proof())
    current = proof(generation=21, created=at(2))
    ingest(ledger, current)
    metadata_change = deepcopy(current)
    metadata_change["updated_at"] = at(3)
    records, evaluation, _ = ingest(ledger, metadata_change)
    saved = ledger.states[scope]["recoveries"][scope]
    assert saved["proof"]["fact"]["manifest"] == current
    assert evaluation["sources"][0]["manifest"] == current
    incidents.deliver(ledger, records, slack, recovery_verifier=lambda item: verify(metadata_change, item))
    future = deepcopy(metadata_change)
    future["updated_at"] = at(101)
    with pytest.raises(incidents.IncidentError, match="future"):
        verify(future, saved)


def test_version_progression_uses_creation_time_with_unique_unordered_generations():
    ledger, slack, scope = posted()
    first = proof(generation=20)
    ingest(ledger, first)
    lower = proof(generation=7, created=at(2))
    records, _, _ = ingest(ledger, lower)
    assert ledger.states[scope]["recoveries"][scope]["proof"]["fact"]["manifest"] == lower
    assert ledger.states[scope]["stable_since"] == at(1)
    incidents.deliver(ledger, records, slack, recovery_verifier=lambda item: verify(lower, item))
    debt = proof(generation=3, created=at(3), debt=2)
    records, _, _ = ingest(ledger, debt)
    assert ledger.states[scope]["incident"]["resolution"] is None
    incidents.deliver(ledger, records, slack)
    fixed = proof(generation=1, created=at(4))
    records, _, _ = ingest(ledger, fixed)
    incidents.deliver(ledger, records, slack, recovery_verifier=lambda item: verify(fixed, item))
    assert ledger.states[scope]["incident"]["resolution"]["proof"]["fact"]["manifest"] == fixed
    assert [call[0] for call in slack.calls].count("post") == 1


def owned_maintenance(status="active"):
    failure = adapter.normalize(maintenance())
    state = incidents.fold(None, failure, IDENTITY)
    binding = incidents.binding_for(failure)
    policy = incidents.policy
    authority = {"actor": "jonaraphael", "repository": incidents.REPOSITORY, "workflow": incidents.WORKFLOW,
                 "ref": "refs/heads/main", "executor_sha": "a" * 40, "run_id": 900, "attempt": 1}
    context = {"authority": authority, "responsible_human": "jonaraphael", "conversation_ref": "codex://threads/origin",
               "current_action": "Repair translation coverage without replaying publication."}
    owner = policy.transition_supervision(None, "claim", binding, now=at(-1), **context)
    owner = policy.transition_supervision(owner, "check_in", binding, now=at(), **context)
    owner = policy.bind_supervision(owner, binding, first_failure_at=at(), now=at())
    if status == "awaiting_clarification":
        owner = policy.transition_supervision(owner, "clarification", binding, now=at(),
            question_ref="codex://threads/origin#question", **context)
    state["supervision"][state["scope"]] = owner
    return PersistedLedger({state["scope"]: state}), deepcopy(owner), state["scope"]


@pytest.mark.parametrize("status,minute,disposition", [
    ("active", 5, "owner_held"), ("active", 11, "escalate"), ("active", 121, "escalate"),
    ("awaiting_clarification", 119, "clarification_wait"), ("awaiting_clarification", 121, "escalate"),
])
def test_canonical_alias_preserves_accountability_and_exact_original_lease(status, minute, disposition):
    ledger, before, scope = owned_maintenance(status)
    records, _, _ = ingest(ledger, proof(debt=2, created=at(2)), now=at(minute))
    owner = ledger.states[scope]["supervision"][scope]
    assert {key: value for key, value in owner.items() if key != "covered_bindings"} == {
        key: value for key, value in before.items() if key != "covered_bindings"}
    canonical = next(item for item in ledger.states[scope]["incident"]["failures"] if item["proof"]["fact"]["kind"] == "translation_manifest")
    assert owner["covered_bindings"] == [incidents.binding_for(canonical).to_dict()]
    decisions = incidents.episode_decisions(ledger.states[scope], now=at(minute))
    assert {decision.disposition.value for _, decision in decisions} == {disposition}
    assert bool(records) is (disposition == "escalate")


def test_existing_canonical_binding_supports_actual_cli_claim_and_check_in_without_pr_provenance(capsys):
    from scripts import slack_supervision
    ledger, original, scope = owned_maintenance()
    ingest(ledger, proof(debt=2, created=at(2)), now=at(5))
    canonical = next(item for item in ledger.states[scope]["incident"]["failures"] if item["proof"]["fact"]["kind"] == "translation_manifest")
    binding = incidents.binding_for(canonical)
    args = ["--operation-id", scope, "--target", scope, "--revision", binding.revision, "--human", "jonaraphael",
            "--conversation", original["conversation_ref"], "--current-action", "Inspect exact canonical translation coverage.", "--dry-run"]
    for command, minute in [("claim", 5), ("check-in", 6)]:
        slack_supervision.main([command, *args])
        request = json.loads(capsys.readouterr().out)
        ledger.run["created_at"] = at(minute)
        incidents.supervise(ledger, IDENTITY, request, now=at(minute))
    owner = ledger.states[scope]["supervision"][scope]
    assert owner["revision"] == binding.revision and owner["pr_number"] is None
    assert owner["first_failure_at"] == original["first_failure_at"] and owner["deadline"] == original["deadline"]
    assert owner["responsible_human"] == original["responsible_human"] and owner["conversation_ref"] == original["conversation_ref"]
    for mutation in [dict(revision="sha256:" + "f" * 64), dict(pr_number=7)]:
        forged = deepcopy(request)
        forged["binding"].update(mutation)
        with pytest.raises(incidents.IncidentError, match="exact signed"):
            incidents.supervise(ledger, IDENTITY, forged, now=at(6))
    forged = deepcopy(canonical)
    forged["proof"]["fact"]["manifest"]["sha256"] = "f" * 64
    with pytest.raises(ValueError):
        incidents.continue_translation_supervision(ledger.states, [forged])


def test_canonical_alias_cannot_create_an_owner_from_an_uncertain_delivery_claim():
    ledger, owner, scope = owned_maintenance()
    state = ledger.states[scope]
    state["incident"]["failures"][0] = adapter.normalize(maintenance(status="already_claimed"))
    current = proof(debt=2, created=at(2))
    observation = adapter.normalize({"kind": "translation_manifest", "resource_type": "gcs_object", "manifest": current,
        "observed_at": current["created_at"], "source_id": "gcsmanifest:" + cloud.digest({"uri": URI, "generation": current["generation"]})})
    incidents.continue_translation_supervision(ledger.states, [observation])
    assert ledger.states[scope]["supervision"][scope] == owner
    state["supervision"] = {}
    incidents.continue_translation_supervision(ledger.states, [observation])
    assert state["supervision"] == {}


def test_manual_fix_roundtrips_signed_cycle_and_offline_gcs_http_without_runtime_event(tmp_path, monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    from scripts import cloud_observation_ledger as cycles, deployment_emission as emissions
    from test_cloud_observation_ledger import API, RUN
    from test_translation_recovery import Response, encoded, metadata

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
                return []
            query = parse_qs(urlsplit(path).query)
            return [deepcopy(record) for record in self.records.values()
                    if all(record.get(key) == query[key][0] for key in ("environment", "task") if key in query)]

    class Session:
        def __init__(self):
            self.calls = []

        def request(self, method, url, **kwargs):
            assert method == "GET"
            self.calls.append((url, kwargs))
            body = encoded(manifest())
            if kwargs["params"].get("alt") == "media":
                assert kwargs["params"]["generation"] == "20"
                return Response(body=body)
            return Response(metadata(body, created=at(1)))

    protocol, work = tmp_path / "protocol", tmp_path / "work"
    work.mkdir()
    for relative in cycles.CONTRACT_FILES:
        path = protocol / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("jobs: {}\n" if relative.endswith((".yml", ".yaml")) else "protocol fixture: " + relative)
    api, run = CompleteAPI(), deepcopy(RUN)
    monkeypatch.setattr(emissions, "verify", api.verify_signature)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    monkeypatch.setenv("SHARED_DATASETS_SLACK_INCIDENTS_SINCE", at(-60))
    monkeypatch.setattr(worker, "routing", lambda *_: {"mode": "unified", "policy_deployment_id": 77, "policy_status_id": 88, "config_sha256": "d" * 64})
    ledger = incidents.Ledger(api, run, work)
    cycle_ledger = cycles.CycleLedger(api, run, work, root=protocol)
    slack = FakeSlack()
    records, _ = incidents.prepare_cloud(ledger, IDENTITY, proposal(maintenance()), shadow=False, now=at(10))
    api.sign()
    incidents.deliver(ledger, records, slack)
    api.sign()
    session = Session()
    reader = cloud.CloudReader(session)
    reader.entries = lambda *_args, **_kwargs: iter(())  # No completion event exists in Cloud Run.
    reader.executions = lambda job: iter(())
    reader.heartbeat = Mock()
    def clock():
        return incidents.policy.instant(at(13))
    monkeypatch.setattr(worker, "utc_now", clock)
    value = worker.prepare_cycle(ledger, cycle_ledger, IDENTITY, reader, now=incidents.policy.instant(at(12)), clock=clock)
    assert [item["kind"] for item in value["bundle"]["evaluation"]["sources"]] == ["translation_manifest"]
    assert value["bundle"]["checkpoint"]["seen"] == {}
    api.sign()
    worker.deliver_messages(ledger, cycle_ledger, slack, cloud_reader=reader)
    api.sign()
    worker.finalize_cycle(ledger, cycle_ledger, clock=clock, cloud_reader=reader)
    api.sign()
    assert worker.verify_heartbeat(ledger, cycle_ledger, reader, clock=clock)
    assert reader.heartbeat.call_count == 1
    assert [call[0] for call in slack.calls].count("post") == 1
    assert [call[0] for call in slack.calls].count("update") == 1
    assert len(session.calls) >= 8 and all("storage.googleapis.com/storage/v1/" in url for url, _ in session.calls)
