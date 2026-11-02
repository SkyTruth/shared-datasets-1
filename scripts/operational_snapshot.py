"""Bounded read transcripts for a settled operational dry run.

The caller owns reconciliation and the settled decision. A snapshot records no
production authority and cannot replace a missing or failed live API response.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from scripts import incident_verification_cache as native
from scripts import install_deployment_verifier as verifier


WORKFLOW = ".github/workflows/operational-safety-check.yml"
JOB = "Check for operational work"
PROBE_STEP = "Check operational work"
UPLOAD_STEP = "Retain operational safety snapshot"
SCHEMA = "shared-datasets-operational-snapshot-v1"
PREFIX = "repos/" + native.REPOSITORY
MAX_REQUESTS = native.MAX_ENTRIES


class SnapshotError(RuntimeError):
    pass


class SnapshotWrite(SnapshotError):
    """A dry run requested a mutation; it must use the normal worker instead."""


def require(value, message):
    if not value:
        raise SnapshotError("OPERATIONAL_SNAPSHOT: " + message)


def artifact_name(run):
    producer = native.execution(run)
    return f"safety-snapshot-{producer['id']}-{producer['run_attempt']}"


def validate_request(method, path, field):
    require(isinstance(method, str) and method in {"get", "pages"} and isinstance(path, str)
            and all(32 < ord(char) < 127 for char in path), "invalid request method/path")
    parsed = urlsplit(path)
    require(not parsed.scheme and not parsed.netloc and not parsed.fragment, "request must use the repository API")
    stem = parsed.path
    suffix = stem.removeprefix(PREFIX)
    require(stem.startswith(PREFIX) and (not suffix or suffix.startswith("/")), "request belongs to another repository")
    identifier = r"[1-9][0-9]*"
    workflow = rf"(?:{identifier}|[A-Za-z0-9_.-]+\.yml)"
    if method == "get":
        require(field is None and not parsed.query and (
            not suffix
            or re.fullmatch(rf"/actions/workflows/{workflow}", suffix)
            or re.fullmatch(rf"/actions/runs/{identifier}(?:/attempts/{identifier})?", suffix)
            or re.fullmatch(rf"/deployments/{identifier}", suffix)
        ), "unexpected reconciliation GET")
        return
    expected_field = None
    allowed = {"per_page"}
    if suffix == "/deployments":
        allowed |= {"environment", "task"}
    elif re.fullmatch(rf"/deployments/{identifier}/statuses", suffix):
        pass
    elif re.fullmatch(rf"/actions/runs/{identifier}/attempts/{identifier}/jobs", suffix):
        expected_field = "jobs"
    elif suffix == "/actions/runs" or re.fullmatch(rf"/actions/workflows/{workflow}/runs", suffix):
        expected_field = "workflow_runs"
        allowed |= {"branch", "created", "head_sha", "status", "event"}
    else:
        raise SnapshotError("OPERATIONAL_SNAPSHOT: unexpected reconciliation enumeration")
    require(field == expected_field, "enumeration field differs from endpoint")
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as error:
        raise SnapshotError("OPERATIONAL_SNAPSHOT: invalid enumeration query") from error
    require(set(query) <= allowed and query.get("per_page") == ["100"]
            and all(len(values) == 1 and values[0] for values in query.values()), "unexpected enumeration query")


class RecordingAPI:
    """Expose only real reads and refuse mutations before reaching transport."""

    def __init__(self, api):
        self.api = api
        self.requests = {}
        self.stable = True
        self.wrote = False

    def _read(self, method, path, field=None):
        validate_request(method, path, field)
        value = self.api.get(path) if method == "get" else self.api.pages(path, field=field)
        key = (method, path, field)
        fingerprint = native.digest(value)
        if key in self.requests and self.requests[key] != fingerprint:
            self.stable = False
        require(key in self.requests or len(self.requests) < MAX_REQUESTS, "too many recorded API requests")
        self.requests[key] = fingerprint
        return copy.deepcopy(value)

    def get(self, path):
        return self._read("get", path)

    def pages(self, path, field=None):
        return self._read("pages", path, field)

    def post(self, *_args, **_kwargs):
        self.wrote = True
        raise SnapshotWrite("OPERATIONAL_SNAPSHOT: dry run requested a mutation")


def validate_context(policy, identity):
    require(isinstance(policy, dict) and set(policy) == {"head_sha", "gh_version", "since"}
            and isinstance(policy["head_sha"], str) and re.fullmatch(r"[0-9a-f]{40}", policy["head_sha"])
            and isinstance(policy["gh_version"], str) and policy["gh_version"].startswith(f"gh version {verifier.VERSION} "),
            "invalid snapshot policy")
    native.timestamp(policy["since"])
    require(isinstance(identity, dict) and set(identity) == {"team", "user", "channel"}
            and all(isinstance(identity[key], str) and re.fullmatch(pattern, identity[key])
                    for key, pattern in (("team", r"T[A-Z0-9]+"), ("user", r"U[A-Z0-9]+"), ("channel", r"[CG][A-Z0-9]+"))),
            "invalid snapshot Slack identity")


class Snapshot:
    def __init__(self, data):
        require(isinstance(data, dict) and set(data) == {"schema", "producer", "policy", "identity", "inventory_sha256", "requests"}
                and data["schema"] == SCHEMA, "invalid snapshot document")
        require(isinstance(data["producer"], dict) and native.execution(data["producer"]) == data["producer"], "invalid snapshot producer")
        validate_context(data["policy"], data["identity"])
        require(data["producer"]["head_sha"] == data["policy"]["head_sha"], "snapshot policy differs from producer revision")
        require(isinstance(data["inventory_sha256"], str) and native.SHA.fullmatch(data["inventory_sha256"]), "invalid inventory fingerprint")
        requests = data["requests"]
        require(isinstance(requests, list) and 0 < len(requests) <= MAX_REQUESTS, "invalid request transcript")
        seen = set()
        for request in requests:
            require(isinstance(request, dict) and set(request) == {"method", "path", "field", "sha256"}
                    and isinstance(request["sha256"], str) and native.SHA.fullmatch(request["sha256"]), "invalid transcript entry")
            validate_request(request["method"], request["path"], request["field"])
            key = (request["method"], request["path"], request["field"])
            require(key not in seen, "duplicate request transcript entry")
            seen.add(key)
        require(len(native.canonical(data)) <= native.MAX_BYTES, "snapshot exceeds size limit")
        self.data = copy.deepcopy(data)

    @classmethod
    def capture(cls, run, policy, identity, inventory, recording):
        require(isinstance(recording, RecordingAPI), "capture requires a real read recorder")
        if not recording.stable or recording.wrote:
            return None
        return cls({"schema": SCHEMA, "producer": native.execution(run), "policy": policy, "identity": identity,
                    "inventory_sha256": native.digest(inventory),
                    "requests": [{"method": method, "path": path, "field": field, "sha256": value}
                                 for (method, path, field), value in recording.requests.items()]})

    def matches(self, api):
        unchanged = True
        for request in self.data["requests"]:
            value = (api.get(request["path"]) if request["method"] == "get"
                     else api.pages(request["path"], field=request["field"]))
            unchanged &= native.digest(value) == request["sha256"]
        return unchanged

    def save(self, directory, run):
        data = {**self.data, "producer": native.execution(run)}
        validated = Snapshot(data)
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / native.MEMBER
        path.write_bytes(native.canonical(validated.data))
        return path

    @classmethod
    def restore(cls, api, run, policy, identity, inventory):
        validate_context(policy, identity)
        require(native.execution(run)["head_sha"] == policy["head_sha"], "current policy differs from execution")
        repository = api.get(PREFIX)
        workflow = api.get(f"{PREFIX}/actions/workflows/{WORKFLOW.rsplit('/', 1)[-1]}")
        require(native.positive(repository.get("id")) and repository.get("full_name") == native.REPOSITORY
                and native.positive(workflow.get("id")) and workflow.get("path") == WORKFLOW, "snapshot repository/workflow mismatch")
        since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        query = urlencode({"branch": "main", "status": "success", "head_sha": run["head_sha"], "created": ">=" + since, "per_page": 100})
        runs = api.pages(f"{PREFIX}/actions/workflows/{workflow['id']}/runs?{query}", field="workflow_runs")
        for producer in sorted((item for item in runs if item.get("id") != run["id"]), key=lambda item: item["id"], reverse=True):
            name = artifact_name(producer)
            artifacts = api.pages(f"{PREFIX}/actions/runs/{producer['id']}/artifacts?per_page=100", field="artifacts")
            selected = [item for item in artifacts if item.get("name") == name]
            if not selected:
                continue
            require(len(selected) == 1, "ambiguous operational snapshot artifact")
            artifact = selected[0]
            if artifact.get("expired") is True or native.timestamp(artifact.get("expires_at")) <= datetime.now(timezone.utc):
                continue
            producer = native.authenticate_producer(api, repository, workflow, producer, run["head_sha"], workflow_path=WORKFLOW,
                job_name=JOB, required_steps=(PROBE_STEP, UPLOAD_STEP), events=("schedule", "workflow_dispatch"))
            snapshot = cls(native.artifact_payload(api, producer, artifact))
            require(snapshot.data["producer"] == native.execution(producer), "snapshot artifact producer mismatch")
            if (snapshot.data["policy"] != policy or snapshot.data["identity"] != identity
                    or snapshot.data["inventory_sha256"] != native.digest(inventory)):
                return None
            return snapshot
        return None
