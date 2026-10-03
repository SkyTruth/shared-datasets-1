from __future__ import annotations

import csv
import json
from pathlib import Path
import unittest

from scripts import feature_metadata_localization as loc, feature_metadata_translation_reuse as reuse, release_feature_model as model
from test_publication import publication_temp_directory


def record(fid, site, text, release="2026-06-09", slug="example-asset"):
    return {"schema_version": 2, "asset_slug": slug, "release": release, "feature_id": fid,
            "identity_key": [site], "geometry_hash": "sha256:" + "a" * 64, "properties_hash": "sha256:" + "b" * 64,
            "properties": {"SITE_PID": site, "name": text, "untouched": "original"}, "provenance": {"source": "fixture"}}


def row(fid, text, value, state="machine_translated", notes="provider=fixture"):
    return {"feature_id": fid, "field": "name", "locale": "fr", "source_value_hash": loc.source_value_hash(text),
            "value": value, "review_state": state, "notes": notes}


def source_bundle(root, records, rows):
    root.mkdir(parents=True, exist_ok=True)
    metadata, source = root / "old.metadata.ndjson.gz", root / "translations.csv"
    model.write_metadata_sidecar(records, metadata)
    with source.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=reuse.COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return {"asset_slug": records[0]["asset_slug"], "release": records[0]["release"], "canonical_sidecar": str(metadata), "translation_source": str(source)}


def build(root, records, rows):
    source = source_bundle(root / "source", records, rows)
    database = root / "memory.sqlite"
    report = reuse.build_memory(database=database, sources=[source], fields=["name"], locales=["fr"], source_key_fields=["SITE_PID"])
    return reuse.TranslationMemory(database), report


def rebuild(root, memory, records):
    canonical, schema = root / "new.metadata.ndjson.gz", root / "new.schema.json"
    model.write_metadata_sidecar(records, canonical)
    schema.write_text(json.dumps({"schema_version": 2, "asset_slug": "example-asset", "release": "2026-10-01", "fields": [
        {"name": "SITE_PID", "type": "String", "nullable": False, "projectable": True},
        {"name": "name", "type": "String", "nullable": False, "projectable": True},
        {"name": "untouched", "type": "String", "nullable": False, "projectable": True}]}))
    report = memory.rebuild(canonical_sidecar=canonical, schema=schema, asset_slug="example-asset", release="2026-10-01", output_dir=root / "output")
    output = list(model.read_metadata_sidecar(root / "output/example-asset.metadata.fr.ndjson.gz"))
    translations = loc.read_translation_source(root / "output/example-asset.metadata-translations.csv", translatable_fields={"name"})
    return report, output, translations


