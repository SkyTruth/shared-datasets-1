#!/usr/bin/env python3
"""Read-only evidence gate for protected operational Slack route changes.

Configuration never enables itself. Reviewed source names immutable evidence;
the protected worker verifies referenced signed cycles and current cloud facts
before applying its exact saved plan. Unknown policies are reported, not edited.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import monitoring_routing_audit as routing
from scripts import cloud_operational_observations as collector

ROOT = Path(__file__).resolve().parents[1]
CONFIG = "catalog/operational-slack-rollout.json"
SCHEMA = "shared-datasets-operational-slack-cutover-v1"
OPERATIONAL_POLICIES = {"google_monitoring_alert_policy." + name for name in (
    "scheduled_ingestion_cloud_run_failure", "wdpa_execution_observer_unhealthy",
    "release_awaiting_identity_decision", "scheduled_ingestion_scheduler_failure",
)}
HEALTH = "google_monitoring_alert_policy.operational_reconciliation_unhealthy"
REQUIRED_NATIVE = routing.SECURITY_ADDRESSES | {HEALTH}
MIN_SHADOW = timedelta(hours=24)
MAX_CYCLE_GAP = timedelta(minutes=10)
FRESHNESS = timedelta(minutes=10)
SHA = re.compile(r"[0-9a-f]{40}")
HASH = re.compile(r"[0-9a-f]{64}")
JOBS = ("wdpa-monthly", "eamlis-monthly", "sea-ice-daily", "mpatlas-monthly")
RUNTIME_FILES = ("ingestion/common/legacy_runtime_notifications.py", "ingestion/common/translation_notices.py",
                 "ingestion/common/feature_metadata.py", "ingestion/common/operational_events.py", "scripts/slack_notify.py")
RUNTIME_SECRET = "shared-datasets-slack-webhook-url"
PROOF_SCHEMA = "shared-datasets-operational-cutover-verification-v1"


def require(condition, message):
    if not condition:
        raise ValueError("OPERATIONAL_CUTOVER: " + message)


def strict_json(value):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            require(key not in result, "duplicate JSON field")
            result[key] = item
        return result
    return json.loads(value, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")))


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def config(value):
    require(isinstance(value, dict) and set(value) == {"schema", "mode", "cutover_evidence", "retire_runtime_webhook_access", "retirement_evidence"}
            and value["schema"] == "shared-datasets-operational-slack-rollout-v1" and value["mode"] in {"native", "unified"}
            and type(value["retire_runtime_webhook_access"]) is bool, "invalid rollout configuration")
    for key in ("cutover_evidence", "retirement_evidence"):
        reference = value[key]
        require(reference is None or isinstance(reference, dict) and set(reference) == {"path", "sha256"}
                and isinstance(reference["path"], str) and re.fullmatch(r"[.]github/operational-cutovers/[a-z0-9][a-z0-9-]*[.]json", reference["path"])
                and isinstance(reference["sha256"], str) and HASH.fullmatch(reference["sha256"]), "invalid immutable evidence reference")
    require(value["mode"] != "unified" or value["cutover_evidence"] is not None, "unified routing requires immutable reviewed evidence")
    require(not value["retire_runtime_webhook_access"] or value["mode"] == "unified" and value["retirement_evidence"] is not None, "runtime secret retirement requires unified routing and independent drain evidence")
    return value


def route_changes(plan):
    result = []
    for row in plan.get("resource_changes", []):
        change = row.get("change", {})
        if row.get("type") != "google_monitoring_alert_policy" or change.get("actions") in ([], ["no-op"], ["read"]):
            continue
        before, after = change.get("before"), change.get("after")
        if before and after and set(before.get("notification_channels", [])) - set(after.get("notification_channels", [])):
            if row["address"] == routing.LEGACY_SCHEMA_ADDRESS:
                require((after.get("name") or after.get("id")) == routing.LEGACY_SCHEMA_ID and after.get("enabled") is False and after.get("notification_channels") == [], "invalid legacy schema tombstone")
                continue
            require(row["address"] in OPERATIONAL_POLICIES and change["actions"] == ["update"], "unregistered or independent policy route removal")
            require(after.get("notification_channels") == [], "partial route changes require a separate reviewed policy change")
            # Channel removal cannot carry an unshadowed detection change.
            for field in ("enabled", "conditions", "combiner", "severity"):
                require(before.get(field) == after.get(field), "cutover cannot change detection with routing")
            result.append(row["address"])
    return result


def secret_removals(plan):
    result = []
    for row in plan.get("resource_changes", []):
        if row.get("type") != "google_secret_manager_secret_iam_member" or "delete" not in row.get("change", {}).get("actions", []):
            continue
        before = row["change"].get("before", {})
        if before.get("secret_id", "").rsplit("/", 1)[-1] != RUNTIME_SECRET:
            continue
        require(row["address"] in {f'google_secret_manager_secret_iam_member.translation_notice["{job}"]' for job in ("wdpa", "eamlis")}
                and row["change"]["actions"] == ["delete"] and before.get("role") == "roles/secretmanager.secretAccessor"
                and before.get("member") == f'serviceAccount:{row["address"].split(chr(34))[1]}-monthly-job@{collector.PROJECT}.iam.gserviceaccount.com',
                "unregistered runtime webhook access removal")
        result.append(row["address"])
    return result


def baseline_plan(plan):
    """Audit installed before-values; reviewed channel removals are intentional."""
    result = deepcopy(plan)
    before = {row["address"]: row.get("change", {}).get("before") for row in plan.get("resource_changes", [])}
    for row in routing.resources(result["planned_values"]["root_module"]):
        if row.get("type") == "google_monitoring_alert_policy" and before.get(row["address"]):
            row["values"] = deepcopy(before[row["address"]])
    return result


def validate_shadow(evidence, *, load_cycle, contract_hash, policy_hash, policy_ids, policy_changed_at, heartbeat_points, now, revision_allowed):
    require(isinstance(evidence, dict) and set(evidence) == {"schema", "collector_contract_sha256", "policy_sha256", "policy_ids", "cycles", "runtime_deployments"}
            and evidence["schema"] == SCHEMA and evidence["collector_contract_sha256"] == contract_hash and evidence["policy_sha256"] == policy_hash,
            "evidence differs from the tested collector or policy contract")
    require(evidence["policy_ids"] == policy_ids, "evidence targets different live policies")
    references = evidence["cycles"]
    require(isinstance(references, list) and 2 <= len(references) <= 400, "require a bounded continuous shadow cycle history")
    cycles = []
    for reference in references:
        require(isinstance(reference, dict) and set(reference) == {"deployment_id", "manifest_sha256"} and type(reference["deployment_id"]) is int and reference["deployment_id"] > 0
                and isinstance(reference["manifest_sha256"], str) and HASH.fullmatch(reference["manifest_sha256"]), "invalid signed cycle reference")
        cycle = load_cycle(reference["deployment_id"])  # Verifies exact statuses, Sigstore signer and every part.
        manifest, checkpoint, evaluation = cycle["manifest"], cycle["checkpoint"], cycle["evaluation"]
        require(cycle["manifest_id"] == reference["deployment_id"] and sha256(canonical(manifest)) == reference["manifest_sha256"], "signed cycle differs from immutable review reference")
        require(manifest["mode"] == "shadow" and manifest["collector_contract_sha256"] == contract_hash and manifest["policy_sha256"] == policy_hash,
                "cycle used another collection or classification contract")
        require(SHA.fullmatch(manifest["source_sha"]) and revision_allowed(manifest["source_sha"]), "cycle does not originate from verified main ancestry")
        require(evaluation.get("complete") is True and evaluation.get("provenance_complete") is True and type(evaluation.get("public_delivery_count")) is int and evaluation["public_delivery_count"] == 0,
                "shadow cycle is incomplete, has provenance gaps or delivered publicly")
        coverage = checkpoint["last_cycle"]
        require(coverage["views"] == list(collector.VIEWS) and coverage["coverage_gaps"] == [] and not checkpoint["pending"], "shadow observation coverage is incomplete")
        completed = collector.instant(manifest["completed_at"])
        require(completed == collector.instant(coverage["completed_at"]) and collector.instant(manifest["covered_through"]) == collector.instant(checkpoint["covered_through"]), "cycle coverage timestamps disagree")
        require(policy_changed_at <= completed <= now, "heartbeat sample predates the current absence policy or lies in the future")
        require(completed in heartbeat_points, "verified cycle has no observed post-policy heartbeat sample")
        if cycles:
            previous, previous_at = cycles[-1]
            require(manifest["previous_cycle_id"] == previous["manifest_id"] and timedelta(0) < completed - previous_at <= MAX_CYCLE_GAP,
                    "shadow history has a missing, repeated or delayed reconciliation cycle")
            require(collector.instant(checkpoint["last_cycle"]["start"]) <= collector.instant(previous["checkpoint"]["covered_through"]), "shadow history leaves an observation gap")
        cycles.append((cycle, completed))
    require(cycles[-1][1] - cycles[0][1] >= MIN_SHADOW, "require at least 24 hours of successful shadow reconciliation")
    require(now - cycles[-1][1] <= FRESHNESS, "shadow evidence is stale")
    return {"first_cycle_id": cycles[0][0]["manifest_id"], "last_cycle_id": cycles[-1][0]["manifest_id"], "last_cycle_completed_at": collector.timestamp(cycles[-1][1]), "hours": (cycles[-1][1] - cycles[0][1]).total_seconds() / 3600}


def recheck_freshness(result, *, now):
    require(isinstance(result, dict) and type(result.get("cutover_authorized")) is bool, "invalid retained gate result")
    if not result["cutover_authorized"]:
        return
    fields = ["heartbeat_at"] + (["last_cycle_completed_at"] if "last_cycle_completed_at" in result else [])
    for field in fields:
        observed = collector.instant(result.get(field))
        require(timedelta(0) <= now - observed <= FRESHNESS, "verified cutover evidence aged beyond the final freshness deadline")


class LiveEvidence:
    def __init__(self, session):
        self.reader = collector.CloudReader(session)

    def pages(self, collection, field, *, params=None):
        page, seen, rows = "", set(), []
        while True:
            query = {**(params or {}), "pageSize": 1000}
            if page:
                query["pageToken"] = page
            value = self.reader.request("GET", f"https://monitoring.googleapis.com/v3/projects/{collector.PROJECT}/{collection}", params=query)
            require(isinstance(value.get(field, []), list), "incomplete live Monitoring enumeration")
            rows.extend(value.get(field, []))
            page = value.get("nextPageToken", "")
            require(isinstance(page, str) and page not in seen, "incomplete or repeated live Monitoring page")
            if not page:
                return rows
            seen.add(page)

    def heartbeat_points(self, *, now):
        values = self.pages("timeSeries", "timeSeries", params={"filter": f'metric.type="{collector.METRIC}" AND resource.type="global" AND resource.labels.project_id="{collector.PROJECT}"',
                      "interval.startTime": collector.timestamp(now - timedelta(hours=36)), "interval.endTime": collector.timestamp(now), "view": "FULL"})
        return {collector.instant(point["interval"]["endTime"]) for series in values for point in series.get("points", []) if point.get("value", {}).get("int64Value") == "1"}

    def verify_views(self, root):
        text = (root / "terraform/envs/prod/operational_observer.tf").read_text()
        matches = re.findall(r'operational_observer_log_filter\s*=\s*<<-EOT\n(.*?)\nEOT', text, re.S)
        require(len(matches) == 1, "reviewed filtered-view boundary is missing or ambiguous")
        expected = matches[0].replace("${var.project_id}", collector.PROJECT).replace("${var.region}", collector.REGION)
        require("${" not in expected, "unresolved reviewed filtered-view boundary")
        normalized = routing.filter_text(expected)
        for name in collector.VIEWS:
            value = self.reader.request("GET", "https://logging.googleapis.com/v2/" + name)
            require(value.get("name") == name and routing.filter_text(value.get("filter")) == normalized,
                    "live filtered-view identity or coverage boundary differs from reviewed Terraform")
        return {"views": list(collector.VIEWS), "filter_sha256": sha256(normalized.encode())}

    def job(self, job):
        return self.reader.request("GET", f"https://run.googleapis.com/v2/projects/{collector.PROJECT}/locations/{collector.REGION}/jobs/{job}")

    def executions(self, job):
        rows, page, seen = [], "", set()
        while True:
            value = self.reader.request("GET", f"https://run.googleapis.com/v2/projects/{collector.PROJECT}/locations/{collector.REGION}/jobs/{job}/executions",
                                        params={"pageSize": 1000, **({"pageToken": page} if page else {})})
            require(isinstance(value.get("executions", []), list), "incomplete execution drain enumeration")
            rows.extend(value.get("executions", []))
            page = value.get("nextPageToken", "")
            require(isinstance(page, str) and page not in seen, "invalid execution drain pagination")
            if not page:
                return rows
            seen.add(page)


def runtime_contract(root, revision):
    return sha256(canonical({path: sha256(subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=root)) for path in RUNTIME_FILES}))


def validate_drain(references, *, live, load_deployment, runtime_hash, revision_allowed):
    """No retained/active old image may send a notice after native routes clear."""
    require(isinstance(references, dict) and set(references) == set(JOBS), "require an exact signed deployment for every runtime bridge job")
    images = {}
    for job, reference in references.items():
        require(isinstance(reference, dict) and set(reference) == {"deployment_id", "runtime_sha256"} and type(reference["deployment_id"]) is int
                and reference["deployment_id"] > 0 and HASH.fullmatch(str(reference["runtime_sha256"])), "invalid drain deployment reference")
        record, payload = load_deployment(reference["deployment_id"])
        require(payload["target"] == job and revision_allowed(record["sha"]) and runtime_hash(record["sha"]) == reference["runtime_sha256"], "runtime deployment does not contain the exact reviewed bridge contract")
        image = payload["artifact"]
        current = live.job(job)
        name = f"projects/{collector.PROJECT}/locations/{collector.REGION}/jobs/{job}"
        require(current.get("name") == name and current.get("reconciling") is not True, "job configuration is unverified or still reconciling")
        containers = current.get("template", {}).get("template", {}).get("containers", [])
        require_unified_container(containers, image)
        identities = set()
        for execution in live.executions(job):
            identity = execution.get("name")
            require(isinstance(identity, str) and identity.startswith(name + "/executions/") and identity not in identities,
                    "duplicate or foreign execution in drain enumeration")
            identities.add(identity)
            if not execution.get("completionTime"):
                require_unified_container(execution.get("template", {}).get("containers", []), image)
        images[job] = image
    return images


def require_unified_container(containers, image):
    require(isinstance(containers, list) and len(containers) == 1 and containers[0].get("image") == image, "old or unverified runtime image has not drained")
    modes = [row for row in containers[0].get("env", []) if row.get("name") == "SHARED_DATASETS_OPERATIONAL_DELIVERY_MODE"]
    require(modes == [{"name": "SHARED_DATASETS_OPERATIONAL_DELIVERY_MODE", "value": "unified"}], "runtime bridge is still capable of native delivery")


def immutable_file(root, executor_sha, reference):
    raw = subprocess.check_output(["git", "show", f"{executor_sha}:{reference['path']}"], cwd=root)
    require(sha256(raw) == reference["sha256"] and (root / reference["path"]).read_bytes() == raw, "reviewed evidence bytes differ from tested main")
    return strict_json(raw)


def verify_plan(plan, rollout, *, now, root=ROOT, executor_sha=None, live=None, load_cycle=None, load_deployment=None, contract_hash=None, policy_hash=None, revision_allowed=None, runtime_hash=None, scope=(), prior_activation=None, clock=None):
    rollout = config(rollout)
    removals = route_changes(plan)
    require(not removals or rollout["mode"] == "unified", "native mode cannot remove an operational Slack route")
    retirements = secret_removals(plan)
    require(not retirements or rollout["retire_runtime_webhook_access"], "runtime webhook removal requires reviewed retirement configuration")
    policy_sync = bool(set(scope) & OPERATIONAL_POLICIES) and rollout["mode"] == "unified"
    if not removals and not retirements and not policy_sync:
        return {"mode": rollout["mode"], "route_removals": [], "runtime_secret_removals": [], "cutover_authorized": False}
    require(executor_sha is not None and SHA.fullmatch(executor_sha), "exact tested main executor is required")
    desired = routing.expected_policies(plan)
    require(all(desired[address]["enabled"] and desired[address]["notification_channels"] for address in REQUIRED_NATIVE), "independent security and pipeline-health delivery must stay enabled")
    require(all(desired[address]["enabled"] and desired[address]["notification_channels"] == [] for address in OPERATIONAL_POLICIES), "unified cutover must preserve detection and clear all registered operational channels")
    view_proof = live.verify_views(root)
    policies, channels = live.pages("alertPolicies", "alertPolicies"), live.pages("notificationChannels", "notificationChannels")
    inventory = routing.audit(baseline_plan(plan), policies, channels)
    require(not inventory["findings"], "fresh live routing audit has discrepancies; review them without disabling unknown policies")
    ids = {address: value.get("name") or value.get("id") for address, value in desired.items()}
    require(all(isinstance(name, str) and re.fullmatch(f"projects/{collector.PROJECT}/alertPolicies/[0-9]+", name) for name in ids.values()), "all exact live policy IDs must be established before cutover")
    health = next(policy for policy in policies if policy["name"] == ids[HEALTH])
    changed = max(collector.instant(health["creationRecord"]["mutateTime"]), collector.instant(health.get("mutationRecord", health["creationRecord"])["mutateTime"]))
    evidence = immutable_file(root, executor_sha, rollout["cutover_evidence"])
    heartbeats = live.heartbeat_points(now=now)
    if removals:
        result = validate_shadow(evidence, load_cycle=load_cycle, contract_hash=contract_hash, policy_hash=policy_hash, policy_ids=ids,
                                 policy_changed_at=changed, heartbeat_points=heartbeats, now=now, revision_allowed=revision_allowed)
    else:
        # Once the gate has applied, its signed protected receipt retains the
        # reviewed shadow proof. Requiring fresh *shadow* cycles thereafter
        # would make later retirement impossible while the worker is unified.
        require(isinstance(prior_activation, dict) and prior_activation.get("mode") == "unified", "no-op cutover or later retirement requires the verified original policy apply")
        require(any(changed <= point <= now and now - point <= FRESHNESS for point in heartbeats), "unified pipeline has no fresh post-policy heartbeat")
        require(evidence.get("policy_ids") == ids, "original cutover evidence targets different live policies")
        result = {"original_policy_deployment_id": prior_activation["policy_deployment_id"]}
    images = validate_drain(evidence["runtime_deployments"], live=live, load_deployment=load_deployment, runtime_hash=runtime_hash, revision_allowed=revision_allowed)
    if retirements:
        retirement = immutable_file(root, executor_sha, rollout["retirement_evidence"])
        require(isinstance(retirement, dict) and set(retirement) == {"schema", "cutover_sha256", "runtime_deployments"}
                and retirement["schema"] == "shared-datasets-runtime-webhook-retirement-v1" and retirement["cutover_sha256"] == rollout["cutover_evidence"]["sha256"]
                and retirement["runtime_deployments"] == evidence["runtime_deployments"], "retirement differs from the verified cutover and drained deployments")
    result = {"mode": "unified", "route_removals": sorted(removals), "runtime_secret_removals": sorted(retirements), "drained_images": images, "filtered_view_proof": view_proof,
              "heartbeat_at": collector.timestamp(max(point for point in heartbeats if point <= now)), "cutover_authorized": True, **result}
    # Signature enumeration and cloud reads may take long enough to age the
    # original proof. Check a fresh wall clock after all those operations.
    recheck_freshness(result, now=clock() if clock else now)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-json", required=True, type=Path)
    parser.add_argument("--executor-sha", required=True)
    parser.add_argument("--scope", required=True, help="Exact newline-separated protected target scope")
    parser.add_argument("--output", type=Path, help="Retain verified IDs/timestamps for the immediate pre-apply freshness check")
    parser.add_argument("--recheck", type=Path, help="Recheck trusted runner-local proof against exact saved-plan/config bytes without repeating signature enumeration")
    args = parser.parse_args(argv)
    require(SHA.fullmatch(args.executor_sha), "invalid tested executor revision")
    raw_config = subprocess.check_output(["git", "show", f"{args.executor_sha}:{CONFIG}"], cwd=ROOT)
    require((ROOT / CONFIG).read_bytes() == raw_config, "rollout configuration differs from exact tested main")
    raw_plan = args.plan_json.read_bytes()
    plan, rollout = strict_json(raw_plan), config(strict_json(raw_config))
    kwargs = {}
    scope = tuple(filter(None, args.scope.splitlines()))
    binding = {"schema": PROOF_SCHEMA, "executor_sha": args.executor_sha, "plan_sha256": sha256(raw_plan), "config_sha256": sha256(raw_config), "scope_sha256": sha256(canonical(scope))}
    if args.recheck:
        proof = strict_json(args.recheck.read_bytes())
        require(isinstance(proof, dict) and set(proof) == {*binding, "result"} and all(proof[key] == value for key, value in binding.items()), "retained gate proof differs from exact executor, saved plan, configuration or target scope")
        recheck_freshness(proof["result"], now=datetime.now(timezone.utc))
        print(json.dumps(proof["result"], sort_keys=True))
        return 0
    if route_changes(plan) or secret_removals(plan) or rollout["mode"] == "unified" and set(scope) & OPERATIONAL_POLICIES:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
        from scripts import cloud_observation_ledger as ledger
        from scripts import deployment_revision as deployments
        from scripts.operational_delivery_activation import activation
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        workdir = Path(os.environ["RUNNER_TEMP"]) / "operational-cutover-verification"
        api = deployments.GitHub()
        repository = os.environ["GITHUB_REPOSITORY"]
        verifier = ledger.CycleLedger(api, None, workdir)
        def load_deployment(identity):
            record = api.get(f"repos/{repository}/deployments/{identity}")
            payload = deployments.verify_record(api, repository, record)
            statuses = deployments.verified_statuses(api, repository, record)
            require(statuses and statuses[0].get("state") == "success" and statuses[0].get("description") in {"applied", "verified"}, "runtime deployment has no verified terminal success")
            return record, payload
        expected_runtime = runtime_contract(ROOT, args.executor_sha)
        def runtime_hash(revision):
            value = runtime_contract(ROOT, revision)
            require(value == expected_runtime, "runtime image uses another bridge protocol")
            return value
        kwargs = {"live": LiveEvidence(AuthorizedSession(credentials, max_refresh_attempts=0, refresh_timeout=30)), "load_cycle": verifier.verify_cycle_id,
                  "load_deployment": load_deployment, "runtime_hash": runtime_hash, "contract_hash": ledger.contract_hash(ROOT), "policy_hash": ledger.policy_hash(ROOT), "revision_allowed": lambda sha: deployments.git_ancestor(sha, args.executor_sha)}
        if rollout["mode"] == "unified" and not route_changes(plan):
            kwargs["prior_activation"] = activation(api, repository, ROOT, args.executor_sha)
    result = verify_plan(plan, rollout, now=datetime.now(timezone.utc), executor_sha=args.executor_sha, scope=scope, clock=lambda: datetime.now(timezone.utc), **kwargs)
    if args.output:
        pending = args.output.with_suffix(args.output.suffix + ".pending")
        pending.write_bytes(canonical({**binding, "result": result}))
        os.replace(pending, args.output)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
