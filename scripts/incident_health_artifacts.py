"""Read bounded native Actions health artifacts; ledger signatures stay fresh."""
from __future__ import annotations

from datetime import datetime
import hashlib
import io
import json
import re
import zipfile

MAX_BYTES = 8 * 1024 * 1024
MEMBER = "cache.json"
REPOSITORY = "SkyTruth/shared-datasets-1"


class ArtifactError(RuntimeError):
    pass


def require(value, message):
    if not value:
        raise ArtifactError("OPERATIONAL_ARTIFACT: " + message)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def timestamp(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value), "invalid producer timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ArtifactError("OPERATIONAL_ARTIFACT: invalid producer timestamp") from error


def artifact_payload(api, producer, artifact):
    require(type(artifact.get("id")) is int and artifact["id"] > 0 and artifact.get("expired") is False
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
        raise ArtifactError("OPERATIONAL_ARTIFACT: unreadable verification artifact") from error
    require(payload == normalized, "verification artifact is not canonical JSON")
    return data
