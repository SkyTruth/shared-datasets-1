from __future__ import annotations

import contextlib
import io
from unittest import mock

import pytest

from scripts import feature_metadata_machine_translate as machine
from scripts import translation_local_io

import csv
import tempfile
import unittest
from pathlib import Path

from scripts import feature_metadata_localization
from scripts import release_feature_model


VALID_HASH_A = "sha256:" + "a" * 64
VALID_HASH_B = "sha256:" + "b" * 64


def write_translation_source(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = ["feature_id", "field", "locale", "source_value_hash", "value", "review_state"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def sidecar_record(feature_id: str, properties_hash: str, properties: dict[str, object]) -> dict[str, object]:
    sidecar_properties = dict(properties)
    sidecar_properties.setdefault("feature_id", feature_id.rsplit(":", 1)[-1])
    return {
        "schema_version": release_feature_model.METADATA_SIDECAR_SCHEMA_VERSION,
        "asset_slug": "example-asset",
        "release": "2026-05-01",
        "feature_id": feature_id,
        "geometry_hash": "sha256:" + "0" * 64,
        "properties_hash": properties_hash,
        "properties": sidecar_properties,
        "provenance": {"source": "fixture"},
    }


class FeatureMetadataLocalizationTests(unittest.TestCase):
    def test_materialize_locale_sidecar_applies_current_translations_and_reports_stale_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "example-asset.metadata.ndjson.gz"
            output = root / "example-asset.metadata.es.ndjson.gz"
            translations = root / "example-asset.metadata-translations.csv"
            release_feature_model.write_metadata_sidecar(
                [
                    sidecar_record("1", VALID_HASH_A, {"name": "Alpha", "kind": "one"}),
                    sidecar_record("2", VALID_HASH_B, {"name": "Beta", "kind": "two"}),
                ],
                canonical,
            )
            write_translation_source(
                translations,
                [
                    {
                        "feature_id": "1",
                        "field": "name",
                        "locale": "es",
                        "source_value_hash": feature_metadata_localization.source_value_hash("Alpha"),
                        "value": "Alfa",
                        "review_state": "human_reviewed",
                    },
                    {
                        "feature_id": "2",
                        "field": "name",
                        "locale": "es",
                        "source_value_hash": feature_metadata_localization.source_value_hash("Old Beta"),
                        "value": "Beta antigua",
                        "review_state": "human_reviewed",
                    },
                ],
            )

            report = feature_metadata_localization.materialize_locale_sidecar(
                canonical_sidecar=canonical,
                translation_source=translations,
                output_sidecar=output,
                locale="es",
                translatable_fields={"name"},
                expected_asset_slug="example-asset",
                expected_release="2026-05-01",
            )
            rows = list(release_feature_model.read_metadata_sidecar(output))

        self.assertEqual(report.feature_count, 2)
        self.assertEqual(report.applied_translation_count, 1)
        self.assertEqual(report.stale_translation_count, 1)
        self.assertEqual(report.untranslated_feature_count, 1)
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["feature_id"] for row in rows], ["1", "2"])
        self.assertEqual([row["properties_hash"] for row in rows], [VALID_HASH_A, VALID_HASH_B])
        self.assertEqual(rows[0]["properties"]["name"], "Alfa")
        self.assertEqual(rows[0]["properties"]["kind"], "one")
        self.assertEqual(rows[1]["properties"]["name"], "Beta")
        self.assertEqual(report.stale_translations[0]["current_source_value_hash"], feature_metadata_localization.source_value_hash("Beta"))

    def test_duplicate_translation_keys_fail_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            translations = root / "example-asset.metadata-translations.csv"
            digest = feature_metadata_localization.source_value_hash("Alpha")
            duplicate = {
                "feature_id": "1",
                "field": "name",
                "locale": "es",
                "source_value_hash": digest,
                "value": "Alfa",
                "review_state": "human_reviewed",
            }
            write_translation_source(translations, [duplicate, duplicate])

            with self.assertRaisesRegex(feature_metadata_localization.FeatureMetadataLocalizationError, "duplicate translation key"):
                feature_metadata_localization.read_translation_source(translations, translatable_fields={"name"})

    def test_materialize_locale_sidecars_generates_every_locale_and_reports_each_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "example-asset.metadata.ndjson.gz"
            output_dir = root / "localized"
            report_dir = root / "reports"
            translations = root / "example-asset.metadata-translations.csv"
            release_feature_model.write_metadata_sidecar(
                [sidecar_record("1", VALID_HASH_A, {"name": "Alpha", "kind": "one"})],
                canonical,
            )
            write_translation_source(
                translations,
                [
                    {
                        "feature_id": "1",
                        "field": "name",
                        "locale": "es",
                        "source_value_hash": feature_metadata_localization.source_value_hash("Alpha"),
                        "value": "Alfa",
                        "review_state": "human_reviewed",
                    },
                    {
                        "feature_id": "1",
                        "field": "name",
                        "locale": "fr",
                        "source_value_hash": feature_metadata_localization.source_value_hash("Alpha"),
                        "value": "Alpha FR",
                        "review_state": "machine_translated",
                    },
                ],
            )

            reports = feature_metadata_localization.materialize_locale_sidecars(
                canonical_sidecar=canonical,
                translation_source=translations,
                output_dir=output_dir,
                locales=None,
                translatable_fields={"name"},
                expected_asset_slug="example-asset",
                expected_release="2026-05-01",
                report_dir=report_dir,
            )
            es_rows = list(release_feature_model.read_metadata_sidecar(output_dir / "example-asset.metadata.es.ndjson.gz"))
            fr_rows = list(release_feature_model.read_metadata_sidecar(output_dir / "example-asset.metadata.fr.ndjson.gz"))
            es_report_exists = (report_dir / "example-asset.metadata.es.ndjson.gz.report.json").exists()
            fr_report_exists = (report_dir / "example-asset.metadata.fr.ndjson.gz.report.json").exists()

        self.assertEqual([report.locale for report in reports], ["es", "fr"])
        self.assertEqual(es_rows[0]["feature_id"], "1")
        self.assertEqual(es_rows[0]["properties_hash"], VALID_HASH_A)
        self.assertEqual(es_rows[0]["properties"]["name"], "Alfa")
        self.assertEqual(fr_rows[0]["properties"]["name"], "Alpha FR")
        self.assertTrue(es_report_exists)
        self.assertTrue(fr_report_exists)

    def test_translation_fields_must_be_allowlisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            translations = root / "example-asset.metadata-translations.csv"
            write_translation_source(
                translations,
                [
                    {
                        "feature_id": "1",
                        "field": "internal_note",
                        "locale": "es",
                        "source_value_hash": feature_metadata_localization.source_value_hash("Alpha"),
                        "value": "Nota",
                        "review_state": "human_reviewed",
                    }
                ],
            )

            with self.assertRaisesRegex(feature_metadata_localization.FeatureMetadataLocalizationError, "not in the translatable-field allowlist"):
                feature_metadata_localization.read_translation_source(translations, translatable_fields={"name"})

    def test_release_schema_can_supply_translatable_field_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            schema = root / "example-asset.schema.json"
            schema.write_text(
                release_feature_model.canonical_json(
                    release_feature_model.build_release_schema(
                        asset_slug="example-asset",
                        release="2026-05-01",
                        fields=[
                            release_feature_model.ReleaseSchemaField("name", "String"),
                            release_feature_model.ReleaseSchemaField("internal", "String", projectable=False),
                        ],
                    )
                )
                + "\n"
            )

            fields = feature_metadata_localization.resolved_translatable_fields(schema=schema, fields=[])

        self.assertEqual(fields, {"name"})


