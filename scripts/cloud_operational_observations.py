#!/usr/bin/env python3
"""Read-only, checkpointed Cloud Logging observations for shadow reconciliation.

The reconciler supplies authenticated persistence and policy evaluation. This
collector cannot start executions, publish data, repair infrastructure, or send
Slack. Only the fixed pipeline heartbeat is a permitted Google write.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote
from types import MappingProxyType

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingestion.common.operational_events import SCHEMA as EVENT_SCHEMA, validate as validate_event
from scripts import deployment_revision, gcp_read_retry, operational_log_views
from scripts.operational_retry import UNATTENDED, operation, request_timeout, run_read

PROJECT = "shared-datasets-1"
REGION = "us-central1"
BUCKET = "skytruth-shared-datasets-1"
SCHEMA = "shared-datasets-cloud-observation-checkpoint-v2"
METRIC = "custom.googleapis.com/shared_datasets/operational_reconciliation_heartbeat_v2"
VIEWS = operational_log_views.VIEWS
OVERLAP = timedelta(minutes=10)
INGESTION_LAG = timedelta(minutes=2)
HISTORY = timedelta(hours=24)
MAX_EVENTS = 100_000
NAME = re.compile(r"[a-z][a-z0-9-]{0,62}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
RUN_RECEIPT_SCHEMA = "shared-datasets-cloud-run-receipt-v1"
RUN_METHODS = ("/Jobs.RunJob", "google.cloud.run.v1.Jobs.RunJob", "google.cloud.run.v2.Jobs.RunJob")


@lru_cache(maxsize=1)
def registered_jobs():
    """The existing deployment graph owns the observation job boundary."""
    root = Path(__file__).resolve().parents[1] / ".github/workflows"
    return frozenset({name for name, workflow in deployment_revision.TARGET_WORKFLOWS.items()
                      if yaml.safe_load((root / workflow).read_text()).get("env", {}).get("JOB_NAME") == name}
                     | set(deployment_revision.AUXILIARY_CLOUD_JOBS))


def job_parent(job):
    require(isinstance(job, str) and NAME.fullmatch(job), "invalid execution job")
    return f"projects/{PROJECT}/locations/{REGION}/jobs/{job}"


def validate_inventory(value):
    require(isinstance(value, dict) and set(value) == set(registered_jobs()), "execution inventory did not cover every registered job")
    for job, row in value.items():
        require(isinstance(row, dict) and set(row) == {"parent", "retained_count", "active_count", "sha256"}
                and row["parent"] == job_parent(job), "invalid execution inventory identity")
        require(type(row["retained_count"]) is int and 0 <= row["retained_count"] <= MAX_EVENTS
                and type(row["active_count"]) is int and 0 <= row["active_count"] <= row["retained_count"]
                and isinstance(row["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]), "invalid execution inventory proof")
    return value


def observation_filter():
    """Payload/severity predicates belong in queries, not log view IAM filters."""
    methods = " OR ".join(f'protoPayload.methodName="{method}"' for method in RUN_METHODS)
    return f'''resource.labels.project_id="{PROJECT}" resource.labels.location="{REGION}"
((resource.type="cloud_run_job" ((log_id("run.googleapis.com/stderr") AND jsonPayload.schema="{EVENT_SCHEMA}")
OR ((log_id("cloudaudit.googleapis.com/activity") OR log_id("cloudaudit.googleapis.com/data_access"))
AND protoPayload.serviceName="run.googleapis.com" AND ({methods}))))
OR (resource.type="cloud_scheduler_job" log_id("cloudscheduler.googleapis.com/executions") severity>=ERROR))'''


@lru_cache(maxsize=1)
def asset_roots():
    """The catalog is the registered asset boundary, not a bucket-wide grant."""
    with (Path(__file__).resolve().parents[1] / "catalog/shared-datasets-catalog.csv").open() as stream:
        rows = csv.DictReader(stream)
        return MappingProxyType({row["asset_slug"]: row["canonical_path"].removeprefix(f"gs://{BUCKET}/").split("/latest/")[0].split("/releases/")[0]
                for row in rows if row["canonical_path"].startswith(f"gs://{BUCKET}/")
                and ("/latest/" in row["canonical_path"] or "/releases/" in row["canonical_path"])})


def validate_run_receipt(value):
    require(isinstance(value, dict) and set(value) == {"schema", "uri", "generation", "size", "sha256", "asset_slug", "run_date", "release_date", "status", "published_paths", "created_at", "updated_at"}
            and value["schema"] == RUN_RECEIPT_SCHEMA, "invalid canonical run-record proof")
    root = asset_roots().get(value["asset_slug"])
    require(root is not None and value["uri"] == f"gs://{BUCKET}/{root}/runs/{value['run_date']}.json", "unregistered run-record identity")
    require(type(value["generation"]) is int and value["generation"] > 0 and type(value["size"]) is int and 0 < value["size"] <= 1024 * 1024
            and isinstance(value["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]), "invalid immutable run-record proof")
    for field in ("run_date", "release_date"):
        require(isinstance(value[field], str) and re.fullmatch(r"\d{4}-\d\d-\d\d", value[field]), "invalid run-record date")
        datetime.strptime(value[field], "%Y-%m-%d")
    require(value["status"] in {"success", "skipped"} and isinstance(value["published_paths"], list) and len(value["published_paths"]) <= 32, "invalid run-record outcome")
    seen = set()
    for row in value["published_paths"]:
        require(isinstance(row, dict) and set(row) == {"path", "generation"} and isinstance(row["path"], str)
                and row["path"].startswith(f"gs://{BUCKET}/{root}/releases/{value['release_date']}/")
                and ".." not in row["path"].split("/") and row["path"] not in seen
                and type(row["generation"]) is int and row["generation"] > 0, "invalid published generation proof")
        seen.add(row["path"])
    require(value["status"] != "success" or bool(seen), "successful run record lacks published generations")
    require(instant(value["created_at"]) <= instant(value["updated_at"]), "invalid authoritative run-record write interval")
    return value


def require(condition, message):
    if not condition:
        raise ValueError("CLOUD_OBSERVATION: " + message)


def instant(value):
    require(isinstance(value, str), "timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0), "timestamp must be UTC")
    return parsed


def timestamp(value):
    require(value.tzinfo is not None and value.utcoffset() == timedelta(0), "clock must be UTC")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_checkpoint(value):
    require(isinstance(value, dict) and set(value) == {"schema", "covered_through", "seen", "pending", "watching", "last_cycle"} and value["schema"] == SCHEMA, "invalid checkpoint schema")
    instant(value["covered_through"])
    require(isinstance(value["seen"], dict) and len(value["seen"]) <= MAX_EVENTS, "invalid deduplication state")
    for source, row in value["seen"].items():
        require(isinstance(source, str) and re.fullmatch(r"cloud(?:log|exec|api):[0-9a-f]{64}", source) and isinstance(row, dict) and set(row) == {"timestamp", "sha256"}, "invalid source checkpoint")
        instant(row["timestamp"])
        require(isinstance(row["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]), "invalid source digest")
    require(isinstance(value["pending"], list) and len(value["pending"]) <= MAX_EVENTS, "invalid pending observations")
    require(isinstance(value["watching"], dict) and len(value["watching"]) <= MAX_EVENTS, "invalid execution watch state")
    require(isinstance(value["last_cycle"], dict), "invalid coverage evidence")
    completed = instant(value["last_cycle"].get("completed_at"))
    from scripts.cloud_incident_adapter import validate_fact
    for fact in [*value["pending"], *value["watching"].values()]:
        validate_fact(fact)
        # Canonical translation polls happen later, during policy evaluation,
        # and have their own precise observed_by(evaluation clock) boundary.
        if fact["kind"] != "translation_manifest":
            require(instant(fact["observed_at"]) <= completed, "source observation is beyond the completed coverage clock")
        execution = fact.get("execution", {})
        require(all(instant(execution[key]) <= completed for key in ("create_time", "start_time", "completion_time")
                    if execution.get(key) is not None), "execution metadata is beyond the completed coverage clock")
    for source, fact in value["watching"].items():
        require(isinstance(fact, dict) and fact.get("source_id") == source and isinstance(fact.get("execution"), dict), "invalid pending execution proof")
        execution = fact["execution"]
        if execution.get("terminal") is False:
            require(source in value["seen"], "active execution lacks a source checkpoint")
        else:
            require(source.startswith("cloudapi:") and source not in value["seen"]
                    and execution.get("terminal") is True and execution.get("completion_time") is not None
                    and instant(execution["completion_time"]) > instant(value["covered_through"]), "invalid future execution buffer")
    validate_inventory(value["last_cycle"].get("execution_inventory"))
    return value


def validate_entry(entry, start, end):
    require(isinstance(entry, dict), "log entry must be an object")
    require(isinstance(entry.get("logName"), str) and entry["logName"].startswith(f"projects/{PROJECT}/logs/"), "foreign log identity")
    require(isinstance(entry.get("insertId"), str) and 0 < len(entry["insertId"]) <= 1024, "log source insertId is missing")
    observed = instant(entry.get("timestamp"))
    require(start <= observed < end, "entry lies outside the requested coverage window")
    resource = entry.get("resource", {})
    labels = resource.get("labels", {})
    require(labels.get("project_id") == PROJECT and labels.get("location") == REGION, "foreign log resource")
    kind = resource.get("type")
    require(kind in {"cloud_run_job", "cloud_scheduler_job"}, "unsupported log resource")
    name = labels.get("job_name" if kind == "cloud_run_job" else "job_id")
    require(isinstance(name, str) and NAME.fullmatch(name), "invalid job identity")
    source = "cloudlog:" + digest({"logName": entry["logName"], "insertId": entry["insertId"]})
    return source, observed, kind, name


def execution_name(entry):
    value = entry.get("labels", {}).get("run.googleapis.com/execution_name")
    require(isinstance(value, str) and NAME.fullmatch(value), "Cloud Run event has no exact execution identity")
    return value


def authoritative_execution(api, job, execution):
    name = job_parent(job) + "/executions/" + execution
    return execution_proof(api.execution(name), job, name=name)


def execution_proof(result, job, *, name=None):
    """Construct one exact v2 execution proof, independently of audit requests."""
    prefix = job_parent(job) + "/executions/"
    require(isinstance(result, dict) and isinstance(result.get("name"), str)
            and re.fullmatch(re.escape(prefix) + r"[a-z][a-z0-9-]{0,62}", result["name"]), "execution metadata belongs to another operation")
    name = name or result["name"]
    require(isinstance(result, dict) and result.get("name") == name, "execution metadata belongs to another operation")
    containers = result.get("template", {}).get("containers", [])
    require(isinstance(containers, list) and all(isinstance(row, dict) for row in containers), "execution container metadata is incomplete")
    images = [row.get("image") for row in containers]
    require(images and all(isinstance(value, str) and value for value in images), "execution container metadata is incomplete")
    uid = result.get("uid")
    require(uid is None or isinstance(uid, str) and 0 < len(uid) <= 200 and not any(ord(char) < 32 for char in uid), "invalid authoritative execution UID")
    immutable = len(images) == 1 and "@" in images[0] and DIGEST.fullmatch(images[0].rsplit("@", 1)[1])
    revision = images[0].rsplit("@", 1)[1] if immutable else None
    gaps = [] if immutable else ["execution_image_digest_unresolved"]
    if not immutable and isinstance(uid, str) and re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", uid) and uid.replace("-", "") != "0" * 32:
        # This is an explicit unresolved-provenance anchor, never an image
        # digest. Canonical policy permits it only for immediate action with
        # uncertain mutation, no retry and no supervision suppression.
        revision = "cloudrun-execution:" + uid
    terminal = result.get("completionTime") is not None
    conditions = result.get("conditions", [])
    require(isinstance(conditions, list) and all(isinstance(row, dict) for row in conditions), "execution conditions are incomplete")
    completed = [row for row in conditions if row.get("type") == "Completed"]
    success = terminal and len(completed) == 1 and completed[0].get("state") == "CONDITION_SUCCEEDED"
    created = instant(result.get("createTime"))
    started = instant(result["startTime"]) if result.get("startTime") is not None else created
    require(created <= started and (not terminal or started <= instant(result["completionTime"])), "execution timestamps are inconsistent")
    return {"name": name, "uid": uid, "revision": revision, "coverage_gaps": gaps, "terminal": terminal, "success": success,
            "completion_time": result.get("completionTime"), "create_time": result.get("createTime"), "start_time": result.get("startTime"), "images": images}


def api_execution_fact(proof, job):
    return {"source_id": "cloudapi:" + digest(proof), "observed_at": proof["completion_time"] or proof["start_time"] or proof["create_time"],
            "resource_type": "cloud_run_job", "job": job, "kind": "execution", "execution": proof,
            "evidence": {"api": "run.googleapis.com/v2", "resource": proof["name"]}}


def request_evidence(audit, job):
    """An API result has no execution identity, even when its response has a name."""
    require(isinstance(audit, dict) and audit.get("serviceName") == "run.googleapis.com"
            and audit.get("methodName") in RUN_METHODS, "unsupported execution observation")
    request = audit.get("request", {})
    require(isinstance(request, dict), "execution request body is malformed")
    proof = {"method": audit["methodName"], "resource_name": audit.get("resourceName"),
             "request_name": request.get("name"), "status": deepcopy(audit.get("status"))}
    reason, _ = request_outcome(proof, job)
    return {"request": proof, "mutation_status": "uncertain",
            "coverage_gaps": [] if reason == "accepted" else ["execution_request_outcome_unverified"]}


def request_outcome(proof, job):
    require(isinstance(proof, dict) and set(proof) == {"method", "resource_name", "request_name", "status"}
            and proof["method"] in RUN_METHODS, "invalid API request proof")
    names = {job_parent(job), f"namespaces/{PROJECT}/jobs/{job}"}
    require(proof["resource_name"] is None or proof["resource_name"] in names, "audit resource differs from authenticated job")
    require(proof["request_name"] is None or proof["request_name"] in names, "audit request differs from authenticated job")
    status = proof["status"]
    require(status is None or isinstance(status, dict), "execution request status is malformed")
    require(status is None or set(status) <= {"code", "message", "details"}, "execution request status has unsupported fields")
    code = status.get("code", 0) if status is not None else None
    require(code is None and status is None or type(code) is int and code >= 0, "execution request status code is malformed")
    require(status is None or isinstance(status.get("message", ""), str)
            and isinstance(status.get("details", []), list), "execution request status details are malformed")
    # Message/detail-bearing non-errors are contradictory rather than success.
    accepted = code == 0 and not status.get("message") and not status.get("details")
    reason = "accepted" if accepted else "permission_denied" if code in {7, 16} else "unknown"
    return reason, code


def normalize(entry, source, api, execution_index):
    resource = entry["resource"]
    labels = resource["labels"]
    kind = resource["type"]
    job = labels["job_name" if kind == "cloud_run_job" else "job_id"]
    result = {"source_id": source, "observed_at": entry["timestamp"], "resource_type": kind, "job": job,
              "evidence": {"log_name": entry["logName"], "insert_id": entry["insertId"]}}
    payload = entry.get("jsonPayload", {})
    if payload.get("schema") == EVENT_SCHEMA:
        payload = {**payload, "severity": payload.get("severity", entry.get("severity"))}
        validate_event(payload)
        require(kind == "cloud_run_job", "runtime fact has a non-runtime resource")
        execution = execution_name(entry)
        require(payload["context"]["job"] == job and payload["context"]["execution"] == execution, "runtime payload disagrees with authenticated log context")
        name = job_parent(job) + "/executions/" + execution
        if job in registered_jobs():
            require(name in execution_index, "runtime execution is absent from complete job inventory")
            proof = execution_index[name]
        else:
            proof = authoritative_execution(api, job, execution)
        result.update(kind="runtime", fact=payload, execution=proof)
        receipt = payload["details"].get("receipt_uri")
        if receipt and payload["details"].get("receipt_generation") is not None:
            result["receipt_metadata"] = api.receipt_metadata(receipt, generation=payload["details"].get("receipt_generation"))
        if payload["details"].get("run_record_uri"):
            result["run_receipt"] = api.run_receipt(payload["details"]["run_record_uri"], generation=payload["details"]["run_record_generation"])
    elif kind == "cloud_run_job":
        result.update(kind="execution_request", **request_evidence(entry.get("protoPayload", {}), job))
    else:
        require(entry.get("severity") in {"ERROR", "CRITICAL", "ALERT", "EMERGENCY"}, "unrelated healthy Scheduler event")
        result.update(kind="scheduler_dispatch", mutation_status="uncertain", coverage_gaps=["scheduler_dispatch_outcome_unverified"])
    return result


def execution_snapshot(api):
    """Validate every retained execution before selecting bounded observations."""
    index, inventory = {}, {}
    for job in sorted(registered_jobs()):
        rows = []
        active = 0
        for raw in api.executions(job):
            proof = execution_proof(raw, job)
            require(proof["name"] not in index, "execution pagination duplicated an identity")
            index[proof["name"]] = proof
            rows.append(raw)
            active += int(not proof["terminal"])
            require(len(index) <= MAX_EVENTS, "execution inventory exceeds bounded snapshot size")
        inventory[job] = {"parent": job_parent(job), "retained_count": len(rows), "active_count": active,
                          "sha256": digest(sorted(rows, key=lambda row: row["name"]))}
    return index, validate_inventory(inventory)


def selected_executions(index, watching, *, start, end):
    tracked = {fact["execution"]["name"] for fact in watching.values()}
    selected, latest = {}, {}
    for name, proof in index.items():
        job = name.split("/jobs/", 1)[1].split("/", 1)[0]
        if not proof["terminal"]:
            selected[name] = proof
            continue
        completed = instant(proof["completion_time"])
        if completed > end:
            selected[name] = proof  # Buffer it without a failure/recovery until its cutoff.
            continue
        if name in tracked or not proof["success"] and start <= completed:
            selected[name] = proof
        if proof["success"] and (job not in latest or (completed, name) >
                                 (instant(latest[job]["completion_time"]), latest[job]["name"])):
            latest[job] = proof
    selected.update((proof["name"], proof) for proof in latest.values())
    return [api_execution_fact(proof, name.split("/jobs/", 1)[1].split("/", 1)[0])
            for name, proof in sorted(selected.items())]


def collect(previous, api, *, now, clock=None):
    """Build a complete candidate checkpoint without mutating prior state.

    Enumeration and metadata lookups share one safe-read budget. A retry starts
    the entire snapshot over, discarding partial pagination and local evidence.
    """
    end = now - INGESTION_LAG
    if previous is None:
        covered, seen, pending, watching = end - HISTORY, {}, [], {}
    else:
        validate_checkpoint(previous)
        covered, seen, pending, watching = instant(previous["covered_through"]), previous["seen"], previous["pending"], previous["watching"]
        require(covered <= end, "clock moved backwards")
        require(end - covered <= HISTORY, f"coverage gap exceeds bounded historical replay: {timestamp(covered)} to {timestamp(end - HISTORY)}")
    start = max(covered - OVERLAP, end - HISTORY)

    def snapshot():
        execution_index, inventory = execution_snapshot(api)
        found, new, next_watching = {}, [], {}

        def add_api_fact(fact):
            source = fact["source_id"]
            if fact["execution"]["terminal"] and instant(fact["execution"]["completion_time"]) > end:
                next_watching[source] = fact
                return
            fingerprint = digest(fact)
            old = found.get(source) or seen.get(source)
            require(old is None or old["sha256"] == fingerprint, "API execution identity changed immutable proof")
            if old is None:
                new.append(fact)
                found[source] = {"timestamp": timestamp(instant(fact["observed_at"])), "sha256": fingerprint}
            if not fact["execution"]["terminal"]:
                next_watching[source] = fact

        for source, fact in watching.items():
            if fact["job"] in registered_jobs():
                require(fact["execution"]["name"] in execution_index, "watched execution is absent from complete inventory")
                updated = execution_index[fact["execution"]["name"]]
            else:
                updated = authoritative_execution(api, fact["job"], fact["execution"]["name"].rsplit("/", 1)[1])
            old = fact["execution"]
            require(not old.get("uid") or updated.get("uid") == old["uid"], "immutable execution UID changed")
            require(not old["revision"] or not old["revision"].startswith("sha256:") or updated["revision"] == old["revision"], "immutable execution image changed")
            require(not old["terminal"] or updated == old, "terminal execution metadata changed")
            if source.startswith("cloudapi:"):
                continue  # The same complete inventory constructs its next proof below.
            if updated != fact["execution"]:
                transition = {**deepcopy(fact), "source_id": "cloudexec:" + digest({"source": source, "proof": updated}), "execution": updated, "observed_at": timestamp(now)}
                new.append(transition)
                found[transition["source_id"]] = {"timestamp": timestamp(now), "sha256": digest(transition)}
            if not updated["terminal"]:
                next_watching[source] = {**deepcopy(fact), "execution": updated}
        selected = selected_executions(execution_index, watching, start=start, end=end)
        for fact in selected:
            add_api_fact(fact)
        for view in VIEWS:
            for entry in api.entries(view, start=start, end=end):
                source, observed, _, _ = validate_entry(entry, start, end)
                fingerprint = digest(entry)
                old = found.get(source) or seen.get(source)
                if old is not None:
                    require(old["sha256"] == fingerprint, "source identity was reused with different log bytes")
                    continue
                found[source] = {"timestamp": timestamp(observed), "sha256": fingerprint}
                fact = normalize(entry, source, api, execution_index)
                new.append(fact)
                if fact.get("execution", {}).get("terminal") is False:
                    next_watching[source] = fact
                require(len(found) <= MAX_EVENTS, "enumeration exceeds bounded snapshot size")
        selected_sources = {fact["source_id"] for fact in selected}
        retained = {source: row for source, row in seen.items() if instant(row["timestamp"]) >= end - HISTORY
                    or source in next_watching or source in selected_sources}
        retained.update(found)
        require(len(retained) <= MAX_EVENTS and len(pending) + len(new) <= MAX_EVENTS, "operational backlog exceeds bounded state")
        gaps = sorted({gap for fact in [*pending, *new, *next_watching.values()] for gap in [*fact.get("coverage_gaps", []), *fact.get("execution", {}).get("coverage_gaps", [])]})
        completed = clock() if clock is not None else now
        require(completed >= now, "completion clock moved backwards")
        require(all(instant(proof["completion_time"] or proof["start_time"] or proof["create_time"]) <= completed
                    for proof in execution_index.values()), "execution metadata is from the future")
        request_timeout(30)  # A slow final page cannot return an expired writable snapshot.
        return validate_checkpoint({"schema": SCHEMA, "covered_through": timestamp(end), "seen": retained,
                                   "pending": [*deepcopy(pending), *new], "watching": next_watching, "last_cycle": {"start": timestamp(start), "end": timestamp(end), "completed_at": timestamp(completed), "new_events": len(new), "views": list(VIEWS), "execution_inventory": inventory, "coverage_gaps": gaps, "overlap_seconds": int(OVERLAP.total_seconds()), "ingestion_lag_seconds": int(INGESTION_LAG.total_seconds())}})

    with operation(profile=UNATTENDED):
        return run_read(snapshot, classify=gcp_read_retry.classify, label="cloud operational observation snapshot")


def evaluated_collection(candidate, evaluation):
    """Produce exact checkpoint/evaluation bytes for the signed ledger proposal."""
    validate_checkpoint(candidate)
    require(isinstance(evaluation, dict) and evaluation.get("complete") is True, "policy evaluation is incomplete")
    resolved = evaluation.get("resolved_source_ids", [])
    require(isinstance(resolved, list) and all(isinstance(source, str) for source in resolved) and len(resolved) == len(set(resolved)) and set(resolved) <= {fact["source_id"] for fact in candidate["pending"]}, "invalid provenance resolution identities")
    policy_gaps = evaluation.get("provenance_gap_source_ids", [])
    require(isinstance(policy_gaps, list) and len(policy_gaps) == len(set(policy_gaps)) and set(policy_gaps) <= {fact["source_id"] for fact in candidate["pending"]}, "invalid evaluated provenance gap identities")
    deferred = evaluation.get("deferred_source_ids", [])
    require(isinstance(deferred, list) and len(deferred) == len(set(deferred)) and set(deferred) <= {fact["source_id"] for fact in candidate["pending"]}, "invalid deferred runtime coverage identities")
    unresolved = [fact for fact in candidate["pending"] if fact["source_id"] not in resolved and (fact["source_id"] in deferred or fact["source_id"] in policy_gaps or fact.get("coverage_gaps") or fact.get("execution", {}).get("coverage_gaps"))]
    coverage = {**candidate["last_cycle"], "coverage_gaps": sorted({gap for fact in unresolved for gap in [*fact.get("coverage_gaps", []), *fact.get("execution", {}).get("coverage_gaps", []), *(["unregistered_or_unverified_cloud_source"] if fact["source_id"] in policy_gaps else [])]})}
    return {"checkpoint": {**candidate, "pending": unresolved, "last_cycle": coverage}, "evaluation": deepcopy(evaluation)}


def confirm_heartbeat(expected, verified_durable, *, delivery_verified, heartbeat):
    """Confirm exact freshly verified ledger bytes after delivery verification.

    The trusted notification worker must first verify the durable record's main
    provenance/signature and all delivery claims. Raw log/CLI input cannot supply
    verified_durable. Replaying old evidence cannot refresh the metric: the
    sample retains that cycle's original timestamp.
    """
    require(delivery_verified is True, "delivery verification must precede heartbeat")
    require(isinstance(expected, dict) and set(expected) == {"checkpoint", "evaluation"}, "invalid reconciliation proposal")
    validate_checkpoint(expected["checkpoint"])
    require(isinstance(verified_durable, dict) and digest(expected) == digest(verified_durable), "verified durable checkpoint differs from proposal")
    evaluation = expected["evaluation"]
    require(isinstance(evaluation, dict) and evaluation.get("complete") is True, "policy evaluation is incomplete")
    checkpoint = expected["checkpoint"]
    if unresolved_pending(checkpoint, evaluation) or checkpoint["last_cycle"].get("coverage_gaps") or evaluation.get("provenance_complete", True) is not True:
        return False
    heartbeat(instant(checkpoint["last_cycle"]["completed_at"]))
    return True


def unresolved_pending(checkpoint, evaluation):
    """Future terminal metadata is a watch buffer, not a gap before the cutoff."""
    from scripts.deployment_revision import AUXILIARY_CLOUD_JOBS
    from scripts import cloud_incident_adapter as adapter
    deferred = evaluation.get("deferred_source_ids", [])
    proofs = evaluation.get("deferred_proofs", {})
    require(isinstance(deferred, list) and len(deferred) == len(set(deferred))
            and set(deferred) <= {fact["source_id"] for fact in checkpoint["pending"]}, "invalid deferred future observations")
    require(isinstance(proofs, dict) and set(proofs) == set(deferred), "deferred coverage lacks exact auxiliary artifact proofs")
    for fact in checkpoint["pending"]:
        if fact["source_id"] in deferred:
            execution = fact.get("execution", {})
            proof = adapter.validate_correlation(proofs[fact["source_id"]])
            require(fact.get("kind") == "execution" and fact.get("job") in AUXILIARY_CLOUD_JOBS
                    and proof.get("kind") == "auxiliary_artifact" and proof["execution"] == execution.get("name")
                    and proof["artifact"] in execution.get("images", []) and not adapter.provenance_gap(fact, proof)
                    and execution.get("terminal") is True and execution.get("completion_time") is not None
                    and instant(checkpoint["covered_through"]) < instant(execution["completion_time"]) <= instant(checkpoint["last_cycle"]["completed_at"]),
                    "deferred terminal observation does not lie beyond the complete coverage cutoff")
    return [fact for fact in checkpoint["pending"] if fact["source_id"] not in deferred]


def reconcile_cycle(previous, api, *, now, reconcile, persist, heartbeat):
    """Convenience transaction; persist returns freshly verified signed evidence.

    reconcile is idempotent by source_id. persist must atomically save the
    checkpoint/evaluation, verify its main signature and delivery state, and
    return that verified durable object. A write acknowledgement alone is not
    enough. The production workflow can instead call collect,
    evaluated_collection and confirm_heartbeat in separate trusted phases.
    """
    candidate = collect(previous, api, now=now)
    evidence = evaluated_collection(candidate, reconcile(deepcopy(candidate["pending"])))
    verified = persist(deepcopy(evidence))
    confirm_heartbeat(evidence, verified, delivery_verified=True, heartbeat=heartbeat)
    return evidence


class CloudReader:
    """The concrete adapter exposes only observation reads and one fixed metric."""

    def __init__(self, session):
        self.session = session

    def request(self, method, url, *, payload=None, params=None):
        from google.api_core import exceptions
        response = self.session.request(method, url, json=payload, params=params, timeout=request_timeout(30))
        if response.status_code != 200:
            raise exceptions.from_http_status(response.status_code, "Cloud operational observation request failed")
        value = response.json()
        require(isinstance(value, dict), "Google API returned a non-object payload")
        return value

    def entries(self, view, *, start, end):
        require(view in VIEWS, "unregistered filtered log view")
        page, seen = "", set()
        while True:
            payload = {"resourceNames": [view], "filter": f'timestamp>="{timestamp(start)}" AND timestamp<"{timestamp(end)}" AND ({observation_filter()})', "orderBy": "timestamp asc", "pageSize": 1000}
            if page:
                payload["pageToken"] = page
            value = self.request("POST", "https://logging.googleapis.com/v2/entries:list", payload=payload)
            require(set(value) <= {"entries", "nextPageToken"}, "unexpected log enumeration response")
            require(isinstance(value.get("entries", []), list), "incomplete log enumeration")
            yield from value.get("entries", [])
            page = value.get("nextPageToken", "")
            require(isinstance(page, str) and page not in seen, "invalid log pagination token")
            if not page:
                return
            seen.add(page)

    def execution(self, name):
        require(re.fullmatch(f"projects/{PROJECT}/locations/{REGION}/jobs/[a-z][a-z0-9-]{{0,62}}/executions/[a-z][a-z0-9-]{{0,62}}", name), "invalid execution resource")
        return self.request("GET", "https://run.googleapis.com/v2/" + name)

    def executions(self, job):
        require(job in registered_jobs(), "unregistered execution inventory refused")
        token, seen = "", set()
        while True:
            params = {"pageSize": 1000, "showDeleted": "true"}
            if token:
                params["pageToken"] = token
            value = self.request("GET", "https://run.googleapis.com/v2/" + job_parent(job) + "/executions", params=params)
            require(set(value) <= {"executions", "nextPageToken"}, "unexpected execution enumeration response")
            require(isinstance(value.get("executions", []), list), "incomplete execution enumeration")
            yield from value.get("executions", [])
            token = value.get("nextPageToken", "")
            require(isinstance(token, str) and token not in seen, "invalid execution pagination token")
            if not token:
                return
            seen.add(token)

    def translation_manifest(self, uri):
        """Read only an already named, current canonical translation manifest."""
        from scripts.translation_recovery import Reader
        return Reader(self.session).read(uri)

    def receipt_metadata(self, uri, *, generation=None):
        require(isinstance(uri, str) and uri.startswith(f"gs://{BUCKET}/"), "foreign operational receipt")
        name = uri.removeprefix(f"gs://{BUCKET}/")
        require("/runs/" in name and name.endswith(".translation-notice.json") and ".." not in name.split("/"), "non-operational receipt read refused")
        params = {"fields": "bucket,name,generation,metageneration,updated,size,metadata"}
        if generation is not None:
            require(type(generation) is int and generation > 0, "invalid receipt generation")
            params["generation"] = str(generation)
        result = self.request("GET", f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o/{quote(name, safe='')}", params=params)
        require(result.get("bucket") == BUCKET and result.get("name") == name and (generation is None or str(result.get("generation")) == str(generation)), "receipt metadata does not match requested identity")
        return result

    def run_receipt(self, uri, *, generation):
        """Read only an existing, exact-generation canonical operational record."""
        require(isinstance(uri, str) and type(generation) is int and generation > 0, "run-record generation is required")
        matches = [(slug, root, match[1]) for slug, root in asset_roots().items()
                   if (match := re.fullmatch(re.escape(f"gs://{BUCKET}/{root}/runs/") + r"(\d{4}-\d\d-\d\d)[.]json", uri))]
        require(len(matches) == 1, "non-operational run-record read refused")
        slug, _, run_date = matches[0]
        name = uri.removeprefix(f"gs://{BUCKET}/")
        url = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o/{quote(name, safe='')}"
        metadata = self.request("GET", url, params={"generation": str(generation), "fields": "bucket,name,generation,size,timeCreated,updated"})
        require(metadata.get("bucket") == BUCKET and metadata.get("name") == name and str(metadata.get("generation")) == str(generation), "run-record metadata identity differs")
        size = int(metadata["size"])
        require(0 < size <= 1024 * 1024, "run record exceeds bounded read")
        from google.api_core import exceptions
        response = self.session.request("GET", url, params={"generation": str(generation), "alt": "media"}, timeout=request_timeout(30), stream=True)
        try:
            if response.status_code != 200:
                raise exceptions.from_http_status(response.status_code, "Canonical run-record read failed")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                body.extend(chunk)
                require(len(body) <= size, "run-record bytes exceed metadata size")
            require(len(body) == size, "run-record bytes are incomplete")
        finally:
            response.close()
        record = json.loads(body)
        require(isinstance(record, dict) and record.get("asset_slug") == slug and record.get("run_date") == run_date, "run-record body identity differs")
        return validate_run_receipt({"schema": RUN_RECEIPT_SCHEMA, "uri": uri, "generation": generation, "size": size,
            "sha256": hashlib.sha256(body).hexdigest(), "asset_slug": slug, "run_date": run_date,
            "release_date": record.get("release_date"), "status": record.get("status"),
            "created_at": metadata.get("timeCreated"), "updated_at": metadata.get("updated"),
            "published_paths": [{"path": row.get("path"), "generation": row.get("generation")} for row in record.get("release_paths", [])]})

    def heartbeat(self, now):
        return self.request("POST", f"https://monitoring.googleapis.com/v3/projects/{PROJECT}/timeSeries", payload={"timeSeries": [{"metric": {"type": METRIC}, "resource": {"type": "global", "labels": {"project_id": PROJECT}}, "points": [{"interval": {"endTime": timestamp(now)}, "value": {"int64Value": "1"}}]}]})


def atomic_snapshot(path, value):
    """Local shadow evidence; production checkpoints use the signed ledger."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".pending")
    created = False
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            created = True
            json.dump(value, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if created and temporary.exists():
            temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path, help="Local shadow evidence; never a substitute for the signed notification ledger")
    args = parser.parse_args(argv)
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    api = CloudReader(AuthorizedSession(credentials, max_refresh_attempts=0, refresh_timeout=30))
    previous = json.loads(args.checkpoint.read_text()) if args.checkpoint.exists() else None
    candidate = collect(previous, api, now=datetime.now(timezone.utc))
    atomic_snapshot(args.checkpoint, candidate)
    print(json.dumps({"mode": "shadow-collection-only", "covered_through": candidate["covered_through"], "pending_events": len(candidate["pending"]), "heartbeat_emitted": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
