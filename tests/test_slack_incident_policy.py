"""Deterministic replay through serialized v3 state and offline Slack transport."""
import copy
import json
from unittest import mock

import pytest

from scripts import operational_policy as policy, slack_incidents as incidents
from test_slack_incidents import (FakeSlack, IDENTITY, MemoryLedger, SHA, SCOPE,
                                 historical_state, observation, signed_record, success)

FAILURE = "2026-10-06T08:52:58Z"


class PersistedLedger(MemoryLedger):
    def __init__(self, states=None):
        super().__init__(states)
        self.run.update(id=900, run_attempt=1, path=incidents.WORKFLOW,
                        head_branch="main", head_sha=SHA)

    def save(self, value, phase):
        result = super().save(json.loads(json.dumps(value, sort_keys=True)), phase)
        # Real Ledger.load suppresses an older prospective row only after the
        # newer signed canonical checkpoint incorporated its ownership.
        for alias in value["aliases"]:
            if alias != value["scope"] and alias in self.states and self.states[alias]["incident"] is None:
                del self.states[alias]
        return result


def fact(value, *, reason="transient_transport", mutation="not_started", recovery="read_only_retry"):
    return {**incidents.operational_fact(value).to_dict(), "reason": reason,
            "mutation_status": mutation, "recovery": recovery,
            "impact": "Catalog refresh delayed; existing published data remains available.",
            "published_data_state": "No mutation started; existing artifacts are unchanged.",
            "required_action": "Repair the catalog input read if safe source retries are exhausted."}


def ingest(ledger, values, now, facts=None):
    return incidents.prepare(ledger, IDENTITY, values, now=now, operational=facts,
                             ancestor=lambda *_: True, success=lambda *_: None)


def request(command="claim", *, revision=SHA, human="jonaraphael", question=None):
    return {"command": command, "binding": {"operation_id": SCOPE, "target": "pmtiles-cdn", "revision": revision, "pr_number": None},
            "responsible_human": human, "conversation_ref": "codex://threads/origin",
            "current_action": "Inspect exact read failure and verify authorized recovery.",
            "question_ref": question, "handoff_ref": None}


def supervise(ledger, command, now, **kwargs):
    ledger.run["created_at"] = now
    return incidents.supervise(ledger, IDENTITY, request(command, **kwargs), now=now)


def test_transient_recovery_before_deadline_creates_zero_public_messages():
    value = observation()
    ledger, slack = PersistedLedger(), FakeSlack()
    assert ingest(ledger, [value], FAILURE, {incidents.observation_key(value): fact(value)}) == []
    assert ledger.states[SCOPE]["incident"]["posts"] == {}
    recovered = success(time="2026-10-06T09:10:00Z")
    assert ingest(ledger, [recovered], recovered["time"]) == []
    assert ingest(ledger, [], "2026-10-06T09:23:00Z") == []
    assert slack.calls == []


def test_scheduled_sweep_escalates_once_after_exact_recovery_budget():
    value = observation()
    ledger, slack = PersistedLedger(), FakeSlack()
    assert ingest(ledger, [value], FAILURE, {incidents.observation_key(value): fact(value)}) == []
    assert ingest(ledger, [], "2026-10-06T09:22:57Z") == []
    records = ingest(ledger, [], "2026-10-06T09:22:58Z")
    assert len(records) == 1
    incidents.deliver(ledger, records, slack)
    assert ingest(ledger, [], "2026-10-06T09:23:00Z") == []
    assert len([call for call in slack.calls if call[0] == "post"]) == 1
    text = slack.calls[0][1]["text"]
    for field in ("Impact:", "Published-data state:", "Responsible owner:", "Required action:", "Recovery attempted:", "Evidence:"):
        assert field in text


def test_registration_alone_does_not_establish_active_supervision():
    ledger = PersistedLedger()
    assert supervise(ledger, "claim", "2026-10-06T08:50:00Z") == []
    assert ledger.states[SCOPE]["incident"] is None
    assert ledger.states[SCOPE]["supervision"][SCOPE]["deadline"] is None
    value = observation()
    records = ingest(ledger, [value], FAILURE, {incidents.observation_key(value): fact(value, reason="deterministic_defect", recovery="none")})
    assert len(records) == 1


def active_owner():
    ledger = PersistedLedger()
    supervise(ledger, "claim", "2026-10-06T08:50:00Z")
    supervise(ledger, "check_in", "2026-10-06T08:52:00Z")
    value = observation()
    assert ingest(ledger, [value], FAILURE, {incidents.observation_key(value): fact(value, reason="deterministic_defect", recovery="none")}) == []
    return ledger