# Integrity regressions exercise real files and the public local/CLI boundaries.


def localization_fixture(tmp_path, *, empty=False, stale=False):
    canonical = tmp_path / "example-asset.metadata.ndjson.gz"
    source = tmp_path / "translations.csv"
    output = tmp_path / "example-asset.metadata.es.ndjson.gz"
    release_feature_model.write_metadata_sidecar(
        [] if empty else [sidecar_record("1", VALID_HASH_A, {"name": "Alpha"})], canonical,
    )
    machine.write_translation_source(source, [] if empty else [{
        "feature_id": "1", "field": "name", "locale": "es",
        "source_value_hash": feature_metadata_localization.source_value_hash("Old" if stale else "Alpha"),
        "value": "Alfa", "review_state": "human_reviewed", "notes": "",
    }])
    return canonical, source, output


def materialize(canonical, source, output, **kwargs):
    return feature_metadata_localization.materialize_locale_sidecar(
        canonical_sidecar=canonical, translation_source=source, output_sidecar=output,
        locale="es", translatable_fields={"name"}, **kwargs,
    )


@pytest.mark.parametrize("protected_name", ["canonical", "source", "schema"])
@pytest.mark.parametrize("alias_kind", ["same", "symlink", "hardlink"])
def test_materialization_refuses_all_input_aliases(tmp_path, protected_name, alias_kind):
    canonical, source, output = localization_fixture(tmp_path)
    schema = tmp_path / "schema.json"
    schema.write_text("{}")
    protected = {"canonical": canonical, "source": source, "schema": schema}[protected_name]
    before = {path: path.read_bytes() for path in (canonical, source, schema)}
    if alias_kind == "same":
        output = protected
    elif alias_kind == "symlink":
        output.symlink_to(protected)
    else:
        output.hardlink_to(protected)
    with pytest.raises(translation_local_io.TranslationLocalIOError):
        materialize(canonical, source, output, protected_inputs=[schema])
    assert all(path.read_bytes() == value for path, value in before.items())


