#!/usr/bin/env python3
"""Materialize locale-specific feature metadata sidecar views."""

from __future__ import annotations

import argparse
import csv
from contextlib import contextmanager
import gzip
import json
import re
import sqlite3
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from itertools import zip_longest
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import release_feature_model, translation_local_io  # noqa: E402


TRANSLATION_SOURCE_SCHEMA = "metadata_translation_csv_v1"
REQUIRED_TRANSLATION_COLUMNS = ("feature_id", "field", "locale", "source_value_hash", "value")
OPTIONAL_TRANSLATION_COLUMNS = ("review_state", "notes")
FIELD_SAFE_LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:_[a-z0-9]{2,8})*$")
FAILED_REVIEW_STATE = "translation_failed"
MACHINE_REVIEW_STATES = frozenset({"machine_placeholder", "machine_translated", "document_translated"})
DEBT_COLUMNS = ("feature_id", "field", "locale", "source_value_hash", "source_value")
LEGACY_FAILURE_NOTES_RE = re.compile(r"machine translation failed; source value retained; provider=google; target=[A-Za-z][A-Za-z0-9_-]*")
SOURCE_VALUE_HASH_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")


class FeatureMetadataLocalizationError(ValueError):
    """Raised when localized metadata sidecars cannot be materialized."""


@dataclass(frozen=True)
class TranslationRow:
    row_number: int
    feature_id: str
    field: str
    locale: str
    source_value_hash: str
    value: str
    review_state: str = ""
    notes: str = ""

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.feature_id, self.field, self.locale, self.source_value_hash)


@dataclass
class LocalizationReport:
    locale: str
    canonical_sidecar: str
    translation_source: str
    output_sidecar: str
    translatable_fields: list[str]
    feature_count: int = 0
    applied_translation_count: int = 0
    stale_translation_count: int = 0
    orphan_translation_count: int = 0
    missing_field_translation_count: int = 0
    untranslated_feature_count: int = 0
    stale_translations: list[dict[str, Any]] = field(default_factory=list)
    orphan_translations: list[dict[str, Any]] = field(default_factory=list)
    missing_field_translations: list[dict[str, Any]] = field(default_factory=list)
    failed_translation_count: int = 0
    failed_translations: list[dict[str, Any]] = field(default_factory=list)
    translatable_values: int = 0
    current: int = 0
    stale: int = 0
    missing: int = 0
    review_states: dict[str, int] = field(default_factory=dict)
    debt_path: str = ""

    def coverage(self) -> dict[str, Any]:
        assert self.current + self.stale + self.missing == self.translatable_values
        return {
            "translatable_values": self.translatable_values,
            "current": self.current, "stale": self.stale, "missing": self.missing,
            "orphan": self.orphan_translation_count,
            "removed_fields": self.missing_field_translation_count,
            "coverage": self.current / self.translatable_values if self.translatable_values else None,
            "review_states": dict(sorted(self.review_states.items())),
        }

    @property
    def valid(self) -> bool:
        return True

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["valid"] = self.valid
        payload["translation_source_schema"] = TRANSLATION_SOURCE_SCHEMA
        unresolved = self.failed_translation_count + self.stale_translation_count + self.orphan_translation_count + self.missing_field_translation_count
        payload["unresolved_translation_count"] = unresolved
        payload["requested_rows_complete"] = unresolved == 0
        payload["coverage"] = self.coverage()
        return payload


def parse_locale_arguments(values: Sequence[str]) -> list[str]:
    locales: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        for part in str(raw_value or "").split(","):
            if not part.strip():
                continue
            locale = normalize_locale(part)
            if locale in seen:
                continue
            locales.append(locale)
            seen.add(locale)
    return locales


def normalize_locale(value: Any) -> str:
    locale = str(value or "").strip().lower().replace("-", "_")
    if not locale or not FIELD_SAFE_LOCALE_RE.fullmatch(locale):
        raise FeatureMetadataLocalizationError(
            "locale must be a lower-case field-safe BCP 47 code such as es, fr, pt_br, or zh_hans"
        )
    return locale


def source_value_hash(value: Any) -> str:
    """Hash the canonical source property value used by a translation row."""
    return "sha256:" + release_feature_model.sha256_hex(release_feature_model.canonical_json(value))


