from __future__ import annotations

import contextlib
import io
import json
import zipfile
from unittest import mock

import pytest

from scripts import feature_metadata_machine_translate as machine
from scripts import translation_local_io

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

    def test_import_matches_intact_hashes_when_rows_are_reordered(self):
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
                    [feature_metadata_localization.source_value_hash("Beta"), "Beta ES"],
                    [feature_metadata_localization.source_value_hash("Alpha"), "Alfa"],
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
        self.assertTrue(import_report["complete"])
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




def document_fixture(tmp_path, *, values=("Alpha", "Beta"), max_rows=60_000, existing=None, **options):
    canonical = tmp_path / "example-asset.metadata.ndjson.gz"
    output = tmp_path / "translations.csv"
    release_feature_model.write_metadata_sidecar([
        sidecar_record(str(i), feature_metadata_localization.source_value_hash(value), {"name": value})
        for i, value in enumerate(values, start=1)
    ], canonical)
    if existing is not None:
        machine.write_translation_source(output, existing)
    report = feature_metadata_document_translate.export_document_workbooks(
        canonical_sidecar=canonical, translation_source=output if existing is not None else None,
        output_dir=tmp_path / "workbooks", locales=["es"], fields=["name"], max_shard_rows=max_rows, **options,
    )
    workbooks = [Path(shard["path"]) for shard in report["shards"]]
    for workbook in workbooks:
        rows = feature_metadata_document_translate.read_xlsx_rows(workbook)
        feature_metadata_document_translate.write_xlsx_rows(workbook, [rows[0], *[[digest, f"ES:{text}"] for digest, text in rows[1:]]])
    return canonical, output, Path(report["manifest"]), workbooks


def document_import(fixture, **kwargs):
    canonical, output, manifest, workbooks = fixture
    return feature_metadata_document_translate.import_document_workbooks(
        canonical_sidecar=canonical, output_translation_source=output, translation_source=None,
        manifest_path=manifest, translated_files={"es": workbooks}, **kwargs,
    )


def doc_row(value="Alfa", state="human_reviewed"):
    return {"feature_id": "1", "field": "name", "locale": "es",
            "source_value_hash": feature_metadata_localization.source_value_hash("Alpha"),
            "value": value, "review_state": state, "notes": ""}


def test_reordered_shards_and_rows_use_actual_hashes(tmp_path):
    fixture = document_fixture(tmp_path, values=("Alpha", "Beta", "Gamma", "Delta"), max_rows=2)
    fixture[3].reverse()
    for workbook in fixture[3]:
        rows = feature_metadata_document_translate.read_xlsx_rows(workbook)
        feature_metadata_document_translate.write_xlsx_rows(workbook, [rows[0], *reversed(rows[1:])])
    report = document_import(fixture)
    assert report["complete"]
    assert [row["value"] for row in read_csv_rows(fixture[1])] == ["ES:Alpha", "ES:Beta", "ES:Gamma", "ES:Delta"]


@pytest.mark.parametrize("damage", ["blank_hash", "bad_hash", "foreign", "duplicate", "extra", "missing", "header", "extra_column", "blank_value", "extra_blank_row"])
def test_damaged_workbook_refuses_without_csv_changes(tmp_path, damage):
    fixture = document_fixture(tmp_path)
    workbook = fixture[3][0]
    rows = feature_metadata_document_translate.read_xlsx_rows(workbook)
    if damage == "blank_hash":
        rows[1][0] = ""
    elif damage == "bad_hash":
        rows[1][0] = "sha256:translated-by-document-provider"
    elif damage == "foreign":
        rows[1][0] = feature_metadata_localization.source_value_hash("foreign")
    elif damage == "duplicate":
        rows[2][0] = rows[1][0]
    elif damage == "extra":
        rows.append([feature_metadata_localization.source_value_hash("extra"), "extra"])
    elif damage == "missing":
        rows.pop()
    elif damage == "header":
        rows[0] = ["translated hash", "translated text"]
    elif damage == "extra_column":
        rows[1].append("foreign column")
    elif damage == "blank_value":
        rows[1][1] = " "
    else:
        rows.append(["", ""])
    feature_metadata_document_translate.write_xlsx_rows(workbook, rows)
    machine.write_translation_source(fixture[1], [doc_row("existing human")])
    before = fixture[1].read_bytes()
    with pytest.raises(feature_metadata_document_translate.FeatureMetadataDocumentTranslateError):
        document_import(fixture)
    assert fixture[1].read_bytes() == before


