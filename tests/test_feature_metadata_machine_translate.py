from __future__ import annotations

from unittest import mock

import pytest

from scripts import feature_metadata_document_translate as document
from scripts import translation_local_io

import csv
import json
import subprocess
import sys
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
        self.assertEqual(report["workers"], feature_metadata_machine_translate.DEFAULT_TRANSLATION_WORKERS)
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

    def test_workers_translate_unique_values_once_per_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "example-asset.metadata.ndjson.gz"
            translations = root / "example-asset.metadata-translations.csv"
            release_feature_model.write_metadata_sidecar(
                [
                    sidecar_record("1", VALID_HASH_A, {"name": "Alpha"}),
                    sidecar_record("2", VALID_HASH_B, {"name": "Alpha"}),
                    sidecar_record("3", "sha256:" + "c" * 64, {"name": "Beta"}),
                ],
                canonical,
            )
            calls: list[tuple[str, str]] = []

            report = feature_metadata_machine_translate.generate_translation_source(
                canonical_sidecar=canonical,
                translation_source=translations,
                locales=["es", "fr"],
                fields=["name"],
                translator_factory=fake_translator_factory(calls),
                sleep_seconds=0,
                workers=2,
            )
            rows = read_csv_rows(translations)

        self.assertEqual(report["workers"], 2)
        self.assertEqual(report["generated_row_count"], 6)
        self.assertEqual(report["translated_unique_value_count"], 4)
        self.assertEqual(sorted(calls), sorted([("es", "Alpha"), ("es", "Beta"), ("fr", "Alpha"), ("fr", "Beta")]))
        self.assertEqual([row["feature_id"] for row in rows], ["1", "1", "2", "2", "3", "3"])
        self.assertEqual(rows[0]["value"], "es:Alpha")
        self.assertEqual(rows[1]["value"], "fr:Alpha")

    def test_rejects_invalid_worker_count(self):
        with self.assertRaisesRegex(
            feature_metadata_machine_translate.FeatureMetadataMachineTranslateError,
            "--workers must be at least 1",
        ):
            feature_metadata_machine_translate.generate_translation_source(
                canonical_sidecar=Path("unused.metadata.ndjson.gz"),
                translation_source=Path("unused.metadata-translations.csv"),
                locales=["es"],
                fields=["name"],
                translator_factory=fake_translator_factory([]),
                workers=0,
            )

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
                    workers=1,
                )

        progress_lines = stderr.getvalue().strip().splitlines()
        self.assertEqual(report["translated_unique_value_count"], 2)
        self.assertEqual(progress_lines[0].split(",")[0], "translation-progress: 0/2 (0.0%)")
        self.assertEqual(progress_lines[-1].split(",")[0], "translation-progress: 2/2 (100.0%)")
        self.assertTrue(all("Alpha" not in line and "Beta" not in line for line in progress_lines))




def machine_fixture(tmp_path, values=("Alpha",)):
    canonical = tmp_path / "example-asset.metadata.ndjson.gz"
    translations = tmp_path / "translations.csv"
    release_feature_model.write_metadata_sidecar([
        sidecar_record(str(index), feature_metadata_localization.source_value_hash(value), {"name": value})
        for index, value in enumerate(values, start=1)
    ], canonical)
    return canonical, translations


def machine_run(canonical, translations, *, fail=False, callback=None, **options):
    calls = []

    class Translator:
        def translate(self, text):
            calls.append(text)
            if callback:
                callback()
            if fail:
                raise RuntimeError("fixture provider unavailable")
            return f"ES:{text}"

    report = feature_metadata_machine_translate.generate_translation_source(
        canonical_sidecar=canonical, translation_source=translations, fields=["name"], locales=["es"],
        translator_factory=lambda target: Translator(), sleep_seconds=0, workers=1,
        max_requests_per_second=0, retry_attempts=0, **options,
    )
    return report, calls


def translation_row(value="Alfa", state="human_reviewed", notes="", canonical="Alpha", feature_id="1"):
    return {"feature_id": feature_id, "field": "name", "locale": "es",
            "source_value_hash": feature_metadata_localization.source_value_hash(canonical),
            "value": value, "review_state": state, "notes": notes}


