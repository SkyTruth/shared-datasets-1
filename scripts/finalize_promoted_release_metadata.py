#!/usr/bin/env python3
"""Finalize promoted release manifests and run records after reviewed copies."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from google.api_core.exceptions import NotFound, PreconditionFailed

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import gcs_asset, release_feature_model  # noqa: E402


class FinalizeReleaseMetadataError(RuntimeError):
    """Raised when promoted release metadata cannot be finalized."""


@dataclass(frozen=True)
class BlobInfo:
    path: str
    generation: int
    size: int
    content_type: str = ""
    sha256: str = ""

    def to_record(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "path": self.path,
            "generation": self.generation,
            "size": self.size,
        }
        if self.content_type:
            payload["content_type"] = self.content_type
        if self.sha256:
            payload["sha256"] = self.sha256
        return payload


def sha256_hex(data: bytes | str) -> str:
    payload = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(payload).hexdigest()


def artifact_uri_to_latest_uri(uri: str) -> str | None:
    marker = "/releases/"
    if marker not in uri:
        return None
    prefix, rest = uri.split(marker, 1)
    parts = rest.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return f"{prefix}/latest/{parts[1]}"


def stat_blob(client: Any, uri: str, *, include_sha256: bool = False) -> BlobInfo:
    bucket_name, object_name = gcs_asset.parse_gs_uri(uri)
    blob = client.bucket(bucket_name).blob(object_name)
    try:
        blob.reload()
    except NotFound as exc:
        raise FinalizeReleaseMetadataError(f"promoted object is missing: {uri}") from exc
    digest = ""
    if include_sha256:
        digest = sha256_hex(blob.download_as_bytes())
    return BlobInfo(
        path=uri,
        generation=int(blob.generation),
        size=int(blob.size or 0),
        content_type=str(blob.content_type or ""),
        sha256=digest,
    )


def maybe_stat_blob(client: Any, uri: str) -> BlobInfo | None:
    bucket_name, object_name = gcs_asset.parse_gs_uri(uri)
    blob = client.bucket(bucket_name).blob(object_name)
    try:
        blob.reload()
    except NotFound:
        return None
    return BlobInfo(
        path=uri,
        generation=int(blob.generation),
        size=int(blob.size or 0),
        content_type=str(blob.content_type or ""),
    )


def manifest_destination_uris(plan: Mapping[str, Any]) -> list[str]:
    uris: list[str] = []
    seen: set[str] = set()
    for promotion in plan.get("promotions") or []:
        if not isinstance(promotion, Mapping):
            continue
        uri = str(promotion.get("destination_uri") or "")
        if uri.endswith(".manifest.json") and uri not in seen:
            uris.append(uri)
            seen.add(uri)
    return uris


def run_record_destination_uris(plan: Mapping[str, Any]) -> list[str]:
    uris: list[str] = []
    seen: set[str] = set()
    for promotion in plan.get("promotions") or []:
        if not isinstance(promotion, Mapping):
            continue
        uri = str(promotion.get("destination_uri") or "")
        if "/runs/" in uri and uri.endswith(".json") and uri not in seen:
            uris.append(uri)
            seen.add(uri)
    return uris


def finalized_manifest_payload(
    manifest: Mapping[str, Any],
    *,
    stat: Callable[[str], BlobInfo],
    maybe_stat: Callable[[str], BlobInfo | None],
) -> dict[str, Any]:
    payload = dict(manifest)
    artifacts: list[dict[str, Any]] = []
    for raw_artifact in payload.get("artifacts") or []:
        if not isinstance(raw_artifact, Mapping):
            raise FinalizeReleaseMetadataError("manifest artifacts must be objects")
        artifact = dict(raw_artifact)
        role = str(artifact.get("role") or artifact.get("format") or "")
        path = str(artifact.get("path") or "")
        if not role or not path:
            raise FinalizeReleaseMetadataError("manifest artifact is missing role or path")
        if role == "manifest":
            artifact.pop("generation", None)
            artifact.pop("latest_generation", None)
            latest_path = artifact.get("latest_path") or artifact_uri_to_latest_uri(path)
            if latest_path and maybe_stat(str(latest_path)):
                artifact["latest_path"] = str(latest_path)
            artifacts.append(artifact)
            continue

        info = stat(path)
        artifact["generation"] = info.generation
        artifact["size"] = info.size
        if info.content_type:
            artifact["content_type"] = info.content_type
        latest_path = artifact.get("latest_path") or artifact_uri_to_latest_uri(path)
        if latest_path:
            latest_info = maybe_stat(str(latest_path))
            if latest_info:
                artifact["latest_path"] = latest_info.path
                artifact["latest_generation"] = latest_info.generation
        artifacts.append(artifact)

    payload["artifacts"] = artifacts
    release_feature_model.validate_release_manifest(
        payload,
        expected_asset_slug=str(payload.get("asset_slug") or ""),
        expected_release=str(payload.get("release") or ""),
        require_generations=True,
    )
    return payload


def replace_json_object(client: Any, uri: str, payload: Mapping[str, Any]) -> BlobInfo:
    gcs_asset.require_mutation_allowed(uri, operation="upload")
    bucket_name, object_name = gcs_asset.parse_gs_uri(uri)
    blob = client.bucket(bucket_name).blob(object_name)
    try:
        blob.reload()
    except NotFound as exc:
        raise FinalizeReleaseMetadataError(f"object is missing before replacement: {uri}") from exc
    generation = int(blob.generation)
    text = json.dumps(dict(payload), indent=2, sort_keys=True) + "\n"
    try:
        blob.upload_from_string(
            text,
            content_type="application/json",
            if_generation_match=generation,
        )
    except PreconditionFailed as exc:
        raise FinalizeReleaseMetadataError(f"object generation changed before replacement: {uri}") from exc
    blob.reload()
    return BlobInfo(
        path=uri,
        generation=int(blob.generation),
        size=int(blob.size or 0),
        content_type=str(blob.content_type or ""),
        sha256=sha256_hex(text),
    )


def load_json_object(client: Any, uri: str) -> dict[str, Any]:
    bucket_name, object_name = gcs_asset.parse_gs_uri(uri)
    blob = client.bucket(bucket_name).blob(object_name)
    try:
        blob.reload()
    except NotFound as exc:
        raise FinalizeReleaseMetadataError(f"object is missing: {uri}") from exc
    payload = json.loads(blob.download_as_text())
    if not isinstance(payload, dict):
        raise FinalizeReleaseMetadataError(f"object must be a JSON object: {uri}")
    return payload


def update_path_entries(
    values: Any,
    *,
    stat: Callable[[str], BlobInfo],
    manifest_infos: Mapping[str, BlobInfo],
) -> list[Any]:
    updated: list[Any] = []
    for value in values or []:
        if not isinstance(value, Mapping):
            updated.append(value)
            continue
        path = str(value.get("path") or "")
        if not path:
            updated.append(dict(value))
            continue
        info = manifest_infos.get(path) if path.endswith(".manifest.json") else stat(path)
        entry = {**dict(value), **info.to_record()}
        updated.append(entry)
    return updated


def finalized_run_record_payload(
    record: Mapping[str, Any],
    *,
    stat: Callable[[str], BlobInfo],
    manifest_infos: Mapping[str, BlobInfo],
) -> dict[str, Any]:
    payload = dict(record)
    payload["release_paths"] = update_path_entries(
        payload.get("release_paths"),
        stat=stat,
        manifest_infos=manifest_infos,
    )
    payload["latest_paths"] = update_path_entries(
        payload.get("latest_paths"),
        stat=stat,
        manifest_infos=manifest_infos,
    )
    release_manifest = next(
        (
            info
            for path, info in manifest_infos.items()
            if "/releases/" in path and path.endswith(".manifest.json")
        ),
        None,
    )
    if release_manifest and release_manifest.sha256:
        sha256 = dict(payload.get("sha256") or {})
        sha256["manifest"] = release_manifest.sha256
        payload["sha256"] = sha256
    return payload


def finalize_promoted_release_metadata(plan: Mapping[str, Any], *, client: Any) -> dict[str, Any]:
    manifest_infos: dict[str, BlobInfo] = {}
    finalized_manifests: list[dict[str, Any]] = []
    for uri in manifest_destination_uris(plan):
        manifest = load_json_object(client, uri)
        payload = finalized_manifest_payload(
            manifest,
            stat=lambda path: stat_blob(client, path),
            maybe_stat=lambda path: maybe_stat_blob(client, path),
        )
        info = replace_json_object(client, uri, payload)
        manifest_infos[uri] = info
        finalized_manifests.append(info.to_record())

    finalized_run_records: list[dict[str, Any]] = []
    if manifest_infos:
        for uri in run_record_destination_uris(plan):
            record = load_json_object(client, uri)
            payload = finalized_run_record_payload(
                record,
                stat=lambda path: stat_blob(client, path),
                manifest_infos=manifest_infos,
            )
            finalized_run_records.append(replace_json_object(client, uri, payload).to_record())

    return {
        "finalized_manifests": finalized_manifests,
        "finalized_run_records": finalized_run_records,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish-plan", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = json.loads(args.publish_plan.read_text())
        if not isinstance(plan, dict):
            raise FinalizeReleaseMetadataError("publish plan must be a JSON object")
        result = finalize_promoted_release_metadata(plan, client=gcs_asset.get_client())
    except (FinalizeReleaseMetadataError, OSError, json.JSONDecodeError) as exc:
        print(f"finalize-promoted-release-metadata failed: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
