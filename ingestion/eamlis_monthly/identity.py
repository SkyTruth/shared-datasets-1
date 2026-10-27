"""Typed EAMLIS source keys and permanent decimal-ID allocations.

Co-located problems are separate entities. Geometry, NUMBER, names and costs
never reconstruct keys. Absent keys remain allocated so reappearance is stable.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Iterable, Mapping

from scripts import release_feature_model as model

CONTRACT = "eamlis-problem-key-v1"
KIND_FIELD = "eamlis_record_kind"
KEY_FIELD = "eamlis_identity_key"
ASSIGNMENT_FIELDS = (KIND_FIELD, KEY_FIELD)
EXCLUDED_PROPERTIES = ("OBJECTID",)
# Explicitly empty problem-side columns are required to classify an area row.
PROBLEM_FIELDS = (
    "NUMBER", "PROB_TY_CD", "PROB_TY_NAME", "PROGRAM", "PRIORITY", "STATUS0",
    "RECLAMATION", "FUND_SRC", "TOTAL_COST", "TOTAL_UNITS",
    "UNFD_COST", "UNFD_UNITS", "UNFD_METERS", "UNFD_GPRA",
    "FUND_COST", "FUND_UNITS", "FUND_METERS", "FUND_GPRA",
    "COMP_COST", "COMP_UNITS", "COMP_METERS", "COMP_GPRA",
)


class IdentityError(RuntimeError):
    """Ambiguous provider input or invalid persisted identity state."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise IdentityError(message)


def exact_key(value: Any, field: str) -> str:
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9]{1,64}", value) is not None,
            f"{field} must be an exact nonempty alphanumeric source string")
    return value


@dataclass(frozen=True)
class SourceIdentity:
    kind: str
    source_key: str
    area_key: str

    def __post_init__(self):
        require(isinstance(self.kind, str) and self.kind in {"problem", "area"}, "invalid EAMLIS record kind")
        exact_key(self.source_key, "source key")
        exact_key(self.area_key, "AMLIS_KEY")
        require(self.kind != "area" or self.source_key == self.area_key, "area identity must use its exact AMLIS_KEY")

    @property
    def key(self) -> tuple[str, str]:
        return self.kind, self.source_key


def classify(properties: Mapping[str, Any]) -> SourceIdentity:
    require("PROBLEM_KEY" in properties and "AMLIS_KEY" in properties,
            "source must explicitly supply PROBLEM_KEY and AMLIS_KEY; historical inference is forbidden")
    area = properties["AMLIS_KEY"]
    problem = properties["PROBLEM_KEY"]
    if problem is not None and problem != "":
        return SourceIdentity("problem", problem, area)
    missing = set(PROBLEM_FIELDS) - properties.keys()
    require(not missing, "area-only classification lacks problem columns: " + ", ".join(sorted(missing)))
    populated = [name for name in PROBLEM_FIELDS if properties[name] is not None and properties[name] != ""]
    require(not populated, "missing PROBLEM_KEY on a populated problem: " + ", ".join(populated))
    return SourceIdentity("area", area, area)


def classify_release(properties: Iterable[Mapping[str, Any]]) -> tuple[SourceIdentity, ...]:
    rows = tuple(classify(row) for row in properties)
    require(bool(rows), "empty source cannot allocate identity state")
    keys = [row.key for row in rows]
    require(len(keys) == len(set(keys)), "duplicate EAMLIS typed source key, including identical duplicate rows")
    areas = {row.area_key for row in rows if row.kind == "area"}
    require(not areas.intersection(row.area_key for row in rows if row.kind == "problem"),
            "an area-only row coexists with problem rows for the same area; source review required")
    return rows