def test_default_provider_failure_retries_then_completes_once(tmp_path):
    canonical, translations = machine_fixture(tmp_path)
    for _ in range(2):
        report, calls = machine_run(canonical, translations, fail=True)
        assert report["valid"] and not report["complete"]
        assert report["failed_task_count"] == 1
        assert report["translated_unique_value_count"] == 0
        assert calls == ["Alpha"]
        rows = read_csv_rows(translations)
        assert len(rows) == 1 and rows[0]["value"] == ""
        assert rows[0]["review_state"] == "translation_failed"
    output = tmp_path / "localized.gz"
    localization = feature_metadata_localization.materialize_locale_sidecar(
        canonical_sidecar=canonical, translation_source=translations, output_sidecar=output,
        locale="es", translatable_fields={"name"},
    )
    assert localization.failed_translation_count == 1 and localization.applied_translation_count == 0
    assert not localization.to_dict()["requested_rows_complete"]
    assert list(release_feature_model.read_metadata_sidecar(output))[0]["properties"]["name"] == "Alpha"
    pending, _, _ = document.collect_pending_tasks(canonical_sidecar=canonical, translation_source=translations, locales=["es"], fields=["name"])
    assert len(pending) == 1
    report, calls = machine_run(canonical, translations)
    assert report["complete"] and calls == ["Alpha"]
    assert read_csv_rows(translations)[0]["value"] == "ES:Alpha"
    assert machine_run(canonical, translations)[1] == []


@pytest.mark.parametrize("value,state,notes,retry", [
    ("Alpha", "source_provided", "", False),
    ("Alfa human edit", "source_provided", "machine translation failed; source value retained; provider=google; target=es", False),
    ("Alpha", "human_reviewed", "machine translation failed; source value retained; provider=google; target=es", False),
    ("Alpha", "source_provided", "machine translation failed", False),
    ("Alpha", "source_provided", "machine translation failed; source value retained; provider=google; target=es", True),
])
def test_legacy_failure_rule_preserves_human_and_source_provenance(tmp_path, value, state, notes, retry):
    canonical, translations = machine_fixture(tmp_path)
    feature_metadata_machine_translate.write_translation_source(translations, [translation_row(value, state, notes)])
    pending, _, _ = document.collect_pending_tasks(canonical_sidecar=canonical, translation_source=translations, locales=["es"], fields=["name"])
    assert bool(pending) is retry
    report, calls = machine_run(canonical, translations)
    assert bool(calls) is retry
    assert report["complete"]
    assert read_csv_rows(translations)[0]["value"] == ("ES:Alpha" if retry else value)


def test_legacy_stringification_requires_explicit_task_policy(tmp_path):
    canonical, translations = machine_fixture(tmp_path, values=(42,))
    feature_metadata_machine_translate.write_translation_source(translations, [translation_row("42", "source_provided", "machine translation failed; source value retained; provider=google; target=es", canonical=42)])
    assert machine_run(canonical, translations)[1] == []
    assert machine_run(canonical, translations, stringify_non_string=True)[1] == ["42"]


def test_source_change_schedules_new_hash_and_preserves_previous_row(tmp_path):
    canonical, translations = machine_fixture(tmp_path)
    machine_run(canonical, translations, fail=True)
    release_feature_model.write_metadata_sidecar([sidecar_record("1", VALID_HASH_B, {"name": "Changed"})], canonical)
    report, calls = machine_run(canonical, translations)
    assert report["complete"] and calls == ["Changed"]
    assert len(read_csv_rows(translations)) == 2


@pytest.mark.parametrize("changed", ["csv", "canonical"])
def test_edits_while_provider_runs_are_not_overwritten(tmp_path, changed):
    canonical, translations = machine_fixture(tmp_path)
    feature_metadata_machine_translate.write_translation_source(translations, [])

    def edit():
        if changed == "csv":
            feature_metadata_machine_translate.write_translation_source(translations, [translation_row("human while provider runs")])
        else:
            canonical.write_bytes(b"new canonical input")

    with pytest.raises(translation_local_io.TranslationLocalIOError, match="changed"):
        machine_run(canonical, translations, callback=edit)
    rows = read_csv_rows(translations)
    assert rows[0]["value"] == "human while provider runs" if changed == "csv" else rows == []


