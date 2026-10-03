#!/usr/bin/env python3
"""Compare pinned canonical release bundles using a task-scoped SQLite index."""

from __future__ import annotations

import argparse
import datetime as dt
import re
import gzip
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts import release_feature_model as model

POLICY = "feature-id-hashes-v1"
RESULT_VERSION = 4
LEGACY_FEATURE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
LEGACY_BOOKKEEPING = {"ext_id", "feature_hash", "feature_id"}


def reject_json_constant(value):
    raise ComparisonError(f"Non-finite JSON number is invalid: {value}")


def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ComparisonError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def read_json(payload):
    value = json.loads(
        payload,
        parse_constant=reject_json_constant,
        object_pairs_hook=unique_json_object,
    )
    if not isinstance(value, dict):
        raise ComparisonError("Release contract/record must be a JSON object")
    return value


def legacy_projection_hash(properties):
    # FlatGeobuf represents null by omitting the property entry. Only this
    # format-boundary fingerprint collapses null/absence; source hashes do not.
    return model.sha256_hex(
        model.canonical_json({k: v for k, v in properties.items() if v is not None})
    )


CLASSES = ("added", "removed", "geometry_only", "properties_only", "both", "unchanged")


class ComparisonError(ValueError):
    pass


class ComparisonLimit(ComparisonError):
    pass


class ComparisonCancelled(ComparisonError):
    pass


@dataclass(frozen=True)
class Limits:
    max_input_bytes: int = 64 * 1024 * 1024
    max_expanded_bytes: int = 256 * 1024 * 1024
    max_rows: int = 100_000
    max_disk_bytes: int = 128 * 1024 * 1024
    max_seconds: int = 600
    max_geometry_bytes: int = 2 * 1024 * 1024 * 1024


def work_root() -> Path:
    return Path(
        os.environ.get(
            "SHARED_DATASETS_WORKDIR",
            str(Path(tempfile.gettempdir()) / "shared-datasets-1"),
        )
    )


def artifact(raw: Mapping) -> dict:
    if not isinstance(raw, Mapping):
        raise ComparisonError("Artifact descriptor must be an object")
    path, generation = raw.get("path"), raw.get("generation")
    if not isinstance(path, str) or not path.startswith("gs://"):
        raise ComparisonError("Input path must be an exact gs:// object URI")
    if (
        isinstance(generation, bool)
        or not re.fullmatch(r"[1-9][0-9]{0,19}", str(generation))
        or not 0 < int(generation) < 2**64
    ):
        raise ComparisonError(
            "Every comparison input needs a positive exact generation"
        )
    result = {"path": path, "generation": str(int(generation))}
    if "sha256" in raw:
        result["sha256"] = model.validate_artifact_hash(raw["sha256"], label=path)
    if "size" in raw:
        if type(raw["size"]) is not int or raw["size"] < 0:
            raise ComparisonError("Input size must be a non-negative integer")
        result["size"] = raw["size"]
    return result


def snapshot(raw: Mapping) -> dict:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("files"), Mapping):
        raise ComparisonError(
            "Snapshot needs asset_slug, release and files keyed by metadata/schema/manifest"
        )
    slug, release = raw.get("asset_slug"), raw.get("release")
    if (
        not isinstance(slug, str)
        or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug)
        or not isinstance(release, str)
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", release)
    ):
        raise ComparisonError("Snapshot identity is missing")
    try:
        dt.date.fromisoformat(release)
    except ValueError as exc:
        raise ComparisonError("Release must be a real calendar date") from exc
    files = {
        role: artifact(raw["files"][role])
        for role in ("metadata", "schema", "manifest")
    }
    if not files["metadata"]["path"].endswith(f"/{slug}.metadata.ndjson.gz"):
        raise ComparisonError(
            "Comparison requires the canonical source-language sidecar"
        )
    root = files["metadata"]["path"].split(f"/releases/{release}/")[0]
    for role, suffix in (
        ("metadata", "metadata.ndjson.gz"),
        ("schema", "schema.json"),
        ("manifest", "manifest.json"),
    ):
        if files[role]["path"] != f"{root}/releases/{release}/{slug}.{suffix}":
            raise ComparisonError("Input is outside the selected asset release")
    if "pmtiles" in raw["files"]:
        files["pmtiles"] = artifact(raw["files"]["pmtiles"])
        if files["pmtiles"]["path"] != f"{root}/releases/{release}/{slug}.pmtiles":
            raise ComparisonError("Map input is outside the selected asset release")
    if "fgb" in raw["files"]:
        files["fgb"] = artifact(raw["files"]["fgb"])
        if files["fgb"]["path"] != f"{root}/releases/{release}/{slug}.fgb":
            raise ComparisonError(
                "Geometry input is outside the selected asset release"
            )
    return {"asset_slug": slug, "release": release, "files": files}


