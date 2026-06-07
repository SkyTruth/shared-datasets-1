from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
