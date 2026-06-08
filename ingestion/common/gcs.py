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
from ingestion.common.feature_metadata import VECTOR_BUNDLE_SUFFIXES
from ingestion.common.runtime import content_type_for
from scripts import release_feature_model


LOGGER = logging.getLogger(__name__)
DEFAULT_RELEASE_SUFFIXES = VECTOR_BUNDLE_SUFFIXES


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

    def load_latest_metadata_records(self, asset: ReleaseAsset) -> list[dict[str, Any]] | None:
        object_name = asset.latest_object(".metadata.ndjson.gz")
        blob = self.bucket.blob(object_name)
        try:
            blob.reload()
        except NotFound:
            return None
        try:
            records = list(
                release_feature_model.read_metadata_sidecar_bytes(
                    blob.download_as_bytes(),
                    label=f"gs://{self.bucket.name}/{object_name}",
                )
            )
            validation = release_feature_model.validate_sidecar_records(records)
            if not validation.valid:
                raise release_feature_model.ReleaseFeatureModelError("; ".join(validation.errors))
            release_feature_model.previous_feature_id_mapping(records)
        except release_feature_model.ReleaseFeatureModelError as exc:
            raise RuntimeError(
                f"{asset.slug} latest metadata sidecar has incompatible feature identity mappings; "
                "refusing to reset generated sequence feature_id values: "
                f"{exc}"
            ) from exc
        return records

    def load_successful_run_record(
        self,
        asset: ReleaseAsset,
        run_date: dt.date,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        object_name = asset.run_record_object(run_date)
        blob = self.bucket.blob(object_name)
        try:
            blob.reload()
        except NotFound:
            return None
        record = json.loads(blob.download_as_text())
        if record.get("status") != "success":
            return None
        return (
            record,
            {
                "path": f"gs://{self.bucket.name}/{object_name}",
                "generation": int(blob.generation),
                "size": int(blob.size or 0),
            },
        )

    def successful_run_record(self, asset: ReleaseAsset, run_date: dt.date) -> bool:
        return self.load_successful_run_record(asset, run_date) is not None

    def record_existing_successful_release(
        self,
        asset: ReleaseAsset,
        run_date: dt.date,
    ) -> dict[str, Any] | None:
        loaded = self.load_successful_run_record(asset, run_date)
        if loaded is None:
            return None
        record, run_record_info = loaded
        try:
            return release_index.record_successful_release(
                self.bucket,
                asset.slug,
                record,
                run_record_info=run_record_info,
            )
        except release_index.ReleaseIndexError as exc:
            self.logger.warning(
                "could not refresh release index from existing successful run record "
                "for %s %s: %s",
                asset.slug,
                run_date.isoformat(),
                exc,
            )
            return None

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

    def replace_latest_metadata_from_run_record(
        self,
        asset: ReleaseAsset,
        run_date: dt.date,
        metadata: dict[str, str],
    ) -> list[dict[str, Any]]:
        loaded = self.load_successful_run_record(asset, run_date)
        if loaded is None:
            return []
        record, _run_record_info = loaded
        refreshed = []
        for value in record.get("latest_paths") or []:
            path = release_index.path_from_info(value)
            if not path:
                continue
            bucket_name, object_name = release_index.split_gs_uri(path)
            if bucket_name != self.bucket.name:
                raise RuntimeError(
                    f"Latest object bucket does not match publisher bucket: {path}"
                )
            expected_generation = value.get("generation") if isinstance(value, dict) else None
            blob = self.bucket.blob(object_name)
            try:
                blob.reload()
            except NotFound:
                self.logger.warning("latest object is missing: %s", path)
                continue
            if expected_generation is not None and int(blob.generation) != int(expected_generation):
                self.logger.warning(
                    "latest object generation changed before metadata refresh: %s",
                    path,
                )
                continue
            metageneration_match = int(getattr(blob, "metageneration", 0) or 0) or None
            blob.metadata = metadata
            patch_kwargs = {}
            if metageneration_match is not None:
                patch_kwargs["if_metageneration_match"] = metageneration_match
            try:
                blob.patch(**patch_kwargs)
            except PreconditionFailed as exc:
                raise RuntimeError(
                    f"Latest object metadata changed before patch: {path}"
                ) from exc
            blob.reload()
            refreshed.append(
                {
                    "path": path,
                    "generation": int(blob.generation),
                    "metageneration": int(getattr(blob, "metageneration", 0) or 0),
                    "size": int(blob.size or 0),
                }
            )
        return refreshed

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
