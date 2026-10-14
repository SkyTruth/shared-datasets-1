from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from google.api_core.exceptions import Forbidden
import pytest

from ingestion.dataset_usage.health import Health
from test_dataset_usage import classifier


def health(*, logging=None, bindings=True, raw_days=7, private=True):
    c = classifier()
    source = Mock()
    source.get_logging.return_value = logging
    raw = Mock()
    raw.lifecycle_rules = [{"action": {"type": "Delete"}, "condition": {"age": raw_days}}]
    raw.iam_configuration = SimpleNamespace(uniform_bucket_level_access_enabled=private, public_access_prevention="enforced")
    expression = "resource.name.startsWith('projects/_/buckets/raw/objects/app-usage/example-production/')"
    raw.get_iam_policy.return_value = SimpleNamespace(bindings=[{"role": "roles/storage.objectCreator", "members": {"serviceAccount:example@example-project.iam.gserviceaccount.com"}, "condition": {"expression": expression}}] if bindings else [])
    client = Mock()
    client.get_bucket.side_effect = lambda bucket, **kwargs: raw if bucket == "raw" else source
    return Health(c, client, "project", "raw")


def test_sources_have_independent_bucket_local_configuration_checks():
    check = health(logging={"logBucket": "raw", "logObjectPrefix": "storage-usage"})
    assert check.configuration_healthy() == {"gcs_usage": True, "app_usage": True}
    assert health().configuration_healthy() == {"gcs_usage": False, "app_usage": True}
    assert health(logging={"logBucket": "raw", "logObjectPrefix": "storage-usage"}, bindings=False).configuration_healthy() == {"gcs_usage": True, "app_usage": False}


@pytest.mark.parametrize("options", [{"raw_days": 1}, {"raw_days": 30}, {"private": False}])
def test_private_storage_and_retention_drift_suspend_both_sources(options):
    assert health(**options).configuration_healthy() == {"gcs_usage": False, "app_usage": False}


def test_disabled_collection_never_discovers_credentials_or_configuration(monkeypatch):
    monkeypatch.setattr("google.auth.default", Mock(side_effect=AssertionError("Credential discovery forbidden")))
    check = health()
    check.classifier.config["collection_enabled"] = False
    assert check.configuration_healthy() == {"gcs_usage": False, "app_usage": False}
    check.client.get_bucket.assert_not_called()


def test_failed_storage_read_cannot_become_a_healthy_pipeline():
    check = health()
    check.client.get_bucket.side_effect = Forbidden("denied")
    with pytest.raises(Forbidden):
        check.configuration_healthy()


@pytest.mark.parametrize("logging", [[], "disabled", 0, False])
def test_malformed_native_configuration_fails(logging):
    with pytest.raises(TypeError, match="logging"):
        health(logging=logging).configuration_healthy()


def test_wrong_bucket_prefix_only_suspends_native_source():
    assert health(logging={"logBucket": "raw", "logObjectPrefix": "wrong"}).configuration_healthy() == {"gcs_usage": False, "app_usage": True}


def test_probe_is_native_head_only_and_cannot_probe_a_retired_source(monkeypatch):
    check = health()
    transport = MagicMock()
    monkeypatch.setattr("urllib.request.urlopen", transport)
    assert check.probe("gcs_usage", "usage-probe-" + "a" * 32) == "example"
    request = transport.call_args.args[0]
    assert request.get_method() == "HEAD" and request.host == "storage.googleapis.com"
    with pytest.raises(ValueError, match="Only native"):
        check.probe("cdn", "unused")
