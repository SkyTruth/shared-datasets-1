"""Versioned runtime facts for shadow collection; no delivery or recovery authority.

Cloud Logging supplies the authenticated source identity. Runtime context and
any human/commit claims in a log are evidence only, never authorization.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import re
import sys

SCHEMA = "shared-datasets-runtime-operation-v1"
KINDS = {
    "job_outcome": {"success", "skipped", "blocked", "failed"},
    "identity_decision": {"required"},
    "translation_maintenance": {"required", "already_claimed", "delivered", "delivery_unknown", "failed"},
}
CONTEXT = {"job": "CLOUD_RUN_JOB", "execution": "CLOUD_RUN_EXECUTION", "task_index": "CLOUD_RUN_TASK_INDEX", "task_attempt": "CLOUD_RUN_TASK_ATTEMPT"}


def require(condition, message):
    if not condition:
        raise ValueError("OPERATIONAL_EVENT: " + message)


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stable_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def validate(event):
    require(isinstance(event, dict) and set(event) == {"schema", "severity", "kind", "status", "observed_at", "context", "subject", "details"}, "invalid schema fields")
    require(event["schema"] == SCHEMA and event["kind"] in KINDS and event["status"] in KINDS[event["kind"]], "unsupported event kind/outcome")
    require(event["severity"] in {"INFO", "WARNING", "ERROR"}, "invalid severity")
    require(isinstance(event["observed_at"], str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", event["observed_at"]), "invalid UTC timestamp")
    datetime.fromisoformat(event["observed_at"].replace("Z", "+00:00"))
    require(isinstance(event["context"], dict) and set(event["context"]) == set(CONTEXT), "invalid runtime context")
    require(all(value is None or isinstance(value, str) and 0 < len(value) <= 200 and not any(ord(char) < 32 for char in value) for value in event["context"].values()), "invalid runtime context values")
    require(isinstance(event["subject"], dict) and set(event["subject"]) <= {"asset_slug", "release", "manifest_uri"}, "invalid subject")
    require(all(isinstance(value, str) and 0 < len(value) <= 2048 and not any(ord(char) < 32 for char in value) for value in event["subject"].values()), "invalid subject values")
    require(isinstance(event["details"], dict), "invalid detail object")
    # Producers emit counts, hashes and receipt references, never source text,
    # secret values, guessed ownership, or guessed repository revisions.
    require(set(event["details"]) <= {"ambiguity_count", "case_set_sha256", "locale_counts", "receipt_uri", "receipt_generation", "manifest_generation", "record_count", "published_data_state", "mutation_status"}, "unsupported detail field")
    detail = event["details"]
    for field in ("ambiguity_count", "record_count", "receipt_generation", "manifest_generation"):
        if field in detail:
            require(type(detail[field]) is int and detail[field] >= (0 if field == "record_count" else 1), "invalid count or generation")
    if "case_set_sha256" in detail:
        require(isinstance(detail["case_set_sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", detail["case_set_sha256"]), "invalid identity case digest")
    if "published_data_state" in detail:
        require(detail["published_data_state"] in {"published", "unchanged", "unknown"}, "invalid published data state")
    if "mutation_status" in detail:
        require(detail["mutation_status"] in {"not_required", "not_started", "in_progress", "completed", "uncertain"}, "invalid mutation state")
    if "receipt_uri" in detail:
        require(isinstance(detail["receipt_uri"], str) and re.fullmatch(r"gs://[a-z0-9_.-]+/[^\r\n]+/runs/[^/]+[.]translation-notice[.]json", detail["receipt_uri"]), "invalid operational receipt URI")
    if "locale_counts" in detail:
        counts = detail["locale_counts"]
        require(isinstance(counts, dict) and bool(counts) and all(isinstance(locale, str) and re.fullmatch(r"[A-Za-z0-9-]{1,32}", locale) and isinstance(value, dict) and all(type(count) is int and count >= 0 for count in value.values()) for locale, value in counts.items()), "invalid translation counts")
    require(len(json.dumps(event, sort_keys=True).encode()) <= 32 * 1024, "event exceeds bounded log payload")
    return event


def emit(kind, status, *, subject=None, details=None, environment=None, stream=None, now=utc_now):
    environment = os.environ if environment is None else environment
    severity = "ERROR" if status == "failed" else "WARNING" if status in {"required", "delivery_unknown", "blocked"} else "INFO"
    event = validate({"schema": SCHEMA, "severity": severity, "kind": kind, "status": status, "observed_at": now(),
                      "context": {key: environment.get(variable) for key, variable in CONTEXT.items()},
                      "subject": subject or {}, "details": details or {}})
    # Keep the existing stdout run-record contract intact. Cloud Run parses a
    # complete JSON line on stderr into jsonPayload, including severity.
    print(json.dumps(event, sort_keys=True, separators=(",", ":")), file=sys.stderr if stream is None else stream, flush=True)
    return event
