#!/usr/bin/env python3
"""Generate feature metadata translation-source CSV rows with deep-translator."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import feature_metadata_localization, release_feature_model  # noqa: E402


TRANSLATION_COLUMNS = (
    "feature_id",
    "field",
    "locale",
    "source_value_hash",
    "value",
    "review_state",
    "notes",
)
DEFAULT_TARGET_OVERRIDES = {
    "es_419": "es",
    "pt_br": "pt",
    "pt_pt": "pt",
    "zh_hans": "zh-CN",
    "zh_hant": "zh-TW",
}
NUMERIC_STRING_RE = re.compile(r"^[+-]?(?:\d+(?:[.,]\d+)*|\d*\.\d+)$")


class FeatureMetadataMachineTranslateError(ValueError):
    """Raised when machine translation rows cannot be generated."""


class Translator(Protocol):
    def translate(self, text: str) -> str:
        """Return translated text."""


@dataclass(frozen=True)
class TranslationTask:
    feature_id: str
    field: str
    locale: str
    source_value_hash: str
    source_text: str
    target: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.feature_id, self.field, self.locale, self.source_value_hash)


def parse_csv_argument(values: Sequence[str]) -> list[str]:
    parsed: list[str] = []
    seen: set[str] = set()
    for value in values:
        for part in str(value or "").split(","):
            normalized = part.strip()
            if not normalized or normalized in seen:
                continue
            parsed.append(normalized)
            seen.add(normalized)
    return parsed


def parse_mapping_arguments(values: Sequence[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise FeatureMetadataMachineTranslateError(
                "--translator-target values must use locale=target_code, for example pt_br=pt"
            )
        locale, target = raw.split("=", 1)
        normalized_locale = feature_metadata_localization.normalize_locale(locale)
        normalized_target = target.strip()
        if not normalized_target:
            raise FeatureMetadataMachineTranslateError("--translator-target target code must be non-empty")
        mapping[normalized_locale] = normalized_target
    return mapping


def translator_target_for_locale(locale: str, overrides: Mapping[str, str]) -> str:
    normalized = feature_metadata_localization.normalize_locale(locale)
    if normalized in overrides:
        return overrides[normalized]
    if normalized in DEFAULT_TARGET_OVERRIDES:
        return DEFAULT_TARGET_OVERRIDES[normalized]
    if "_" in normalized:
        return normalized.split("_", 1)[0]
    return normalized


def import_deep_translator_google() -> type[Translator]:
    try:
        from deep_translator import GoogleTranslator
    except ImportError as exc:
        raise FeatureMetadataMachineTranslateError(
            "deep-translator is not installed. Run through uv, for example: "
            "UV_CACHE_DIR=.uv-cache uv run --with deep-translator --with tqdm "
            "python scripts/feature_metadata_machine_translate.py ..."
        ) from exc
    return GoogleTranslator


def deep_translator_factory(provider: str) -> Callable[[str], Translator]:
    normalized = provider.strip().lower()
    if normalized != "google":
        raise FeatureMetadataMachineTranslateError("only the google provider is currently supported")
    translator_class = import_deep_translator_google()

    def factory(target: str) -> Translator:
        return translator_class(source="auto", target=target)

    return factory


def progress_iter(items: Sequence[tuple[str, str]], *, enabled: bool) -> Sequence[tuple[str, str]] | Any:
    if not enabled:
        return items
    try:
        from tqdm import tqdm
    except ImportError:
        return items
    return tqdm(items, unit="text")


def translation_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("feature_id") or "").strip(),
        str(row.get("field") or "").strip(),
        feature_metadata_localization.normalize_locale(row.get("locale")),
        feature_metadata_localization.normalize_source_value_hash(
            row.get("source_value_hash"),
            context="existing translation row",
        ),
    )


def read_existing_rows(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        missing = [name for name in TRANSLATION_COLUMNS[:5] if name not in fieldnames]
        if missing:
            raise FeatureMetadataMachineTranslateError(
                f"existing translation source is missing required column(s): {', '.join(missing)}"
            )
        unsupported = sorted(set(fieldnames) - set(TRANSLATION_COLUMNS))
        if unsupported:
            raise FeatureMetadataMachineTranslateError(
                f"existing translation source has unsupported column(s): {', '.join(unsupported)}"
            )
        rows: list[dict[str, str]] = []
        seen: dict[tuple[str, str, str, str], int] = {}
        for row_number, raw_row in enumerate(reader, start=2):
            row = {name: str(raw_row.get(name) or "") for name in TRANSLATION_COLUMNS}
            feature_id, field, locale, digest = translation_key(row)
            if not feature_id:
                raise FeatureMetadataMachineTranslateError(f"{path}:{row_number}: feature_id is required")
            release_feature_model.validate_feature_id(feature_id)
            if not field:
                raise FeatureMetadataMachineTranslateError(f"{path}:{row_number}: field is required")
            if not row["value"]:
                raise FeatureMetadataMachineTranslateError(f"{path}:{row_number}: value is required")
            row["locale"] = locale
            row["source_value_hash"] = digest
            key = (feature_id, field, locale, digest)
            previous = seen.get(key)
            if previous is not None:
                raise FeatureMetadataMachineTranslateError(
                    f"{path}:{row_number}: duplicate translation key first seen on row {previous}"
                )
            seen[key] = row_number
            rows.append(row)
    return rows


def source_text_for_translation(value: Any, *, stringify_non_string: bool) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value if value.strip() else None
    if not stringify_non_string:
        return None
    return str(value)


def collect_tasks(
    *,
    records: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
    locales: Sequence[str],
    target_by_locale: Mapping[str, str],
    existing_keys: set[tuple[str, str, str, str]],
    stringify_non_string: bool,
    skip_numeric_strings: bool,
) -> tuple[list[TranslationTask], dict[str, Any]]:
    stats: dict[str, Any] = {
        "field_record_counts": {field: 0 for field in fields},
        "missing_field_count": 0,
        "empty_value_count": 0,
        "non_string_value_count": 0,
        "numeric_string_skip_count": 0,
        "existing_current_row_count": 0,
    }
    tasks: list[TranslationTask] = []
    fields_seen: set[str] = set()
    for record_number, record in enumerate(records, start=1):
        feature_id = str(record.get("feature_id") or "").strip()
        properties = record.get("properties")
        if not isinstance(properties, Mapping):
            raise FeatureMetadataMachineTranslateError(f"record {record_number}: properties must be an object")
        for field in fields:
            if field not in properties:
                stats["missing_field_count"] += 1
                continue
            fields_seen.add(field)
            stats["field_record_counts"][field] += 1
            raw_value = properties.get(field)
            if raw_value is not None and not isinstance(raw_value, str) and not stringify_non_string:
                stats["non_string_value_count"] += 1
                continue
            source_text = source_text_for_translation(raw_value, stringify_non_string=stringify_non_string)
            if source_text is None:
                stats["empty_value_count"] += 1
                continue
            if skip_numeric_strings and NUMERIC_STRING_RE.fullmatch(source_text.strip()):
                stats["numeric_string_skip_count"] += 1
                continue
            source_hash = feature_metadata_localization.source_value_hash(raw_value)
            for locale in locales:
                key = (feature_id, field, locale, source_hash)
                if key in existing_keys:
                    stats["existing_current_row_count"] += 1
                    continue
                tasks.append(
                    TranslationTask(
                        feature_id=feature_id,
                        field=field,
                        locale=locale,
                        source_value_hash=source_hash,
                        source_text=source_text,
                        target=target_by_locale[locale],
                    )
                )
    absent_fields = sorted(set(fields) - fields_seen)
    if absent_fields:
        raise FeatureMetadataMachineTranslateError(
            "requested field(s) were not present in any metadata record: " + ", ".join(absent_fields)
        )
    return tasks, stats


def translate_unique_values(
    tasks: Sequence[TranslationTask],
    *,
    translator_factory: Callable[[str], Translator],
    sleep_seconds: float,
    on_error: str,
    progress: bool,
) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], str]]:
    unique_pairs = sorted({(task.target, task.source_text) for task in tasks})
    translators: dict[str, Translator] = {}
    translations: dict[tuple[str, str], str] = {}
    failures: dict[tuple[str, str], str] = {}
    for target, source_text in progress_iter(unique_pairs, enabled=progress):
        try:
            translator = translators.get(target)
            if translator is None:
                translator = translator_factory(target)
                translators[target] = translator
            translated = str(translator.translate(source_text) or "")
            if not translated.strip():
                raise FeatureMetadataMachineTranslateError("translator returned an empty value")
            translations[(target, source_text)] = translated
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
        except Exception as exc:  # noqa: BLE001 - controlled by --on-error.
            if on_error == "fail":
                raise FeatureMetadataMachineTranslateError(
                    f"translation failed for target {target!r}, value {source_text!r}: {exc}"
                ) from exc
            failures[(target, source_text)] = f"{type(exc).__name__}: {exc}"
            if on_error == "source":
                translations[(target, source_text)] = source_text
            elif on_error != "skip":
                raise FeatureMetadataMachineTranslateError("--on-error must be fail, source, or skip")
    return translations, failures


def write_translation_source(path: Path, rows: Sequence[Mapping[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(TRANSLATION_COLUMNS), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: str(row.get(name) or "") for name in TRANSLATION_COLUMNS})


def generate_translation_source(
    *,
    canonical_sidecar: Path,
    translation_source: Path,
    locales: Sequence[str],
    fields: Sequence[str],
    schema: Path | None = None,
    existing_translation_source: Path | None = None,
    preserve_existing: bool = True,
    refresh_current: bool = False,
    provider: str = "google",
    target_overrides: Mapping[str, str] | None = None,
    translator_factory: Callable[[str], Translator] | None = None,
    sleep_seconds: float = 0.05,
    review_state: str = "machine_translated",
    on_error: str = "source",
    stringify_non_string: bool = False,
    skip_numeric_strings: bool = False,
    expected_asset_slug: str | None = None,
    expected_release: str | None = None,
    progress: bool = False,
) -> dict[str, Any]:
    normalized_locales = feature_metadata_localization.parse_locale_arguments(locales)
    if not normalized_locales:
        raise FeatureMetadataMachineTranslateError("at least one --locale is required")
    normalized_fields = parse_csv_argument(fields)
    if not normalized_fields:
        raise FeatureMetadataMachineTranslateError("at least one --field is required")
    if schema:
        feature_metadata_localization.resolved_translatable_fields(schema=schema, fields=normalized_fields)
    records = list(release_feature_model.read_metadata_sidecar(canonical_sidecar))
    validation = release_feature_model.validate_sidecar_records(
        records,
        expected_asset_slug=expected_asset_slug,
        expected_release=expected_release,
    )
    if not validation.valid:
        raise FeatureMetadataMachineTranslateError("canonical sidecar validation failed: " + "; ".join(validation.errors))

    existing_path = existing_translation_source
    if existing_path is None and translation_source.exists():
        existing_path = translation_source
    existing_rows = read_existing_rows(existing_path) if preserve_existing else []
    target_by_locale = {
        locale: translator_target_for_locale(locale, target_overrides or {})
        for locale in normalized_locales
    }
    existing_keys = {translation_key(row) for row in existing_rows}
    keys_to_skip = set() if refresh_current else existing_keys
    tasks, stats = collect_tasks(
        records=records,
        fields=normalized_fields,
        locales=normalized_locales,
        target_by_locale=target_by_locale,
        existing_keys=keys_to_skip,
        stringify_non_string=stringify_non_string,
        skip_numeric_strings=skip_numeric_strings,
    )
    current_keys = {task.key for task in tasks}
    if refresh_current:
        existing_rows = [row for row in existing_rows if translation_key(row) not in current_keys]

    factory = translator_factory or deep_translator_factory(provider)
    translations, failures = translate_unique_values(
        tasks,
        translator_factory=factory,
        sleep_seconds=sleep_seconds,
        on_error=on_error,
        progress=progress,
    )

    generated_rows: list[dict[str, str]] = []
    skipped_error_rows = 0
    for task in tasks:
        pair = (task.target, task.source_text)
        translated = translations.get(pair)
        if translated is None:
            skipped_error_rows += 1
            continue
        failed = failures.get(pair)
        generated_rows.append(
            {
                "feature_id": task.feature_id,
                "field": task.field,
                "locale": task.locale,
                "source_value_hash": task.source_value_hash,
                "value": translated,
                "review_state": "source_provided" if failed else review_state,
                "notes": (
                    f"machine translation failed; source value retained; provider={provider}; target={task.target}"
                    if failed
                    else f"provider={provider}; target={task.target}"
                ),
            }
        )

    output_rows = [*existing_rows, *generated_rows]
    seen_output_keys: dict[tuple[str, str, str, str], int] = {}
    for row_number, row in enumerate(output_rows, start=2):
        key = translation_key(row)
        previous = seen_output_keys.get(key)
        if previous is not None:
            raise FeatureMetadataMachineTranslateError(
                f"output would contain duplicate translation key on row {row_number}; first seen on row {previous}"
            )
        seen_output_keys[key] = row_number
    write_translation_source(translation_source, output_rows)

    return {
        "valid": True,
        "translation_source_schema": feature_metadata_localization.TRANSLATION_SOURCE_SCHEMA,
        "provider": provider,
        "canonical_sidecar": str(canonical_sidecar),
        "translation_source": str(translation_source),
        "existing_translation_source": str(existing_path) if existing_path else "",
        "locales": normalized_locales,
        "fields": normalized_fields,
        "target_by_locale": target_by_locale,
        "feature_count": validation.feature_count,
        "existing_row_count": len(existing_rows),
        "generated_row_count": len(generated_rows),
        "output_row_count": len(output_rows),
        "requested_task_count": len(tasks),
        "translated_unique_value_count": len(translations),
        "translation_failure_count": len(failures),
        "skipped_error_row_count": skipped_error_rows,
        **stats,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-sidecar", required=True, type=Path)
    parser.add_argument("--translation-source", required=True, type=Path)
    parser.add_argument("--existing-translation-source", type=Path)
    parser.add_argument("--schema", type=Path, help="Optional release schema used to validate requested fields.")
    parser.add_argument("--locale", action="append", default=[], help="Locale to generate. May be repeated or comma-separated.")
    parser.add_argument("--field", action="append", default=[], help="Metadata property field to translate. May be repeated.")
    parser.add_argument("--asset-slug", help="Expected asset slug for sidecar validation.")
    parser.add_argument("--release", help="Expected release date for sidecar validation.")
    parser.add_argument("--provider", default="google", help="deep-translator provider. Currently only google is supported.")
    parser.add_argument(
        "--translator-target",
        action="append",
        default=[],
        help="Override translator target code for a locale, for example pt_br=pt. May be repeated.",
    )
    parser.add_argument("--sleep-seconds", type=float, default=0.05)
    parser.add_argument("--review-state", default="machine_translated")
    parser.add_argument("--on-error", choices=("fail", "source", "skip"), default="source")
    parser.add_argument("--no-preserve-existing", action="store_true")
    parser.add_argument("--refresh-current", action="store_true")
    parser.add_argument("--stringify-non-string", action="store_true")
    parser.add_argument("--skip-numeric-strings", action="store_true")
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = generate_translation_source(
            canonical_sidecar=args.canonical_sidecar,
            translation_source=args.translation_source,
            existing_translation_source=args.existing_translation_source,
            locales=args.locale,
            fields=args.field,
            schema=args.schema,
            preserve_existing=not args.no_preserve_existing,
            refresh_current=args.refresh_current,
            provider=args.provider,
            target_overrides=parse_mapping_arguments(args.translator_target),
            sleep_seconds=args.sleep_seconds,
            review_state=args.review_state,
            on_error=args.on_error,
            stringify_non_string=args.stringify_non_string,
            skip_numeric_strings=args.skip_numeric_strings,
            expected_asset_slug=args.asset_slug,
            expected_release=args.release,
            progress=args.progress,
        )
    except (
        FeatureMetadataMachineTranslateError,
        feature_metadata_localization.FeatureMetadataLocalizationError,
        release_feature_model.ReleaseFeatureModelError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"feature-metadata-machine-translate failed: {exc}", file=sys.stderr)
        return 2
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
