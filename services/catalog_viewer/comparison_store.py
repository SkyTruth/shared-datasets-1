"""Private, temporary comparison state shared by all viewer instances.

Only a job's worker writes its state. Result files are immutable; the complete
state is published after every file has uploaded. Access leases and cancellation
are separate objects, so clients cannot overwrite worker progress or results.
"""

from __future__ import annotations

import hashlib
import json
import time

from google.api_core.exceptions import NotFound, PreconditionFailed

from scripts import compare_releases as engine

MAX_STATE_BYTES = (
    12 * 1024 * 1024
)  # API summary budget plus private result descriptors.


class GcsComparisonStore:
    def __init__(self, bucket_name, *, client=None):
        from google.cloud import storage

        self.bucket = (client or storage.Client()).bucket(bucket_name)

    def blob(self, job_id, name):
        return self.bucket.blob(f"jobs/{job_id}/{name}")

    def read_json(self, job_id, name):
        blob = self.blob(job_id, name)
        try:
            # One bounded request returns bytes and their generation together.
            # A reload followed by a pinned read races the worker's next update.
            data = blob.download_as_bytes(start=0, end=MAX_STATE_BYTES, timeout=15)
        except NotFound:
            return None, 0
        if len(data) > MAX_STATE_BYTES:
            raise engine.ComparisonError("Comparison cache state exceeds its budget")
        return json.loads(data), int(blob.generation)

    def write_json(self, job_id, name, value, generation):
        data = json.dumps(value)
        if len(data.encode()) > MAX_STATE_BYTES:
            raise engine.ComparisonLimit("Comparison cache state exceeds its budget")
        blob = self.blob(job_id, name)
        blob.cache_control = "no-store"
        blob.upload_from_string(
            data,
            content_type="application/json",
            if_generation_match=generation,
            timeout=15,
        )
        return int(blob.generation)

    def touch(self, job_id, now, generation):
        try:
            self.write_json(job_id, "access.json", {"at": now}, generation)
        except PreconditionFailed:
            # Another authenticated reader renewed this same lease first.
            pass

    def cancel(self, job_id):
        try:
            self.write_json(job_id, "cancel.json", {}, 0)
        except PreconditionFailed:
            # Cancellation is an idempotent, immutable marker.
            pass

    def cancelled(self, job_id):
        return self.blob(job_id, "cancel.json").exists(timeout=15)

    def publish_files(self, job_id, comparison):
        files = {}
        paths = {
            "comparison.sqlite": comparison.db_path,
            **{
                f"{side}-metadata": path
                for side, (path, _) in comparison.metadata_sources.items()
            },
        }
        for name, path in paths.items():
            comparison.check()
            blob = self.blob(job_id, name)
            blob.cache_control = "no-store"
            blob.upload_from_filename(str(path), if_generation_match=0, timeout=60)
            with path.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            files[name] = {
                "generation": int(blob.generation),
                "size": path.stat().st_size,
                "sha256": digest,
            }
        comparison.check()
        return files

    def restore_files(self, job_id, files, comparison):
        started = time.monotonic()
        expected = {"comparison.sqlite", "baseline-metadata", "target-metadata"}
        if (
            set(files) != expected
            or sum(ref["size"] for ref in files.values())
            > comparison.limits.max_disk_bytes
        ):
            raise engine.ComparisonError("Invalid complete comparison cache bundle")
        for name, ref in files.items():
            path = comparison.directory / name
            generation = ref["generation"]
            blob = self.bucket.blob(f"jobs/{job_id}/{name}", generation=generation)
            with (
                blob.open(
                    "rb",
                    if_generation_match=generation,
                    chunk_size=1024 * 1024,
                    timeout=15,
                ) as source,
                path.open("wb") as destination,
            ):
                size, digest = 0, hashlib.sha256()
                while data := source.read(1024 * 1024):
                    comparison.check(started=started)
                    size += len(data)
                    if size > ref["size"]:
                        raise engine.ComparisonError(
                            "Comparison cache file exceeds its declared size"
                        )
                    destination.write(data)
                    digest.update(data)
            if size != ref["size"] or digest.hexdigest() != ref["sha256"]:
                raise engine.ComparisonError(
                    "Comparison cache file checksum or size differs"
                )


def owner_key(owner):
    return hashlib.sha256(owner.encode()).hexdigest()


def restore_comparison(comparison, state):
    comparison.summary = state["summary"]
    comparison.metadata_sources = {
        side: (comparison.directory / f"{side}-metadata", state["metadata_refs"][side])
        for side in ("baseline", "target")
    }
