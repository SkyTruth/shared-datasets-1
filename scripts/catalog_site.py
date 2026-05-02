#!/usr/bin/env python3
"""Build the static shared-datasets catalog web preview."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote

import yaml


DEFAULT_BUCKET = "skytruth-shared-datasets-1"
DEFAULT_SITE_PREFIX = "_catalog/web"
DEFAULT_PMTILES_CDN_BASE_URL = "https://tiles.skytruth.org/pmtiles"
APPROVED_FORMATS = {"fgb", "cog", "zarr", "pmtiles", "geojson", "ndgeojson", "csv"}
ACCESS_TIERS = {"public", "private"}
REQUIRED_FIELDS = [
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
    "last_updated",
    "source",
    "license",
]
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n?(.*)\Z", re.DOTALL)
FRONTMATTER_BLOCK_RE = re.compile(r"\A---\n(?P<yaml>.*?)\n---\n?(?P<body>.*)\Z", re.DOTALL)
SECTION_RE = re.compile(r"(?ms)^## (?P<title>[^\n]+)\n(?P<body>.*?)(?=^## |\Z)")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
INLINE_CODE_RE = re.compile(r"`([^`]+)`")
MARKDOWN_TABLE_RE = re.compile(r"^\s*\|.*\|\s*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RELEASE_PATH_RE = re.compile(r"^releases/(?P<date>\d{4}-\d{2}-\d{2}|YYYY-MM-DD)/(?P<object>.+)$")
REFERENTIAL_TERMS_RE = re.compile(r"\bsee\b.{0,80}\bterms\b")


class CatalogSiteError(ValueError):
    """Raised when catalog site inputs are invalid."""


@dataclass(frozen=True)
class CatalogVersion:
    date: str
    canonical_path: str
    public_url: str
    pmtiles_path: str | None
    pmtiles_url: str | None
    available_formats: list[str]


@dataclass(frozen=True)
class CatalogAsset:
    slug: str
    title: str
    category: str
    subcategory: str
    status: str
    access_tier: str
    owner: str
    update_cadence: str
    canonical_path: str
    canonical_format: str
    available_formats: list[str]
    metadata_paths: list[str]
    has_pmtiles: bool
    has_geojson: bool
    has_csv: bool
    last_updated: str
    source: str
    license: str
    notes: str
    bounds: list[float] | None
    geometry_type: str | None
    row_count: int | None
    source_url: str | None
    public_url: str
    pmtiles_path: str | None
    pmtiles_url: str | None
    docs_path: str
    docs_url: str
    description: str
    license_flags: list[str]
    versions: list[CatalogVersion]
    sort_key: str


def load_categories(path: Path) -> dict[str, dict[str, str]]:
    payload = yaml.safe_load(path.read_text()) or {}
    categories = payload.get("categories") or {}
    if not isinstance(categories, dict):
        raise CatalogSiteError(f"{path}: categories must be a mapping")
    result: dict[str, dict[str, str]] = {}
    for category, data in categories.items():
        subcategories = (data or {}).get("subcategories") or {}
        if not isinstance(subcategories, dict):
            raise CatalogSiteError(f"{path}: category {category!r} subcategories must be a mapping")
        result[str(category)] = {str(key): str(value) for key, value in subcategories.items()}
    return result


def load_catalog_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise CatalogSiteError(f"{path}: catalog has no header row")
        return [dict(row) for row in reader]


def split_semicolon(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(";") if part.strip()]


def split_gs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise CatalogSiteError(f"expected gs:// URI, got {uri!r}")
    rest = uri[5:]
    bucket, separator, object_name = rest.partition("/")
    if not bucket or not separator or not object_name:
        raise CatalogSiteError(f"expected gs:// object URI, got {uri!r}")
    return bucket, object_name


def gs_to_https(uri: str) -> str:
    bucket, object_name = split_gs_uri(uri)
    return f"https://storage.googleapis.com/{bucket}/{quote(object_name)}"


def pmtiles_cdn_url(slug: str, access_tier: str) -> str:
    return f"{DEFAULT_PMTILES_CDN_BASE_URL}/{quote(access_tier)}/{quote(slug)}.pmtiles"


def latest_root(uri: str) -> str:
    if "/latest/" not in uri:
        raise CatalogSiteError(f"canonical path must include /latest/: {uri}")
    return uri.split("/latest/", 1)[0] + "/latest"


def path_for_format(canonical_path: str, slug: str, format_name: str) -> str:
    if format_name == "zarr":
        raise CatalogSiteError("cannot infer companion Zarr paths from the static catalog")
    extensions = {
        "fgb": ".fgb",
        "pmtiles": ".pmtiles",
        "geojson": ".geojson",
        "ndgeojson": ".ndgeojson",
        "csv": ".csv",
        "cog": ".tif",
    }
    extension = extensions.get(format_name)
    if extension is None:
        raise CatalogSiteError(f"unsupported format {format_name!r}")
    return f"{latest_root(canonical_path)}/{slug}{extension}"


def release_path_for_latest_path(latest_path: str, date: str) -> str:
    if "/latest/" not in latest_path:
        raise CatalogSiteError(f"latest path must include /latest/: {latest_path}")
    return latest_path.replace("/latest/", f"/releases/{date}/", 1)


def strip_markdown(text: str) -> str:
    text = MARKDOWN_LINK_RE.sub(r"\1", text)
    text = INLINE_CODE_RE.sub(r"\1", text)
    text = re.sub(r"[*_>#]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def read_doc_metadata(path: Path) -> dict[str, Any]:
    raw = path.read_text()
    match = FRONTMATTER_BLOCK_RE.match(raw)
    if not match:
        return {}
    payload = yaml.safe_load(match.group("yaml")) or {}
    if not isinstance(payload, dict):
        raise CatalogSiteError(f"{path}: frontmatter must be a mapping")
    return payload


def optional_text(metadata: dict[str, Any], key: str, *, doc_path: Path) -> str | None:
    if key not in metadata or metadata[key] is None:
        return None
    value = str(metadata[key]).strip()
    return value or None


def optional_int(metadata: dict[str, Any], key: str, *, doc_path: Path) -> int | None:
    if key not in metadata or metadata[key] is None or metadata[key] == "":
        return None
    try:
        value = int(metadata[key])
    except (TypeError, ValueError) as error:
        raise CatalogSiteError(f"{doc_path}: {key} must be an integer") from error
    if value < 0:
        raise CatalogSiteError(f"{doc_path}: {key} must be non-negative")
    return value


def optional_bounds(metadata: dict[str, Any], *, doc_path: Path) -> list[float] | None:
    if "bounds" not in metadata or metadata["bounds"] in (None, ""):
        return None
    raw_bounds = metadata["bounds"]
    if not isinstance(raw_bounds, (list, tuple)) or len(raw_bounds) != 4:
        raise CatalogSiteError(f"{doc_path}: bounds must be [min_lon, min_lat, max_lon, max_lat]")
    try:
        bounds = [float(value) for value in raw_bounds]
    except (TypeError, ValueError) as error:
        raise CatalogSiteError(f"{doc_path}: bounds values must be numbers") from error
    min_lon, min_lat, max_lon, max_lat = bounds
    if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180 and -90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        raise CatalogSiteError(f"{doc_path}: bounds must be valid WGS84 longitude/latitude values")
    if min_lon > max_lon or min_lat > max_lat:
        raise CatalogSiteError(f"{doc_path}: bounds minimums must not exceed maximums")
    return bounds


def frontmatter_license_flags(metadata: dict[str, Any], *, doc_path: Path) -> list[str]:
    if "license_flags" not in metadata or metadata["license_flags"] in (None, ""):
        return []
    raw_flags = metadata["license_flags"]
    if isinstance(raw_flags, str):
        flags = split_semicolon(raw_flags) if ";" in raw_flags else [part.strip() for part in raw_flags.split(",") if part.strip()]
    elif isinstance(raw_flags, (list, tuple)):
        flags = [str(flag).strip() for flag in raw_flags if str(flag).strip()]
    else:
        raise CatalogSiteError(f"{doc_path}: license_flags must be a list or delimited string")
    return flags


def merged_license_flags(license_text: str, metadata: dict[str, Any], *, doc_path: Path) -> list[str]:
    flags: list[str] = []
    for flag in [*license_flags(license_text), *frontmatter_license_flags(metadata, doc_path=doc_path)]:
        if flag not in flags:
            flags.append(flag)
    return flags


def require_iso_date(value: str, *, context: str) -> str:
    if not DATE_RE.fullmatch(value):
        raise CatalogSiteError(f"{context}: expected YYYY-MM-DD date, got {value!r}")
    try:
        dt.date.fromisoformat(value)
    except ValueError as error:
        raise CatalogSiteError(f"{context}: invalid date {value!r}") from error
    return value


def release_versions(
    *,
    doc_path: Path,
    canonical_path: str,
    slug: str,
    canonical_format: str,
    available_formats: list[str],
    last_updated: str,
) -> list[CatalogVersion]:
    metadata = read_doc_metadata(doc_path)
    files = metadata.get("files") or []
    if not isinstance(files, list):
        raise CatalogSiteError(f"{doc_path}: files must be a list")

    by_date: dict[str, set[str]] = {}
    for index, item in enumerate(files, start=1):
        if not isinstance(item, dict):
            raise CatalogSiteError(f"{doc_path}: files entry {index} must be a mapping")
        path = str(item.get("path") or "").strip()
        if not path.startswith("releases/"):
            continue
        match = RELEASE_PATH_RE.fullmatch(path)
        if not match:
            raise CatalogSiteError(f"{doc_path}: release path must be releases/YYYY-MM-DD/object, got {path!r}")
        raw_date = match.group("date")
        date = last_updated if raw_date == "YYYY-MM-DD" else raw_date
        require_iso_date(date, context=f"{doc_path}: release date")
        format_name = str(item.get("format") or "").strip()
        if format_name not in APPROVED_FORMATS:
            raise CatalogSiteError(f"{doc_path}: release path {path!r} has unsupported format {format_name!r}")
        by_date.setdefault(date, set()).add(format_name)

    versions: list[CatalogVersion] = []
    for date in sorted(by_date, reverse=True):
        release_formats = [format_name for format_name in available_formats if format_name in by_date[date]]
        if canonical_format not in release_formats:
            release_formats.insert(0, canonical_format)
        release_canonical_path = release_path_for_latest_path(canonical_path, date)
        release_pmtiles_path = None
        if "pmtiles" in release_formats:
            release_pmtiles_path = release_path_for_latest_path(path_for_format(canonical_path, slug, "pmtiles"), date)
        versions.append(
            CatalogVersion(
                date=date,
                canonical_path=release_canonical_path,
                public_url=gs_to_https(release_canonical_path),
                pmtiles_path=release_pmtiles_path,
                pmtiles_url=gs_to_https(release_pmtiles_path) if release_pmtiles_path else None,
                available_formats=release_formats,
            )
        )
    return versions


def read_description(path: Path) -> str:
    raw = path.read_text()
    match = FRONTMATTER_RE.match(raw)
    body = match.group(1) if match else raw
    for section in SECTION_RE.finditer(body):
        if section.group("title").strip().lower() != "what this is":
            continue
        paragraph: list[str] = []
        for line in section.group("body").splitlines():
            stripped = line.strip()
            if not stripped:
                if paragraph:
                    break
                continue
            if MARKDOWN_TABLE_RE.match(stripped):
                continue
            if stripped.startswith("<!--") or stripped.startswith("|---"):
                continue
            paragraph.append(stripped)
        description = strip_markdown(" ".join(paragraph))
        if description:
            return description
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "-", "<!--", "|")):
            return strip_markdown(stripped)
    return ""


def license_flags(license_text: str) -> list[str]:
    lowered = license_text.lower()
    flags: list[str] = []
    if "non-commercial" in lowered or "noncommercial" in lowered or "nc" in lowered:
        flags.append("non-commercial")
    if "no redistribution" in lowered or "permission" in lowered:
        flags.append("redistribution-limited")
    if "no explicit license" in lowered or REFERENTIAL_TERMS_RE.search(lowered):
        flags.append("confirm-license")
    if "public domain" in lowered or "public u.s. government work" in lowered:
        flags.append("open")
    return flags


def validate_row(
    *,
    row: dict[str, str],
    row_number: int,
    categories: dict[str, dict[str, str]],
    seen: set[str],
    docs_dir: Path,
) -> None:
    prefix = f"catalog row {row_number}"
    for field in REQUIRED_FIELDS:
        if not (row.get(field) or "").strip():
            raise CatalogSiteError(f"{prefix}: missing required field {field!r}")
    slug = row["asset_slug"].strip()
    if slug in seen:
        raise CatalogSiteError(f"{prefix}: duplicate asset_slug {slug!r}")
    if not SLUG_RE.fullmatch(slug):
        raise CatalogSiteError(f"{prefix}: asset_slug must be lowercase kebab-case")
    access_tier = row["access_tier"].strip()
    if access_tier not in ACCESS_TIERS:
        raise CatalogSiteError(f"{prefix}: access_tier must be one of: {', '.join(sorted(ACCESS_TIERS))}")
    category = row["category"].strip()
    subcategory = row["subcategory"].strip()
    if category not in categories:
        raise CatalogSiteError(f"{prefix}: unknown category {category!r}")
    if subcategory not in categories[category]:
        raise CatalogSiteError(f"{prefix}: unknown subcategory {category}/{subcategory}")
    canonical_format = row["canonical_format"].strip()
    if canonical_format not in APPROVED_FORMATS:
        raise CatalogSiteError(f"{prefix}: unsupported canonical_format {canonical_format!r}")
    formats = split_semicolon(row.get("available_formats", ""))
    if canonical_format not in formats:
        raise CatalogSiteError(f"{prefix}: available_formats must include canonical_format")
    unsupported = sorted(set(formats) - APPROVED_FORMATS)
    if unsupported:
        raise CatalogSiteError(f"{prefix}: unsupported available format(s): {', '.join(unsupported)}")
    bucket, object_name = split_gs_uri(row["canonical_path"].strip())
    expected_prefix = f"{category}/{subcategory}/{slug}/latest/"
    if not object_name.startswith(expected_prefix):
        raise CatalogSiteError(f"{prefix}: canonical_path must be under {expected_prefix}")
    if not bucket:
        raise CatalogSiteError(f"{prefix}: canonical_path is missing bucket")
    docs_path = docs_dir / f"{slug}.md"
    if not docs_path.exists():
        raise CatalogSiteError(f"{prefix}: unresolved docs link {docs_path}")


def asset_from_row(row: dict[str, str], docs_dir: Path) -> CatalogAsset:
    slug = row["asset_slug"].strip()
    canonical_path = row["canonical_path"].strip()
    canonical_format = row["canonical_format"].strip()
    formats = split_semicolon(row["available_formats"])
    access_tier = row["access_tier"].strip()
    metadata_paths = split_semicolon(row["metadata_paths"])
    pmtiles_path = path_for_format(canonical_path, slug, "pmtiles") if "pmtiles" in formats else None
    docs_path = f"docs/assets/{slug}.md"
    doc_path = docs_dir / f"{slug}.md"
    doc_metadata = read_doc_metadata(doc_path)
    last_updated = row["last_updated"].strip()
    versions = release_versions(
        doc_path=doc_path,
        canonical_path=canonical_path,
        slug=slug,
        canonical_format=canonical_format,
        available_formats=formats,
        last_updated=last_updated,
    )
    return CatalogAsset(
        slug=slug,
        title=row["title"].strip(),
        category=row["category"].strip(),
        subcategory=row["subcategory"].strip(),
        status=row["status"].strip(),
        access_tier=access_tier,
        owner=row["owner"].strip(),
        update_cadence=row["update_cadence"].strip(),
        canonical_path=canonical_path,
        canonical_format=canonical_format,
        available_formats=formats,
        metadata_paths=metadata_paths,
        has_pmtiles="pmtiles" in formats,
        has_geojson="geojson" in formats,
        has_csv="csv" in formats,
        last_updated=last_updated,
        source=row["source"].strip(),
        license=row["license"].strip(),
        notes=row.get("notes", "").strip(),
        bounds=optional_bounds(doc_metadata, doc_path=doc_path),
        geometry_type=optional_text(doc_metadata, "geometry_type", doc_path=doc_path),
        row_count=optional_int(doc_metadata, "row_count", doc_path=doc_path),
        source_url=optional_text(doc_metadata, "source_url", doc_path=doc_path),
        public_url=gs_to_https(canonical_path),
        pmtiles_path=pmtiles_path,
        pmtiles_url=pmtiles_cdn_url(slug, access_tier) if pmtiles_path else None,
        docs_path=docs_path,
        docs_url=docs_path,
        description=read_description(doc_path),
        license_flags=merged_license_flags(row["license"].strip(), doc_metadata, doc_path=doc_path),
        versions=versions,
        sort_key=f"{last_updated}|{slug}",
    )


def build_catalog_payload(
    *,
    catalog_path: Path,
    categories_path: Path,
    docs_dir: Path,
    bucket: str,
    site_prefix: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    categories = load_categories(categories_path)
    rows = load_catalog_rows(catalog_path)
    seen: set[str] = set()
    assets: list[CatalogAsset] = []
    for index, row in enumerate(rows, start=2):
        validate_row(row=row, row_number=index, categories=categories, seen=seen, docs_dir=docs_dir)
        seen.add(row["asset_slug"].strip())
        assets.append(asset_from_row(row, docs_dir))
    assets.sort(key=lambda asset: (asset.last_updated, asset.slug), reverse=True)
    return {
        "schema_version": 1,
        "generated_at": generated_at or dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "bucket": bucket,
        "site_prefix": site_prefix.strip("/"),
        "source_catalog": str(catalog_path),
        "categories": categories,
        "formats": sorted(APPROVED_FORMATS),
        "assets": [asdict(asset) for asset in assets],
    }


def copy_static_files(source_dir: Path, out_dir: Path) -> list[Path]:
    copied: list[Path] = []
    for name in ("index.html", "styles.css", "app.js", "map-preview.js"):
        src = source_dir / name
        if not src.exists():
            raise CatalogSiteError(f"missing static source file: {src}")
        dst = out_dir / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(dst)
    return copied


def copy_docs(docs_dir: Path, out_dir: Path) -> list[Path]:
    copied: list[Path] = []
    target_dir = out_dir / "docs/assets"
    target_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted(docs_dir.glob("*.md")):
        dst = target_dir / src.name
        shutil.copy2(src, dst)
        copied.append(dst)
    return copied


def build_site(
    *,
    catalog_path: Path,
    categories_path: Path,
    docs_dir: Path,
    static_dir: Path,
    out_dir: Path,
    bucket: str,
    site_prefix: str,
    generated_at: str | None = None,
) -> list[Path]:
    payload = build_catalog_payload(
        catalog_path=catalog_path,
        categories_path=categories_path,
        docs_dir=docs_dir,
        bucket=bucket,
        site_prefix=site_prefix,
        generated_at=generated_at,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    written = copy_static_files(static_dir, out_dir)
    written.extend(copy_docs(docs_dir, out_dir))
    catalog_json = out_dir / "catalog.json"
    catalog_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    written.append(catalog_json)
    return written


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the static shared-datasets catalog site.")
    parser.add_argument("--catalog", type=Path, default=Path("catalog/shared-datasets-catalog.csv"))
    parser.add_argument("--categories", type=Path, default=Path("catalog/categories.yaml"))
    parser.add_argument("--docs-dir", type=Path, default=Path("docs/assets"))
    parser.add_argument("--static-dir", type=Path, default=Path("web/catalog"))
    parser.add_argument("--out", type=Path, required=True, help="Output directory for the static web bundle.")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--site-prefix", default=DEFAULT_SITE_PREFIX)
    parser.add_argument("--generated-at", help="Override generated_at timestamp for deterministic tests.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    written = build_site(
        catalog_path=args.catalog,
        categories_path=args.categories,
        docs_dir=args.docs_dir,
        static_dir=args.static_dir,
        out_dir=args.out,
        bucket=args.bucket,
        site_prefix=args.site_prefix,
        generated_at=args.generated_at,
    )
    print(json.dumps({"output": str(args.out), "files": [str(path) for path in written]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