def normalize_source_value_hash(value: Any, *, context: str) -> str:
    digest = str(value or "").strip().lower()
    if not SOURCE_VALUE_HASH_RE.fullmatch(digest):
        raise FeatureMetadataLocalizationError(f"{context}: source_value_hash must be sha256: plus 64 lowercase hex chars")
    return digest if digest.startswith("sha256:") else f"sha256:{digest}"


def iter_translation_source(
    path: Path,
    *,
    translatable_fields: set[str] | None = None,
) -> Iterable[TranslationRow]:
    if not path.exists():
        raise FeatureMetadataLocalizationError(f"translation source does not exist: {path}")
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        if not fieldnames:
            raise FeatureMetadataLocalizationError(f"translation source is empty: {path}")
        duplicates = sorted({name for name in fieldnames if fieldnames.count(name) > 1})
        if duplicates:
            raise FeatureMetadataLocalizationError("translation source has duplicate column(s): " + ", ".join(duplicates))
        missing = [name for name in REQUIRED_TRANSLATION_COLUMNS if name not in fieldnames]
        if missing:
            raise FeatureMetadataLocalizationError("translation source is missing required column(s): " + ", ".join(missing))
        unsupported = sorted(set(fieldnames) - set(REQUIRED_TRANSLATION_COLUMNS) - set(OPTIONAL_TRANSLATION_COLUMNS))
        if unsupported:
            raise FeatureMetadataLocalizationError("translation source has unsupported column(s): " + ", ".join(unsupported))

        for row_number, row in enumerate(reader, start=2):
            errors: list[str] = []
            if None in row or any(value is None for value in row.values()):
                errors.append(f"row {row_number}: CSV row width does not match header")
            feature_id = str(row.get("feature_id") or "").strip()
            field_name = str(row.get("field") or "").strip()
            value = str(row.get("value") or "")
            if not feature_id:
                errors.append(f"row {row_number}: feature_id is required")
            else:
                try:
                    release_feature_model.validate_feature_id(feature_id)
                except release_feature_model.ReleaseFeatureModelError as exc:
                    errors.append(f"row {row_number}: {exc}")
            if not field_name:
                errors.append(f"row {row_number}: field is required")
            elif translatable_fields is not None and field_name not in translatable_fields:
                errors.append(f"row {row_number}: field {field_name!r} is not in the translatable-field allowlist")
            review_state = str(row.get("review_state") or "").strip()
            if review_state == FAILED_REVIEW_STATE:
                if value != "":
                    errors.append(f"row {row_number}: translation_failed must have empty value; change review_state when completing a translation")
            elif not value.strip():
                errors.append(f"row {row_number}: value is required for successful translations")
            try:
                locale = normalize_locale(row.get("locale"))
            except FeatureMetadataLocalizationError as exc:
                errors.append(f"row {row_number}: {exc}")
                locale = ""
            try:
                digest = normalize_source_value_hash(row.get("source_value_hash"), context=f"row {row_number}")
            except FeatureMetadataLocalizationError as exc:
                errors.append(str(exc))
                digest = ""
            translation = TranslationRow(
                row_number=row_number,
                feature_id=feature_id,
                field=field_name,
                locale=locale,
                source_value_hash=digest,
                value=value,
                review_state=review_state,
                notes=str(row.get("notes") or "").strip(),
            )
            if errors:
                raise FeatureMetadataLocalizationError("; ".join(errors))
            yield translation


@contextmanager
def translation_index(path: Path, *, translatable_fields: set[str] | None = None):
    """One bounded-memory CSV boundary; the unique key also validates history."""
    with tempfile.TemporaryDirectory(prefix="translation-index-", dir=path.parent) as directory:
        db = sqlite3.connect(Path(directory) / "rows.sqlite")
        try:
            db.executescript("""
                PRAGMA journal_mode=OFF;
                PRAGMA cache_size=-32768;
                PRAGMA temp_store=FILE;
                CREATE TABLE rows (
                    row_number INTEGER, feature_id TEXT, field TEXT, locale TEXT,
                    source_value_hash TEXT, value TEXT, review_state TEXT, notes TEXT,
                    PRIMARY KEY(locale, feature_id, field, source_value_hash)
                ) WITHOUT ROWID;
                CREATE TABLE seen (feature_id TEXT PRIMARY KEY) WITHOUT ROWID;
            """)
            try:
                db.executemany("INSERT INTO rows VALUES (?,?,?,?,?,?,?,?)", (
                    tuple(asdict(row).values()) for row in iter_translation_source(path, translatable_fields=translatable_fields)
                ))
            except sqlite3.IntegrityError as exc:
                raise FeatureMetadataLocalizationError("duplicate translation key") from exc
            db.commit()
            yield db
        finally:
            db.close()