@dataclass(frozen=True)
class Ledger:
    """Reserve returns a replacement; existing assignments cannot be edited."""
    entries: tuple[tuple[str, str, str, str], ...]  # kind, key, area, decimal ID
    next_feature_id: int

    def __post_init__(self) -> None:
        require(type(self.next_feature_id) is int and 1 <= self.next_feature_id <= 10**64,
                "invalid EAMLIS allocation high-water")
        require(isinstance(self.entries, tuple), "ledger entries must be an immutable tuple")
        keys = set()
        for ordinal, entry in enumerate(self.entries, 1):
            require(isinstance(entry, tuple) and len(entry) == 4, "invalid ledger entry")
            kind, key, area, feature_id = entry
            row = SourceIdentity(kind, key, area)
            require(feature_id == str(ordinal), "ledger allocations must be ordered, contiguous decimal strings")
            require(row.key not in keys, "duplicate ledger key")
            keys.add(row.key)
        require(self.next_feature_id == len(self.entries) + 1, "ledger high-water differs from permanent assignments")

    @classmethod
    def genesis(cls) -> Ledger:
        """Offline/review preparation only; runtime never infers genesis."""
        return cls((), 1)

    @classmethod
    def parse(cls, value: Any) -> Ledger:
        require(isinstance(value, dict) and set(value) == {"schema_version", "contract_id", "next_feature_id", "entries"}
                and type(value["schema_version"]) is int and value["schema_version"] == 1
                and value["contract_id"] == CONTRACT and isinstance(value["entries"], list), "invalid EAMLIS identity ledger")
        entries = []
        for row in value["entries"]:
            require(isinstance(row, dict) and set(row) == {"kind", "source_key", "area_key", "feature_id"}, "invalid ledger entry fields")
            entries.append(tuple(row[name] for name in ("kind", "source_key", "area_key", "feature_id")))
        return cls(tuple(entries), value["next_feature_id"])

    @property
    def value(self) -> dict[str, Any]:
        return {"schema_version": 1, "contract_id": CONTRACT, "next_feature_id": self.next_feature_id,
                "entries": [dict(zip(("kind", "source_key", "area_key", "feature_id"), row)) for row in self.entries]}

    @property
    def ids(self) -> dict[tuple[str, str], str]:
        return {(kind, key): feature_id for kind, key, _area, feature_id in self.entries}

    def reserve(self, rows: tuple[SourceIdentity, ...]) -> Ledger:
        require(bool(rows) and len({row.key for row in rows}) == len(rows), "allocation requires a nonempty unique release")
        previous = {row[:2]: row for row in self.entries}
        for row in rows:
            if old := previous.get(row.key):
                require(old[2] == row.area_key, "PROBLEM_KEY moved to another AMLIS_KEY; possible key reuse requires review")
        baseline = model.GeneratedIdentityBaseline(
            tuple({"identity_key": row[:2], "feature_id": row[3]} for row in self.entries),
            self.next_feature_id, "ledger" if self.entries else None, contract_id=CONTRACT)
        allocation = model.assign_generated_feature_ids(sorted(row.key for row in rows), baseline=baseline)
        added = sorted((row for row in rows if row.key not in previous), key=lambda row: row.key)
        entries = self.entries + tuple((row.kind, row.source_key, row.area_key, allocation.ids_by_key[row.key]) for row in added)
        return Ledger(entries, allocation.next_feature_id)

    def require_extension_of(self, previous: Ledger) -> None:
        require(self.next_feature_id >= previous.next_feature_id and self.entries[:len(previous.entries)] == previous.entries,
                "identity state cannot remove, renumber, or rebind an existing allocation")


def enrich(features, *, ledger: Ledger, release: str, asset_slug: str, provenance: Mapping[str, Any]):
    from ingestion.common import feature_metadata
    features = tuple(features)
    rows = classify_release(feature["properties"] for feature in features)
    require(ledger.reserve(rows) == ledger, "source identities have not been durably reserved")
    assignments = ledger.ids
    enriched, records = [], []
    for feature, row in zip(features, rows):
        original = feature["properties"]
        require(not set(ASSIGNMENT_FIELDS).intersection(original), "upstream source collides with derived EAMLIS identity columns")
        properties = {**original, KIND_FIELD: row.kind, KEY_FIELD: row.source_key}
        item, record = feature_metadata.feature_record(
            asset_slug=asset_slug, release=release, feature_id=assignments[row.key], geometry=feature["geometry"],
            source_properties=properties, provenance=provenance, identity_key=row.key,
            identity_excluded_properties=EXCLUDED_PROPERTIES)
        enriched.append(item)
        records.append(record)
    return enriched, records


def metadata(*, previous_release: str | None, before: int, after: int) -> dict[str, Any]:
    return model.build_identity_metadata(strategy="generated_sequence_source_fields", source_fields=ASSIGNMENT_FIELDS,
        properties_hash_excluded_properties=EXCLUDED_PROPERTIES, contract_id=CONTRACT, previous_release=previous_release,
        next_generated_feature_id_before_release=before, next_generated_feature_id_after_release=after)


def encoded(ledger: Ledger) -> bytes:
    return (json.dumps(ledger.value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
