from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
from unittest.mock import Mock

from google.api_core.exceptions import PermissionDenied, ServiceUnavailable
import pytest

from ingestion.common import operational_events as events
from scripts import cloud_operational_observations as cloud

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
JOB = "example-job"
EXECUTION = "example-job-abcde"
UID = "10be5376-cc93-4eed-9b11-ab2d3ef8d790"
IMAGE = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/example@sha256:" + "a" * 64


def event_entry(identifier="first", *, time=None, kind="job_outcome", status="success"):
    fact = events.emit(kind, status, environment={"CLOUD_RUN_JOB": JOB, "CLOUD_RUN_EXECUTION": EXECUTION}, stream=io.StringIO(), now=lambda: "2026-10-21T11:56:00Z")
    fact.pop("severity")  # Cloud Logging extracts the special field.
    return {"logName": f"projects/{cloud.PROJECT}/logs/run.googleapis.com%2Fstderr", "insertId": identifier,
            "timestamp": cloud.timestamp(time or NOW - timedelta(minutes=4)), "severity": "INFO",
            "resource": {"type": "cloud_run_job", "labels": {"project_id": cloud.PROJECT, "location": cloud.REGION, "job_name": JOB}},
            "labels": {"run.googleapis.com/execution_name": EXECUTION}, "jsonPayload": fact}


def execution(*, terminal=True, image=IMAGE):
    return {"name": f"projects/{cloud.PROJECT}/locations/{cloud.REGION}/jobs/{JOB}/executions/{EXECUTION}", "uid": UID,
            "template": {"containers": [{"image": image}]}, "completionTime": "2026-10-21T11:57:00Z" if terminal else None,
            "conditions": [{"type": "Completed", "state": "CONDITION_SUCCEEDED" if terminal else "CONDITION_PENDING"}]}


def reader(entries=(), *, result=None):
    api = Mock()
    api.entries.side_effect = lambda view, **kwargs: iter(deepcopy(entries)) if view == cloud.VIEWS[0] else iter(())
    api.execution.return_value = execution() if result is None else result
    return api


def cycle(previous, api, **kwargs):
    return cloud.reconcile_cycle(previous, api, now=kwargs.pop("now", NOW), reconcile=kwargs.pop("reconcile", lambda _: {"complete": True}),
                                 persist=kwargs.pop("persist", lambda value: value), heartbeat=kwargs.pop("heartbeat", lambda _: None), **kwargs)


def test_real_runtime_json_has_explicit_context_and_no_owner_authority():
    stream = io.StringIO()
    event = events.emit("identity_decision", "required", subject={"asset_slug": "example", "release": "2026-10-01"},
                        details={"ambiguity_count": 2, "case_set_sha256": "b" * 64, "published_data_state": "unchanged", "mutation_status": "not_started"},
                        environment={"CLOUD_RUN_JOB": JOB, "CLOUD_RUN_EXECUTION": EXECUTION}, stream=stream, now=lambda: "2026-10-21T11:56:00Z")
    assert json.loads(stream.getvalue()) == event
    assert event["context"]["job"] == JOB
    with pytest.raises(ValueError, match="unsupported detail"):
        events.emit("identity_decision", "required", details={"responsible_human": "forged"}, stream=stream)


def test_empty_single_multiple_and_duplicate_views_advance_only_after_durable_evaluation():
    for count in (0, 1, 3):
        entries = [event_entry(str(index)) for index in range(count)]
        api = reader(entries)
        api.entries.side_effect = lambda *args, **kwargs: iter(deepcopy(entries))
        order = []
        def persist(evidence):
            order.append(("persist", evidence))
            return json.loads(json.dumps(evidence))
        result = cycle(None, api, persist=persist, heartbeat=lambda _: order.append(("heartbeat", None)))
        assert [row[0] for row in order] == ["persist", "heartbeat"]
        state = result["checkpoint"]
        assert len(state["seen"]) == count
        assert state["pending"] == []
        assert state["covered_through"] == cloud.timestamp(NOW - cloud.INGESTION_LAG)
        assert cycle(json.loads(json.dumps(state)), reader(entries), now=NOW + timedelta(minutes=5))["checkpoint"]["last_cycle"]["new_events"] == 0


def test_policy_or_persistence_failure_never_emits_heartbeat_or_mutates_prior():
    state = cloud.collect(None, reader([event_entry()]), now=NOW)
    original = deepcopy(state)
    heartbeat = Mock()
    with pytest.raises(OSError, match="persistence unavailable"):
        cycle(state, reader(), now=NOW + timedelta(minutes=5), persist=Mock(side_effect=OSError("persistence unavailable")), heartbeat=heartbeat)
    heartbeat.assert_not_called()
    assert state == original
    with pytest.raises(ValueError, match="policy evaluation"):
        cycle(state, reader(), now=NOW + timedelta(minutes=5), reconcile=lambda _: {"complete": False}, heartbeat=heartbeat)
    heartbeat.assert_not_called()


