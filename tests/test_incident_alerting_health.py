"""The independent health report exposes disabled event incident alerting."""
import json
from datetime import timedelta
import sys
from unittest import mock

import pytest
import yaml

from scripts import operational_safety_check as safety, slack_notify
from test_operational_safety_check import NOW, OperationalAPI


@pytest.mark.parametrize("enabled,token,findings", [
    ("true", "configured-test-token", 0), ("false", "configured-test-token", 1),
    ("", "configured-test-token", 1), (None, "configured-test-token", 1),
    ("true", "", 1), ("true", " \t", 1), ("false", "", 2),
])
def test_alerting_configuration_is_reported_and_deduplicated(tmp_path, monkeypatch, enabled, token, findings):
    api = OperationalAPI()
    monkeypatch.setattr(safety, "GitHub", lambda: api)
    monkeypatch.setattr(safety, "context", lambda *_: api.current)
    monkeypatch.setattr(safety, "policy_warnings", lambda _: ([], []))
    monkeypatch.setattr(safety, "audit_freshness", lambda *_a, **_k: [])
    monkeypatch.setenv("SAFETY_PROBE_OUTCOME", "failure")
    if enabled is None:
        monkeypatch.delenv("SHARED_DATASETS_SLACK_INCIDENTS_ENABLED", raising=False)
    else:
        monkeypatch.setenv("SHARED_DATASETS_SLACK_INCIDENTS_ENABLED", enabled)
    monkeypatch.setenv("SHARED_DATASETS_SLACK_BOT_TOKEN", token)
    monkeypatch.setenv("SHARED_DATASETS_SLACK_INCIDENTS_SINCE", "2026-10-06T17:28:14Z")
    monkeypatch.setattr(sys, "argv", ["safety", "--health", "--work-dir", str(tmp_path)])
    previous = {"problems": [], "warnings": [], "notified_at": None}
    with mock.patch.object(safety, "freshness", return_value=[]), mock.patch.object(slack_notify, "notify") as notify:
        for hour, expected_sends in [(0, 1), (3, 1), (24, 2)]:
            class Clock(type(NOW)):
                @classmethod
                def now(cls, _zone):
                    return NOW + timedelta(hours=hour)
            monkeypatch.setattr(safety, "datetime", Clock)
            monkeypatch.setattr(safety, "previous_health", lambda *_: previous)
            safety.main()
            previous = json.loads((tmp_path / "health/cache.json").read_text())
            assert sum(message.startswith("Event incident alerting") for message in previous["problems"]) == findings
            assert previous["scope"] == safety.HEALTH_SCOPE
            assert notify.call_count == expected_sends
            assert token.strip() not in json.dumps(previous) if token.strip() else True
        monkeypatch.setenv("SHARED_DATASETS_SLACK_INCIDENTS_ENABLED", "true")
        monkeypatch.setenv("SHARED_DATASETS_SLACK_BOT_TOKEN", "replacement-test-token")
        safety.main()
        current = json.loads((tmp_path / "health/cache.json").read_text())
        assert not any(message.startswith("Event incident alerting") for message in current["problems"])
        assert notify.call_count == (3 if findings else 2)  # Configuration recovery changes the report immediately.


def test_independent_health_step_receives_kill_switch_and_bot_availability():
    workflow = yaml.safe_load((safety.ROOT / ".github/workflows/operational-safety-check.yml").read_text())
    job = workflow["jobs"]["safety"]
    health = next(step for step in job["steps"] if step.get("id") == "health")
    assert "SHARED_DATASETS_SLACK_INCIDENTS_ENABLED" not in job.get("if", "")
    assert health["env"]["SHARED_DATASETS_SLACK_INCIDENTS_ENABLED"] == "${{ vars.SHARED_DATASETS_SLACK_INCIDENTS_ENABLED }}"
    assert health["env"]["SHARED_DATASETS_SLACK_BOT_TOKEN"] == "${{ secrets.SHARED_DATASETS_SLACK_BOT_TOKEN }}"
    assert health["env"]["SHARED_DATASETS_SLACK_WEBHOOK_URL"] == "${{ secrets.SHARED_DATASETS_SLACK_WEBHOOK_URL }}"
