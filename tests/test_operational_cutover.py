"""Offline cutover counterexamples exercise persisted JSON and real GETs."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import ceil
from unittest import mock

import pytest

from scripts import operational_cutover as gate
from test_monitoring_routing_audit import CHANNEL, fixture as routing_fixture

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
CONTRACT, POLICY = "a" * 64, "b" * 64


def rollout(mode="native", retire=False):
    reference = {"path": ".github/operational-cutovers/cutover.json", "sha256": "c" * 64}
    return {"schema": "shared-datasets-operational-slack-rollout-v1", "mode": mode,
            "cutover_evidence": reference if mode == "unified" else None,
            "retire_runtime_webhook_access": retire, "retirement_evidence": {**reference, "path": ".github/operational-cutovers/drain.json"} if retire else None}


def shadow(interval=timedelta(minutes=5)):
    cycles, references, heartbeats = {}, [], set()
    count = ceil(timedelta(hours=24) / interval) + 1
    for index in range(count):
        completed = NOW - timedelta(minutes=5) - interval * (count - 1 - index)
        instant = gate.collector.timestamp(completed)
        covered = gate.collector.timestamp(completed - timedelta(minutes=2))
        coverage = {"start": gate.collector.timestamp(completed - interval - timedelta(minutes=7)), "end": covered,
                    "completed_at": instant, "views": list(gate.collector.VIEWS), "coverage_gaps": []}
        manifest = {"mode": "shadow", "collector_contract_sha256": CONTRACT, "policy_sha256": POLICY,
                    "source_sha": "d" * 40, "completed_at": instant, "covered_through": covered, "previous_cycle_id": index if index else None}
        cycle = {"manifest_id": index + 1, "manifest": manifest,
                 "checkpoint": {"last_cycle": coverage, "covered_through": covered, "pending": []},
                 "evaluation": {"complete": True, "provenance_complete": True, "public_delivery_count": 0}}
        cycles[index + 1] = cycle
        references.append({"deployment_id": index + 1, "manifest_sha256": gate.sha256(gate.canonical(manifest))})
        heartbeats.add(completed)
    evidence = {"schema": gate.SCHEMA, "collector_contract_sha256": CONTRACT, "policy_sha256": POLICY,
                "policy_ids": {"exact": "policies/123"}, "cycles": references,
                "audit_setup_deployment_id": 500,
                "runtime_deployments": {job: {"deployment_id": 123, "runtime_sha256": "e" * 64} for job in gate.JOBS}}
    return evidence, cycles, heartbeats


def verify(evidence, cycles, heartbeats, **kwargs):
    defaults = {"load_cycle": cycles.__getitem__, "load_latest": lambda: cycles[max(cycles)], "contract_hash": CONTRACT, "policy_hash": POLICY,
                "policy_ids": {"exact": "policies/123"}, "policy_changed_at": NOW - timedelta(hours=25), "heartbeat_points": heartbeats,
                "now": NOW, "revision_allowed": lambda _: True}
    return gate.validate_shadow(evidence, **(defaults | kwargs))


def test_full_day_requires_observed_post_policy_samples_and_signed_continuity():
    evidence, cycles, heartbeats = shadow()
    assert verify(evidence, cycles, heartbeats)["hours"] == 24
    with pytest.raises(ValueError, match="heartbeat sample"):
        verify(evidence, cycles, set())
    with pytest.raises(ValueError, match="predates"):
        gate.validate_shadow(evidence, load_cycle=cycles.__getitem__, load_latest=lambda: cycles[max(cycles)], contract_hash=CONTRACT, policy_hash=POLICY,
            policy_ids=evidence["policy_ids"], policy_changed_at=NOW - timedelta(hours=23), heartbeat_points=heartbeats,
            now=NOW, revision_allowed=lambda _: True)


@pytest.mark.parametrize("interval", [timedelta(seconds=841.134997), timedelta(minutes=25)])
def test_delayed_schedule_preserves_full_day_with_exact_overlap_and_real_samples(interval):
    # Replay the signed production successor gap, plus the last eligible bound.
    # These are complete scans, not missing runs or invented heartbeat samples.
    evidence, cycles, points = shadow(interval)
    assert verify(evidence, cycles, points)["hours"] >= 24
    assert gate.FRESHNESS == timedelta(minutes=10)


@pytest.mark.parametrize("interval", [timedelta(minutes=25, microseconds=1), timedelta(minutes=30)])
def test_completion_gap_must_leave_margin_before_independent_absence_alarm(interval):
    evidence, cycles, points = shadow(interval)
    with pytest.raises(ValueError, match="delayed reconciliation cycle"):
        verify(evidence, cycles, points)


@pytest.mark.parametrize("defect", ["coverage", "missing_sample", "stale", "missing_predecessor"])
def test_healthy_completion_cadence_does_not_authorize_missing_coverage_or_stale_proof(defect):
    evidence, cycles, points = shadow(timedelta(seconds=841.134997))
    if defect == "coverage":
        cycles[2]["checkpoint"]["last_cycle"]["start"] = gate.collector.timestamp(
            gate.collector.instant(cycles[1]["checkpoint"]["covered_through"]) + timedelta(microseconds=1))
    elif defect == "missing_sample":
        points.remove(gate.collector.instant(cycles[2]["manifest"]["completed_at"]))
    elif defect == "stale":
        with pytest.raises(ValueError, match="stale"):
            verify(evidence, cycles, points, now=NOW + timedelta(minutes=5, microseconds=1))
        return
    else:
        cycles[2]["manifest"]["previous_cycle_id"] = None
        evidence["cycles"][1]["manifest_sha256"] = gate.sha256(gate.canonical(cycles[2]["manifest"]))
    with pytest.raises(ValueError):
        verify(evidence, cycles, points)


@pytest.mark.parametrize("defect", [None, "within_window", "future", "unregistered", "unresolved", "tagged", "missing_proof"])
def test_only_proven_auxiliary_terminal_buffer_beyond_coverage_can_remain_pending(defect):
    from scripts import cloud_incident_adapter as adapter
    evidence, cycles, points = shadow()
    cycle = cycles[145]
    covered, completed = (gate.collector.instant(cycle["manifest"][key]) for key in ("covered_through", "completed_at"))
    terminal = covered if defect == "within_window" else completed + timedelta(seconds=1) if defect == "future" else completed - timedelta(seconds=1)
    job = "unregistered-observer" if defect == "unregistered" else "wdpa-execution-observer"
    log_name = "projects/shared-datasets-1/logs/run.googleapis.com%2Fstderr"
    source = "cloudlog:" + gate.collector.digest({"logName": log_name, "insertId": "auxiliary-buffer"})
    fact = {"source_id": source, "observed_at": gate.collector.timestamp(covered - timedelta(minutes=1)), "resource_type": "cloud_run_job", "job": job,
            "evidence": {"log_name": log_name, "insert_id": "auxiliary-buffer"}, "kind": "execution",
            "execution": {"name": f"projects/shared-datasets-1/locations/us-central1/jobs/{job}/executions/observer-abcde",
                          "uid": "10be5376-cc93-4eed-9b11-ab2d3ef8d790", "images": [image("wdpa-monthly")], "revision": "sha256:" + "f" * 64,
                          "terminal": True, "success": True, "create_time": gate.collector.timestamp(covered - timedelta(minutes=1)), "start_time": gate.collector.timestamp(covered - timedelta(minutes=1)),
                          "completion_time": gate.collector.timestamp(terminal), "coverage_gaps": []}}
    if defect == "tagged":
        fact["execution"].update(images=["mutable:latest"], revision="cloudrun-execution:" + fact["execution"]["uid"], coverage_gaps=["execution_image_digest_unresolved"])
    cycle["checkpoint"]["pending"] = [adapter.validate_fact(gate.strict_json(gate.canonical(fact)))]
    cycle["evaluation"]["deferred_source_ids"] = [] if defect == "unresolved" else [source]
    cycle["evaluation"]["deferred_proofs"] = {} if defect in {"unresolved", "missing_proof"} else {source: {
        "kind": "auxiliary_artifact", "record_id": 1, "status_id": 2, "revision": "b" * 40, "artifact": image("wdpa-monthly"),
        "execution": fact["execution"]["name"], "deployment_target": "wdpa-monthly", "resource": "module.wdpa_execution_observer_job.google_cloud_run_v2_job.this"}}
    if defect:
        with pytest.raises(ValueError, match="coverage|deferred|registered"):
            verify(evidence, cycles, points)
    else:
        assert verify(evidence, cycles, points)["hours"] == 24


def rolling_shadow():
    evidence, cycles, _ = shadow()
    for identifier in range(290, 578):
        cycle = deepcopy(cycles[identifier - 1])
        cycle["manifest_id"] = identifier
        cycle["manifest"].update(previous_cycle_id=identifier - 1, source_sha="e" * 40)
        for key in ("completed_at", "covered_through"):
            cycle["manifest"][key] = gate.collector.timestamp(gate.collector.instant(cycle["manifest"][key]) + timedelta(minutes=5))
        coverage = cycle["checkpoint"]["last_cycle"]
        for key in ("start", "end", "completed_at"):
            coverage[key] = gate.collector.timestamp(gate.collector.instant(coverage[key]) + timedelta(minutes=5))
        cycle["checkpoint"]["covered_through"] = cycle["manifest"]["covered_through"]
        cycles[identifier] = gate.strict_json(gate.canonical(cycle))
    points = {gate.collector.instant(cycle["manifest"]["completed_at"]) for identifier, cycle in cycles.items() if identifier >= 289}
    return evidence, cycles, points


def test_reviewed_historical_day_allows_fresh_compatible_post_merge_window():
    evidence, cycles, points = rolling_shadow()
    result = verify(evidence, cycles, points, now=NOW + timedelta(hours=24))
    assert result["reviewed_last_cycle_id"] == 289
    assert result["first_cycle_id"] == 289 and result["last_cycle_id"] == 577
    assert result["last_cycle_manifest_sha256"] == gate.sha256(gate.canonical(cycles[577]["manifest"]))
    # Historical heartbeat retention cannot make immutable review bytes stale;
    # all 289 fresh cycles still require independent observed metric points.
    assert len(points) == 289 and result["hours"] == 24


@pytest.mark.parametrize("defect", ["bad_latest", "different_contract", "different_policy", "incomplete", "gap", "missing_heartbeat", "nonmain", "stale", "rewound", "missing_predecessor"])
def test_rolling_window_never_falls_back_to_good_historical_day(defect):
    evidence, cycles, points = rolling_shadow()
    options = {"now": NOW + timedelta(hours=24)}
    if defect == "bad_latest":
        options["load_latest"] = mock.Mock(side_effect=ValueError("unsigned latest cycle"))
    elif defect == "different_contract":
        cycles[577]["manifest"]["collector_contract_sha256"] = "0" * 64
    elif defect == "different_policy":
        cycles[577]["manifest"]["policy_sha256"] = "0" * 64
    elif defect == "incomplete":
        cycles[400]["evaluation"]["complete"] = False
    elif defect == "gap":
        cycles[400]["manifest"]["previous_cycle_id"] = 397
    elif defect == "missing_heartbeat":
        points.remove(gate.collector.instant(cycles[400]["manifest"]["completed_at"]))
    elif defect == "nonmain":
        options["revision_allowed"] = lambda sha: sha != "e" * 40
    elif defect == "stale":
        options["now"] += timedelta(minutes=6)
    elif defect == "rewound":
        options["load_latest"] = lambda: cycles[288]
    elif defect == "missing_predecessor":
        cycles[400]["manifest"]["previous_cycle_id"] = None
    with pytest.raises(ValueError):
        verify(evidence, cycles, points, **options)


@pytest.mark.parametrize("defect", [None, "future", "missing_sample"])
def test_new_cycle_during_signature_verification_uses_fresh_clock_and_heartbeat_inventory(defect):
    evidence, cycles, old_points = shadow()
    fresh_points = set(old_points)
    def latest_after_review():
        assert reads[-1] == 289
        for identifier in (290, 291):
            cycle = deepcopy(cycles[identifier - 1])
            cycle["manifest_id"] = identifier
            cycle["manifest"]["previous_cycle_id"] = identifier - 1
            for key in ("completed_at", "covered_through"):
                cycle["manifest"][key] = gate.collector.timestamp(gate.collector.instant(cycle["manifest"][key]) + timedelta(minutes=5))
            for key in ("start", "end", "completed_at"):
                cycle["checkpoint"]["last_cycle"][key] = gate.collector.timestamp(gate.collector.instant(cycle["checkpoint"]["last_cycle"][key]) + timedelta(minutes=5))
            cycle["checkpoint"]["covered_through"] = cycle["manifest"]["covered_through"]
            cycles[identifier] = gate.strict_json(gate.canonical(cycle))
            fresh_points.add(gate.collector.instant(cycle["manifest"]["completed_at"]))
        return cycles[291]
    reads = []
    def read(identifier):
        reads.append(identifier)
        return cycles[identifier]
    def observed_heartbeats(*, now):
        assert 290 in reads  # No metric snapshot before the current window reads.
        assert now == NOW + timedelta(minutes=6) if defect != "future" else now == NOW
        return old_points if defect == "missing_sample" else fresh_points
    options = {"load_cycle": read, "load_latest": latest_after_review, "clock": lambda: NOW if defect == "future" else NOW + timedelta(minutes=6), "heartbeat_reader": observed_heartbeats}
    if defect:
        with pytest.raises(ValueError, match="future|heartbeat sample"):
            verify(evidence, cycles, old_points, **options)
    else:
        result = verify(evidence, cycles, old_points, **options)
        assert result["last_cycle_id"] == 291
        assert result["heartbeat_at"] == result["last_cycle_completed_at"] == gate.collector.timestamp(NOW + timedelta(minutes=5))


def test_final_wall_clock_rejects_evidence_aged_during_signature_enumeration():
    result = {"cutover_authorized": True, "heartbeat_at": gate.collector.timestamp(NOW - timedelta(minutes=5)),
              "last_cycle_completed_at": gate.collector.timestamp(NOW - timedelta(minutes=5))}
    gate.recheck_freshness(result, now=NOW)
    with pytest.raises(ValueError, match="final freshness"):
        gate.recheck_freshness(result, now=NOW + timedelta(minutes=6))
    with pytest.raises(ValueError, match="final freshness"):
        gate.recheck_freshness({**result, "heartbeat_at": gate.collector.timestamp(NOW + timedelta(seconds=1))}, now=NOW)


@pytest.mark.parametrize("defect", [None, "plan", "config", "scope", "expired"])
def test_recheck_cli_binds_persisted_proof_to_exact_plan_config_and_scope(tmp_path, monkeypatch, defect):
    config_path = tmp_path / gate.CONFIG
    config_path.parent.mkdir(parents=True)
    raw_config = gate.canonical(rollout("unified"))
    config_path.write_bytes(raw_config)
    plan_path = tmp_path / "plan.json"
    plan_path.write_bytes(b"{}\n")
    proof_path = tmp_path / "proof.json"
    scope = sorted(gate.OPERATIONAL_POLICIES)
    result = {"mode": "unified", "cutover_authorized": True, "last_cycle_completed_at": gate.collector.timestamp(NOW - timedelta(minutes=5)), "heartbeat_at": gate.collector.timestamp(NOW - timedelta(minutes=5))}
    proof = {"schema": gate.PROOF_SCHEMA, "executor_sha": "a" * 40, "plan_sha256": gate.sha256(plan_path.read_bytes()),
             "config_sha256": gate.sha256(raw_config), "scope_sha256": gate.sha256(gate.canonical(scope)), "result": result}
    if defect == "plan":
        plan_path.write_bytes(b'{"different":true}\n')
    elif defect == "config":
        proof["config_sha256"] = "0" * 64
    elif defect == "scope":
        scope.pop()
    elif defect == "expired":
        result["last_cycle_completed_at"] = gate.collector.timestamp(NOW - timedelta(minutes=11))
    proof_path.write_bytes(gate.canonical(proof))
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    class Clock:
        @staticmethod
        def now(zone):
            return NOW
    monkeypatch.setattr(gate, "datetime", Clock)
    with mock.patch.object(gate.subprocess, "check_output", return_value=raw_config), mock.patch.object(gate, "LiveEvidence", side_effect=AssertionError("reloaded remote signatures")):
        args = ["--plan-json", str(plan_path), "--executor-sha", "a" * 40, "--scope", "\n".join(scope), "--recheck", str(proof_path)]
        if defect:
            with pytest.raises(ValueError):
                gate.main(args)
        else:
            assert gate.main(args) == 0


@pytest.mark.parametrize("defect", ["forged_manifest", "missing_cycle", "incomplete", "provenance", "bool_count", "public_send", "pending", "views", "coverage", "wrong_hash", "wrong_policy", "unverified_main", "short", "stale", "duplicate_json"])
def test_rejects_forged_incomplete_or_stale_shadow_history(defect):
    evidence, cycles, heartbeats = shadow()
    cycle = cycles[145]
    if defect == "forged_manifest":
        evidence["cycles"][144]["manifest_sha256"] = "0" * 64
    elif defect == "missing_cycle":
        evidence["cycles"].pop(144)
    elif defect == "incomplete":
        cycle["evaluation"]["complete"] = False
    elif defect == "provenance":
        cycle["evaluation"]["provenance_complete"] = False
    elif defect == "bool_count":
        cycle["evaluation"]["public_delivery_count"] = False
    elif defect == "public_send":
        cycle["evaluation"]["public_delivery_count"] = 1
    elif defect == "pending":
        cycle["checkpoint"]["pending"] = [{"source_id": "cloudlog:" + "0" * 64, "unresolved": True}]
    elif defect == "views":
        cycle["checkpoint"]["last_cycle"]["views"].pop()
    elif defect == "coverage":
        cycle["checkpoint"]["last_cycle"]["coverage_gaps"] = ["lost log"]
    elif defect == "wrong_hash":
        evidence["collector_contract_sha256"] = "0" * 64
    elif defect == "wrong_policy":
        evidence["policy_ids"]["exact"] = "policies/other"
    elif defect == "short":
        evidence["cycles"] = evidence["cycles"][1:]
    elif defect == "stale":
        evidence["cycles"] = evidence["cycles"][:-2]
    elif defect == "unverified_main":
        with pytest.raises(ValueError, match="main ancestry"):
            gate.validate_shadow(evidence, load_cycle=cycles.__getitem__, load_latest=lambda: cycles[max(cycles)], contract_hash=CONTRACT, policy_hash=POLICY,
                policy_ids=evidence["policy_ids"], policy_changed_at=NOW - timedelta(hours=25), heartbeat_points=heartbeats,
                now=NOW, revision_allowed=lambda _: False)
        return
    elif defect == "duplicate_json":
        with pytest.raises(ValueError, match="duplicate JSON"):
            gate.strict_json('{"schema":1,"schema":2}')
        return
    with pytest.raises(ValueError):
        verify(evidence, cycles, heartbeats)


def image(job):
    return f"us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/{job}@sha256:" + "f" * 64


def container(job, mode="unified"):
    return [{"image": image(job), "env": [{"name": "SHARED_DATASETS_OPERATIONAL_DELIVERY_MODE", "value": mode}]}]


class Runtime:
    def __init__(self):
        self.active = {}
    def job(self, job):
        return {"name": f"projects/shared-datasets-1/locations/us-central1/jobs/{job}", "template": {"template": {"containers": container(job)}}}
    def executions(self, job):
        return self.active.get(job, [])


def drain(evidence, live=None, **kwargs):
    defaults = {"live": live or Runtime(), "load_deployment": lambda identity: ({"id": identity, "sha": "a" * 40}, {"target": gate.JOBS[(identity - 1) % 100], "artifact": image(gate.JOBS[(identity - 1) % 100])}),
                "resolve_current": lambda job, current: {"record": {"id": 201 + gate.JOBS.index(job), "sha": "b" * 40}, "payload": {"target": job, "artifact": current}, "status_id": 301 + gate.JOBS.index(job)},
                "runtime_hash": lambda sha: "e" * 64, "revision_allowed": lambda sha: True, "ancestor": lambda old, new: True, "unified_source": lambda sha: True}
    return gate.validate_drain(evidence["runtime_deployments"], **(defaults | kwargs))


def test_old_active_images_and_native_mode_cannot_authorize_cutover_or_secret_retirement():
    evidence, _, _ = shadow()
    for index, job in enumerate(gate.JOBS):
        evidence["runtime_deployments"][job]["deployment_id"] = index + 1
    assert set(drain(evidence)) == set(gate.JOBS)
    live = Runtime()
    live.active["wdpa-monthly"] = [{"name": "projects/shared-datasets-1/locations/us-central1/jobs/wdpa-monthly/executions/old", "template": {"containers": container("wdpa-monthly", "native")}}]
    with pytest.raises(ValueError, match="native delivery"):
        drain(evidence, live)
    live.active["wdpa-monthly"][0]["completionTime"] = "2026-10-22T00:00:00Z"
    assert drain(evidence, live)
    live.active["wdpa-monthly"][0].pop("completionTime")
    live.active["wdpa-monthly"][0]["template"]["containers"][0]["image"] = "mutable:latest"
    with pytest.raises(ValueError, match="old or unverified"):
        drain(evidence, live)


def test_native_era_source_anchor_allows_new_signed_compatible_unified_image():
    evidence, _, _ = shadow()
    for index, job in enumerate(gate.JOBS):
        evidence["runtime_deployments"][job]["deployment_id"] = index + 1
    result = drain(evidence, load_deployment=lambda identifier: ({"id": identifier, "sha": "a" * 40}, {"target": gate.JOBS[identifier - 1], "artifact": "old-native-image@sha256:" + "a" * 64}))
    proof = result["wdpa-monthly"]
    assert proof == {"anchor_deployment_id": 1, "deployment_id": 201, "status_id": 301, "source_sha": "b" * 40, "runtime_sha256": "e" * 64, "image": image("wdpa-monthly")}


@pytest.mark.parametrize("defect", ["anchor_id", "target", "artifact", "status", "older_attempt", "runtime", "nonmain", "divergent", "native_source"])
def test_current_deployment_must_be_signed_supported_unified_descendant(defect):
    evidence, _, _ = shadow()
    for index, job in enumerate(gate.JOBS):
        evidence["runtime_deployments"][job]["deployment_id"] = index + 1
    options = {}
    current = {"record": {"id": 201, "sha": "b" * 40}, "payload": {"target": "wdpa-monthly", "artifact": image("wdpa-monthly")}, "status_id": 301}
    if defect == "anchor_id":
        options["load_deployment"] = lambda identifier: ({"id": 999, "sha": "a" * 40}, {"target": "wdpa-monthly"})
    elif defect == "target":
        current["payload"]["target"] = "eamlis-monthly"
    elif defect == "artifact":
        current["payload"]["artifact"] = "other@sha256:" + "e" * 64
    elif defect == "status":
        current["status_id"] = False
    elif defect == "older_attempt":
        current["record"]["id"] = 0
    elif defect == "runtime":
        options["runtime_hash"] = lambda sha: "e" * 64 if sha == "a" * 40 else "f" * 64
    elif defect == "nonmain":
        options["revision_allowed"] = lambda sha: sha == "a" * 40
    elif defect == "divergent":
        options["ancestor"] = lambda old, new: False
    elif defect == "native_source":
        options["unified_source"] = lambda sha: False
    with pytest.raises(ValueError, match="source anchor|unified main ancestry"):
        drain(evidence, resolve_current=lambda job, image: current, **options)


@pytest.mark.parametrize("producer", ["ingestion/common/runtime.py", "ingestion/wdpa_monthly/execution_observer.py", "scripts/operational_retry.py", "scripts/gcp_read_retry.py", "ingestion/wdpa_monthly/Dockerfile", "ingestion/wdpa_monthly/Dockerfile.promotion"])
def test_runtime_fact_producer_change_invalidates_reviewed_source_anchor(tmp_path, producer):
    evidence, _, _ = shadow()
    def source(command, **kwargs):
        return b"changed runtime facts" if command[-1] == "b" * 40 + ":" + producer else b"same reviewed file"
    with mock.patch.object(gate.subprocess, "check_output", side_effect=source):
        expected = gate.runtime_contract(tmp_path, "a" * 40)
        assert gate.runtime_contract(tmp_path, "b" * 40) != expected
        for index, job in enumerate(gate.JOBS):
            evidence["runtime_deployments"][job] = {"deployment_id": index + 1, "runtime_sha256": expected}
        with pytest.raises(ValueError, match="supported unified main ancestry"):
            drain(evidence, runtime_hash=lambda sha: gate.runtime_contract(tmp_path, sha))


@pytest.mark.parametrize("defect", [None, "native", "other_cutover", "duplicate_json"])
def test_current_runtime_source_itself_requires_reviewed_unified_config(tmp_path, defect):
    value = rollout("native" if defect == "native" else "unified")
    if defect == "other_cutover":
        value["cutover_evidence"]["sha256"] = "0" * 64
    raw = b'{"schema":1,"schema":2}' if defect == "duplicate_json" else gate.canonical(value)
    with mock.patch.object(gate.subprocess, "check_output", return_value=raw) as read:
        if defect == "duplicate_json":
            with pytest.raises(ValueError, match="duplicate JSON"):
                gate.unified_runtime_source(tmp_path, "b" * 40, rollout("unified")["cutover_evidence"])
        else:
            assert gate.unified_runtime_source(tmp_path, "b" * 40, rollout("unified")["cutover_evidence"]) is (defect is None)
        read.assert_called_once_with(["git", "show", "b" * 40 + ":" + gate.CONFIG], cwd=tmp_path)


@pytest.mark.parametrize("defect", [None, "unsigned", "failed", "unknown", "pending", "artifact", "target", "empty", "duplicate", "missing_id"])
def test_latest_runtime_inventory_never_falls_back_from_bad_attempt(defect):
    from scripts import deployment_revision as deployments
    records = gate.strict_json(b'[{"id":201,"sha":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},{"id":1,"sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]')
    payload = {"target": "wdpa-monthly", "artifact": image("wdpa-monthly")}
    status = {"id": 301, "state": "success", "description": "verified"}
    if defect == "failed":
        status.update(state="failure", description="failed")
    elif defect == "unknown":
        status.update(state="error", description="unknown")
    elif defect == "pending":
        status.update(state="in_progress", description="verification_pending")
    elif defect == "artifact":
        payload["artifact"] = "another@sha256:" + "0" * 64
    elif defect == "target":
        payload["target"] = "eamlis-monthly"
    elif defect == "empty":
        records = []
    elif defect == "duplicate":
        records.append(records[0])
    elif defect == "missing_id":
        records[0].pop("id")
    api = mock.Mock()
    api.pages.return_value = records
    with mock.patch.object(deployments, "verify_record", return_value=payload, side_effect=ValueError("unsigned latest attempt") if defect == "unsigned" else None) as verify_record, \
         mock.patch.object(deployments, "verified_statuses", return_value=[status]):
        if defect:
            with pytest.raises(ValueError):
                gate.current_runtime_deployment(api, "SkyTruth/shared-datasets-1", "wdpa-monthly", image("wdpa-monthly"))
        else:
            proof = gate.current_runtime_deployment(api, "SkyTruth/shared-datasets-1", "wdpa-monthly", image("wdpa-monthly"))
            assert proof["record"]["id"] == 201 and proof["status_id"] == 301
        if records and defect not in {"duplicate", "missing_id"}:
            assert verify_record.call_args.args[-1]["id"] == 201
    api.pages.assert_called_once_with("repos/SkyTruth/shared-datasets-1/deployments?environment=production-wdpa-monthly&per_page=100")


def test_only_registered_operational_route_removal_is_eligible_and_schema_retirement_stays_possible():
    base = {"enabled": True, "notification_channels": [CHANNEL], "conditions": [{}]}
    row = {"type": "google_monitoring_alert_policy", "address": sorted(gate.OPERATIONAL_POLICIES)[0], "change": {"actions": ["update"], "before": base, "after": {**base, "notification_channels": []}}}
    assert gate.route_changes({"resource_changes": [row]}) == [row["address"]]
    for address in gate.REQUIRED_NATIVE | {"google_monitoring_alert_policy.future"}:
        with pytest.raises(ValueError, match="independent"):
            gate.route_changes({"resource_changes": [{**row, "address": address}]})
    row["change"]["after"]["conditions"] = [{"filter": "changed"}]
    with pytest.raises(ValueError, match="detection"):
        gate.route_changes({"resource_changes": [row]})
    row["address"] = gate.routing.LEGACY_SCHEMA_ADDRESS
    row["change"]["after"].update(name=gate.routing.LEGACY_SCHEMA_ID, enabled=False)
    assert gate.route_changes({"resource_changes": [row]}) == []


def test_retirement_configuration_and_exact_legacy_accessor_deletions():
    for job in ("wdpa", "eamlis"):
        row = {"type": "google_secret_manager_secret_iam_member", "address": f'google_secret_manager_secret_iam_member.translation_notice["{job}"]',
               "change": {"actions": ["delete"], "before": {"secret_id": gate.RUNTIME_SECRET, "role": "roles/secretmanager.secretAccessor", "member": f"serviceAccount:{job}-monthly-job@shared-datasets-1.iam.gserviceaccount.com"}}}
        assert gate.secret_removals({"resource_changes": [row]}) == [row["address"]]
        with pytest.raises(ValueError, match="reviewed retirement"):
            gate.verify_plan({"resource_changes": [row]}, rollout(), now=NOW)
        row["change"]["before"]["member"] = "serviceAccount:other@example.test"
        with pytest.raises(ValueError, match="unregistered"):
            gate.secret_removals({"resource_changes": [row]})
    with pytest.raises(ValueError):
        gate.config({**rollout(), "retire_runtime_webhook_access": True})


def test_native_noop_is_offline_but_unified_policy_noop_cannot_bypass_gate():
    assert gate.verify_plan({}, rollout(), now=NOW)["cutover_authorized"] is False
    with pytest.raises(ValueError, match="exact tested main"):
        gate.verify_plan({}, rollout("unified"), now=NOW, scope=gate.OPERATIONAL_POLICIES)


def test_immutable_review_document_binds_exact_git_bytes_and_rejects_duplicates(tmp_path):
    reference = {"path": ".github/operational-cutovers/cutover.json", "sha256": gate.sha256(b'{"ok":true}\n')}
    path = tmp_path / reference["path"]
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"ok":true}\n')
    with mock.patch.object(gate.subprocess, "check_output", return_value=path.read_bytes()) as read:
        assert gate.immutable_file(tmp_path, "a" * 40, reference) == {"ok": True}
        assert read.call_args.args[0] == ["git", "show", "a" * 40 + ":" + reference["path"]]
        path.write_bytes(b'{"ok":false}\n')
        with pytest.raises(ValueError, match="reviewed evidence"):
            gate.immutable_file(tmp_path, "a" * 40, reference)


def test_live_evidence_enumerates_every_page_using_read_only_http():
    replies = iter(({"alertPolicies": [{"name": "one"}], "nextPageToken": "next"}, {"alertPolicies": [{"name": "two"}]},
                    {"executions": [], "nextPageToken": "more"}, {"executions": []}))
    calls = []
    class Response:
        status_code = 200
        def json(self):
            return next(replies)
    class Session:
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return Response()
    live = gate.LiveEvidence(Session())
    assert len(live.pages("alertPolicies", "alertPolicies")) == 2
    assert live.executions("wdpa-monthly") == []
    assert {row[0] for row in calls} == {"GET"}
    assert calls[1][2]["params"]["pageToken"] == "next"
    assert calls[3][2]["params"]["pageToken"] == "more"


@pytest.mark.parametrize("defect", [None, "filter", "identity", "missing_filter", "unresolved_source", "swapped_family", "wrong_project", "wrong_region"])
@pytest.mark.parametrize("view_index", range(8))
def test_live_filtered_views_must_match_each_reviewed_access_boundary(tmp_path, defect, view_index):
    from scripts import operational_log_views as views
    path = tmp_path / views.CONTRACT
    path.parent.mkdir(parents=True)
    path.write_bytes((gate.ROOT / views.CONTRACT).read_bytes())
    expected = views.expected_filters(tmp_path)
    if defect == "unresolved_source":
        path.write_text(path.read_text().replace("run.googleapis.com/stderr", "${var.other}"))
    selected = views.VIEWS[view_index]
    calls = []
    class Response:
        status_code = 200
        def json(self):
            name = calls[-1]
            altered = name == selected
            boundary = expected[name]
            if altered and defect == "swapped_family":
                boundary = expected[views.VIEWS[(view_index + 1) % len(views.VIEWS)]]
            elif altered and defect == "wrong_project":
                boundary = boundary.replace(views.PROJECT, "other-project")
            elif altered and defect == "wrong_region":
                boundary = boundary.replace(views.REGION, "europe-west1")
            return {"name": "foreign" if altered and defect == "identity" else name,
                    "filter": "severity>=ERROR" if altered and defect == "filter" else None if altered and defect == "missing_filter" else boundary}
    class Session:
        def request(self, method, url, **kwargs):
            assert method == "GET"
            calls.append(url.removeprefix("https://logging.googleapis.com/v2/"))
            return Response()
    if defect:
        with pytest.raises(ValueError, match="filter|boundary|log-view"):
            gate.LiveEvidence(Session()).verify_views(tmp_path)
    else:
        proof = gate.LiveEvidence(Session()).verify_views(tmp_path)
        assert calls == list(views.VIEWS)
        normalized = {name: gate.routing.filter_text(value) for name, value in expected.items()}
        assert proof["filter_sha256"] == gate.sha256(gate.canonical(normalized))


@pytest.mark.parametrize("defect", [None, "missing", "exempted", "all_services_exempted", "wrong_type"])
def test_live_run_write_audit_capture_has_no_exemptions_and_uses_read_only_iam_method(defect):
    value = {"auditConfigs": [{"service": "run.googleapis.com", "auditLogConfigs": [{"logType": "DATA_WRITE"}]}]}
    if defect == "missing":
        value["auditConfigs"] = []
    elif defect == "exempted":
        value["auditConfigs"][0]["auditLogConfigs"][0]["exemptedMembers"] = ["serviceAccount:worker@example.test"]
    elif defect == "all_services_exempted":
        value["auditConfigs"].append({"service": "allServices", "auditLogConfigs": [{"logType": "DATA_WRITE", "exemptedMembers": ["allUsers"]}]})
    elif defect == "wrong_type":
        value["auditConfigs"][0]["auditLogConfigs"][0]["logType"] = "DATA_READ"
    session = mock.Mock()
    session.request.return_value.status_code = 200
    session.request.return_value.json.return_value = value
    if defect:
        with pytest.raises(ValueError, match="capture|exemption"):
            gate.LiveEvidence(session).verify_run_audit()
    else:
        assert gate.LiveEvidence(session).verify_run_audit()["log_type"] == "DATA_WRITE"
    call = session.request.call_args
    assert call.args == ("POST", "https://cloudresourcemanager.googleapis.com/v1/projects/shared-datasets-1:getIamPolicy")
    assert call.kwargs["params"] == {"fields": "auditConfigs"}
    assert call.kwargs["json"] == {"options": {"requestedPolicyVersion": 3}}


@pytest.mark.parametrize("defect", [None, "unsigned", "wrong_scope", "wrong_target", "failed", "source_drift", "registry_drift", "not_ancestor"])
def test_audit_enablement_time_comes_from_exact_signed_setup_receipt(tmp_path, defect):
    from scripts import deployment_revision as deployments
    path = tmp_path / gate.OBSERVER_TF
    path.parent.mkdir(parents=True)
    path.write_bytes(b"reviewed audit+view boundary\n")
    registry = tmp_path / gate.collector.operational_log_views.CONTRACT
    registry.parent.mkdir(parents=True)
    registry.write_bytes((gate.ROOT / gate.collector.operational_log_views.CONTRACT).read_bytes())
    api = mock.Mock()
    api.get.return_value = {"id": 500, "sha": "b" * 40}
    payload = {"target": gate.AUDIT_SETUP_TARGET, "targets": [gate.AUDIT_ADDRESS]}
    status = {"id": 501, "state": "success", "description": "applied", "created_at": "2026-10-20T00:00:00Z"}
    if defect == "wrong_scope":
        payload["targets"] = []
    elif defect == "wrong_target":
        payload["target"] = "terraform-other"
    elif defect == "failed":
        status["state"] = "failure"
    def source(args, **kwargs):
        name = args[-1].split(":", 1)[1]
        changed = name == gate.OBSERVER_TF and defect == "source_drift" or name == gate.collector.operational_log_views.CONTRACT and defect == "registry_drift"
        return b"other reviewed source" if changed else (tmp_path / name).read_bytes()
    with mock.patch.object(deployments, "verify_record", return_value=payload, side_effect=ValueError("invalid signature") if defect == "unsigned" else None), \
         mock.patch.object(deployments, "verified_statuses", return_value=[status]), \
         mock.patch.object(deployments, "git_ancestor", return_value=defect != "not_ancestor"), \
         mock.patch.object(gate.subprocess, "check_output", side_effect=source):
        if defect:
            with pytest.raises(ValueError):
                gate.audit_setup_receipt(api, "SkyTruth/shared-datasets-1", 500, tmp_path, "a" * 40)
        else:
            assert gate.audit_setup_receipt(api, "SkyTruth/shared-datasets-1", 500, tmp_path, "a" * 40)["completed_at"] == status["created_at"]
    api.get.assert_called_once_with("repos/SkyTruth/shared-datasets-1/deployments/500")


def test_full_plan_gate_audits_current_filters_channels_and_preserves_independent_delivery():
    plan, policies, channels = routing_fixture()
    desired = plan["planned_values"]["root_module"]["resources"]
    for index, address in enumerate(sorted(gate.OPERATIONAL_POLICIES | {gate.HEALTH})):
        row = deepcopy(desired[0])
        row["address"] = address
        row["values"].update(name=f"projects/shared-datasets-1/alertPolicies/{100+index}", display_name=address, enabled=True)
        row["values"]["notification_channels"] = [] if address in gate.OPERATIONAL_POLICIES else [CHANNEL]
        desired.append(row)
        actual = deepcopy(policies[0])
        actual.update(name=row["values"]["name"], displayName=address, enabled=True, notificationChannels=[CHANNEL], creationRecord={"mutateTime": "2026-10-20T00:00:00Z"})
        policies.append(actual)
        plan["configuration"]["root_module"]["resources"].append({"address": address, "type": row["type"]})
    plan["resource_changes"] = [{"address": row["address"], "type": row["type"], "change": {"actions": ["update"] if row["address"] in gate.OPERATIONAL_POLICIES else ["no-op"],
        "after": row["values"], "before": {**row["values"], "notification_channels": [CHANNEL] if row["address"] != gate.routing.LEGACY_SCHEMA_ADDRESS else []}}} for row in desired]
    evidence, cycles, points = shadow()
    evidence["policy_ids"] = {row["address"]: row["values"]["name"] for row in desired}
    class Live(Runtime):
        def verify_views(self, root):
            return {"views": list(gate.collector.VIEWS), "filter_sha256": "a" * 64}
        def verify_run_audit(self):
            return {"service": "run.googleapis.com", "log_type": "DATA_WRITE", "exemptions": []}
        def pages(self, collection, field):
            return policies if field == "alertPolicies" else channels
        def heartbeat_points(self, **kwargs):
            return points
    for index, job in enumerate(gate.JOBS):
        evidence["runtime_deployments"][job]["deployment_id"] = index + 1
    def deployment(identity):
        job = gate.JOBS[(identity - 1) % 100]
        return {"id": identity, "sha": "a" * 40}, {"target": job, "artifact": image(job)}
    kwargs = {"now": NOW, "executor_sha": "a" * 40, "live": Live(), "load_cycle": cycles.__getitem__, "load_latest": lambda: cycles[max(cycles)], "contract_hash": CONTRACT,
              "policy_hash": POLICY, "revision_allowed": lambda _: True, "load_deployment": deployment, "runtime_hash": lambda _: "e" * 64,
              "resolve_current": lambda job, current: {"record": {"id": 201 + gate.JOBS.index(job), "sha": "b" * 40}, "payload": {"target": job, "artifact": current}, "status_id": 301 + gate.JOBS.index(job)},
              "ancestor": lambda old, new: True, "unified_source": lambda sha: True,
              "load_setup": lambda identifier: {"deployment_id": identifier, "status_id": 501, "completed_at": gate.collector.timestamp(NOW - timedelta(hours=25))}}
    with mock.patch.object(gate, "immutable_file", return_value=evidence):
        result = gate.verify_plan(plan, rollout("unified"), **kwargs)
        assert result["cutover_authorized"] is True and len(result["route_removals"]) == 4
        premature_retirement = deepcopy(plan)
        premature_retirement["resource_changes"].append({"type": "google_secret_manager_secret_iam_member", "address": 'google_secret_manager_secret_iam_member.translation_notice["wdpa"]',
            "change": {"actions": ["delete"], "before": {"secret_id": gate.RUNTIME_SECRET, "role": "roles/secretmanager.secretAccessor", "member": "serviceAccount:wdpa-monthly-job@shared-datasets-1.iam.gserviceaccount.com"}}})
        with pytest.raises(ValueError, match="already-applied"):
            gate.verify_plan(premature_retirement, rollout("unified", retire=True), **kwargs)
        recent_setup = {**kwargs, "load_setup": lambda identifier: {"deployment_id": identifier, "status_id": 501, "completed_at": gate.collector.timestamp(NOW - timedelta(hours=23))}}
        with pytest.raises(ValueError, match="predates"):
            gate.verify_plan(plan, rollout("unified"), **recent_setup)
        with pytest.raises(ValueError, match="final freshness"):
            gate.verify_plan(plan, rollout("unified"), **kwargs, clock=mock.Mock(side_effect=[NOW, NOW + timedelta(minutes=6)]))
        # Later secret retirement uses the verified original policy receipt;
        # unified collection must not pretend to create new shadow cycles.
        for row in plan["resource_changes"]:
            if row["address"] in gate.OPERATIONAL_POLICIES:
                row["change"]["before"] = deepcopy(row["change"]["after"])
                row["change"]["actions"] = ["no-op"]
        for policy in policies:
            if policy["displayName"] in gate.OPERATIONAL_POLICIES:
                policy["notificationChannels"] = []
        plan["resource_changes"].append({"type": "google_secret_manager_secret_iam_member", "address": 'google_secret_manager_secret_iam_member.translation_notice["wdpa"]',
            "change": {"actions": ["delete"], "before": {"secret_id": gate.RUNTIME_SECRET, "role": "roles/secretmanager.secretAccessor", "member": "serviceAccount:wdpa-monthly-job@shared-datasets-1.iam.gserviceaccount.com"}}})
        retirement = {"schema": "shared-datasets-runtime-webhook-retirement-v1", "cutover_sha256": rollout("unified")["cutover_evidence"]["sha256"],
                      "runtime_deployments": {job: {"deployment_id": 101 + index, "runtime_sha256": "e" * 64} for index, job in enumerate(gate.JOBS)}}
        with mock.patch.object(gate, "immutable_file", side_effect=lambda root, sha, ref: retirement if ref["path"].endswith("drain.json") else evidence):
            with pytest.raises(ValueError, match="verified original policy apply"):
                gate.verify_plan(plan, rollout("unified", retire=True), **kwargs)
            kwargs["prior_activation"] = {"mode": "unified", "policy_deployment_id": 999}
            retired = gate.verify_plan(plan, rollout("unified", retire=True), **kwargs)
            assert retired["runtime_secret_removals"]
            assert retired["runtime_deployment_proof"]["wdpa-monthly"]["anchor_deployment_id"] == 101
            assert retired["runtime_deployment_proof"]["wdpa-monthly"]["deployment_id"] == 201
            retirement["cutover_sha256"] = "0" * 64
            with pytest.raises(ValueError, match="original cutover"):
                gate.verify_plan(plan, rollout("unified", retire=True), **kwargs)
            retirement["cutover_sha256"] = rollout("unified")["cutover_evidence"]["sha256"]
            points.clear()
            with pytest.raises(ValueError, match="fresh post-policy heartbeat"):
                gate.verify_plan(plan, rollout("unified", retire=True), **kwargs)
            points.add(NOW - timedelta(minutes=1))
            # With no new removals a routine policy sync cannot gain removal
            # authority and must not depend on historical runtime image pins.
            noop = deepcopy(plan)
            noop["resource_changes"].pop()
            forbidden = mock.Mock(side_effect=AssertionError("no new removal requires no runtime drain"))
            result = gate.verify_plan(noop, rollout("unified", retire=True), **(kwargs | {"resolve_current": forbidden}), scope=gate.OPERATIONAL_POLICIES)
            assert result["runtime_deployment_proof"] == {} and result["original_policy_deployment_id"] == 999
            forbidden.assert_not_called()
        policies.append({**deepcopy(policies[0]), "name": "projects/shared-datasets-1/alertPolicies/unknown"})
        with pytest.raises(ValueError, match="live routing audit"):
            gate.verify_plan(plan, rollout("unified", retire=True), **kwargs)


def test_historical_two_view_cycles_cannot_qualify_twenty_four_hour_shadow():
    evidence, cycles, points = shadow()
    cycles[145]["checkpoint"]["last_cycle"]["views"] = [f"projects/{gate.collector.PROJECT}/locations/global/buckets/{bucket}/views/shared-datasets-operations"
                                                       for bucket in ("_Default", "_Required")]
    with pytest.raises(ValueError, match="coverage"):
        verify(evidence, cycles, points)
