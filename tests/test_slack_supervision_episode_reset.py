"""Historical resolution must not discard a new signed prospective owner."""
import copy
import json
from unittest import mock

import pytest

from scripts import operational_policy as policy, slack_incidents as incidents
from test_slack_incident_policy import PersistedLedger, active_owner, fact, ingest, request
from test_slack_incidents import IDENTITY, SCOPE, observation, success

NEW_REVISION = "b" * 40
CLAIMED_AT = "2026-10-06T09:30:00Z"


def cold_load(ledger, tmp_path):
    # Preserve every serialized checkpoint; substitute only external signature
    # trust. Actual state validation, latest selection and ledger loading run.
    records = json.loads(json.dumps([
        {"id": number, "payload": {"schema": incidents.SCHEMA, "state": state}}
        for number, (_, state) in enumerate(ledger.writes, 1)
    ]))
    api = mock.Mock()
    api.pages.return_value = records
    reader = incidents.Ledger(api, {}, tmp_path)

    def verified(record):
        return copy.deepcopy(incidents.validate_state(record["payload"]["state"]))

    with mock.patch.object(reader, "verify", side_effect=verified):
        loaded = PersistedLedger(reader.load())
    loaded.writes = copy.deepcopy(ledger.writes)
    loaded.run = copy.deepcopy(ledger.run)
    return loaded


def dispatch(ledger, command, submitted_at, *, processed_at=None, revision=NEW_REVISION,
             human="jonaraphael", conversation="codex://threads/origin"):
    value = request(command, revision=revision, human=human)
    value["conversation_ref"] = conversation
    ledger.run.update(created_at=submitted_at, actor={"login": human})
    return incidents.supervise(ledger, IDENTITY, value, now=processed_at or submitted_at)


def resolved_owner(tmp_path):
    ledger = active_owner()
    recovery = success(time="2026-10-06T09:00:00Z")
    assert ingest(ledger, [recovery], recovery["time"]) == []
    return cold_load(ledger, tmp_path)


def prospective_owner(tmp_path):
    ledger = resolved_owner(tmp_path)
    assert dispatch(ledger, "claim", CLAIMED_AT) == []
    ledger = cold_load(ledger, tmp_path)
    owner = ledger.states[SCOPE]["supervision"][SCOPE]
    assert owner["registered_at"] == CLAIMED_AT
    assert owner["first_failure_at"] is owner["deadline"] is None
    return ledger


@pytest.mark.parametrize("command,status", [("check_in", "active"), ("release", "released"),
                                            ("supersede", "superseded"), ("claim", "registered")])
def test_fresh_prospective_owner_survives_historical_resolution_and_cold_reload(tmp_path, command, status):
    ledger = prospective_owner(tmp_path)
    original = copy.deepcopy(ledger.states[SCOPE]["incident"])
    assert dispatch(ledger, command, "2026-10-06T09:31:00Z") == []
    ledger = cold_load(ledger, tmp_path)
    owner = ledger.states[SCOPE]["supervision"][SCOPE]
    assert owner["registered_at"] == CLAIMED_AT
    assert owner["first_failure_at"] is owner["deadline"] is None
    assert owner["status"] == status
    assert owner["revision"] == NEW_REVISION
    assert ledger.states[SCOPE]["incident"] == original


@pytest.mark.parametrize("field,value,message", [
    ("human", "other-human", "cannot transfer human responsibility"),
    ("conversation", "codex://threads/another", "originating conversation"),
])
def test_repeated_prospective_claim_cannot_replace_human_or_originating_chat(tmp_path, field, value, message):
    ledger = prospective_owner(tmp_path)
    previous = copy.deepcopy(ledger.states)
    with pytest.raises(policy.PolicyError, match=message):
        dispatch(ledger, "claim", "2026-10-06T09:31:00Z", **{field: value})
    assert ledger.states == previous


def test_repeated_claim_preserves_original_registration_after_explicit_check_in(tmp_path):
    ledger = prospective_owner(tmp_path)
    dispatch(ledger, "check_in", "2026-10-06T09:31:00Z")
    ledger = cold_load(ledger, tmp_path)
    assert dispatch(ledger, "claim", "2026-10-06T09:32:00Z") == []
    owner = cold_load(ledger, tmp_path).states[SCOPE]["supervision"][SCOPE]
    assert owner["registered_at"] == CLAIMED_AT
    assert owner["first_failure_at"] is owner["deadline"] is None
    # A prospective claim remains registration, never fabricated active work.
    assert owner["status"] == "registered" and owner["last_check_in"] is None