def test_heartbeat_requires_exact_verified_persistence_and_delivery_and_cannot_refresh_old_evidence():
    proposal = cloud.evaluated_collection(cloud.collect(None, reader(), now=NOW), {"complete": True})
    heartbeat = Mock()
    for verified, delivered in ((None, True), ({**proposal, "evaluation": {"complete": True, "changed": True}}, True), (proposal, False)):
        with pytest.raises(ValueError):
            cloud.confirm_heartbeat(proposal, verified, delivery_verified=delivered, heartbeat=heartbeat)
    heartbeat.assert_not_called()
    cloud.confirm_heartbeat(proposal, json.loads(json.dumps(proposal)), delivery_verified=True, heartbeat=heartbeat)
    heartbeat.assert_called_once_with(NOW)
    with pytest.raises(ValueError, match="verified durable"):
        cycle(None, reader(), persist=lambda _: None, heartbeat=heartbeat)


def test_partial_pagination_is_discarded_before_shared_budget_retry(monkeypatch):
    monkeypatch.setattr("scripts.operational_retry.sleep", lambda _: None)
    attempts = 0

    def entries(view, **kwargs):
        nonlocal attempts
        if view != cloud.VIEWS[0]:
            return
        attempts += 1
        yield event_entry("partial" if attempts == 1 else "complete")
        if attempts == 1:
            raise ServiceUnavailable("provider unavailable")

    api = reader()
    api.entries.side_effect = entries
    result = cloud.collect(None, api, now=NOW)
    assert attempts == 2 and len(result["pending"]) == 1
    assert result["pending"][0]["evidence"]["insert_id"] == "complete"


def test_denied_enumeration_changed_source_and_forged_context_cannot_advance():
    api = reader()
    api.entries.side_effect = PermissionDenied("denied")
    heartbeat = Mock()
    with pytest.raises(PermissionDenied):
        cycle(None, api, heartbeat=heartbeat)
    heartbeat.assert_not_called()
    original = event_entry()
    state = cloud.collect(None, reader([original]), now=NOW)
    changed = deepcopy(original)
    changed["jsonPayload"]["status"] = "failed"
    with pytest.raises(ValueError, match="different log bytes"):
        cloud.collect(state, reader([changed]), now=NOW + timedelta(minutes=5))
    forged = event_entry()
    forged["jsonPayload"]["context"]["execution"] = "different-job"
    with pytest.raises(ValueError, match="authenticated log context"):
        cloud.collect(None, reader([forged]), now=NOW)


def test_historical_replay_is_bounded_and_coverage_gap_is_explicit():
    state = cloud.collect(None, reader(), now=NOW)
    assert cloud.instant(state["last_cycle"]["end"]) - cloud.instant(state["last_cycle"]["start"]) == cloud.HISTORY
    with pytest.raises(ValueError, match="coverage gap exceeds"):
        cloud.collect(state, reader(), now=NOW + timedelta(hours=25))


def test_pending_execution_is_rechecked_without_new_logs_and_proves_terminal_transition():
    api = reader([event_entry()], result=execution(terminal=False))
    first = cycle(None, api)["checkpoint"]
    assert len(first["watching"]) == 1
    api = reader(result=execution())
    second = cycle(first, api, now=NOW + timedelta(minutes=5))
    assert not second["checkpoint"]["watching"]
    assert second["checkpoint"]["last_cycle"]["new_events"] == 1
    assert any(key.startswith("cloudexec:") for key in second["checkpoint"]["seen"])
    api.execution.assert_called_once()


def test_tagged_new_job_stays_observable_with_provenance_gap_and_no_heartbeat():
    heartbeat = Mock()
    result = cycle(None, reader([event_entry()], result=execution(image=IMAGE.split("@")[0] + ":latest")), heartbeat=heartbeat)
    state = result["checkpoint"]
    assert len(state["pending"]) == 1
    assert state["pending"][0]["execution"]["revision"] == "cloudrun-execution:" + UID
    assert state["pending"][0]["execution"]["uid"] == UID
    assert state["last_cycle"]["coverage_gaps"] == ["execution_image_digest_unresolved"]
    heartbeat.assert_not_called()
    # An empty next scan cannot erase an unresolved provenance gap.
    again = cycle(state, reader(), now=NOW + timedelta(minutes=5), heartbeat=heartbeat)
    assert again["checkpoint"]["pending"]
    heartbeat.assert_not_called()


