"""Versioned source equality, independent of retrieval and publication dates.

A fingerprint is evidence of inputs, never publication completion or permission.
Only a successful, committed run can be its comparison baseline.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ingestion.common import release_index
from scripts.release_feature_model import normalize_geometry_exact as normalized_geometry


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def unordered_digest(values: Iterable[Any], *, workdir: Path | None = None) -> str:
    """Hash a multiset: duplicates count; record and object-key order do not.

    Large WDPA language tables sort fixed-size hashes on disk with bounded cache.
    Both paths have identical serialization, including for empty inputs.
    """
    hashes = (bytes.fromhex(digest(value)) for value in values)
    result = hashlib.sha256(b"source-multiset-v1\0")
    if workdir is None:
        for value in sorted(hashes):
            result.update(value)
    else:
        with tempfile.TemporaryDirectory(
            prefix="fingerprint-", dir=workdir
        ) as directory:
            with closing(
                sqlite3.connect(Path(directory) / "hashes.sqlite")
            ) as database:
                database.execute("PRAGMA cache_size=-8192")
                database.execute("PRAGMA temp_store=FILE")
                database.execute("CREATE TABLE hashes (value BLOB NOT NULL)")
                database.executemany(
                    "INSERT INTO hashes VALUES (?)", ((value,) for value in hashes)
                )
                database.commit()
                for (value,) in database.execute(
                    "SELECT value FROM hashes ORDER BY value"
                ):
                    result.update(value)
    return result.hexdigest()


def feature_digest(features: Iterable[Mapping[str, Any]]) -> str:
    # Source properties are deliberately not passed through properties_hash:
    # its generated-column exclusions are inappropriate for arbitrary sources.
    return unordered_digest(
        {
            "geometry": normalized_geometry(row.get("geometry")),
            "properties": row["properties"],
        }
        for row in features
    )


def csv_digest(path: Path) -> str:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        if not fields or not all(fields) or len(set(fields)) != len(fields):
            raise ValueError("fingerprint CSV requires distinct named columns")

        def checked_rows():
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("fingerprint CSV row differs from its header")
                yield row

        rows_hash = unordered_digest(checked_rows(), workdir=path.parent)
    return digest({"fields": sorted(fields), "rows": rows_hash})


@dataclass(frozen=True, init=False)
class Fingerprint:
    _json: str

    def __init__(self, definition: str, inputs: Mapping[str, Any]):
        # One immutable representation owns both the hash and optional evidence.
        object.__setattr__(
            self,
            "_json",
            json.dumps(
                {"version": 1, "definition": definition, "inputs": dict(inputs)},
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ),
        )

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self._json)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self._json.encode()).hexdigest()

    def record_fields(self) -> dict[str, Any]:
        return {
            "source_fingerprint": self.payload,
            "source_fingerprint_hash": self.sha256,
        }

    def matches(self, record: Mapping[str, Any] | None) -> bool:
        return bool(
            record
            and record.get("status") == "success"
            and record.get("source_fingerprint_hash") == self.sha256
        )


def latest_success_record(publisher, asset, *, exclude_run_date=None):
    """Resolve latest_release, never latest_run (which may be a skip)."""
    index = publisher.load_json(release_index.release_index_object(asset.slug))
    if not index or not index.get("latest_release"):
        return None
    latest = index["latest_release"]
    uri = latest.get("run_record_path")
    if not uri:
        return None  # historical indexes without a run pointer need a refresh
    bucket, name = release_index.split_gs_uri(uri)
    if bucket != publisher.bucket.name or not name.startswith(asset.runs_prefix):
        raise ValueError(
            "latest publication run record is outside the asset's run prefix"
        )
    if exclude_run_date and name == asset.run_record_object(exclude_run_date):
        return None
    record = publisher.load_json(name)
    if not record or record.get("status") != "success":
        raise ValueError("latest release does not reference a successful publication")
    if record.get("asset_slug", asset.slug) != asset.slug:
        raise ValueError("latest release points at a different asset")
    return record