def read_translation_source(path: Path, *, translatable_fields: set[str] | None = None) -> list[TranslationRow]:
    # Compatibility for interactive machine/document translation tools. Release
    # materialization reads the same validated index without collecting all rows.
    with translation_index(path, translatable_fields=translatable_fields) as db:
        return [TranslationRow(*row) for row in db.execute("SELECT * FROM rows ORDER BY row_number")]


def translation_failed(row: TranslationRow | Mapping[str, Any], current_value: Any) -> bool:
    """Recognize explicit failures and only provable untouched legacy fallbacks."""
    state = row.review_state if isinstance(row, TranslationRow) else row.get("review_state", "")
    if state == FAILED_REVIEW_STATE:
        return True
    notes = row.notes if isinstance(row, TranslationRow) else row.get("notes", "")
    value = row.value if isinstance(row, TranslationRow) else row.get("value", "")
    digest = row.source_value_hash if isinstance(row, TranslationRow) else row.get("source_value_hash", "")
    if state != "source_provided" or not LEGACY_FAILURE_NOTES_RE.fullmatch(notes):
        return False
    # Old non-string opt-in used str(raw), but keyed the canonical JSON value.
    # Checking both preserves human edits even when the old notes remain.
    return current_value is not None and digest == source_value_hash(current_value) and value == str(current_value)


