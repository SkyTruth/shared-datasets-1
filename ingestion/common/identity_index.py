"""Disposable SQLite indexes for verified identities and release planning.

These files are scratch, never allocation authority. The generation-bound
baseline and publication owner remain the authority across executions.
"""

from __future__ import annotations

import json
import hashlib
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from contextlib import contextmanager
import tempfile
from pathlib import Path

from scripts import release_feature_model as model


def baseline_digest(baseline, *, match_properties=True):
    """Bind retained matches to the complete baseline and matching policy."""
    digest = hashlib.sha256()
    digest.update(model.canonical_json({
        "contract": baseline.contract_id, "next": baseline.next_feature_id,
        "release": baseline.release,
        "snapshot": asdict(baseline.snapshot) if baseline.snapshot else None,
        "match_properties": match_properties,
    }).encode())
    for record in baseline.records:
        digest.update(model.canonical_json(dict(record)).encode() + b"\n")
    return digest.hexdigest()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA cache_size=-65536")
    db.execute("PRAGMA temp_store=FILE")
    db.execute("PRAGMA mmap_size=0")
    # Only complete, checksummed analysis snapshots may be reopened. A killed
    # preparation has no manifest and must never be reused.
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    return db


def key_text(key) -> str:
    return model.canonical_json(list(key))


class IdentityMapping(Mapping):
    def __init__(self, records):
        self.records = records

    def __getitem__(self, key):
        row = self.records.db.execute(
            "SELECT feature_id FROM records WHERE identity_key=?", (key_text(key),)
        ).fetchone()
        if row is None:
            raise KeyError(key)
        return row[0]

    def __iter__(self):
        for row in self.records.db.execute(
            "SELECT identity_key FROM records ORDER BY ordinal"
        ):
            yield tuple(json.loads(row[0]))

    def __len__(self):
        return len(self.records)