@pytest.mark.parametrize("damage", ["missing", "extra", "duplicate", "mixed"])
def test_shard_membership_is_exact(tmp_path, damage):
    fixture = document_fixture(tmp_path, values=("Alpha", "Beta", "Gamma", "Delta"), max_rows=2)
    files = fixture[3]
    if damage == "missing":
        files.pop()
    elif damage == "extra":
        files.append(files[0])
    elif damage == "duplicate":
        files[1] = files[0]
    else:
        left = feature_metadata_document_translate.read_xlsx_rows(files[0])
        right = feature_metadata_document_translate.read_xlsx_rows(files[1])
        left[1], right[1] = right[1], left[1]
        feature_metadata_document_translate.write_xlsx_rows(files[0], left)
        feature_metadata_document_translate.write_xlsx_rows(files[1], right)
    with pytest.raises(feature_metadata_document_translate.FeatureMetadataDocumentTranslateError):
        document_import(fixture)
    assert not fixture[1].exists()


@pytest.mark.parametrize("damage", ["v1", "duplicate_json", "task_duplicate", "task_missing", "pending_count", "shard_duplicate", "row_count", "entry_missing", "text", "options", "canonical", "schema", "invalid_type"])
def test_manifest_identity_must_match_exact_tasks_and_inputs(tmp_path, damage):
    fixture = document_fixture(tmp_path)
    path = fixture[2]
    payload = json.loads(path.read_text())
    if damage == "v1":
        payload["schema"] = "feature_metadata_document_translation_manifest_v1"
    elif damage == "duplicate_json":
        path.write_text(path.read_text().replace('"valid": true', '"valid": true, "valid": true'))
    elif damage == "task_duplicate":
        payload["tasks"].append(payload["tasks"][0])
    elif damage == "task_missing":
        payload["tasks"].pop()
    elif damage == "pending_count":
        payload["requested_task_count"] += 1
    elif damage == "shard_duplicate":
        payload["shards"].append(payload["shards"][0])
    elif damage == "row_count":
        payload["shards"][0]["data_row_count"] += 1
    elif damage == "entry_missing":
        payload["entries"].pop()
    elif damage == "text":
        payload["entries"][0]["source_text"] = "wrong source"
    elif damage == "options":
        payload["collection_options"]["stringify_non_string"] = "false"
    elif damage == "canonical":
        payload["canonical_sha256"] = "0" * 64
    elif damage == "schema":
        payload["schema_sha256"] = "0" * 64
    else:
        payload["tasks"] = {}
    if damage != "duplicate_json":
        path.write_text(json.dumps(payload))
    with pytest.raises(feature_metadata_document_translate.FeatureMetadataDocumentTranslateError):
        document_import(fixture)
    assert not fixture[1].exists()


def test_import_preserves_human_completed_after_export_and_is_idempotent(tmp_path):
    fixture = document_fixture(tmp_path)
    machine.write_translation_source(fixture[1], [doc_row("human done while workbook translated")])
    document_import(fixture)
    assert [row["value"] for row in read_csv_rows(fixture[1])] == ["human done while workbook translated", "ES:Beta"]
    before = fixture[1].read_bytes()
    assert document_import(fixture)["generated_row_count"] == 0
    assert fixture[1].read_bytes() == before


def test_missing_formerly_completed_task_requires_fresh_export(tmp_path):
    fixture = document_fixture(tmp_path, existing=[doc_row()])
    machine.write_translation_source(fixture[1], [])
    with pytest.raises(feature_metadata_document_translate.FeatureMetadataDocumentTranslateError, match="re-export"):
        document_import(fixture)
    assert read_csv_rows(fixture[1]) == []


def test_failed_current_task_is_exported_and_replaced(tmp_path):
    fixture = document_fixture(tmp_path, existing=[doc_row("", "translation_failed")])
    assert json.loads(fixture[2].read_text())["requested_task_count"] == 2
    document_import(fixture)
    assert len(read_csv_rows(fixture[1])) == 2
    assert read_csv_rows(fixture[1])[0]["value"] == "ES:Alpha"


@pytest.mark.parametrize("values,existing", [((), None), (("Alpha",), [doc_row()])])
def test_empty_task_export_has_one_explicit_empty_shard(tmp_path, values, existing):
    fixture = document_fixture(tmp_path, values=values, existing=existing)
    assert feature_metadata_document_translate.read_xlsx_rows(fixture[3][0]) == [["hash", "text"]]
    assert document_import(fixture)["complete"]


def test_collection_options_survive_export_import(tmp_path):
    fixture = document_fixture(tmp_path, values=(42, "123", "Alpha"), stringify_non_string=True, skip_numeric_strings=True)
    document_import(fixture)
    assert [row["feature_id"] for row in read_csv_rows(fixture[1])] == ["3"]


