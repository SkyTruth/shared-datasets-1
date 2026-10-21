"""Replay routing through strict serialized facts, with explicit fake time."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from scripts import operational_policy as p


BASE = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
BINDING = p.OperationBinding("deployment:catalog-web", "catalog-web", "a" * 40, 17)
CHAT = "codex://threads/00000000-0000-0000-0000-000000000001"
HUMAN = "maintainer"
AUTHORITY = {"actor": HUMAN, "repository": p.REPOSITORY, "workflow": p.WORKFLOW,
             "ref": "refs/heads/main", "executor_sha": "b" * 40, "run_id": 12, "attempt": 1}


def at(seconds=0):
    return p.timestamp(BASE + timedelta(seconds=seconds))


def observation(**changes):
    value = {**BINDING.to_dict(), "source_id": "github:123:1:456", "observed_at": at(),
             "phase": "preparation", "reason": "unknown", "mutation_status": "not_started", "recovery": "none",
             "required_action": "Inspect the exact failed attempt and repair the cause.", "impact": "Impact is unknown.",
             "published_data_state": "Published data is unchanged.", "evidence": ["https://github.com/SkyTruth/shared-datasets-1/actions/runs/123"]}
    value.update(changes)
    # Exercise the actual JSON representation consumed by the signed ledger.
    return p.OperationalObservation.from_dict(json.loads(json.dumps(value)))


def transition(previous, command, seconds=0, binding=BINDING, **changes):
    arguments = {"now": at(seconds), "authority": AUTHORITY, "responsible_human": HUMAN,
                 "conversation_ref": CHAT, "current_action": "Inspecting exact run evidence."}
    arguments.update(changes)
    return p.transition_supervision(previous, command, binding, **arguments)


def supervision(*, active=True):
    record = transition(None, "claim", -60)
    if active:
        record = transition(record, "check_in")
    return p.bind_supervision(record, BINDING, first_failure_at=at(), now=at())


def decide(value=None, seconds=0, record=None, **changes):
    arguments = {"first_failure_at": at(), "now": at(seconds), "supervision": record, "ownership_verified": True}
    arguments.update(changes)
    return p.decide(value or observation(), **arguments)


def test_registry_classifies_every_explicit_reason_and_preserves_security_route():
    policy = p.load_policy()
    assert set(policy.reasons) == set(p.Reason)
    assert (policy.transient_recovery_seconds, policy.supervision_seconds, policy.check_in_seconds,
            policy.stable_recovery_seconds) == (1800, 7200, 600, 1800)
    with pytest.raises(TypeError):
        policy.reasons[p.Reason.UNKNOWN] = "record_only"


@pytest.mark.parametrize("change", ["extra", "missing-reason", "unknown-reason", "bool-clock", "duplicate", "security-route"])
def test_invalid_registry_cannot_hide_unsupported_or_security_observations(tmp_path, change):
    value = json.loads(p.REGISTRY.read_text())
    if change == "extra":
        value["unreviewed"] = True
    elif change == "missing-reason":
        del value["reasons"]["unknown"]
    elif change == "unknown-reason":
        value["reasons"]["another failure"] = "record_only"
    elif change == "bool-clock":
        value["supervision_seconds"] = True
    elif change == "security-route":
        value["reasons"]["security_risk"] = "record_only"
    encoded = json.dumps(value)
    if change == "duplicate":
        encoded = encoded.replace('"supervision_seconds": 7200', '"supervision_seconds": 7200, "supervision_seconds": 7201')
    path = tmp_path / "registry.json"
    path.write_text(encoded)
    with pytest.raises(p.PolicyError):
        p.load_policy(path)


@pytest.mark.parametrize("reason", ["healthy", "unchanged", "superseded", "source_not_ready"])
def test_expected_outcomes_are_quiet_without_creating_a_recovery_claim(reason):
    decision = decide(observation(reason=reason), seconds=90000)
    assert decision.disposition == p.Disposition.RECORD_ONLY
    assert not decision.retry_safe and decision.eligible_at is None


def test_transient_recovery_before_30_minutes_produces_no_public_request():
    value = observation(reason="transient_transport", phase="read", mutation_status="not_required", recovery="read_only_retry")
    for second in (0, 5, 20, 60, 180, 420, 900, 1740, 1799):
        result = decide(value, second)
        assert result.disposition == p.Disposition.RECOVER and result.retry_safe
        assert result.eligible_at == at(1800)
    result = decide(value, 1800)
    assert result.disposition == p.Disposition.ESCALATE and result.reason == "safe_recovery_exhausted"
    assert decide(observation(reason="healthy"), 1200).disposition == p.Disposition.RECORD_ONLY


@pytest.mark.parametrize("reason", ["deterministic_defect", "permission_denied", "missing_configuration", "unknown",
                                  "identity_decision_required", "translation_maintenance_required", "publication_receipt_failed"])
def test_actionable_failures_require_an_active_accountable_owner(reason):
    value = observation(reason=reason)
    assert decide(value).disposition == p.Disposition.ESCALATE
    assert decide(value, 1, supervision()).disposition == p.Disposition.OWNER_HELD
    assert decide(value, 1, supervision(active=False)).disposition == p.Disposition.ESCALATE


def test_pending_prerequisite_is_deferred_without_substituting_revision_or_authorizing_mutation():
    value = observation(reason="prerequisite_pending", recovery="wait_for_prerequisite")
    result = decide(value, 1799)
    assert result.disposition == p.Disposition.RECOVER
    assert value.revision == BINDING.revision and value.mutation_status == p.MutationStatus.NOT_STARTED
    assert decide(value, 1800).disposition == p.Disposition.ESCALATE


def test_accepted_artifact_visibility_is_pending_without_claiming_a_transport_failure():
    value = observation(reason="artifact_visibility_pending", phase="verification", mutation_status="completed", recovery="npm_visibility")
    assert decide(value, 1799).disposition == p.Disposition.RECOVER
    assert decide(value, 1800).disposition == p.Disposition.ESCALATE
    assert decide(replace(value, mutation_status=p.MutationStatus.NOT_STARTED)).disposition == p.Disposition.ESCALATE


@pytest.mark.parametrize("reason", ["security_risk", "data_integrity_risk", "unapproved_mutation"])
def test_security_and_demonstrated_integrity_bypass_active_and_clarification_holds(reason):
    record = transition(supervision(), "clarification", 10, question_ref=CHAT + "#question-1")
    result = decide(observation(reason=reason), 10, record)
    assert result.disposition == p.Disposition.INDEPENDENT_SECURITY
    assert result.freeze_replay and not result.retry_safe and result.eligible_at == at()


def test_unknown_mutation_freezes_replay_while_accountable_owner_inspects_privately():
    value = observation(reason="transient_transport", phase="mutation", mutation_status="uncertain", recovery="read_only_retry")
    result = decide(value, 1, supervision())
    assert result.disposition == p.Disposition.OWNER_HELD and result.freeze_replay and not result.retry_safe
    unowned = decide(value)
    assert unowned.disposition == p.Disposition.ESCALATE and unowned.freeze_replay and not unowned.retry_safe
    assert "inspect_authoritative" in unowned.reason
    stale = decide(value, 600, supervision())
    assert stale.disposition == p.Disposition.ESCALATE and stale.freeze_replay and not stale.retry_safe


@pytest.mark.parametrize("status,recovery,expected", [
    ("not_started", "before_mutation_lock_retry", True),
    ("completed", "before_mutation_lock_retry", False),
    ("in_progress", "before_mutation_lock_retry", False),
    ("completed", "receipt_only_repair", True),
    ("not_started", "receipt_only_repair", False),
    ("completed", "npm_visibility", True),
    ("uncertain", "npm_visibility", False),
    ("completed", "read_only_retry", True),
    ("in_progress", "read_only_retry", False),
    ("not_started", "none", False),
])
def test_transient_label_cannot_authorize_an_unsafe_operation_specific_replay(status, recovery, expected):
    result = decide(observation(reason="transient_transport", mutation_status=status, recovery=recovery))
    assert result.retry_safe is expected
    assert (result.disposition == p.Disposition.RECOVER) is expected


def test_prospective_registration_and_check_in_do_not_fabricate_failure_evidence():
    record = transition(None, "claim", -100)
    assert record["first_failure_at"] is record["deadline"] is record["last_check_in"] is None
    assert record["status"] == "registered"
    active = transition(record, "check_in", -1)
    assert active["first_failure_at"] is active["deadline"] is None
    assert decide(record=active).disposition == p.Disposition.ESCALATE  # Not yet bound to verified failure.
    bound = p.bind_supervision(active, BINDING, first_failure_at=at(), now=at())
    assert decide(record=bound).disposition == p.Disposition.OWNER_HELD
    assert bound["deadline"] == at(7200)


def test_check_in_stales_at_ten_minutes_even_during_transient_recovery_window():
    record = supervision()
    assert decide(seconds=599, record=record).disposition == p.Disposition.OWNER_HELD
    result = decide(seconds=600, record=record)
    assert result.disposition == p.Disposition.ESCALATE and result.reason == "supervision_check_in_stale"
    transient = observation(reason="transient_transport", recovery="read_only_retry")
    assert decide(transient, 600, record).disposition == p.Disposition.ESCALATE


def test_repeated_failures_check_ins_and_new_commits_do_not_restart_original_deadline():
    record = supervision()
    for second in range(500, 7001, 500):
        record = transition(record, "check_in", second)
        record = p.bind_supervision(record, BINDING, first_failure_at=at(second), now=at(second))
        assert record["deadline"] == at(7200) and record["first_failure_at"] == at()
    new_binding = replace(BINDING, revision="c" * 40, pr_number=18)
    record = transition(record, "claim", 7100, binding=new_binding)
    assert record["status"] == "registered" and record["deadline"] == at(7200)
    record = transition(record, "check_in", 7101, binding=new_binding)
    assert decide(observation(revision="c" * 40, pr_number=18), 7199, record).disposition == p.Disposition.OWNER_HELD
    assert decide(observation(revision="c" * 40, pr_number=18), 7200, record).disposition == p.Disposition.ESCALATE
    with pytest.raises(p.PolicyError, match="expired"):
        transition(record, "claim", 7200, binding=new_binding)


def test_verified_repair_rebind_keeps_original_failure_covered_without_renewing_check_in():
    original = supervision()
    repair = replace(BINDING, revision="c" * 40, pr_number=18)
    owner = transition(original, "claim", 100, binding=repair, verified_bindings=[BINDING])
    owner = json.loads(json.dumps(owner))
    assert owner["status"] == "active" and owner["last_check_in"] == at()
    assert owner["deadline"] == at(7200) and owner["covered_bindings"] == [BINDING.to_dict()]
    assert p.covers_binding(owner, BINDING) and p.covers_binding(owner, repair)
    assert not p.covers_binding(owner, replace(BINDING, revision="d" * 40))
    assert decide(seconds=100, record=owner).disposition == p.Disposition.OWNER_HELD
    assert decide(seconds=600, record=owner).disposition == p.Disposition.ESCALATE  # Claim did not refresh activity.
    owner = transition(owner, "check_in", 500, binding=repair)
    assert decide(seconds=600, record=owner).disposition == p.Disposition.OWNER_HELD
    assert owner["deadline"] == at(7200)


def test_prospective_guessed_revision_cannot_become_covered_by_repair_registration():
    original = supervision()
    repair = replace(BINDING, revision="c" * 40, pr_number=18)
    unrelated = replace(BINDING, revision="d" * 40)
    owner = transition(original, "claim", 100, binding=repair, verified_bindings=[unrelated])
    assert owner["status"] == "registered" and owner["covered_bindings"] == []
    assert not p.covers_binding(owner, BINDING)
    with pytest.raises(p.PolicyError, match="exact operation"):
        decide(seconds=100, record=owner)


def test_verified_repair_rebind_preserves_recorded_question_and_original_deadline():
    owner = transition(supervision(), "clarification", 10, question_ref=CHAT + "#question-1")
    repair = replace(BINDING, revision="c" * 40, pr_number=18)
    owner = transition(owner, "claim", 100, binding=repair, verified_bindings=[BINDING])
    assert owner["status"] == "awaiting_clarification" and owner["question_ref"] == CHAT + "#question-1"
    assert decide(seconds=3600, record=owner).disposition == p.Disposition.CLARIFICATION_WAIT
    assert decide(seconds=7200, record=owner).disposition == p.Disposition.ESCALATE


def test_covered_binding_history_cannot_change_operation_or_target():
    owner = supervision()
    owner["covered_bindings"] = [replace(BINDING, target="other", revision="c" * 40).to_dict()]
    with pytest.raises(p.PolicyError, match="operation/target"):
        p.validate_supervision(owner)


def test_newly_discovered_earlier_alias_tightens_but_cannot_extend_deadline():
    record = supervision()
    earlier = p.bind_supervision(record, BINDING, first_failure_at=at(-30), now=at(1))
    assert earlier["first_failure_at"] == at(-30) and earlier["deadline"] == at(7170)
    later = p.bind_supervision(earlier, BINDING, first_failure_at=at(120), now=at(120))
    assert later["first_failure_at"] == at(-30) and later["deadline"] == at(7170)


def test_recorded_clarification_pauses_check_ins_but_preserves_original_deadline_and_failure():
    record = transition(supervision(), "clarification", 10, question_ref=CHAT + "#question-1")
    for second in (610, 3600, 7199):
        result = decide(seconds=second, record=record)
        assert result.disposition == p.Disposition.CLARIFICATION_WAIT and result.eligible_at == at(7200)
    assert decide(seconds=7200, record=record).disposition == p.Disposition.ESCALATE
    assert record["first_failure_at"] == at() and record["deadline"] == at(7200)
    assert decide(observation(reason="healthy"), 50, record).disposition == p.Disposition.RECORD_ONLY


def test_human_answer_requires_explicit_check_in_to_resume_active_ownership():
    record = transition(supervision(), "clarification", 10, question_ref=CHAT + "#question-1")
    answered = transition(record, "check_in", 3600, current_action="Applying the clarified approved recovery.")
    assert answered["question_ref"] is None and answered["deadline"] == at(7200)
    assert decide(seconds=3601, record=answered).disposition == p.Disposition.OWNER_HELD


@pytest.mark.parametrize("command", ["release", "supersede"])
def test_released_or_superseded_ownership_escalates_without_waiting_for_transient_budget(command):
    record = transition(supervision(), command, 20, handoff_ref=CHAT + "#handoff-1")
    transient = observation(reason="transient_transport", recovery="read_only_retry")
    assert decide(transient, 20, record).disposition == p.Disposition.ESCALATE
    with pytest.raises(p.PolicyError, match="registration"):
        transition(record, "check_in", 21)


def test_late_owner_registration_cannot_retract_an_existing_public_incident():
    result = decide(seconds=1, record=supervision(), public_parent_exists=True)
    assert result.disposition == p.Disposition.ESCALATE and result.reason == "update_existing_actionable_parent"


def test_unverified_supervision_cannot_hold_or_authorize_recovery():
    forged = supervision()
    assert decide(record=forged, ownership_verified=False).disposition == p.Disposition.ESCALATE
    assert decide(record={"forged": True}, ownership_verified=False).disposition == p.Disposition.ESCALATE


@pytest.mark.parametrize("field,value", [("revision", "c" * 40), ("target", "other"), ("pr_number", 18), ("operation_id", "deployment:other")])
def test_signed_ownership_for_another_operation_cannot_hold_this_failure(field, value):
    record = {**supervision(), field: value}
    with pytest.raises(p.PolicyError, match="exact operation"):
        decide(record=record)


@pytest.mark.parametrize("field,value", [("ref", "refs/heads/feature"), ("actor", "attacker"), ("repository", "other/repository"),
                                        ("workflow", ".github/workflows/ci.yml"), ("executor_sha", "not-a-revision"), ("run_id", True)])
def test_untrusted_worker_metadata_is_rejected_before_ownership_registration(field, value):
    with pytest.raises(p.PolicyError):
        transition(None, "claim", authority={**AUTHORITY, field: value})


@pytest.mark.parametrize("change", ["future-clock", "reset-deadline", "registered-active", "clarification-other-chat", "unknown-status", "extra-field"])
def test_forged_or_inconsistent_persisted_ownership_never_becomes_a_hold(change):
    record = supervision()
    if change == "future-clock":
        record["updated_at"] = at(1)
    elif change == "reset-deadline":
        record["deadline"] = at(8000)
    elif change == "registered-active":
        record["status"] = "registered"
    elif change == "clarification-other-chat":
        record.update(status="awaiting_clarification", question_ref="codex://threads/other#question-1")
    elif change == "unknown-status":
        record["status"] = "trust me"
    else:
        record["verified"] = True
    with pytest.raises(p.PolicyError):
        decide(record=record)


@pytest.mark.parametrize("field,value", [("reason", "looks like a safe transient"), ("mutation_status", "probably unchanged"),
                                        ("revision", "main"), ("pr_number", True), ("evidence", []), ("impact", ""),
                                        ("source_id", "not\na source")])
def test_observation_boundary_rejects_unsupported_or_unactionable_facts(field, value):
    with pytest.raises(p.PolicyError):
        observation(**{field: value})


def test_quiet_source_observation_cannot_conceal_uncertain_mutation_or_request_retry():
    with pytest.raises(p.PolicyError):
        observation(reason="healthy", mutation_status="uncertain")
    with pytest.raises(p.PolicyError):
        observation(reason="unchanged", recovery="read_only_retry")


def test_supervision_request_round_trip_excludes_caller_supplied_failure_clocks_or_authority():
    request = {"command": "claim", "binding": BINDING.to_dict(), "responsible_human": HUMAN,
               "conversation_ref": CHAT, "current_action": "Overseeing the exact deployment.", "question_ref": None, "handoff_ref": None}
    assert p.validate_supervision_request(json.loads(json.dumps(request))) == request
    for field, value in (("first_failure_at", at()), ("deadline", at(9999)), ("authority", AUTHORITY), ("ownership_verified", True),
                         ("covered_bindings", [BINDING.to_dict()]), ("verified_bindings", [BINDING.to_dict()])):
        with pytest.raises(p.PolicyError):
            p.validate_supervision_request({**request, field: value})


def test_clarification_request_must_name_the_question_in_the_originating_chat():
    request = {"command": "clarification", "binding": BINDING.to_dict(), "responsible_human": HUMAN,
               "conversation_ref": CHAT, "current_action": "Awaiting the publication decision.", "question_ref": CHAT + "#question-1", "handoff_ref": None}
    assert p.validate_supervision_request(request) == request
    for wrong in (None, CHAT, CHAT + "#", "codex://threads/other#question-1", CHAT + "#question\nforged"):
        with pytest.raises(p.PolicyError):
            p.validate_supervision_request({**request, "question_ref": wrong})


def test_serialized_observation_and_decision_preserve_explicit_classification():
    value = observation()
    assert p.OperationalObservation.from_dict(json.loads(json.dumps(value.to_dict()))) == value
    decision = decide(value)
    encoded = json.loads(json.dumps(decision.to_dict()))
    assert encoded == {"disposition": "escalate", "eligible_at": at(), "retry_safe": False,
                       "freeze_replay": False, "owner": None, "reason": "owner_action_required"}


def test_cloud_operation_can_bind_authoritative_image_digest_without_inventing_git_revision():
    digest = "sha256:" + "f" * 64
    value = observation(revision=digest, pr_number=None)
    assert value.binding.revision == digest
    with pytest.raises(p.PolicyError):
        observation(revision="image:latest")
    with pytest.raises(p.PolicyError):
        observation(revision="sha256:" + "F" * 64)
    with pytest.raises(p.PolicyError):
        transition(None, "claim", authority={**AUTHORITY, "executor_sha": digest})


def test_execution_uid_without_resolved_artifact_is_an_actionable_observation_only_anchor():
    anchor = "cloudrun-execution:12345678-1234-4234-9234-123456789abc"
    value = observation(revision=anchor, pr_number=None, reason="artifact_provenance_unverified",
                        mutation_status="uncertain", recovery="none")
    result = decide(value)
    assert result.disposition == p.Disposition.ESCALATE and result.freeze_replay and not result.retry_safe
    assert result.reason == "artifact_provenance_unverified"
    with pytest.raises(p.PolicyError, match="artifact revision"):
        transition(None, "claim", binding=value.binding)
    for changes in ({"reason": "unknown"}, {"mutation_status": "not_started"}, {"recovery": "read_only_retry"},
                    {"revision": "cloudrun-execution:legacy-uid"},
                    {"revision": "cloudrun-execution:00000000-0000-0000-0000-000000000000"}):
        with pytest.raises(p.PolicyError):
            p.OperationalObservation.from_dict({**value.to_dict(), **changes})
