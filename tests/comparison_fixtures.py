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
        if item["role"] in {"metadata", "schema", "manifest", "pmtiles", "fgb"}:
            path = paths[item["role"]]
            files[item["role"]] = {
                "path": item["path"],
                "generation": str(item.get("generation", generation + 2)),
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    return {"asset_slug": "example", "release": release, "files": files}, paths


def generated(contract="contract-v1"):
    return model.build_identity_metadata(
        strategy="generated_sequence_content_hash",
        contract_id=contract,
        next_generated_feature_id_before_release=1,
        next_generated_feature_id_after_release=10,
    )


def historical_bundle(root, release):
    """A persisted v1 polygon bundle, including exact full-precision FGB bytes."""
    import geopandas as gpd
    import pyogrio
    from shapely.geometry import MultiPolygon, Polygon, mapping

    # The historical reader must not round MultiPolygon coordinates to 6 decimals.
    geom = MultiPolygon(
        [Polygon([(0.123456789012345, 0), (1, 0), (1, 1), (0.123456789012345, 0)])]
    )
    feature_id = "gen:coral-example"
    properties = {"ext_id": "1", "name": "reef"}
    feature_hash = (
        "sha256:"
        + hashlib.sha256(
            model.canonical_json(
                {"geometry": mapping(geom), "properties": properties}
            ).encode()
        ).hexdigest()
    )
    ref, paths = bundle(
        root,
        release,
        [],
        fields=[
            {"name": "ext_id", "type": "string"},
            {"name": "name", "type": "string"},
        ],
    )
    pyogrio.write_dataframe(
        gpd.GeoDataFrame(
            [{"feature_id": feature_id, "feature_hash": feature_hash, **properties}],
            geometry=[geom],
            crs="EPSG:4326",
        ),
        paths["fgb"],
        driver="FlatGeobuf",
    )
    old = {
        "schema_version": 1,
        "asset_slug": "example",
        "release": release,
        "feature_id": feature_id,
        "feature_hash": feature_hash,
        "properties": properties,
    }
    import gzip

    with gzip.open(paths["metadata"], "wt") as output:
        output.write(json.dumps(old) + "\n")
    schema = json.loads(paths["schema"].read_text())
    schema["schema_version"] = 1
    paths["schema"].write_text(json.dumps(schema))
    manifest = json.loads(paths["manifest"].read_text())
    manifest.update(
        schema_version=1,
        release_feature_model_schema_version=1,
        schema=schema,
        feature_hash_algorithm="sha256:canonical-feature-content:v1",
        identity={"strategy": "generated_hash"},
        validation={"feature_count": 1},
    )
    for item in manifest["artifacts"]:
        role = item["role"]
        if role != "manifest":
            item.update(
                size=paths[role].stat().st_size,
                sha256=hashlib.sha256(paths[role].read_bytes()).hexdigest(),
            )
    paths["manifest"].write_text(json.dumps(manifest))
    for role, file in ref["files"].items():
        file.update(
            size=paths[role].stat().st_size,
            sha256=hashlib.sha256(paths[role].read_bytes()).hexdigest(),
        )
    return (ref, paths), mapping(geom)
