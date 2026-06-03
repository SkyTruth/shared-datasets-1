#!/usr/bin/env python3
"""Materialize locale-specific feature metadata sidecar views."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import release_feature_model  # noqa: E402


TRANSLATION_SOURCE_SCHEMA = "metadata_translation_csv_v1"
REQUIRED_TRANSLATION_COLUMNS = ("feature_id", "field", "locale", "source_value_hash", "value")
OPTIONAL_TRANSLATION_COLUMNS = ("review_state", "notes")
FIELD_SAFE_LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:_[a-z0-9]{2,8})*$")
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

    @property
    def valid(self) -> bool:
        return True

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["valid"] = self.valid
        payload["translation_source_schema"] = TRANSLATION_SOURCE_SCHEMA
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


def read_translation_source(
    path: Path,
    *,
    translatable_fields: set[str],
) -> list[TranslationRow]:
    if not path.exists():
        raise FeatureMetadataLocalizationError(f"translation source does not exist: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
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

        rows: list[TranslationRow] = []
        errors: list[str] = []
        seen_keys: dict[tuple[str, str, str, str], int] = {}
        for row_number, row in enumerate(reader, start=2):
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
            elif field_name not in translatable_fields:
                errors.append(f"row {row_number}: field {field_name!r} is not in the translatable-field allowlist")
            if value == "":
                errors.append(f"row {row_number}: value is required; omit untranslated values so canonical values fall back")
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
                review_state=str(row.get("review_state") or "").strip(),
                notes=str(row.get("notes") or "").strip(),
            )
            key = translation.key
            if all(key):
                previous = seen_keys.get(key)
                if previous is not None:
                    errors.append(f"row {row_number}: duplicate translation key first seen on row {previous}")
                seen_keys[key] = row_number
            rows.append(translation)
    if errors:
        raise FeatureMetadataLocalizationError("; ".join(errors))
    return rows


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


def translations_by_feature(rows: Iterable[TranslationRow], *, locale: str) -> dict[str, list[TranslationRow]]:
    grouped: dict[str, list[TranslationRow]] = {}
    for row in rows:
        if row.locale != locale:
            continue
        grouped.setdefault(row.feature_id, []).append(row)
    return grouped


def locales_from_translation_rows(rows: Iterable[TranslationRow]) -> list[str]:
    return sorted({row.locale for row in rows})


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


def iter_localized_records(
    canonical_records: Iterable[Mapping[str, Any]],
    *,
    grouped_translations: Mapping[str, Sequence[TranslationRow]],
    report: LocalizationReport,
) -> Iterable[dict[str, Any]]:
    seen_feature_ids: set[str] = set()
    for record in canonical_records:
        payload = dict(record)
        feature_id = str(payload.get("feature_id") or "").strip()
        feature_hash = str(payload.get("feature_hash") or "").strip()
        properties = payload.get("properties")
        if not isinstance(properties, Mapping):
            raise FeatureMetadataLocalizationError(f"record {report.feature_count + 1}: properties must be an object")
        localized_properties = dict(properties)
        applied_fields: set[str] = set()
        report.feature_count += 1
        seen_feature_ids.add(feature_id)
        for translation in grouped_translations.get(feature_id, ()):
            if translation.field not in properties:
                report.missing_field_translation_count += 1
                report.missing_field_translations.append(_translation_summary(translation))
                continue
            current_hash = source_value_hash(properties.get(translation.field))
            if translation.source_value_hash != current_hash:
                report.stale_translation_count += 1
                report.stale_translations.append(_translation_summary(translation, expected_hash=current_hash))
                continue
            localized_properties[translation.field] = translation.value
            applied_fields.add(translation.field)
            report.applied_translation_count += 1
        if not applied_fields:
            report.untranslated_feature_count += 1
        payload["feature_id"] = feature_id
        payload["feature_hash"] = feature_hash
        payload["properties"] = localized_properties
        yield payload

    for feature_id, rows in grouped_translations.items():
        if feature_id in seen_feature_ids:
            continue
        for row in rows:
            report.orphan_translation_count += 1
            report.orphan_translations.append(_translation_summary(row))


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
) -> LocalizationReport:
    normalized_locale = normalize_locale(locale)
    rows = read_translation_source(translation_source, translatable_fields=translatable_fields)
    grouped = translations_by_feature(rows, locale=normalized_locale)
    report = LocalizationReport(
        locale=normalized_locale,
        canonical_sidecar=str(canonical_sidecar),
        translation_source=str(translation_source),
        output_sidecar=str(output_sidecar),
        translatable_fields=sorted(translatable_fields),
    )
    records = iter_localized_records(
        release_feature_model.read_metadata_sidecar(canonical_sidecar),
        grouped_translations=grouped,
        report=report,
    )
    release_feature_model.write_metadata_sidecar(records, output_sidecar)
    validation = release_feature_model.validate_sidecar_records(
        release_feature_model.read_metadata_sidecar(output_sidecar),
        expected_asset_slug=expected_asset_slug,
        expected_release=expected_release,
    )
    if not validation.valid:
        raise FeatureMetadataLocalizationError("localized sidecar validation failed: " + "; ".join(validation.errors))
    if validation.feature_count != report.feature_count:
        raise FeatureMetadataLocalizationError("localized sidecar row count does not match canonical sidecar")
    if fail_on_stale and report.stale_translation_count:
        raise FeatureMetadataLocalizationError(
            f"{report.stale_translation_count} stale translation(s) found for locale {normalized_locale}"
        )
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
) -> list[LocalizationReport]:
    rows = read_translation_source(translation_source, translatable_fields=translatable_fields)
    selected_locales = parse_locale_arguments(locales or [])
    if not selected_locales:
        selected_locales = locales_from_translation_rows(rows)
    if not selected_locales:
        raise FeatureMetadataLocalizationError("translation source does not contain any locales")

    reports: list[LocalizationReport] = []
    for locale in selected_locales:
        output_sidecar = localized_sidecar_path(canonical_sidecar=canonical_sidecar, output_dir=output_dir, locale=locale)
        report = materialize_locale_sidecar(
            canonical_sidecar=canonical_sidecar,
            translation_source=translation_source,
            output_sidecar=output_sidecar,
            locale=locale,
            translatable_fields=translatable_fields,
            expected_asset_slug=expected_asset_slug,
            expected_release=expected_release,
            fail_on_stale=fail_on_stale,
        )
        reports.append(report)
        if report_dir:
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = report_dir / f"{output_sidecar.name}.report.json"
            report_path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
    parser.add_argument("--fail-on-stale", action="store_true", help="Fail after writing if stale translations were detected.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
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
            report = materialize_locale_sidecar(
                canonical_sidecar=args.canonical_sidecar,
                translation_source=args.translation_source,
                output_sidecar=args.output_sidecar,
                locale=locales[0],
                translatable_fields=fields,
                expected_asset_slug=args.asset_slug,
                expected_release=args.release,
                fail_on_stale=args.fail_on_stale,
            )
            payload_obj = report.to_dict()
    except (FeatureMetadataLocalizationError, release_feature_model.ReleaseFeatureModelError, OSError, json.JSONDecodeError) as exc:
        print(f"feature-metadata-localization failed: {exc}", file=sys.stderr)
        return 2
    payload = json.dumps(payload_obj, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
