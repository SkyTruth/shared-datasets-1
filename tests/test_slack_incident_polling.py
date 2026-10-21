"""Closed episodes retain proof; only open episodes poll authoritative recovery."""
from copy import deepcopy
from unittest.mock import Mock, patch

import pytest

from scripts import deployment_emission as emissions, slack_incidents as incidents
from test_slack_incidents import (IDENTITY, MemoryLedger, SCOPE, observation,
                                  signed_record, success)


def recovered(*, pending_update=False):
    value = incidents.fold(None, observation(), IDENTITY)
    if pending_update:
        value["incident"]["posts"]["parent"] = {"state": "delivered", "ts": "1791270000.000001"}
        value["incident"]["parent_view_sha256"] = incidents.parent_view_sha256(value)
    return incidents.fold(value, success(), IDENTITY, ancestor=lambda *_: True)


@pytest.mark.parametrize("pending_update", [False, True])
def test_resolved_episode_preserves_signed_recovery_without_polling(pending_update):
    value = recovered(pending_update=pending_update)
    ledger = MemoryLedger({SCOPE: value})
    read = Mock(side_effect=AssertionError("closed episode must not poll"))
    incidents.prepare(ledger, IDENTITY, [], success=read)
    read.assert_not_called()
    assert ledger.states[SCOPE]["incident"]["resolution"] == value["incident"]["resolution"]
    assert ledger.states[SCOPE]["watermarks"] == value["watermarks"]
    assert ledger.states[SCOPE]["stable_since"] == value["stable_since"]


def test_open_episode_still_polls_exact_authoritative_terminal_evidence():
    value = incidents.fold(None, observation(), IDENTITY)
    ledger = MemoryLedger({SCOPE: value})
    read = Mock(return_value=success())
    incidents.prepare(ledger, IDENTITY, [], success=read, ancestor=lambda *_: True)
    read.assert_called_once_with(ledger.api, SCOPE)
    assert ledger.states[SCOPE]["incident"]["resolution"] == success()


@pytest.mark.parametrize("failed_at,repaired_at,same_episode", [
    ("2026-10-06T10:20:00Z", "2026-10-06T10:21:00Z", True),
    ("2026-10-06T11:00:00Z", "2026-10-06T11:05:00Z", False),
])
def test_new_failure_reopens_before_terminal_poll(failed_at, repaired_at, same_episode):
    value = recovered()
    ledger = MemoryLedger({SCOPE: value})
    failed = observation(sha="b" * 40, run_id=102, time=failed_at)
    repaired = success(sha="c" * 40, run_id=103, time=repaired_at)
    read = Mock(return_value=repaired)
    incidents.prepare(ledger, IDENTITY, [failed], success=read, ancestor=lambda *_: True)
    read.assert_called_once_with(ledger.api, SCOPE)
    current = ledger.states[SCOPE]
    assert (current["incident"]["id"] == value["incident"]["id"]) is same_episode
    assert current["incident"]["resolution"] == repaired
    assert current["first_failure_at"] == (observation()["time"] if same_episode else failed_at)


def test_old_green_cannot_close_a_later_reopened_failure():
    ledger = MemoryLedger({SCOPE: recovered()})
    failed = observation(sha="b" * 40, run_id=102, time="2026-10-06T11:00:00Z")
    read = Mock(return_value=success())
    incidents.prepare(ledger, IDENTITY, [failed], success=read, ancestor=lambda *_: True)
    read.assert_called_once_with(ledger.api, SCOPE)
    assert ledger.states[SCOPE]["incident"]["resolution"] is None
    assert ledger.states[SCOPE]["first_failure_at"] == failed["time"]


def test_verified_source_success_can_advance_closed_watermark_without_polling():
    value = recovered()
    ledger = MemoryLedger({SCOPE: value})
    latest = success(sha="c" * 40, run_id=103, time="2026-10-06T11:00:00Z")
    read = Mock(side_effect=AssertionError("verified source already supplies recovery"))
    incidents.prepare(ledger, IDENTITY, [latest], success=read, ancestor=lambda *_: True)
    read.assert_not_called()
    assert ledger.states[SCOPE]["watermarks"][SCOPE] == latest
    assert ledger.states[SCOPE]["incident"]["resolution"] == value["incident"]["resolution"]
    assert ledger.states[SCOPE]["stable_since"] == value["stable_since"]


