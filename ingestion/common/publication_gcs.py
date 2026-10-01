"""Generation-pinned GCS adapter for the opt-in publication core.

Callers must use an approved publisher identity. No CLI or deployment enables
this adapter automatically. It preserves supplied metadata and writes ownership
tags atomically with bytes, including server-side rewrites.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from google.api_core.exceptions import NotFound, PreconditionFailed

from ingestion.common.publication import (
    Conflict, JsonObject, NotRecoverableSource, ObjectHead, ObjectVersion,
    PublicationError, canonical, digest, require, split_uri, strict_json,
)


def work_root() -> Path:
    root = Path(os.environ.get("SHARED_DATASETS_WORKDIR") or Path(tempfile.gettempdir()) / "shared-datasets-1") / "_scratch"
    root.mkdir(parents=True, exist_ok=True)
    return root


class GcsStore:
    def __init__(self, client: Any):
        self.client = client

    def _blob(self, uri: str, generation: int | None = None):
        bucket, name = split_uri(uri)
        return self.client.bucket(bucket).blob(name, generation=generation)

    @staticmethod
    def _head(uri: str, blob: Any) -> ObjectHead:
        return ObjectHead(uri, int(blob.generation), int(blob.size), str(blob.content_type or ""), str(blob.cache_control or ""), tuple(sorted((blob.metadata or {}).items())))

    @classmethod
    def _version(cls, uri: str, blob: Any, sha256: str) -> ObjectVersion:
        return ObjectVersion(**asdict(cls._head(uri, blob)), sha256=sha256)

    def head(self, uri: str) -> ObjectHead | None:
        blob = self._blob(uri)
        try:
            blob.reload()
        except NotFound:
            return None
        return self._head(uri, blob)

    def list_heads(self, prefix: str):
        bucket, name = split_uri(prefix.rstrip("/"))
        for blob in self.client.list_blobs(bucket, prefix=name + "/"):
            yield self._head(f"gs://{bucket}/{blob.name}", blob)

    def read_json(self, uri: str) -> JsonObject | None:
        blob = self._blob(uri)
        try:
            blob.reload()
        except NotFound:
            return None
        # Downloads mutate Blob metadata with HTTP transport headers. Preserve
        # the stored object metadata from the generation-pinned metadata read.
        head = self._head(uri, blob)
        try:
            data = blob.download_as_bytes(if_generation_match=head.generation)
        except (NotFound, PreconditionFailed) as exc:
            raise Conflict(f"JSON changed during read: {uri}") from exc
        try:
            value = strict_json(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublicationError(f"invalid publication JSON: {uri}") from exc
        require(isinstance(value, dict), "publication JSON must be an object")
        return JsonObject(value, ObjectVersion(**asdict(head), sha256=digest(data)))

    def inspect(self, uri: str, generation: int | None = None) -> ObjectVersion | None:
        blob = self._blob(uri, generation)
        try:
            blob.reload(if_generation_match=generation)
        except NotFound:
            return None
        except PreconditionFailed as exc:
            raise Conflict(f"object generation changed: {uri}") from exc
        head = self._head(uri, blob)
        try:
            with tempfile.TemporaryFile(dir=work_root()) as handle:
                blob.download_to_file(handle, if_generation_match=head.generation)
                handle.seek(0)
                sha256 = hashlib.file_digest(handle, "sha256").hexdigest()
        except (NotFound, PreconditionFailed) as exc:
            raise Conflict(f"object changed while hashing: {uri}") from exc
        return ObjectVersion(**asdict(head), sha256=sha256)

    def write_json(self, uri: str, value: dict[str, Any], expected: int) -> ObjectVersion:
        return self.write_bytes(uri, canonical(value), expected, {}, "application/json", "no-cache")

    def _destination(self, uri: str, tags: Mapping[str, str], content_type: str, cache_control: str):
        blob = self._blob(uri)
        blob.metadata = dict(tags)
        blob.content_type = content_type
        blob.cache_control = cache_control
        return blob

    def write_bytes(self, uri: str, data: bytes, expected: int, tags: Mapping[str, str], content_type: str, cache_control: str) -> ObjectVersion:
        blob = self._destination(uri, tags, content_type, cache_control)
        try:
            blob.upload_from_string(data, content_type=content_type, if_generation_match=expected)
        except PreconditionFailed as exc:
            raise Conflict(f"original destination generation changed: {uri}") from exc
        return self._version(uri, blob, digest(data))

    def upload(self, uri: str, path: Path, expected: int, tags: Mapping[str, str], content_type: str, cache_control: str) -> ObjectVersion:
        with tempfile.TemporaryDirectory(prefix="publication-input-", dir=work_root()) as temporary:
            snapshot = Path(temporary) / path.name
            try:
                shutil.copyfile(path, snapshot)
            except FileNotFoundError as exc:
                raise NotRecoverableSource(f"checkpoint source disappeared: {path}") from exc
            with snapshot.open("rb") as handle:
                sha256 = hashlib.file_digest(handle, "sha256").hexdigest()
            require(sha256 == tags["publication-sha256"], "checkpoint bytes changed since preparation")
            snapshot.chmod(0o400)
            blob = self._destination(uri, tags, content_type, cache_control)
            try:
                blob.upload_from_filename(str(snapshot), content_type=content_type, if_generation_match=expected)
            except PreconditionFailed as exc:
                raise Conflict(f"original destination generation changed: {uri}") from exc
            return self._version(uri, blob, sha256)

    def copy(self, source: ObjectVersion, uri: str, expected: int, tags: Mapping[str, str], content_type: str, cache_control: str) -> ObjectVersion:
        verified = self.inspect(source.path, source.generation)
        if verified is None:
            raise NotRecoverableSource(f"pinned source generation is missing: {source.path}#{source.generation}")
        require(verified.sha256 == source.sha256 and verified.size == source.size, "pinned source content mismatch")
        src = self._blob(source.path, source.generation)
        dst = self._destination(uri, tags, content_type, cache_control)
        token = None
        try:
            while True:
                token, _done, _total = dst.rewrite(src, token=token, if_source_generation_match=source.generation, if_generation_match=expected)
                if token is None:
                    break
        except PreconditionFailed as exc:
            raise Conflict(f"source or original destination generation changed: {uri}") from exc
        except NotFound as exc:
            raise NotRecoverableSource(f"pinned copy source disappeared: {source.path}") from exc
        return self._version(uri, dst, source.sha256)
