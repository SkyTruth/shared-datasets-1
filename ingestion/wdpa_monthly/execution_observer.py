"""Independent execution observations. This identity has no publication rights."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re

import google.auth
from google.auth.transport.requests import AuthorizedSession
from google.api_core.exceptions import NotFound, PreconditionFailed, from_http_status, GoogleAPICallError
from google.cloud import storage

from ingestion.common.runtime import configure_logging
from ingestion.common.operational_events import emit
from scripts import gcp_read_retry
from scripts.operational_retry import OBSERVER_READ, operation, request_timeout, run_read

STATUS_OBJECT = "_catalog/wdpa-monthly-execution.json"
TERMINAL = {"succeeded", "failed", "cancelled"}
# Messages can contain source paths, credentials or user input. Publish only
# recognized API enum values, and a generic code for everything else.
REASONS = {
    "JOB_STATUS_SERVICE_POLLING_ERROR",
    "NON_ZERO_EXIT_CODE",
    "CANCELLED",
    "JOB_STATUS_USER_CANCELLED",
    "JOB_STATUS_TASK_EXECUTION_FAILED",
    "JOB_STATUS_TASKS_SCHEDULING_FAILED",
    "CONTAINER_MISSING",
    "CONTAINER_PERMISSION_DENIED",
    "CONTAINER_IMAGE_UNAUTHORIZED",
    "PROGRESS_DEADLINE_EXCEEDED",
    "EXECUTION_FAILED",
    "EXECUTION_CANCELLED",
    "EXECUTION_SUCCEEDED",
    "WAITING_FOR_OPERATION",
    "CANCELLING",
}


def timestamp(value):
    return (
        dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value
        else dt.datetime.min.replace(tzinfo=dt.UTC)
    )


def execution_entry(raw):
    identifier = str(raw.get("name", "")).rsplit("/", 1)[-1]
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", identifier):
        raise ValueError("Cloud Run execution has an invalid ID")
    completed = next(
        (c for c in raw.get("conditions", []) if c.get("type") == "Completed"), {}
    )
    reason = completed.get("executionReason") or completed.get("reason") or ""
    if raw.get("cancelledCount", 0) or reason in {
        "CANCELLED",
        "JOB_STATUS_USER_CANCELLED",
        "EXECUTION_CANCELLED",
    }:
        state = "cancelled"
    elif completed.get("state") == "CONDITION_SUCCEEDED":
        state = "succeeded"
    elif completed.get("state") == "CONDITION_FAILED" or raw.get("failedCount", 0):
        state = "failed"
    elif raw.get("completionTime"):
        state = "unknown"
    elif raw.get("startTime") or raw.get("runningCount", 0):
        state = "running"
    elif raw.get("createTime"):
        state = "pending"
    else:
        state = "unknown"
    return {
        "id": identifier,
        "created_at": raw.get("createTime"),
        "started_at": raw.get("startTime"),
        "completed_at": raw.get("completionTime"),
        "state": state,
        "reason_code": reason
        if reason in REASONS
        else ("UNSPECIFIED" if not reason else "OTHER"),
    }


def observe(executions, *, job_name, observed_at):
    latest = latest_completed = None
    for raw in executions:
        entry = execution_entry(raw)
        if latest is None or (timestamp(entry["created_at"]), entry["id"]) > (
            timestamp(latest["created_at"]),
            latest["id"],
        ):
            latest = entry
        if entry["completed_at"]:
            if latest_completed is None or (
                timestamp(entry["completed_at"]),
                entry["id"],
            ) > (timestamp(latest_completed["completed_at"]), latest_completed["id"]):
                latest_completed = entry
    return {
        "schema_version": 1,
        "job_name": job_name,
        "observed_at": observed_at,
        "latest_execution": latest,
        "latest_completed_execution": latest_completed,
    }


def list_executions(session, job_name):
    token, seen = None, set()
    while True:
        params = {"pageSize": 100}
        if token:
            params["pageToken"] = token
        timeout = request_timeout(15)
        response = session.get(
            f"https://run.googleapis.com/v2/{job_name}/executions",
            params=params,
            timeout=timeout,
            max_allowed_time=timeout,
        )
        if response.status_code != 200:
            raise from_http_status(response.status_code, "WDPA execution observation read failed")
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("executions", []), list):
            raise ValueError("WDPA observation enumeration is incomplete")
        executions = payload.get("executions", [])
        if any(not isinstance(row, dict) or not isinstance(row.get("name"), str)
               or not re.fullmatch(re.escape(job_name) + r"/executions/[a-z][a-z0-9-]{0,62}", row["name"]) for row in executions):
            raise ValueError("WDPA observation enumerated a foreign execution")
        yield from executions
        token = payload.get("nextPageToken")
        if token is not None and (not isinstance(token, str) or token in seen):
            raise ValueError("WDPA observation pagination is incomplete or repeated")
        if not token:
            return
        seen.add(token)


def read_snapshot(job_name):
    """Discard partial pages/credentials before each safe bounded retry."""
    def snapshot():
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        with AuthorizedSession(credentials, max_refresh_attempts=0, refresh_timeout=15) as session:
            executions = list(list_executions(session, job_name))
            request_timeout(15)  # Exhausted work cannot return a writable snapshot.
            return credentials, executions
    with operation(profile=OBSERVER_READ):
        return run_read(snapshot, classify=gcp_read_retry.classify, label="WDPA observer execution snapshot")


def read_failure_reason(error):
    if gcp_read_retry.classify(error) is not None:
        return "transient_transport"
    if isinstance(error, GoogleAPICallError) and error.code in {401, 403}:
        return "permission_denied"
    if isinstance(error, KeyError):
        return "missing_configuration"
    return "unknown"


def write_observation(bucket, observation):
    # A competing observer causes a fresh observation on the next scheduled
    # run. Never retry old bytes against a newer generation.
    blob = bucket.blob(STATUS_OBJECT)
    try:
        blob.reload()
        generation = int(blob.generation)
        previous = json.loads(blob.download_as_text(if_generation_match=generation))
        if timestamp(previous["observed_at"]) >= timestamp(observation["observed_at"]):
            return False
        if (
            previous.get("schema_version") != 1
            or previous.get("job_name") != observation["job_name"]
        ):
            raise ValueError("unexpected execution-status document contract")
        # Retained API history can shrink or briefly lag a prior read. Neither
        # event should conceal a failure or rewind one execution to running.
        old_completed = previous.get("latest_completed_execution")
        new_completed = observation.get("latest_completed_execution")
        if old_completed and (
            not new_completed
            or timestamp(old_completed["completed_at"])
            > timestamp(new_completed["completed_at"])
        ):
            observation["latest_completed_execution"] = old_completed
        old_latest = previous.get("latest_execution")
        new_latest = observation.get("latest_execution")
        if old_latest and (
            not new_latest
            or timestamp(old_latest["created_at"]) > timestamp(new_latest["created_at"])
            or (
                old_latest["id"] == new_latest["id"]
                and old_latest["state"] in TERMINAL
                and new_latest["state"] not in TERMINAL
            )
        ):
            observation["latest_execution"] = old_latest
    except PreconditionFailed:
        return False
    except NotFound:
        generation = 0
    blob.cache_control = "public, max-age=0, must-revalidate"
    try:
        blob.upload_from_string(
            json.dumps(observation, sort_keys=True) + "\n",
            content_type="application/json",
            if_generation_match=generation,
        )
    except PreconditionFailed:
        logging.getLogger(__name__).info(
            "A newer observer won the status-document generation race"
        )
        return False
    return True


def run():
    configure_logging()
    try:
        project = os.environ["GOOGLE_CLOUD_PROJECT"]
        region = os.environ["WDPA_JOB_REGION"]
        bucket_name = os.environ["SHARED_DATASETS_BUCKET"]
        job_name = f"projects/{project}/locations/{region}/jobs/wdpa-monthly"
        # The original timestamp prevents a retried older snapshot overwriting
        # an overlapping observation that finished more recently.
        observed_at = dt.datetime.now(dt.UTC).isoformat()
        credentials, executions = read_snapshot(job_name)
        observation = observe(executions, job_name=job_name, observed_at=observed_at)
    except Exception as error:
        emit("job_outcome", "failed", details={"failure_reason": read_failure_reason(error),
             "mutation_status": "not_started", "published_data_state": "unknown"})
        raise
    bucket = storage.Client(project=project, credentials=credentials).bucket(
        bucket_name
    )
    write_observation(bucket, observation)
    emit("job_outcome", "success", details={"mutation_status": "completed", "published_data_state": "unknown"})
    logging.getLogger(__name__).info(
        "WDPA execution observation: %s", json.dumps(observation, sort_keys=True)
    )


if __name__ == "__main__":
    run()
