"""Reuse exact notification verification, authenticated by native Actions evidence.

This cache cannot authorize production receipts. Every hit rereads the complete
mutable API inputs used by the prior successful notification signature check.
Source replay, checkpoint parsing and delivery remain the caller's responsibility.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import zipfile
from urllib.parse import urlencode

from scripts import install_deployment_verifier as verifier


REPOSITORY = "SkyTruth/shared-datasets-1"
WORKFLOW = ".github/workflows/unattended-workflow-alert.yml"
JOB = "Reconcile Slack incident status"
PREPARE_STEP = "Prepare incident changes and claim posts"
UPLOAD_STEP = "Retain verified notification evidence"
MEMBER = "cache.json"
SCHEMA = "shared-datasets-notification-verification-v1"
MAX_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 2048
SHA = re.compile(r"[0-9a-f]{64}")


class CacheError(RuntimeError):
    pass


def require(value, message):
    if not value:
        raise CacheError("NOTIFICATION_CACHE: " + message)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def positive(value):
    return type(value) is int and value > 0


def artifact_name(run):
    require(positive(run.get("id")) and positive(run.get("run_attempt")), "invalid producer attempt")
    return f"notification-verification-{run['id']}-{run['run_attempt']}"


def execution(run):
    artifact_name(run)
    require(re.fullmatch(r"[0-9a-f]{40}", str(run.get("head_sha", ""))), "producer lacks full revision")
    return {key: run[key] for key in ("id", "run_attempt", "head_sha")}


def timestamp(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value), "invalid producer timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CacheError("NOTIFICATION_CACHE: invalid producer timestamp") from error


def allowed_get(path):
    prefix = f"repos/{REPOSITORY}"
    return (path == prefix or re.fullmatch(re.escape(prefix) + r"/actions/runs/[1-9][0-9]*/attempts/[1-9][0-9]*", path)
            or re.fullmatch(re.escape(prefix) + r"/actions/workflows/[A-Za-z0-9_.-]+\.yml", path))


def validate_reads(reads):
    require(isinstance(reads, dict) and len(reads) == 3
            and all(isinstance(path, str) and allowed_get(path) and isinstance(value, str) and SHA.fullmatch(value)
                    for path, value in reads.items())
            and f"repos/{REPOSITORY}" in reads
            and sum("/actions/runs/" in path for path in reads) == 1
            and sum("/actions/workflows/" in path for path in reads) == 1, "invalid verification read transcript")


class RecordingAPI:
    """The signature verifier receives GET only; writes cannot be recorded."""

    def __init__(self, api):
        self.api, self.reads, self.stable = api, {}, True

    def get(self, path):
        require(isinstance(path, str) and allowed_get(path), "unexpected verification API read")
        value = self.api.get(path)
        fingerprint = digest(value)
        if path in self.reads and self.reads[path] != fingerprint:
            self.stable = False
        self.reads[path] = fingerprint
        return copy.deepcopy(value)


class VerificationCache:
    def __init__(self, run, identity, *, gh_version=None, entries=None):
        self.run, self.identity = execution(run), copy.deepcopy(identity)
        gh_version = gh_version if gh_version is not None else subprocess.check_output(["gh", "--version"], text=True)
        require(isinstance(gh_version, str) and gh_version.startswith(f"gh version {verifier.VERSION} "), "unpinned receipt verifier")
        self.policy = {"head_sha": run["head_sha"], "gh_version": gh_version}
        self.entries = copy.deepcopy({} if entries is None else entries)
        require(isinstance(self.entries, dict) and len(self.entries) <= MAX_ENTRIES, "invalid verification entries")
        for key, reads in self.entries.items():
            require(isinstance(key, str) and SHA.fullmatch(key), "invalid checkpoint fingerprint")
            validate_reads(reads)
        self.used = {}
        self.hits = self.misses = 0

    def verify(self, api, record, statuses, identity, verify_callable):
        """Run the original verifier unless every previously verified byte matches."""
        require(record.get("task") == "slack-incident" and record.get("environment") == "slack-incidents"
                and record.get("production_environment") is False, "only notification checkpoints may reuse verification")
        require(identity == self.identity, "Slack identity changed within cache invocation")
        require(isinstance(statuses, list) and len(statuses) == 1, "notification checkpoint requires its complete status history")
        key = digest({"record": record, "statuses": statuses, "identity": identity})
        reads = self.used.get(key, self.entries.get(key))
        if reads is not None and all(digest(api.get(path)) == value for path, value in reads.items()):
            self.used[key] = copy.deepcopy(reads)
            self.hits += 1
            return True
        self.misses += 1
        captured = RecordingAPI(api)
        verify_callable(captured)
        validate_reads(captured.reads)
        if captured.stable:
            require(len(self.used) < MAX_ENTRIES or key in self.used, "too many notification checkpoints")
            self.used[key] = captured.reads
        return False

    def save(self, directory):
        """Export only entries successfully checked during this invocation."""
        data = canonical({"schema": SCHEMA, "producer": self.run, "policy": self.policy,
                          "identity": self.identity, "entries": self.used})
        require(len(data) <= MAX_BYTES, "verification cache exceeds size limit")
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / MEMBER
        path.write_bytes(data)
        print(f"NOTIFICATION_CACHE: reused={self.hits} verified={self.misses} entries={len(self.used)}")
        return path

    @classmethod
    def restore(cls, api, run, identity, *, gh_version=None):
        current = cls(run, identity, gh_version=gh_version)
        repository = api.get(f"repos/{REPOSITORY}")
        workflow = api.get(f"repos/{REPOSITORY}/actions/workflows/{WORKFLOW.rsplit('/', 1)[-1]}")
        require(positive(repository.get("id")) and repository.get("full_name") == REPOSITORY
                and positive(workflow.get("id")) and workflow.get("path") == WORKFLOW, "cache repository/workflow mismatch")
        since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        query = urlencode({"branch": "main", "status": "success", "head_sha": run["head_sha"], "created": ">=" + since, "per_page": 100})
        runs = api.pages(f"repos/{REPOSITORY}/actions/workflows/{workflow['id']}/runs?{query}", field="workflow_runs")
        candidates = [item for item in runs if item.get("id") != run["id"] and item.get("head_sha") == run["head_sha"]]
        for producer in sorted(candidates, key=lambda item: item["id"], reverse=True):
            execution(producer)
            artifacts = api.pages(f"repos/{REPOSITORY}/actions/runs/{producer['id']}/artifacts?per_page=100", field="artifacts")
            selected = [item for item in artifacts if item.get("name") == artifact_name(producer)]
            if not selected:
                continue
            require(len(selected) == 1, "ambiguous notification verification artifact")
            artifact = selected[0]
            if artifact.get("expired") is True or timestamp(artifact.get("expires_at")) <= datetime.now(timezone.utc):
                continue
            producer = authenticate_producer(api, repository, workflow, producer, run["head_sha"])
            data = artifact_payload(api, producer, artifact)
            require(isinstance(data, dict) and set(data) == {"schema", "producer", "policy", "identity", "entries"}
                    and data["schema"] == SCHEMA and data["producer"] == execution(producer), "verification artifact producer mismatch")
            policy, saved_identity = data["policy"], data["identity"]
            require(isinstance(policy, dict) and set(policy) == {"head_sha", "gh_version"}
                    and isinstance(policy["head_sha"], str) and re.fullmatch(r"[0-9a-f]{40}", policy["head_sha"])
                    and isinstance(policy["gh_version"], str) and policy["gh_version"].startswith(f"gh version {verifier.VERSION} "),
                    "invalid saved verification policy")
            require(isinstance(saved_identity, dict) and set(saved_identity) == {"team", "user", "channel"}
                    and all(isinstance(saved_identity[key], str) and re.fullmatch(pattern, saved_identity[key])
                            for key, pattern in (("team", r"T[A-Z0-9]+"), ("user", r"U[A-Z0-9]+"), ("channel", r"[CG][A-Z0-9]+"))),
                    "invalid saved Slack identity")
            if data["policy"] != current.policy or data["identity"] != identity:
                return current
            return cls(run, identity, gh_version=current.policy["gh_version"], entries=data["entries"])
        return current


def authenticate_producer(api, repository, workflow, producer, head_sha, *, workflow_path=WORKFLOW,
                          job_name=JOB, required_steps=(PREPARE_STEP, UPLOAD_STEP), events=("workflow_run", "workflow_dispatch")):
    expected = execution(producer)
    latest = api.get(f"repos/{REPOSITORY}/actions/runs/{expected['id']}")
    exact = api.get(f"repos/{REPOSITORY}/actions/runs/{expected['id']}/attempts/{expected['run_attempt']}")
    for value in (latest, exact):
        require(execution(value) == expected and value.get("head_sha") == head_sha
                and value.get("workflow_id") == workflow["id"] and value.get("path") == workflow_path
                and value.get("head_branch") == "main" and value.get("event") in events
                and value.get("status") == "completed" and value.get("conclusion") == "success"
                and all(value.get(key, {}).get("id") == repository["id"] and value.get(key, {}).get("full_name") == REPOSITORY
                        for key in ("repository", "head_repository")), "untrusted verification artifact producer")
    jobs = api.pages(f"repos/{REPOSITORY}/actions/runs/{expected['id']}/attempts/{expected['run_attempt']}/jobs?per_page=100", field="jobs")
    selected = [job for job in jobs if job.get("name") == job_name]
    require(len(selected) == 1, "missing or ambiguous cache producer job")
    job = selected[0]
    require(positive(job.get("id")) and job.get("run_id") == expected["id"]
            and job.get("run_attempt") == expected["run_attempt"] and job.get("head_sha") == head_sha
            and job.get("status") == "completed" and job.get("conclusion") == "success", "cache producer job did not succeed")
    for name in required_steps:
        steps = [step for step in job.get("steps", []) if step.get("name") == name]
        require(len(steps) == 1 and steps[0].get("status") == "completed" and steps[0].get("conclusion") == "success",
                "cache producer did not complete evidence export")
    return {**exact, "cache_job_started_at": job["started_at"], "cache_job_completed_at": job["completed_at"]}


def artifact_payload(api, producer, artifact):
    require(positive(artifact.get("id")) and artifact.get("expired") is False
            and artifact.get("workflow_run", {}).get("id") == producer["id"]
            and artifact.get("workflow_run", {}).get("head_sha") == producer["head_sha"]
            and artifact.get("workflow_run", {}).get("head_branch") == "main"
            and type(artifact.get("size_in_bytes")) is int and 0 < artifact["size_in_bytes"] <= MAX_BYTES
            and timestamp(producer["cache_job_started_at"]) <= timestamp(artifact.get("created_at")) <= timestamp(producer["cache_job_completed_at"]),
            "invalid verification artifact identity, size or attempt interval")
    raw = api.archive(REPOSITORY, artifact["id"])
    require(isinstance(raw, bytes) and len(raw) <= MAX_BYTES
            and artifact.get("digest") == "sha256:" + hashlib.sha256(raw).hexdigest(), "verification artifact digest mismatch")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            require(archive.namelist() == [MEMBER] and archive.getinfo(MEMBER).file_size <= MAX_BYTES,
                    "verification artifact must contain only bounded cache.json")
            payload = archive.read(MEMBER)
        data = json.loads(payload)
        normalized = canonical(data)
    except (ValueError, zipfile.BadZipFile, RuntimeError) as error:
        raise CacheError("NOTIFICATION_CACHE: unreadable verification artifact") from error
    require(payload == normalized, "verification artifact is not canonical JSON")
    return data