def identity_compatibility(
    before: dict, after: dict, old_fields: dict, new_fields: dict
) -> dict:
    if any(x.get("release_feature_model_schema_version") != 2 for x in (before, after)):
        return {
            "compatible": False,
            "reason": "Historical feature identity cannot be joined across releases.",
        }
    a, b = before.get("identity"), after.get("identity")
    try:
        model.validate_identity_metadata(a)
        model.validate_identity_metadata(b)
    except model.ReleaseFeatureModelError as exc:
        return {"compatible": False, "reason": f"Insufficient identity evidence: {exc}"}
    semantics = (
        "strategy",
        "source_fields",
        "feature_id_regex",
        "hash_algorithm",
        "canonicalization",
        "assignment_key",
    )
    if any(a.get(key) != b.get(key) for key in semantics):
        return {
            "compatible": False,
            "reason": "Identity strategy, source fields or assignment semantics differ.",
        }
    if sorted(a.get("properties_hash_excluded_properties", [])) != sorted(
        b.get("properties_hash_excluded_properties", [])
    ):
        return {
            "compatible": False,
            "reason": "Source-property hash exclusions differ.",
        }
    if before.get("hashes") != after.get("hashes") or before.get("hashes") != {
        "geometry_hash_algorithm": model.GEOMETRY_HASH_ALGORITHM,
        "properties_hash_algorithm": model.PROPERTIES_HASH_ALGORITHM,
    }:
        return {
            "compatible": False,
            "reason": "Hash semantics are missing or incompatible.",
        }
    if a["strategy"].startswith("generated_sequence"):
        if (
            not a.get("contract_id")
            or not b.get("contract_id")
            or a["contract_id"] != b["contract_id"]
        ):
            return {
                "compatible": False,
                "reason": "Generated identity contract is missing or differs (possible reset).",
            }
        if any(
            x.get("sequence_state_version") != model.GENERATED_SEQUENCE_STATE_VERSION
            for x in (a, b)
        ):
            return {
                "compatible": False,
                "reason": "Generated sequence state evidence is missing.",
            }
    else:
        for field in a["source_fields"]:
            if (
                field not in old_fields
                or field not in new_fields
                or old_fields[field] != new_fields[field]
            ):
                return {
                    "compatible": False,
                    "reason": f"Source identity field {field!r} has missing or incompatible schema semantics.",
                }
    return {
        "compatible": True,
        "reason": "Same declared identity and hash semantics; match only by feature_id.",
    }


def schema_changes(a: dict, b: dict) -> dict:
    return {
        "added": [asdict(b[k]) for k in sorted(b.keys() - a.keys())],
        "removed": [asdict(a[k]) for k in sorted(a.keys() - b.keys())],
        "datatype_changes": [
            {"field": k, "before": a[k].type, "after": b[k].type}
            for k in sorted(a.keys() & b.keys())
            if a[k].type != b[k].type
        ],
        "field_semantics_changes": [
            {"field": k, "before": asdict(a[k]), "after": asdict(b[k])}
            for k in sorted(a.keys() & b.keys())
            if (a[k].nullable, a[k].projectable) != (b[k].nullable, b[k].projectable)
        ],
    }


def property_changes(old: dict | None, new: dict | None) -> list[dict]:
    a, b = (old or {}).get("properties", {}), (new or {}).get("properties", {})
    changes = []
    for field in sorted(a.keys() | b.keys()):
        x = {"present": field in a, **({"value": a[field]} if field in a else {})}
        y = {"present": field in b, **({"value": b[field]} if field in b else {})}
        if model.canonical_json(x) != model.canonical_json(y):
            changes.append({"field": field, "before": x, "after": y})
    return changes