def test_fail_on_stale_preserves_previous_output(tmp_path):
    canonical, source, output = localization_fixture(tmp_path, stale=True)
    output.write_bytes(b"previous verified output")
    with pytest.raises(feature_metadata_localization.FeatureMetadataLocalizationError, match="stale"):
        materialize(canonical, source, output, fail_on_stale=True)
    assert output.read_bytes() == b"previous verified output"


@pytest.mark.parametrize("defect", ["midstream", "identity", "missing_record"])
def test_candidate_failure_does_not_replace_final(tmp_path, defect):
    canonical, source, output = localization_fixture(tmp_path)
    original = release_feature_model.write_metadata_sidecar
    output.write_bytes(b"verified")

    def corrupt_writer(records, path):
        def corrupt():
            for record in records:
                if defect == "missing_record":
                    continue
                record = dict(record)
                if defect == "identity":
                    record["geometry_hash"] = VALID_HASH_B
                yield record
                if defect == "midstream":
                    raise OSError("fixture interrupted gzip")
        original(corrupt(), path)

    with mock.patch.object(release_feature_model, "write_metadata_sidecar", side_effect=corrupt_writer):
        with pytest.raises((feature_metadata_localization.FeatureMetadataLocalizationError, OSError)):
            materialize(canonical, source, output)
    assert output.read_bytes() == b"verified"
    assert len(list(release_feature_model.read_metadata_sidecar(canonical))) == 1


def test_valid_empty_source_is_not_treated_as_truncation(tmp_path):
    canonical, source, output = localization_fixture(tmp_path, empty=True)
    report = materialize(canonical, source, output)
    assert report.valid and report.feature_count == 0
    assert list(release_feature_model.read_metadata_sidecar(output)) == []


def test_malformed_canonical_preserves_final(tmp_path):
    canonical, source, output = localization_fixture(tmp_path)
    canonical.write_bytes(b"invalid gzip")
    output.write_bytes(b"verified")
    with pytest.raises(OSError):
        materialize(canonical, source, output)
    assert output.read_bytes() == b"verified"