def translatable_fields_from_schema(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise FeatureMetadataLocalizationError(f"release schema must be a JSON object: {path}")
    return set(release_feature_model.validate_release_schema(payload))


def resolved_translatable_fields(*, schema: Path | None, fields: Sequence[str]) -> set[str]:
    schema_fields = translatable_fields_from_schema(schema) if schema else set()
    explicit_fields = {field.strip() for field in fields if field.strip()}
    if schema_fields and explicit_fields:
        unknown = sorted(explicit_fields - schema_fields)
        if unknown:
            raise FeatureMetadataLocalizationError(
                "translatable field(s) are not projectable in the release schema: " + ", ".join(unknown)
            )
        return explicit_fields
    if explicit_fields:
        return explicit_fields
    if schema_fields:
        return schema_fields
    raise FeatureMetadataLocalizationError("--schema or at least one --translatable-field is required")


def localized_sidecar_path(*, canonical_sidecar: Path, output_dir: Path, locale: str) -> Path:
    canonical_name = canonical_sidecar.name
    if not canonical_name.endswith(".metadata.ndjson.gz"):
        raise FeatureMetadataLocalizationError(
            "canonical sidecar filename must end with .metadata.ndjson.gz to derive localized sidecar names"
        )
    stem = canonical_name[: -len(".metadata.ndjson.gz")]
    return output_dir / f"{stem}.metadata.{normalize_locale(locale)}.ndjson.gz"


def _translation_summary(row: TranslationRow, *, expected_hash: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "row_number": row.row_number,
        "feature_id": row.feature_id,
        "field": row.field,
        "locale": row.locale,
        "source_value_hash": row.source_value_hash,
    }
    if expected_hash is not None:
        payload["current_source_value_hash"] = expected_hash
    if row.review_state:
        payload["review_state"] = row.review_state
    return payload


def localize_record(
    record: Mapping[str, Any], *, rows: Sequence[TranslationRow],
    report: LocalizationReport, debt: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Classify each eligible value once, independently of translation history."""
    properties = record["properties"]
    localized = dict(properties)
    feature_id = record["feature_id"]
    by_field: dict[str, list[TranslationRow]] = {}
    report.feature_count += 1
    for row in rows:
        if row.field not in properties or row.field not in report.translatable_fields:
            report.missing_field_translation_count += 1
            if len(report.missing_field_translations) < 25:
                report.missing_field_translations.append(_translation_summary(row))
            continue
        by_field.setdefault(row.field, []).append(row)
        digest = source_value_hash(properties[row.field])
        if translation_failed(row, properties[row.field]):
            report.failed_translation_count += 1
            if len(report.failed_translations) < 25:
                report.failed_translations.append(_translation_summary(row))
        elif row.source_value_hash != digest:
            report.stale_translation_count += 1
            if len(report.stale_translations) < 25:
                report.stale_translations.append(_translation_summary(row, expected_hash=digest))

    translated, fallback, machine, reviewed = [], [], [], []
    for name in report.translatable_fields:
        original = properties.get(name)
        eligible = isinstance(original, str) and bool(original.strip())
        if original is None or isinstance(original, str) and not eligible:
            continue
        digest = source_value_hash(original)
        candidates = by_field.get(name, ())
        current = next((row for row in candidates if row.source_value_hash == digest), None)
        usable = current is not None and not translation_failed(current, original)
        # Preserve explicit non-string translations created by the public
        # --stringify-non-string tool. Only nonblank strings enter coverage/debt.
        if not eligible and not usable:
            continue
        report.translatable_values += int(eligible)
        if usable:
            localized[name] = current.value
            translated.append(name)
            report.applied_translation_count += 1
            state = current.review_state or "unknown"
            if eligible:
                report.current += 1
                report.review_states[state] = report.review_states.get(state, 0) + 1
            if state in MACHINE_REVIEW_STATES:
                machine.append(name)
            elif state == "human_reviewed":
                reviewed.append(name)
            continue
        fallback.append(name)
        # A current failed task is missing, even if older successful rows remain.
        stale = current is None and any(not translation_failed(row, original) for row in candidates)
        if stale:
            report.stale += 1
        else:
            report.missing += 1
        if debt is not None:
            debt(dict(zip(DEBT_COLUMNS, (feature_id, name, report.locale, digest, original))))
    if not translated:
        report.untranslated_feature_count += 1
    return {
        **record, "properties": localized,
        "translation": {
            "locale": report.locale,
            "state": "complete" if not fallback else "partial" if translated else "fallback",
            "translated_fields": translated, "fallback_fields": fallback,
            "machine_fields": machine, "human_reviewed_fields": reviewed,
        },
    }


def indexed_localized_records(canonical_sidecar, db, report, debt=None):
    for record in release_feature_model.read_metadata_sidecar(canonical_sidecar):
        rows = [TranslationRow(*row) for row in db.execute(
            "SELECT * FROM rows WHERE locale=? AND feature_id=? ORDER BY field,source_value_hash",
            (report.locale, record["feature_id"]),
        )]
        db.execute("INSERT OR IGNORE INTO seen VALUES (?)", (record["feature_id"],))
        yield localize_record(record, rows=rows, report=report, debt=debt)
    for row in db.execute("SELECT * FROM rows WHERE locale=? AND feature_id NOT IN (SELECT feature_id FROM seen)", (report.locale,)):
        report.orphan_translation_count += 1
        if len(report.orphan_translations) < 25:
            report.orphan_translations.append(_translation_summary(TranslationRow(*row)))


def translations_payload(reports: Sequence[LocalizationReport]) -> dict[str, Any]:
    return {"schema_version": 1, "locales": {report.locale: report.coverage() for report in reports}}


def validate_sidecar(path: Path, *, expected_asset_slug=None, expected_release=None):
    """Validate large sidecars without retaining every feature ID in memory."""
    from ingestion.common.identity_index import DiskIdentityRecords

    with tempfile.TemporaryDirectory(prefix="translation-validation-", dir=path.parent) as directory:
        index = DiskIdentityRecords(Path(directory) / "identities.sqlite")
        try:
            return release_feature_model.validate_sidecar_records(
                release_feature_model.read_metadata_sidecar(path),
                expected_asset_slug=expected_asset_slug, expected_release=expected_release,
                identity_index=index,
            )
        finally:
            index.close()


def debt_file_path(output_sidecar: Path, locale: str) -> Path:
    stem = output_sidecar.name.removesuffix(f".metadata.{normalize_locale(locale)}.ndjson.gz")
    return output_sidecar.with_name(f"{stem}.translation-debt.{normalize_locale(locale)}.csv")


def materialize_locale_sidecar(
    *,
    canonical_sidecar: Path,
    translation_source: Path,
    output_sidecar: Path,
    locale: str,
    translatable_fields: set[str],
    expected_asset_slug: str | None = None,
    expected_release: str | None = None,
    fail_on_stale: bool = False,
    protected_inputs: Sequence[Path] = (),
    expected_output: translation_local_io.FileSnapshot | None = None,
    input_snapshots: Sequence[translation_local_io.FileSnapshot] = (),
) -> LocalizationReport:
    expected = translation_local_io.snapshots([canonical_sidecar, translation_source, *protected_inputs], observed=input_snapshots)
    expected_output = expected_output or translation_local_io.FileSnapshot.capture(output_sidecar)
    expected_debt = translation_local_io.FileSnapshot.capture(debt_file_path(output_sidecar, locale))
    with translation_index(translation_source) as db:
        return _materialize_locale_sidecar(
            db=db, canonical_sidecar=canonical_sidecar, translation_source=translation_source,
            output_sidecar=output_sidecar, locale=locale, translatable_fields=translatable_fields,
            expected_asset_slug=expected_asset_slug, expected_release=expected_release,
            fail_on_stale=fail_on_stale, protected_inputs=protected_inputs,
            expected_output=expected_output, expected_debt=expected_debt, input_snapshots=expected,
        )


def _materialize_locale_sidecar(
    *, db: sqlite3.Connection, canonical_sidecar: Path, translation_source: Path,
    output_sidecar: Path, locale: str, translatable_fields: set[str],
    expected_asset_slug: str | None, expected_release: str | None,
    fail_on_stale: bool, protected_inputs: Sequence[Path],
    expected_output: translation_local_io.FileSnapshot | None,
    expected_debt: translation_local_io.FileSnapshot,
    input_snapshots: Sequence[translation_local_io.FileSnapshot],
) -> LocalizationReport:
    inputs = [canonical_sidecar, translation_source, *protected_inputs]
    translation_local_io.validate_paths(inputs=inputs, outputs=[output_sidecar])
    expected = (*translation_local_io.snapshots(inputs, observed=input_snapshots), expected_output or translation_local_io.FileSnapshot.capture(output_sidecar))
    normalized_locale = normalize_locale(locale)
    source_validation = validate_sidecar(
        canonical_sidecar,
        expected_asset_slug=expected_asset_slug, expected_release=expected_release,
    )
    if not source_validation.valid:
        raise FeatureMetadataLocalizationError("canonical sidecar validation failed: " + "; ".join(source_validation.errors))
    debt_path = debt_file_path(output_sidecar, normalized_locale)
    translation_local_io.validate_paths(inputs=inputs, outputs=[output_sidecar, debt_path])
    report = LocalizationReport(normalized_locale, str(canonical_sidecar), str(translation_source), str(output_sidecar), sorted(translatable_fields), debt_path=str(debt_path))
    with translation_local_io.candidate_output(output_sidecar, expected=expected, protected=inputs) as candidate, \
         translation_local_io.candidate_output(debt_path, expected=[*expected, expected_debt], protected=inputs) as debt_candidate:
        with debt_candidate.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=DEBT_COLUMNS, lineterminator="\n")
            writer.writeheader()
            records = indexed_localized_records(canonical_sidecar, db, report, writer.writerow)
            release_feature_model.write_metadata_sidecar(records, candidate)
        validation = validate_sidecar(
            candidate,
            expected_asset_slug=expected_asset_slug, expected_release=expected_release,
        )
        if not validation.valid:
            raise FeatureMetadataLocalizationError("localized sidecar validation failed: " + "; ".join(validation.errors))
        if validation.feature_count != source_validation.feature_count:
            raise FeatureMetadataLocalizationError("localized sidecar row count does not match canonical sidecar")
        comparison_report = LocalizationReport(normalized_locale, str(canonical_sidecar), str(translation_source), str(output_sidecar), sorted(translatable_fields))
        expected_records = indexed_localized_records(canonical_sidecar, db, comparison_report)
        for expected_record, actual in zip_longest(expected_records, release_feature_model.read_metadata_sidecar(candidate)):
            if expected_record != actual:
                raise FeatureMetadataLocalizationError("localized sidecar identity or properties differ from canonical derivation")
        if fail_on_stale and report.stale_translation_count:
            raise FeatureMetadataLocalizationError(f"{report.stale_translation_count} stale translation(s) found for locale {normalized_locale}")
    return report


def materialize_locale_sidecars(
    *,
    canonical_sidecar: Path,
    translation_source: Path,
    output_dir: Path,
    locales: Sequence[str] | None,
    translatable_fields: set[str],
    expected_asset_slug: str | None = None,
    expected_release: str | None = None,
    fail_on_stale: bool = False,
    report_dir: Path | None = None,
    protected_inputs: Sequence[Path] = (),
    reserved_outputs: Sequence[Path] = (),
    input_snapshots: Sequence[translation_local_io.FileSnapshot] = (),
) -> list[LocalizationReport]:
    inputs = [canonical_sidecar, translation_source, *protected_inputs]
    expected = translation_local_io.snapshots(inputs, observed=input_snapshots)
    with translation_index(translation_source) as db:
        selected_locales = parse_locale_arguments(locales or [])
        if not selected_locales:
            selected_locales = [row[0] for row in db.execute("SELECT DISTINCT locale FROM rows ORDER BY locale")]
        if not selected_locales:
            raise FeatureMetadataLocalizationError("translation source does not contain any locales")

        outputs = [localized_sidecar_path(canonical_sidecar=canonical_sidecar, output_dir=output_dir, locale=locale) for locale in selected_locales]
        debt_paths = [debt_file_path(path, locale) for path, locale in zip(outputs, selected_locales)]
        report_paths = [report_dir / f"{path.name}.report.json" for path in outputs] if report_dir else []
        translation_local_io.validate_paths(inputs=inputs, outputs=[*outputs, *debt_paths, *report_paths, *reserved_outputs])
        destinations = {path: translation_local_io.FileSnapshot.capture(path) for path in [*outputs, *debt_paths, *report_paths]}
        reports: list[LocalizationReport] = []
        for locale, output_sidecar, debt_path in zip(selected_locales, outputs, debt_paths):
            for snapshot in expected:
                snapshot.verify()
            report = _materialize_locale_sidecar(
                db=db, canonical_sidecar=canonical_sidecar,
                translation_source=translation_source,
                output_sidecar=output_sidecar,
                locale=locale,
                translatable_fields=translatable_fields,
                expected_asset_slug=expected_asset_slug,
                expected_release=expected_release,
                fail_on_stale=fail_on_stale,
                protected_inputs=protected_inputs,
                expected_output=destinations[output_sidecar],
                expected_debt=destinations[debt_path],
                input_snapshots=expected,
            )
            reports.append(report)
            if report_dir:
                report_path = report_dir / f"{output_sidecar.name}.report.json"
                translation_local_io.write_json(report_path, report.to_dict(), protected=[*inputs, *outputs, *debt_paths, *reserved_outputs], expected=[destinations[report_path]])
    return reports


def batch_report_payload(
    *,
    canonical_sidecar: Path,
    translation_source: Path,
    output_dir: Path,
    reports: Sequence[LocalizationReport],
) -> dict[str, Any]:
    return {
        "valid": all(report.valid for report in reports),
        "translation_source_schema": TRANSLATION_SOURCE_SCHEMA,
        "canonical_sidecar": str(canonical_sidecar),
        "translation_source": str(translation_source),
        "output_dir": str(output_dir),
        "locales": [report.locale for report in reports],
        "report_count": len(reports),
        "reports": [report.to_dict() for report in reports],
        "translations": translations_payload(reports),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-sidecar", required=True, type=Path)
    parser.add_argument("--translation-source", required=True, type=Path)
    parser.add_argument("--output-sidecar", type=Path, help="Single localized sidecar output path.")
    parser.add_argument("--output-dir", type=Path, help="Output directory for generated .metadata.{locale}.ndjson.gz files.")
    parser.add_argument("--locale", action="append", default=[], help="Locale to generate. May be repeated or comma-separated.")
    parser.add_argument("--all-locales", action="store_true", help="Generate every locale present in the translation source.")
    parser.add_argument("--schema", type=Path, help="Release schema JSON. Projectable fields become the allowlist.")
    parser.add_argument(
        "--translatable-field",
        action="append",
        default=[],
        help="Field allowed to be translated. May be repeated; narrows --schema when both are provided.",
    )
    parser.add_argument("--asset-slug", help="Expected asset slug for sidecar validation.")
    parser.add_argument("--release", help="Expected release date for sidecar validation.")
    parser.add_argument("--report", type=Path, help="Optional JSON report path. Prints to stdout when omitted.")
    parser.add_argument("--report-dir", type=Path, help="Optional directory for one JSON report per generated locale.")
    parser.add_argument("--fail-on-stale", action="store_true", help="Refuse replacement if stale translations were detected.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report_expected = translation_local_io.snapshots([args.report]) if args.report else ()
        input_expected = translation_local_io.snapshots([args.canonical_sidecar, args.translation_source, *([args.schema] if args.schema else [])])
        output_expected = translation_local_io.FileSnapshot.capture(args.output_sidecar) if args.output_sidecar else None
        fields = resolved_translatable_fields(schema=args.schema, fields=args.translatable_field)
        locales = parse_locale_arguments(args.locale)
        batch_mode = args.all_locales or args.output_dir is not None or args.report_dir is not None
        if batch_mode:
            if not args.output_dir:
                raise FeatureMetadataLocalizationError("--output-dir is required when generating multiple locale sidecars")
            reports = materialize_locale_sidecars(
                canonical_sidecar=args.canonical_sidecar,
                translation_source=args.translation_source,
                output_dir=args.output_dir,
                locales=locales,
                translatable_fields=fields,
                expected_asset_slug=args.asset_slug,
                expected_release=args.release,
                fail_on_stale=args.fail_on_stale,
                report_dir=args.report_dir,
                protected_inputs=[args.schema] if args.schema else [],
                reserved_outputs=[args.report] if args.report else [],
                input_snapshots=input_expected,
            )
            payload_obj: dict[str, Any] = batch_report_payload(
                canonical_sidecar=args.canonical_sidecar,
                translation_source=args.translation_source,
                output_dir=args.output_dir,
                reports=reports,
            )
        else:
            if not args.output_sidecar:
                raise FeatureMetadataLocalizationError("--output-sidecar is required for single-locale generation")
            if len(locales) != 1:
                raise FeatureMetadataLocalizationError("exactly one --locale is required for single-locale generation")
            translation_local_io.validate_paths(
                inputs=[args.canonical_sidecar, args.translation_source, *([args.schema] if args.schema else [])],
                outputs=[args.output_sidecar, debt_file_path(args.output_sidecar, locales[0]), *([args.report] if args.report else [])],
            )
            report = materialize_locale_sidecar(
                canonical_sidecar=args.canonical_sidecar,
                translation_source=args.translation_source,
                output_sidecar=args.output_sidecar,
                locale=locales[0],
                translatable_fields=fields,
                expected_asset_slug=args.asset_slug,
                expected_release=args.release,
                fail_on_stale=args.fail_on_stale,
                protected_inputs=[args.schema] if args.schema else [],
                input_snapshots=input_expected,
                expected_output=output_expected,
            )
            payload_obj = report.to_dict()
        if args.report:
            protected = [args.canonical_sidecar, args.translation_source, *([args.schema] if args.schema else [])]
            if batch_mode:
                protected.extend(Path(report.output_sidecar) for report in reports)
                protected.extend(Path(report.debt_path) for report in reports)
            else:
                protected.append(args.output_sidecar)
                protected.append(Path(report.debt_path))
            translation_local_io.write_json(args.report, payload_obj, protected=protected, expected=report_expected)
        else:
            print(json.dumps(payload_obj, indent=2, sort_keys=True))
    except (FeatureMetadataLocalizationError, release_feature_model.ReleaseFeatureModelError, OSError, csv.Error, json.JSONDecodeError) as exc:
        print(f"feature-metadata-localization failed: {exc}; per-file commits completed earlier may remain", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