def test_snapshot_cli_storage_is_atomic_and_round_trips(tmp_path, monkeypatch):
    path = tmp_path / "checkpoint.json"
    value = cloud.collect(None, reader(), now=NOW)
    cloud.atomic_snapshot(path, value)
    assert json.loads(path.read_text()) == value
    previous = path.read_bytes()
    monkeypatch.setattr(cloud.os, "replace", Mock(side_effect=OSError("persist failed")))
    with pytest.raises(OSError):
        cloud.atomic_snapshot(path, {**value, "pending": [{"test": True}]})
    assert path.read_bytes() == previous
    assert not path.with_name(path.name + ".pending").exists()


class Response:
    status_code = 200

    def __init__(self, value):
        self.value = value

    def json(self):
        return self.value


def test_http_pagination_metadata_and_fixed_heartbeat_construct_real_requests():
    session = Mock()
    session.request.side_effect = [Response({"entries": [event_entry("one")], "nextPageToken": "next"}), Response({"entries": [event_entry("two")]}), Response({})]
    api = cloud.CloudReader(session)
    assert len(list(api.entries(cloud.VIEWS[0], start=NOW - timedelta(hours=1), end=NOW))) == 2
    calls = session.request.call_args_list
    assert all(call.args == ("POST", "https://logging.googleapis.com/v2/entries:list") for call in calls)
    assert calls[1].kwargs["json"]["pageToken"] == "next"
    query = calls[0].kwargs["json"]["filter"]
    assert calls[0].kwargs["json"]["resourceNames"] == [cloud.VIEWS[0]]
    for required in (f'jsonPayload.schema="{events.SCHEMA}"', 'severity>=ERROR', 'protoPayload.serviceName="run.googleapis.com"',
                     'log_id("run.googleapis.com/stderr")', 'log_id("cloudscheduler.googleapis.com/executions")',
                     'log_id("cloudaudit.googleapis.com/activity")', 'log_id("cloudaudit.googleapis.com/data_access")'):
        assert required in query
    assert all(f'protoPayload.methodName="{method}"' in query for method in cloud.RUN_METHODS)
    assert query == calls[1].kwargs["json"]["filter"]
    assert "stdout" not in query
    api.heartbeat(NOW)
    call = session.request.call_args
    assert call.args == ("POST", f"https://monitoring.googleapis.com/v3/projects/{cloud.PROJECT}/timeSeries")
    assert call.kwargs["json"]["timeSeries"][0]["metric"]["type"] == cloud.METRIC
    with pytest.raises(ValueError, match="non-operational receipt"):
        api.receipt_metadata(f"gs://{cloud.BUCKET}/dataset/latest/data.fgb")
    name = "category/group/asset/runs/2026-10-01.translation-notice.json"
    session.request.side_effect = None
    session.request.return_value = Response({"bucket": cloud.BUCKET, "name": name, "generation": "123"})
    api.receipt_metadata(f"gs://{cloud.BUCKET}/{name}", generation=123)
    assert session.request.call_args.args[0] == "GET"
    assert "alt" not in session.request.call_args.kwargs["params"]
    assert session.request.call_args.kwargs["params"]["generation"] == "123"


def test_repeated_or_unfinished_http_pagination_never_looks_complete():
    session = Mock()
    session.request.return_value = Response({"nextPageToken": "same"})
    api = cloud.CloudReader(session)
    with pytest.raises(ValueError, match="pagination token"):
        list(api.entries(cloud.VIEWS[0], start=NOW - timedelta(hours=1), end=NOW))


def test_real_collection_cli_serialization_reloads_checkpoint_without_mutation_or_heartbeat(tmp_path, monkeypatch, capsys):
    import google.auth
    from google.auth.transport import requests

    class Clock(datetime):
        current = NOW

        @classmethod
        def now(cls, _zone):
            return cls.current

    session = Mock()
    session.request.side_effect = [Response({"entries": [event_entry()]}), Response(execution()), Response({}), Response({"entries": [event_entry()]}), Response({})]
    monkeypatch.setattr(google.auth, "default", lambda **kwargs: (object(), cloud.PROJECT))
    monkeypatch.setattr(requests, "AuthorizedSession", lambda *args, **kwargs: session)
    monkeypatch.setattr(cloud, "datetime", Clock)
    path = tmp_path / "shadow" / "checkpoint.json"
    assert cloud.main(["--checkpoint", str(path)]) == 0
    first = json.loads(path.read_text())
    assert len(first["pending"]) == 1
    assert json.loads(capsys.readouterr().out)["heartbeat_emitted"] is False
    Clock.current += timedelta(minutes=5)
    assert cloud.main(["--checkpoint", str(path)]) == 0
    second = json.loads(path.read_text())
    assert len(second["pending"]) == 1 and len(second["seen"]) == 1
    assert second["covered_through"] != first["covered_through"]
    assert all(call.args[0] == "GET" or call.args[1] == "https://logging.googleapis.com/v2/entries:list" for call in session.request.call_args_list)