def test_active_owner_holds_then_stale_check_in_escalates_without_clock_reset():
    ledger = active_owner()
    owner = copy.deepcopy(ledger.states[SCOPE]["supervision"][SCOPE])
    assert owner["first_failure_at"] == FAILURE and owner["deadline"] == "2026-10-06T10:52:58Z"
    assert ingest(ledger, [], "2026-10-06T09:01:59Z") == []
    assert ledger.states[SCOPE]["supervision"][SCOPE] == owner
    assert len(ingest(ledger, [], "2026-10-06T09:02:00Z")) == 1


def test_late_check_in_cannot_erase_eligibility_between_scheduled_sweeps():
    ledger = active_owner()
    records = supervise(ledger, "check_in", "2026-10-06T09:02:01Z")
    assert len(records) == 1
    state = ledger.states[SCOPE]
    assert state["escalation_at"] == "2026-10-06T09:02:00Z"
    assert state["escalation_reason"] == "supervision_check_in_stale"
    assert state["supervision"][SCOPE]["last_check_in"] == "2026-10-06T09:02:01Z"
    assert state["incident"]["posts"]["parent"]["state"] == "claimed"


def test_stale_prospective_check_in_cannot_escalate_before_first_failure():
    ledger = PersistedLedger()
    supervise(ledger, "claim", "2026-10-06T07:00:00Z")
    supervise(ledger, "check_in", "2026-10-06T07:01:00Z")
    value = observation()
    records = ingest(ledger, [value], FAILURE, {incidents.observation_key(value): fact(value, reason="unknown", recovery="none")})
    assert len(records) == 1
    assert ledger.states[SCOPE]["escalation_at"] == FAILURE


def test_clarification_wait_pauses_check_ins_but_preserves_original_deadline():
    ledger = active_owner()
    assert supervise(ledger, "clarification", "2026-10-06T08:59:00Z", question="codex://threads/origin#question-1") == []
    assert ingest(ledger, [], "2026-10-06T10:52:57Z") == []
    owner = ledger.states[SCOPE]["supervision"][SCOPE]
    assert owner["deadline"] == "2026-10-06T10:52:58Z"
    assert ledger.states[SCOPE]["incident"]["resolution"] is None
    assert len(ingest(ledger, [], "2026-10-06T10:52:58Z")) == 1


@pytest.mark.parametrize("command", ["release", "supersede"])
def test_releasing_or_superseding_owner_escalates_once(command):
    ledger = active_owner()
    records = supervise(ledger, command, "2026-10-06T08:59:00Z")
    assert len(records) == 1
    incidents.deliver(ledger, records, FakeSlack())
    assert ingest(ledger, [], "2026-10-06T09:00:00Z") == []


def test_forged_owner_and_wrong_revision_cannot_hold_an_issue():
    ledger = PersistedLedger()
    with pytest.raises(policy.PolicyError, match="authenticated actor"):
        supervise(ledger, "claim", "2026-10-06T08:50:00Z", human="someone-else")
    assert ledger.states == {}
    supervise(ledger, "claim", "2026-10-06T08:50:00Z", revision="b" * 40)
    supervise(ledger, "check_in", "2026-10-06T08:52:00Z", revision="b" * 40)
    value = observation()
    assert len(ingest(ledger, [value], FAILURE, {incidents.observation_key(value): fact(value, reason="unknown", recovery="none")})) == 1


def test_uncertain_mutation_remains_frozen_during_accountable_private_inspection():
    ledger = active_owner()
    value = observation(run_id=102, time="2026-10-06T08:53:00Z")
    records = ingest(ledger, [value], value["time"], {incidents.observation_key(value): fact(value, mutation="uncertain")})
    assert records == []
    decision = incidents.episode_decisions(ledger.states[SCOPE], now=value["time"])[-1][1]
    assert decision.freeze_replay and not decision.retry_safe
    assert decision.disposition == policy.Disposition.OWNER_HELD


