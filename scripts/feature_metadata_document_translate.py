#!/usr/bin/env python3
"""Export and import document-translation workbooks for feature metadata."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree
from xml.sax.saxutils import escape

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import feature_metadata_localization, feature_metadata_machine_translate, release_feature_model  # noqa: E402


MANIFEST_SCHEMA = "feature_metadata_document_translation_manifest_v1"
DEFAULT_MAX_SHARD_ROWS = 60_000
DEFAULT_MAX_SHARD_CHARS = 1_000_000
DEFAULT_DIRECT_THRESHOLD_SECONDS = 30 * 60
DEFAULT_DIRECT_RPS = feature_metadata_machine_translate.DEFAULT_MAX_REQUESTS_PER_SECOND
XML_ILLEGAL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class FeatureMetadataDocumentTranslateError(ValueError):
    """Raised when document translation shards cannot be prepared or ingested."""


def xml_text(value: str) -> str:
    return escape(XML_ILLEGAL_RE.sub(" ", value), {"'": "&apos;", '"': "&quot;"})


def column_name(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def column_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha()).upper()
    index = 0
    for letter in letters:
        index = index * 26 + (ord(letter) - ord("A") + 1)
    return index


def cell_xml(row_number: int, col_number: int, value: str) -> str:
    ref = f"{column_name(col_number)}{row_number}"
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{xml_text(value)}</t></is></c>'


def write_xlsx_rows(path: Path, rows: Sequence[Sequence[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row_xml = []
    for row_number, row in enumerate(rows, start=1):
        cells = "".join(cell_xml(row_number, col_number, str(value)) for col_number, value in enumerate(row, start=1))
        row_xml.append(f'<row r="{row_number}">{cells}</row>')
    last_row = max(1, len(rows))
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<dimension ref="A1:B{last_row}"/>'
        "<sheetViews><sheetView workbookViewId=\"0\"/></sheetViews>"
        '<sheetData>'
        + "".join(row_xml)
        + "</sheetData></worksheet>"
    )
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>
"""
    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""
    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="translate" sheetId="1" r:id="rId1"/></sheets>