class DiskIdentityRecords(model.IdentityRecordIndex):
    def __init__(self, path: Path, *, exclude_properties=()):
        self.path = path
        self._excluded_properties = tuple(exclude_properties)
        self.db = connect(path)
        self.db.executescript("""
            CREATE TABLE records (
                ordinal INTEGER PRIMARY KEY, identity_key TEXT NOT NULL UNIQUE,
                feature_id TEXT NOT NULL UNIQUE, geometry_hash TEXT, properties_hash TEXT,
                source_identity_key TEXT NOT NULL UNIQUE
            );
            CREATE INDEX by_geometry ON records(geometry_hash);
            CREATE INDEX by_properties ON records(properties_hash);
        """)
        self._sealed = False

    @property
    def excluded_properties(self):
        return self._excluded_properties

    def add(self, record):
        if self._sealed:
            raise RuntimeError("verified identity index is immutable")
        source_key = model.identity_key_from_record(record)
        record = next(
            model.project_identity_records(
                (record,), exclude_properties=self.excluded_properties
            )
        )
        feature_id = str(record.get("feature_id") or "").strip()
        model.validate_feature_id(feature_id)
        key = model.identity_key_from_record(record)
        try:
            self.db.execute(
                "INSERT INTO records(identity_key,feature_id,geometry_hash,properties_hash,source_identity_key) VALUES (?,?,?,?,?)",
                (
                    key_text(key),
                    feature_id,
                    record.get("geometry_hash"),
                    record.get("properties_hash"),
                    key_text(source_key),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise model.ReleaseFeatureModelError(
                "duplicate feature IDs or identity keys in identity index"
            ) from exc

    def seal(self):
        self.db.commit()
        self.db.execute("PRAGMA query_only=ON")
        self._sealed = True
        return self

    def mapping(self):
        return IdentityMapping(self)

    def __len__(self):
        return self.db.execute("SELECT count(*) FROM records").fetchone()[0]

    def __iter__(self):
        for ordinal, key, feature_id, geometry_hash, properties_hash in self.db.execute(
            "SELECT ordinal,identity_key,feature_id,geometry_hash,properties_hash FROM records ORDER BY ordinal"
        ):
            yield {
                "identity_key": tuple(json.loads(key)),
                "feature_id": feature_id,
                "geometry_hash": geometry_hash,
                "properties_hash": properties_hash,
            }

    def validate_baseline(self, next_feature_id):
        if not self._sealed:
            raise model.ReleaseFeatureModelError(
                "identity index must be sealed before becoming a baseline"
            )
        for (feature_id,) in self.db.execute("SELECT feature_id FROM records"):
            if not model.GENERATED_FEATURE_ID_RE.fullmatch(feature_id):
                raise model.ReleaseFeatureModelError(
                    "generated baseline IDs must be canonical positive decimal strings"
                )
            if int(feature_id) >= next_feature_id:
                raise model.ReleaseFeatureModelError(
                    "next generated feature ID must exceed every previous allocation"
                )

    def matches(self, column, value):
        if column not in {"geometry_hash", "properties_hash"}:
            raise ValueError("unsupported identity match column")
        other = "properties_hash" if column == "geometry_hash" else "geometry_hash"
        return self.db.execute(
            f"SELECT feature_id,{other} FROM records WHERE {column}=?", (value,)
        ).fetchall()

    def geometry_for_key(self, key):
        row = self.db.execute(
            "SELECT geometry_hash FROM records WHERE identity_key=?", (key_text(key),)
        ).fetchone()
        return row[0] if row else None

    def close(self):
        self.db.close()


class DiskAmbiguities(Sequence):
    def __init__(self, db, *, unresolved=False):
        self.db = db
        self.where = " WHERE resolved=0" if unresolved else ""

    def __len__(self):
        return self.db.execute(
            "SELECT count(*) FROM ambiguities" + self.where
        ).fetchone()[0]

    @staticmethod
    def decode(payload):
        raw = json.loads(payload)
        for name in raw:
            if name == "identity_key" or name.startswith("matching_"):
                raw[name] = tuple(raw[name])
        return model.IdentityAmbiguity(**raw)

    def __iter__(self):
        for (payload,) in self.db.execute(
            "SELECT payload FROM ambiguities" + self.where + " ORDER BY ordinal"
        ):
            yield self.decode(payload)

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step != 1:
                return tuple(self[i] for i in range(start, stop, step))
            return tuple(
                self.decode(row[0])
                for row in self.db.execute(
                    "SELECT payload FROM ambiguities"
                    + self.where
                    + " ORDER BY ordinal LIMIT ? OFFSET ?",
                    (stop - start, start),
                )
            )
        if index < 0:
            index += len(self)
        row = self.db.execute(
            "SELECT payload FROM ambiguities"
            + self.where
            + " ORDER BY ordinal LIMIT 1 OFFSET ?",
            (index,),
        ).fetchone()
        if index < 0 or row is None:
            raise IndexError(index)
        return self.decode(row[0])


class DiskIdentityPlan:
    def __init__(self, path: Path):
        self._resolved = False
        self.db = connect(path)
        self.db.executescript("""
            CREATE TABLE analysis(binding TEXT NOT NULL, corroborated INTEGER NOT NULL);
            CREATE TABLE planned(ordinal INTEGER PRIMARY KEY, identity_key TEXT NOT NULL UNIQUE,
                geometry_hash TEXT NOT NULL, properties_hash TEXT NOT NULL, feature_id TEXT UNIQUE);
            CREATE TABLE duplicates(identity_key TEXT NOT NULL, ordinal INTEGER NOT NULL);
            CREATE INDEX duplicate_keys ON duplicates(identity_key);
            CREATE TABLE ambiguities(ordinal INTEGER PRIMARY KEY, identity_key TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0);
        """)

    @classmethod
    def open(cls, path: Path):
        """Open a hash-verified retained plan; callers own the artifact binding."""
        plan = cls.__new__(cls)
        plan.db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=rw", uri=True)
        plan._resolved = plan.db.execute(
            "SELECT count(*) FROM planned WHERE feature_id IS NOT NULL"
        ).fetchone()[0] > 0
        return plan

    def add(self, ordinal, feature, *, source_fields, excluded_properties=()):
        if self._resolved or self.db.execute("SELECT count(*) FROM analysis").fetchone()[0]:
            raise RuntimeError("analyzed identity input is immutable")
        geometry_hash, properties_hash = model.content_hashes(
            geometry=feature.get("geometry"),
            properties=feature.get("properties") or {},
            exclude_properties=excluded_properties,
        )
        key = (
            model.source_fields_identity_key(
                feature.get("properties") or {}, source_fields
            )
            if source_fields
            else model.content_identity_key(
                geometry_hash_value=geometry_hash, properties_hash_value=properties_hash
            )
        )
        previous = self.db.execute(
            "SELECT ordinal,geometry_hash,properties_hash FROM planned WHERE identity_key=?",
            (key_text(key),),
        ).fetchone()
        if previous:
            if previous[1:] != (geometry_hash, properties_hash):
                raise RuntimeError(
                    f"duplicate generated identity key with different content: rows {previous[0]} and {ordinal}"
                )
            self.db.execute(
                "INSERT INTO duplicates VALUES (?,?)", (key_text(key), ordinal)
            )
            return False
        self.db.execute(
            "INSERT INTO planned VALUES (?,?,?,?,NULL)",
            (ordinal, key_text(key), geometry_hash, properties_hash),
        )
        return True

    @contextmanager
    def baseline_records(self, baseline):
        if isinstance(baseline.records, DiskIdentityRecords):
            yield baseline.records
            return
        directory = Path(self.db.execute("PRAGMA database_list").fetchone()[2]).parent
        with tempfile.TemporaryDirectory(prefix="baseline-", dir=directory) as temporary:
            records = DiskIdentityRecords(Path(temporary) / "records.sqlite")
            try:
                for record in baseline.records:
                    records.add(record)
                yield records.seal()
            finally:
                records.close()

    def discover(self, *, baseline, match_properties=True):
        """Match the entire input without allocating IDs or sending notifications."""
        binding = baseline_digest(baseline, match_properties=match_properties)
        retained = self.db.execute("SELECT binding FROM analysis").fetchone()
        if retained:
            if retained[0] != binding:
                raise model.ReleaseFeatureModelError("retained identity analysis baseline or policy changed")
            return DiskAmbiguities(self.db)
        with self.baseline_records(baseline) as records:
            corroborated = 0
            for ordinal, key, geometry_hash, properties_hash, _ in self.db.execute(
                "SELECT * FROM planned ORDER BY ordinal"
            ):
                decoded_key = tuple(json.loads(key))
                ambiguity, settled = model.classify_identity_matches(
                    {
                        "identity_key": decoded_key,
                        "geometry_hash": geometry_hash,
                        "properties_hash": properties_hash,
                    },
                    geometry_records=records.matches("geometry_hash", geometry_hash),
                    properties_records=records.matches(
                        "properties_hash", properties_hash
                    )
                    if match_properties
                    else (),
                    baseline_geometry=records.geometry_for_key(decoded_key),
                    match_properties=match_properties,
                )
                corroborated += int(settled)
                if ambiguity:
                    self.db.execute(
                        "INSERT INTO ambiguities(ordinal,identity_key,payload) VALUES (?,?,?)",
                        (ordinal, key, model.canonical_json(asdict(ambiguity))),
                    )
            self.db.execute("INSERT INTO analysis VALUES (?,?)", (binding, corroborated))
            self.db.commit()
        return DiskAmbiguities(self.db)

    def review(self, *, baseline, release, decisions=(), match_properties=True):
        """Validate exact decisions against complete matches, without allocation."""
        if self._resolved:
            raise RuntimeError("identity allocation has already completed")
        self.discover(baseline=baseline, match_properties=match_properties)
        self.db.execute("UPDATE ambiguities SET resolved=0")
        with self.baseline_records(baseline) as records:
            previous = records.mapping()
            resolutions = []
            reuse_targets = set()
            for decision in decisions:
                key = model.normalize_identity_key(
                    decision.get("new_identity_key"), label="new_identity_key"
                )
                row = self.db.execute(
                    "SELECT payload,resolved FROM ambiguities WHERE identity_key=?",
                    (key_text(key),),
                ).fetchone()
                if row is None:
                    raise model.ReleaseFeatureModelError(
                        f"stale feature identity resolution does not match a current ambiguity: {key}"
                    )
                if row[1]:
                    raise model.ReleaseFeatureModelError(
                        f"duplicate feature identity resolution for ambiguity: {key}"
                    )
                resolution = model.validate_identity_resolutions(
                    release=release,
                    ambiguities=[DiskAmbiguities.decode(row[0])],
                    decisions=[decision],
                    previous_feature_ids=previous,
                )[0]
                if resolution.reuse_feature_id:
                    if resolution.reuse_feature_id in reuse_targets:
                        raise model.ReleaseFeatureModelError(
                            "multiple identity decisions cannot reuse the same previous feature ID"
                        )
                    reuse_targets.add(resolution.reuse_feature_id)
                    holder = records.db.execute(
                        "SELECT identity_key FROM records WHERE feature_id=?",
                        (resolution.reuse_feature_id,),
                    ).fetchone()
                    holder_key = tuple(json.loads(holder[0]))
                    present = self.db.execute(
                        "SELECT 1 FROM planned WHERE identity_key=?", (holder[0],)
                    ).fetchone()
                    collisions = model.check_reuse_target_collisions(
                        [resolution],
                        release_identity_keys=[holder_key] if present else (),
                        previous_feature_ids={holder_key: resolution.reuse_feature_id},
                    )
                    if collisions:
                        raise model.ReleaseFeatureModelError("; ".join(collisions))
                resolutions.append(resolution)
                self.db.execute(
                    "UPDATE ambiguities SET resolved=1 WHERE identity_key=?",
                    (key_text(key),),
                )
            self.db.commit()
            return resolutions, DiskAmbiguities(self.db, unresolved=True)

    def resolve(self, *, baseline, asset_slug, release, decisions=(), match_properties=True):
        from ingestion.common.feature_metadata import raise_unresolved_identity_ambiguities

        resolutions, unresolved = self.review(
            baseline=baseline, release=release, decisions=decisions,
            match_properties=match_properties,
        )
        raise_unresolved_identity_ambiguities(
            asset_slug=asset_slug, release=release, ambiguities=unresolved,
        )
        with self.baseline_records(baseline) as records:
            previous = records.mapping()
            overrides = model.resolved_feature_id_overrides(resolutions)
            force_new = set(model.resolved_force_new_identity_keys(resolutions))
            next_sequence = baseline.next_feature_id
            for ordinal, key in self.db.execute(
                "SELECT ordinal,identity_key FROM planned ORDER BY ordinal"
            ):
                decoded_key = tuple(json.loads(key))
                feature_id, next_sequence = model.allocate_generated_feature_id(
                    previous_feature_id=previous.get(decoded_key),
                    override=overrides.get(decoded_key),
                    force_new=decoded_key in force_new,
                    next_sequence=next_sequence,
                )
                try:
                    self.db.execute(
                        "UPDATE planned SET feature_id=? WHERE ordinal=?",
                        (feature_id, ordinal),
                    )
                except sqlite3.IntegrityError as exc:
                    raise model.ReleaseFeatureModelError(
                        "generated allocation would assign the same feature ID to multiple identities"
                    ) from exc
            self.db.commit()
            self.db.execute("PRAGMA query_only=ON")
            self._resolved = True
            return next_sequence, model.build_identity_decisions(
                ambiguities_detected=len(DiskAmbiguities(self.db)),
                key_corroborated=self.db.execute("SELECT corroborated FROM analysis").fetchone()[0],
                resolutions=resolutions,
            )

    def record(self, ordinal):
        row = self.db.execute(
            "SELECT identity_key,geometry_hash,properties_hash,feature_id FROM planned WHERE ordinal=?",
            (ordinal,),
        ).fetchone()
        if row is None or row[3] is None:
            raise RuntimeError("source ordinal has no completed identity allocation")
        return {
            "identity_key": tuple(json.loads(row[0])),
            "geometry_hash": row[1],
            "properties_hash": row[2],
            "feature_id": row[3],
            "duplicate_source_row_numbers": [
                r[0]
                for r in self.db.execute(
                    "SELECT ordinal FROM duplicates WHERE identity_key=? ORDER BY ordinal",
                    (row[0],),
                )
            ],
        }

    def __len__(self):
        return self.db.execute("SELECT count(*) FROM planned").fetchone()[0]

    def close(self):
        self.db.close()