def test_workflow_and_deployment_aliases_keep_one_parent_and_target_proof():
    workflow = observation(kind="workflow", scope="workflow:catalog-web-deploy.yml", job_id=0,
                           job_name="Catalog web deploy", label="Catalog web deploy")
    ledger, slack = PersistedLedger(), FakeSlack()
    incidents.deliver(ledger, ingest(ledger, [workflow], FAILURE), slack)
    original = ledger.states[workflow["scope"]]["incident"]["id"]
    exact = observation(scope="deployment:catalog-web", label="catalog-web", time="2026-10-06T08:52:57Z")
    incidents.deliver(ledger, ingest(ledger, [exact], "2026-10-06T08:53:00Z"), slack)
    assert len(ledger.states) == 1
    state = ledger.states[workflow["scope"]]
    assert state["incident"]["id"] == original and state["first_failure_at"] == exact["time"]
    assert len([call for call in slack.calls if call[0] == "post"]) == 1
    generic_green = {**workflow, "conclusion": "success", "run_id": 103, "time": "2026-10-06T09:00:00Z"}
    ingest(ledger, [generic_green], generic_green["time"])
    assert ledger.states[workflow["scope"]]["incident"]["resolution"] is None
    proof = success(scope=exact["scope"], label="catalog-web", time="2026-10-06T09:01:00Z")
    incidents.deliver(ledger, ingest(ledger, [proof], proof["time"]), slack)
    assert ledger.states[workflow["scope"]]["incident"]["resolution"] == proof
    assert len([call for call in slack.calls if call[0] == "post"]) == 1


def test_brief_flapping_reopens_same_parent_without_attempt_replies():
    ledger, slack = PersistedLedger(), FakeSlack()
    incidents.deliver(ledger, ingest(ledger, [observation()], FAILURE), slack)
    original = ledger.states[SCOPE]["incident"]["id"]
    recovered = success(time="2026-10-06T09:00:00Z")
    incidents.deliver(ledger, ingest(ledger, [recovered], recovered["time"]), slack)
    flap = observation(run_id=102, sha="b" * 40, time="2026-10-06T09:29:59Z")
    incidents.deliver(ledger, ingest(ledger, [flap], flap["time"]), slack)
    assert ledger.states[SCOPE]["incident"]["id"] == original
    assert ledger.states[SCOPE]["first_failure_at"] == FAILURE
    assert ledger.states[SCOPE]["incident"]["resolution"] is None
    assert len([call for call in slack.calls if call[0] == "post"]) == 1
    assert set(ledger.states[SCOPE]["incident"]["posts"]) == {"parent"}


def test_correlated_targets_require_each_exact_recovery_proof():
    workflow = observation(kind="workflow", scope="workflow:ci.yml", job_id=0,
                           job_name="CI", label="CI")
    ledger = PersistedLedger()
    ingest(ledger, [workflow], FAILURE)
    first = observation(time="2026-10-06T08:53:00Z")
    second = observation(scope="deployment:catalog-web", label="catalog-web", job_id=42,
                         time="2026-10-06T08:53:00Z")
    ingest(ledger, [first, second], first["time"])
    root = workflow["scope"]
    assert len(ledger.states) == 1
    # A claimed public parent is intentionally uncertain until delivery is
    # acknowledged. Release it through the existing reviewed receipt path.
    ledger.states[root]["incident"]["posts"]["parent"] = {"state": "delivered", "ts": "1791270000.000001"}
    ingest(ledger, [success()], "2026-10-06T10:18:44Z")
    assert ledger.states[root]["incident"]["resolution"] is None
    assert set(ledger.states[root]["recoveries"]) == {SCOPE}
    proof = success(scope="deployment:catalog-web", label="catalog-web", job_name="catalog-web",
                    time="2026-10-06T10:19:00Z")
    ingest(ledger, [proof], proof["time"])
    assert ledger.states[root]["incident"]["resolution"] == proof


def test_prospective_owner_joins_workflow_fallback_without_a_duplicate_episode():
    ledger = PersistedLedger()
    supervise(ledger, "claim", "2026-10-06T08:50:00Z")
    supervise(ledger, "check_in", "2026-10-06T08:52:00Z")
    workflow = observation(kind="workflow", scope="workflow:pmtiles-cdn-sync.yml", job_id=0,
                           job_name="PMTiles CDN sync", label="PMTiles CDN sync")
    slack = FakeSlack()
    incidents.deliver(ledger, ingest(ledger, [workflow], FAILURE), slack)
    value = observation(time="2026-10-06T08:53:00Z")
    ingest(ledger, [value], value["time"], {incidents.observation_key(value): fact(value, reason="deterministic_defect", recovery="none")})
    assert len(ledger.states) == 1
    state = ledger.states[workflow["scope"]]
    assert SCOPE in state["aliases"] and state["supervision"][SCOPE]["first_failure_at"] == FAILURE
    assert len([call for call in slack.calls if call[0] == "post"]) == 1


