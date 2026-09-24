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

from scripts import feature_metadata_localization, feature_metadata_machine_translate, release_feature_model, translation_local_io  # noqa: E402


MANIFEST_SCHEMA = "feature_metadata_document_translation_manifest_v2"
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


def write_xlsx_rows(path: Path, rows: Sequence[Sequence[str]], *, expected: Sequence[translation_local_io.FileSnapshot] = (), protected: Sequence[Path] = ()) -> None:
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
    with translation_local_io.candidate_output(path, expected=expected, protected=protected) as candidate:
        with zipfile.ZipFile(candidate, "w", compression=zipfile.ZIP_DEFLATED) as archive:
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
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise FeatureMetadataDocumentTranslateError(f"duplicate workbook ZIP member: {path}")
        shared_strings = read_shared_strings(archive)
        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in archive.namelist():
            raise FeatureMetadataDocumentTranslateError(f"workbook does not contain {sheet_name}: {path}")
        root = ElementTree.fromstring(archive.read(sheet_name))

    rows: list[list[str]] = []
    seen_rows: set[str] = set()
    for row in root.findall(".//{*}row"):
        row_ref = row.attrib.get("r", "")
        if not re.fullmatch(r"[1-9][0-9]*", row_ref) or row_ref in seen_rows:
            raise FeatureMetadataDocumentTranslateError(f"invalid or duplicate workbook row reference: {path}")
        seen_rows.add(row_ref)
        values: dict[int, str] = {}
        next_col = 1
        for cell in row.findall("{*}c"):
            ref = cell.attrib.get("r", "")
            col = column_index(ref) if ref else next_col
            if col not in (1, 2) or col in values or (ref and ref != f"{column_name(col)}{row_ref}") or cell.find("{*}f") is not None:
                raise FeatureMetadataDocumentTranslateError(f"invalid/duplicate cell or formula in two-column workbook: {path}")
            next_col = col + 1
            cell_type = cell.attrib.get("t", "")
            value = ""
            if cell_type == "s":
                raw = cell.findtext("{*}v")
                if raw is not None:
                    try:
                        if not raw.isdecimal():
                            raise ValueError("negative/noninteger shared string index")
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
        if normalized_destination in mapping:
            raise FeatureMetadataDocumentTranslateError("duplicate reuse destination locale")
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
    existing_keys = feature_metadata_machine_translate.completed_keys(existing_rows, records)
    tasks, stats = feature_metadata_machine_translate.collect_tasks(
        records=records,
        fields=normalized_fields,
        locales=normalized_locales,
        target_by_locale=target_by_locale,
        existing_keys=set(),
        stringify_non_string=stringify_non_string,
        skip_numeric_strings=skip_numeric_strings,
    )
    task_evidence = [{"key": list(task.key), "pending": task.key not in existing_keys} for task in tasks]
    stats["existing_current_row_count"] = sum(task.key in existing_keys for task in tasks)
    if not refresh_current:
        tasks = [task for task in tasks if task.key not in existing_keys]
    report = {
        "tasks": task_evidence,
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
    reserved_outputs: Sequence[Path] = (),
) -> dict[str, Any]:
    inputs = [canonical_sidecar, *([translation_source] if translation_source else []), *([schema] if schema else [])]
    expected = translation_local_io.snapshots(inputs)
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
    stem = output_stem or default_output_stem(canonical_sidecar)
    if not stem or Path(stem).name != stem or stem in {".", ".."}:
        raise FeatureMetadataDocumentTranslateError("output stem must be a filename stem")
    workbook_paths = [output_dir / (f"{stem}.for-translate.xlsx" if len(shards) == 1 else f"{stem}.for-translate.part-{index:03d}.xlsx") for index in range(1, len(shards) + 1)]
    manifest_path = output_dir / f"{stem}.for-translate.manifest.json"
    translation_local_io.validate_paths(inputs=inputs, outputs=[*workbook_paths, manifest_path, *reserved_outputs])

    destinations = {path: translation_local_io.FileSnapshot.capture(path) for path in [*workbook_paths, manifest_path]}
    manifest_entries: list[dict[str, Any]] = []
    shard_reports: list[dict[str, Any]] = []
    for index, shard in enumerate(shards, start=1):
        filename = f"{stem}.for-translate.xlsx" if len(shards) == 1 else f"{stem}.for-translate.part-{index:03d}.xlsx"
        workbook_path = output_dir / filename
        rows = [["hash", "text"], *[[entry["source_value_hash"], entry["source_text"]] for entry in shard]]
        write_xlsx_rows(workbook_path, rows, expected=[*expected, destinations[workbook_path]], protected=[*inputs, *reserved_outputs])
        shard_reports.append(
            {
                "path": str(workbook_path),
                "name": filename,
                "id": shard_id([entry["source_value_hash"] for entry in shard]),
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
        "canonical_sha256": expected[0].sha256,
        "schema_sha256": translation_local_io.file_sha256(schema) if schema else None,
        "collection_options": {"stringify_non_string": stringify_non_string, "skip_numeric_strings": skip_numeric_strings},
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
    validate_manifest(payload)
    translation_local_io.write_json(manifest_path, payload, expected=[*expected, destinations[manifest_path]], protected=[*inputs, *workbook_paths, *reserved_outputs])
    payload["manifest"] = str(manifest_path)
    return payload


def shard_id(hashes: Sequence[str]) -> str:
    return release_feature_model.sha256_hex(release_feature_model.canonical_json(sorted(hashes)))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise FeatureMetadataDocumentTranslateError(message)


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"duplicate manifest JSON key: {key}")
        result[key] = value
    return result


def exact_hash(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None


def validate_manifest(payload: Mapping[str, Any]) -> None:
    require(isinstance(payload, dict) and payload.get("schema") == MANIFEST_SCHEMA,
            "unsupported manifest; re-export with current canonical/schema/CSV and approved fields/locales; transfer text only by verified intact hashes, never position")
    for field in ("canonical_sha256", "schema_sha256"):
        value = payload.get(field)
        require((field == "schema_sha256" and value is None) or isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None, f"invalid {field}")
    for field in ("asset_slug", "release"):
        require(isinstance(payload.get(field), str), f"invalid manifest {field}")
    for field in ("locales", "fields"):
        values = payload.get(field)
        require(isinstance(values, list) and bool(values) and all(isinstance(v, str) and v.strip() == v and bool(v) for v in values), f"invalid manifest {field}")
        require(len(values) == len(set(values)), f"duplicate manifest {field}")
    require(all(feature_metadata_localization.normalize_locale(v) == v for v in payload["locales"]), "manifest locales must be normalized")
    targets = payload.get("target_by_locale")
    require(isinstance(targets, dict) and set(targets) == set(payload["locales"]) and all(isinstance(v, str) and v.strip() for v in targets.values()), "invalid target mappings")
    options = payload.get("collection_options")
    require(isinstance(options, dict) and set(options) == {"stringify_non_string", "skip_numeric_strings"} and all(type(v) is bool for v in options.values()), "invalid collection options")
    tasks = payload.get("tasks")
    require(isinstance(tasks, list), "manifest tasks must be a list")
    keys: set[tuple[str, ...]] = set()
    pending_hashes: set[str] = set()
    for task in tasks:
        require(isinstance(task, dict) and set(task) == {"key", "pending"} and type(task["pending"]) is bool, "invalid manifest task")
        key = task["key"]
        require(isinstance(key, list) and len(key) == 4 and all(isinstance(v, str) for v in key), "invalid manifest task key")
        release_feature_model.validate_feature_id(key[0])
        require(key[1] in payload["fields"] and key[2] in payload["locales"] and exact_hash(key[3]), "invalid task field/locale/hash")
        require(tuple(key) not in keys, "duplicate manifest task key")
        keys.add(tuple(key))
        if task["pending"]:
            pending_hashes.add(key[3])
    shards = payload.get("shards")
    entries = payload.get("entries")
    require(isinstance(shards, list) and bool(shards) and isinstance(entries, list), "manifest requires shards and entries")
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    ids: set[str] = set()
    for shard in shards:
        require(isinstance(shard, dict) and isinstance(shard.get("name"), str) and bool(shard["name"]), "invalid shard name")
        require(shard["name"] not in grouped, "duplicate shard name")
        require(isinstance(shard.get("id"), str) and shard["id"] not in ids, "duplicate/invalid shard id")
        ids.add(shard["id"])
        grouped[shard["name"]] = []
    hashes: set[str] = set()
    for entry in entries:
        require(isinstance(entry, dict) and isinstance(entry.get("shard"), str) and entry["shard"] in grouped, "entry has missing/foreign shard")
        digest = entry.get("source_value_hash")
        require(exact_hash(digest) and digest not in hashes, "duplicate/invalid manifest source hash")
        require(isinstance(entry.get("source_text"), str) and bool(entry["source_text"].strip()), "invalid source text")
        hashes.add(digest)
        grouped[entry["shard"]].append(entry)
    require(hashes == pending_hashes, "manifest entry hashes do not equal pending task hashes")
    for shard in shards:
        members = grouped[shard["name"]]
        require(type(shard.get("data_row_count")) is int and shard["data_row_count"] == len(members), "shard row count mismatch")
        require(shard["id"] == shard_id([e["source_value_hash"] for e in members]), "shard identity mismatch")
        require(bool(members) or len(shards) == 1 and not entries, "unexpected empty shard")
        require([e.get("row_number") for e in members] == list(range(2, len(members) + 2)), "invalid entry row hints")
    require(type(payload.get("shard_count")) is int and payload["shard_count"] == len(shards), "shard count mismatch")
    require(type(payload.get("unique_source_value_count")) is int and payload["unique_source_value_count"] == len(entries), "entry count mismatch")
    require(type(payload.get("requested_task_count")) is int and payload["requested_task_count"] == sum(t["pending"] for t in tasks), "pending task count mismatch")


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_object,
                         parse_constant=lambda value: require(False, f"nonfinite JSON: {value}"))
    validate_manifest(payload)
    return payload


def translated_values_for_locale(*, locale: str, files: Sequence[Path], manifest: Mapping[str, Any]) -> dict[str, str]:
    expected_ids = {shard["id"] for shard in manifest["shards"]}
    require(len(files) == len(expected_ids), f"locale {locale}: wrong workbook count; provide each exported shard exactly once")
    observed_ids: set[str] = set()
    translations: dict[str, str] = {}
    for path in files:
        rows = read_xlsx_rows(path)
        require(bool(rows) and rows[0] == ["hash", "text"], f"{path}: expected exact hash,text header")
        shard_values: dict[str, str] = {}
        for number, row in enumerate(rows[1:], start=2):
            require(len(row) == 2 and exact_hash(row[0]), f"{path}:{number}: damaged/blank hash or wrong columns; restore verified original hashes or retranslate; no positional recovery")
            require(row[0] not in shard_values and row[0] not in translations, f"{path}:{number}: duplicate source hash")
            require(bool(row[1].strip()), f"{path}:{number}: blank translated value")
            shard_values[row[0]] = row[1]
        identity = shard_id(list(shard_values))
        require(identity in expected_ids and identity not in observed_ids, f"{path}: missing/extra/foreign rows or duplicate shard; restore exact exported hash membership")
        observed_ids.add(identity)
        translations.update(shard_values)
    require(observed_ids == expected_ids, "missing translated shard")
    return translations


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
    if translation_source is None and output_translation_source.exists():
        translation_source = output_translation_source
    inputs = [manifest_path, canonical_sidecar, *([schema] if schema else []), *[p for paths in translated_files.values() for p in paths]]
    if translation_source and translation_source.absolute() != output_translation_source.absolute():
        inputs.append(translation_source)
    translation_local_io.validate_paths(inputs=inputs, outputs=[output_translation_source])
    expected = translation_local_io.snapshots([*inputs, output_translation_source])
    manifest = load_manifest(manifest_path)
    require(translation_local_io.file_sha256(canonical_sidecar) == manifest["canonical_sha256"], "canonical snapshot changed; re-export against current files")
    require((translation_local_io.file_sha256(schema) if schema else None) == manifest["schema_sha256"], "schema snapshot changed or missing; re-export with the same schema")
    require(not asset_slug or not manifest["asset_slug"] or asset_slug == manifest["asset_slug"], "asset override disagrees with manifest")
    require(not release or not manifest["release"] or release == manifest["release"], "release override disagrees with manifest")
    require(review_state != feature_metadata_localization.FAILED_REVIEW_STATE, "successful review state cannot be translation_failed")
    locales, fields = manifest["locales"], manifest["fields"]
    normalized_files: dict[str, list[Path]] = {}
    for locale, paths in translated_files.items():
        normalized = feature_metadata_localization.normalize_locale(locale)
        require(normalized not in normalized_files and normalized in locales, "duplicate/foreign translated locale")
        normalized_files[normalized] = list(paths)
    reuse: dict[str, str] = {}
    for destination, source in (reuse_locale or {}).items():
        destination = feature_metadata_localization.normalize_locale(destination)
        source = feature_metadata_localization.normalize_locale(source)
        require(destination not in reuse and destination not in normalized_files and destination in locales and source in normalized_files,
                "reuse requires one direct source locale and no duplicate/direct destination; chains/cycles are not supported")
        reuse[destination] = source
    require(set(normalized_files) | set(reuse) == set(locales), "missing translated locale files or reuse mapping")
    translated_by_locale = {locale: translated_values_for_locale(locale=locale, files=files, manifest=manifest) for locale, files in normalized_files.items()}
    for destination, source in reuse.items():
        translated_by_locale[destination] = translated_by_locale[source]

    all_tasks, existing_rows, report = collect_pending_tasks(
        canonical_sidecar=canonical_sidecar, translation_source=translation_source,
        locales=locales, fields=fields, schema=schema, refresh_current=True,
        expected_asset_slug=asset_slug or manifest["asset_slug"] or None,
        expected_release=release or manifest["release"] or None,
        target_overrides=manifest["target_by_locale"], **manifest["collection_options"],
    )
    current_evidence = report.pop("tasks")
    current_keys = {task.key for task in all_tasks}
    require(current_keys == {tuple(task["key"]) for task in manifest["tasks"]}, "manifest task identity does not match canonical tasks; re-export")
    completed = {tuple(task["key"]) for task in current_evidence if not task["pending"]}
    exported_pending = {tuple(task["key"]) for task in manifest["tasks"] if task["pending"]}
    require(current_keys - exported_pending <= completed, "previously completed tasks are now missing/failed; re-export with current CSV")
    source_text = {task.source_value_hash: task.source_text for task in all_tasks}
    require(all(source_text.get(entry["source_value_hash"]) == entry["source_text"] for entry in manifest["entries"]), "manifest source text does not match canonical tasks")
    tasks = [task for task in all_tasks if task.key in exported_pending and (refresh_current or task.key not in completed)]
    replaced_keys = {task.key for task in tasks}
    existing_rows = [row for row in existing_rows if feature_metadata_machine_translate.translation_key(row) not in replaced_keys]
    generated_rows = [
        {"feature_id": task.feature_id, "field": task.field, "locale": task.locale,
         "source_value_hash": task.source_value_hash, "value": translated_by_locale[task.locale][task.source_value_hash],
         "review_state": review_state,
         "notes": notes + (f"; reused_locale={reuse[task.locale]}" if task.locale in reuse else "")}
        for task in tasks
    ]
    output_rows = [*existing_rows, *generated_rows]
    feature_metadata_machine_translate.write_translation_source(output_translation_source, output_rows, expected=expected, protected=inputs)
    return {
        **report, "schema": MANIFEST_SCHEMA, "valid": True, "complete": True,
        "manifest": str(manifest_path), "canonical_sidecar": str(canonical_sidecar),
        "translation_source": str(translation_source or ""), "output_translation_source": str(output_translation_source),
        "locales": locales, "fields": fields, "refresh_current": refresh_current,
        "requested_task_count": len(tasks), "existing_row_count": len(existing_rows),
        "generated_row_count": len(generated_rows), "output_row_count": len(output_rows),
        "outstanding_current_task_count": 0,
    }


def write_or_print_report(payload: Mapping[str, Any], report_path: Path | None, *, protected: Sequence[Path], expected: Sequence[translation_local_io.FileSnapshot]) -> None:
    summary = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if report_path:
        translation_local_io.write_json(report_path, payload, protected=protected, expected=expected)
    print(summary, end="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="Create two-column .xlsx shards and a task/hash-bound manifest.")
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
        help="Translated workbook as locale=path. Repeat for all shards in any order; intact hashes identify rows and shards.",
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
        report_expected = translation_local_io.snapshots([args.report]) if args.report else ()
        protected = [args.canonical_sidecar, *([args.translation_source] if args.translation_source else []), *([args.schema] if args.schema else [])]
        if args.command == "import":
            protected.extend([args.manifest, args.output_translation_source])
            protected.extend(p for paths in parse_path_mapping(args.translated_file, option_name="--translated-file").values() for p in paths)
        if args.report:
            translation_local_io.validate_paths(inputs=protected, outputs=[args.report])
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
                reserved_outputs=[args.report] if args.report else [],
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
        if args.command == "export":
            protected.extend([Path(payload["manifest"]), *[Path(shard["path"]) for shard in payload["shards"]]])
        write_or_print_report(payload, args.report, protected=protected, expected=report_expected)
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
        print(f"feature-metadata-document-translate failed: {exc}; earlier per-file commits may remain", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