class TranslationReuseTests(unittest.TestCase):
    def test_rebuild_and_generic_materialization_agree_on_history_and_failed_current_rows(self):
        with publication_temp_directory() as temp:
            root = Path(temp)
            memory, _ = build(root, [record("1", "changed", "New"), record("2", "failed", "Oops")], [
                row("1", "Old", "Ancien"), row("2", "Old failure", "Ancien échec"),
                row("2", "Oops", "", "translation_failed"),
            ])
            self.addCleanup(memory.close)
            report, output, rows = rebuild(root, memory, [
                record("11", "changed", "New", "2026-10-01"), record("12", "failed", "Oops", "2026-10-01"),
            ])
            coverage = report["translations"]["locales"]["fr"]
            self.assertEqual((coverage["current"], coverage["stale"], coverage["missing"]), (0, 1, 1))
            self.assertEqual(rows[0].source_value_hash, loc.source_value_hash("Old"))
            self.assertEqual(rows[1].review_state, "translation_failed")
            generic, = loc.materialize_locale_sidecars(
                canonical_sidecar=root / "new.metadata.ndjson.gz",
                translation_source=root / "output/example-asset.metadata-translations.csv",
                output_dir=root / "generic", locales=["fr"], translatable_fields={"name"},
            )
            self.assertEqual(generic.coverage(), coverage)
            self.assertEqual(list(model.read_metadata_sidecar(Path(generic.output_sidecar))), output)

    def test_complete_csv_join_validation_rejects_corrupted_output(self):
        for problem in ("feature_id", "source_hash", "extra_row", "translated_value", "failed_value"):
            with self.subTest(problem=problem), publication_temp_directory() as temp:
                root = Path(temp)
                memory, _ = build(root, [record("1", "park-a", "Alpha")], [row("1", "Alpha", "Alfa")])
                try:
                    _, output, _ = rebuild(root, memory, [record("9", "park-a", "Alpha", "2026-10-01")])
                finally:
                    memory.close()
                csv_path = root / "output/example-asset.metadata-translations.csv"
                locale_path = root / "output/example-asset.metadata.fr.ndjson.gz"
                with csv_path.open() as handle:
                    rows = list(csv.DictReader(handle))
                if problem == "feature_id":
                    rows[0]["feature_id"] = "10"
                elif problem == "source_hash":
                    rows[0]["source_value_hash"] = loc.source_value_hash("different text")
                elif problem == "extra_row":
                    rows.append(rows[0])
                elif problem == "translated_value":
                    output[0]["properties"]["name"] = "wrong translated value"
                    model.write_metadata_sidecar(output, locale_path)
                else:
                    rows[0]["review_state"] = "translation_failed"
                with csv_path.open("w", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=reuse.COLUMNS)
                    writer.writeheader()
                    writer.writerows(rows)
                with self.assertRaises(reuse.TranslationReuseError):
                    reuse.validate_rebuilt_sidecars(root / "new.metadata.ndjson.gz", {"fr": locale_path}, ["name"], "example-asset", "2026-10-01", 1, csv_path)

    def test_supplement_fills_gaps_without_overwriting_existing_translations(self):
        with publication_temp_directory() as temp:
            root = Path(temp)
            memory, _ = build(root, [record("1", "park-a", "Alpha")], [row("1", "Alpha", "Alfa", "human_reviewed")])
            memory.close()
            supplement = root / "supplement.ndjson"
            values = [{"field": "name", "locale": "fr", "source_value": text, "source_value_hash": loc.source_value_hash(text),
                       "value": value, "review_state": "document_translated", "notes": "provider=test"}
                      for text, value in (("Alpha", "Must not replace"), ("New", "Nouveau"))]
            supplement.write_text("".join(json.dumps(r) + "\n" for r in values))
            memory = reuse.TranslationMemory(root / "memory.sqlite", supplement=supplement)
            self.addCleanup(memory.close)
            report, output, rows = rebuild(root, memory, [record("7", "park-a", "Alpha", "2026-10-01"), record("8", "park-b", "New", "2026-10-01")])
            self.assertEqual([r["properties"]["name"] for r in output], ["Alfa", "Nouveau"])
            self.assertEqual([r.review_state for r in rows], ["human_reviewed", "document_translated"])
            self.assertTrue(report["requested_rows_complete"])
            self.assertEqual(report["by_locale"]["fr"]["supplement_rows"], 1)

    def test_supplement_rejects_stale_hash_duplicate_or_failed_rows(self):
        for problem in ("hash", "duplicate", "failed", "field"):
            with self.subTest(problem=problem), publication_temp_directory() as temp:
                path = Path(temp) / "supplement.ndjson"
                value = {"field": "name", "locale": "fr", "source_value": "New", "source_value_hash": loc.source_value_hash("New"),
                         "value": "Nouveau", "review_state": "document_translated", "notes": "provider=test"}
                if problem == "hash":
                    value["source_value_hash"] = loc.source_value_hash("Old")
                elif problem == "failed":
                    value["review_state"] = "translation_failed"
                elif problem == "field":
                    value["field"] = "unapproved"
                path.write_text((json.dumps(value) + "\n") * (2 if problem == "duplicate" else 1))
                with self.assertRaises(reuse.TranslationReuseError):
                    reuse.read_supplement(path, [("name", "fr")])

    def test_renumbered_features_preserve_direct_human_translations_and_metadata(self):
        with publication_temp_directory() as temp:
            root = Path(temp)
            memory, _ = build(root, [record("1", "park-a", "Alpha"), record("2", "park-b", "Beta")],
                              [row("1", "Alpha", "Alfa", "human_reviewed", "reviewed for this site"), row("2", "Beta", "Bêta")])
            self.addCleanup(memory.close)
            targets = [record("2", "park-a", "Alpha", "2026-10-01"), record("1", "park-b", "Beta", "2026-10-01")]
            report, output, rows = rebuild(root, memory, targets)
            self.assertTrue(report["requested_rows_complete"])
            self.assertEqual([r["properties"]["name"] for r in output], ["Alfa", "Bêta"])
            self.assertEqual([r.feature_id for r in rows], ["2", "1"])
            self.assertEqual(rows[0].review_state, "human_reviewed")
            self.assertIn("reviewed for this site", rows[0].notes)
            self.assertEqual(output[0]["properties"]["untouched"], "original")
            self.assertEqual(output[0]["geometry_hash"], targets[0]["geometry_hash"])

    def test_changed_text_reuses_known_phrase_but_missing_text_is_a_failed_task(self):
        with publication_temp_directory() as temp:
            root = Path(temp)
            memory, _ = build(root, [record("1", "park-a", "Old"), record("2", "park-b", "New")],
                              [row("1", "Old", "Ancien"), row("2", "New", "Nouveau", "human_reviewed")])
            self.addCleanup(memory.close)
            report, output, rows = rebuild(root, memory, [record("9", "park-a", "New", "2026-10-01"), record("10", "new-site", "Unknown", "2026-10-01")])
            self.assertEqual(report["by_locale"]["fr"]["shared_text_rows"], 1)
            self.assertEqual(rows[0].review_state, "human_reviewed")
            self.assertEqual(output[0]["properties"]["name"], "Nouveau")
            self.assertEqual(output[1]["properties"]["name"], "Unknown")
            self.assertEqual(rows[1].review_state, "translation_failed")
            self.assertEqual(rows[1].value, "")
            self.assertFalse(report["requested_rows_complete"])

    def test_conflicting_phrases_remain_site_specific_and_do_not_spread(self):
        with publication_temp_directory() as temp:
            root = Path(temp)
            memory, report = build(root, [record("1", "park-a", "Bank"), record("2", "park-b", "Bank")],
                                   [row("1", "Bank", "Rive", "human_reviewed"), row("2", "Bank", "Banque", "human_reviewed")])
            self.addCleanup(memory.close)
            self.assertEqual(report["ambiguous_text_keys"], 1)
            report, output, _ = rebuild(root, memory, [record("5", "park-a", "Bank", "2026-10-01"), record("6", "park-c", "Bank", "2026-10-01")])
            self.assertEqual(output[0]["properties"]["name"], "Rive")
            self.assertEqual(output[1]["properties"]["name"], "Bank")
            self.assertEqual(report["by_locale"]["fr"]["conflicting_translations"], 1)

    def test_stale_placeholders_and_provider_failure_fallbacks_are_not_reused(self):
        with publication_temp_directory() as temp:
            root = Path(temp)
            records = [record(str(i), str(i), "New") for i in range(1, 4)]
            rows = [row("1", "Old", "Ancien"), row("2", "New", "New", "needs_review"),
                    row("3", "New", "New", "source_provided", "machine translation failed; source value retained; provider=google; target=fr")]
            memory, report = build(root, records, rows)
            self.addCleanup(memory.close)
            self.assertEqual(report["sources"][0]["stale_source_rows"], 1)
            self.assertEqual(report["sources"][0]["unconfirmed_or_failed_rows"], 1)
            report, _, rows = rebuild(root, memory, [record("1", "1", "New", "2026-10-01")])
            self.assertEqual(report["unique_pending_tasks"], 0)
            self.assertEqual(rows[0].review_state, "needs_review")

    def test_duplicate_source_identity_or_translation_refuses_without_replacing_database(self):
        for duplicate in ("identity", "translation"):
            with self.subTest(duplicate=duplicate), publication_temp_directory() as temp:
                root = Path(temp)
                database = root / "memory.sqlite"
                database.write_bytes(b"preserve me")
                records = [record("1", "site", "Alpha")]
                rows = [row("1", "Alpha", "Alfa")]
                if duplicate == "identity":
                    records.append(record("2", "site", "Alpha"))
                else:
                    rows.append(rows[0])
                source = source_bundle(root / "source", records, rows)
                with self.assertRaises(reuse.TranslationReuseError):
                    reuse.build_memory(database=database, sources=[source], fields=["name"], locales=["fr"], source_key_fields=["SITE_PID"])
                self.assertEqual(database.read_bytes(), b"preserve me")

    def test_shared_text_can_cross_assets_but_numeric_ids_cannot(self):
        with publication_temp_directory() as temp:
            root = Path(temp)
            bundle = source_bundle(root / "source", [record("1", "site", "Alpha", slug="other-asset")], [row("1", "Alpha", "Alfa")])
            reuse.build_memory(database=root / "memory.sqlite", sources=[bundle], fields=["name"], locales=["fr"], source_key_fields=["SITE_PID"])
            memory = reuse.TranslationMemory(root / "memory.sqlite")
            self.addCleanup(memory.close)
            report, output, _ = rebuild(root, memory, [record("1", "another-site", "Alpha", "2026-10-01")])
            self.assertEqual(report["by_locale"]["fr"]["shared_text_rows"], 1)
            self.assertEqual(output[0]["properties"]["name"], "Alfa")