def test_claimed_new_revision_and_repeated_failure_preserve_original_deadline():
    ledger = active_owner()
    assert supervise(ledger, "claim", "2026-10-06T08:59:00Z", revision="b" * 40) == []
    assert ledger.states[SCOPE]["supervision"][SCOPE]["deadline"] == "2026-10-06T10:52:58Z"
    owner = ledger.states[SCOPE]["supervision"][SCOPE]
    assert owner["covered_bindings"] == [incidents.binding_for(observation()).to_dict()]
    assert owner["last_check_in"] == "2026-10-06T08:52:00Z"
    assert supervise(ledger, "check_in", "2026-10-06T09:01:00Z", revision="b" * 40) == []
    assert ingest(ledger, [], "2026-10-06T09:02:00Z") == []
    assert ledger.states[SCOPE]["supervision"][SCOPE]["deadline"] == "2026-10-06T10:52:58Z"
    assert ledger.states[SCOPE]["incident"]["posts"] == {}


def test_guessed_prior_revision_never_becomes_a_covered_failure():
    ledger = PersistedLedger()
    supervise(ledger, "claim", "2026-10-06T08:50:00Z", revision="b" * 40)
    supervise(ledger, "check_in", "2026-10-06T08:52:00Z", revision="b" * 40)
    value = observation()
    ingest(ledger, [value], FAILURE, {incidents.observation_key(value): fact(value, reason="unknown", recovery="none")})
    supervise(ledger, "claim", "2026-10-06T08:59:00Z", revision="c" * 40)
    assert ledger.states[SCOPE]["supervision"][SCOPE]["covered_bindings"] == []
    assert ledger.states[SCOPE]["incident"]["posts"]["parent"]["state"] == "claimed"


def test_supplied_pr_must_match_exact_merged_revision_before_registration():
    ledger = PersistedLedger()
    value = request()
    value["binding"]["pr_number"] = 208
    ledger.run["created_at"] = "2026-10-06T08:50:00Z"
    ledger.api.get.return_value = {"merged": True, "merge_commit_sha": "b" * 40,
                                   "base": {"ref": "main", "repo": {"full_name": incidents.REPOSITORY}}}
    with pytest.raises(incidents.IncidentError, match="exact merged deployment revision"):
        incidents.supervise(ledger, IDENTITY, value, now="2026-10-06T08:50:00Z")
    assert ledger.states == {}


def test_queued_check_in_does_not_manufacture_current_agent_activity():
    ledger = active_owner()
    ledger.run["created_at"] = "2026-10-06T08:59:00Z"
    records = incidents.supervise(ledger, IDENTITY, request("check_in"), now="2026-10-06T09:15:00Z")
    assert len(records) == 1
    state = ledger.states[SCOPE]
    assert state["supervision"][SCOPE]["last_check_in"] == "2026-10-06T08:59:00Z"
    assert state["escalation_at"] == "2026-10-06T09:02:00Z"


@pytest.mark.parametrize("created_at,attempt", [(None, 1), ("2026-10-06T08:59:00Z", 2)])
def test_supervision_requires_authoritative_dispatch_time_and_new_attempt(created_at, attempt):
    ledger = active_owner()
    ledger.run.update(created_at=created_at, run_attempt=attempt)
    with pytest.raises((policy.PolicyError, incidents.IncidentError)):
        incidents.supervise(ledger, IDENTITY, request("check_in"), now="2026-10-06T09:00:00Z")


def test_new_episode_after_stable_recovery_does_not_inherit_expired_owner():
    ledger = active_owner()
    recovered = success(time="2026-10-06T09:00:00Z")
    ingest(ledger, [recovered], recovered["time"])
    original = ledger.states[SCOPE]["incident"]["id"]
    value = observation(run_id=104, sha="b" * 40, time="2026-10-06T12:00:00Z")
    assert ingest(ledger, [value], value["time"], {incidents.observation_key(value): fact(value)}) == []
    current = ledger.states[SCOPE]
    assert current["incident"]["id"] != original
    assert current["first_failure_at"] == value["time"]
    assert current["supervision"] == {}


