#!/usr/bin/env python3
"""Generate catalog and managed asset documentation from docs/assets/*.md."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import yaml


DEFAULT_BUCKET = "skytruth-shared-datasets-1"
SCHEMA_VERSION = 1
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)
H2_RE_TEMPLATE = r"(?ms)^## {heading}\s*\n.*?(?=^## |\Z)"
ASSET_SUMMARY_BLOCK_RE = re.compile(
    r"(?ms)\n*<!-- BEGIN GENERATED asset-summary -->.*?<!-- END GENERATED asset-summary -->\n*"
)

CATALOG_COLUMNS = [
    "asset_slug",
    "title",
    "category",
    "subcategory",
    "status",
    "access_tier",
    "owner",
    "update_cadence",
    "canonical_path",
    "canonical_format",
    "available_formats",
    "metadata_paths",
    "has_pmtiles",
    "has_geojson",
    "has_csv",
    "last_updated",
    "source",
    "license",
    "notes",
]

FRONTMATTER_KEYS = [
    "schema_version",
    "asset_slug",
    "title",
    "category",
    "subcategory",
    "status",
    "access_tier",
    "owner",
    "update_cadence",
    "canonical_format",
    "canonical_file",
    "available_formats",
    "metadata_paths",
    "last_updated",
    "source",
    "license",
    "notes",
    "files",
]

REQUIRED_SCALAR_FIELDS = [
    "asset_slug",
    "title",
    "category",
    "subcategory",
    "status",
    "access_tier",
    "owner",
    "update_cadence",
    "canonical_format",
    "canonical_file",
    "last_updated",
    "source",
    "license",
]

REQUIRED_SECTIONS = [
    "## What this is",
    "## Files",
    "## Schema notes",
    "## Properties / columns",
    "## Update notes",
]

APPROVED_CANONICAL_FORMATS = {"fgb", "cog", "zarr", "pmtiles", "geojson", "ndgeojson", "csv"}
PUBLISHED_ROLES = {"canonical", "companion"}
FILE_ROLES = {"canonical", "companion", "release", "run-record", "source", "preview", "metadata"}
ACCESS_TIERS = {"public", "private"}


@dataclass
class FileEntry:
    path: str
    format: str
    role: str
    purpose: str


@dataclass
class AssetDoc:
    path: Path
    metadata: dict[str, Any]
    body: str
    warnings: list[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return str(self.metadata["asset_slug"])


class CatalogDocsError(ValueError):
    """Raised when generated catalog/docs inputs are invalid."""


def load_categories(path: Path) -> dict[str, set[str]]:
    payload = yaml.safe_load(path.read_text()) or {}
    categories = payload.get("categories") or {}
    return {
        str(category): set((data.get("subcategories") or {}).keys())
        for category, data in categories.items()
    }


def load_catalog_rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="") as handle:
        return {row["asset_slug"]: row for row in csv.DictReader(handle) if row.get("asset_slug")}


def split_frontmatter(text: str, path: Path) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise CatalogDocsError(f"{path}: missing YAML frontmatter")
    payload = yaml.safe_load(match.group(1)) or {}
    if not isinstance(payload, dict):
        raise CatalogDocsError(f"{path}: frontmatter must be a YAML mapping")
    return payload, match.group(2)


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat") and not isinstance(value, str):
        return value.isoformat()
    return str(value)


def normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        delimiter = ";" if ";" in value else ","
        return [part.strip() for part in value.split(delimiter) if part.strip()]
    if isinstance(value, (list, tuple)):
        return [as_text(part).strip() for part in value if as_text(part).strip()]
    raise CatalogDocsError(f"expected list or delimited string, got {type(value).__name__}")


def infer_format(path: str, canonical_format: str | None = None) -> str:
    lowered = path.lower()
    if lowered == "latest/manifest.json" and canonical_format == "zarr":
        return "zarr"
    for suffix, format_name in (
        (".ndgeojson", "ndgeojson"),
        (".geojson", "geojson"),
        (".pmtiles", "pmtiles"),
        (".fgb", "fgb"),
        (".csv", "csv"),
        (".tiff", "cog"),
        (".tif", "cog"),
        (".json", "json"),
        (".png", "png"),
        (".jpg", "jpg"),
        (".jpeg", "jpg"),
        (".webp", "webp"),
    ):
        if lowered.endswith(suffix):
            return format_name
    return "unknown"


def infer_role(path: str, canonical_file: str) -> str:
    if path == canonical_file:
        return "canonical"
    if path.startswith("latest/"):
        return "companion"
    if path.startswith("releases/"):
        return "release"
    if path.startswith("runs/"):
        return "run-record"
    if path.startswith("source/") or path.startswith("sources/") or path.startswith("archive/"):
        return "source"
    if path.startswith("previews/"):
        return "preview"
    return "metadata"


def parse_files_table(body: str, canonical_file: str, canonical_format: str) -> list[dict[str, str]]:
    match = re.search(H2_RE_TEMPLATE.format(heading=re.escape("Files")), body)
    if not match:
        return []
    entries: list[dict[str, str]] = []
    for raw_line in match.group(0).splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or "---" in line or line.lower().startswith("| file"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        file_path = cells[0].strip("` ")
        if not file_path:
            continue
        entries.append(
            {
                "path": file_path,
                "format": infer_format(file_path, canonical_format),
                "role": infer_role(file_path, canonical_file),
                "purpose": cells[1],
            }
        )
    return entries


def canonical_file_from_row(row: dict[str, str] | None) -> str:
    if not row:
        return ""
    canonical_path = row.get("canonical_path", "")
    if "/latest/" not in canonical_path:
        return ""
    return "latest/" + canonical_path.split("/latest/", 1)[1]


def normalize_file_entries(raw_files: Any, canonical_file: str, canonical_format: str) -> list[dict[str, str]]:
    if not isinstance(raw_files, list):
        raise CatalogDocsError("files must be a list")
    normalized = []
    for index, raw_entry in enumerate(raw_files, start=1):
        if not isinstance(raw_entry, dict):
            raise CatalogDocsError(f"files[{index}] must be a mapping")
        path = as_text(raw_entry.get("path")).strip()
        purpose = as_text(raw_entry.get("purpose")).strip()
        format_name = as_text(raw_entry.get("format")).strip() or infer_format(path, canonical_format)
        role = as_text(raw_entry.get("role")).strip() or infer_role(path, canonical_file)
        if not path:
            raise CatalogDocsError(f"files[{index}] is missing path")
        if not purpose:
            raise CatalogDocsError(f"files[{index}] is missing purpose")
        normalized.append(
            {
                "path": path,
                "format": format_name,
                "role": role,
                "purpose": purpose,
            }
        )
    return normalized


def normalize_metadata(
    *,
    path: Path,
    raw: dict[str, Any],
    body: str,
    categories: dict[str, set[str]],
    catalog_row: dict[str, str] | None,
    allow_legacy: bool,
) -> tuple[dict[str, Any], list[str]]:
    metadata: dict[str, Any] = {}
    warnings: list[str] = []

    if allow_legacy and not raw.get("schema_version"):
        metadata["schema_version"] = SCHEMA_VERSION
    else:
        metadata["schema_version"] = raw.get("schema_version")

    for key in REQUIRED_SCALAR_FIELDS:
        value = raw.get(key)
        if key == "access_tier" and not value and allow_legacy:
            value = (catalog_row or {}).get("access_tier") or "public"
        if key == "canonical_file" and not value and allow_legacy:
            value = canonical_file_from_row(catalog_row)
        if not value and catalog_row and key in {"last_updated", "source", "license"} and allow_legacy:
            value = catalog_row.get(key)
        metadata[key] = as_text(value).strip()

    notes = raw.get("notes")
    if notes is None and catalog_row and allow_legacy:
        notes = catalog_row.get("notes", "")
    metadata["notes"] = as_text(notes).strip()

    available_formats = normalize_list(raw.get("available_formats"))
    if not available_formats and catalog_row and allow_legacy:
        available_formats = normalize_list(catalog_row.get("available_formats", ""))
    metadata["available_formats"] = available_formats

    metadata_paths = normalize_list(raw.get("metadata_paths"))
    if not metadata_paths and catalog_row and allow_legacy:
        metadata_paths = normalize_list(catalog_row.get("metadata_paths", ""))
    if not metadata_paths:
        metadata_paths = ["README.md"]
    metadata["metadata_paths"] = metadata_paths

    files = raw.get("files")
    if files is None and allow_legacy:
        files = parse_files_table(body, metadata["canonical_file"], metadata["canonical_format"])
    metadata["files"] = normalize_file_entries(files, metadata["canonical_file"], metadata["canonical_format"])

    validate_metadata(path, metadata, categories)
    rendered_body = render_body(path, metadata, body)
    validate_body(path, metadata, rendered_body)
    lowered_body = rendered_body.lower()
    if re.search(r"\bneeds?\s+source confirmation\b", lowered_body) or "needing source confirmation" in lowered_body:
        warnings.append(f"{path}: property descriptions include source-confirmation placeholders")
    return metadata, warnings


def validate_metadata(path: Path, metadata: dict[str, Any], categories: dict[str, set[str]]) -> None:
    for key in REQUIRED_SCALAR_FIELDS:
        if not metadata.get(key):
            raise CatalogDocsError(f"{path}: missing required frontmatter field {key!r}")
    if metadata.get("schema_version") != SCHEMA_VERSION:
        raise CatalogDocsError(f"{path}: schema_version must be {SCHEMA_VERSION}")
    if metadata.get("access_tier") not in ACCESS_TIERS:
        raise CatalogDocsError(f"{path}: access_tier must be one of: {', '.join(sorted(ACCESS_TIERS))}")
    slug = metadata["asset_slug"]
    if not SLUG_RE.fullmatch(slug):
        raise CatalogDocsError(f"{path}: asset_slug must be lowercase kebab-case")
    if path.stem != slug:
        raise CatalogDocsError(f"{path}: filename stem must match asset_slug {slug!r}")
    category = metadata["category"]
    subcategory = metadata["subcategory"]
    if category not in categories:
        raise CatalogDocsError(f"{path}: unknown category {category!r}")
    if subcategory not in categories[category]:
        raise CatalogDocsError(f"{path}: unknown subcategory {category}/{subcategory}")
    if metadata["canonical_format"] not in APPROVED_CANONICAL_FORMATS:
        raise CatalogDocsError(f"{path}: unsupported canonical_format {metadata['canonical_format']!r}")
    if not metadata["canonical_file"].startswith("latest/"):
        raise CatalogDocsError(f"{path}: canonical_file must be under latest/")
    if "README.md" not in metadata["metadata_paths"]:
        raise CatalogDocsError(f"{path}: metadata_paths must include README.md")

    canonical_entries = [entry for entry in metadata["files"] if entry["path"] == metadata["canonical_file"]]
    if len(canonical_entries) != 1:
        raise CatalogDocsError(f"{path}: files must contain exactly one entry for canonical_file")
    canonical_entry = canonical_entries[0]
    if canonical_entry["role"] != "canonical":
        raise CatalogDocsError(f"{path}: canonical_file entry must use role 'canonical'")
    if canonical_entry["format"] != metadata["canonical_format"]:
        raise CatalogDocsError(f"{path}: canonical_file format must match canonical_format")
    for entry in metadata["files"]:
        if entry["role"] not in FILE_ROLES:
            raise CatalogDocsError(f"{path}: file {entry['path']} has unsupported role {entry['role']!r}")
    latest_formats = published_latest_formats(metadata["files"])
    if latest_formats != metadata["available_formats"]:
        raise CatalogDocsError(
            f"{path}: available_formats {metadata['available_formats']} must match latest canonical/companion file formats {latest_formats}"
        )


def validate_body(path: Path, metadata: dict[str, Any], body: str) -> None:
    if metadata.get("status") != "active":
        return
    missing = [section for section in REQUIRED_SECTIONS if section not in body]
    if missing:
        raise CatalogDocsError(f"{path}: missing required section(s): {', '.join(missing)}")


def published_latest_formats(files: list[dict[str, str]]) -> list[str]:
    formats: list[str] = []
    for entry in files:
        if entry["role"] not in PUBLISHED_ROLES or not entry["path"].startswith("latest/"):
            continue
        format_name = entry["format"]
        if format_name not in formats:
            formats.append(format_name)
    return formats


def read_asset_docs(
    *,
    docs_dir: Path,
    categories: dict[str, set[str]],
    catalog_rows: dict[str, dict[str, str]],
    allow_legacy: bool,
) -> list[AssetDoc]:
    docs: list[AssetDoc] = []
    seen: set[str] = set()
    for path in sorted(docs_dir.glob("*.md")):
        if path.name == "index.md":
            continue
        raw, body = split_frontmatter(path.read_text(), path)
        slug = as_text(raw.get("asset_slug")).strip()
        if slug in seen:
            raise CatalogDocsError(f"{path}: duplicate asset_slug {slug!r}")
        metadata, warnings = normalize_metadata(
            path=path,
            raw=raw,
            body=body,
            categories=categories,
            catalog_row=catalog_rows.get(slug),
            allow_legacy=allow_legacy,
        )
        seen.add(metadata["asset_slug"])
        docs.append(AssetDoc(path=path, metadata=metadata, body=body, warnings=warnings))
    return docs


def asset_root(metadata: dict[str, Any]) -> str:
    return f"{metadata['category']}/{metadata['subcategory']}/{metadata['asset_slug']}"


def canonical_gs_uri(metadata: dict[str, Any], bucket: str) -> str:
    return f"gs://{bucket}/{asset_root(metadata)}/{metadata['canonical_file']}"


def catalog_row(metadata: dict[str, Any], bucket: str) -> dict[str, str]:
    formats = metadata["available_formats"]
    return {
        "asset_slug": metadata["asset_slug"],
        "title": metadata["title"],
        "category": metadata["category"],
        "subcategory": metadata["subcategory"],
        "status": metadata["status"],
        "access_tier": metadata["access_tier"],
        "owner": metadata["owner"],
        "update_cadence": metadata["update_cadence"],
        "canonical_path": canonical_gs_uri(metadata, bucket),
        "canonical_format": metadata["canonical_format"],
        "available_formats": ";".join(formats),
        "metadata_paths": ";".join(metadata["metadata_paths"]),
        "has_pmtiles": str("pmtiles" in formats).lower(),
        "has_geojson": str("geojson" in formats).lower(),
        "has_csv": str("csv" in formats).lower(),
        "last_updated": metadata["last_updated"],
        "source": metadata["source"],
        "license": metadata["license"],
        "notes": metadata.get("notes", ""),
    }


def render_catalog_csv(docs: Sequence[AssetDoc], bucket: str) -> str:
    from io import StringIO

    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=CATALOG_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for doc in sorted(docs, key=lambda item: (item.metadata["category"], item.metadata["subcategory"], item.slug)):
        writer.writerow(catalog_row(doc.metadata, bucket))
    return output.getvalue()


def markdown_link(path: str, label: str) -> str:
    return f"[{label}]({path})"


def render_index(docs: Sequence[AssetDoc]) -> str:
    lines = [
        "# Shared Dataset Asset Index",
        "",
        "<!-- GENERATED by scripts/catalog_docs.py; do not edit by hand. -->",
        "",
    ]
    current_category = None
    for doc in sorted(docs, key=lambda item: (item.metadata["category"], item.metadata["subcategory"], item.slug)):
        metadata = doc.metadata
        if metadata["category"] != current_category:
            if current_category is not None:
                lines.append("")
            current_category = metadata["category"]
            lines.extend(
                [
                    f"## {current_category}",
                    "",
                    "| Asset | Subcategory | Status | Access tier | Formats | Last updated | Canonical file |",
                    "|---|---|---|---|---|---|---|",
                ]
            )
        formats = ";".join(metadata["available_formats"])
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_link(f"{metadata['asset_slug']}.md", metadata["title"]),
                    metadata["subcategory"],
                    metadata["status"],
                    metadata["access_tier"],
                    f"`{formats}`",
                    metadata["last_updated"],
                    f"`{metadata['canonical_file']}`",
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def summary_block(metadata: dict[str, Any]) -> str:
    formats = ", ".join(f"`{format_name}`" for format_name in metadata["available_formats"])
    return "\n".join(
        [
            "<!-- BEGIN GENERATED asset-summary -->",
            f"- **Status:** {metadata['status']}",
            f"- **Access tier:** {metadata['access_tier']}",
            f"- **Owner:** {metadata['owner']}",
            f"- **Last updated:** {metadata['last_updated']}",
            f"- **Update cadence:** {metadata['update_cadence']}",
            f"- **Canonical file:** `{metadata['canonical_file']}`",
            f"- **Available formats:** {formats}",
            f"- **Source:** {metadata['source']}",
            f"- **License / terms:** {metadata['license']}",
            "<!-- END GENERATED asset-summary -->",
        ]
    )


def files_table_block(metadata: dict[str, Any]) -> str:
    lines = [
        "<!-- BEGIN GENERATED files-table -->",
        "| File | Format | Role | Purpose |",
        "|---|---|---|---|",
    ]
    for entry in metadata["files"]:
        lines.append(f"| `{entry['path']}` | `{entry['format']}` | `{entry['role']}` | {entry['purpose']} |")
    lines.append("<!-- END GENERATED files-table -->")
    return "\n".join(lines)


def replace_summary(body: str, metadata: dict[str, Any]) -> str:
    body = ASSET_SUMMARY_BLOCK_RE.sub("\n\n", body)
    heading = f"# {metadata['title']}"
    if re.search(r"(?m)^# .+$", body):
        body = re.sub(r"(?m)^# .+$", heading, body, count=1)
    else:
        body = f"{heading}\n\n{body.lstrip()}"
    pattern = r"(?ms)(^# [^\n]*\n)(.*?)(?=^## |\Z)"
    replacement = r"\1\n" + summary_block(metadata) + "\n\n"
    return re.sub(pattern, replacement, body, count=1)


def replace_files_section(body: str, metadata: dict[str, Any]) -> str:
    body = body.replace("<!-- END GENERATED files-table -->## ", "<!-- END GENERATED files-table -->\n\n## ")
    replacement = "## Files\n\n" + files_table_block(metadata) + "\n\n"
    pattern = H2_RE_TEMPLATE.format(heading=re.escape("Files"))
    if re.search(pattern, body):
        return re.sub(pattern, replacement, body, count=1)
    schema_index = body.find("## Schema notes")
    if schema_index >= 0:
        return body[:schema_index].rstrip() + "\n\n" + replacement + body[schema_index:].lstrip()
    return body.rstrip() + "\n\n" + replacement.rstrip() + "\n"


def render_body(path: Path, metadata: dict[str, Any], body: str) -> str:
    rendered = replace_summary(body, metadata)
    rendered = replace_files_section(rendered, metadata)
    if not rendered.endswith("\n"):
        rendered += "\n"
    return rendered


def render_frontmatter(metadata: dict[str, Any]) -> str:
    ordered = {key: metadata[key] for key in FRONTMATTER_KEYS}
    return yaml.safe_dump(ordered, sort_keys=False, width=120)


def render_asset_doc(doc: AssetDoc) -> str:
    return "---\n" + render_frontmatter(doc.metadata) + "---\n\n" + render_body(doc.path, doc.metadata, doc.body).lstrip().rstrip() + "\n"


def write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return True


def compare_file(path: Path, expected: str, errors: list[str]) -> None:
    actual = path.read_text() if path.exists() else ""
    if actual != expected:
        errors.append(f"{path}: generated content is stale")


def validate_catalog_slug_set(catalog_path: Path, docs: Sequence[AssetDoc], errors: list[str]) -> None:
    rows = load_catalog_rows(catalog_path)
    catalog_slugs = set(rows)
    doc_slugs = {doc.slug for doc in docs}
    missing_docs = sorted(catalog_slugs - doc_slugs)
    extra_docs = sorted(doc_slugs - catalog_slugs)
    if missing_docs:
        errors.append(f"{catalog_path}: catalog rows missing docs: {', '.join(missing_docs)}")
    if extra_docs:
        errors.append(f"{catalog_path}: docs missing catalog rows: {', '.join(extra_docs)}")


def check_outputs(
    *,
    docs: Sequence[AssetDoc],
    catalog_path: Path,
    index_path: Path,
    bucket: str,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    validate_catalog_slug_set(catalog_path, docs, errors)
    compare_file(catalog_path, render_catalog_csv(docs, bucket), errors)
    compare_file(index_path, render_index(docs), errors)
    for doc in docs:
        compare_file(doc.path, render_asset_doc(doc), errors)
        warnings.extend(doc.warnings)
    return errors, warnings


def generate_outputs(
    *,
    docs: Sequence[AssetDoc],
    catalog_path: Path,
    index_path: Path,
    bucket: str,
) -> list[Path]:
    changed: list[Path] = []
    for doc in docs:
        if write_if_changed(doc.path, render_asset_doc(doc)):
            changed.append(doc.path)
    if write_if_changed(catalog_path, render_catalog_csv(docs, bucket)):
        changed.append(catalog_path)
    if write_if_changed(index_path, render_index(docs)):
        changed.append(index_path)
    return changed


def export_readmes(docs: Sequence[AssetDoc], output_dir: Path) -> list[Path]:
    changed: list[Path] = []
    for doc in docs:
        target = output_dir / asset_root(doc.metadata) / "README.md"
        text = render_body(doc.path, doc.metadata, doc.body)
        if write_if_changed(target, text):
            changed.append(target)
    return changed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-dir", type=Path, default=Path("docs/assets"))
    parser.add_argument("--catalog", type=Path, default=Path("catalog/shared-datasets-catalog.csv"))
    parser.add_argument("--categories", type=Path, default=Path("catalog/categories.yaml"))
    parser.add_argument("--index", type=Path, default=Path("docs/assets/index.md"))
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="Fail if generated catalog/docs outputs are stale.")
    subparsers.add_parser("generate", help="Update generated catalog/docs outputs.")
    export_parser = subparsers.add_parser("export-readmes", help="Export bucket-ready README files.")
    export_parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def command_context(args: argparse.Namespace, *, allow_legacy: bool) -> list[AssetDoc]:
    categories = load_categories(args.categories)
    catalog_rows = load_catalog_rows(args.catalog)
    return read_asset_docs(
        docs_dir=args.docs_dir,
        categories=categories,
        catalog_rows=catalog_rows,
        allow_legacy=allow_legacy,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        docs = command_context(args, allow_legacy=args.command == "generate")
        if args.command == "generate":
            changed = generate_outputs(docs=docs, catalog_path=args.catalog, index_path=args.index, bucket=args.bucket)
            for path in changed:
                print(f"updated {path}")
            print(f"generated {len(docs)} asset doc(s)")
            return 0
        if args.command == "check":
            errors, warnings = check_outputs(docs=docs, catalog_path=args.catalog, index_path=args.index, bucket=args.bucket)
            for warning in warnings:
                print(f"warning: {warning}", file=sys.stderr)
            if errors:
                for error in errors:
                    print(f"error: {error}", file=sys.stderr)
                return 1
            print(f"catalog/docs are current for {len(docs)} asset doc(s)")
            return 0
        if args.command == "export-readmes":
            changed = export_readmes(docs, args.output_dir)
            for path in changed:
                print(f"exported {path}")
            print(f"exported README files for {len(docs)} asset doc(s)")
            return 0
    except CatalogDocsError as exc:
        print(f"catalog-docs: {exc}", file=sys.stderr)
        return 1
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
