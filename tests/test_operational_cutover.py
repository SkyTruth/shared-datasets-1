"""Offline cutover counterexamples exercise persisted JSON and real GETs."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
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


def shadow():
    cycles, references, heartbeats = {}, [], set()
    for index in range(289):
        completed = NOW - timedelta(hours=24, minutes=5) + timedelta(minutes=5 * index)
        instant = gate.collector.timestamp(completed)
        covered = gate.collector.timestamp(completed - timedelta(minutes=2))
        coverage = {"start": gate.collector.timestamp(completed - timedelta(minutes=12)), "end": covered,
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
                "runtime_deployments": {job: {"deployment_id": 123, "runtime_sha256": "e" * 64} for job in gate.JOBS}}
    return evidence, cycles, heartbeats


def verify(evidence, cycles, heartbeats, **kwargs):
    return gate.validate_shadow(evidence, load_cycle=cycles.__getitem__, contract_hash=CONTRACT, policy_hash=POLICY,
                               policy_ids={"exact": "policies/123"}, policy_changed_at=NOW - timedelta(hours=25), heartbeat_points=heartbeats,
                               now=NOW, revision_allowed=lambda _: True, **kwargs)


def test_full_day_requires_observed_post_policy_samples_and_signed_continuity():
    evidence, cycles, heartbeats = shadow()
    assert verify(evidence, cycles, heartbeats)["hours"] == 24
    with pytest.raises(ValueError, match="heartbeat sample"):
        verify(evidence, cycles, set())
    with pytest.raises(ValueError, match="predates"):
        gate.validate_shadow(evidence, load_cycle=cycles.__getitem__, contract_hash=CONTRACT, policy_hash=POLICY,
            policy_ids=evidence["policy_ids"], policy_changed_at=NOW - timedelta(hours=23), heartbeat_points=heartbeats,
            now=NOW, revision_allowed=lambda _: True)


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
        cycle["checkpoint"]["pending"] = [{"unresolved": True}]
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
            gate.validate_shadow(evidence, load_cycle=cycles.__getitem__, contract_hash=CONTRACT, policy_hash=POLICY,
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
    return gate.validate_drain(evidence["runtime_deployments"], live=live or Runtime(),
        load_deployment=lambda identity: ({"sha": "a" * 40}, {"target": gate.JOBS[identity - 1], "artifact": image(gate.JOBS[identity - 1])}),
        runtime_hash=lambda sha: "e" * 64, revision_allowed=lambda sha: True, **kwargs)


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


@pytest.mark.parametrize("defect", [None, "filter", "identity", "missing_filter", "unresolved_source"])
def test_live_filtered_views_must_match_the_reviewed_access_boundary(tmp_path, defect):
    path = tmp_path / "terraform/envs/prod/operational_observer.tf"
    path.parent.mkdir(parents=True)
    path.write_text('operational_observer_log_filter = <<-EOT\nresource.labels.project_id="${var.project_id}"\nresource.labels.location="${var.region}"\nEOT\n')
    if defect == "unresolved_source":
        path.write_text(path.read_text().replace("${var.region}", "${var.other}"))
    expected = 'resource.labels.project_id="shared-datasets-1" resource.labels.location="us-central1"'
    calls = []
    class Response:
        status_code = 200
        def json(self):
            return {"name": "foreign" if defect == "identity" else calls[-1], "filter": "severity>=ERROR" if defect == "filter" else None if defect == "missing_filter" else expected}
    class Session:
        def request(self, method, url, **kwargs):
            assert method == "GET"
            calls.append(url.removeprefix("https://logging.googleapis.com/v2/"))
            return Response()
    if defect:
        with pytest.raises(ValueError, match="filter|boundary"):
            gate.LiveEvidence(Session()).verify_views(tmp_path)
    else:
        proof = gate.LiveEvidence(Session()).verify_views(tmp_path)
        assert calls == list(gate.collector.VIEWS)
        assert proof["filter_sha256"] == gate.sha256(gate.routing.filter_text(expected).encode())


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
        def pages(self, collection, field):
            return policies if field == "alertPolicies" else channels
        def heartbeat_points(self, **kwargs):
            return points
    for index, job in enumerate(gate.JOBS):
        evidence["runtime_deployments"][job]["deployment_id"] = index + 1
    def deployment(identity):
        job = gate.JOBS[identity - 1]
        return {"sha": "a" * 40}, {"target": job, "artifact": image(job)}
    kwargs = {"now": NOW, "executor_sha": "a" * 40, "live": Live(), "load_cycle": cycles.__getitem__, "contract_hash": CONTRACT,
              "policy_hash": POLICY, "revision_allowed": lambda _: True, "load_deployment": deployment, "runtime_hash": lambda _: "e" * 64}
    with mock.patch.object(gate, "immutable_file", return_value=evidence):
        result = gate.verify_plan(plan, rollout("unified"), **kwargs)
        assert result["cutover_authorized"] is True and len(result["route_removals"]) == 4
        with pytest.raises(ValueError, match="final freshness"):
            gate.verify_plan(plan, rollout("unified"), **kwargs, clock=lambda: NOW + timedelta(minutes=6))
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
        retirement = {"schema": "shared-datasets-runtime-webhook-retirement-v1", "cutover_sha256": rollout("unified")["cutover_evidence"]["sha256"], "runtime_deployments": evidence["runtime_deployments"]}
        with mock.patch.object(gate, "immutable_file", side_effect=lambda root, sha, ref: retirement if ref["path"].endswith("drain.json") else evidence):
            with pytest.raises(ValueError, match="verified original policy apply"):
                gate.verify_plan(plan, rollout("unified", retire=True), **kwargs)
            kwargs["prior_activation"] = {"mode": "unified", "policy_deployment_id": 999}
            assert gate.verify_plan(plan, rollout("unified", retire=True), **kwargs)["runtime_secret_removals"]
            points.clear()
            with pytest.raises(ValueError, match="fresh post-policy heartbeat"):
                gate.verify_plan(plan, rollout("unified", retire=True), **kwargs)
            points.add(NOW - timedelta(minutes=1))
        policies.append({**deepcopy(policies[0]), "name": "projects/shared-datasets-1/alertPolicies/unknown"})
        with pytest.raises(ValueError, match="live routing audit"):
            gate.verify_plan(plan, rollout("unified", retire=True), **kwargs)