@pytest.mark.parametrize("batch", [False, True])
def test_cli_report_cannot_replace_input_or_sidecar(tmp_path, batch):
    canonical, source, output = localization_fixture(tmp_path)
    before = canonical.read_bytes()
    args = ["--canonical-sidecar", str(canonical), "--translation-source", str(source),
            "--locale", "es", "--translatable-field", "name"]
    args += ["--all-locales", "--output-dir", str(tmp_path)] if batch else ["--output-sidecar", str(output)]
    for report in (canonical, source, output):
        with contextlib.redirect_stderr(io.StringIO()):
            assert feature_metadata_localization.main([*args, "--report", str(report)]) == 2
        assert canonical.read_bytes() == before
        assert not output.exists()


@pytest.mark.parametrize("later", ["sidecar", "report"])
def test_earlier_locale_cannot_refresh_later_output_expectation(tmp_path, later):
    canonical, source, first = localization_fixture(tmp_path)
    later_path = (tmp_path / "example-asset.metadata.fr.ndjson.gz" if later == "sidecar"
                  else tmp_path / "reports" / "example-asset.metadata.fr.ndjson.gz.report.json")
    later_path.parent.mkdir(exist_ok=True)
    later_path.write_bytes(b"original later output")
    original = release_feature_model.write_metadata_sidecar
    calls = 0

    def edit_later(records, path):
        nonlocal calls
        original(records, path)
        calls += 1
        if calls == 1:
            later_path.write_bytes(b"human edited later output")

    with mock.patch.object(release_feature_model, "write_metadata_sidecar", side_effect=edit_later):
        with pytest.raises(translation_local_io.TranslationLocalIOError, match="changed"):
            feature_metadata_localization.materialize_locale_sidecars(
                canonical_sidecar=canonical, translation_source=source, output_dir=tmp_path,
                locales=["es", "fr"], translatable_fields={"name"}, report_dir=tmp_path / "reports",
            )
    assert first.exists()
    assert later_path.read_bytes() == b"human edited later output"


def test_cli_snapshot_precedes_schema_allowlist_read(tmp_path):
    canonical, source, output = localization_fixture(tmp_path)
    schema = tmp_path / "schema.json"
    schema.write_text(release_feature_model.canonical_json(release_feature_model.build_release_schema(
        asset_slug="example-asset", release="2026-05-01",
        fields=[release_feature_model.ReleaseSchemaField("name", "String")],
    )))
    original = feature_metadata_localization.resolved_translatable_fields

    def parse_then_edit(**kwargs):
        result = original(**kwargs)
        schema.write_text(schema.read_text() + "\n")
        return result

    with mock.patch.object(feature_metadata_localization, "resolved_translatable_fields", side_effect=parse_then_edit):
        with contextlib.redirect_stderr(io.StringIO()):
            assert feature_metadata_localization.main([
                "--canonical-sidecar", str(canonical), "--translation-source", str(source),
                "--schema", str(schema), "--locale", "es", "--output-sidecar", str(output),
            ]) == 2
    assert not output.exists()


def test_batch_snapshot_precedes_locale_csv_read(tmp_path):
    canonical, source, output = localization_fixture(tmp_path)
    original = feature_metadata_localization.read_translation_source
    calls = 0

    def read_then_edit(*args, **kwargs):
        nonlocal calls
        rows = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            source.write_text(source.read_text().replace("Alfa", "human correction"))
        return rows

    with mock.patch.object(feature_metadata_localization, "read_translation_source", side_effect=read_then_edit):
        with pytest.raises(translation_local_io.TranslationLocalIOError, match="changed"):
            feature_metadata_localization.materialize_locale_sidecars(
                canonical_sidecar=canonical, translation_source=source, output_dir=tmp_path,
                locales=None, translatable_fields={"name"},
            )
    assert not output.exists() and "human correction" in source.read_text()


if __name__ == "__main__":
    unittest.main()
