"""Equality of reviewed WDPA inputs; never a source of publish authorization."""

from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

from ingestion.common.source_fingerprint import Fingerprint, unordered_digest
from ingestion.wdpa_monthly.translations import FIELDS, LOCALES


def rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def retained_fingerprint(paths, facts, source_fields, workdir: Path):
    # geometry_hash comes from the accepted producer's validated canonical
    # sidecar. Generated IDs, release dates and retrieval provenance are excluded.
    identities = {}

    def canonical_rows():
        for row in rows(paths["metadata"]):
            key = row["identity_key"]
            if row["feature_id"] in identities:
                raise ValueError("duplicate retained WDPA feature identity")
            identities[row["feature_id"]] = key
            yield {
                "geometry_hash": row["geometry_hash"],
                "properties": row["properties"],
                "identity_key": key,
            }

    canonical = unordered_digest(canonical_rows(), workdir=workdir)
    localized = {
        locale: unordered_digest(
            (
                {
                    "geometry_hash": row["geometry_hash"],
                    "properties": row["properties"],
                    "identity_key": row["identity_key"],
                }
                for row in rows(paths[f"metadata_{locale}"])
            ),
            workdir=workdir,
        )
        for locale in LOCALES
    }

    def translation_rows():
        with paths["metadata_translations"].open(
            encoding="utf-8-sig", newline=""
        ) as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                # Orphans remain meaningful translation-maintenance evidence.
                feature_id = row.pop("feature_id")
                yield {
                    "identity_key": identities.get(feature_id, ["orphan", feature_id]),
                    **row,
                }

    schema = json.loads(paths["schema"].read_text())
    return Fingerprint(
        "wdpa-retained-content-v1",
        {
            "canonical": canonical,
            "localized": localized,
            "translation_source": unordered_digest(translation_rows(), workdir=workdir),
            "fields": sorted(source_fields, key=lambda field: field["name"]),
            "schema_fields": sorted(schema["fields"], key=lambda field: field["name"]),
            "translation_fields": sorted(FIELDS),
            "translation_locales": sorted(LOCALES),
            "identity_contract": facts["identity_contract"],
            "identity_decisions": facts["identity_decisions"],
        },
    )
