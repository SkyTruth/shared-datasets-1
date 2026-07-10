"""Shared asset-path and release-bundle helpers for vector ingestion pipelines."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from ingestion.common import feature_metadata
from ingestion.common.gcs import GcsPublisher
from ingestion.common.runtime import sha256_file


CORE_BUNDLE_ROLES = ("fgb", "pmtiles", "metadata", "schema")
BUNDLE_ROLES = (*CORE_BUNDLE_ROLES, "manifest")


class AssetPaths:
    """Mixin providing canonical bucket object paths for a dataset asset.

    Subclasses must provide ``slug`` and ``parent`` attributes (typically
    dataclass fields).
    """

    @property
    def root(self) -> str:
        return f"{self.parent}/{self.slug}"

    @property
    def runs_prefix(self) -> str:
        return f"{self.root}/runs/"

    def release_prefix(self, run_date: dt.date) -> str:
        return f"{self.root}/releases/{run_date.isoformat()}"

    def release_object(self, run_date: dt.date, suffix: str) -> str:
        return f"{self.release_prefix(run_date)}/{self.slug}{suffix}"

    def latest_object(self, suffix: str) -> str:
        return f"{self.root}/latest/{self.slug}{suffix}"

    def run_record_object(self, run_date: dt.date) -> str:
        return f"{self.runs_prefix}{run_date.isoformat()}.json"


class VectorBundleAsset(Protocol):
    slug: str

    @property
    def root(self) -> str: ...

    def release_object(self, run_date: dt.date, suffix: str) -> str: ...

    def latest_object(self, suffix: str) -> str: ...


class VectorBundleOutputs(Protocol):
    """Local artifact bundle produced by a vector pipeline build step."""

    @property
    def fgb(self) -> Path: ...

    @property
    def pmtiles(self) -> Path: ...

    @property
    def metadata(self) -> Path: ...

    @property
    def schema(self) -> Path: ...

    @property
    def manifest(self) -> Path: ...

    @property
    def row_count(self) -> int: ...

    @property
    def sha256(self) -> dict[str, str]: ...

    @property
    def schema_payload(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PublishedVectorBundle:
    release_by_role: dict[str, dict[str, Any]]
    latest_by_role: dict[str, dict[str, Any]]
    extra_release_paths: tuple[dict[str, Any], ...]
    extra_latest_paths: tuple[dict[str, Any], ...]
    sha256: dict[str, str]

    @property
    def release_paths(self) -> list[dict[str, Any]]:
        return [self.release_by_role[role] for role in BUNDLE_ROLES]

    @property
    def latest_paths(self) -> list[dict[str, Any]]:
        return [self.latest_by_role[role] for role in BUNDLE_ROLES]


def publish_vector_bundle(
    *,
    publisher: GcsPublisher,
    asset: VectorBundleAsset,
    run_date: dt.date,
    outputs: VectorBundleOutputs,
    object_metadata: dict[str, str],
    source_inputs: Sequence[Mapping[str, Any]],
    identity: Mapping[str, Any],
    extra_suffix_paths: Sequence[tuple[str, Path]] = (),
) -> PublishedVectorBundle:
    """Upload the standard FGB/PMTiles/metadata/schema/manifest release bundle.

    Uploads each artifact as a new release object, replaces the matching
    ``latest/`` copy, writes the final manifest last, and returns the recorded
    blob info by role. ``extra_suffix_paths`` entries are uploaded between the
    metadata and schema artifacts, preserving the historical upload order for
    pipelines with additional sidecars.
    """
    role_suffix_paths = {
        "fgb": (".fgb", outputs.fgb),
        "pmtiles": (".pmtiles", outputs.pmtiles),
        "metadata": (".metadata.ndjson.gz", outputs.metadata),
        "schema": (".schema.json", outputs.schema),
    }

    release_by_role: dict[str, dict[str, Any]] = {}
    extra_release: list[dict[str, Any]] = []
    for role in ("fgb", "pmtiles", "metadata"):
        suffix, path = role_suffix_paths[role]
        release_by_role[role] = publisher.upload_new_object(
            local_path=path,
            object_name=asset.release_object(run_date, suffix),
            metadata=object_metadata,
        )
    for suffix, path in extra_suffix_paths:
        extra_release.append(
            publisher.upload_new_object(
                local_path=path,
                object_name=asset.release_object(run_date, suffix),
                metadata=object_metadata,
            )
        )
    release_by_role["schema"] = publisher.upload_new_object(
        local_path=outputs.schema,
        object_name=asset.release_object(run_date, ".schema.json"),
        metadata=object_metadata,
    )

    latest_by_role: dict[str, dict[str, Any]] = {}
    extra_latest: list[dict[str, Any]] = []
    for role in ("fgb", "pmtiles", "metadata"):
        suffix, path = role_suffix_paths[role]
        latest_by_role[role] = publisher.replace_latest_object(
            local_path=path,
            object_name=asset.latest_object(suffix),
            metadata=object_metadata,
        )
    for suffix, path in extra_suffix_paths:
        extra_latest.append(
            publisher.replace_latest_object(
                local_path=path,
                object_name=asset.latest_object(suffix),
                metadata=object_metadata,
            )
        )
    latest_by_role["schema"] = publisher.replace_latest_object(
        local_path=outputs.schema,
        object_name=asset.latest_object(".schema.json"),
        metadata=object_metadata,
    )

    manifest_release_object = asset.release_object(run_date, ".manifest.json")
    manifest_latest_object = asset.latest_object(".manifest.json")
    feature_metadata.write_manifest(
        feature_metadata.final_manifest_payload(
            asset_slug=asset.slug,
            release=run_date.isoformat(),
            bucket_name=publisher.bucket.name,
            asset_root=asset.root,
            sha256_by_role=outputs.sha256,
            schema=outputs.schema_payload,
            source_inputs=list(source_inputs),
            identity=identity,
            feature_count=outputs.row_count,
            release_blob_info_by_role={role: release_by_role[role] for role in CORE_BUNDLE_ROLES},
            latest_blob_info_by_role={role: latest_by_role[role] for role in CORE_BUNDLE_ROLES},
            manifest_release_path=f"gs://{publisher.bucket.name}/{manifest_release_object}",
            manifest_latest_path=f"gs://{publisher.bucket.name}/{manifest_latest_object}",
        ),
        outputs.manifest,
    )
    release_by_role["manifest"] = publisher.upload_new_object(
        local_path=outputs.manifest,
        object_name=manifest_release_object,
        metadata=object_metadata,
    )
    latest_by_role["manifest"] = publisher.replace_latest_object(
        local_path=outputs.manifest,
        object_name=manifest_latest_object,
        metadata=object_metadata,
    )
    return PublishedVectorBundle(
        release_by_role=release_by_role,
        latest_by_role=latest_by_role,
        extra_release_paths=tuple(extra_release),
        extra_latest_paths=tuple(extra_latest),
        sha256={**outputs.sha256, "manifest": sha256_file(outputs.manifest)},
    )
