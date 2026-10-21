#!/usr/bin/env python3
"""Read-only, checkpointed Cloud Logging observations for shadow reconciliation.

The reconciler supplies authenticated persistence and policy evaluation. This
collector cannot start executions, publish data, repair infrastructure, or send
Slack. Only the fixed pipeline heartbeat is a permitted Google write.
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
import sys
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingestion.common.operational_events import SCHEMA as EVENT_SCHEMA, validate as validate_event
from scripts import gcp_read_retry
from scripts.operational_retry import UNATTENDED, operation, request_timeout, run_read

PROJECT = "shared-datasets-1"
REGION = "us-central1"
BUCKET = "skytruth-shared-datasets-1"
SCHEMA = "shared-datasets-cloud-observation-checkpoint-v1"
METRIC = "custom.googleapis.com/shared_datasets/operational_reconciliation_heartbeat"
VIEWS = tuple(f"projects/{PROJECT}/locations/global/buckets/{bucket}/views/shared-datasets-operations" for bucket in ("_Default", "_Required"))
OVERLAP = timedelta(minutes=10)
INGESTION_LAG = timedelta(minutes=2)
HISTORY = timedelta(hours=24)
MAX_EVENTS = 100_000
NAME = re.compile(r"[a-z][a-z0-9-]{0,62}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


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
        require(isinstance(source, str) and re.fullmatch(r"cloud(?:log|exec):[0-9a-f]{64}", source) and isinstance(row, dict) and set(row) == {"timestamp", "sha256"}, "invalid source checkpoint")
        instant(row["timestamp"])
        require(isinstance(row["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]), "invalid source digest")
    require(isinstance(value["pending"], list) and len(value["pending"]) <= MAX_EVENTS, "invalid pending observations")
    require(isinstance(value["watching"], dict) and len(value["watching"]) <= MAX_EVENTS, "invalid execution watch state")
    for source, fact in value["watching"].items():
        require(source in value["seen"] and isinstance(fact, dict) and fact.get("source_id") == source and isinstance(fact.get("execution"), dict) and fact["execution"].get("terminal") is False, "invalid pending execution proof")
    require(isinstance(value["last_cycle"], dict), "invalid coverage evidence")
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


def execution_name(entry, *, required=True):
    value = entry.get("labels", {}).get("run.googleapis.com/execution_name")
    if value is None:
        response = entry.get("protoPayload", {}).get("response", {})
        value = response.get("metadata", {}).get("name") or response.get("name")
        if isinstance(value, str):
            value = value.rsplit("/", 1)[-1]
    if value is None and not required:
        return None
    require(isinstance(value, str) and NAME.fullmatch(value), "Cloud Run event has no exact execution identity")
    return value


def authoritative_execution(api, job, execution):
    name = f"projects/{PROJECT}/locations/{REGION}/jobs/{job}/executions/{execution}"
    result = api.execution(name)
    require(isinstance(result, dict) and result.get("name") == name, "execution metadata belongs to another operation")
    containers = result.get("template", {}).get("containers", [])
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
    completed = [row for row in result.get("conditions", []) if row.get("type") == "Completed"]
    success = terminal and len(completed) == 1 and completed[0].get("state") == "CONDITION_SUCCEEDED"
    return {"name": name, "uid": uid, "revision": revision, "coverage_gaps": gaps, "terminal": terminal, "success": success,
            "completion_time": result.get("completionTime"), "images": images}


def normalize(entry, source, api):
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
        result.update(kind="runtime", fact=payload, execution=authoritative_execution(api, job, execution))
        receipt = payload["details"].get("receipt_uri")
        if receipt:
            result["receipt_metadata"] = api.receipt_metadata(receipt, generation=payload["details"].get("receipt_generation"))
    elif kind == "cloud_run_job":
        audit = entry.get("protoPayload", {})
        require(audit.get("serviceName") == "run.googleapis.com" and audit.get("methodName") in {"/Jobs.RunJob", "google.cloud.run.v2.Jobs.RunJob"}, "unsupported execution observation")
        execution = execution_name(entry, required=False)
        if execution is None:
            code = audit.get("status", {}).get("code")
            require(type(code) is int and code > 0, "execution request has neither execution identity nor a failed status")
            result.update(kind="execution_request", reason="permission_denied" if code in {7, 16} else "unknown", mutation_status="uncertain", status_code=code, coverage_gaps=["execution_request_outcome_unverified"])
        else:
            result.update(kind="execution", execution=authoritative_execution(api, job, execution))
    else:
        require(entry.get("severity") in {"ERROR", "CRITICAL", "ALERT", "EMERGENCY"}, "unrelated healthy Scheduler event")
        result.update(kind="scheduler_dispatch", mutation_status="uncertain", coverage_gaps=["scheduler_dispatch_outcome_unverified"])
    return result


def collect(previous, api, *, now):
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
        found, new, next_watching = {}, [], {}
        for source, fact in watching.items():
            updated = authoritative_execution(api, fact["job"], fact["execution"]["name"].rsplit("/", 1)[1])
            old = fact["execution"]
            require(not old.get("uid") or updated.get("uid") == old["uid"], "immutable execution UID changed")
            require(not old["revision"] or not old["revision"].startswith("sha256:") or updated["revision"] == old["revision"], "immutable execution image changed")
            if updated != fact["execution"]:
                transition = {**deepcopy(fact), "source_id": "cloudexec:" + digest({"source": source, "proof": updated}), "execution": updated, "observed_at": timestamp(now)}
                new.append(transition)
                found[transition["source_id"]] = {"timestamp": timestamp(now), "sha256": digest(transition)}
            if not updated["terminal"]:
                next_watching[source] = {**deepcopy(fact), "execution": updated}
        for view in VIEWS:
            for entry in api.entries(view, start=start, end=end):
                source, observed, _, _ = validate_entry(entry, start, end)
                fingerprint = digest(entry)
                old = found.get(source) or seen.get(source)
                if old is not None:
                    require(old["sha256"] == fingerprint, "source identity was reused with different log bytes")
                    continue
                found[source] = {"timestamp": timestamp(observed), "sha256": fingerprint}
                fact = normalize(entry, source, api)
                new.append(fact)
                if fact.get("execution", {}).get("terminal") is False:
                    next_watching[source] = fact
                require(len(found) <= MAX_EVENTS, "enumeration exceeds bounded snapshot size")
        retained = {source: row for source, row in seen.items() if instant(row["timestamp"]) >= end - HISTORY or source in next_watching}
        retained.update(found)
        require(len(retained) <= MAX_EVENTS and len(pending) + len(new) <= MAX_EVENTS, "operational backlog exceeds bounded state")
        gaps = sorted({gap for fact in [*pending, *new, *next_watching.values()] for gap in [*fact.get("coverage_gaps", []), *fact.get("execution", {}).get("coverage_gaps", [])]})
        return validate_checkpoint({"schema": SCHEMA, "covered_through": timestamp(end), "seen": retained,
                                   "pending": [*deepcopy(pending), *new], "watching": next_watching, "last_cycle": {"start": timestamp(start), "end": timestamp(end), "completed_at": timestamp(now), "new_events": len(new), "views": list(VIEWS), "coverage_gaps": gaps, "overlap_seconds": int(OVERLAP.total_seconds()), "ingestion_lag_seconds": int(INGESTION_LAG.total_seconds())}})

    with operation(profile=UNATTENDED):
        return run_read(snapshot, classify=gcp_read_retry.classify, label="cloud operational observation snapshot")


def evaluated_collection(candidate, evaluation):
    """Produce exact checkpoint/evaluation bytes for the signed ledger proposal."""
    validate_checkpoint(candidate)
    require(isinstance(evaluation, dict) and evaluation.get("complete") is True, "policy evaluation is incomplete")
    resolved = evaluation.get("resolved_source_ids", [])
    require(isinstance(resolved, list) and all(isinstance(source, str) for source in resolved) and len(resolved) == len(set(resolved)) and set(resolved) <= {fact["source_id"] for fact in candidate["pending"]}, "invalid provenance resolution identities")
    unresolved = [fact for fact in candidate["pending"] if fact["source_id"] not in resolved and (fact.get("coverage_gaps") or fact.get("execution", {}).get("coverage_gaps"))]
    coverage = {**candidate["last_cycle"], "coverage_gaps": sorted({gap for fact in unresolved for gap in [*fact.get("coverage_gaps", []), *fact.get("execution", {}).get("coverage_gaps", [])]})}
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
    if checkpoint["pending"] or checkpoint["last_cycle"].get("coverage_gaps") or evaluation.get("provenance_complete", True) is not True:
        return False
    heartbeat(instant(checkpoint["last_cycle"]["completed_at"]))
    return True


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
            payload = {"resourceNames": [view], "filter": f'timestamp>="{timestamp(start)}" AND timestamp<"{timestamp(end)}"', "orderBy": "timestamp asc", "pageSize": 1000}
            if page:
                payload["pageToken"] = page
            value = self.request("POST", "https://logging.googleapis.com/v2/entries:list", payload=payload)
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
