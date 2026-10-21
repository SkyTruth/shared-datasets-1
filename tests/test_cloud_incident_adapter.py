"""Real serialized cloud evidence; fake clocks and offline transports only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
from unittest.mock import Mock, patch

import pytest

from ingestion.common import operational_events as events, runtime
from scripts import cloud_incident_adapter as adapter, cloud_operational_observations as cloud
from scripts import deployment_emission as emissions, operational_policy as policy, slack_incidents as incidents
from test_slack_incident_policy import PersistedLedger, fact as github_fact
from test_slack_incidents import IDENTITY, FakeSlack, observation as github_observation, signed_record

NOW = "2026-10-21T12:00:00Z"
JOB = "sea-ice-daily"
UID = "10be5376-cc93-4eed-9b11-ab2d3ef8d790"
EXECUTION = JOB + "-abcde"
IMAGE = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/sea-ice-daily@sha256:" + "a" * 64
SUBJECT = {"asset_slug": "ims-sea-ice-extent", "release": "2026-10-21"}


def at(minutes=0):
    return policy.timestamp(policy.instant(NOW) + timedelta(minutes=minutes))


def run_receipt(**changes):
    root = cloud.asset_roots()[SUBJECT["asset_slug"]]
    value = {"schema": cloud.RUN_RECEIPT_SCHEMA, "uri": f"gs://{cloud.BUCKET}/{root}/runs/2026-10-21.json",
             "generation": 123, "size": 100, "sha256": "c" * 64, **SUBJECT, "run_date": "2026-10-21", "release_date": SUBJECT["release"],
             "created_at": at(1), "updated_at": at(1),
             "status": "success", "published_paths": [{"path": f"gs://{cloud.BUCKET}/{root}/releases/2026-10-21/ims-sea-ice-extent.manifest.json", "generation": 122}]}
    del value["release"]
    value.update(changes)
    return cloud.validate_run_receipt(value)


def source(identifier="first", *, kind="job_outcome", status="failed", subject=None, details=None,
           job=JOB, image=IMAGE, uid=UID, success=False, terminal=True, time=NOW, receipt=None):
    event = events.emit(kind, status, subject=subject, details=details,
        environment={"CLOUD_RUN_JOB": job, "CLOUD_RUN_EXECUTION": EXECUTION}, stream=io.StringIO(), now=lambda: time)
    log_name = f"projects/{cloud.PROJECT}/logs/run.googleapis.com%2Fstderr"
    source_id = "cloudlog:" + cloud.digest({"logName": log_name, "insertId": identifier})
    raw = {"name": f"projects/{cloud.PROJECT}/locations/{cloud.REGION}/jobs/{job}/executions/{EXECUTION}", "uid": uid,
           "createTime": at(-1), "startTime": NOW,
           "template": {"containers": [{"image": image}]}, "completionTime": at(5) if terminal else None,
           "conditions": [{"type": "Completed", "state": "CONDITION_SUCCEEDED" if success else "CONDITION_FAILED"}]}
    api = Mock()
    api.execution.return_value = raw
    execution = cloud.authoritative_execution(api, job, EXECUTION)
    value = {"source_id": source_id, "observed_at": time, "resource_type": "cloud_run_job", "job": job,
             "evidence": {"log_name": log_name, "insert_id": identifier}, "kind": "runtime", "fact": event, "execution": execution}
    if receipt:
        value["run_receipt"] = receipt
    return adapter.validate_fact(value)


def proposal(*facts):
    return {"schema": cloud.SCHEMA, "covered_through": at(8), "seen": {}, "pending": list(facts), "watching": {},
            "last_cycle": {"start": at(-60), "end": at(8), "completed_at": at(10), "coverage_gaps": []}}


def ingest(ledger, *facts, shadow=True, now=None, **kwargs):
    return incidents.prepare_cloud(ledger, IDENTITY, proposal(*facts), shadow=shadow, now=now or at(10), **kwargs)


def published(identifier="success", *, time=None):
    receipt = run_receipt()
    return source(identifier, status="success", subject=SUBJECT,
                  details={"run_record_uri": receipt["uri"], "run_record_generation": receipt["generation"],
                           "published_data_state": "published", "mutation_status": "completed"},
                  success=True, time=time or at(1), receipt=receipt)


def correlation():
    return {"record_id": 1, "status_id": 2, "revision": "b" * 40, "artifact": IMAGE,
            "run_id": 3, "attempt": 1, "job_id": 4, "job_name": "sea-ice-daily / Deploy and verify sea-ice-daily",
            "workflow": ".github/workflows/ci.yml", "execution": f"projects/{cloud.PROJECT}/locations/{cloud.REGION}/jobs/{JOB}/executions/{EXECUTION}"}


def auxiliary_source(identifier="observer", *, status="failed", reason="transient_transport", success=False, time=NOW, image=None, audit=False):
    image = image or IMAGE.replace("sea-ice-daily@", "wdpa-monthly@")
    value = source(identifier, job="wdpa-execution-observer", status=status, image=image, success=success, time=time,
                   details={"failure_reason": reason, "mutation_status": "not_started"} if status == "failed" else {})
    name = "wdpa-execution-observer-abcde"
    value["execution"]["name"] = f"projects/{cloud.PROJECT}/locations/{cloud.REGION}/jobs/wdpa-execution-observer/executions/{name}"
    value["execution"]["completion_time"] = at(1)
    value["fact"]["context"]["execution"] = name
    if audit:
        value.pop("fact")
        value["kind"] = "execution"
    return adapter.validate_fact(value)


def auxiliary_proof(fact):
    return {"kind": "auxiliary_artifact", "deployment_target": "wdpa-monthly",
            "resource": incidents.deployments.AUXILIARY_CLOUD_JOBS["wdpa-execution-observer"]["resource"],
            "record_id": 17, "status_id": 18, "revision": "b" * 40,
            "artifact": fact["execution"]["images"][0], "execution": fact["execution"]["name"]}


def supervised_deployment(*, revision="b" * 40, active=True):
    binding = policy.OperationBinding("deployment:" + JOB, JOB, revision, None)
    authority = {"actor": "jonaraphael", "repository": incidents.REPOSITORY, "workflow": incidents.WORKFLOW,
                 "ref": "refs/heads/main", "executor_sha": "a" * 40, "run_id": 900, "attempt": 1}
    owner = policy.transition_supervision(None, "claim", binding, now=at(-1), authority=authority,
        responsible_human="jonaraphael", conversation_ref="codex://threads/origin", current_action="Verify safe deployment and resolve any identity decision.")
    if active:
        owner = policy.transition_supervision(owner, "check_in", binding, now=NOW, authority=authority,
            responsible_human="jonaraphael", conversation_ref="codex://threads/origin", current_action="Inspecting the exact supervised execution.")
    state = incidents.new_state({"scope": binding.operation_id, "kind": "deployment", "label": JOB}, IDENTITY)
    state["supervision"][binding.operation_id] = owner
    return state


def test_cloud_variant_has_real_source_identity_and_one_canonical_classification():
    fact = source()
    value = adapter.normalize(json.loads(json.dumps(fact)))
    assert set(value).isdisjoint({"sha", "run_id", "attempt", "job_id", "job_name"})
    state = incidents.fold(None, value, IDENTITY)
    assert state["operational"] == {}
    assert state["source_aliases"] == [adapter.alias(value)]
    assert incidents.validate_state(json.loads(json.dumps(state))) == state
    for field, replacement in (("revision", "b" * 40), ("source_id", "cloudlog:" + "d" * 64), ("time", at(1)), ("conclusion", "success")):
        forged = deepcopy(value)
        forged[field] = replacement
        with pytest.raises(ValueError):
            incidents.validate_observation(forged)


@pytest.mark.parametrize("change", [{"image": IMAGE.split("@")[0] + ":latest"}, {"uid": None}, {"job": "brand-new-job"}])
def test_tagged_unregistered_and_missing_uid_sources_cannot_retry_or_owner_hide(change):
    value = adapter.normalize(source(**change))
    fact = incidents.operational_fact(value)
    assert fact.reason == policy.Reason.ARTIFACT_PROVENANCE_UNVERIFIED
    decision = policy.decide(fact, first_failure_at=NOW, now=NOW, supervision={"forged": True}, ownership_verified=False)
    assert decision.disposition == policy.Disposition.ESCALATE and decision.freeze_replay and not decision.retry_safe


def test_scheduler_and_unallocated_execution_failures_use_truthful_source_anchor():
    fact = source()
    for kind in ("execution_request", "scheduler_dispatch"):
        value = {key: fact[key] for key in ("source_id", "observed_at", "resource_type", "job", "evidence")}
        value.update(kind=kind, mutation_status="uncertain", coverage_gaps=["execution_request_outcome_unverified" if kind == "execution_request" else "scheduler_dispatch_outcome_unverified"])
        if kind == "execution_request":
            value.update(reason="permission_denied", status_code=7)
        else:
            value["resource_type"] = "cloud_scheduler_job"
        observation = adapter.normalize(value)
        assert observation["revision"].startswith("cloudlog-source:")
        assert incidents.operational_fact(observation).reason == policy.Reason.ARTIFACT_PROVENANCE_UNVERIFIED


def test_changed_identity_case_sets_keep_one_action_episode_and_no_attempt_replies():
    first = source(kind="identity_decision", status="required", subject=SUBJECT,
                   details={"ambiguity_count": 1, "case_set_sha256": "1" * 64})
    second = source("different-case-set", kind="identity_decision", status="required", subject=SUBJECT,
                    details={"ambiguity_count": 3, "case_set_sha256": "2" * 64}, time=at(1))
    ledger, slack = PersistedLedger(), FakeSlack()
    records, _ = ingest(ledger, first, shadow=False)
    incidents.deliver(ledger, records, slack)
    initial = next(iter(ledger.states.values()))
    incident_id = initial["incident"]["id"]
    records, _ = ingest(ledger, second, shadow=False)
    incidents.deliver(ledger, records, slack)
    state = next(iter(ledger.states.values()))
    assert state["incident"]["id"] == incident_id and len(state["incident"]["failures"]) == 2
    assert set(state["incident"]["posts"]) == {"parent"}
    assert [call[0] for call in slack.calls] == ["post", "update"]
    assert "*Failed attempts:* 1" in incidents.render(state)["text"]


def test_cloud_shadow_sweeps_github_pending_and_legacy_sweeps_cannot_promote_cloud():
    ledger = PersistedLedger()
    old = github_observation(time=NOW)
    incidents.prepare(ledger, IDENTITY, [old], now=NOW, operational={incidents.observation_key(old): github_fact(old)}, success=lambda *_: None)
    records, evaluation = ingest(ledger, source(), now=at(31))
    assert len(records) == 1 and records[0]["payload"]["state"]["scope"] == old["scope"]
    cloud_state = next(state for scope, state in ledger.states.items() if scope.startswith("cloud:"))
    assert cloud_state["incident"]["posts"] == {}
    incidents.prepare(ledger, IDENTITY, [], now=at(32), success=lambda *_: None)
    assert ledger.states[cloud_state["scope"]]["incident"]["posts"] == {}
    assert evaluation["cloud_counts"]["actionable"] == 1


def test_plain_green_execution_does_not_close_unknown_mutation_or_identity_action():
    ledger = PersistedLedger()
    failed = source()
    ingest(ledger, failed)
    green = source("green", status="success", success=True, time=at(1))
    ingest(ledger, green)
    # A successful canonical publication cannot explain an earlier failure
    # whose mutation subject/frontier is unknown, even in that same execution.
    ingest(ledger, published(), recovery_verifier=lambda _: True)
    state = ledger.states[adapter.source_scope(failed)]
    assert state["incident"]["resolution"] is None


def test_identity_requires_exact_generation_canonical_receipt_for_same_asset_release():
    blocked = source(kind="identity_decision", status="required", subject=SUBJECT, details={"ambiguity_count": 1})
    ledger = PersistedLedger()
    ingest(ledger, blocked)
    ingest(ledger, source("plain-success", status="success", success=True, time=at(1)))
    scope = adapter.source_scope(blocked)
    assert ledger.states[scope]["incident"]["resolution"] is None
    with pytest.raises(incidents.IncidentError, match="authoritative execution/receipt"):
        ingest(ledger, published())
    ingest(ledger, published(), recovery_verifier=lambda _: True)
    state = ledger.states[scope]
    assert state["incident"]["resolution"]["conclusion"] == "success"
    assert state["incident"]["posts"] == {}


def test_verified_deployment_owner_holds_identity_clarification_without_renewing_original_clock():
    blocked = source(kind="identity_decision", status="required", subject=SUBJECT, details={"ambiguity_count": 1})
    deployment = supervised_deployment()
    ledger = PersistedLedger({deployment["scope"]: deployment})
    scope = adapter.source_scope(blocked)
    with patch.object(incidents, "verified_cloud_correlation", return_value=correlation()):
        records, evaluation = ingest(ledger, blocked, shadow=False, now=at(1))
    assert records == [] and evaluation["cloud_counts"]["owner_held"] == 1
    state = ledger.states[scope]
    owner = state["supervision"][scope]
    assert state["escalation_at"] is None and owner["deadline"] == at(120)
    assert owner["last_check_in"] == NOW and owner["conversation_ref"] == "codex://threads/origin"
    request = {"command": "clarification", "binding": incidents.binding_for(state["incident"]["failures"][0]).to_dict(),
               "responsible_human": "jonaraphael", "conversation_ref": owner["conversation_ref"],
               "current_action": "Awaiting the human's exact identity assignment choice.",
               "question_ref": owner["conversation_ref"] + "#question-1", "handoff_ref": None}
    ledger.run["created_at"] = at(2)
    incidents.supervise(ledger, IDENTITY, request, now=at(2))
    later = deepcopy(blocked)
    later["source_id"] = "cloudlog:" + cloud.digest({"logName": blocked["evidence"]["log_name"], "insertId": "later"})
    later["evidence"]["insert_id"] = "later"
    later["observed_at"] = at(30)
    later["fact"]["observed_at"] = at(30)
    with patch.object(incidents, "verified_cloud_correlation", return_value=correlation()):
        records, evaluation = ingest(ledger, later, shadow=False, now=at(31))
    assert records == [] and evaluation["cloud_counts"]["clarification_wait"] == 1
    assert ledger.states[scope]["supervision"][scope]["deadline"] == at(120)
    assert ledger.states[scope]["supervision"][scope]["question_ref"] == request["question_ref"]
    records, _ = ingest(ledger, shadow=False, now=at(120))
    assert len(records) == 1 and records[0]["payload"]["state"]["scope"] == scope


@pytest.mark.parametrize("active,revision", [(False, "b" * 40), (True, "c" * 40)])
def test_registered_or_wrong_revision_deployment_owner_cannot_hide_cloud_action(active, revision):
    blocked = source(kind="identity_decision", status="required", subject=SUBJECT, details={"ambiguity_count": 1})
    deployment = supervised_deployment(revision=revision, active=active)
    ledger = PersistedLedger({deployment["scope"]: deployment})
    with patch.object(incidents, "verified_cloud_correlation", return_value=correlation()):
        records, _ = ingest(ledger, blocked, shadow=False, now=at(1))
    assert len(records) == 1


def test_abandoned_cloud_supervision_escalates_and_wrong_image_clarification_is_rejected():
    blocked = source(kind="identity_decision", status="required", subject=SUBJECT, details={"ambiguity_count": 1})
    deployment = supervised_deployment()
    ledger = PersistedLedger({deployment["scope"]: deployment})
    with patch.object(incidents, "verified_cloud_correlation", return_value=correlation()):
        ingest(ledger, blocked, shadow=False, now=at(1))
    scope = adapter.source_scope(blocked)
    request = {"command": "claim", "binding": {**incidents.binding_for(ledger.states[scope]["incident"]["failures"][0]).to_dict(), "revision": "sha256:" + "f" * 64},
               "responsible_human": "jonaraphael", "conversation_ref": "codex://threads/origin", "current_action": "Guessed revision.", "question_ref": None, "handoff_ref": None}
    ledger.run["created_at"] = at(2)
    with pytest.raises(incidents.IncidentError, match="exact signed execution or canonical source"):
        incidents.supervise(ledger, IDENTITY, request, now=at(2))
    records, _ = ingest(ledger, shadow=False, now=at(11))
    assert len(records) == 1 and ledger.states[scope]["escalation_at"] == at(10)


@pytest.mark.parametrize("state", ["claimed", "delivered", "delivery_unknown"])
def test_legacy_translation_delivery_is_audit_only_or_one_inspection_action(state):
    report = source(kind="translation_maintenance", status="already_claimed", subject=SUBJECT,
                    details={"legacy_delivery_state": state})
    ledger = PersistedLedger()
    records, evaluation = ingest(ledger, report, shadow=False)
    stored = next(iter(ledger.states.values()))
    if state == "delivered":
        assert records == [] and stored["incident"] is None
        assert stored["cloud_audit"] == {report["source_id"]: report}
        assert evaluation["cloud_counts"]["legacy_delivery_reports"] == 1
    else:
        assert len(records) == 1 and set(stored["incident"]["posts"]) == {"parent"}
        assert "without resending" in incidents.operational_fact(stored["incident"]["failures"][0]).required_action
    assert stored["healthy"] is None


def test_unconfirmed_delivery_claim_is_never_resent_or_adopted_as_bot_parent():
    report = source(kind="translation_maintenance", status="already_claimed", subject=SUBJECT,
                    details={"legacy_delivery_state": "claimed"})
    ledger, slack = PersistedLedger(), FakeSlack()
    records, _ = ingest(ledger, report, shadow=False)
    assert len(records) == 1
    later = source("later-report", kind="translation_maintenance", status="already_claimed", subject=SUBJECT,
                   details={"legacy_delivery_state": "claimed"}, time=at(1))
    assert ingest(ledger, later, shadow=False)[0] == []
    assert slack.calls == []


def translation_event(identifier, status, *, generation=11, delivered=False):
    root = cloud.asset_roots()[SUBJECT["asset_slug"]]
    uri = f"gs://{cloud.BUCKET}/{root}/runs/2026-10-21.translation-notice.json"
    subject = {**SUBJECT, "manifest_uri": f"gs://{cloud.BUCKET}/{root}/releases/{SUBJECT['release']}/{SUBJECT['asset_slug']}.manifest.json"}
    details = {"manifest_generation": generation}
    if delivered:
        details.update(receipt_uri=uri, receipt_generation=13, legacy_delivery_state="delivered")
    value = source(identifier, kind="translation_maintenance", status=status, subject=subject, details=details)
    if delivered:
        value["receipt_metadata"] = {"bucket": cloud.BUCKET, "name": uri.removeprefix(f"gs://{cloud.BUCKET}/"), "generation": "13"}
    return adapter.validate_fact(value)


@pytest.mark.parametrize("same_batch", [True, False])
def test_exact_legacy_delivery_coverage_prevents_new_bot_broadcast_without_false_recovery(same_batch):
    required = translation_event("required", "required")
    delivered = translation_event("delivered-report", "already_claimed", delivered=True)
    ledger = PersistedLedger()
    if same_batch:
        records, _ = ingest(ledger, required, delivered, shadow=False)
    else:
        ingest(ledger, required, shadow=True)
        records, _ = ingest(ledger, delivered, shadow=False)
    state = ledger.states[adapter.source_scope(required)]
    assert records == [] and state["incident"]["posts"] == {}
    assert state["incident"]["failures"][0]["proof"]["fact"] == required
    assert state["first_failure_at"] == NOW and state["healthy"] is None and state["incident"]["resolution"] is None
    assert incidents.required_failures(state) == []
    changed = translation_event("new-manifest", "required", generation=12)
    records, _ = ingest(ledger, changed, shadow=False)
    assert len(records) == 1 and len(incidents.required_failures(ledger.states[state["scope"]])) == 1


def test_legacy_report_never_erases_already_public_or_uncertain_bot_parent():
    required = translation_event("required", "required")
    ledger, slack = PersistedLedger(), FakeSlack()
    records, _ = ingest(ledger, required, shadow=False)
    incidents.deliver(ledger, records, slack)
    parent = next(iter(ledger.states.values()))["incident"]["posts"]["parent"]
    records, _ = ingest(ledger, translation_event("report", "already_claimed", delivered=True), shadow=False)
    state = next(iter(ledger.states.values()))
    assert state["incident"]["posts"]["parent"] == parent and state["incident"]["resolution"] is None
    assert len(incidents.required_failures(state)) == 1
    assert all(key == "parent" for key in state["incident"]["posts"])


def test_manifest_only_failure_and_unknown_job_delivery_report_remain_actionable():
    root = cloud.asset_roots()[SUBJECT["asset_slug"]]
    value = source(kind="translation_maintenance", status="failed",
        subject={"manifest_uri": f"gs://{cloud.BUCKET}/{root}/releases/{SUBJECT['release']}/{SUBJECT['asset_slug']}.manifest.json"})
    assert adapter.source_scope(value).startswith("cloud:translation:")
    assert adapter.normalize(value)["conclusion"] == "failure"
    unregistered = source(kind="translation_maintenance", status="already_claimed", subject=SUBJECT,
                          details={"legacy_delivery_state": "delivered"}, job="unregistered-job")
    normalized = incidents.normalize_cloud_collection(proposal(unregistered))
    assert normalized["audit_reports"] == [] and len(normalized["observations"]) == 1
    assert incidents.operational_fact(normalized["observations"][0]).reason == policy.Reason.ARTIFACT_PROVENANCE_UNVERIFIED


def test_new_job_policy_gap_stays_pending_and_cannot_emit_heartbeat_next_empty_cycle():
    raw = source(job="unregistered-job")
    ledger = PersistedLedger()
    candidate = proposal(raw)
    _, evaluation = incidents.prepare_cloud(ledger, IDENTITY, candidate, now=at(10))
    bundle = cloud.evaluated_collection(candidate, evaluation)
    assert bundle["checkpoint"]["pending"] == [raw] and not evaluation["provenance_complete"]
    assert cloud.confirm_heartbeat(bundle, deepcopy(bundle), delivery_verified=True, heartbeat=Mock()) is False


def correlation_api(*, complete=True, image=IMAGE, exact_execution=True):
    repo = {"id": 1, "full_name": incidents.REPOSITORY}
    run = {"id": 3, "run_attempt": 1, "head_sha": "b" * 40, "name": "CI", "path": ".github/workflows/ci.yml",
           "workflow_id": 9, "event": "push", "status": "completed", "conclusion": "failure", "head_branch": "main",
           "repository": repo, "head_repository": repo}
    job = {"id": 4, "run_id": 3, "name": "sea-ice / Build image and apply sea-ice-daily Terraform", "status": "completed" if complete else "in_progress", "conclusion": "failure"}
    record = {"id": 1, "sha": "b" * 40}
    status = {"id": 2, "environment_url": adapter.link(source()) if exact_execution else "https://wrong.example"}
    payload = {"target": JOB, "artifact": image, "execution_run_id": 3, "execution_run_attempt": 1}
    api = Mock()
    def get(path):
        if path.endswith("/deployments/1"):
            return deepcopy(record)
        if "/actions/runs/" in path:
            return deepcopy(run)
        if "/actions/workflows/" in path:
            return {"id": 9, "path": run["path"]}
        if path == "repos/" + incidents.REPOSITORY:
            return deepcopy(repo)
        raise AssertionError(path)
    def pages(path, **kwargs):
        if "?environment=" in path:
            return [deepcopy(record)]
        if path.endswith("/statuses?per_page=100"):
            return [deepcopy(status)]
        assert path.endswith("/actions/runs/3/attempts/1/jobs?per_page=100") and kwargs == {"field": "jobs"}
        return [deepcopy(job)]
    api.get.side_effect, api.pages.side_effect = get, pages
    return api, payload


def test_exact_signed_execution_correlation_requires_full_authoritative_attempt_jobs():
    api, payload = correlation_api()
    with patch.object(incidents.deployments, "verify_record", return_value=payload), patch.object(incidents.deployments, "verify_receipt") as verify:
        proof = incidents.verified_cloud_correlation(api, source())
    assert proof["run_id"] == 3 and proof["job_id"] == 4 and proof["revision"] == "b" * 40
    verify.assert_called_once()
    promoted = adapter.normalize(source(), correlation=proof, promote=True)
    assert promoted["scope"] == "deployment:" + JOB and incidents.source_alias(promoted) == "github:3:1"
    for options, error in (({"complete": False}, "incomplete"), ({"image": IMAGE.replace("a" * 64, "b" * 64)}, "artifact")):
        api, payload = correlation_api(**options)
        with patch.object(incidents.deployments, "verify_record", return_value=payload), patch.object(incidents.deployments, "verify_receipt"), pytest.raises(incidents.IncidentError, match=error):
            incidents.verified_cloud_correlation(api, source())
    api, payload = correlation_api(exact_execution=False)
    with patch.object(incidents.deployments, "verify_record", return_value=payload):
        assert incidents.verified_cloud_correlation(api, source()) is None


def test_unposted_shadow_history_is_adopted_into_exact_existing_parent_without_losing_clock():
    raw = source()
    old = incidents.fold(None, adapter.normalize(raw), IDENTITY)
    github = github_observation(scope="deployment:" + JOB, label=JOB, run_id=3, attempt=1, time=at(1))
    existing = incidents.fold(None, github, IDENTITY)
    existing["incident"]["posts"]["parent"] = {"state": "delivered", "ts": "1791270000.000001"}
    existing_id = existing["incident"]["id"]
    states = {old["scope"]: old, existing["scope"]: existing}
    promoted = adapter.normalize(raw, correlation=correlation(), promote=True)
    incidents.adopt_cloud_shadow(states, [promoted])
    assert len(states) == 1
    current = incidents.fold(states[existing["scope"]], promoted, IDENTITY)
    assert current["incident"]["id"] == existing_id and current["first_failure_at"] == NOW
    assert old["incident"]["failures"][0] in current["incident"]["failures"]
    assert all(item["kind"] == "deployment" for item in incidents.required_failures(current))
    assert "*Failed attempts:* 1" in incidents.render(current)["text"]
    incidents.validate_state(current)


def test_signed_shadow_adoption_requires_complete_retained_history_and_both_verified_records(tmp_path):
    raw = source()
    old = incidents.fold(None, adapter.normalize(raw), IDENTITY)
    promoted = adapter.normalize(raw, correlation=correlation(), promote=True)
    github = github_observation(scope="deployment:" + JOB, label=JOB, run_id=3, attempt=1, time=at(1))
    existing = incidents.fold(None, github, IDENTITY)
    states = {old["scope"]: old, existing["scope"]: existing}
    incidents.adopt_cloud_shadow(states, [promoted])
    current = incidents.fold(states[existing["scope"]], promoted, IDENTITY)
    first, latest = signed_record(), signed_record()
    first["payload"]["state"] = old
    latest["id"] += 1
    latest["payload"]["state"] = current
    api = Mock()
    api.pages.side_effect = lambda path: [latest, first] if "?environment=" in path else [{"description": "applied", "state": "success"}]
    with patch.object(emissions, "verify") as verify:
        loaded = incidents.Ledger(api, {}, tmp_path).load()
    assert list(loaded) == [existing["scope"]] and verify.call_count == 2
    lost = deepcopy(current)
    lost["incident"]["failures"] = [item for item in lost["incident"]["failures"] if item != old["incident"]["failures"][0]]
    lost["source_aliases"] = sorted({incidents.source_alias(item) for item in lost["incident"]["failures"]})
    latest["payload"]["state"] = lost
    with patch.object(emissions, "verify"), pytest.raises(incidents.IncidentError, match="alias conflicts"):
        incidents.Ledger(api, {}, tmp_path).load()


def test_native_mode_updates_known_parent_but_cannot_open_cloud_only_parent():
    ledger, slack = PersistedLedger(), FakeSlack()
    blocked = source(kind="identity_decision", status="required", subject=SUBJECT, details={"ambiguity_count": 1})
    records, _ = ingest(ledger, blocked, shadow=False)
    incidents.deliver(ledger, records, slack)
    scope = adapter.source_scope(blocked)
    later = source("later", kind="identity_decision", status="required", subject=SUBJECT, details={"ambiguity_count": 2}, time=at(1))
    next_state = incidents.fold(ledger.states[scope], adapter.normalize(later), IDENTITY)
    records = incidents.prepare(ledger, IDENTITY, [], now=at(2), initial_states={scope: next_state}, original_states=ledger.states, success=lambda *_: None)
    assert len(records) == 1 and records[0]["payload"]["state"]["incident"]["posts"]["parent"]["state"] == "delivered"
    incidents.deliver(ledger, records, slack)
    assert [call[0] for call in slack.calls] == ["post", "update"]
    fresh = PersistedLedger()
    assert incidents.prepare(fresh, IDENTITY, [adapter.normalize(blocked)], now=at(2), success=lambda *_: None) == []
    assert next(iter(fresh.states.values()))["incident"]["posts"] == {}


def test_evaluation_replay_rejects_omitted_actions_false_counts_and_forged_healthy():
    ledger = PersistedLedger()
    candidate = proposal(source())
    _, evaluation = incidents.prepare_cloud(ledger, IDENTITY, candidate, now=at(10))
    bundle = cloud.evaluated_collection(candidate, evaluation)
    assert incidents.verify_cloud_evaluation(bundle["checkpoint"], evaluation, ledger.states, shadow=True, worker=evaluation["worker"])
    for mutation in (lambda value: value.update(observations=[]), lambda value: value["cloud_counts"].update(actionable=0),
                     lambda value: value.update(provenance_complete=False), lambda value: value["observations"][0].update(conclusion="success")):
        forged = deepcopy(evaluation)
        mutation(forged)
        with pytest.raises((ValueError, incidents.IncidentError)):
            incidents.verify_cloud_evaluation(bundle["checkpoint"], forged, ledger.states, shadow=True, worker=evaluation["worker"])


def test_terminal_receipt_reverification_reads_exact_execution_uid_image_and_generation():
    proof = published()
    observation = adapter.normalize(proof)
    reader = Mock()
    reader.run_receipt.return_value = proof["run_receipt"]
    with patch.object(cloud, "authoritative_execution", return_value=proof["execution"]):
        assert incidents.reverify_saved_cloud_success(reader, observation)
    reader.run_receipt.assert_called_once_with(proof["run_receipt"]["uri"], generation=123)
    changed = {**proof["execution"], "uid": "different-execution"}
    with patch.object(cloud, "authoritative_execution", return_value=changed), pytest.raises(incidents.IncidentError, match="execution changed"):
        incidents.reverify_saved_cloud_success(reader, observation)


def test_stale_and_subsecond_prior_receipts_cannot_resolve_new_identity_failure():
    blocked = source(kind="identity_decision", status="required", subject=SUBJECT, details={"ambiguity_count": 1})
    blocked["observed_at"] = "2026-10-21T12:00:00.900000Z"
    failure = adapter.normalize(blocked)
    fresh_log = published()
    fresh_log["run_receipt"]["created_at"] = "2026-10-21T12:00:00.200000Z"
    fresh_log["run_receipt"]["updated_at"] = "2026-10-21T12:00:00.200000Z"
    evidence = adapter.normalize(fresh_log)
    evidence["scope"] = failure["scope"]
    adapter.validate_observation(evidence)
    assert not adapter.covers(failure, evidence)
    stale = published("stale")
    stale["run_receipt"]["created_at"] = at(-60)
    stale["run_receipt"]["updated_at"] = at(-60)
    assert adapter.recoverable(stale) is False and adapter.normalize(stale) is None


def test_final_policy_clock_cannot_hold_an_owner_that_expired_during_authoritative_reads():
    blocked = source(kind="identity_decision", status="required", subject=SUBJECT, details={"ambiguity_count": 1})
    deployment = supervised_deployment()
    ledger = PersistedLedger({deployment["scope"]: deployment})
    with patch.object(incidents, "verified_cloud_correlation", return_value=correlation()):
        records, evaluation = ingest(ledger, blocked, shadow=False, now=at(1), clock=lambda: policy.instant(at(11)))
    assert len(records) == 1 and evaluation["worker"]["evaluated_at"] == at(11)
    assert evaluation["cloud_counts"]["owner_held"] == 0


def test_auxiliary_registration_is_the_existing_wdpa_target_resource_and_same_image_contract():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    contract = incidents.deployments.AUXILIARY_CLOUD_JOBS["wdpa-execution-observer"]
    assert contract["deployment_target"] in incidents.deployments.TARGET_WORKFLOWS
    text = (root / "terraform/envs/prod/wdpa_execution_observer.tf").read_text()
    assert 'wdpa_execution_observer_job_name = "wdpa-execution-observer"' in text
    assert 'module "wdpa_execution_observer_job"' in text and "image                 = var.wdpa_monthly_image" in text
    workflow = (root / ".github/workflows/wdpa-monthly-deploy.yml").read_text()
    assert contract["resource"] in workflow and "unexpected image for" in workflow


def test_signed_auxiliary_image_proof_is_not_a_fabricated_same_attempt_correlation():
    fact = auxiliary_source()
    proof = auxiliary_proof(fact)
    assert adapter.normalize(fact, correlation=proof, promote=True)["kind"] == "job"
    assert adapter.provenance_gap(fact) and not adapter.provenance_gap(fact, proof)
    api = Mock()
    record = {"id": 17, "sha": "b" * 40, "payload": {"target": "wdpa-monthly", "artifact": proof["artifact"], "targets": [proof["resource"]]}}
    api.pages.side_effect = lambda path: [record] if "?environment=" in path else [{"id": 18, "description": "verification_pending", "state": "in_progress"}]
    with patch.object(incidents.deployments, "verify_record", return_value=record["payload"]), patch.object(incidents.deployments, "verify_receipt") as verify:
        assert incidents.verified_auxiliary_artifact(api, fact) == proof
    verify.assert_called_once()
    record["payload"]["targets"] = []
    assert incidents.verified_auxiliary_artifact(api, fact) is None
    forged = {**proof, "resource": "module.wdpa_monthly_job.google_cloud_run_v2_job.this"}
    with pytest.raises(ValueError, match="registered deployment resource"):
        adapter.normalize(fact, correlation=forged)


def test_typed_observer_read_failure_has_one_thirty_minute_window_and_quiet_health_recovery():
    raw = auxiliary_source("raw", audit=True)
    typed = auxiliary_source("typed")
    ledger = PersistedLedger()
    with patch.object(incidents, "verified_cloud_correlation", side_effect=lambda _api, fact: auxiliary_proof(fact)):
        records, evaluation = ingest(ledger, raw, typed, shadow=False, now=at(2))
    assert records == [] and evaluation["cloud_counts"]["actionable"] == 0
    scope = adapter.source_scope(raw)
    state = ledger.states[scope]
    assert len(state["incident"]["failures"]) == 2 and incidents.required_failures(state) == [adapter.normalize(typed, correlation=auxiliary_proof(typed))]
    assert incidents.episode_decisions(state, now=at(29))[0][1].disposition == policy.Disposition.RECOVER
    healed = auxiliary_source("healthy", status="success", success=True, time=at(3), audit=True)
    healed["execution"].update(create_time=at(2), start_time=at(2), completion_time=at(4))
    with patch.object(incidents, "verified_cloud_correlation", side_effect=lambda _api, fact: auxiliary_proof(fact)):
        records, evaluation = ingest(ledger, healed, shadow=False, now=at(5), recovery_verifier=lambda _: True)
    assert records == [] and evaluation["cloud_counts"]["verified_asset_publications"] == 0
    assert evaluation["cloud_counts"]["verified_observer_health"] == 1
    assert ledger.states[scope]["incident"]["resolution"] is not None
    assert "canonical dataset and deployment outcomes remain separate" in incidents.operational_fact(ledger.states[scope]["incident"]["resolution"]).published_data_state


@pytest.mark.parametrize("reason", ["permission_denied", "missing_configuration", "unknown"])
def test_permanent_or_unknown_observer_read_failure_is_actionable(reason):
    fact = auxiliary_source(reason=reason)
    ledger = PersistedLedger()
    with patch.object(incidents, "verified_cloud_correlation", return_value=auxiliary_proof(fact)):
        assert len(ingest(ledger, fact, shadow=False, now=at(2))[0]) == 1


def test_exhausted_observer_episode_escalates_once_and_later_exact_repair_image_can_recover_only_health():
    failed = auxiliary_source()
    ledger = PersistedLedger()
    with patch.object(incidents, "verified_cloud_correlation", return_value=auxiliary_proof(failed)):
        ingest(ledger, failed, shadow=False, now=at(2))
    records, _ = ingest(ledger, shadow=False, now=at(30))
    assert len(records) == 1
    new_image = failed["execution"]["images"][0].replace("a" * 64, "c" * 64)
    healed = auxiliary_source("repair", status="success", success=True, time=at(32), image=new_image, audit=True)
    healed["execution"].update(create_time=at(31), start_time=at(31), completion_time=at(33))
    recovery = adapter.normalize(healed, correlation=auxiliary_proof(healed))
    failure = adapter.normalize(failed, correlation=auxiliary_proof(failed))
    assert adapter.covers(failure, recovery)
    stale = deepcopy(recovery)
    stale["proof"]["fact"]["execution"]["create_time"] = at(-1)
    assert not adapter.covers(failure, stale)
    canonical = github_observation(scope="deployment:wdpa-monthly", label="wdpa-monthly", time=NOW)
    assert not incidents.covers_failure(canonical, recovery, lambda *_: True)


def test_future_auxiliary_metadata_is_buffered_without_failing_complete_cutoff_heartbeat():
    raw = auxiliary_source(audit=True)
    raw["execution"]["completion_time"] = at(9)
    ledger = PersistedLedger()
    candidate = proposal(raw)
    with patch.object(incidents, "verified_cloud_correlation", return_value=auxiliary_proof(raw)):
        _, evaluation = incidents.prepare_cloud(ledger, IDENTITY, candidate, now=at(10))
    bundle = cloud.evaluated_collection(candidate, evaluation)
    assert cloud.unresolved_pending(bundle["checkpoint"], evaluation) == []
    assert bundle["checkpoint"]["pending"] == [raw] and evaluation["deferred_source_ids"] == [raw["source_id"]]
    assert evaluation["provenance_complete"] and ledger.states == {}
    assert incidents.verify_cloud_evaluation(bundle["checkpoint"], evaluation, ledger.states,
                                             shadow=True, worker=evaluation["worker"])
    heartbeat = Mock()
    assert cloud.confirm_heartbeat(bundle, deepcopy(bundle), delivery_verified=True, heartbeat=heartbeat)
    forged = deepcopy(evaluation)
    forged["deferred_proofs"][raw["source_id"]]["artifact"] = "tag:latest"
    with pytest.raises(ValueError):
        cloud.unresolved_pending(bundle["checkpoint"], forged)
    covered = deepcopy(bundle["checkpoint"])
    covered["covered_through"] = at(10)
    with pytest.raises(ValueError, match="coverage cutoff"):
        cloud.unresolved_pending(covered, evaluation)


def test_runtime_dict_and_list_results_emit_existing_generation_pinned_receipts_without_writes(capsys):
    receipt = run_receipt()
    record = {"status": "success", "asset_slug": SUBJECT["asset_slug"], "release_date": SUBJECT["release"],
              "run_record": {"path": receipt["uri"], "generation": receipt["generation"]}}
    for result in (record, [record]):
        runtime.run_job_main(lambda: result, logger=Mock(), failure_message="failed")
        output = capsys.readouterr()
        lines = [json.loads(line) for line in output.err.splitlines()]
        assert lines[0]["details"]["run_record_generation"] == 123
        assert lines[0]["subject"] == SUBJECT and lines[-1]["details"]["record_count"] == 1
        assert json.loads(output.out) == result


def test_readonly_run_record_transport_pins_generation_and_rejects_dataset_reads():
    receipt = run_receipt()
    body = json.dumps({"asset_slug": receipt["asset_slug"], "run_date": receipt["run_date"], "release_date": receipt["release_date"],
                       "status": "success", "release_paths": receipt["published_paths"]}).encode()
    session = Mock()
    metadata, media = Mock(), Mock()
    metadata.status_code = media.status_code = 200
    metadata.json.return_value = {"bucket": cloud.BUCKET, "name": receipt["uri"].split(f"gs://{cloud.BUCKET}/", 1)[1], "generation": "123", "size": str(len(body)), "timeCreated": receipt["created_at"], "updated": receipt["updated_at"]}
    media.iter_content.return_value = [body[:25], body[25:]]
    session.request.side_effect = [metadata, media]
    actual = cloud.CloudReader(session).run_receipt(receipt["uri"], generation=123)
    assert actual["generation"] == 123 and actual["published_paths"] == receipt["published_paths"]
    assert [call.kwargs["params"] for call in session.request.call_args_list] == [
        {"generation": "123", "fields": "bucket,name,generation,size,timeCreated,updated"}, {"generation": "123", "alt": "media"}]
    media.close.assert_called_once()
    for uri in (receipt["uri"].replace("/runs/", "/latest/"), receipt["published_paths"][0]["path"], receipt["uri"].replace(cloud.BUCKET, "another-bucket")):
        with pytest.raises(ValueError, match="read refused"):
            cloud.CloudReader(session).run_receipt(uri, generation=123)
    assert session.request.call_count == 2


def test_heartbeat_finish_time_is_after_complete_enumeration_without_advancing_scan_end():
    api = Mock()
    api.entries.return_value = []
    started = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
    finished = started + timedelta(minutes=29)
    result = cloud.collect(None, api, now=started, clock=lambda: finished)
    assert result["last_cycle"]["completed_at"] == cloud.timestamp(finished)
    assert result["covered_through"] == cloud.timestamp(started - cloud.INGESTION_LAG)