@pytest.mark.parametrize("damage", ["duplicate_cell", "formula", "duplicate_row"])
def test_ambiguous_xlsx_coordinates_refused(tmp_path, damage):
    fixture = document_fixture(tmp_path)
    workbook = fixture[3][0]
    with zipfile.ZipFile(workbook) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    xml = members["xl/worksheets/sheet1.xml"].decode()
    if damage == "duplicate_cell":
        xml = xml.replace('r="B2"', 'r="A2"')
    elif damage == "formula":
        xml = xml.replace('<c r="B2" t="inlineStr">', '<c r="B2" t="inlineStr"><f>1+1</f>')
    else:
        xml = xml.replace('<row r="3">', '<row r="2">')
    members["xl/worksheets/sheet1.xml"] = xml.encode()
    with zipfile.ZipFile(workbook, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    with pytest.raises(feature_metadata_document_translate.FeatureMetadataDocumentTranslateError):
        document_import(fixture)


def test_cli_report_alias_is_refused_before_import(tmp_path):
    fixture = document_fixture(tmp_path)
    args = ["import", "--manifest", str(fixture[2]), "--canonical-sidecar", str(fixture[0]),
            "--output-translation-source", str(fixture[1]), "--translated-file", f"es={fixture[3][0]}"]
    for report in (fixture[0], fixture[1], fixture[2], fixture[3][0]):
        with contextlib.redirect_stderr(io.StringIO()):
            assert feature_metadata_document_translate.main([*args, "--report", str(report)]) == 2
        assert not fixture[1].exists()


def test_export_report_alias_is_preflighted_with_generated_paths(tmp_path):
    canonical, _, _, _ = document_fixture(tmp_path)
    with pytest.raises(translation_local_io.TranslationLocalIOError):
        feature_metadata_document_translate.export_document_workbooks(
            canonical_sidecar=canonical, translation_source=None, output_dir=tmp_path / "new",
            locales=["es"], fields=["name"], reserved_outputs=[tmp_path / "new" / "example-asset.for-translate.xlsx"],
        )
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("later", ["shard", "manifest"])
def test_earlier_shard_cannot_refresh_later_destination_expectation(tmp_path, later):
    canonical, _, _, _ = document_fixture(tmp_path)
    output_dir = tmp_path / "new"
    output_dir.mkdir()
    name = "example-asset.for-translate.part-002.xlsx" if later == "shard" else "example-asset.for-translate.manifest.json"
    later_path = output_dir / name
    later_path.write_bytes(b"original output")
    original = feature_metadata_document_translate.write_xlsx_rows
    calls = 0

    def edit_later(path, rows, **kwargs):
        nonlocal calls
        original(path, rows, **kwargs)
        calls += 1
        if calls == 1:
            later_path.write_bytes(b"human edited output")

    with mock.patch.object(feature_metadata_document_translate, "write_xlsx_rows", side_effect=edit_later):
        with pytest.raises(translation_local_io.TranslationLocalIOError, match="changed"):
            feature_metadata_document_translate.export_document_workbooks(
                canonical_sidecar=canonical, translation_source=None, output_dir=output_dir,
                locales=["es"], fields=["name"], max_shard_rows=1,
            )
    assert later_path.read_bytes() == b"human edited output"
    assert (output_dir / "example-asset.for-translate.part-001.xlsx").exists()


def test_document_cli_export_import_print_one_json_each(tmp_path):
    canonical, output, _, _ = document_fixture(tmp_path)
    report_path = tmp_path / "summary.json"
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        assert feature_metadata_document_translate.main([
            "export", "--canonical-sidecar", str(canonical), "--output-dir", str(tmp_path / "cli"),
            "--locale", "es", "--field", "name", "--report", str(report_path),
        ]) == 0
    exported = json.loads(stdout.getvalue())
    assert exported == json.loads(report_path.read_text())
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        assert feature_metadata_document_translate.main([
            "import", "--canonical-sidecar", str(canonical), "--manifest", exported["manifest"],
            "--translated-file", "es=" + exported["shards"][0]["path"],
            "--output-translation-source", str(output), "--report", str(report_path),
        ]) == 0
    imported = json.loads(stdout.getvalue())
    assert imported == json.loads(report_path.read_text()) and imported["complete"]


@pytest.mark.parametrize("row_ref", ["02", "٠٢", "0", "-2"])
def test_noncanonical_row_coordinates_are_refused(tmp_path, row_ref):
    fixture = document_fixture(tmp_path)
    workbook = fixture[3][0]
    with zipfile.ZipFile(workbook) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    xml = members["xl/worksheets/sheet1.xml"].decode()
    xml = xml.replace('<row r="3">', f'<row r="{row_ref}">').replace('r="A3"', f'r="A{row_ref}"').replace('r="B3"', f'r="B{row_ref}"')
    members["xl/worksheets/sheet1.xml"] = xml.encode()
    with zipfile.ZipFile(workbook, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    with pytest.raises(feature_metadata_document_translate.FeatureMetadataDocumentTranslateError, match="row reference"):
        document_import(fixture)
    assert not fixture[1].exists()


if __name__ == "__main__":
    unittest.main()
