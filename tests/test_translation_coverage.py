"""Release coverage is a partition of eligible values, not CSV history rows."""

import csv
import json
from copy import deepcopy
from unittest import mock

import pytest

from scripts import feature_metadata_localization as loc, release_feature_model as model
from scripts import translation_local_io
from test_feature_metadata_localization import sidecar_record, write_translation_source, VALID_HASH_A


def translation(field, source, value, state="machine_translated", feature_id="1"):
    return {"feature_id": feature_id, "field": field, "locale": "es",
            "source_value_hash": loc.source_value_hash(source), "value": value, "review_state": state}


def test_coverage_counts_current_values_once_and_streams_only_actionable_debt(tmp_path):
    canonical = tmp_path / "example-asset.metadata.ndjson.gz"
    source = tmp_path / "translations.csv"
    original = sidecar_record("1", VALID_HASH_A, {
        "current": "Alpha", "changed": "New", "failed": "Oops", "missing": "Absent",
        "blank": "  ", "number": 12, "null": None,
    })
    model.write_metadata_sidecar([original], canonical)
    write_translation_source(source, [
        translation("current", "Alpha", "Alfa", "needs_review"),
        translation("current", "Earlier", "Anterior", "human_reviewed"),
        translation("changed", "Old", "Antiguo"),
        translation("changed", "Older", "Más antiguo"),
        translation("failed", "Old failure", "Anterior"),
        translation("failed", "Oops", "", "translation_failed"),
        translation("retired", "Retired", "Retirado"),
        translation("current", "Orphan", "Huérfano", feature_id="99"),
        translation("number", 12, "doce", "human_reviewed"),
    ])
    with mock.patch.object(loc, "translation_index", wraps=loc.translation_index) as index:
        reports = loc.materialize_locale_sidecars(
            canonical_sidecar=canonical, translation_source=source, output_dir=tmp_path,
            locales=["es", "fr"], translatable_fields=set(original["properties"]) - {"feature_id"},
        )
    assert index.call_count == 1  # All declared locales share the large CSV index.
    es, fr = reports
    assert es.coverage() == {
        "translatable_values": 4, "current": 1, "stale": 1, "missing": 2,
        "orphan": 1, "removed_fields": 1, "coverage": 0.25, "review_states": {"needs_review": 1},
    }
    assert (fr.current, fr.stale, fr.missing, fr.translatable_values) == (0, 0, 4, 4)
    localized = next(model.read_metadata_sidecar(tmp_path / "example-asset.metadata.es.ndjson.gz"))
    assert localized["properties"]["current"] == "Alfa"
    assert localized["properties"]["changed"] == "New"
    assert localized["properties"]["number"] == "doce"  # Explicit legacy opt-in remains supported.
    assert localized["translation"]["state"] == "partial"
    assert localized["translation"]["human_reviewed_fields"] == ["number"]
    for key in ("release", "feature_id", "geometry_hash", "properties_hash", "provenance"):
        assert localized[key] == original[key]
    with open(es.debt_path) as handle:
        debt = list(csv.DictReader(handle))
    assert [row["field"] for row in debt] == ["changed", "failed", "missing"]
    assert all(row["source_value_hash"] == loc.source_value_hash(row["source_value"]) for row in debt)
    model.validate_translation_coverage(loc.translations_payload(reports))


def test_declared_locale_with_empty_csv_and_no_eligible_values_has_null_coverage(tmp_path):
    canonical = tmp_path / "example-asset.metadata.ndjson.gz"
    source = tmp_path / "translations.csv"
    model.write_metadata_sidecar([sidecar_record("1", VALID_HASH_A, {"name": None})], canonical)
    write_translation_source(source, [])
    report, = loc.materialize_locale_sidecars(canonical_sidecar=canonical, translation_source=source,
                                            output_dir=tmp_path, locales=["es"], translatable_fields={"name"})
    assert report.coverage()["coverage"] is None
    assert report.translatable_values == 0
    model.validate_translation_coverage(loc.translations_payload([report]))
    assert next(model.read_metadata_sidecar(tmp_path / "example-asset.metadata.es.ndjson.gz"))["translation"]["state"] == "complete"


