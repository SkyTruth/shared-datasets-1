"""Reviewed history plus explicit ownership assumptions, never invented recovery."""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
import json
from pathlib import Path

from scripts import operational_policy as policy


HISTORY = json.loads((Path(__file__).parent / "fixtures/operational-alerts/2026-10-07-through-21.json").read_text())
AUTHORITY = {"actor": "responsible-human", "repository": policy.REPOSITORY, "workflow": policy.WORKFLOW,
             "ref": "refs/heads/main", "executor_sha": "a" * 40, "run_id": 10, "attempt": 1}
CHAT = "codex://threads/counterfactual-fixture"


def observation(row):
    return policy.OperationalObservation.from_dict({key: value for key, value in row.items()
        if key not in {"historical_parent", "verified_recovery_at"}})


def owned_until(row, until):
    source = observation(row)
    first = policy.instant(row["observed_at"])
    owner = policy.transition_supervision(None, "claim", source.binding, now=policy.timestamp(first - timedelta(seconds=1)),
        authority=AUTHORITY, responsible_human=AUTHORITY["actor"], conversation_ref=CHAT, current_action=row["required_action"])
    owner = policy.bind_supervision(owner, source.binding, first_failure_at=row["observed_at"], now=row["observed_at"])
    tick = first
    deadline = first + timedelta(seconds=policy.DEFAULT_POLICY.supervision_seconds)
    while tick <= until and tick < deadline:
        owner = policy.transition_supervision(owner, "check_in", source.binding, now=policy.timestamp(tick),
            authority=AUTHORITY, responsible_human=AUTHORITY["actor"], conversation_ref=CHAT, current_action=row["required_action"])
        tick += timedelta(minutes=9)
    return policy.decide(source, first_failure_at=row["observed_at"], now=policy.timestamp(until),
                         supervision=owner, ownership_verified=True)


def test_every_reviewed_parent_has_exact_failure_recovery_and_a_concrete_action():
    assert HISTORY["schema"] == "reviewed-alert-counterfactual-v1"
    assert len(HISTORY["incidents"]) == 19
    for row in HISTORY["incidents"]:
        source = observation(row)
        assert policy.instant(row["verified_recovery_at"]) > policy.instant(source.observed_at)
        assert source.evidence[0].startswith("https://github.com/SkyTruth/shared-datasets-1/actions/runs/")
        assert len(source.required_action) > 30


def test_counterfactual_does_not_invent_provider_recovery_inside_the_retry_window():
    # The observed green times were not provider-availability measurements.
    # This conservative unattended replay retains real unresolved work.
    for row in HISTORY["incidents"]:
        source = observation(row)
        deadline = policy.instant(source.observed_at) + timedelta(seconds=1800)
        decision = policy.decide(source, first_failure_at=source.observed_at, now=policy.timestamp(deadline))
        assert decision.disposition == policy.Disposition.ESCALATE


def test_accountable_owner_counterfactual_uses_recorded_terminal_times_and_fixed_deadlines():
    episodes = {}
    for row in HISTORY["incidents"]:
        key = row["operation_id"]
        if key not in episodes or row["observed_at"] < episodes[key]["observed_at"]:
            episodes[key] = row
    assert len(episodes) == 18  # The workflow/deployment duplicate is one episode.
    counts = Counter()
    for row in episodes.values():
        first, recovered = policy.instant(row["observed_at"]), policy.instant(row["verified_recovery_at"])
        deadline = first + timedelta(seconds=7200)
        if recovered < deadline:
            decision = owned_until(row, recovered - timedelta(seconds=1))
            assert decision.disposition == policy.Disposition.OWNER_HELD
            counts["owner_recovered_before_public_escalation"] += 1
        else:
            decision = owned_until(row, deadline)
            assert decision.disposition == policy.Disposition.ESCALATE
            assert decision.reason == "supervision_deadline_expired"
            counts["one_actionable_parent"] += 1
    assert counts == {"owner_recovered_before_public_escalation": 8, "one_actionable_parent": 10}