</workbook>
"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
"""
    styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border/></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
</styleSheet>
"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def element_text(element: ElementTree.Element) -> str:
    return "".join(text for text in element.itertext())


def read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return [element_text(si) for si in root.findall("{*}si")]


def read_xlsx_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = read_shared_strings(archive)
        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in archive.namelist():
            raise FeatureMetadataDocumentTranslateError(f"workbook does not contain {sheet_name}: {path}")
        root = ElementTree.fromstring(archive.read(sheet_name))

    rows: list[list[str]] = []
    for row in root.findall(".//{*}row"):
        values: dict[int, str] = {}
        next_col = 1
        for cell in row.findall("{*}c"):
            ref = cell.attrib.get("r", "")
            col = column_index(ref) if ref else next_col
            next_col = col + 1
            cell_type = cell.attrib.get("t", "")
            value = ""
            if cell_type == "s":
                raw = cell.findtext("{*}v")
                if raw is not None:
                    try:
                        value = shared_strings[int(raw)]
                    except (IndexError, ValueError) as exc:
                        raise FeatureMetadataDocumentTranslateError(f"invalid shared string reference in {path}: {raw}") from exc
            elif cell_type == "inlineStr":
                inline = cell.find("{*}is")
                value = element_text(inline) if inline is not None else ""
            else:
                value = cell.findtext("{*}v") or ""
            values[col] = value
        if values:
            max_col = max(values)
            rows.append([values.get(col, "") for col in range(1, max_col + 1)])
        else:
            rows.append([])

    while rows and not any(value.strip() for value in rows[-1]):
        rows.pop()
    return rows


def parse_csv_argument(values: Sequence[str]) -> list[str]:
    return feature_metadata_machine_translate.parse_csv_argument(values)


def parse_path_mapping(values: Sequence[str], *, option_name: str) -> dict[str, list[Path]]:
    mapping: dict[str, list[Path]] = {}
    for raw in values:
        if "=" not in raw:
            raise FeatureMetadataDocumentTranslateError(f"{option_name} values must use locale=path")
        locale, path = raw.split("=", 1)
        normalized_locale = feature_metadata_localization.normalize_locale(locale)
        if not path.strip():
            raise FeatureMetadataDocumentTranslateError(f"{option_name} path must be non-empty")
        mapping.setdefault(normalized_locale, []).append(Path(path).expanduser())
    return mapping


def parse_locale_mapping(values: Sequence[str], *, option_name: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise FeatureMetadataDocumentTranslateError(f"{option_name} values must use destination_locale=source_locale")
        destination, source = raw.split("=", 1)
        normalized_destination = feature_metadata_localization.normalize_locale(destination)
        normalized_source = feature_metadata_localization.normalize_locale(source)
        mapping[normalized_destination] = normalized_source
    return mapping


def read_existing_rows(path: Path | None) -> list[dict[str, str]]:
    return feature_metadata_machine_translate.read_existing_rows(path) if path and path.exists() else []


def collect_pending_tasks(
    *,
    canonical_sidecar: Path,
    translation_source: Path | None,
    locales: Sequence[str],
    fields: Sequence[str],
    schema: Path | None = None,
    refresh_current: bool = False,
    stringify_non_string: bool = False,
    skip_numeric_strings: bool = False,
    expected_asset_slug: str | None = None,
    expected_release: str | None = None,
    target_overrides: Mapping[str, str] | None = None,
) -> tuple[list[feature_metadata_machine_translate.TranslationTask], list[dict[str, str]], dict[str, Any]]:
    normalized_locales = feature_metadata_localization.parse_locale_arguments(locales)
    if not normalized_locales:
        raise FeatureMetadataDocumentTranslateError("at least one locale is required")
    normalized_fields = parse_csv_argument(fields)
    if not normalized_fields:
        raise FeatureMetadataDocumentTranslateError("at least one field is required")
    if schema:
        feature_metadata_localization.resolved_translatable_fields(schema=schema, fields=normalized_fields)
    records = list(release_feature_model.read_metadata_sidecar(canonical_sidecar))
    validation = release_feature_model.validate_sidecar_records(
        records,
        expected_asset_slug=expected_asset_slug,
        expected_release=expected_release,
    )
    if not validation.valid:
        raise FeatureMetadataDocumentTranslateError("canonical sidecar validation failed: " + "; ".join(validation.errors))
    existing_rows = read_existing_rows(translation_source)
    target_by_locale = {
        locale: feature_metadata_machine_translate.translator_target_for_locale(locale, target_overrides or {})
        for locale in normalized_locales
    }
    existing_keys = {feature_metadata_machine_translate.translation_key(row) for row in existing_rows}
    keys_to_skip = set() if refresh_current else existing_keys
    tasks, stats = feature_metadata_machine_translate.collect_tasks(
        records=records,
        fields=normalized_fields,
        locales=normalized_locales,
        target_by_locale=target_by_locale,
        existing_keys=keys_to_skip,
        stringify_non_string=stringify_non_string,
        skip_numeric_strings=skip_numeric_strings,
    )
    report = {
        "feature_count": validation.feature_count,
        "locales": normalized_locales,
        "fields": normalized_fields,
        "target_by_locale": target_by_locale,
        "existing_row_count": len(existing_rows),
        "requested_task_count": len(tasks),
        **stats,
    }
    return tasks, existing_rows, report


def unique_entries_from_tasks(
    tasks: Sequence[feature_metadata_machine_translate.TranslationTask],
) -> list[dict[str, str]]:
    entries: OrderedDict[str, str] = OrderedDict()
    for task in tasks:
        previous = entries.get(task.source_value_hash)
        if previous is not None and previous != task.source_text:
            raise FeatureMetadataDocumentTranslateError(
                f"source hash {task.source_value_hash} is associated with conflicting source text"
            )
        entries.setdefault(task.source_value_hash, task.source_text)
    return [
        {"source_value_hash": source_hash, "source_text": source_text}
        for source_hash, source_text in entries.items()
    ]


def shard_entries(
    entries: Sequence[Mapping[str, str]],
    *,
    max_rows: int,
    max_chars: int,
) -> list[list[Mapping[str, str]]]:
    if max_rows < 1:
        raise FeatureMetadataDocumentTranslateError("--max-shard-rows must be at least 1")
    if max_chars < 1:
        raise FeatureMetadataDocumentTranslateError("--max-shard-chars must be at least 1")
    shards: list[list[Mapping[str, str]]] = []
    current: list[Mapping[str, str]] = []
    current_chars = 0
    for entry in entries:
        entry_chars = len(entry["source_text"])
        if current and (len(current) >= max_rows or current_chars + entry_chars > max_chars):
            shards.append(current)
            current = []
            current_chars = 0
        current.append(entry)
        current_chars += entry_chars
    if current or not shards:
        shards.append(current)
    return shards


def default_output_stem(canonical_sidecar: Path) -> str:
    name = canonical_sidecar.name
    if name.endswith(".metadata.ndjson.gz"):
        return name[: -len(".metadata.ndjson.gz")]
    return canonical_sidecar.stem


def direct_translation_estimate_seconds(
    tasks: Sequence[feature_metadata_machine_translate.TranslationTask],
    *,
    max_rps: float,
) -> float | None:
    unique_pairs = {(task.target, task.source_text) for task in tasks}
    if max_rps <= 0:
        return None
    return len(unique_pairs) / max_rps


def export_document_workbooks(
    *,
    canonical_sidecar: Path,
    translation_source: Path | None,
    output_dir: Path,
    locales: Sequence[str],
    fields: Sequence[str],
    schema: Path | None = None,
    asset_slug: str | None = None,
    release: str | None = None,
    target_overrides: Mapping[str, str] | None = None,
    max_shard_rows: int = DEFAULT_MAX_SHARD_ROWS,
    max_shard_chars: int = DEFAULT_MAX_SHARD_CHARS,
    direct_threshold_seconds: int = DEFAULT_DIRECT_THRESHOLD_SECONDS,
    direct_max_rps: float = DEFAULT_DIRECT_RPS,
    output_stem: str | None = None,
    stringify_non_string: bool = False,
    skip_numeric_strings: bool = False,
) -> dict[str, Any]:
    tasks, _existing_rows, report = collect_pending_tasks(
        canonical_sidecar=canonical_sidecar,
        translation_source=translation_source,
        locales=locales,
        fields=fields,
        schema=schema,
        expected_asset_slug=asset_slug,
        expected_release=release,
        target_overrides=target_overrides,
        stringify_non_string=stringify_non_string,
        skip_numeric_strings=skip_numeric_strings,
    )
    entries = unique_entries_from_tasks(tasks)
    shards = shard_entries(entries, max_rows=max_shard_rows, max_chars=max_shard_chars)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = output_stem or default_output_stem(canonical_sidecar)

    manifest_entries: list[dict[str, Any]] = []
    shard_reports: list[dict[str, Any]] = []
    for index, shard in enumerate(shards, start=1):
        filename = f"{stem}.for-translate.xlsx" if len(shards) == 1 else f"{stem}.for-translate.part-{index:03d}.xlsx"
        workbook_path = output_dir / filename
        rows = [["hash", "text"], *[[entry["source_value_hash"], entry["source_text"]] for entry in shard]]
        write_xlsx_rows(workbook_path, rows)
        shard_reports.append(
            {
                "path": str(workbook_path),
                "name": filename,
                "data_row_count": len(shard),
                "first_data_row": 2 if shard else None,
                "last_data_row": len(shard) + 1 if shard else None,
            }
        )
        for row_offset, entry in enumerate(shard, start=2):
            manifest_entries.append(
                {
                    "shard": filename,
                    "row_number": row_offset,
                    "source_value_hash": entry["source_value_hash"],
                    "source_text": entry["source_text"],
                }
            )

    estimate_seconds = direct_translation_estimate_seconds(tasks, max_rps=direct_max_rps)
    unique_pairs = {(task.target, task.source_text) for task in tasks}
    recommendation = "document_translation" if estimate_seconds is not None and estimate_seconds > direct_threshold_seconds else "direct_machine_translate"
    payload = {
        "schema": MANIFEST_SCHEMA,
        "valid": True,
        "canonical_sidecar": str(canonical_sidecar),
        "translation_source": str(translation_source or ""),
        "asset_slug": asset_slug or "",
        "release": release or "",
        "locales": report["locales"],
        "fields": report["fields"],
        "target_by_locale": report["target_by_locale"],
        "workbook_schema": "two columns: hash,text",
        "direct_translation_threshold_seconds": direct_threshold_seconds,
        "direct_translation_estimate_seconds": estimate_seconds,
        "direct_translation_unique_pair_count": len(unique_pairs),
        "recommended_workflow": recommendation,
        "unique_source_value_count": len(entries),
        "shard_count": len(shards),
        "shards": shard_reports,
        "entries": manifest_entries,
        **report,
    }
    manifest_path = output_dir / f"{stem}.for-translate.manifest.json"
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    payload["manifest"] = str(manifest_path)
    return payload


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != MANIFEST_SCHEMA:
        raise FeatureMetadataDocumentTranslateError(f"unsupported manifest schema in {path}")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise FeatureMetadataDocumentTranslateError("manifest is missing entries")
    return payload


def entries_by_shard(manifest: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for raw_entry in manifest["entries"]:
        if not isinstance(raw_entry, dict):
            raise FeatureMetadataDocumentTranslateError("manifest entry must be an object")
        shard = str(raw_entry.get("shard") or "")
        source_hash = str(raw_entry.get("source_value_hash") or "")
        if not shard or not source_hash:
            raise FeatureMetadataDocumentTranslateError("manifest entries require shard and source_value_hash")
        grouped.setdefault(shard, []).append(raw_entry)
    return grouped


def translated_values_for_locale(
    *,
    locale: str,
    files: Sequence[Path],
    manifest: Mapping[str, Any],
) -> tuple[dict[str, str], int]:
    shard_reports = list(manifest.get("shards") or [])
    if len(files) != len(shard_reports):
        raise FeatureMetadataDocumentTranslateError(
            f"locale {locale} has {len(files)} translated file(s), expected {len(shard_reports)} from the manifest"
        )
    grouped = entries_by_shard(manifest)
    translations: dict[str, str] = {}
    mismatched_hash_columns = 0
    for file_path, shard_report in zip(files, shard_reports):
        shard_name = str(shard_report.get("name") or Path(str(shard_report.get("path") or "")).name)
        expected_entries = grouped.get(shard_name, [])
        rows = read_xlsx_rows(file_path)
        if not rows:
            raise FeatureMetadataDocumentTranslateError(f"translated workbook is empty for locale {locale}: {file_path}")
        data_rows = rows[1:]
        if len(data_rows) < len(expected_entries):
            raise FeatureMetadataDocumentTranslateError(
                f"translated workbook has too few data rows for locale {locale}: {file_path}"
            )
        for row, expected_entry in zip(data_rows, expected_entries):
            observed_hash = row[0].strip() if row else ""
            expected_hash = str(expected_entry["source_value_hash"])
            if observed_hash and observed_hash != expected_hash:
                mismatched_hash_columns += 1
            translated_value = row[1] if len(row) > 1 else ""
            if not translated_value.strip():
                raise FeatureMetadataDocumentTranslateError(
                    f"blank translated value for locale {locale}, shard {shard_name}, row {expected_entry['row_number']}"
                )
            translations[expected_hash] = translated_value
    return translations, mismatched_hash_columns


def dedupe_output_rows(rows: Sequence[Mapping[str, str]]) -> None:
    seen: dict[tuple[str, str, str, str], int] = {}
    for row_number, row in enumerate(rows, start=2):
        key = feature_metadata_machine_translate.translation_key(row)
        previous = seen.get(key)
        if previous is not None:
            raise FeatureMetadataDocumentTranslateError(
                f"output would contain duplicate translation key on row {row_number}; first seen on row {previous}"
            )
        seen[key] = row_number


def import_document_workbooks(
    *,
    manifest_path: Path,
    canonical_sidecar: Path,
    translation_source: Path | None,
    output_translation_source: Path,
    translated_files: Mapping[str, Sequence[Path]],
    reuse_locale: Mapping[str, str] | None = None,
    schema: Path | None = None,
    refresh_current: bool = False,
    review_state: str = "document_translated",
    notes: str = "provider=document-translation",
    asset_slug: str | None = None,
    release: str | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    locales = feature_metadata_localization.parse_locale_arguments(list(manifest.get("locales") or []))
    fields = parse_csv_argument(list(manifest.get("fields") or []))
    if not locales or not fields:
        raise FeatureMetadataDocumentTranslateError("manifest must include locales and fields")
    reuse_locale = reuse_locale or {}
    for destination_locale, source_locale in reuse_locale.items():
        if destination_locale not in locales:
            raise FeatureMetadataDocumentTranslateError(f"reuse destination locale is not in manifest locales: {destination_locale}")
        if source_locale not in locales:
            raise FeatureMetadataDocumentTranslateError(f"reuse source locale is not in manifest locales: {source_locale}")

    normalized_files = {feature_metadata_localization.normalize_locale(locale): list(paths) for locale, paths in translated_files.items()}
    translated_by_locale: dict[str, dict[str, str]] = {}
    hash_mismatch_counts: dict[str, int] = {}
    for locale, files in normalized_files.items():
        if locale not in locales:
            raise FeatureMetadataDocumentTranslateError(f"translated file locale is not in manifest locales: {locale}")
        translations, mismatch_count = translated_values_for_locale(locale=locale, files=files, manifest=manifest)
        translated_by_locale[locale] = translations
        hash_mismatch_counts[locale] = mismatch_count
    for destination_locale, source_locale in reuse_locale.items():
        if source_locale not in translated_by_locale:
            raise FeatureMetadataDocumentTranslateError(
                f"reuse source locale {source_locale} does not have a translated file"
            )
        translated_by_locale[destination_locale] = dict(translated_by_locale[source_locale])
        hash_mismatch_counts[destination_locale] = hash_mismatch_counts.get(source_locale, 0)

    missing_files = [locale for locale in locales if locale not in translated_by_locale]
    if missing_files:
        raise FeatureMetadataDocumentTranslateError(
            "missing translated file(s) or reuse mapping for locale(s): " + ", ".join(missing_files)
        )

    tasks, existing_rows, report = collect_pending_tasks(
        canonical_sidecar=canonical_sidecar,
        translation_source=translation_source,
        locales=locales,
        fields=fields,
        schema=schema,
        refresh_current=refresh_current,
        expected_asset_slug=asset_slug or str(manifest.get("asset_slug") or "") or None,
        expected_release=release or str(manifest.get("release") or "") or None,
    )
    current_keys = {task.key for task in tasks}
    if refresh_current:
        existing_rows = [row for row in existing_rows if feature_metadata_machine_translate.translation_key(row) not in current_keys]

    generated_rows: list[dict[str, str]] = []
    missing_translation_count = 0
    for task in tasks:
        value = translated_by_locale[task.locale].get(task.source_value_hash)
        if value is None:
            missing_translation_count += 1
            continue
        source_locale = next((source for destination, source in reuse_locale.items() if destination == task.locale), task.locale)
        row_notes = notes
        if source_locale != task.locale:
            row_notes = f"{notes}; reused_locale={source_locale}"
        generated_rows.append(
            {
                "feature_id": task.feature_id,
                "field": task.field,
                "locale": task.locale,
                "source_value_hash": task.source_value_hash,
                "value": value,
                "review_state": review_state,
                "notes": row_notes,
            }
        )
    if missing_translation_count:
        raise FeatureMetadataDocumentTranslateError(
            f"translated workbooks are missing {missing_translation_count} source hash value(s) required by current tasks"
        )

    output_rows = [*existing_rows, *generated_rows]
    dedupe_output_rows(output_rows)
    feature_metadata_machine_translate.write_translation_source(output_translation_source, output_rows)
    return {
        "schema": MANIFEST_SCHEMA,
        "valid": True,
        "manifest": str(manifest_path),
        "canonical_sidecar": str(canonical_sidecar),
        "translation_source": str(translation_source or ""),
        "output_translation_source": str(output_translation_source),
        "locales": locales,
        "fields": fields,
        "refresh_current": refresh_current,
        **report,
        "existing_row_count": len(existing_rows),
        "generated_row_count": len(generated_rows),
        "output_row_count": len(output_rows),
        "hash_column_mismatch_count_by_locale": hash_mismatch_counts,
    }


def write_or_print_report(payload: Mapping[str, Any], report_path: Path | None) -> None:
    summary = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(summary, encoding="utf-8")
    print(summary, end="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="Create two-column .xlsx shards and a row-order manifest.")
    export_parser.add_argument("--canonical-sidecar", required=True, type=Path)
    export_parser.add_argument("--translation-source", type=Path)
    export_parser.add_argument("--output-dir", required=True, type=Path)
    export_parser.add_argument("--locale", action="append", default=[], help="Locale to prepare. May be repeated or comma-separated.")
    export_parser.add_argument("--field", action="append", default=[], help="Metadata property field to translate. May be repeated.")
    export_parser.add_argument("--schema", type=Path, help="Optional release schema used to validate requested fields.")
    export_parser.add_argument("--asset-slug", help="Expected asset slug for sidecar validation.")
    export_parser.add_argument("--release", help="Expected YYYY-MM-DD release for sidecar validation.")
    export_parser.add_argument("--translator-target", action="append", default=[], help="Override target code, for example pt_br=pt.")
    export_parser.add_argument("--max-shard-rows", type=int, default=DEFAULT_MAX_SHARD_ROWS)
    export_parser.add_argument("--max-shard-chars", type=int, default=DEFAULT_MAX_SHARD_CHARS)
    export_parser.add_argument("--direct-threshold-seconds", type=int, default=DEFAULT_DIRECT_THRESHOLD_SECONDS)
    export_parser.add_argument("--direct-max-rps", type=float, default=DEFAULT_DIRECT_RPS)
    export_parser.add_argument("--output-stem")
    export_parser.add_argument("--stringify-non-string", action="store_true")
    export_parser.add_argument("--skip-numeric-strings", action="store_true")
    export_parser.add_argument("--report", type=Path)

    import_parser = subparsers.add_parser("import", help="Ingest returned translated workbooks into metadata-translations CSV.")
    import_parser.add_argument("--manifest", required=True, type=Path)
    import_parser.add_argument("--canonical-sidecar", required=True, type=Path)
    import_parser.add_argument("--translation-source", type=Path)
    import_parser.add_argument("--output-translation-source", required=True, type=Path)
    import_parser.add_argument(
        "--translated-file",
        action="append",
        default=[],
        help="Translated workbook as locale=path. Repeat in manifest shard order when there are multiple shards.",
    )
    import_parser.add_argument("--reuse-locale", action="append", default=[], help="Reuse one locale's file for another, destination=source.")
    import_parser.add_argument("--schema", type=Path, help="Optional release schema used to validate requested fields.")
    import_parser.add_argument("--asset-slug", help="Expected asset slug for sidecar validation.")
    import_parser.add_argument("--release", help="Expected YYYY-MM-DD release for sidecar validation.")
    import_parser.add_argument("--refresh-current", action="store_true")
    import_parser.add_argument("--review-state", default="document_translated")
    import_parser.add_argument("--notes", default="provider=document-translation")
    import_parser.add_argument("--report", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            payload = export_document_workbooks(
                canonical_sidecar=args.canonical_sidecar,
                translation_source=args.translation_source,
                output_dir=args.output_dir,
                locales=args.locale,
                fields=args.field,
                schema=args.schema,
                asset_slug=args.asset_slug,
                release=args.release,
                target_overrides=feature_metadata_machine_translate.parse_mapping_arguments(args.translator_target),
                max_shard_rows=args.max_shard_rows,
                max_shard_chars=args.max_shard_chars,
                direct_threshold_seconds=args.direct_threshold_seconds,
                direct_max_rps=args.direct_max_rps,
                output_stem=args.output_stem,
                stringify_non_string=args.stringify_non_string,
                skip_numeric_strings=args.skip_numeric_strings,
            )
        else:
            payload = import_document_workbooks(
                manifest_path=args.manifest,
                canonical_sidecar=args.canonical_sidecar,
                translation_source=args.translation_source,
                output_translation_source=args.output_translation_source,
                translated_files=parse_path_mapping(args.translated_file, option_name="--translated-file"),
                reuse_locale=parse_locale_mapping(args.reuse_locale, option_name="--reuse-locale"),
                schema=args.schema,
                refresh_current=args.refresh_current,
                review_state=args.review_state,
                notes=args.notes,
                asset_slug=args.asset_slug,
                release=args.release,
            )
        write_or_print_report(payload, args.report)
    except (
        FeatureMetadataDocumentTranslateError,
        feature_metadata_machine_translate.FeatureMetadataMachineTranslateError,
        feature_metadata_localization.FeatureMetadataLocalizationError,
        release_feature_model.ReleaseFeatureModelError,
        OSError,
        csv.Error,
        json.JSONDecodeError,
        zipfile.BadZipFile,
        ElementTree.ParseError,
    ) as exc:
        print(f"feature-metadata-document-translate failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