@pytest.mark.parametrize("change", [
    {"current": True}, {"stale": -1}, {"missing": 1}, {"coverage": 0},
    {"review_states": {}}, {"review_states": {"machine_translated": True}},
])
def test_manifest_rejects_inconsistent_coverage(change):
    counts = {"translatable_values": 1, "current": 1, "stale": 0, "missing": 0,
              "orphan": 0, "removed_fields": 0, "coverage": 1.0, "review_states": {"machine_translated": 1}}
    with pytest.raises(model.ReleaseFeatureModelError):
        model.validate_translation_coverage({"schema_version": 1, "locales": {"es": {**counts, **change}}})


def test_optional_record_provenance_validates_partitions_and_keeps_old_records_readable():
    original = sidecar_record("1", VALID_HASH_A, {"name": "Alpha"})
    assert model.validate_sidecar_records([original]).valid
    report = loc.LocalizationReport("es", "", "", "", ["name"])
    localized = loc.localize_record(original, rows=[], report=report)
    assert model.validate_sidecar_records([localized]).valid
    for change in ({"state": "complete"}, {"translated_fields": ["name"]},
                   {"machine_fields": ["name"]}, {"fallback_fields": ["absent"]}):
        invalid = deepcopy(localized)
        invalid["translation"].update(change)
        assert not model.validate_sidecar_records([invalid]).valid


def test_batch_refuses_debt_report_alias_before_writing(tmp_path):
    canonical = tmp_path / "example-asset.metadata.ndjson.gz"
    source = tmp_path / "translations.csv"
    output = tmp_path / "example-asset.metadata.es.ndjson.gz"
    model.write_metadata_sidecar([sidecar_record("1", VALID_HASH_A, {"name": "Alpha"})], canonical)
    write_translation_source(source, [])
    with pytest.raises(translation_local_io.TranslationLocalIOError, match="aliases"):
        loc.materialize_locale_sidecars(canonical_sidecar=canonical, translation_source=source,
                                       output_dir=tmp_path, locales=["es"], translatable_fields={"name"},
                                       reserved_outputs=[loc.debt_file_path(output, "es")])
    assert not output.exists()


def test_prepared_manifest_contains_coverage_and_exact_language_artifacts(tmp_path):
    from ingestion.common import feature_metadata

    canonical = tmp_path / "example-asset.metadata.ndjson.gz"
    source = tmp_path / "example-asset.metadata-translations.csv"
    manifest = tmp_path / "example-asset.manifest.json"
    model.write_metadata_sidecar([sidecar_record("1", VALID_HASH_A, {"name": "Alpha"})], canonical)
    write_translation_source(source, [translation("name", "Alpha", "Alfa")])
    payload = feature_metadata.manifest_payload(
        asset_slug="example-asset", release="2026-05-01", bucket_name="bucket", asset_root="category/group/example-asset",
        sha256_by_role={"fgb": "a" * 64, "pmtiles": "b" * 64, "metadata": translation_local_io.file_sha256(canonical), "schema": "d" * 64},
        schema=feature_metadata.schema_from_records(asset_slug="example-asset", release="2026-05-01", records=[]),
        source_inputs=[], identity=model.build_identity_metadata(strategy="source_field", source_fields=["name"]), feature_count=1,
    )
    manifest.write_text(json.dumps(payload))
    assert loc.main(["--canonical-sidecar", str(canonical), "--translation-source", str(source),
                     "--output-dir", str(tmp_path), "--locale", "es", "--locale", "fr",
                     "--translatable-field", "name", "--manifest", str(manifest)]) == 0
    updated = json.loads(manifest.read_text())
    assert updated["identity"] == payload["identity"]
    assert updated["translations"]["locales"]["es"]["current"] == 1
    assert updated["translations"]["locales"]["fr"]["missing"] == 1
    for artifact in updated["artifacts"][5:]:
        local = tmp_path / artifact["path"].rsplit("/", 1)[1]
        assert artifact["sha256"] == translation_local_io.file_sha256(local)
    model.validate_release_manifest(updated)
    model.validate_translation_bundle_manifest(updated, ["es", "fr"])
    with pytest.raises(model.ReleaseFeatureModelError, match="declared locales"):
        model.validate_translation_bundle_manifest(updated, ["es"])
    updated["artifacts"].pop()
    with pytest.raises(model.ReleaseFeatureModelError, match="inventory"):
        model.validate_translation_bundle_manifest(updated, ["es", "fr"])
