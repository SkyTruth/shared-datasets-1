#!/usr/bin/env python3
"""Translate only reuse gaps with the existing hash-verified document workflow.

The small task projection is scratch data, never a publishable feature dataset.
Its IDs identify translation requests, not protected areas. The gap manifest
binds the projection and document manifest to the original pending task files.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import feature_metadata_document_translate as documents, feature_metadata_localization as localization, release_feature_model as model, translation_local_io as local_io
from scripts.feature_metadata_translation_reuse import read_supplement, require


def pending_tasks(paths: list[Path]) -> list[dict]:
    tasks = {}
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                require(row["reason"] in {"missing_translation", "conflicting_translations"}, "unknown pending reason")
                require(isinstance(row["source_value"], str) and row["source_value"].strip(), "only nonempty source text can be translated")
                require(localization.source_value_hash(row["source_value"]) == row["source_value_hash"], "pending source hash differs")
                require(localization.normalize_locale(row["locale"]) == row["locale"], "pending locale is not canonical")
                key = (row["field"], row["locale"], row["source_value_hash"])
                if key in tasks:
                    require(tasks[key]["source_value"] == row["source_value"], "pending task text conflicts")
                tasks[key] = {name: row[name] for name in ("field", "locale", "source_value_hash", "source_value")}
    require(bool(tasks), "no pending translations")
    return [tasks[key] for key in sorted(tasks)]


def export_gaps(*, pending: list[Path], output_dir: Path) -> dict:
    require(not output_dir.exists(), "gap output directory must be new")
    snapshots = local_io.snapshots(pending)
    tasks = pending_tasks(pending)
    texts = {row["source_value_hash"]: row["source_value"] for row in tasks}
    locales = sorted({row["locale"] for row in tasks})
    output_dir.mkdir(parents=True)
    projection = output_dir / "translation-requests.metadata.ndjson.gz"
    records = []
    for index, (digest, value) in enumerate(sorted(texts.items()), 1):
        properties = {"text": value}
        records.append({"schema_version": 2, "asset_slug": "translation-requests", "release": "1970-01-01", "feature_id": str(index),
                        "identity_key": [digest], "geometry_hash": model.geometry_hash(None), "properties_hash": model.properties_hash(properties),
                        "properties": properties, "provenance": {"purpose": "local translation request projection; not a dataset"}})
    model.write_metadata_sidecar(records, projection)
    exported = documents.export_document_workbooks(canonical_sidecar=projection, translation_source=None, output_dir=output_dir,
                                                   locales=locales, fields=["text"], asset_slug="translation-requests", release="1970-01-01",
                                                   output_stem="translation-gaps")
    for snapshot in snapshots:
        snapshot.verify()
    manifest = {"schema": "translation_reuse_gaps_v1", "pending_sha256": {str(s.path): s.sha256 for s in snapshots},
                "projection": str(projection), "projection_sha256": local_io.file_sha256(projection),
                "document_manifest": exported["manifest"], "document_manifest_sha256": local_io.file_sha256(Path(exported["manifest"])),
                "task_count": len(tasks), "unique_source_texts": len(texts), "workbooks": [row["path"] for row in exported["shards"]]}
    local_io.write_json(output_dir / "gap-manifest.json", manifest)
    return manifest


def import_gaps(*, manifest_path: Path, translated_files: dict[str, list[Path]], output: Path, reuse_locale: dict[str, str] | None = None) -> dict:
    manifest = json.loads(manifest_path.read_text())
    require(manifest["schema"] == "translation_reuse_gaps_v1", "unsupported gap manifest")
    projection, document_manifest = Path(manifest["projection"]), Path(manifest["document_manifest"])
    expected_hashes = {**manifest["pending_sha256"], str(projection): manifest["projection_sha256"], str(document_manifest): manifest["document_manifest_sha256"]}
    inputs = [manifest_path, *[Path(path) for path in expected_hashes], *[path for paths in translated_files.values() for path in paths]]
    snapshots = local_io.snapshots(inputs)
    for path, digest in expected_hashes.items():
        require(local_io.file_sha256(Path(path)) == digest, "gap export input changed; export again")
    tasks = pending_tasks([Path(path) for path in manifest["pending_sha256"]])
    require(len(tasks) == manifest["task_count"], "gap task count differs")
    imported_csv = manifest_path.parent / "translated-requests.csv"
    local_io.validate_paths(inputs=inputs, outputs=[imported_csv, output])
    report = documents.import_document_workbooks(manifest_path=document_manifest, canonical_sidecar=projection, translation_source=None,
                                                output_translation_source=imported_csv, translated_files=translated_files, reuse_locale=reuse_locale,
                                                notes="provider=google-document-translation; machine output, not human reviewed")
    rows = {(row.locale, row.source_value_hash): row for row in localization.read_translation_source(imported_csv)}
    with local_io.candidate_output(output, protected=[*inputs, imported_csv], expected=snapshots) as candidate:
        with candidate.open("w", encoding="utf-8") as handle:
            for task in tasks:
                row = rows[task["locale"], task["source_value_hash"]]
                handle.write(model.canonical_json({**task, "value": row.value, "review_state": row.review_state, "notes": row.notes}) + "\n")
        read_supplement(candidate, sorted({(task["field"], task["locale"]) for task in tasks}))
    return {"valid": True, "gap_task_count": len(tasks), "supplement": str(output), "supplement_sha256": local_io.file_sha256(output),
            "manifest_sha256": local_io.file_sha256(manifest_path), "document_import": report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--pending", type=Path, action="append", required=True)
    export.add_argument("--output-dir", type=Path, required=True)
    ingest = commands.add_parser("import")
    ingest.add_argument("--manifest", type=Path, required=True)
    ingest.add_argument("--translated-file", action="append", required=True)
    ingest.add_argument("--reuse-locale", action="append", default=[])
    ingest.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "export":
        report = export_gaps(pending=args.pending, output_dir=args.output_dir)
    else:
        report = import_gaps(manifest_path=args.manifest, translated_files=documents.parse_path_mapping(args.translated_file, option_name="--translated-file"),
                             reuse_locale=documents.parse_locale_mapping(args.reuse_locale, option_name="--reuse-locale"), output=args.output)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
