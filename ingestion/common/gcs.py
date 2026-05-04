"""Safe GCS publishing helpers for scheduled ingestion jobs."""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any, Protocol

from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage

from ingestion.common import release_index
from ingestion.common.runtime import content_type_for


LOGGER = logging.getLogger(__name__)
DEFAULT_RELEASE_SUFFIXES = (".fgb", ".pmtiles")


class ReleaseAsset(Protocol):
    slug: str

    def release_object(self, run_date: dt.date, suffix: str) -> str: ...

    def latest_object(self, suffix: str) -> str: ...

    def run_record_object(self, run_date: dt.date) -> str: ...


class GcsPublisher:
    """Publish versioned dataset outputs with GCS generation preconditions."""

    def __init__(
        self,
        client: storage.Client,
        bucket_name: str,
        *,
        release_suffixes: tuple[str, ...] = DEFAULT_RELEASE_SUFFIXES,
        logger: logging.Logger | None = None,
    ) -> None:
        self.bucket = client.bucket(bucket_name)
        self.release_suffixes = release_suffixes
        self.logger = logger or LOGGER

    def blob_exists(self, name: str) -> bool:
        blob = self.bucket.blob(name)
        try:
            blob.reload()
            return True
        except NotFound:
            return False

    def load_json(self, name: str) -> dict[str, Any] | None:
        blob = self.bucket.blob(name)
        try:
            blob.reload()
        except NotFound:
            return None
        return json.loads(blob.download_as_text())

    def successful_run_record(self, asset: ReleaseAsset, run_date: dt.date) -> bool:
        record = self.load_json(asset.run_record_object(run_date))
        return bool(record and record.get("status") == "success")

    def assert_no_partial_release(
        self,
        asset: ReleaseAsset,
        run_date: dt.date,
        *,
        suffixes: tuple[str, ...] | None = None,
    ) -> None:
        release_suffixes = suffixes or self.release_suffixes
        existing = [
            name
            for name in (
                asset.release_object(run_date, suffix)
                for suffix in release_suffixes
            )
            if self.blob_exists(name)
        ]
        if existing:
            raise RuntimeError(
                "Release object(s) already exist without a successful run record: "
                + ", ".join(f"gs://{self.bucket.name}/{name}" for name in existing)
            )

    def upload_new_object(
        self,
        *,
        local_path: Path,
        object_name: str,
        metadata: dict[str, str],
    ) -> dict[str, Any]:
        blob = self.bucket.blob(object_name)
        blob.metadata = metadata
        try:
            blob.upload_from_filename(
                local_path,
                content_type=content_type_for(local_path),
                if_generation_match=0,
            )
        except PreconditionFailed as exc:
            raise RuntimeError(
                f"Refusing to overwrite existing release object: "
                f"gs://{self.bucket.name}/{object_name}"
            ) from exc
        blob.reload()
        return {
            "path": f"gs://{self.bucket.name}/{object_name}",
            "generation": int(blob.generation),
            "size": int(blob.size or 0),
        }

    def replace_latest_object(
        self,
        *,
        local_path: Path,
        object_name: str,
        metadata: dict[str, str],
    ) -> dict[str, Any]:
        blob = self.bucket.blob(object_name)
        try:
            blob.reload()
            generation_match = int(blob.generation)
        except NotFound:
            generation_match = 0
        blob.metadata = metadata
        try:
            blob.upload_from_filename(
                local_path,
                content_type=content_type_for(local_path),
                if_generation_match=generation_match,
            )
        except PreconditionFailed as exc:
            raise RuntimeError(
                f"Latest object generation changed before upload: "
                f"gs://{self.bucket.name}/{object_name}"
            ) from exc
        blob.reload()
        return {
            "path": f"gs://{self.bucket.name}/{object_name}",
            "generation": int(blob.generation),
            "size": int(blob.size or 0),
        }

    def write_run_record(
        self,
        *,
        asset: ReleaseAsset,
        run_date: dt.date,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        object_name = asset.run_record_object(run_date)
        blob = self.bucket.blob(object_name)
        blob.metadata = {
            "asset_slug": asset.slug,
            "run_date": run_date.isoformat(),
        }
        index_payload = payload
        try:
            blob.upload_from_string(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                content_type="application/json",
                if_generation_match=0,
            )
        except PreconditionFailed as exc:
            existing = self.load_json(object_name)
            if existing and existing.get("status") == "success":
                self.logger.info("%s run record already exists", asset.slug)
                index_payload = existing
                blob.reload()
            else:
                raise RuntimeError(
                    f"Run record already exists and is not successful: "
                    f"gs://{self.bucket.name}/{object_name}"
                ) from exc
        blob.reload()
        run_record_info = {
            "path": f"gs://{self.bucket.name}/{object_name}",
            "generation": int(blob.generation),
            "size": int(blob.size or 0),
        }
        if index_payload.get("status") == "success":
            release_index.record_successful_release(
                self.bucket,
                asset.slug,
                index_payload,
                run_record_info=run_record_info,
            )
        else:
            release_index.record_latest_run(
                self.bucket,
                asset.slug,
                index_payload,
                run_record_info=run_record_info,
            )
        return run_record_info

    def update_latest_run_index(
        self,
        *,
        asset: ReleaseAsset,
        payload: dict[str, Any],
        run_record_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return release_index.record_latest_run(
            self.bucket,
            asset.slug,
            payload,
            run_record_info=run_record_info,
        )