def test_cold_load_suppresses_only_older_prospective_alias_with_retained_owner(tmp_path):
    ledger = PersistedLedger()
    supervise(ledger, "claim", "2026-10-06T08:50:00Z")
    prospective = copy.deepcopy(ledger.states[SCOPE])
    workflow = observation(kind="workflow", scope="workflow:pmtiles-cdn-sync.yml", job_id=0,
                           job_name="PMTiles CDN sync", label="PMTiles CDN sync")
    ingest(ledger, [workflow], FAILURE)
    value = observation(time="2026-10-06T08:53:00Z")
    ingest(ledger, [value], value["time"])
    canonical = ledger.states[workflow["scope"]]
    records = [{"id": number, "payload": {"schema": incidents.SCHEMA, "state": state}}
               for number, state in ((1, prospective), (2, canonical))]
    api = mock.Mock()
    api.pages.return_value = records
    reader = incidents.Ledger(api, {}, tmp_path)
    with mock.patch.object(reader, "verify", side_effect=lambda row: copy.deepcopy(row["payload"]["state"])):
        loaded = reader.load()
    assert set(loaded) == {workflow["scope"]}
    assert loaded[workflow["scope"]]["supervision"][SCOPE]["responsible_human"] == "jonaraphael"
    records[0]["id"] = 3
    with mock.patch.object(reader, "verify", side_effect=lambda row: copy.deepcopy(row["payload"]["state"])):
        with pytest.raises(incidents.IncidentError, match="newer registration"):
            reader.load()


def test_out_of_order_failure_is_not_hidden_by_another_targets_newer_receipt():
    workflow = observation(kind="workflow", scope="workflow:ci.yml", job_id=0, job_name="CI", label="CI")
    first = observation(time="2026-10-06T08:53:00Z")
    second = observation(scope="deployment:catalog-web", label="catalog-web", job_id=42,
                         time="2026-10-06T08:53:00Z")
    ledger = PersistedLedger()
    ingest(ledger, [workflow, first, second], first["time"])
    root = workflow["scope"]
    ledger.states[root]["incident"]["posts"]["parent"] = {"state": "delivered", "ts": "1791270000.000001"}
    proof_a = success(sha=SHA, time="2026-10-06T09:10:00Z")
    proof_b = success(scope="deployment:catalog-web", label="catalog-web", job_name="catalog-web",
                      sha=SHA, time="2026-10-06T09:20:00Z")
    ingest(ledger, [proof_a, proof_b], proof_b["time"])
    assert ledger.states[root]["incident"]["resolution"] == proof_b
    delayed = observation(run_id=104, time="2026-10-06T09:15:00Z")
    ingest(ledger, [delayed], "2026-10-06T09:21:00Z")
    state = ledger.states[root]
    # Green updates first receive their delivery acknowledgment. The late
    # failure is retained, then reconsidered against its own target's proof.
    state["incident"]["parent_view_sha256"] = incidents.parent_view_sha256(state)
    ingest(ledger, [], "2026-10-06T09:22:00Z")
    state = ledger.states[root]
    assert state["incident"]["resolution"] is None
    assert delayed in state["incident"]["failures"]
    assert state["watermarks"][SCOPE] == proof_a
    assert state["watermarks"]["deployment:catalog-web"] == proof_b
    assert SCOPE not in state["recoveries"]
    # Action text concerns the outstanding target, never the recovered one.
    state["operational"][incidents.observation_key(second)] = fact(second, reason="unknown", recovery="none")
    state["operational"][incidents.observation_key(second)]["required_action"] = "Repair already-recovered catalog."
    payload = incidents.render(state)
    assert "Repair already-recovered catalog." not in payload["text"]
    assert "*Failed attempts:* 2" in payload["text"]


@pytest.mark.parametrize("schema", [incidents.LEGACY_SCHEMA, incidents.PREVIOUS_SCHEMA])
def test_signed_legacy_migration_preserves_parent_and_uncertain_claims(schema, tmp_path):
    record = signed_record()
    value = incidents.fold(record["payload"]["state"], observation(run_id=102, time="2026-10-06T09:00:00Z"), IDENTITY)
    value["incident"]["posts"]["parent"] = {"state": "claimed", "ts": None}
    operation = "attempt:" + incidents.observation_key(value["incident"]["failures"][-1])
    value["incident"]["posts"][operation] = {"state": "claimed", "ts": None}
    record["payload"].update(schema=schema, state=historical_state(value, schema))
    original = copy.deepcopy(record)
    api = mock.Mock()
    api.pages.return_value = [{"id": 12, "state": "success", "description": "applied"}]
    with mock.patch.object(incidents.emissions, "verify") as verify:
        migrated = incidents.Ledger(api, {}, tmp_path).verify(record)
    verify.assert_called_once()
    assert record == original
    assert migrated["incident"]["id"] == value["incident"]["id"]
    assert migrated["incident"]["posts"] == value["incident"]["posts"]
    assert migrated["first_failure_at"] == FAILURE
    assert ingest(PersistedLedger({SCOPE: migrated}), [], "2026-10-06T09:30:00Z") == []