def test_only_explicit_claim_can_retire_historical_bound_ownership(tmp_path):
    ledger = resolved_owner(tmp_path)
    original = copy.deepcopy(ledger.states[SCOPE]["supervision"][SCOPE])
    assert dispatch(ledger, "check_in", "2026-10-06T09:31:00Z", revision=original["revision"]) == []
    ledger = cold_load(ledger, tmp_path)
    assert dispatch(ledger, "release", "2026-10-06T12:00:00Z", revision=original["revision"]) == []
    owner = cold_load(ledger, tmp_path).states[SCOPE]["supervision"][SCOPE]
    assert owner["status"] == "released"
    assert all(owner[key] == original[key] for key in ("registered_at", "first_failure_at", "deadline"))


def test_queued_claim_uses_authenticated_submission_time_for_stable_boundary(tmp_path):
    ledger = resolved_owner(tmp_path)
    original = copy.deepcopy(ledger.states[SCOPE]["supervision"][SCOPE])
    assert dispatch(ledger, "claim", "2026-10-06T09:29:59Z", processed_at="2026-10-06T09:40:00Z") == []
    owner = cold_load(ledger, tmp_path).states[SCOPE]["supervision"][SCOPE]
    assert all(owner[key] == original[key] for key in ("registered_at", "first_failure_at", "deadline"))
    assert owner["updated_at"] == "2026-10-06T09:29:59Z"
    assert owner["last_check_in"] == original["last_check_in"]


def test_queued_before_boundary_claim_cannot_transfer_owner_when_processed_later(tmp_path):
    ledger = resolved_owner(tmp_path)
    previous = copy.deepcopy(ledger.states)
    with pytest.raises(policy.PolicyError, match="cannot transfer human responsibility"):
        dispatch(ledger, "claim", "2026-10-06T09:29:59Z", processed_at="2026-10-06T09:40:00Z", human="other-human")
    assert ledger.states == previous


def test_explicit_claim_at_stable_boundary_may_register_a_new_responsible_owner(tmp_path):
    ledger = resolved_owner(tmp_path)
    assert dispatch(ledger, "claim", CLAIMED_AT, human="other-human", conversation="codex://threads/new-work") == []
    owner = cold_load(ledger, tmp_path).states[SCOPE]["supervision"][SCOPE]
    assert owner["responsible_human"] == "other-human" and owner["conversation_ref"] == "codex://threads/new-work"
    assert owner["registered_at"] == CLAIMED_AT
    assert owner["first_failure_at"] is owner["deadline"] is None


def test_new_failure_binds_surviving_owner_once_and_repair_claims_cannot_renew_clock(tmp_path):
    ledger = prospective_owner(tmp_path)
    dispatch(ledger, "check_in", "2026-10-06T09:59:00Z")
    ledger = cold_load(ledger, tmp_path)
    failure = observation(run_id=104, sha=NEW_REVISION, time="2026-10-06T10:00:00Z")
    assert ingest(ledger, [failure], failure["time"], {
        incidents.observation_key(failure): fact(failure, reason="deterministic_defect", recovery="none")}) == []
    ledger = cold_load(ledger, tmp_path)
    assert dispatch(ledger, "check_in", "2026-10-06T10:05:00Z") == []
    ledger = cold_load(ledger, tmp_path)
    assert dispatch(ledger, "claim", "2026-10-06T10:06:00Z", revision="c" * 40) == []
    ledger = cold_load(ledger, tmp_path)
    owner = ledger.states[SCOPE]["supervision"][SCOPE]
    assert owner["registered_at"] == CLAIMED_AT
    assert owner["first_failure_at"] == failure["time"] and owner["deadline"] == "2026-10-06T12:00:00Z"
    assert owner["last_check_in"] == "2026-10-06T10:05:00Z"
    previous = copy.deepcopy(ledger.states)
    with pytest.raises(policy.PolicyError, match="original supervision deadline has expired"):
        dispatch(ledger, "claim", "2026-10-06T12:00:00Z", revision="d" * 40)
    assert ledger.states == previous


def test_unresolved_episode_never_discards_expired_bound_owner(tmp_path):
    ledger = cold_load(active_owner(), tmp_path)
    previous = copy.deepcopy(ledger.states)
    with pytest.raises(policy.PolicyError, match="original supervision deadline has expired"):
        dispatch(ledger, "claim", "2026-10-06T12:00:00Z")
    assert ledger.states == previous
