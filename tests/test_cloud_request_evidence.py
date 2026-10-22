"""Provider-shaped HTTP snapshots: API acceptance never proves job recovery."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import unquote

from google.api_core.exceptions import Forbidden, ServiceUnavailable
import pytest

from scripts import cloud_incident_adapter as adapter, cloud_operational_observations as cloud
from scripts import operational_retry, slack_incidents as incidents
from test_slack_incident_policy import PersistedLedger
from test_slack_incidents import IDENTITY

NOW = datetime(2026, 10, 22, 7, tzinfo=timezone.utc)
JOB = "sea-ice-daily"
IMAGE = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/sea-ice-daily@sha256:" + "a" * 64
CAPTURE = json.loads((Path(__file__).parent / "fixtures/cloud-run-operational-log-shapes.json").read_text())
AUDIT = next(row["entry"] for row in CAPTURE["shapes"] if "protoPayload" in row["entry"])


def raw_execution(name="sea-ice-daily-one", *, job=JOB, created=-30, completed=-5, success=True, image=IMAGE):
    return {"name": cloud.job_parent(job) + "/executions/" + name,
            "uid": "10be5376-cc93-4eed-9b11-ab2d3ef8d790", "createTime": cloud.timestamp(NOW + timedelta(minutes=created)),
            "startTime": cloud.timestamp(NOW + timedelta(minutes=created, seconds=1)),
            "completionTime": cloud.timestamp(NOW + timedelta(minutes=completed)) if completed is not None else None,
            "template": {"containers": [{"image": image}]},
            "conditions": [{"type": "Completed", "state": "CONDITION_SUCCEEDED" if success else "CONDITION_FAILED"}]}


class Response:
    def __init__(self, value, status=200, body=None):
        self.value, self.status_code, self.body = value, status, body

    def json(self):
        return deepcopy(self.value)

    def iter_content(self, **_kwargs):
        yield self.body

    def close(self):
        pass


class Session:
    def __init__(self, entries=(), executions=()):
        self.entries, self.executions, self.calls = deepcopy(list(entries)), deepcopy(list(executions)), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, deepcopy(kwargs)))
        if url == "https://logging.googleapis.com/v2/entries:list":
            assert method == "POST"
            view = kwargs["json"]["resourceNames"][0]
            return Response({"entries": self.entries} if view == cloud.VIEWS[0] else {})
        assert method == "GET", "Offline transport permits observation reads only"
        if url.startswith("https://run.googleapis.com/v2/") and url.endswith("/executions"):
            parent = url.removeprefix("https://run.googleapis.com/v2/").removesuffix("/executions")
            assert kwargs["params"] == {"pageSize": 1000, "showDeleted": "true"}
            return Response({"executions": [row for row in self.executions if row["name"].startswith(parent + "/executions/")]})
        if url.startswith("https://storage.googleapis.com/storage/v1/"):
            name = unquote(url.split("/o/", 1)[1])
            event = next(row["jsonPayload"] for row in self.entries if row.get("jsonPayload", {}).get("details", {}).get("run_record_uri") == f"gs://{cloud.BUCKET}/{name}")
            subject, details = event["subject"], event["details"]
            body = json.dumps({"asset_slug": subject["asset_slug"], "run_date": name.rsplit("/", 1)[1].removesuffix(".json"),
                               "release_date": subject["release"], "status": "success", "release_paths": [
                                   {"path": f"gs://{cloud.BUCKET}/{cloud.asset_roots()[subject['asset_slug']]}/releases/{subject['release']}/fixture.manifest.json", "generation": 123}]}).encode()
            assert kwargs["params"]["generation"] == str(details["run_record_generation"])
            if kwargs["params"].get("alt") == "media":
                return Response(None, body=body)
            written = cloud.timestamp(cloud.instant(event["observed_at"]) - timedelta(seconds=1))
            return Response({"bucket": cloud.BUCKET, "name": name, "generation": str(details["run_record_generation"]),
                             "size": str(len(body)), "timeCreated": written, "updated": written})
        raise AssertionError("Unexpected API request: " + url)


def collect(session, previous=None, *, now=NOW):
    return cloud.collect(previous, cloud.CloudReader(session), now=now)


def evaluate(candidate):
    ledger = PersistedLedger()
    records, evaluation = incidents.prepare_cloud(ledger, IDENTITY, json.loads(json.dumps(candidate)),
                                                   now=cloud.timestamp(NOW), shadow=True)
    return ledger, records, evaluation, cloud.evaluated_collection(candidate, evaluation)


@pytest.mark.parametrize("status", [{}, {"code": 0}])
def test_actual_122_identityless_accepted_requests_preserve_audit_without_execution_or_recovery(status):
    entries = [{**deepcopy(AUDIT), "insertId": f"actual-shape-{i}"} for i in range(122)]
    for entry in entries:
        entry["protoPayload"]["status"] = status
    session = Session(entries)
    candidate = collect(session)
    assert len(candidate["pending"]) == 122
    assert all(fact["kind"] == "execution_request" and "execution" not in fact and adapter.accepted_request(fact)
               for fact in candidate["pending"])
    assert all(adapter.normalize(json.loads(json.dumps(fact))) is None for fact in candidate["pending"])
    ledger, records, evaluation, bundle = evaluate(candidate)
    assert not ledger.states and not records and evaluation["observations"] == []
    assert evaluation["cloud_counts"]["verified_observer_health"] == 0
    assert evaluation["cloud_counts"]["verified_asset_publications"] == 0
    assert set(candidate["last_cycle"]["execution_inventory"]) == set(cloud.registered_jobs())
    assert len([call for call in session.calls if call[1].endswith("/executions")]) == 6
    heartbeat = Mock()
    assert cloud.confirm_heartbeat(bundle, json.loads(json.dumps(bundle)), delivery_verified=True, heartbeat=heartbeat)
    heartbeat.assert_called_once_with(NOW)  # Collection visibility, never request/execution completion.
    missing = deepcopy(candidate)
    missing["last_cycle"].pop("execution_inventory")
    with pytest.raises(ValueError, match="every registered job"):
        incidents.normalize_cloud_collection(missing)
    api = Mock()
    assert incidents.verified_cloud_correlation(api, candidate["pending"][0]) is None
    assert api.mock_calls == []  # Accepted auxiliary requests cannot enter image/execution correlation.


def test_all_five_captured_provider_shapes_survive_real_http_and_checkpoint_roundtrip():
    entries = [row["entry"] for row in CAPTURE["shapes"]]
    session = Session(entries, CAPTURE["executions"])
    candidate = json.loads(json.dumps(collect(session)))
    assert len([fact for fact in candidate["pending"] if fact["kind"] == "runtime"]) == 4
    assert len([fact for fact in candidate["pending"] if fact["kind"] == "execution_request"]) == 1
    for fact in candidate["pending"]:
        adapter.validate_fact(fact)
    assert not any("/executions/" in url for _, url, _ in session.calls)  # Inventory index reused for logs.
    assert any(kwargs.get("params") is not None and "alt" in kwargs["params"] for _, _, kwargs in session.calls)


@pytest.mark.parametrize("status", [None, {"code": 7}, {"code": 13}, {"code": 0, "message": "contradictory error"}])
def test_nonaccepted_status_cannot_become_record_only_or_heartbeat(status):
    entry = deepcopy(AUDIT)
    if status is None:
        entry["protoPayload"].pop("status")
    else:
        entry["protoPayload"]["status"] = status
    candidate = collect(Session([entry]))
    fact = candidate["pending"][0]
    assert not adapter.accepted_request(fact)
    ledger, records, evaluation, bundle = evaluate(candidate)
    assert len(ledger.states) == 1 and not records and len(evaluation["observations"]) == 1
    heartbeat = Mock()
    assert not cloud.confirm_heartbeat(bundle, bundle, delivery_verified=True, heartbeat=heartbeat)
    heartbeat.assert_not_called()


@pytest.mark.parametrize("status", [[], {"code": True}, {"code": "0"}, {"code": None}, {"code": -1}, {"details": {}}, {"unknown": 0}])
def test_malformed_status_is_rejected_at_the_snapshot_boundary(status):
    entry = deepcopy(AUDIT)
    entry["protoPayload"]["status"] = status
    with pytest.raises(ValueError, match="status"):
        collect(Session([entry]))


@pytest.mark.parametrize("field", ["resourceName", "request"])
def test_conflicting_audit_resource_is_not_a_benign_request(field):
    entry = deepcopy(AUDIT)
    entry["protoPayload"][field] = {"name": "namespaces/shared-datasets-1/jobs/another-job"} if field == "request" else "namespaces/another-project/jobs/wdpa-execution-observer"
    with pytest.raises(ValueError, match="differs from authenticated job"):
        collect(Session([entry]))


def test_response_operation_or_execution_names_never_supply_execution_outcomes():
    for response in ({"name": "projects/shared-datasets-1/locations/us-central1/operations/operation-one"},
                     {"metadata": {"name": "wdpa-execution-observer-one"}}):
        entry = deepcopy(AUDIT)
        entry["protoPayload"]["response"] = response
        candidate = collect(Session([entry]))
        assert len(candidate["pending"]) == 1 and candidate["pending"][0]["kind"] == "execution_request"
        assert "execution" not in candidate["pending"][0]


def test_unknown_job_request_remains_actionable_not_hidden_by_registered_inventory():
    entry = deepcopy(AUDIT)
    entry["resource"]["labels"]["job_name"] = "new-job"
    entry["protoPayload"]["resourceName"] = entry["protoPayload"]["request"]["name"] = "namespaces/shared-datasets-1/jobs/new-job"
    candidate = collect(Session([entry]))
    ledger, _, evaluation, bundle = evaluate(candidate)
    assert len(ledger.states) == 1 and not evaluation["provenance_complete"]
    assert not cloud.confirm_heartbeat(bundle, bundle, delivery_verified=True, heartbeat=Mock())


def test_inventory_materialization_uses_completion_window_all_active_and_one_latest_success():
    rows = [raw_execution("old-failure", created=-5000, completed=-3000, success=False),
            raw_execution("old-created-recent-failure", created=-5000, completed=-5, success=False),
            raw_execution("old-active", created=-5000, completed=None),
            raw_execution("success-older", completed=-10), raw_execution("success-latest", completed=-3)]
    candidate = collect(Session(executions=rows))
    facts = candidate["pending"]
    assert {fact["execution"]["name"].rsplit("/", 1)[1] for fact in facts} == {"old-created-recent-failure", "old-active", "success-latest"}
    inventory = candidate["last_cycle"]["execution_inventory"][JOB]
    assert inventory["retained_count"] == 5 and inventory["active_count"] == 1
    assert inventory["sha256"] == cloud.digest(sorted(rows, key=lambda row: row["name"]))
    ledger, _, evaluation, _ = evaluate(candidate)
    assert evaluation["cloud_counts"]["verified_asset_publications"] == 0
    assert len(ledger.states) == 1 and next(iter(ledger.states.values()))["incident"]["resolution"] is None


def test_active_transition_keeps_exact_execution_identity_and_cannot_fake_recovery():
    active = raw_execution(completed=None, created=-3000)
    first = collect(Session(executions=[active]))
    assert len(first["watching"]) == 1
    failed = {**active, "completionTime": cloud.timestamp(NOW + timedelta(minutes=1)),
              "conditions": [{"type": "Completed", "state": "CONDITION_FAILED"}]}
    second = collect(Session(executions=[failed]), json.loads(json.dumps(first)), now=NOW + timedelta(minutes=5))
    assert second["watching"] == {}
    terminal = [fact for fact in second["pending"] if fact.get("execution", {}).get("terminal")]
    assert len(terminal) == 1 and not terminal[0]["execution"]["success"]
    assert terminal[0]["source_id"] != next(iter(first["watching"]))
    assert adapter.alias(adapter.normalize(terminal[0])) == adapter.alias({"proof": {"correlation": None, "fact": first["pending"][0]}, "kind": "job"})


def test_future_terminal_is_durable_buffer_then_eligible_without_early_green_or_action():
    future = raw_execution(completed=-1, success=False)
    first = collect(Session(executions=[future]))
    assert first["pending"] == [] and first["seen"] == {} and len(first["watching"]) == 1
    evaluate(first)  # A coverage watch is not a failed/recovered execution yet.
    second = collect(Session(executions=[future]), json.loads(json.dumps(first)), now=NOW + timedelta(minutes=5))
    assert not second["watching"] and len(second["pending"]) == 1
    assert adapter.normalize(second["pending"][0])["conclusion"] == "failure"


def test_ignored_old_rows_still_validate_and_changed_watches_fail_closed():
    bad = raw_execution("old-foreign", created=-5000, completed=-3000)
    bad["name"] = bad["name"].replace("/jobs/sea-ice-daily/", "/jobs/other-job/")
    class Foreign(Session):
        def request(self, method, url, **kwargs):
            if url.endswith("/jobs/sea-ice-daily/executions"):
                return Response({"executions": [bad]})
            return super().request(method, url, **kwargs)
    with pytest.raises(ValueError, match="another operation"):
        collect(Foreign())
    active = raw_execution(completed=None)
    first = collect(Session(executions=[active]))
    changed = {**active, "uid": "20be5376-cc93-4eed-9b11-ab2d3ef8d790"}
    with pytest.raises(ValueError, match="UID changed"):
        collect(Session(executions=[changed]), first, now=NOW + timedelta(minutes=5))


@pytest.mark.parametrize("job_index", [0, -1])
def test_partial_inventory_retry_discards_pages_and_restarts_every_job_under_one_budget(monkeypatch, job_index):
    clock = [0]
    monkeypatch.setattr(operational_retry, "monotonic", lambda: clock[0])
    monkeypatch.setattr(operational_retry, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    first_job = sorted(cloud.registered_jobs())[job_index]
    partial = raw_execution("discarded", job=first_job, success=False)
    complete = raw_execution("complete", job=first_job, success=False)
    class Partial(Session):
        attempts = 0
        def request(self, method, url, **kwargs):
            if url.endswith("/jobs/" + first_job + "/executions"):
                self.calls.append((method, url, deepcopy(kwargs)))
                if kwargs["params"].get("pageToken"):
                    assert kwargs["params"]["pageToken"] == "partial"
                    return Response({}, 503)
                self.attempts += 1
                return Response({"executions": [partial], "nextPageToken": "partial"} if self.attempts == 1 else {"executions": [complete]})
            return super().request(method, url, **kwargs)
    session = Partial()
    candidate = collect(session)
    assert session.attempts == 2 and clock[0] == 5
    assert len(candidate["pending"]) == 1 and candidate["pending"][0]["execution"]["name"].endswith("/complete")
    assert candidate["last_cycle"]["execution_inventory"][first_job]["retained_count"] == 1
    if job_index == -1:
        previous_job = sorted(cloud.registered_jobs())[0]
        assert len([url for _, url, _ in session.calls if url.endswith("/jobs/" + previous_job + "/executions")]) == 2


@pytest.mark.parametrize("shape", ["repeated-token", "duplicate-identity", "wrong-envelope"])
def test_incomplete_execution_pagination_is_not_an_empty_healthy_inventory(shape):
    target = sorted(cloud.registered_jobs())[0]
    raw = raw_execution(job=target)
    class Invalid(Session):
        def request(self, method, url, **kwargs):
            if url.endswith("/jobs/" + target + "/executions"):
                if shape == "repeated-token":
                    return Response({"nextPageToken": "again"})
                if shape == "duplicate-identity":
                    return Response({"executions": [raw, raw]})
                return Response({"execution": raw})
            return super().request(method, url, **kwargs)
    with pytest.raises(ValueError, match="pagination|duplicated|unexpected"):
        collect(Invalid())


def test_foreign_api_proof_or_backdated_future_watch_cannot_authorize_heartbeat():
    candidate = collect(Session(executions=[raw_execution(completed=-1, success=False)]))
    key = next(iter(candidate["watching"]))
    assert key not in candidate["seen"]
    corrupt = deepcopy(candidate)
    corrupt["watching"][key]["execution"]["completion_time"] = cloud.timestamp(NOW - timedelta(minutes=4))
    with pytest.raises(ValueError):
        cloud.validate_checkpoint(corrupt)
    corrupt = deepcopy(candidate)
    corrupt["covered_through"] = cloud.timestamp(NOW)
    with pytest.raises(ValueError, match="future execution buffer"):
        cloud.validate_checkpoint(corrupt)


def test_canonically_rehashed_future_metadata_beyond_completed_clock_is_invalid_persistence():
    from scripts import cloud_observation_ledger as cycles
    candidate = collect(Session(executions=[raw_execution(completed=-1, success=False)]))
    raw = raw_execution(completed=1440, success=False)
    fact = cloud.api_execution_fact(cloud.execution_proof(raw, JOB), JOB)
    candidate["watching"] = {fact["source_id"]: fact}
    candidate["last_cycle"]["execution_inventory"][JOB]["sha256"] = cloud.digest([raw])
    # This is a self-consistent new source key and inventory digest, not a
    # corrupt old fingerprint. The persisted trusted clock is the boundary.
    with pytest.raises(ValueError, match="beyond the completed coverage clock"):
        cloud.validate_checkpoint(candidate)
    with pytest.raises(ValueError, match="beyond the completed coverage clock"):
        cycles.validate_bundle(candidate, {"complete": True, "provenance_complete": True, "public_delivery_count": 0}, "shadow")


@pytest.mark.parametrize("kind", ["logs", "executions"])
@pytest.mark.parametrize("body", [{"error": {"code": 503}}, {"unexpected": []}, []])
def test_http_200_malformed_list_envelope_never_becomes_healthy_empty_coverage(kind, body):
    class Invalid(Session):
        invalid_reads = 0
        def request(self, method, url, **kwargs):
            if (kind == "logs" and url == "https://logging.googleapis.com/v2/entries:list"
                    or kind == "executions" and url.endswith("/executions")):
                self.invalid_reads += 1
                return Response(body)
            return super().request(method, url, **kwargs)
    session = Invalid()
    persist, heartbeat = Mock(), Mock()
    with pytest.raises(ValueError, match="unexpected|non-object"):
        cloud.reconcile_cycle(None, cloud.CloudReader(session), now=NOW, reconcile=Mock(), persist=persist, heartbeat=heartbeat)
    assert session.invalid_reads == 1  # Malformed HTTP200 is never a transport retry.
    persist.assert_not_called()
    heartbeat.assert_not_called()


@pytest.mark.parametrize("status,error", [(403, Forbidden), (503, ServiceUnavailable)])
def test_denied_or_exhausted_inventory_never_returns_candidate_or_heartbeat(monkeypatch, status, error):
    clock = [0]
    monkeypatch.setattr(operational_retry, "monotonic", lambda: clock[0])
    monkeypatch.setattr(operational_retry, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    session = Mock()
    session.request.return_value = Response({}, status)
    persist, heartbeat = Mock(), Mock()
    with pytest.raises(error):
        cloud.reconcile_cycle(None, cloud.CloudReader(session), now=NOW, reconcile=Mock(), persist=persist, heartbeat=heartbeat)
    assert session.request.call_count == (1 if status == 403 else 8)
    persist.assert_not_called()
    heartbeat.assert_not_called()


@pytest.mark.parametrize("mutation", ["prefix", "resource", "proof"])
def test_api_source_cannot_be_rewritten_as_log_or_other_execution(mutation):
    candidate = collect(Session(executions=[raw_execution()]))
    fact = candidate["pending"][0]
    adapter.validate_fact(json.loads(json.dumps(fact)))
    if mutation == "prefix":
        fact["source_id"] = fact["source_id"].replace("cloudapi:", "cloudexec:")
    elif mutation == "resource":
        fact["evidence"]["resource"] += "-other"
    else:
        fact["execution"]["uid"] = "forged"
    with pytest.raises(ValueError):
        adapter.validate_fact(fact)
