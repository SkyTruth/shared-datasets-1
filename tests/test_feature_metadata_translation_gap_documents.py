from pathlib import Path
import json
import unittest

from scripts import feature_metadata_document_translate as documents, feature_metadata_translation_gap_documents as gaps, feature_metadata_localization as loc
from scripts.feature_metadata_translation_reuse import TranslationReuseError
from test_publication import publication_temp_directory


class GapDocumentTests(unittest.TestCase):
    def prepare(self, root):
        tasks = [{"field": field, "locale": locale, "source_value_hash": loc.source_value_hash(text), "source_value": text,
                  "reason": "missing_translation", "affected_rows": 1000000}
                 for field, text in (("name", "Alpha"), ("designation", "Alpha"), ("name", "Beta")) for locale in ("pt", "pt_br")]
        pending = root / "pending.ndjson"
        pending.write_text("".join(json.dumps(t) + "\n" for t in tasks))
        exported = gaps.export_gaps(pending=[pending], output_dir=root / "documents")
        workbook = root / "translated.xlsx"
        rows = documents.read_xlsx_rows(Path(exported["workbooks"][0]))
        documents.write_xlsx_rows(workbook, [rows[0], *[[key, "translated " + text] for key, text in reversed(rows[1:])]])
        return pending, workbook, exported

    def test_only_distinct_gap_texts_are_exported_and_reordered_import_rejoins_all_fields(self):
        with publication_temp_directory() as temp:
            root = Path(temp)
            _, workbook, exported = self.prepare(root)
            self.assertEqual(exported["task_count"], 6)
            self.assertEqual(exported["unique_source_texts"], 2)
            output = root / "supplement.ndjson"
            report = gaps.import_gaps(manifest_path=root / "documents/gap-manifest.json", translated_files={"pt": [workbook]}, reuse_locale={"pt_br": "pt"}, output=output)
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(report["gap_task_count"], 6)
            self.assertTrue(all(row["value"] == "translated " + row["source_value"] for row in rows))
            self.assertTrue(all(row["review_state"] == "document_translated" for row in rows))
            self.assertTrue(all("reused_locale=pt" in row["notes"] for row in rows if row["locale"] == "pt_br"))

    def test_changed_task_snapshot_rejects_import_without_replacing_output(self):
        with publication_temp_directory() as temp:
            root = Path(temp)
            pending, workbook, _ = self.prepare(root)
            pending.write_text(pending.read_text() + "\n")
            output = root / "supplement.ndjson"
            output.write_text("preserve")
            with self.assertRaisesRegex(TranslationReuseError, "input changed"):
                gaps.import_gaps(manifest_path=root / "documents/gap-manifest.json", translated_files={"pt": [workbook]}, reuse_locale={"pt_br": "pt"}, output=output)
            self.assertEqual(output.read_text(), "preserve")

    def test_corrupted_translation_hash_rejects_import(self):
        with publication_temp_directory() as temp:
            root = Path(temp)
            _, workbook, _ = self.prepare(root)
            rows = documents.read_xlsx_rows(workbook)
            rows[1][0] = loc.source_value_hash("unknown")
            documents.write_xlsx_rows(workbook, rows)
            with self.assertRaises(documents.FeatureMetadataDocumentTranslateError):
                gaps.import_gaps(manifest_path=root / "documents/gap-manifest.json", translated_files={"pt": [workbook]}, reuse_locale={"pt_br": "pt"}, output=root / "supplement.ndjson")
            self.assertFalse((root / "supplement.ndjson").exists())
