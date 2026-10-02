"""Tiny, fully pinned canonical bundles for comparison acceptance tests."""

import hashlib
import json
from pathlib import Path

from scripts import release_feature_model as model


def record(feature_id, release, *, name="value", geometry=None, extra=None):
    properties = {"feature_id": str(feature_id), "name": name, **(extra or {})}
    return {
        "schema_version": 2,
        "asset_slug": "example",
        "release": release,
        "feature_id": str(feature_id),
        "geometry_hash": model.geometry_hash(
            geometry or {"type": "Point", "coordinates": [int(feature_id), 0]}
        ),
        "properties_hash": model.properties_hash(properties),
        "properties": properties,
        "provenance": {"provider": "Synthetic"},
    }


def bundle(root: Path, release, records, *, identity=None, fields=None, generation=10):
    root.mkdir(parents=True, exist_ok=True)
    uri = f"gs://example-bucket/category/subcategory/example/releases/{release}"
    paths = {
        role: root / f"example.{suffix}"
        for role, suffix in {
            "metadata": "metadata.ndjson.gz",
            "schema": "schema.json",
            "manifest": "manifest.json",
            "fgb": "fgb",
            "pmtiles": "pmtiles",
        }.items()
    }
    fields = fields or [
        {"name": "feature_id", "type": "string", "nullable": False},
        {"name": "name", "type": "string"},
    ]
    schema = model.build_release_schema(
        asset_slug="example", release=release, fields=fields
    )
    paths["schema"].write_text(json.dumps(schema))
    paths["fgb"].write_bytes(b"synthetic-download-only")
    paths["pmtiles"].write_bytes(b"synthetic-display-only")
    model.write_metadata_sidecar(records, paths["metadata"])
    artifacts = []
    for offset, (role, path) in enumerate(paths.items()):
        item = {"role": role, "format": role, "path": f"{uri}/{path.name}"}
        if role != "manifest":
            item.update(
                generation=generation + offset,
                size=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        artifacts.append(item)
    identity = identity or model.build_identity_metadata(
        strategy="source_field", source_fields=["feature_id"]
    )
    manifest = model.build_release_manifest(
        asset_slug="example",
        release=release,
        source_inputs=[{"citation": "Synthetic"}],
        artifacts=artifacts,
        schema=schema,
        identity=identity,
        validation={"feature_count": len(records)},
    )
    paths["manifest"].write_text(json.dumps(manifest))
    files = {}
    for item in artifacts:
        if item["role"] in {"metadata", "schema", "manifest", "pmtiles"}:
            path = paths[item["role"]]
            files[item["role"]] = {
                "path": item["path"],
                "generation": str(item.get("generation", generation + 2)),
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    return {"asset_slug": "example", "release": release, "files": files}, paths
