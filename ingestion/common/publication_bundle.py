"""Shared immutable vector-bundle recipe and versioned derivations.

Runtime adapters establish identity and authorization. This module constructs
one checkpoint/data/manifest/run/index graph for generated and source-field IDs.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from ingestion.common import feature_metadata, publication as p, release_index
from ingestion.common import release_feature_model as model
from ingestion.common.runtime import content_type_for
from scripts import vector_asset

def blob_info(version: p.ObjectVersion) -> dict[str, Any]:
    return {"path": version.path, "generation": version.generation, "size": version.size}


def derive(parameters: dict[str, Any], results: Mapping[str, p.ObjectVersion]) -> bytes:
    kind = parameters["kind"]
    if kind in {"manifest", "manifest-v2"}:
        extra_artifacts = [
            {"role": key.removeprefix("release-"), "format": "metadata", **blob_info(version), "sha256": version.sha256,
             "latest_path": results[key.replace("release-", "latest-", 1)].path,
             "latest_generation": results[key.replace("release-", "latest-", 1)].generation}
            for key, version in results.items() if key.startswith("release-extra-") and kind == "manifest-v2"
        ]
        payload = feature_metadata.final_manifest_payload(
            **parameters["payload"],
            extra_artifacts=extra_artifacts,
            release_blob_info_by_role={role: blob_info(results[f"release-{role}"]) for role in ("fgb", "pmtiles", "metadata", "schema")},
            latest_blob_info_by_role={role: blob_info(results[f"latest-{role}"]) for role in ("fgb", "pmtiles", "metadata", "schema")},
        )
    elif kind == "translation-manifest-v1":
        payload = {**parameters["payload"], "artifacts": []}
        by_path = {version.path: version for version in results.values()}
        for original in parameters["payload"]["artifacts"]:
            artifact = dict(original)
            if version := by_path.get(artifact["path"]):
                artifact.update(blob_info(version), sha256=version.sha256)
                latest_path = artifact["path"].replace(f"/releases/{payload['release']}/", "/latest/")
                artifact.update(latest_path=latest_path, latest_generation=by_path[latest_path].generation)
            payload["artifacts"].append(artifact)
        model.validate_release_manifest(payload, require_generations=True)
    elif kind == "translation-run-v1":
        payload = dict(parameters["payload"])
        for field, prefix in (("release_paths", "release-"), ("latest_paths", "latest-")):
            paths = {entry["path"]: entry for entry in payload[field]}
            paths.update({results[key].path: blob_info(results[key]) for key in parameters["artifact_operations"] if key.startswith(prefix)})
            payload[field] = list(paths.values())
        payload["sha256"] = {**payload["sha256"], "manifest": results["release-manifest"].sha256,
                             **{role: results[key].sha256 for role, key in parameters["hash_roles"].items()}}
    elif kind == "run":
        payload = {**parameters["payload"],
                   "release_paths": [blob_info(results[key]) for key in parameters["release_operations"]],
                   "latest_paths": [blob_info(results[key]) for key in parameters["latest_operations"]],
                   "sha256": {**parameters["hashes"], "manifest": results["release-manifest"].sha256}}
    elif kind == "index":
        run_parameters = parameters["run_parameters"]
        record = p.strict_json(derive(run_parameters, results))
        run_info = blob_info(results["run-record"])
        payload = release_index.merge_successful_release(
            parameters["index"], release_index.release_entry_from_record(record, run_record_info=run_info),
            latest_run=release_index.run_entry_from_record(record, run_record_info=run_info), updated_at=parameters["updated_at"],
        )
    elif kind == "skip-index":
        payload = release_index.merge_latest_run(parameters["index"], release_index.run_entry_from_record(parameters["record"]), updated_at=parameters["updated_at"])
    else:
        raise p.PublicationError("unsupported generated publication derivation")
    return p.canonical(payload)


def operation(key, phase, destination, source, expected, slug, metadata=None):
    return {"id": key, "phase": phase, "destination": destination, "expected_generation": expected,
            "source": source, "content_type": content_type_for(Path(destination)), "cache_control": "no-cache",
            "user_metadata": metadata if metadata is not None else {"asset_slug": slug}}

def prepare_bundle(*, context, store, asset, run_date, outputs, object_metadata, source_inputs,
                   identity, record, current, reservation=None, translations=None,
                   extra_suffix_paths=(), asset_readme=None):
    roles = {role: (suffix, getattr(outputs, role)) for role, suffix in feature_metadata.ROLE_SUFFIXES.items() if role != "manifest"}
    for index, (suffix, path) in enumerate(extra_suffix_paths):
        roles[f"extra-{index}"] = (suffix, path)
    native = vector_asset.validate_metadata_lookup_bundle(outputs.fgb, outputs.pmtiles)
    p.require(native.valid, "native generated bundle validation failed: " + "; ".join(native.errors))
    from ingestion.common.identity_index import DiskIdentityRecords
    with tempfile.TemporaryDirectory(prefix="publication-validation-", dir=outputs.metadata.parent) as temp:
        index = DiskIdentityRecords(Path(temp) / "identities.sqlite")
        try:
            validation = model.validate_sidecar_records(model.read_metadata_sidecar(outputs.metadata),
                expected_asset_slug=asset.slug, expected_release=run_date.isoformat(), identity_index=index)
            p.require(validation.valid and validation.feature_count == outputs.row_count, "generated metadata bundle is invalid")
            if reservation is not None:
                model.GeneratedIdentityBaseline(index.seal(), reservation["next"], run_date.isoformat(), contract_id=context.identity_contract)
        finally:
            index.close()
    p.require(p.strict_json(outputs.schema.read_bytes()) == outputs.schema_payload, "schema bytes differ from manifest schema")
    model.validate_release_schema(outputs.schema_payload, expected_asset_slug=asset.slug, expected_release=run_date.isoformat())
    operations, local_sources = [], {}
    for index, (role, (suffix, path)) in enumerate(roles.items()):
        with path.open("rb") as handle:
            sha = hashlib.file_digest(handle, "sha256").hexdigest()
        if role in outputs.sha256:
            p.require(sha == outputs.sha256[role], "generated artifact changed since validation")
        local_sources[role] = path
        source = {"kind": "local", "key": role, "sha256": sha, "size": path.stat().st_size}
        checkpoint_suffix = suffix if suffix != ".metadata-translations.csv" else ".csv"
        operations.append(operation(f"input-{role}", "checkpoint", f"gs://{context.bucket}/{asset.root}/publications/inputs/{context.proposal_key}/{index}{checkpoint_suffix}", source, 0, asset.slug))
    if asset_readme is not None:
        data = asset_readme.read_bytes()
        p.require(bool(data), "asset README must not be empty")
        local_sources["asset-readme"] = asset_readme
        source = {"kind": "local", "key": "asset-readme", "sha256": p.digest(data), "size": len(data)}
        operations.append(operation("input-asset-readme", "checkpoint",
            f"gs://{context.bucket}/{asset.root}/publications/inputs/{context.proposal_key}/{len(roles)}.md",
            source, 0, asset.slug))
    for prefix in ("release", "latest"):
        for role, (suffix, _path) in roles.items():
            destination = f"gs://{context.bucket}/" + (asset.release_object(run_date, suffix) if prefix == "release" else asset.latest_object(suffix))
            head = store.head(destination)
            expected = (head.generation if head else 0) if prefix == "latest" else 0
            source = {"kind": "result", "operation": f"input-{role}" if prefix == "release" else f"release-{role}"}
            operations.append(operation(f"{prefix}-{role}", "data", destination, source, expected, asset.slug, object_metadata))
    payload = {"asset_slug": asset.slug, "release": run_date.isoformat(), "bucket_name": context.bucket, "asset_root": asset.root,
               "sha256_by_role": outputs.sha256, "schema": outputs.schema_payload, "source_inputs": source_inputs, "identity": identity,
               "feature_count": outputs.row_count, "manifest_release_path": f"gs://{context.bucket}/{asset.release_object(run_date, '.manifest.json')}",
               "manifest_latest_path": f"gs://{context.bucket}/{asset.latest_object('.manifest.json')}"}
    if translations is not None:
        model.validate_translation_coverage(translations)
        payload["translations"] = translations
    dependencies = [op["id"] for op in operations if op["phase"] == "data"]
    # Persist the new derivation explicitly. Existing captured "manifest"
    # intents must reproduce their original bytes when resumed.
    source = {"kind": "derived", "version": p.FINALIZATION_VERSION, "parameters": {"kind": "manifest-v2", "payload": payload}, "dependencies": dependencies}
    operations.append(operation("release-manifest", "commit", payload["manifest_release_path"], source, 0, asset.slug, object_metadata))
    operations.append(operation("latest-manifest", "commit", payload["manifest_latest_path"], {"kind": "result", "operation": "release-manifest"}, current["latest_manifest"]["generation"], asset.slug, object_metadata))
    ordered_roles = ["fgb", "pmtiles", "metadata", "schema", "manifest", *[role for role in roles if role.startswith("extra-")]]
    run_parameters = {"kind": "run", "payload": record, "hashes": outputs.sha256,
                      "release_operations": [f"release-{role}" for role in ordered_roles], "latest_operations": [f"latest-{role}" for role in ordered_roles]}
    dependencies = [op["id"] for op in operations if op["phase"] in {"data", "commit"}]
    operations.append(operation("run-record", "asset_derived", f"gs://{context.bucket}/{asset.run_record_object(run_date)}",
                                     {"kind": "derived", "version": p.FINALIZATION_VERSION, "parameters": run_parameters, "dependencies": dependencies}, 0, asset.slug))
    if asset_readme is not None:
        uri = f"gs://{context.bucket}/{asset.root}/README.md"
        head = store.head(uri)
        operations.append(operation("asset-readme", "asset_derived", uri,
            {"kind": "result", "operation": "input-asset-readme"},
            head.generation if head else 0, asset.slug))
    index_uri = release_index.release_index_uri(context.bucket, asset.slug)
    index = store.read_json(index_uri)
    now = dt.datetime.now(dt.UTC).isoformat()
    parameters = {"kind": "index", "run_parameters": run_parameters, "index": release_index.coerce_release_index(index.value if index else None, asset.slug), "updated_at": now}
    operations.append(operation("release-index", "asset_derived", index_uri,
                                     {"kind": "derived", "version": p.FINALIZATION_VERSION, "parameters": parameters, "dependencies": [*dependencies, "run-record"]}, index.version.generation if index else 0, asset.slug))
    intent = p.Intent.build({"schema_version": 1, "context": asdict(context), "mode": "complete_release", "release": run_date.isoformat(),
                            "predecessor": current["latest_manifest"], "reservation": reservation,
                            "prepared_at": now, "operations": operations, "notification": None})
    return intent, local_sources
