"""A released healthy operation must not reserve every future chat forever."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import operational_policy as policy, slack_incidents as incidents
from test_slack_incident_policy import PersistedLedger, fact, ingest
from test_slack_incidents import IDENTITY, SCOPE, observation, success
from test_slack_supervision_episode_reset import cold_load

ROOT = Path(__file__).resolve().parents[1]
OLD_REVISION, NEW_REVISION = "b" * 40, "c" * 40
OLD_CHAT, NEW_CHAT = "codex://threads/original", "codex://threads/new-operation"


def cli_request(command, *, revision=OLD_REVISION, pr=300, human="jonaraphael", chat=OLD_CHAT, target="pmtiles-cdn"):
    args = [sys.executable, str(ROOT / "scripts/slack_supervision.py"), command,
            "--target", target, "--revision", revision, "--human", human,
            "--conversation", chat, "--current-action", "Verify this exact deployment and terminal evidence.", "--dry-run"]
    if pr is not None:
        args.extend(["--pr", str(pr)])
    if command in {"release", "supersede"}:
        args.extend(["--handoff-ref", chat])
    if command == "clarification":
        args.extend(["--question-ref", chat + "#question-1"])
    return json.loads(subprocess.run(args, text=True, capture_output=True, check=True).stdout)


def dispatch(ledger, command, submitted_at, *, processed_at=None, pr_response=None, **kwargs):
    request = cli_request(command, **kwargs)
    ledger.run.update(created_at=submitted_at, actor={"login": request["responsible_human"]})
    ledger.api.get.side_effect = lambda path: pr_response if pr_response is not None else {
        "merged": True, "merge_commit_sha": OLD_REVISION if path.endswith("/300") else NEW_REVISION,
        "base": {"ref": "main", "repo": {"full_name": incidents.REPOSITORY}},
    }
    return incidents.supervise(ledger, IDENTITY, request, now=processed_at or submitted_at)


def released_healthy(tmp_path, *, historical=False, release=True):
    ledger = PersistedLedger()
    assert dispatch(ledger, "claim", "2026-10-06T08:00:00Z") == []
    if historical:
        failure = observation()
        ingest(ledger, [failure], failure["time"], {incidents.observation_key(failure): fact(failure)})
        recovered = success(sha=OLD_REVISION, time="2026-10-06T09:00:00Z")
        assert ingest(ledger, [recovered], recovered["time"]) == []
    if release:
        assert dispatch(ledger, "release", "2026-10-06T09:01:00Z") == []
    return cold_load(ledger, tmp_path)


@pytest.mark.parametrize("historical", [False, True])
def test_actual_cli_new_merged_operation_after_healthy_release_can_use_new_chat(tmp_path, historical):
    ledger = released_healthy(tmp_path, historical=historical)
    original = deepcopy(ledger.states[SCOPE])
    historical_checkpoints = deepcopy(ledger.writes)
    assert dispatch(ledger, "claim", "2026-10-06T09:30:00Z", revision=NEW_REVISION, pr=301, chat=NEW_CHAT) == []
    ledger = cold_load(ledger, tmp_path)
    state = ledger.states[SCOPE]
    owner = state["supervision"][SCOPE]
    assert owner["conversation_ref"] == NEW_CHAT and owner["responsible_human"] == "jonaraphael"
    assert owner["revision"] == NEW_REVISION and owner["pr_number"] == 301
    assert owner["status"] == "registered" and owner["registered_at"] == "2026-10-06T09:30:00Z"
    assert owner["last_check_in"] is owner["first_failure_at"] is owner["deadline"] is None
    assert owner["covered_bindings"] == []
    assert {key: value for key, value in state.items() if key != "supervision"} == {
        key: value for key, value in original.items() if key != "supervision"}
    assert ledger.writes[:len(historical_checkpoints)] == historical_checkpoints
    assert dispatch(ledger, "check-in", "2026-10-06T09:31:00Z", revision=NEW_REVISION, pr=301, chat=NEW_CHAT) == []
    assert cold_load(ledger, tmp_path).states[SCOPE]["supervision"][SCOPE]["status"] == "active"


@pytest.mark.parametrize("defect", ["same-binding", "same-revision", "missing-pr", "same-pr", "different-human",
                                  "registered", "active", "clarification", "superseded", "backdated", "wrong-pr"])
def test_new_chat_does_not_bypass_existing_owner_or_exact_merged_binding(tmp_path, defect):
    ledger = released_healthy(tmp_path, release=defect not in {"registered", "active", "clarification", "superseded"})
    if defect in {"active", "clarification", "superseded"}:
        dispatch(ledger, {"active": "check-in", "clarification": "clarification", "superseded": "supersede"}[defect], "2026-10-06T09:01:00Z")
        ledger = cold_load(ledger, tmp_path)
    before, writes = deepcopy(ledger.states), deepcopy(ledger.writes)
    kwargs = {"revision": NEW_REVISION, "pr": 301, "chat": NEW_CHAT}
    if defect in {"same-binding", "same-revision"}:
        kwargs["revision"] = OLD_REVISION
    if defect in {"same-binding", "same-pr"}:
        kwargs["pr"] = 300
    if defect == "missing-pr":
        kwargs["pr"] = None
    if defect == "different-human":
        kwargs["human"] = "other-human"
    if defect == "wrong-pr":
        kwargs["pr"] = 999
        kwargs["revision"] = "d" * 40
    submitted = "2026-10-06T08:59:59Z" if defect == "backdated" else "2026-10-06T09:30:00Z"
    with pytest.raises((policy.PolicyError, incidents.IncidentError)):
        dispatch(ledger, "claim", submitted, processed_at="2026-10-06T10:00:00Z", **kwargs)
    assert ledger.states == before and ledger.writes == writes


def test_historical_recovery_window_uses_dispatch_time_not_late_processing(tmp_path):
    ledger = released_healthy(tmp_path, historical=True)
    before = deepcopy(ledger.states)
    with pytest.raises(policy.PolicyError, match="originating conversation"):
        dispatch(ledger, "claim", "2026-10-06T09:29:59Z", processed_at="2026-10-06T10:00:00Z",
                 revision=NEW_REVISION, pr=301, chat=NEW_CHAT)
    assert ledger.states == before


def test_unresolved_old_failure_cannot_start_new_operation_or_reset_escalation(tmp_path):
    ledger = released_healthy(tmp_path)
    failure = observation()
    ingest(ledger, [failure], "2026-10-06T09:20:00Z")
    before = deepcopy(ledger.states)
    with pytest.raises(policy.PolicyError, match="originating conversation"):
        dispatch(ledger, "claim", "2026-10-06T09:30:00Z", revision=NEW_REVISION, pr=301, chat=NEW_CHAT)
    assert ledger.states == before


@pytest.mark.parametrize("defect", ["empty", "malformed", "unmerged", "wrong-revision", "wrong-branch", "wrong-repository", "same-revision", "same-pr"])
def test_new_operation_requires_independent_exact_merged_pr_and_new_binding(tmp_path, defect):
    ledger = released_healthy(tmp_path)
    before, writes = deepcopy(ledger.states), deepcopy(ledger.writes)
    revision, pr = NEW_REVISION, 301
    response = {"merged": True, "merge_commit_sha": revision,
                "base": {"ref": "main", "repo": {"full_name": incidents.REPOSITORY}}}
    if defect == "empty":
        response = {}
    elif defect == "malformed":
        response["base"] = {}
    elif defect == "unmerged":
        response["merged"] = False
    elif defect == "wrong-revision":
        response["merge_commit_sha"] = "d" * 40
    elif defect == "wrong-branch":
        response["base"]["ref"] = "feature"
    elif defect == "wrong-repository":
        response["base"]["repo"]["full_name"] = "other/repository"
    elif defect == "same-revision":
        revision = response["merge_commit_sha"] = OLD_REVISION
    else:
        pr = 300
    with pytest.raises((policy.PolicyError, incidents.IncidentError)):
        dispatch(ledger, "claim", "2026-10-06T09:30:00Z", revision=revision, pr=pr,
                 chat=NEW_CHAT, pr_response=response)
    assert ledger.states == before and ledger.writes == writes


@pytest.mark.parametrize("resolved,submitted", [(False, "2026-10-06T09:29:59Z"),
                                               (False, "2026-10-06T12:00:00Z"),
                                               (True, "2026-10-06T09:29:59Z")])
def test_released_owner_that_owned_failure_keeps_original_episode_constraints(tmp_path, resolved, submitted):
    ledger = PersistedLedger()
    dispatch(ledger, "claim", "2026-10-06T08:00:00Z")
    dispatch(ledger, "check-in", "2026-10-06T08:52:00Z")
    failure = observation(sha=OLD_REVISION)
    ingest(ledger, [failure], failure["time"], {incidents.observation_key(failure): fact(failure)})
    if resolved:
        recovered = success(sha=OLD_REVISION, time="2026-10-06T09:00:00Z")
        ingest(ledger, [recovered], recovered["time"])
    dispatch(ledger, "release", "2026-10-06T09:01:00Z")
    ledger = cold_load(ledger, tmp_path)
    before, writes = deepcopy(ledger.states), deepcopy(ledger.writes)
    owner = before[SCOPE]["supervision"][SCOPE]
    assert owner["first_failure_at"] == failure["time"] and owner["deadline"] == "2026-10-06T10:52:58Z"
    with pytest.raises(policy.PolicyError, match="originating conversation"):
        dispatch(ledger, "claim", submitted, revision=NEW_REVISION, pr=301, chat=NEW_CHAT)
    assert ledger.states == before and ledger.writes == writes


def test_new_deployment_owner_cannot_take_over_old_inherited_cloud_episode(tmp_path):
    from scripts import cloud_incident_adapter as adapter
    from test_cloud_incident_adapter import JOB, NOW, SUBJECT, at, correlation, source

    scope = "deployment:" + JOB
    ledger = PersistedLedger()
    dispatch(ledger, "claim", at(-60), target=JOB)
    dispatch(ledger, "release", at(-59), target=JOB)
    old_fact = source(kind="identity_decision", status="required", subject=SUBJECT,
                      details={"ambiguity_count": 1, "case_set_sha256": "1" * 64})
    old_observation = adapter.normalize(old_fact, correlation=correlation())
    states = ledger.load()
    inherited = incidents.fold(None, old_observation, IDENTITY)
    states[inherited["scope"]] = inherited
    incidents.inherit_cloud_supervision(states, IDENTITY, [old_observation], now=NOW)
    incidents.latch_escalation(inherited, NOW)
    ledger.save(inherited, "claims")
    ledger = cold_load(ledger, tmp_path)
    original = deepcopy(ledger.states[inherited["scope"]])
    assert dispatch(ledger, "claim", at(1), revision=NEW_REVISION, pr=301, chat=NEW_CHAT, target=JOB) == []
    ledger = cold_load(ledger, tmp_path)
    assert ledger.states[inherited["scope"]] == original
    owner = ledger.states[scope]["supervision"][scope]
    assert owner["covered_bindings"] == [] and owner["last_check_in"] is owner["deadline"] is None
    assert not policy.covers_binding(owner, policy.OperationBinding(scope, JOB, OLD_REVISION, 300))
    new_fact = source("new-revision", kind="identity_decision", status="required", subject=SUBJECT,
                      details={"ambiguity_count": 2, "case_set_sha256": "2" * 64}, time=at(1))
    new_observation = adapter.normalize(new_fact, correlation={**correlation(), "revision": NEW_REVISION})
    incidents.inherit_cloud_supervision(ledger.states, IDENTITY, [old_observation, new_observation], now=at(1))
    assert ledger.states[inherited["scope"]] == original
