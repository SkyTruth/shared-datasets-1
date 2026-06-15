from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from scripts import feature_metadata_document_translate, feature_metadata_localization, release_feature_model


VALID_HASH_A = "sha256:" + "a" * 64
VALID_HASH_B = "sha256:" + "b" * 64


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


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class FeatureMetadataDocumentTranslateTests(unittest.TestCase):
    def test_export_writes_two_column_workbook_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "example-asset.metadata.ndjson.gz"
            output_dir = root / "translate"
            release_feature_model.write_metadata_sidecar(
                [
                    sidecar_record("1", VALID_HASH_A, {"name": "Alpha"}),
                    sidecar_record("2", VALID_HASH_B, {"name": "Alpha"}),
                ],
                canonical,
            )

            report = feature_metadata_document_translate.export_document_workbooks(
                canonical_sidecar=canonical,
                translation_source=None,
                output_dir=output_dir,
                locales=["es", "fr"],
                fields=["name"],
                asset_slug="example-asset",
                release="2026-05-01",
                direct_threshold_seconds=0,
                direct_max_rps=1,
            )
            workbook = output_dir / "example-asset.for-translate.xlsx"
            rows = feature_metadata_document_translate.read_xlsx_rows(workbook)
            manifest = feature_metadata_document_translate.load_manifest(Path(report["manifest"]))

        self.assertEqual(rows[0], ["hash", "text"])
        self.assertEqual(rows[1], [feature_metadata_localization.source_value_hash("Alpha"), "Alpha"])
        self.assertEqual(report["requested_task_count"], 4)
        self.assertEqual(report["unique_source_value_count"], 1)
        self.assertEqual(report["recommended_workflow"], "document_translation")
        self.assertEqual(manifest["workbook_schema"], "two columns: hash,text")

    def test_import_uses_manifest_order_and_ignores_translated_hash_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "example-asset.metadata.ndjson.gz"
            output_dir = root / "translate"
            translated = root / "translated-es.xlsx"
            translations = root / "example-asset.metadata-translations.csv"
            release_feature_model.write_metadata_sidecar(
                [
                    sidecar_record("1", VALID_HASH_A, {"name": "Alpha"}),
                    sidecar_record("2", VALID_HASH_B, {"name": "Beta"}),
                ],
                canonical,
            )
            export_report = feature_metadata_document_translate.export_document_workbooks(
                canonical_sidecar=canonical,
                translation_source=None,
                output_dir=output_dir,
                locales=["es"],
                fields=["name"],
                asset_slug="example-asset",
                release="2026-05-01",
            )
            feature_metadata_document_translate.write_xlsx_rows(
                translated,
                [
                    ["hash", "text"],
                    ["sha256:this-cell-was-translated", "Alfa"],
                    ["", "Beta ES"],
                ],
            )

            import_report = feature_metadata_document_translate.import_document_workbooks(
                manifest_path=Path(export_report["manifest"]),
                canonical_sidecar=canonical,
                translation_source=None,
                output_translation_source=translations,
                translated_files={"es": [translated]},
            )
            rows = read_csv_rows(translations)

        self.assertEqual(import_report["generated_row_count"], 2)
        self.assertEqual(import_report["hash_column_mismatch_count_by_locale"], {"es": 1})
        self.assertEqual([row["feature_id"] for row in rows], ["1", "2"])
        self.assertEqual([row["value"] for row in rows], ["Alfa", "Beta ES"])
        self.assertEqual(rows[0]["source_value_hash"], feature_metadata_localization.source_value_hash("Alpha"))

    def test_import_can_reuse_one_locale_file_for_another_locale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "example-asset.metadata.ndjson.gz"
            output_dir = root / "translate"
            translated = root / "translated-pt-br.xlsx"
            translations = root / "example-asset.metadata-translations.csv"
            release_feature_model.write_metadata_sidecar(
                [sidecar_record("1", VALID_HASH_A, {"name": "Alpha"})],
                canonical,
            )
            export_report = feature_metadata_document_translate.export_document_workbooks(
                canonical_sidecar=canonical,
                translation_source=None,
                output_dir=output_dir,
                locales=["pt", "pt_br"],
                fields=["name"],
                asset_slug="example-asset",
                release="2026-05-01",
            )
            feature_metadata_document_translate.write_xlsx_rows(
                translated,
                [["hash", "text"], [feature_metadata_localization.source_value_hash("Alpha"), "Alfa PT-BR"]],
            )

            feature_metadata_document_translate.import_document_workbooks(
                manifest_path=Path(export_report["manifest"]),
                canonical_sidecar=canonical,
                translation_source=None,
                output_translation_source=translations,
                translated_files={"pt_br": [translated]},
                reuse_locale={"pt": "pt_br"},
            )
            rows = read_csv_rows(translations)

        self.assertEqual([row["locale"] for row in rows], ["pt", "pt_br"])
        self.assertEqual([row["value"] for row in rows], ["Alfa PT-BR", "Alfa PT-BR"])
        self.assertIn("reused_locale=pt_br", rows[0]["notes"])


if __name__ == "__main__":
    unittest.main()
