"""One bounded operational object, read by generation and replaced by CAS."""
from __future__ import annotations

from dataclasses import dataclass

from google.api_core.exceptions import NotFound, PreconditionFailed

from services.monitoring_slack.model import MAX_BYTES, canonical, parse_json, require

STATE_OBJECT = "monitoring-slack/state-v1.json"
CAPABILITY_OBJECT = "monitoring-slack/capabilities-v1.json"


class Conflict(RuntimeError):
    """Another writer changed the exact generation; read before deciding again."""


@dataclass(frozen=True)
class Snapshot:
    value: dict | None
    generation: int


class Store:
    def __init__(self, client, bucket):
        self.bucket = client.bucket(bucket)

    def load(self, name=STATE_OBJECT):
        require(name in {STATE_OBJECT, CAPABILITY_OBJECT}, "unsupported operational object")
        blob = self.bucket.blob(name)
        try:
            blob.reload(timeout=15, retry=None)
        except NotFound:
            return Snapshot(None, 0)
        generation = int(blob.generation)
        require(generation > 0 and type(blob.size) is int and 0 <= blob.size <= MAX_BYTES, "invalid operational object metadata")
        try:
            body = blob.download_as_bytes(if_generation_match=generation, timeout=15, retry=None)
        except PreconditionFailed as error:
            raise Conflict("operational object changed during pinned read") from error
        require(len(body) == blob.size, "operational object size mismatch")
        value = parse_json(body)
        require(isinstance(value, dict), "operational snapshot must be an object")
        return Snapshot(value, generation)

    def save(self, value, generation, name=STATE_OBJECT):
        require(name in {STATE_OBJECT, CAPABILITY_OBJECT} and type(generation) is int and generation >= 0,
                "invalid generation-CAS write")
        body = canonical(value)
        require(len(body) <= MAX_BYTES, "operational object exceeds its bound")
        blob = self.bucket.blob(name)
        try:
            blob.upload_from_string(body, content_type="application/json", if_generation_match=generation,
                                    timeout=15, retry=None)
        except PreconditionFailed as error:
            raise Conflict("operational object generation changed") from error
        require(int(blob.generation) > 0, "write returned no operational generation")
        return Snapshot(parse_json(body), int(blob.generation))
