"""The 120-second observer retries only complete safe read snapshots."""
from unittest.mock import Mock
import json

from google.api_core.exceptions import Forbidden, PermissionDenied, ServiceUnavailable
import pytest

from ingestion.wdpa_monthly import execution_observer as observer
from scripts import operational_retry

JOB = "projects/shared-datasets-1/locations/us-central1/jobs/wdpa-monthly"


def response(payload=None, status=200):
    result = Mock(status_code=status)
    result.json.return_value = payload
    return result


def session(monkeypatch, responses):
    transport = Mock()
    transport.__enter__ = Mock(return_value=transport)
    transport.__exit__ = Mock(return_value=False)
    transport.get.side_effect = responses
    constructor = Mock(return_value=transport)
    monkeypatch.setattr(observer, "AuthorizedSession", constructor)
    monkeypatch.setattr(observer.google.auth, "default", Mock(return_value=(Mock(), None)))
    clock = [0]
    monkeypatch.setattr(operational_retry, "monotonic", lambda: clock[0])
    monkeypatch.setattr(operational_retry, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    return transport, constructor, clock


def execution(name):
    return {"name": JOB + "/executions/" + name}


def test_partial_pages_are_discarded_and_credentials_reacquired_with_one_short_budget(monkeypatch):
    partial, complete = execution("partial"), execution("complete")
    api, constructor, clock = session(monkeypatch, [
        response({"executions": [partial], "nextPageToken": "second"}), response(status=503),
        response({"executions": [complete]}),
    ])
    _, rows = observer.read_snapshot(JOB)
    assert rows == [complete] and clock[0] == 5
    assert constructor.call_count == 2 and constructor.call_args.kwargs == {"max_refresh_attempts": 0, "refresh_timeout": 15}
    assert [call.kwargs["params"] for call in api.get.call_args_list] == [
        {"pageSize": 100}, {"pageSize": 100, "pageToken": "second"}, {"pageSize": 100}]
    assert all(call.kwargs["timeout"] <= 15 and call.kwargs["max_allowed_time"] == call.kwargs["timeout"] for call in api.get.call_args_list)


@pytest.mark.parametrize("failure", [
    response(status=403),
    response({"executions": "partial"}),
    response({"executions": [{"name": "projects/foreign/jobs/other/executions/id"}]}),
    response({"executions": [{"name": JOB + "/executions/first/conditions/second"}]}),
])
def test_auth_and_malformed_snapshots_do_not_retry(monkeypatch, failure):
    api, _, clock = session(monkeypatch, [failure])
    with pytest.raises((Forbidden, ValueError)):
        observer.read_snapshot(JOB)
    assert api.get.call_count == 1 and clock[0] == 0


def test_repeated_page_token_is_a_terminal_validation_failure(monkeypatch):
    api, _, clock = session(monkeypatch, [
        response({"executions": [], "nextPageToken": "same"}),
        response({"executions": [], "nextPageToken": "same"}),
    ])
    with pytest.raises(ValueError, match="pagination"):
        observer.read_snapshot(JOB)
    assert api.get.call_count == 2 and clock[0] == 0


def test_complete_pagination_cannot_return_success_after_absolute_sixty_second_deadline(monkeypatch):
    api, _, clock = session(monkeypatch, [])
    def slow_get(*_args, **kwargs):
        clock[0] += kwargs["max_allowed_time"]
        return response({"executions": [], "nextPageToken": str(clock[0])})
    api.get.side_effect = slow_get
    with pytest.raises(operational_retry.RetryBudgetExhausted):
        observer.read_snapshot(JOB)
    assert clock[0] == 60 and api.get.call_count == 4


@pytest.mark.parametrize("error,reason", [
    (ServiceUnavailable("provider unavailable"), "transient_transport"),
    (PermissionDenied("denied"), "permission_denied"),
    (ValueError("malformed response"), "unknown"),
])
def test_typed_read_failure_precedes_any_storage_write(monkeypatch, capsys, error, reason):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "shared-datasets-1")
    monkeypatch.setenv("WDPA_JOB_REGION", "us-central1")
    monkeypatch.setenv("SHARED_DATASETS_BUCKET", "skytruth-shared-datasets-1")
    monkeypatch.setattr(observer, "read_snapshot", Mock(side_effect=error))
    storage = Mock()
    monkeypatch.setattr(observer.storage, "Client", storage)
    with pytest.raises(type(error)):
        observer.run()
    storage.assert_not_called()
    event = json.loads(capsys.readouterr().err.strip())
    assert event["details"]["failure_reason"] == reason and event["details"]["mutation_status"] == "not_started"


def test_transient_status_write_failure_never_replays_reads_or_writes_or_claims_prewrite_failure(monkeypatch, capsys):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "shared-datasets-1")
    monkeypatch.setenv("WDPA_JOB_REGION", "us-central1")
    monkeypatch.setenv("SHARED_DATASETS_BUCKET", "skytruth-shared-datasets-1")
    credentials = Mock()
    read = Mock(return_value=(credentials, []))
    write = Mock(side_effect=ServiceUnavailable("status write outcome is uncertain"))
    client = Mock()
    sleep = Mock()
    monkeypatch.setattr(observer, "read_snapshot", read)
    monkeypatch.setattr(observer, "write_observation", write)
    monkeypatch.setattr(observer.storage, "Client", client)
    monkeypatch.setattr(operational_retry, "sleep", sleep)
    with pytest.raises(ServiceUnavailable, match="write outcome is uncertain"):
        observer.run()
    read.assert_called_once_with(JOB)
    write.assert_called_once()
    client.assert_called_once_with(project="shared-datasets-1", credentials=credentials)
    sleep.assert_not_called()
    # The real event serializer must emit neither successful health nor a
    # read-only/not_started claim after crossing the status-write boundary.
    assert capsys.readouterr().err == ""
