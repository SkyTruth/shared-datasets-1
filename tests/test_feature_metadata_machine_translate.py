from __future__ import annotations

import csv
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from scripts import feature_metadata_localization, feature_metadata_machine_translate, release_feature_model


VALID_HASH_A = "sha256:" + "a" * 64
VALID_HASH_B = "sha256:" + "b" * 64


class FakeTranslator:
    def __init__(self, target: str, calls: list[tuple[str, str]]) -> None:
        self.target = target
        self.calls = calls

    def translate(self, text: str) -> str:
        self.calls.append((self.target, text))
        return f"{self.target}:{text}"


def fake_translator_factory(calls: list[tuple[str, str]]):
    def factory(target: str) -> FakeTranslator:
        return FakeTranslator(target, calls)

    return factory


def sidecar_record(feature_id: str, properties_hash: str, properties: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": release_feature_model.METADATA_SIDECAR_SCHEMA_VERSION,
        "asset_slug": "example-asset",
        "release": "2026-05-01",
        "feature_id": feature_id,
        "geometry_hash": "sha256:" + "0" * 64,
        "properties_hash": properties_hash,
        "properties": properties,
        "provenance": {"source": "fixture"},
    }


def write_schema(path: Path, fields: list[str]) -> None:
    path.write_text(
        release_feature_model.canonical_json(
            release_feature_model.build_release_schema(
                asset_slug="example-asset",
                release="2026-05-01",
                fields=[release_feature_model.ReleaseSchemaField(field, "String") for field in fields],
            )
        )
        + "\n",
        encoding="utf-8",
    )


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class FeatureMetadataMachineTranslateTests(unittest.TestCase):
    def test_generates_generic_fields_and_locales_with_unique_value_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "example-asset.metadata.ndjson.gz"
            schema = root / "example-asset.schema.json"
            translations = root / "example-asset.metadata-translations.csv"
            release_feature_model.write_metadata_sidecar(
                [
                    sidecar_record("1", VALID_HASH_A, {"name": "Alpha", "designation": "Park"}),
                    sidecar_record("2", VALID_HASH_B, {"name": "Alpha", "designation": "Reserve"}),
                ],
                canonical,
            )
            write_schema(schema, ["name", "designation"])
            calls: list[tuple[str, str]] = []

            report = feature_metadata_machine_translate.generate_translation_source(
                canonical_sidecar=canonical,
                translation_source=translations,
                schema=schema,
                locales=["es", "fr"],
                fields=["name", "designation"],
                translator_factory=fake_translator_factory(calls),
                sleep_seconds=0,
                expected_asset_slug="example-asset",
                expected_release="2026-05-01",
            )
            rows = read_csv_rows(translations)

        self.assertEqual(report["generated_row_count"], 8)
        self.assertEqual(report["translated_unique_value_count"], 6)
        self.assertEqual(sorted(calls), sorted([("es", "Alpha"), ("es", "Park"), ("es", "Reserve"), ("fr", "Alpha"), ("fr", "Park"), ("fr", "Reserve")]))
        self.assertEqual(rows[0].keys(), set(feature_metadata_machine_translate.TRANSLATION_COLUMNS))
        self.assertIn(
            {
                "feature_id": "1",
                "field": "name",
                "locale": "es",
                "source_value_hash": feature_metadata_localization.source_value_hash("Alpha"),
                "value": "es:Alpha",
                "review_state": "machine_translated",
                "notes": "provider=google; target=es",
            },
            rows,
        )

    def test_preserves_existing_current_rows_and_materializes_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "example-asset.metadata.ndjson.gz"
            translations = root / "example-asset.metadata-translations.csv"
            output = root / "example-asset.metadata.es.ndjson.gz"
            release_feature_model.write_metadata_sidecar(
                [
                    sidecar_record("1", VALID_HASH_A, {"name": "Alpha"}),
                    sidecar_record("2", VALID_HASH_B, {"name": "Beta"}),
                ],
                canonical,
            )
            with translations.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=list(feature_metadata_machine_translate.TRANSLATION_COLUMNS),
                    lineterminator="\n",
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "feature_id": "1",
                        "field": "name",
                        "locale": "es",
                        "source_value_hash": feature_metadata_localization.source_value_hash("Alpha"),
                        "value": "Alfa",
                        "review_state": "human_reviewed",
                        "notes": "",
                    }
                )
            calls: list[tuple[str, str]] = []

            report = feature_metadata_machine_translate.generate_translation_source(
                canonical_sidecar=canonical,
                translation_source=translations,
                locales=["es"],
                fields=["name"],
                translator_factory=fake_translator_factory(calls),
                sleep_seconds=0,
            )
            localization_report = feature_metadata_localization.materialize_locale_sidecar(
                canonical_sidecar=canonical,
                translation_source=translations,
                output_sidecar=output,
                locale="es",
                translatable_fields={"name"},
                expected_asset_slug="example-asset",
                expected_release="2026-05-01",
            )
            localized_rows = list(release_feature_model.read_metadata_sidecar(output))

        self.assertEqual(report["existing_current_row_count"], 1)
        self.assertEqual(report["generated_row_count"], 1)
        self.assertEqual(calls, [("es", "Beta")])
        self.assertEqual(localization_report.applied_translation_count, 2)
        self.assertEqual(localized_rows[0]["properties"]["name"], "Alfa")
        self.assertEqual(localized_rows[1]["properties"]["name"], "es:Beta")

    def test_maps_field_safe_locale_to_translator_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "example-asset.metadata.ndjson.gz"
            translations = root / "example-asset.metadata-translations.csv"
            release_feature_model.write_metadata_sidecar(
                [sidecar_record("1", VALID_HASH_A, {"name": "Alpha"})],
                canonical,
            )
            calls: list[tuple[str, str]] = []

            report = feature_metadata_machine_translate.generate_translation_source(
                canonical_sidecar=canonical,
                translation_source=translations,
                locales=["pt_br"],
                fields=["name"],
                translator_factory=fake_translator_factory(calls),
                sleep_seconds=0,
            )
            rows = read_csv_rows(translations)

        self.assertEqual(report["target_by_locale"], {"pt_br": "pt"})
        self.assertEqual(calls, [("pt", "Alpha")])
        self.assertEqual(rows[0]["locale"], "pt_br")
        self.assertEqual(rows[0]["value"], "pt:Alpha")

    def test_progress_emits_compact_counters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "example-asset.metadata.ndjson.gz"
            translations = root / "example-asset.metadata-translations.csv"
            release_feature_model.write_metadata_sidecar(
                [
                    sidecar_record("1", VALID_HASH_A, {"name": "Alpha"}),
                    sidecar_record("2", VALID_HASH_B, {"name": "Beta"}),
                ],
                canonical,
            )
            calls: list[tuple[str, str]] = []
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                report = feature_metadata_machine_translate.generate_translation_source(
                    canonical_sidecar=canonical,
                    translation_source=translations,
                    locales=["es"],
                    fields=["name"],
                    translator_factory=fake_translator_factory(calls),
                    sleep_seconds=0,
                    progress=True,
                    progress_interval_seconds=0,
                )

        progress_lines = stderr.getvalue().strip().splitlines()
        self.assertEqual(report["translated_unique_value_count"], 2)
        self.assertEqual(progress_lines[0].split(",")[0], "translation-progress: 0/2 (0.0%)")
        self.assertEqual(progress_lines[-1].split(",")[0], "translation-progress: 2/2 (100.0%)")
        self.assertTrue(all("Alpha" not in line and "Beta" not in line for line in progress_lines))


if __name__ == "__main__":
    unittest.main()
