"""Explicit public transport inventory and dormant runtime bridge boundary."""
import ast
import json
from pathlib import Path
from unittest import mock

import pytest

from ingestion.common import legacy_runtime_notifications as bridge
from scripts import slack_breakglass, slack_notify

ROOT = Path(__file__).resolve().parents[1]


def test_capability_constructs_real_http_without_leaking_resolver(capsys):
    capability = slack_notify.webhook_capability("breaking_change", webhook_url="https://example.invalid/fixture")
    requests = []

    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    def opener(request, timeout):
        requests.append(request)
        assert timeout == 30
        return Response()

    slack_notify.post_webhook(capability, {"text": "reviewed notice"}, opener=opener)
    assert requests[0].method == "POST"
    assert json.loads(requests[0].data) == {"text": "reviewed notice"}
    assert "fixture" not in repr(capability)
    assert not capsys.readouterr().out


def test_generic_cli_is_preview_even_with_webhook_environment(monkeypatch, capsys):
    monkeypatch.setenv(slack_notify.WEBHOOK_ENV, "https://example.invalid/fixture")
    with mock.patch.object(slack_notify, "post_webhook", side_effect=AssertionError("generic CLI sent")):
        assert slack_notify.main(["Title", "Body"]) == 0
    assert json.loads(capsys.readouterr().out)["text"] == "💡 Title\nBody"


def test_unified_runtime_cannot_construct_capability(monkeypatch):
    monkeypatch.setenv(bridge.MODE_ENV, "unified")
    with mock.patch.object(slack_notify, "webhook_capability", side_effect=AssertionError("unified delivery capability")):
        assert bridge.notify(title="identity", body="case") is False


def test_native_bridge_is_explicit_and_missing_configuration_never_discovers_secret(monkeypatch):
    monkeypatch.delenv(bridge.MODE_ENV, raising=False)
    monkeypatch.delenv(slack_notify.WEBHOOK_ENV, raising=False)
    with mock.patch("subprocess.run", side_effect=AssertionError("ambient secret read")):
        assert bridge.notify(title="test", body="body") is False
    monkeypatch.setenv(bridge.MODE_ENV, "invalid")
    with pytest.raises(ValueError, match="Unsupported operational"):
        bridge.notify(title="test", body="body")


def test_emergency_capability_requires_named_authority_and_resolution_is_deferred():
    with pytest.raises(slack_notify.SlackNotificationError, match="named approved emergency"):
        slack_breakglass.emergency_capability(approved_emergency=False, project_id="shared-datasets-1", secret_id="shared-datasets-slack-webhook-url")
    with mock.patch("subprocess.run", side_effect=AssertionError("early secret read")):
        capability = slack_breakglass.emergency_capability(approved_emergency=True, project_id="shared-datasets-1", secret_id="shared-datasets-slack-webhook-url")
    assert capability.purpose == "named_breakglass"


def test_capability_mint_inventory_stays_at_reviewed_entrypoints():
    expected = {
        "scripts/dataset_alerts.py", "scripts/repo_alerts.py", "ingestion/common/legacy_runtime_notifications.py",
        "scripts/slack_breakglass.py", "scripts/slack_notify.py",
    }
    found = set()
    for directory in ("scripts", "ingestion", "services"):
        for path in (ROOT / directory).rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and ((isinstance(node.func, ast.Name) and node.func.id in {"webhook_capability", "WebhookCapability"})
                    or isinstance(node.func, ast.Attribute) and node.func.attr in {"webhook_capability", "WebhookCapability"}):
                    found.add(str(path.relative_to(ROOT)))
    assert found == expected