@pytest.mark.parametrize("mode", ["source", "skip", "fail"])
def test_error_modes_and_cli_exit_status(tmp_path, mode):
    canonical, translations = machine_fixture(tmp_path)
    feature_metadata_machine_translate.write_translation_source(translations, [])
    before = translations.read_bytes()
    report_path = tmp_path / "report.json"

    class Failing:
        def translate(self, text):
            raise RuntimeError("outage")

    args = ["--canonical-sidecar", str(canonical), "--translation-source", str(translations),
            "--locale", "es", "--field", "name", "--on-error", mode, "--retry-attempts", "0",
            "--workers", "1", "--max-rps", "0", "--report", str(report_path)]
    with mock.patch.object(feature_metadata_machine_translate, "deep_translator_factory", return_value=lambda target: Failing()):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = feature_metadata_machine_translate.main(args)
    if mode == "fail":
        assert code == 2 and translations.read_bytes() == before and not report_path.exists()
    else:
        assert code == 1
        assert '"complete": false' in report_path.read_text()
        assert len(read_csv_rows(translations)) == (1 if mode == "source" else 0)
        assert machine_run(canonical, translations)[1] == ["Alpha"]


def test_atomic_csv_validates_before_replacement(tmp_path):
    path = tmp_path / "translations.csv"
    feature_metadata_machine_translate.write_translation_source(path, [translation_row()])
    before = path.read_bytes()
    with pytest.raises(feature_metadata_localization.FeatureMetadataLocalizationError, match="duplicate"):
        feature_metadata_machine_translate.write_translation_source(path, [translation_row(), translation_row()])
    assert path.read_bytes() == before
    with mock.patch.object(feature_metadata_machine_translate.csv.DictWriter, "writerow", side_effect=OSError("interrupted CSV")):
        with pytest.raises(OSError):
            feature_metadata_machine_translate.write_translation_source(path, [translation_row("new")])
    assert path.read_bytes() == before


def test_provider_cannot_overwrite_report_edited_during_work(tmp_path):
    canonical, translations = machine_fixture(tmp_path)
    report_path = tmp_path / "report.json"
    report_path.write_text('{"original": true}')

    class EditingProvider:
        def translate(self, text):
            report_path.write_text('{"human": true}')
            return "Alfa"

    args = ["--canonical-sidecar", str(canonical), "--translation-source", str(translations),
            "--locale", "es", "--field", "name", "--workers", "1", "--max-rps", "0",
            "--report", str(report_path)]
    with mock.patch.object(feature_metadata_machine_translate, "deep_translator_factory", return_value=lambda target: EditingProvider()):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            assert feature_metadata_machine_translate.main(args) == 2
    assert json.loads(report_path.read_text()) == {"human": True}
    # CSV committed first: explicitly per-file, with no rollback claim.
    assert read_csv_rows(translations)[0]["value"] == "Alfa"


def test_machine_cli_success_prints_one_json_document(tmp_path):
    canonical, translations = machine_fixture(tmp_path)
    report_path = tmp_path / "report.json"
    args = ["--canonical-sidecar", str(canonical), "--translation-source", str(translations),
            "--locale", "es", "--field", "name", "--workers", "1", "--max-rps", "0", "--report", str(report_path)]
    stdout = io.StringIO()
    with mock.patch.object(feature_metadata_machine_translate, "deep_translator_factory", return_value=fake_translator_factory([])):
        with contextlib.redirect_stdout(stdout):
            assert feature_metadata_machine_translate.main(args) == 0
    payload = json.loads(stdout.getvalue())
    assert payload == json.loads(report_path.read_text())
    assert payload["complete"]


def test_oversized_csv_is_validation_exit_two_without_replacement(tmp_path):
    canonical, translations = machine_fixture(tmp_path)
    with translations.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(feature_metadata_machine_translate.TRANSLATION_COLUMNS))
        writer.writeheader()
        writer.writerow(translation_row("x" * (csv.field_size_limit() + 1)))
    before = translations.read_bytes()
    report = tmp_path / "report.json"
    result = subprocess.run([
        sys.executable, str(Path(feature_metadata_machine_translate.__file__)),
        "--canonical-sidecar", str(canonical), "--translation-source", str(translations),
        "--locale", "es", "--field", "name", "--report", str(report),
    ], capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "field larger than field limit" in result.stderr and "Traceback" not in result.stderr
    assert translations.read_bytes() == before and not report.exists()


def test_one_provider_failure_counts_each_affected_task(tmp_path):
    canonical, translations = machine_fixture(tmp_path)
    release_feature_model.write_metadata_sidecar([
        sidecar_record("1", VALID_HASH_A, {"name": "Alpha"}),
        sidecar_record("2", VALID_HASH_B, {"name": "Alpha"}),
    ], canonical)
    report, calls = machine_run(canonical, translations, fail=True)
    assert calls == ["Alpha"] and report["translation_failure_count"] == 1
    assert report["failed_task_count"] == 2 and report["successful_task_count"] == 0


if __name__ == "__main__":
    unittest.main()
