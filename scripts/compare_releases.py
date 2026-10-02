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
RESULT_VERSION = 2


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
    max_seconds: int = 120


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
    return {"asset_slug": slug, "release": release, "files": files}


def identity_compatibility(
    before: dict, after: dict, old_fields: dict, new_fields: dict
) -> dict:
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

    def check(self):
        if self.cancelled():
            raise ComparisonCancelled("Comparison cancelled")
        if time.monotonic() - self.started > self.limits.max_seconds:
            raise ComparisonLimit(
                "Comparison time budget exceeded; use the local CLI with a larger explicit budget"
            )
        if (
            sum(p.stat().st_size for p in self.directory.iterdir() if p.is_file())
            > self.limits.max_disk_bytes
        ):
            raise ComparisonLimit("Comparison disk budget exceeded; use the local CLI")

    def verify_bytes(self, path: Path, ref: dict):
        self.check()
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

    def load(self, db, side: str, ref: dict, paths: dict):
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
        fields = model.validate_release_schema(
            schema,
            expected_asset_slug=ref["asset_slug"],
            expected_release=ref["release"],
        )
        # Legacy identity evidence is a capability failure, never permission to guess IDs.
        artifacts = model.validate_release_manifest(
            manifest,
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
                if role in paths:
                    self.verify_bytes(paths[role], evidence)
        db.execute(
            f"CREATE TABLE {side} (id TEXT PRIMARY KEY, identity_key TEXT UNIQUE, geometry TEXT, properties_hash TEXT, record TEXT, search TEXT)"
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
        count, expanded = 0, 0
        with gzip.open(paths["metadata"], "rb") as handle:
            while True:
                self.check()
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
                record = read_json(line)
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
                    raise ComparisonError(
                        "Record has properties outside the release schema"
                    )
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
                    if not model.GENERATED_FEATURE_ID_RE.fullmatch(
                        record["feature_id"]
                    ):
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
                key = model.canonical_json(key_value)
                try:
                    db.execute(
                        f"INSERT INTO {side} VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            record["feature_id"],
                            key,
                            record["geometry_hash"],
                            record["properties_hash"],
                            model.canonical_json(record),
                            model.canonical_json(props),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ComparisonError(
                        "Duplicate feature_id or record identity key"
                    ) from exc
                count += 1
                if count > self.limits.max_rows:
                    raise ComparisonLimit("Sidecar exceeds max_rows; use the local CLI")
                if count % 500 == 0:
                    self.progress(side, count)
        if assertions["feature_count"] != count:
            raise ComparisonError(
                "Manifest feature_count differs from the complete sidecar"
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

    @contextmanager
    def checked_connection(self):
        interrupted = []

        def check_query():
            try:
                self.check()
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
        self, baseline: dict, target: dict, baseline_paths: dict, target_paths: dict
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
            a, af, ac = self.load(db, "baseline", baseline, baseline_paths)
            b, bf, bc = self.load(db, "target", target, target_paths)
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
                      WHEN a.properties_hash=b.properties_hash THEN 'geometry_only' ELSE 'both' END AS classification,
                      a.search || coalesce(b.search, '') AS search
                    FROM baseline a LEFT JOIN target b ON a.id=b.id
                    UNION ALL SELECT b.id, 'added', b.search FROM target b LEFT JOIN baseline a ON a.id=b.id WHERE a.id IS NULL""")
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
            "method": "Complete canonical sidecars; match feature_id; compare geometry_hash/properties_hash. No spatial matching or tile-derived counts. Provenance and localization excluded.",
            "map_method": "Exact geometry_hash set union; shared geometry with differing sets of source properties_hash is yellow. Geometry membership is independent of feature identity. All loaded map features are colored through bounded per-release lookups, independently of table pagination.",
            "property_hash_exclusions": sorted(
                model.HASH_EXCLUDED_PROPERTIES
                | set(a["identity"].get("properties_hash_excluded_properties", []))
            )
            if compatibility["compatible"]
            else None,
            "publication": {
                "baseline": a.get("source_inputs", []),
                "target": b.get("source_inputs", []),
            },
        }
        return self.summary

    def map_features(self, side: str, feature_ids: list[str]) -> list[dict]:
        """Resolve geometry colors within one release; never join IDs across releases."""
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
            model.validate_feature_id(feature_id)
        if len(set(feature_ids)) != len(feature_ids):
            raise ComparisonError("Map feature IDs must be unique")
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute(
                f"SELECT r.id, g.color FROM {side} r JOIN geometry_display g ON r.geometry=g.geometry WHERE r.id IN ({','.join('?' for _ in feature_ids)}) ORDER BY r.id COLLATE BINARY",
                feature_ids,
            ).fetchall()
        if len(rows) != len(feature_ids):
            raise ComparisonError(
                "Display feature is absent from the complete selected release input"
            )
        return [
            {"feature_id": feature_id, "change": change} for feature_id, change in rows
        ]

    def page(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        query: str = "",
        classification: str = "",
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
        ):
            raise ComparisonError("Invalid page options")
        where, args = (
            "WHERE (instr(lower(id), lower(?)) > 0 OR instr(lower(search), lower(?)) > 0)",
            [query, query],
        )
        if classification:
            where += " AND classification=?"
            args.append(classification)
        with sqlite3.connect(self.db_path) as db:
            total = db.execute(
                f"SELECT count(*) FROM changes {where}", args
            ).fetchone()[0]
            rows = [
                {"feature_id": i, "classification": c, **self.map_membership(db, i)}
                for i, c in db.execute(
                    f"SELECT id, classification FROM changes {where} ORDER BY id COLLATE BINARY LIMIT ? OFFSET ?",
                    [*args, limit, offset],
                )
            ]
        return {"rows": rows, "total": total, "offset": offset, "limit": limit}

    def map_membership(self, db, feature_id):
        result = {}
        for side in ("baseline", "target"):
            row = db.execute(
                f"SELECT r.geometry, g.color FROM {side} r JOIN geometry_display g ON r.geometry=g.geometry WHERE r.id=?",
                (feature_id,),
            ).fetchone()
            result["map_before" if side == "baseline" else "map_after"] = (
                {"geometry_hash": row[0], "change": row[1]} if row else None
            )
        return result

    def inspect(self, feature_id: str) -> dict:
        model.validate_feature_id(feature_id)
        if not self.summary or not self.summary["identity"]["compatible"]:
            raise ComparisonError("Authoritative feature inspection is unavailable")
        with sqlite3.connect(self.db_path) as db:
            classification = db.execute(
                "SELECT classification FROM changes WHERE id=?", (feature_id,)
            ).fetchone()
            if classification is None:
                raise ComparisonError("Feature not found")
            membership = self.map_membership(db, feature_id)
            records = []
            for side in ("baseline", "target"):
                row = db.execute(
                    f"SELECT record FROM {side} WHERE id=?", (feature_id,)
                ).fetchone()
                records.append(json.loads(row[0]) if row else None)
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
                for role in ("metadata", "schema", "manifest")
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