class Comparison:
    def __init__(
        self,
        directory: Path,
        *,
        limits: Limits = Limits(),
        cancelled: Callable[[], bool] = lambda: False,
        progress: Callable[[str, int], None] = lambda phase, rows: None,
    ):
        self.directory, self.limits = directory, limits
        directory.mkdir(parents=True, exist_ok=True)
        self.db_path = directory / "comparison.sqlite"
        self.cancelled, self.progress = cancelled, progress
        self.started = time.monotonic()
        self.summary = None
        self.metadata_sources = {}

    def check(self, *, started=None):
        if self.cancelled():
            raise ComparisonCancelled("Comparison cancelled")
        if (
            time.monotonic() - (self.started if started is None else started)
            > self.limits.max_seconds
        ):
            raise ComparisonLimit(
                "Comparison time budget exceeded; use the local CLI with a larger explicit budget"
            )
        if (
            sum(p.stat().st_size for p in self.directory.iterdir() if p.is_file())
            > self.limits.max_disk_bytes
        ):
            raise ComparisonLimit("Comparison disk budget exceeded; use the local CLI")

    def verify_bytes(self, path: Path, ref: dict, *, started=None):
        self.check(started=started)
        size = path.stat().st_size
        if size > self.limits.max_input_bytes:
            raise ComparisonLimit("Input exceeds max_input_bytes; use the local CLI")
        if "size" in ref and size != ref["size"]:
            raise ComparisonError("Input size does not match pinned declaration")
        if "sha256" in ref:
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if digest != ref["sha256"]:
                raise ComparisonError(
                    "Input checksum does not match pinned declaration"
                )

    def iter_records(self, path: Path, *, started=None):
        """Stream bounded records without retaining expanded metadata."""
        count, expanded = 0, 0
        with gzip.open(path, "rb") as handle:
            while True:
                self.check(started=started)
                offset = handle.tell()
                line = handle.readline(model.DEFAULT_MAX_SIDECAR_RECORD_BYTES + 1)
                if not line:
                    break
                expanded += len(line)
                if (
                    len(line) > model.DEFAULT_MAX_SIDECAR_RECORD_BYTES
                    or expanded > self.limits.max_expanded_bytes
                ):
                    raise ComparisonLimit(
                        "Expanded sidecar or row exceeds the comparison budget; use the local CLI"
                    )
                if not line.strip():
                    continue
                count += 1
                if count > self.limits.max_rows:
                    raise ComparisonLimit("Sidecar exceeds max_rows; use the local CLI")
                yield offset, read_json(line)

    def detail_source(self, side, *, started):
        path, ref = self.metadata_sources[side]
        # CLI paths can be changed after classification. Recheck the pinned bytes
        # before using them as the detail/search source.
        self.verify_bytes(path, ref, started=started)
        return path

    def detail_record(self, side, feature_id, offset, *, started):
        path = self.detail_source(side, started=started)
        with gzip.open(path, "rb") as handle:
            # Offsets point into the validated uncompressed stream. Seeking
            # decompresses preceding bytes without parsing preceding JSON rows.
            handle.seek(offset)
            self.check(started=started)
            record = read_json(
                handle.readline(model.DEFAULT_MAX_SIDECAR_RECORD_BYTES + 1)
            )
        if record["feature_id"] != feature_id:
            raise ComparisonError(
                "Validated metadata offset differs from the indexed feature"
            )
        return record

    def load(self, db, side: str, ref: dict, paths: dict, open_geometry=None):
        for role in ("metadata", "schema", "manifest"):
            self.verify_bytes(paths[role], ref["files"][role])
        if any(
            paths[role].stat().st_size > 4 * 1024 * 1024
            for role in ("schema", "manifest")
        ):
            raise ComparisonLimit(
                "Schema or manifest exceeds the 4 MiB contract budget"
            )
        schema = read_json(paths["schema"].read_text())
        manifest = read_json(paths["manifest"].read_text())
        legacy = schema.get("schema_version") == 1
        if legacy and (
            manifest.get("schema_version") != 1
            or manifest.get("release_feature_model_schema_version") != 1
            or manifest.get("feature_hash_algorithm")
            != "sha256:canonical-feature-content:v1"
        ):
            raise ComparisonError(
                "Historical comparison needs the declared v1 feature contract"
            )
        fields = model.validate_release_schema(
            {**schema, "schema_version": 2} if legacy else schema,
            expected_asset_slug=ref["asset_slug"],
            expected_release=ref["release"],
        )
        # Legacy identity evidence is a capability failure, never permission to guess IDs.
        artifacts = model.validate_release_manifest(
            {
                **manifest,
                "schema_version": 2,
                "release_feature_model_schema_version": 2,
                "schema": {**schema, "schema_version": 2},
                "index_load_status": "Firestore metadata serving is inactive",
                "index_status_policy": {
                    "mode": "inactive_firestore_serving",
                    "path": None,
                },
            }
            if legacy
            else manifest,
            expected_asset_slug=ref["asset_slug"],
            expected_release=ref["release"],
            require_generations=True,
            validate_identity=False,
        )
        if manifest["schema"] != schema:
            raise ComparisonError("Manifest schema differs from the pinned schema")
        for role in ref["files"]:
            declared, pinned = artifacts[role], ref["files"][role]
            if declared["path"] != pinned["path"] or (
                role != "manifest"
                and str(declared["generation"]) != pinned["generation"]
            ):
                raise ComparisonError(
                    f"Manifest {role} identity differs from the selected snapshot"
                )
            if role != "manifest":
                evidence = artifact(declared)
                for key in ("sha256", "size"):
                    if (
                        key in pinned
                        and key in evidence
                        and pinned[key] != evidence[key]
                    ):
                        raise ComparisonError(
                            f"Manifest {role} {key} differs from selected snapshot"
                        )
                if role in paths and role != "fgb":
                    self.verify_bytes(paths[role], evidence)
        db.execute(
            f"CREATE TABLE {side} (id TEXT PRIMARY KEY, identity_key TEXT UNIQUE, geometry BLOB, properties_hash BLOB, metadata_offset INTEGER NOT NULL)"
        )
        if legacy:
            # Only v1's FGB validation needs these hashes, not the full properties.
            db.execute(
                "CREATE TEMP TABLE legacy_records (id TEXT PRIMARY KEY, feature_hash TEXT, projection_hash TEXT)"
            )
        raw_identity = manifest.get("identity")
        try:
            model.validate_identity_metadata(raw_identity)
            identity = raw_identity
        except model.ReleaseFeatureModelError:
            identity = None
        assertions = manifest.get("validation")
        if (
            not isinstance(assertions, Mapping)
            or type(assertions.get("feature_count")) is not int
            or assertions["feature_count"] < 0
        ):
            raise ComparisonError(
                "Manifest needs a non-negative complete feature_count"
            )
        count = 0
        for offset, record in self.iter_records(paths["metadata"]):
            if legacy:
                if (
                    record.get("schema_version") != 1
                    or record.get("asset_slug") != ref["asset_slug"]
                    or record.get("release") != ref["release"]
                    or not isinstance(record.get("feature_id"), str)
                    or not LEGACY_FEATURE_ID_RE.fullmatch(record["feature_id"])
                    or not isinstance(record.get("properties"), dict)
                ):
                    raise ComparisonError("Invalid historical sidecar record")
                model.validate_hash(
                    record.get("feature_hash", ""), label="feature_hash"
                )
                if set(record["properties"]) - set(fields):
                    raise ComparisonError(
                        "Historical properties are outside the release schema"
                    )
                record["properties"] = {
                    k: v
                    for k, v in record["properties"].items()
                    if k not in LEGACY_BOOKKEEPING
                }
                record["geometry_hash"] = None
                record["properties_hash"] = model.properties_hash(record["properties"])
            else:
                self.validate_record(record, ref, fields, identity)
            key_value = None if legacy else model.identity_key_from_record(record)
            key = None if legacy else model.canonical_json(key_value)
            try:
                db.execute(
                    f"INSERT INTO {side} VALUES (?, ?, ?, ?, ?)",
                    (
                        record["feature_id"],
                        key,
                        bytes.fromhex(record["geometry_hash"][7:])
                        if not legacy
                        else None,
                        bytes.fromhex(record["properties_hash"][7:]),
                        offset,
                    ),
                )
                if legacy:
                    db.execute(
                        "INSERT INTO legacy_records VALUES (?, ?, ?)",
                        (
                            record["feature_id"],
                            record["feature_hash"],
                            legacy_projection_hash(record["properties"]),
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                raise ComparisonError(
                    "Duplicate feature_id or record identity key"
                ) from exc
            count += 1
            if count % 500 == 0:
                self.progress(side, count)
        if assertions["feature_count"] != count:
            raise ComparisonError(
                "Manifest feature_count differs from the complete sidecar"
            )
        if legacy:
            self.load_legacy_geometry(
                db,
                side,
                ref,
                paths,
                fields,
                count,
                open_geometry,
                artifacts.get("fgb", {}),
            )
            db.execute("DROP TABLE legacy_records")
        self.metadata_sources[side] = (
            paths["metadata"],
            {**artifact(artifacts["metadata"]), **ref["files"]["metadata"]},
        )
        self.progress(side, count)
        declared_fields = {
            f["name"]: model.ReleaseSchemaField(
                name=f["name"],
                type=f["type"],
                nullable=f.get("nullable", True),
                projectable=f.get("projectable", True),
            )
            for f in schema["fields"]
        }
        return manifest, declared_fields, count

    def validate_record(self, record, ref, fields, identity):
        validation = model.validate_sidecar_records(
            [record],
            expected_asset_slug=ref["asset_slug"],
            expected_release=ref["release"],
        )
        if not validation.valid:
            raise ComparisonError("; ".join(validation.errors))
        if type(record["feature_id"]) is not str:
            raise ComparisonError("feature_id must be a string")
        props = record["properties"]
        if set(props) - set(fields):
            raise ComparisonError("Record has properties outside the release schema")
        if identity is not None and (
            model.properties_hash(
                props,
                exclude_properties=identity.get(
                    "properties_hash_excluded_properties", []
                ),
            )
            != record["properties_hash"]
        ):
            raise ComparisonError(
                "Record properties_hash does not match canonical properties"
            )
        if identity is not None and identity.get("strategy") == "source_field":
            field = identity["source_fields"][0]
            if (
                model.source_field_feature_id(
                    source_field=field, source_value=props.get(field)
                )
                != record["feature_id"]
            ):
                raise ComparisonError(
                    "Record does not match the declared source-field identity"
                )
        elif identity is not None and identity.get("strategy", "").startswith(
            "generated_sequence"
        ):
            if not model.GENERATED_FEATURE_ID_RE.fullmatch(record["feature_id"]):
                raise ComparisonError(
                    "Generated feature_id must be a monotonic decimal string"
                )
            next_id = identity.get("next_generated_feature_id_after_release")
            if next_id is not None and int(record["feature_id"]) >= next_id:
                raise ComparisonError(
                    "Generated feature_id exceeds the declared allocation state"
                )
        key_value = model.identity_key_from_record(record)
        if (
            identity is not None
            and identity.get("strategy") == "generated_sequence_source_fields"
        ):
            if key_value != model.source_fields_identity_key(
                props, identity["source_fields"]
            ):
                raise ComparisonError(
                    "Record assignment key differs from declared source fields"
                )
        if (
            identity is not None
            and identity.get("strategy") == "generated_sequence_content_hash"
        ):
            if key_value != (
                record["geometry_hash"],
                record["properties_hash"],
            ):
                raise ComparisonError(
                    "Record assignment key differs from content hashes"
                )

    def load_legacy_geometry(
        self, db, side, ref, paths, fields, expected_count, open_geometry, declared_fgb
    ):
        """Recover separate hashes from canonical v1 geometry, never display tiles."""
        from flatgeobuf.FlatGeobuf.Feature import Feature
        from flatgeobuf.FlatGeobuf.GeometryType import GeometryType
        from flatgeobuf.generic.feature import parse_properties
        from flatgeobuf.geojson.geometry import to_geojson_coordinates
        from flatgeobuf.header_meta import from_byte_buffer
        from flatgeobuf.packedrtree import calc_tree_size

        fgb = ref["files"].get("fgb")
        if fgb is None or (open_geometry is None and "fgb" not in paths):
            raise ComparisonError("Historical comparison needs the exact canonical FGB")
        fgb = {**artifact(declared_fgb), **fgb}
        if fgb.get("size", 0) > self.limits.max_geometry_bytes:
            raise ComparisonLimit(
                "Historical FGB exceeds the streaming geometry budget"
            )
        digest, total = hashlib.sha256(), 0

        def geometry(value, kind):
            kind = value.Type() if kind == GeometryType.Unknown else kind
            if kind == GeometryType.MultiPolygon:
                return {
                    "type": "MultiPolygon",
                    "coordinates": [
                        geometry(value.Parts(i), GeometryType.Polygon)["coordinates"]
                        for i in range(value.PartsLength())
                    ],
                }
            if kind == GeometryType.GeometryCollection:
                return {
                    "type": "GeometryCollection",
                    "geometries": [
                        geometry(value.Parts(i), GeometryType.Unknown)
                        for i in range(value.PartsLength())
                    ],
                }
            names = {
                GeometryType.Point: "Point",
                GeometryType.MultiPoint: "MultiPoint",
                GeometryType.LineString: "LineString",
                GeometryType.MultiLineString: "MultiLineString",
                GeometryType.Polygon: "Polygon",
            }
            if kind not in names:
                raise ComparisonError("Unsupported canonical historical geometry type")
            return {
                "type": names[kind],
                "coordinates": to_geojson_coordinates(value, kind),
            }

        with open_geometry(fgb) if open_geometry else paths["fgb"].open("rb") as stream:

            def read(size, *, eof=False):
                nonlocal total
                self.check()
                if not 0 <= size <= 64 * 1024 * 1024:
                    raise ComparisonLimit("Historical FGB feature exceeds 64 MiB")
                data = stream.read(size)
                total += len(data)
                digest.update(data)
                if total > self.limits.max_geometry_bytes:
                    raise ComparisonLimit(
                        "Historical FGB exceeds the streaming geometry budget"
                    )
                if len(data) != size and not (eof and not data):
                    raise ComparisonError("Historical FGB is truncated")
                return data

            if read(8) != b"fgb\x03fgb\x01":
                raise ComparisonError("Historical geometry is not FlatGeobuf v3")
            header_size = int.from_bytes(read(4), "little")
            if not 0 < header_size <= 4 * 1024 * 1024:
                raise ComparisonError("Invalid historical FGB header size")
            header = from_byte_buffer(bytearray(read(header_size)))
            if header.features_count != expected_count:
                raise ComparisonError(
                    "Historical FGB feature count differs from the complete sidecar"
                )
            remaining = (
                (
                    80
                    if header.features_count == 1
                    else calc_tree_size(header.features_count, header.index_node_size)
                )
                if header.index_node_size and header.features_count
                else 0
            )
            while remaining:
                size = min(remaining, 1024 * 1024)
                read(size)
                remaining -= size
            count = 0
            while prefix := read(4, eof=True):
                value = Feature.GetRootAsFeature(
                    bytearray(read(int.from_bytes(prefix, "little")))
                )
                props = parse_properties(value, header.columns)
                feature_id = props.get("feature_id")
                row = db.execute(
                    f"SELECT r.geometry, l.feature_hash, l.projection_hash FROM {side} r JOIN legacy_records l USING(id) WHERE r.id=?",
                    (feature_id,),
                ).fetchone()
                if row is None or row[0] is not None:
                    raise ComparisonError(
                        "Historical FGB IDs differ from the complete sidecar"
                    )
                projected = {
                    k: v
                    for k, v in props.items()
                    if k in fields and k not in LEGACY_BOOKKEEPING
                }
                if (
                    legacy_projection_hash(projected) != row[2]
                    or props.get("feature_hash") != row[1]
                ):
                    raise ComparisonError(
                        "Historical FGB metadata differs from the pinned sidecar"
                    )
                raw = value.Geometry()
                hashed = model.geometry_hash(
                    geometry(raw, header.geometry_type) if raw else None
                )
                db.execute(
                    f"UPDATE {side} SET geometry=? WHERE id=?",
                    (bytes.fromhex(hashed[7:]), feature_id),
                )
                count += 1
                if count % 500 == 0:
                    self.progress(f"{side} geometry", count)
            if count != expected_count:
                raise ComparisonError("Historical FGB is incomplete")
        if "size" in fgb and total != fgb["size"]:
            raise ComparisonError(
                "Historical FGB size differs from the selected snapshot"
            )
        if "sha256" in fgb and digest.hexdigest() != fgb["sha256"]:
            raise ComparisonError(
                "Historical FGB checksum differs from the selected snapshot"
            )

    @contextmanager
    def checked_connection(self, *, started=None):
        self.check(started=started)
        interrupted = []

        def check_query():
            try:
                self.check(started=started)
                return 0
            except ComparisonError as exc:
                interrupted.append(exc)
                return 1

        with sqlite3.connect(self.db_path) as db:
            db.set_progress_handler(check_query, 10000)
            try:
                yield db
            except sqlite3.OperationalError:
                if interrupted:
                    raise interrupted[-1]
                raise

    def run(
        self,
        baseline: dict,
        target: dict,
        baseline_paths: dict,
        target_paths: dict,
        *,
        open_geometry=None,
    ) -> dict:
        baseline, target = snapshot(baseline), snapshot(target)
        if baseline["asset_slug"] != target["asset_slug"]:
            raise ComparisonError("Compare releases of one asset")
        if self.db_path.exists():
            raise ComparisonError(
                "Comparison index already exists; use a fresh work directory"
            )
        with self.checked_connection() as db:
            db.execute("PRAGMA cache_size=-8192")
            db.execute("PRAGMA temp_store=FILE")
            a, af, ac = self.load(
                db, "baseline", baseline, baseline_paths, open_geometry
            )
            b, bf, bc = self.load(db, "target", target, target_paths, open_geometry)
            for side in ("baseline", "target"):
                db.execute(
                    f"CREATE INDEX {side}_geometry ON {side}(geometry, properties_hash)"
                )
            db.execute("""CREATE TABLE geometry_display AS
                SELECT geometry, CASE
                    WHEN NOT EXISTS (SELECT 1 FROM target b WHERE b.geometry=a.geometry) THEN 'removed'
                    WHEN EXISTS (SELECT 1 FROM baseline x WHERE x.geometry=a.geometry AND NOT EXISTS
                        (SELECT 1 FROM target y WHERE y.geometry=x.geometry AND y.properties_hash=x.properties_hash))
                      OR EXISTS (SELECT 1 FROM target y WHERE y.geometry=a.geometry AND NOT EXISTS
                        (SELECT 1 FROM baseline x WHERE x.geometry=y.geometry AND x.properties_hash=y.properties_hash))
                    THEN 'metadata_changed' ELSE 'unchanged' END AS color
                FROM baseline a GROUP BY geometry
                UNION ALL SELECT geometry, 'novel' FROM target b
                    WHERE NOT EXISTS (SELECT 1 FROM baseline a WHERE a.geometry=b.geometry) GROUP BY geometry""")
            db.execute(
                "CREATE UNIQUE INDEX geometry_display_hash ON geometry_display(geometry)"
            )
            compatibility = identity_compatibility(a, b, af, bf)
            geometry_counts = dict.fromkeys(
                ("novel", "removed", "metadata_changed", "unchanged"), 0
            )
            geometry_counts.update(
                dict(
                    db.execute(
                        "SELECT color, count(*) FROM geometry_display GROUP BY color"
                    )
                )
            )
            counts = None
            if compatibility["compatible"]:
                self.progress("classifying", ac + bc)
                self.check()
                db.execute("""CREATE TABLE changes AS
                    SELECT a.id, CASE WHEN b.id IS NULL THEN 'removed'
                      WHEN a.geometry=b.geometry AND a.properties_hash=b.properties_hash THEN 'unchanged'
                      WHEN a.geometry=b.geometry THEN 'properties_only'
                      WHEN a.properties_hash=b.properties_hash THEN 'geometry_only' ELSE 'both' END AS classification
                    FROM baseline a LEFT JOIN target b ON a.id=b.id
                    UNION ALL SELECT b.id, 'added' FROM target b LEFT JOIN baseline a ON a.id=b.id WHERE a.id IS NULL""")
                db.execute("CREATE UNIQUE INDEX change_id ON changes(id)")
                counts = dict.fromkeys(CLASSES, 0)
                counts.update(
                    dict(
                        db.execute(
                            "SELECT classification, count(*) FROM changes GROUP BY classification"
                        )
                    )
                )
                self.check()
        self.check()
        self.summary = {
            "result_schema_version": RESULT_VERSION,
            "policy": POLICY,
            "inputs": {"baseline": baseline, "target": target},
            "input_key": hashlib.sha256(
                model.canonical_json(
                    [POLICY, RESULT_VERSION, baseline, target]
                ).encode()
            ).hexdigest(),
            "identity": compatibility,
            "counts": counts,
            "geometry_counts": geometry_counts,
            "feature_counts": {"baseline": ac, "target": bc},
            "schema_changes": schema_changes(af, bf),
            "limits": asdict(self.limits),
            "method": "Complete canonical sidecars; match compatible feature_id; compare geometry_hash/properties_hash. Historical v1 geometry hashes come from the exact canonical FGB, with legacy bookkeeping excluded from source properties. No spatial matching or tile-derived counts. Provenance and localization excluded.",
            "map_method": "Comparable feature IDs use their own classification: additions and new positions green, removals and old positions red, stationary metadata edits yellow, unchanged gray. Incompatible identities use exact geometry_hash sets; yellow means contents differ here, without pairing objects. Complete compressed per-release classification indexes color all loaded features independently of table pagination.",
            "property_hash_exclusions": sorted(
                model.HASH_EXCLUDED_PROPERTIES
                | set(
                    a.get("identity", {}).get("properties_hash_excluded_properties", [])
                )
                | set(
                    b.get("identity", {}).get("properties_hash_excluded_properties", [])
                )
            ),
            "publication": {
                "baseline": a.get("source_inputs", []),
                "target": b.get("source_inputs", []),
            },
        }
        return self.summary

    def map_rows_sql(self, side: str) -> str:
        """One map projection shared by batch lookups, inspection and filters."""
        if self.summary["identity"]["compatible"]:
            movement = "removed" if side == "baseline" else "novel"
            return f"""SELECT r.id, r.geometry, CASE c.classification
                WHEN 'unchanged' THEN 'unchanged'
                WHEN 'properties_only' THEN 'metadata_changed'
                ELSE '{movement}' END AS color
                FROM {side} r JOIN changes c ON r.id=c.id"""
        return f"""SELECT r.id, r.geometry, g.color
            FROM {side} r JOIN geometry_display g ON r.geometry=g.geometry"""

    def map_features(self, side: str, feature_ids: list[str]) -> list[dict]:
        """Resolve each release-scoped feature using the declared identity contract."""
        if self.summary is None:
            raise ComparisonError("Comparison is incomplete")
        if (
            not isinstance(side, str)
            or side not in {"baseline", "target"}
            or not isinstance(feature_ids, list)
            or not 1 <= len(feature_ids) <= 200
        ):
            raise ComparisonError("Select one release and 1–200 map feature IDs")
        for feature_id in feature_ids:
            if not isinstance(feature_id, str):
                raise ComparisonError("Map feature IDs must be strings")
            if not LEGACY_FEATURE_ID_RE.fullmatch(feature_id):
                raise ComparisonError("Invalid display feature ID")
        if len(set(feature_ids)) != len(feature_ids):
            raise ComparisonError("Map feature IDs must be unique")
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute(
                f"SELECT id, color, geometry FROM ({self.map_rows_sql(side)}) WHERE id IN ({','.join('?' for _ in feature_ids)}) ORDER BY id COLLATE BINARY",
                feature_ids,
            ).fetchall()
        if len(rows) != len(feature_ids):
            raise ComparisonError(
                "Display feature is absent from the complete selected release input"
            )
        return [
            {
                "feature_id": feature_id,
                "change": change,
                "geometry_hash": "sha256:" + geometry.hex(),
            }
            for feature_id, change, geometry in rows
        ]

    def iter_map_index(self, side):
        """Stream the complete compact index, never geometry or metadata records."""
        if self.summary is None or side not in {"baseline", "target"}:
            raise ComparisonError("Select one completed release side")
        with self.checked_connection(started=time.monotonic()) as db:
            db.execute("PRAGMA cache_size=-8192")
            for feature_id, change, geometry in db.execute(
                f"SELECT id, color, geometry FROM ({self.map_rows_sql(side)}) ORDER BY id COLLATE BINARY"
            ):
                yield [feature_id, change, "sha256:" + geometry.hex()]

    def page(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        query: str = "",
        classification: str = "",
        geometry_change: str = "",
    ) -> dict:
        if self.summary is None or not self.summary["identity"]["compatible"]:
            raise ComparisonError("Authoritative feature classification is unavailable")
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 100
            or len(query) > 200
            or classification not in ("", *CLASSES)
            or geometry_change
            not in ("", "novel", "removed", "metadata_changed", "unchanged")
        ):
            raise ComparisonError("Invalid page options")
        started = time.monotonic()
        where, args = "WHERE 1=1", []
        if classification:
            where += " AND c.classification=?"
            args.append(classification)
        if geometry_change:
            where += f""" AND (EXISTS (SELECT 1 FROM ({self.map_rows_sql("baseline")}) m
                WHERE m.id=c.id AND m.color=?)
                OR EXISTS (SELECT 1 FROM ({self.map_rows_sql("target")}) m
                WHERE m.id=c.id AND m.color=?))"""
            args.extend([geometry_change, geometry_change])
        with self.checked_connection(started=started) as db:
            if query:
                # Search is optional. Retain only matching IDs for this request,
                # not another expanded copy of either release's metadata.
                db.execute("PRAGMA temp_store=MEMORY")
                db.execute(
                    "CREATE TEMP TABLE search_ids (id TEXT PRIMARY KEY) WITHOUT ROWID"
                )
                needle = query.lower()
                for side in ("baseline", "target"):
                    path = self.detail_source(side, started=started)
                    for _, record in self.iter_records(path, started=started):
                        if (
                            needle in record["feature_id"].lower()
                            or needle
                            in model.canonical_json(record["properties"]).lower()
                        ):
                            db.execute(
                                "INSERT OR IGNORE INTO search_ids VALUES (?)",
                                (record["feature_id"],),
                            )
                where += " AND c.id IN (SELECT id FROM search_ids)"
            total = db.execute(
                f"SELECT count(*) FROM changes c {where}",
                args,
            ).fetchone()[0]
            rows = [
                {"feature_id": i, "classification": c, **self.map_membership(db, i)}
                for i, c in db.execute(
                    f"SELECT c.id, c.classification FROM changes c {where} ORDER BY c.id COLLATE BINARY LIMIT ? OFFSET ?",
                    [*args, limit, offset],
                )
            ]
        return {"rows": rows, "total": total, "offset": offset, "limit": limit}

    def map_membership(self, db, feature_id):
        result = {}
        for side in ("baseline", "target"):
            row = db.execute(
                f"SELECT geometry, color FROM ({self.map_rows_sql(side)}) WHERE id=?",
                (feature_id,),
            ).fetchone()
            result["map_before" if side == "baseline" else "map_after"] = (
                {"geometry_hash": "sha256:" + row[0].hex(), "change": row[1]}
                if row
                else None
            )
        return result

    def inspect(self, feature_id: str) -> dict:
        model.validate_feature_id(feature_id)
        if not self.summary or not self.summary["identity"]["compatible"]:
            raise ComparisonError("Authoritative feature inspection is unavailable")
        started = time.monotonic()
        with self.checked_connection(started=started) as db:
            classification = db.execute(
                "SELECT classification FROM changes WHERE id=?", (feature_id,)
            ).fetchone()
            if classification is None:
                raise ComparisonError("Feature not found")
            membership = self.map_membership(db, feature_id)
            records = []
            for side in ("baseline", "target"):
                row = db.execute(
                    f"SELECT metadata_offset FROM {side} WHERE id=?", (feature_id,)
                ).fetchone()
                records.append(
                    self.detail_record(side, feature_id, row[0], started=started)
                    if row
                    else None
                )
        return {
            "feature_id": feature_id,
            "classification": classification[0],
            "before": records[0],
            "after": records[1],
            "property_changes": [
                c
                for c in property_changes(*records)
                if c["field"] not in self.summary["property_hash_exclusions"]
            ],
            "excluded_property_changes": [
                c
                for c in property_changes(*records)
                if c["field"] in self.summary["property_hash_exclusions"]
            ],
            **membership,
        }

    def export(self, path: Path):
        if self.summary is None:
            raise ComparisonError("Comparison is incomplete")
        with path.open("w") as handle:
            handle.write(
                '{"summary":' + model.canonical_json(self.summary) + ',"features":['
            )
            if self.summary["identity"]["compatible"]:
                with sqlite3.connect(self.db_path) as db:
                    for index, (feature_id, classification) in enumerate(
                        db.execute(
                            "SELECT id, classification FROM changes ORDER BY id COLLATE BINARY"
                        )
                    ):
                        handle.write(
                            ("," if index else "")
                            + model.canonical_json(
                                {
                                    "feature_id": feature_id,
                                    "classification": classification,
                                }
                            )
                        )
            handle.write("]}\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for side in ("baseline", "target"):
        parser.add_argument(
            f"--{side}",
            type=Path,
            required=True,
            help="Snapshot JSON with pinned file descriptors and local_paths",
        )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path)
    for name, value in asdict(Limits()).items():
        parser.add_argument("--" + name.replace("_", "-"), type=int, default=value)
    args = parser.parse_args(argv)
    limits = Limits(**{name: getattr(args, name) for name in asdict(Limits())})
    if any(value <= 0 for value in asdict(limits).values()):
        parser.error("All resource limits must be positive")
    directory = args.work_dir or work_root() / "comparisons" / (
        "cli-" + str(time.time_ns())
    )
    try:
        inputs = [
            read_json(getattr(args, side).read_text())
            for side in ("baseline", "target")
        ]
        paths = [
            {
                role: Path(raw["local_paths"][role])
                for role in ("metadata", "schema", "manifest", "fgb")
                if role != "fgb" or role in raw["local_paths"]
            }
            for raw in inputs
        ]
        comparison = Comparison(
            directory,
            limits=limits,
            progress=lambda phase, rows: print(
                f"{phase}: {rows} rows", file=sys.stderr
            ),
        )
        comparison.run(*inputs, *paths)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        comparison.export(args.output)
        print(f"Report: {args.output}; retained workspace: {directory}")
        return 0
    except (
        ComparisonError,
        model.ReleaseFeatureModelError,
        OSError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
