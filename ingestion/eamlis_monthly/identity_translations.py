"""Re-key only hash-bound translations from one pinned concrete legacy release."""
from __future__ import annotations

import csv
from dataclasses import asdict
import gzip
import json
import logging

from ingestion.eamlis_monthly import identity, identity_state as state, source_quality as quality
from scripts import feature_metadata_localization as localization
from scripts import release_feature_model as model


def remap(ledger, records, translation_source, destination, *, fields, locales, provenance):
    records = tuple(records)
    identity.require(model.validate_sidecar_records(records).valid, "invalid legacy canonical metadata")
    keys = identity.classify_release(record["properties"] for record in records)
    identity.require(ledger.reserve(keys) == ledger, "legacy release is missing from audited ledger")
    old = {record["feature_id"]: (record, key) for record, key in zip(records, keys)}
    assignments = ledger.ids
    report = {"matched": 0, "archived_unmapped": 0, "source": provenance}
    columns = (*localization.REQUIRED_TRANSLATION_COLUMNS, *localization.OPTIONAL_TRANSLATION_COLUMNS)
    with destination.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in localization.iter_translation_source(translation_source, translatable_fields=set(fields)):
            match = old.get(row.feature_id)
            if match is None or row.locale not in locales or row.field not in match[0]["properties"] or row.source_value_hash != localization.source_value_hash(match[0]["properties"][row.field]):
                report["archived_unmapped"] += 1
                continue
            value = asdict(row)
            value.pop("row_number")
            value["feature_id"] = assignments[match[1].key]
            value["notes"] = (row.notes + f"; EAMLIS identity cutover: {provenance}; legacy_feature_id={row.feature_id}").lstrip("; ")
            writer.writerow(value)
            report["matched"] += 1
    return report


def prepare_translation_source(guard, destination, *, fields, locales):
    from ingestion.eamlis_monthly.identity_installation import read_marker
    marker = read_marker(guard.bucket)
    metadata_ref, csv_ref = marker["metadata_source"], marker["translation_source"]
    metadata = quality.pinned(guard.bucket.client, metadata_ref)
    source = destination.with_name("legacy-translations.csv")
    source.write_bytes(quality.pinned(guard.bucket.client, csv_ref))
    records = (json.loads(line) for line in gzip.decompress(metadata).splitlines())
    ledger = state.ledger_for(guard, guard.state["identity"]["ledger"])
    report = remap(ledger, records, source, destination, fields=fields, locales=locales,
                  provenance=f"{metadata_ref['path']}#{metadata_ref['generation']}")
    destination.with_name("identity-translation-migration.json").write_bytes(quality.encode(report))
    logging.getLogger(__name__).info("identity translation migration: %s", report)
    return destination, [{"uri": ref["path"], **{k: ref[k] for k in ("generation", "size", "sha256")},
                          "identity_translation_migration": report}
                         for ref in (metadata_ref, csv_ref)]