def test_open_correlated_episode_keeps_each_targets_recovery_requirement():
    other_scope = "deployment:catalog-web"
    other = observation(scope=other_scope, label="catalog-web", job_id=112187253800)
    value = incidents.fold(None, observation(), IDENTITY)
    value = incidents.fold(value, other, IDENTITY, correlate=True)
    value = incidents.fold(value, success(), IDENTITY, ancestor=lambda *_: True)
    assert value["incident"]["resolution"] is None
    ledger = MemoryLedger({SCOPE: value})
    other_success = success(scope=other_scope, label="catalog-web")
    read = Mock(side_effect=lambda _api, target: success() if target == SCOPE else other_success)
    incidents.prepare(ledger, IDENTITY, [], success=read, ancestor=lambda *_: True)
    assert {call.args[1] for call in read.call_args_list} == {SCOPE, other_scope}
    assert set(ledger.states[SCOPE]["recoveries"]) == {SCOPE, other_scope}
    assert ledger.states[SCOPE]["incident"]["resolution"] is not None


def verification_fixture(tmp_path):
    api = Mock()
    api.pages.return_value = [{"id": 12, "state": "success", "description": "applied"}]
    return incidents.Ledger(api, {}, tmp_path), api, signed_record()


def test_identical_receipt_reuses_signature_only_after_fresh_status_enumeration(tmp_path):
    ledger, api, record = verification_fixture(tmp_path)
    with patch.object(emissions, "verify") as verify:
        first = ledger.verify(record)
        first["incident"]["posts"]["parent"]["state"] = "delivered"
        second = ledger.verify(deepcopy(record))
    verify.assert_called_once()
    assert api.pages.call_count == 2
    assert second["incident"]["posts"]["parent"] == {"state": "claimed", "ts": None}
    assert record["payload"]["state"]["incident"]["posts"]["parent"]["state"] == "claimed"


def test_failed_signature_never_enters_the_instance_cache(tmp_path):
    ledger, _, record = verification_fixture(tmp_path)
    with patch.object(emissions, "verify", side_effect=[emissions.EmissionError("forged"), None]) as verify:
        with pytest.raises(emissions.EmissionError, match="forged"):
            ledger.verify(record)
        ledger.verify(record)
    assert verify.call_count == 2


@pytest.mark.parametrize("change", [
    lambda record: record.update(updated_at="2026-10-21T16:00:00Z"),
    lambda record: record["payload"]["state"].update(label="Changed exact evidence"),
    lambda record: record["payload"].update(execution_run_attempt=2),
])
def test_any_record_byte_change_requires_signature_verification_again(tmp_path, change):
    ledger, _, record = verification_fixture(tmp_path)
    with patch.object(emissions, "verify") as verify:
        ledger.verify(record)
        change(record)
        ledger.verify(record)
    assert verify.call_count == 2


def test_changed_success_status_bytes_require_signature_verification_again(tmp_path):
    ledger, api, record = verification_fixture(tmp_path)
    with patch.object(emissions, "verify") as verify:
        ledger.verify(record)
        api.pages.return_value = [{"id": 13, "state": "success", "description": "applied"}]
        ledger.verify(record)
    assert api.pages.call_count == verify.call_count == 2


@pytest.mark.parametrize("statuses", [[],
    [{"id": 12, "state": "failure", "description": "applied"}],
    [{"id": 12, "state": "success", "description": "applied"},
     {"id": 13, "state": "success", "description": "applied"}],
])
def test_cached_signature_cannot_hide_missing_failed_or_extra_status(tmp_path, statuses):
    ledger, api, record = verification_fixture(tmp_path)
    with patch.object(emissions, "verify") as verify:
        ledger.verify(record)
        api.pages.return_value = statuses
        with pytest.raises(incidents.IncidentError, match="incomplete"):
            ledger.verify(record)
    verify.assert_called_once()


def test_another_ledger_instance_cannot_reuse_verification(tmp_path):
    ledger, api, record = verification_fixture(tmp_path)
    with patch.object(emissions, "verify") as verify:
        ledger.verify(record)
        incidents.Ledger(api, {}, tmp_path).verify(record)
    assert verify.call_count == 2


def test_cached_signature_still_validates_the_current_state_schema(tmp_path):
    ledger, api, record = verification_fixture(tmp_path)
    with patch.object(emissions, "verify") as verify:
        ledger.verify(record)
        record["payload"]["state"]["unrecognized"] = True
        with pytest.raises(incidents.IncidentError, match="state schema"):
            ledger.verify(record)
    verify.assert_called_once()
    assert api.pages.call_count == 1


@pytest.mark.parametrize("context", ["REPOSITORY", "WORKFLOW"])
def test_changed_repository_or_signer_cannot_reuse_verification(tmp_path, context):
    ledger, _, record = verification_fixture(tmp_path)
    with patch.object(emissions, "verify") as verify:
        ledger.verify(record)
        with patch.object(incidents, context, "changed-verifier-context"):
            ledger.verify(record)
    assert verify.call_count == 2
