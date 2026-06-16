#!/usr/bin/env python3
"""Read-only compliance audit for the shared datasets bucket.

The script lists GCS objects, validates their paths against AGENTS.md conventions,
checks adjacent README content, and compares discovered asset roots with the repo
catalog. It reports findings only; it never mutates local or remote state.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml
from google.cloud import storage

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.raster_asset import (
    COG_EXTENSIONS,
    PREVIEW_EXTENSIONS,
    RASTER_CANONICAL_FORMATS,
    RASTER_SIDECAR_SUFFIXES,
    RASTER_SOURCE_EXTENSIONS,
    validate_zarr_manifest_payload,
)
from scripts import release_feature_model

APPROVED_CANONICAL_FORMATS = {"fgb", "pmtiles", "geojson", "ndgeojson", "csv", "cog", "zarr"}
APPROVED_DATA_EXTENSIONS = {".fgb", ".pmtiles", ".geojson", ".ndgeojson", ".csv", ".tif", ".tiff"}
FIELD_SAFE_LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:_[a-z0-9]{2,8})*$")
APPROVED_RELEASE_METADATA_SUFFIXES = (".schema.json", ".manifest.json")
RELEASE_METADATA_SIDECAR_RE = re.compile(r"\.metadata(?:\.(?P<locale>[a-z0-9_]+))?\.ndjson\.gz$")
CATALOG_REQUIRED_COLUMNS = (
    "asset_slug",
    "title",
    "category",
    "subcategory",
    "status",
    "owner",
    "update_cadence",
    "canonical_path",
    "canonical_format",
    "available_formats",
    "metadata_paths",
    "has_pmtiles",
    "has_geojson",
    "has_csv",
    "source",
    "license",
    "citation",
    "notes",
)
RESERVED_TOP_LEVEL = {"_catalog", "_templates", "_scratch", "_deprecated"}
SYSTEM_TOP_LEVEL = {"000-system"}
ROOT_ALLOWED_DOCS = {"README.md"}
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RUN_RECORD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")
UPLOADER_METADATA_KEYS = ("uploaded_by", "uploader", "created_by", "creator", "owner", "author")
RELEASE_INDEX_PREFIX = "_catalog/releases"
RELEASE_INDEX_MODES = {"report": "INFO", "warn": "WARN", "enforce": "ERROR"}
SCHEDULE_FRESHNESS_DAYS = {"daily": 3, "monthly": 45}
GENERIC_PROPERTIES_ROW_RE = re.compile(r"(?mi)^\|\s*Source fields\s*\|\s*varies\s*\|")
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
LOAD_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
INDEX_LOAD_STATUS = "Firestore metadata serving is inactive"
INDEX_STATUS_MODE = "inactive_firestore_serving"
FEATURE_METADATA_LATEST_FILES = {
    "metadata": ".metadata.ndjson.gz",
    "schema": ".schema.json",
    "manifest": ".manifest.json",
}

README_REQUIRED_SNIPPETS = {
    "status": "**Status:**",
    "owner": "**Owner:**",
    "update_cadence": "**Update cadence:**",
    "canonical_file": "**Canonical file:**",
    "source": "**Source:**",
    "license_terms": "**License / terms:**",
    "what_this_is": "## What this is",
    "files": "## Files",
    "schema_notes": "## Schema notes",
    "properties_columns": "## Properties / columns",
    "update_notes": "## Update notes",
}
RASTER_README_REQUIRED_SNIPPETS = {
    "raster_metadata": "## Raster metadata",
}
@dataclass
class BlobInfo:
    name: str
    size: int
    generation: str
    updated: str
    content_type: Optional[str]
    metadata: Dict[str, str]


@dataclass
class Finding:
    severity: str
    path: str
    check: str
    message: str
    uploader_hint: str = "unknown"
    suggested_next_step: str = ""
    impact: str = ""
    repair_category: str = ""
    codex_prompt: str = ""


@dataclass
class AuditRunResult:
    findings: List[Finding]
    object_count: int
    exit_code: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only shared datasets compliance audit.")
    parser.add_argument(
        "--bucket",
        default=os.environ.get("SHARED_DATASETS_BUCKET", "skytruth-shared-datasets-1"),
        help="GCS bucket name, without gs://.",
    )
    parser.add_argument("--prefix", default="", help="Optional GCS object prefix to audit.")
    parser.add_argument(
        "--catalog",
        default="catalog/shared-datasets-catalog.csv",
        help="Local catalog CSV path.",
    )
    parser.add_argument(
        "--categories",
        default="catalog/categories.yaml",
        help="Local categories YAML path.",
    )
    parser.add_argument(
        "--asset-docs-dir",
        default="docs/assets",
        help="Directory containing local docs/assets/*.md metadata sources.",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format.",
    )
    parser.add_argument(
        "--skip-readme-content",
        action="store_true",
        help="Do not download README.md text for checks.",
    )
    parser.add_argument(
        "--skip-remote-catalog-check",
        action="store_true",
        help="Do not compare bucket _catalog CSV with local catalog.",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Validate local catalog/taxonomy shape only; do not contact GCS.",
    )
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="Exit 1 if blocking findings exist.",
    )
    parser.add_argument(
        "--health-profile",
        choices=("advisory", "production"),
        default="advisory",
        help=(
            "Use advisory for reporting-only audits, or production for hard live "
            "contract health checks with Codex repair prompts."
        ),
    )
    parser.add_argument(
        "--release-integrity-mode",
        choices=("report", "warn", "enforce"),
        default="warn",
        help="Validate JSON release indexes as info, non-blocking warnings, or blocking errors.",
    )
    return parser.parse_args()


def load_categories(path: Path) -> Dict[str, set[str]]:
    payload = yaml.safe_load(path.read_text()) or {}
    categories = payload.get("categories", {})
    return {name: set((data.get("subcategories") or {}).keys()) for name, data in categories.items()}


def load_catalog(path: Path) -> Tuple[List[Dict[str, str]], Dict[str, Dict[str, str]]]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    return rows, {row.get("asset_slug", ""): row for row in rows if row.get("asset_slug")}


def list_blobs(bucket_name: str, prefix: str) -> List[BlobInfo]:
    client = storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT") or None)
    blobs = []
    for blob in client.list_blobs(bucket_name, prefix=prefix):
        blobs.append(
            BlobInfo(
                name=blob.name,
                size=int(blob.size or 0),
                generation=str(blob.generation or ""),
                updated=blob.updated.isoformat() if blob.updated else "",
                content_type=blob.content_type,
                metadata={str(k): str(v) for k, v in (blob.metadata or {}).items()},
            )
        )
    return blobs


def download_readme_text(bucket_name: str, blob_name: str, generation: str) -> str:
    client = storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT") or None)
    blob = client.bucket(bucket_name).blob(blob_name, generation=int(generation) if generation else None)
    return blob.download_as_text()


def download_object_text(bucket_name: str, blob_name: str, generation: str) -> str:
    client = storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT") or None)
    blob = client.bucket(bucket_name).blob(blob_name, generation=int(generation) if generation else None)
    return blob.download_as_text()


def as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def same_generation(expected: Any, actual: Any) -> bool:
    expected_int = as_int(expected)
    actual_int = as_int(actual)
    return expected_int is not None and actual_int is not None and expected_int == actual_int


def release_file_for_format(files: Any, format_name: str) -> dict[str, Any] | None:
    if not isinstance(files, list):
        return None
    for file_entry in files:
        if not isinstance(file_entry, dict):
            continue
        candidate = str(file_entry.get("format") or file_entry.get("role") or "").strip()
        if candidate == format_name:
            return file_entry
    return None


def canonical_metadata_release_file(files: Any) -> dict[str, Any] | None:
    if not isinstance(files, list):
        return None
    for file_entry in files:
        if not isinstance(file_entry, dict):
            continue
        candidate = str(file_entry.get("format") or file_entry.get("role") or "").strip()
        role = str(file_entry.get("role") or "").strip()
        path = release_file_path(file_entry)
        sidecar_match = RELEASE_METADATA_SIDECAR_RE.search(path)
        is_localized_sidecar = bool(sidecar_match and sidecar_match.group("locale"))
        if candidate == "metadata" and role != "metadata.es" and not is_localized_sidecar:
            return file_entry
    return release_file_for_format(files, "metadata")


def release_file_path(file_entry: Any) -> str:
    if not isinstance(file_entry, dict):
        return ""
    path = str(file_entry.get("path") or "").strip()
    return path if path.startswith("gs://") else ""


def load_asset_doc_feature_metadata(docs_dir: Path) -> Dict[str, dict[str, Any]]:
    metadata_by_slug: Dict[str, dict[str, Any]] = {}
    if not docs_dir.exists():
        return metadata_by_slug
    for path in sorted(docs_dir.glob("*.md")):
        match = FRONTMATTER_RE.match(path.read_text())
        if not match:
            continue
        payload = yaml.safe_load(match.group(1)) or {}
        if not isinstance(payload, dict):
            continue
        slug = str(payload.get("asset_slug") or "").strip()
        feature_metadata = payload.get("feature_metadata")
        if slug and isinstance(feature_metadata, dict):
            metadata_by_slug[slug] = feature_metadata
    return metadata_by_slug


def release_index_repair_next_step(asset_slug: str) -> str:
    return (
        "Generate a dry-run release-index repair candidate, review exact paths and generations, "
        "then route canonical _catalog/ changes through a reviewed PR and the approved dataset "
        "mutation workflow."
    )


def release_index_exists_prompt(*, asset_slug: str, exact_gcs_uri: str) -> str:
    return (
        "In shared-datasets-1, load AGENTS.md plus the gcp-shared-datasets and "
        "shared-datasets-compliance-audit skills. The Bucket hygiene audit failed because this "
        f"active versioned asset has no JSON release index: {exact_gcs_uri}. Do not mutate canonical "
        f"GCS from a local terminal. Inspect the catalog row, docs/assets/{asset_slug}.md, existing "
        f"latest/release objects, and object generations. Build a reviewed repair plan that creates "
        f"_catalog/releases/{asset_slug}.json through the approved dataset mutation workflow, including "
        "exact source object URIs, generations, checksums, row counts where known, and validation commands. "
        "Rerun the production bucket hygiene audit in enforce mode after the PR-mediated repair."
    )


def successful_run_missing_prompt(*, asset_slug: str, exact_run_record_uri: str) -> str:
    return (
        "In shared-datasets-1, load AGENTS.md plus the gcp-shared-datasets and "
        "shared-datasets-compliance-audit skills. The Bucket hygiene audit failed because this "
        f"successful run record is not represented in _catalog/releases/{asset_slug}.json: "
        f"{exact_run_record_uri}. Do not mutate canonical GCS locally. Inspect the run record, release "
        "objects for the same date, and the current release index. Determine whether the run produced "
        "a valid release that must be indexed or whether the run record status is wrong. Prepare a "
        "reviewed repair PR with exact generation-aware _catalog/releases changes or the correct safe "
        "alternative, then rerun the production audit."
    )


def canonical_file_missing_prompt(*, asset_slug: str, release_date: str) -> str:
    return (
        "In shared-datasets-1, load AGENTS.md plus the gcp-shared-datasets and "
        "shared-datasets-compliance-audit skills. The Bucket hygiene audit failed because release "
        f"{release_date} for {asset_slug} is indexed without the catalog canonical format file. Do not "
        f"mutate canonical GCS locally. Inspect _catalog/releases/{asset_slug}.json, the asset doc, latest/ "
        f"objects, and releases/{release_date}/ objects. Choose the safe repair: either index/promote the "
        "existing canonical FGB for that release if it exists and validates, or retarget latest_release to "
        "the newest complete release. Prepare the repair through a reviewed PR / approved mutation plan "
        "with exact paths and generations, then rerun the production audit."
    )


def feature_metadata_prompt(*, asset_slug: str) -> str:
    return (
        "In shared-datasets-1, load AGENTS.md plus the gcp-shared-datasets, shared-datasets-compliance-audit, "
        f"and publish-shared-dataset skills. The Bucket hygiene audit failed because docs/assets/{asset_slug}.md "
        "advertises feature_metadata, but the live bucket does not have a complete usable metadata contract. "
        "Do not mutate canonical GCS locally. Inspect the latest sidecar/schema/manifest paths, "
        f"_catalog/releases/{asset_slug}.json, and release manifest. Publish or repair a "
        "complete metadata-backed release through the approved workflow: FGB, lightweight PMTiles, "
        ".metadata.ndjson.gz, .schema.json, .manifest.json, release index entries with generations, and a "
        "manifest/release-index policy marking Firestore metadata serving inactive. Rerun the production bucket "
        "hygiene audit after the approved repair."
    )


def validate_remote_catalog(bucket_name: str, local_catalog_path: Path) -> List[Finding]:
    client = storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT") or None)
    blob = client.bucket(bucket_name).blob("_catalog/shared-datasets-catalog.csv")
    if not blob.exists():
        return [
            Finding(
                "ERROR",
                f"gs://{bucket_name}/_catalog/shared-datasets-catalog.csv",
                "remote-catalog-exists",
                "Bucket-side catalog object is missing.",
                "unknown",
                "Offer to upload the repo catalog with no-clobber behavior after approval.",
            )
        ]
    remote_text = blob.download_as_text()
    local_text = local_catalog_path.read_text()
    if remote_text != local_text:
        return [
            Finding(
                "ERROR",
                f"gs://{bucket_name}/_catalog/shared-datasets-catalog.csv",
                "remote-catalog-current",
                "Bucket-side catalog differs from the repo catalog.",
                uploader_hint(
                    BlobInfo(
                        name=blob.name,
                        size=int(blob.size or 0),
                        generation=str(blob.generation or ""),
                        updated=blob.updated.isoformat() if blob.updated else "",
                        content_type=blob.content_type,
                        metadata={str(k): str(v) for k, v in (blob.metadata or {}).items()},
                    )
                ),
                "Offer to replace the remote catalog using the current generation precondition after approval.",
            )
        ]
    return []


def uploader_hint(blob: Optional[BlobInfo]) -> str:
    if not blob:
        return "unknown"
    for key in UPLOADER_METADATA_KEYS:
        value = blob.metadata.get(key)
        if value:
            return f"{key}={value}"
    return "unknown"


def asset_root_for(name: str, categories: Dict[str, set[str]]) -> Tuple[Optional[str], Optional[str]]:
    parts = [part for part in name.split("/") if part]
    if not parts:
        return None, "empty object name"
    top = parts[0]
    if len(parts) == 1 and top in ROOT_ALLOWED_DOCS:
        return None, None
    if top in RESERVED_TOP_LEVEL or top in SYSTEM_TOP_LEVEL:
        return None, None
    if top not in categories:
        return None, f"unknown top-level prefix {top!r}"
    if len(parts) < 3:
        return None, "object under category is not inside {category}/{subcategory}/{asset-slug}/"
    subcategory = parts[1]
    if subcategory not in categories[top]:
        return "/".join(parts[:3]), f"unknown subcategory {top}/{subcategory}"
    return "/".join(parts[:3]), None


def is_taxonomy_placeholder_or_doc(name: str, categories: Dict[str, set[str]]) -> bool:
    parts = [part for part in name.split("/") if part]
    if not parts:
        return True
    if name.endswith("/") or parts[-1] == ".keep":
        return True
    top = parts[0]
    if len(parts) == 1 and top in ROOT_ALLOWED_DOCS:
        return True
    if top in categories and len(parts) < 3 and parts[-1] == "README.md":
        return True
    if top in RESERVED_TOP_LEVEL or top in SYSTEM_TOP_LEVEL:
        return True
    return False


def is_zarr_internal_path(parts: Sequence[str]) -> bool:
    return any(part.endswith(".zarr") for part in parts)


def has_raster_sidecar_name(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(RASTER_SIDECAR_SUFFIXES)


def has_release_metadata_sidecar_name(name: str) -> bool:
    match = RELEASE_METADATA_SIDECAR_RE.search(Path(name).name)
    if not match:
        return False
    locale = match.group("locale")
    return locale is None or bool(FIELD_SAFE_LOCALE_RE.fullmatch(locale))


def row_format(row: Optional[Dict[str, str]]) -> str:
    return (row or {}).get("canonical_format", "").strip().lower()


def object_is_raster_like(blob_name: str, row: Optional[Dict[str, str]]) -> bool:
    lowered = blob_name.lower()
    return (
        row_format(row) in RASTER_CANONICAL_FORMATS
        or Path(blob_name).suffix.lower() in COG_EXTENSIONS
        or ".zarr/" in lowered
        or lowered.endswith(".zarr")
    )


def validate_data_extension(
    *,
    root: str,
    blob: BlobInfo,
    row: Optional[Dict[str, str]],
    context: str,
) -> List[Finding]:
    findings: List[Finding] = []
    hint = uploader_hint(blob)
    ext = Path(blob.name).suffix.lower()
    fmt = row_format(row)

    if has_raster_sidecar_name(blob.name):
        findings.append(
            Finding(
                "ERROR",
                blob.name,
                "raster-sidecar",
                "Raster sidecar files are not allowed in canonical latest/release outputs; "
                "raster assets must be self-contained.",
                hint,
                "Publish a self-contained COG or documented Zarr release instead of sidecar-dependent raster files.",
            )
        )
        return findings

    if blob.name.endswith(APPROVED_RELEASE_METADATA_SUFFIXES) or has_release_metadata_sidecar_name(blob.name):
        return findings

    if ext in PREVIEW_EXTENSIONS:
        findings.append(
            Finding(
                "ERROR",
                blob.name,
                "preview-location",
                "PNG/JPEG/WebP files are only allowed under `previews/` or inside PMTiles.",
                hint,
                "Move or republish previews only after confirming owner intent.",
            )
        )
        return findings

    if ext not in APPROVED_DATA_EXTENSIONS:
        findings.append(
            Finding(
                "ERROR",
                blob.name,
                "approved-format",
                f"{context} data file extension {ext or '<none>'} is not approved by default.",
                hint,
                "Ask the uploader/owner whether the file should be converted or documented as an exception.",
            )
        )
        return findings

    if ext in COG_EXTENSIONS and fmt != "cog":
        findings.append(
            Finding(
                "ERROR",
                blob.name,
                "raw-raster-canonical",
                "GeoTIFF files under latest/releases must be cataloged as canonical_format "
                "`cog` and validated as Cloud Optimized GeoTIFFs.",
                hint,
                "Convert raw GeoTIFFs to COG or document why this raster is only a source/archive exception.",
            )
        )

    return findings


def validate_object_layout(root: str, blob: BlobInfo, row: Optional[Dict[str, str]] = None) -> List[Finding]:
    findings: List[Finding] = []
    rel = blob.name[len(root) :].lstrip("/")
    parts = rel.split("/") if rel else []
    hint = uploader_hint(blob)
    fmt = row_format(row)

    if rel == "README.md":
        return findings
    if len(parts) >= 2 and parts[0] == "previews":
        ext = Path(parts[-1]).suffix.lower()
        if ext not in PREVIEW_EXTENSIONS:
            findings.append(
                Finding(
                    "ERROR",
                    blob.name,
                    "preview-format",
                    f"`previews/` file extension {ext or '<none>'} is not an approved preview image format.",
                    hint,
                    "Use PNG, JPEG, or WebP for previews, or publish analytical data in latest/releases.",
                )
            )
        return findings
    if len(parts) >= 2 and parts[0] in {"source", "sources", "archive"}:
        ext = Path(parts[-1]).suffix.lower()
        if ext not in RASTER_SOURCE_EXTENSIONS and ext not in APPROVED_DATA_EXTENSIONS:
            findings.append(
                Finding(
                    "WARN",
                    blob.name,
                    "source-archive-format",
                    f"`{parts[0]}/` file extension {ext or '<none>'} is not a "
                    "documented source/archive exception format.",
                    hint,
                    "Confirm this source/archive object is intentional and documented in the README.",
                )
            )
        return findings
    if len(parts) >= 2 and parts[0] == "latest":
        if parts == ["latest", "manifest.json"]:
            return findings
        if is_zarr_internal_path(parts):
            findings.append(
                Finding(
                    "ERROR",
                    blob.name,
                    "zarr-latest-layout",
                    "Zarr objects must not be mirrored under `latest/`; update only `latest/manifest.json`.",
                    hint,
                    "Republish Zarr data under immutable releases/YYYY-MM-DD/ and point latest/manifest.json at it.",
                )
            )
            return findings
        if len(parts) != 2:
            findings.append(
                Finding(
                    "ERROR" if fmt == "zarr" else "WARN",
                    blob.name,
                    "latest-layout",
                    "`latest/` should usually contain files directly, not nested paths.",
                    hint,
                    "Confirm this nesting is intentional before changing it.",
                )
            )
        findings.extend(validate_data_extension(root=root, blob=blob, row=row, context="Latest"))
        return findings
    if len(parts) >= 3 and parts[0] == "releases":
        if not DATE_RE.match(parts[1]):
            findings.append(
                Finding(
                    "ERROR",
                    blob.name,
                    "release-date",
                    "`releases/` child should be YYYY-MM-DD.",
                    hint,
                    "Discuss with the uploader before moving release objects.",
                )
            )
        if is_zarr_internal_path(parts):
            if fmt != "zarr":
                findings.append(
                    Finding(
                        "ERROR",
                        blob.name,
                        "zarr-catalog-format",
                        "Objects inside a `.zarr/` release require catalog canonical_format `zarr`.",
                        hint,
                        "Update the catalog only after confirming the asset is intended to be canonical Zarr.",
                    )
                )
            return findings
        findings.extend(validate_data_extension(root=root, blob=blob, row=row, context="Release"))
        return findings
    if len(parts) == 2 and parts[0] == "runs":
        if not RUN_RECORD_RE.match(parts[1]):
            findings.append(
                Finding(
                    "ERROR",
                    blob.name,
                    "run-record-name",
                    "`runs/` records should be named YYYY-MM-DD.json.",
                    hint,
                    "Confirm run identity before renaming.",
                )
            )
        return findings
    if len(parts) == 3 and parts[0] == "index-loads":
        if not DATE_RE.match(parts[1]):
            findings.append(
                Finding(
                    "ERROR",
                    blob.name,
                    "index-load-date",
                    "`index-loads/` child should be YYYY-MM-DD.",
                    hint,
                    "Confirm load identity before renaming.",
                )
            )
        if not parts[2].endswith(".json") or not LOAD_ID_RE.fullmatch(parts[2].removesuffix(".json")):
            findings.append(
                Finding(
                    "ERROR",
                    blob.name,
                    "index-load-name",
                    "`index-loads/` records should be named {load-id}.json with a safe load ID.",
                    hint,
                    "Confirm load identity before renaming.",
                )
            )
        return findings

    findings.append(
        Finding(
            "ERROR",
            blob.name,
            "asset-layout",
            "Object is outside the standard README.md/latest/releases/runs asset layout.",
            hint,
            "Do not move automatically; identify uploader/owner and confirm intended asset placement first.",
        )
    )
    return findings


def validate_readme(
    root: str,
    readme_blob: Optional[BlobInfo],
    text: Optional[str],
    *,
    requires_raster_metadata: bool = False,
) -> List[Finding]:
    if not readme_blob:
        return [
            Finding(
                "ERROR",
                root,
                "readme-exists",
                "Asset root has no README.md.",
                "unknown",
                "Offer to add a README after confirming source, owner, license, and canonical file.",
            )
        ]
    if text is None:
        return [
            Finding(
                "INFO",
                readme_blob.name,
                "readme-content-skipped",
                "README content checks were skipped.",
                uploader_hint(readme_blob),
                "Run without --skip-readme-content for full README validation.",
            )
        ]

    findings: List[Finding] = []
    for key, snippet in README_REQUIRED_SNIPPETS.items():
        if snippet not in text:
            findings.append(
                Finding(
                    "WARN",
                    readme_blob.name,
                    f"readme-{key}",
                    f"README is missing required marker `{snippet}`.",
                    uploader_hint(readme_blob),
                    "Offer to update README content after confirming source details.",
                )
            )
    if requires_raster_metadata:
        for key, snippet in RASTER_README_REQUIRED_SNIPPETS.items():
            if snippet not in text:
                findings.append(
                    Finding(
                        "WARN",
                        readme_blob.name,
                        f"readme-{key}",
                        f"Raster asset README is missing required marker `{snippet}`.",
                        uploader_hint(readme_blob),
                        "Add a raster metadata table covering CRS, resolution, dimensions, "
                        "bands, dtype, nodata, units, and sampling.",
                    )
                )
    if "## Properties / columns" in text and "| Name | Type | Description |" not in text:
        findings.append(
            Finding(
                "WARN",
                readme_blob.name,
                "readme-properties-table",
                "README has a properties/columns section but not the standard table header.",
                uploader_hint(readme_blob),
                "Offer to normalize the properties table.",
            )
        )
    if GENERIC_PROPERTIES_ROW_RE.search(text):
        findings.append(
            Finding(
                "WARN",
                readme_blob.name,
                "readme-properties-placeholder",
                "README uses a generic `Source fields | varies` row instead of listing field names and types.",
                uploader_hint(readme_blob),
                "List field names and types; use source-confirmation notes for definitions that are not known.",
            )
        )
    if "## Raster metadata" in text and "| Field | Value |" not in text:
        findings.append(
            Finding(
                "WARN",
                readme_blob.name,
                "readme-raster-metadata-table",
                "README has a raster metadata section but not the standard table header.",
                uploader_hint(readme_blob),
                "Offer to normalize the raster metadata table.",
            )
        )
    return findings


def validate_zarr_latest_manifest(
    *,
    bucket: str,
    root: str,
    row: Optional[Dict[str, str]],
    root_blobs: Sequence[BlobInfo],
    object_names: set[str],
    skip_text_content: bool,
) -> List[Finding]:
    if row_format(row) != "zarr":
        return []

    findings: List[Finding] = []
    slug = root.split("/")[2]
    manifest_name = f"{root}/latest/manifest.json"
    manifest_blob = next((blob for blob in root_blobs if blob.name == manifest_name), None)
    hint = uploader_hint(manifest_blob or (root_blobs[0] if root_blobs else None))
    latest_blobs = [blob for blob in root_blobs if blob.name.startswith(f"{root}/latest/")]

    if not manifest_blob:
        findings.append(
            Finding(
                "ERROR",
                root,
                "zarr-latest-manifest",
                "Zarr assets must expose `latest/manifest.json` as the only mutable latest pointer.",
                hint,
                "Add a manifest that points at an immutable releases/YYYY-MM-DD/{asset-slug}.zarr/ prefix.",
            )
        )
        return findings

    extra_latest = [blob.name for blob in latest_blobs if blob.name != manifest_name]
    if extra_latest:
        findings.append(
            Finding(
                "ERROR",
                root,
                "zarr-latest-only-manifest",
                "`latest/` for Zarr assets must contain only manifest.json.",
                hint,
                "Move Zarr data objects to immutable releases/YYYY-MM-DD/ prefixes after owner confirmation.",
            )
        )

    if skip_text_content:
        findings.append(
            Finding(
                "INFO",
                manifest_name,
                "zarr-manifest-content-skipped",
                "Zarr latest manifest content checks were skipped.",
                hint,
                "Run without --skip-readme-content for full Zarr manifest validation.",
            )
        )
        return findings

    try:
        text = download_object_text(bucket, manifest_blob.name, manifest_blob.generation)
        payload = json.loads(text)
    except (json.JSONDecodeError, OSError) as exc:
        findings.append(
            Finding(
                "ERROR",
                manifest_name,
                "zarr-manifest-json",
                f"Zarr latest manifest is not valid JSON: {exc}.",
                hint,
                "Replace the manifest using the current generation precondition after "
                "confirming the intended release path.",
            )
        )
        return findings

    if not isinstance(payload, dict):
        findings.append(
            Finding(
                "ERROR",
                manifest_name,
                "zarr-manifest-json",
                "Zarr latest manifest must be a JSON object.",
                hint,
                "Replace the manifest with the standard pointer object.",
            )
        )
        return findings

    for message in validate_zarr_manifest_payload(payload, bucket=bucket, asset_root=root, asset_slug=slug):
        findings.append(
            Finding(
                "ERROR",
                manifest_name,
                "zarr-manifest-pointer",
                message,
                hint,
                "Correct the manifest pointer after confirming the intended immutable release.",
            )
        )

    release_path = str(payload.get("release_path") or payload.get("zarr_path") or "")
    expected_prefix = f"gs://{bucket}/"
    if release_path.startswith(expected_prefix):
        release_name_prefix = release_path[len(expected_prefix) :].rstrip("/") + "/"
        if not any(name.startswith(release_name_prefix) for name in object_names):
            findings.append(
                Finding(
                    "ERROR",
                    manifest_name,
                    "zarr-manifest-target-exists",
                    f"Manifest release_path has no matching discovered objects: {release_path}.",
                    hint,
                    "Confirm whether the release prefix exists or the manifest points at the wrong path.",
                )
            )
    return findings


def validate_catalog(
    bucket: str,
    roots: Dict[str, List[BlobInfo]],
    catalog_rows: Sequence[Dict[str, str]],
    catalog_by_slug: Dict[str, Dict[str, str]],
    object_names: set[str],
    prefix: str,
) -> List[Finding]:
    findings: List[Finding] = []
    discovered_slugs = {root.split("/")[2]: root for root in roots}

    for root, blobs in roots.items():
        category, subcategory, slug = root.split("/")
        row = catalog_by_slug.get(slug)
        hint = uploader_hint(max(blobs, key=lambda blob: blob.updated or ""))
        if not row:
            findings.append(
                Finding(
                    "ERROR",
                    root,
                    "catalog-row",
                    "Asset root is missing from catalog/shared-datasets-catalog.csv.",
                    hint,
                    "Offer to add a catalog row after confirming owner/source/license.",
                )
            )
            continue
        if row.get("category") != category:
            findings.append(
                Finding(
                    "ERROR",
                    root,
                    "catalog-category",
                    f"Catalog category is {row.get('category')!r}, expected {category!r}.",
                    hint,
                )
            )
        if row.get("subcategory") != subcategory:
            findings.append(
                Finding(
                    "ERROR",
                    root,
                    "catalog-subcategory",
                    f"Catalog subcategory is {row.get('subcategory')!r}, expected {subcategory!r}.",
                    hint,
                )
            )
        canonical_path = row.get("canonical_path", "")
        expected_prefix = f"gs://{bucket}/"
        if not canonical_path.startswith(expected_prefix):
            findings.append(
                Finding(
                    "ERROR",
                    root,
                    "catalog-canonical-path",
                    f"Canonical path does not start with {expected_prefix}.",
                    hint,
                    "Offer to correct catalog path if the remote object is confirmed.",
                )
            )
        else:
            canonical_name = canonical_path[len(expected_prefix) :]
            if canonical_name not in object_names:
                findings.append(
                    Finding(
                        "ERROR",
                        root,
                        "catalog-canonical-exists",
                        f"Catalog canonical_path object does not exist: {canonical_path}.",
                        hint,
                        "Do not rewrite blindly; confirm whether catalog or object path is wrong.",
                    )
                )
        fmt = row.get("canonical_format", "")
        if fmt and fmt.lower() not in APPROVED_CANONICAL_FORMATS:
            findings.append(
                Finding(
                    "ERROR",
                    root,
                    "catalog-format",
                    f"Catalog canonical_format {fmt!r} is not approved by default.",
                    hint,
                )
            )
        if fmt.lower() == "cog" and not canonical_path.lower().endswith((".tif", ".tiff")):
            findings.append(
                Finding(
                    "ERROR",
                    root,
                    "catalog-cog-path",
                    "Catalog canonical_format `cog` requires a .tif/.tiff canonical_path.",
                    hint,
                    "Point canonical_path at latest/{asset-slug}.tif after confirming the COG object exists.",
                )
            )
        if fmt.lower() == "zarr" and not canonical_path.endswith("/latest/manifest.json"):
            findings.append(
                Finding(
                    "ERROR",
                    root,
                    "catalog-zarr-path",
                    "Catalog canonical_format `zarr` requires canonical_path to point at latest/manifest.json.",
                    hint,
                    "Use latest/manifest.json as the mutable pointer to the immutable Zarr release.",
                )
            )
        available_formats = row.get("available_formats")
        if available_formats is None:
            findings.append(
                Finding(
                    "WARN",
                    root,
                    "catalog-available-formats-column",
                    "Catalog is missing the available_formats column.",
                    hint,
                    "Add available_formats to the catalog schema.",
                )
            )
        metadata_paths = row.get("metadata_paths")
        if metadata_paths is None:
            findings.append(
                Finding(
                    "WARN",
                    root,
                    "catalog-metadata-paths-column",
                    "Catalog is missing the metadata_paths column.",
                    hint,
                    "Add metadata_paths to the catalog schema.",
                )
            )

    for row in catalog_rows:
        slug = row.get("asset_slug", "")
        canonical_path = row.get("canonical_path", "")
        if (
            prefix
            and canonical_path.startswith(f"gs://{bucket}/")
            and not canonical_path[len(f"gs://{bucket}/") :].startswith(prefix)
        ):
            continue
        if slug and slug not in discovered_slugs:
            findings.append(
                Finding(
                    "ERROR",
                    slug,
                    "catalog-orphan-row",
                    "Catalog row has no matching discovered asset root.",
                    "unknown",
                    "Check whether the asset was moved, deleted, or filtered by --prefix.",
                )
            )
    return findings


def validate_local_catalog(
    *,
    bucket: str,
    categories: Dict[str, set[str]],
    catalog_rows: Sequence[Dict[str, str]],
) -> List[Finding]:
    findings: List[Finding] = []
    seen_slugs: set[str] = set()
    if catalog_rows:
        fieldnames = set(catalog_rows[0].keys())
        for column in CATALOG_REQUIRED_COLUMNS:
            if column not in fieldnames:
                findings.append(
                    Finding(
                        "ERROR",
                        "catalog/shared-datasets-catalog.csv",
                        "catalog-column",
                        f"Catalog is missing required column {column!r}.",
                        "unknown",
                        "Add the column before publishing catalog changes.",
                    )
                )

    for row in catalog_rows:
        slug = (row.get("asset_slug") or "").strip()
        category = (row.get("category") or "").strip()
        subcategory = (row.get("subcategory") or "").strip()
        canonical_path = (row.get("canonical_path") or "").strip()
        canonical_format = (row.get("canonical_format") or "").strip().lower()
        available_formats = {
            part.strip().lower()
            for part in (row.get("available_formats") or "").split(";")
            if part.strip()
        }
        path = slug or "catalog/shared-datasets-catalog.csv"

        if not slug:
            findings.append(Finding("ERROR", path, "catalog-slug", "Catalog row is missing asset_slug."))
            continue
        if slug in seen_slugs:
            findings.append(Finding("ERROR", slug, "catalog-duplicate-slug", "Catalog contains a duplicate asset_slug."))
        seen_slugs.add(slug)
        if not SLUG_RE.fullmatch(slug):
            findings.append(Finding("ERROR", slug, "catalog-slug", "asset_slug must be lowercase kebab-case."))
        if category not in categories:
            findings.append(Finding("ERROR", slug, "catalog-category", f"Unknown catalog category {category!r}."))
        elif subcategory not in categories[category]:
            findings.append(
                Finding(
                    "ERROR",
                    slug,
                    "catalog-subcategory",
                    f"Unknown catalog subcategory {category}/{subcategory}.",
                )
            )
        if canonical_format not in APPROVED_CANONICAL_FORMATS:
            findings.append(
                Finding(
                    "ERROR",
                    slug,
                    "catalog-format",
                    f"canonical_format {canonical_format!r} is not approved.",
                )
            )
        if canonical_format and canonical_format not in available_formats:
            findings.append(
                Finding(
                    "ERROR",
                    slug,
                    "catalog-available-formats",
                    "available_formats must include canonical_format.",
                )
            )
        expected_root = f"gs://{bucket}/{category}/{subcategory}/{slug}/"
        if not canonical_path.startswith(expected_root):
            findings.append(
                Finding(
                    "ERROR",
                    slug,
                    "catalog-canonical-path",
                    f"canonical_path must start with {expected_root}.",
                )
            )
        if canonical_format == "zarr":
            if not canonical_path.endswith("/latest/manifest.json"):
                findings.append(
                    Finding(
                        "ERROR",
                        slug,
                        "catalog-zarr-path",
                        "Zarr canonical_path must point at latest/manifest.json.",
                    )
                )
        elif "/latest/" not in canonical_path:
            findings.append(
                Finding(
                    "ERROR",
                    slug,
                    "catalog-latest-path",
                    "canonical_path should point at a latest/ object.",
                )
            )
        if canonical_format == "cog" and not canonical_path.lower().endswith((".tif", ".tiff")):
            findings.append(
                Finding(
                    "ERROR",
                    slug,
                    "catalog-cog-path",
                    "COG canonical_path must end with .tif or .tiff.",
                )
            )
        if canonical_format == "csv" and not canonical_path.lower().endswith(".csv"):
            findings.append(
                Finding(
                    "ERROR",
                    slug,
                    "catalog-csv-path",
                    "CSV canonical_path must end with .csv.",
                )
            )
        metadata_paths = row.get("metadata_paths") or ""
        if "README.md" not in {part.strip() for part in metadata_paths.split(";") if part.strip()}:
            findings.append(
                Finding(
                    "WARN",
                    slug,
                    "catalog-metadata-paths",
                    "metadata_paths should include README.md.",
                )
            )
    return findings


def validate_asset_roots(
    bucket: str,
    blobs: Sequence[BlobInfo],
    categories: Dict[str, set[str]],
    catalog_rows: Sequence[Dict[str, str]],
    catalog_by_slug: Dict[str, Dict[str, str]],
    skip_readme_content: bool,
    prefix: str,
) -> List[Finding]:
    findings: List[Finding] = []
    audit_blobs = [blob for blob in blobs if not is_taxonomy_placeholder_or_doc(blob.name, categories)]
    roots: Dict[str, List[BlobInfo]] = {}
    object_names = {blob.name for blob in audit_blobs}

    for blob in audit_blobs:
        root, path_issue = asset_root_for(blob.name, categories)
        if path_issue:
            findings.append(
                Finding(
                    "ERROR",
                    blob.name,
                    "path-taxonomy",
                    path_issue,
                    uploader_hint(blob),
                    "Do not move automatically; identify uploader/owner and confirm intended classification first.",
                )
            )
        if root:
            roots.setdefault(root, []).append(blob)

    for root, root_blobs in sorted(roots.items()):
        category, subcategory, slug = root.split("/")
        row = catalog_by_slug.get(slug)
        if not SLUG_RE.match(slug):
            findings.append(
                Finding(
                    "ERROR",
                    root,
                    "asset-slug",
                    "Asset slug is not lowercase kebab-case.",
                    uploader_hint(root_blobs[0]),
                    "Discuss rename impact with consumers before changing.",
                )
            )
        readme_blob = next((blob for blob in root_blobs if blob.name == f"{root}/README.md"), None)
        latest_blobs = [blob for blob in root_blobs if blob.name.startswith(f"{root}/latest/")]
        if not latest_blobs:
            findings.append(
                Finding(
                    "ERROR",
                    root,
                    "latest-exists",
                    "Asset root has no `latest/` object.",
                    uploader_hint(readme_blob or root_blobs[0]),
                    "Offer to add latest only after confirming the canonical source object.",
                )
            )
        for blob in root_blobs:
            findings.extend(validate_object_layout(root, blob, row))

        readme_text = None
        if readme_blob and not skip_readme_content:
            readme_text = download_readme_text(bucket, readme_blob.name, readme_blob.generation)
        requires_raster_metadata = object_is_raster_like(
            row.get("canonical_path", "") if row else "",
            row,
        ) or any(object_is_raster_like(blob.name, row) for blob in root_blobs)
        findings.extend(
            validate_readme(
                root,
                readme_blob,
                readme_text,
                requires_raster_metadata=requires_raster_metadata,
            )
        )
        findings.extend(
            validate_zarr_latest_manifest(
                bucket=bucket,
                root=root,
                row=row,
                root_blobs=root_blobs,
                object_names=object_names,
                skip_text_content=skip_readme_content,
            )
        )

    findings.extend(validate_catalog(bucket, roots, catalog_rows, catalog_by_slug, object_names, prefix))
    return findings


def release_integrity_severity(mode: str) -> str:
    return RELEASE_INDEX_MODES[mode]


def row_asset_root(bucket: str, row: Dict[str, str]) -> str:
    canonical_path = row.get("canonical_path", "")
    expected_prefix = f"gs://{bucket}/"
    if not canonical_path.startswith(expected_prefix) or "/latest/" not in canonical_path:
        return ""
    return canonical_path[len(expected_prefix) :].split("/latest/", 1)[0]


def scheduled_cadence(row: Dict[str, str]) -> str:
    cadence = (row.get("update_cadence") or "").lower()
    for candidate in SCHEDULE_FRESHNESS_DAYS:
        if candidate in cadence:
            return candidate
    return ""


def is_versioned_active_asset(
    *,
    root: str,
    row: Dict[str, str],
    object_names: set[str],
) -> bool:
    if (row.get("status") or "").strip().lower() != "active":
        return False
    if scheduled_cadence(row):
        return True
    if any(name.startswith(f"{root}/releases/") for name in object_names):
        return True
    if any(name.startswith(f"{root}/runs/") for name in object_names):
        return True
    metadata_paths = row.get("metadata_paths") or ""
    return any(part.strip().startswith("runs/") for part in metadata_paths.split(";"))


def parse_iso_date(value: Any) -> dt.date | None:
    if not isinstance(value, str) or not DATE_RE.match(value):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def gcs_object_name_if_same_bucket(bucket: str, uri: str) -> str:
    expected_prefix = f"gs://{bucket}/"
    if not uri.startswith(expected_prefix):
        return ""
    return uri[len(expected_prefix) :]


def validate_indexed_file_exists(
    *,
    bucket: str,
    root: str,
    release_date: str,
    file_entry: Any,
    object_names: set[str],
    severity: str,
) -> List[Finding]:
    if not isinstance(file_entry, dict):
        return [
            Finding(
                severity,
                root,
                "release-index-file-entry",
                f"Release {release_date} has a non-object file entry.",
                "unknown",
                "Rebuild the release index from remote releases and run records.",
            )
        ]
    path = str(file_entry.get("path") or "")
    object_name = gcs_object_name_if_same_bucket(bucket, path)
    if not object_name or object_name not in object_names:
        return [
            Finding(
                severity,
                path or root,
                "release-index-file-exists",
                f"Indexed release file for {release_date} does not exist in the bucket listing.",
                "unknown",
                "Confirm the release object path, then rebuild or repair the release index.",
            )
        ]
    return []


def validate_release_integrity(
    *,
    bucket: str,
    blobs: Sequence[BlobInfo],
    catalog_rows: Sequence[Dict[str, str]],
    mode: str,
    today: dt.date | None = None,
) -> List[Finding]:
    severity = release_integrity_severity(mode)
    findings: List[Finding] = []
    object_by_name = {blob.name: blob for blob in blobs}
    object_names = set(object_by_name)
    current_date = today or dt.datetime.now(dt.UTC).date()

    for row in catalog_rows:
        slug = (row.get("asset_slug") or "").strip()
        if not slug:
            continue
        root = row_asset_root(bucket, row)
        if not root or not is_versioned_active_asset(root=root, row=row, object_names=object_names):
            continue

        index_name = f"{RELEASE_INDEX_PREFIX}/{slug}.json"
        index_uri = f"gs://{bucket}/{index_name}"
        index_blob = object_by_name.get(index_name)
        if not index_blob:
            findings.append(
                Finding(
                    severity,
                    index_uri,
                    "release-index-exists",
                    "Versioned active asset has no JSON release index.",
                    "unknown",
                    release_index_repair_next_step(slug),
                    "Consumers cannot discover release history or verify immutable release objects for this asset.",
                    "release-index",
                    release_index_exists_prompt(asset_slug=slug, exact_gcs_uri=index_uri),
                )
            )
            continue

        try:
            payload = json.loads(download_object_text(bucket, index_blob.name, index_blob.generation))
        except (json.JSONDecodeError, OSError) as exc:
            findings.append(
                Finding(
                    severity,
                    f"gs://{bucket}/{index_name}",
                    "release-index-json",
                    f"Release index is not valid JSON: {exc}.",
                    uploader_hint(index_blob),
                    "Rebuild the release index from remote releases and run records.",
                )
            )
            continue
        if not isinstance(payload, dict):
            findings.append(
                Finding(
                    severity,
                    f"gs://{bucket}/{index_name}",
                    "release-index-json",
                    "Release index must be a JSON object.",
                    uploader_hint(index_blob),
                    "Rebuild the release index from remote releases and run records.",
                )
            )
            continue
        if payload.get("asset_slug") not in (None, slug):
            findings.append(
                Finding(
                    severity,
                    f"gs://{bucket}/{index_name}",
                    "release-index-slug",
                    f"Release index asset_slug is {payload.get('asset_slug')!r}, expected {slug!r}.",
                    uploader_hint(index_blob),
                    "Rebuild or replace the mismatched release index.",
                )
            )

        releases = payload.get("releases") or []
        if not isinstance(releases, list):
            findings.append(
                Finding(
                    severity,
                    f"gs://{bucket}/{index_name}",
                    "release-index-releases",
                    "Release index `releases` must be a list.",
                    uploader_hint(index_blob),
                    "Rebuild the release index from remote releases and run records.",
                )
            )
            continue

        release_dates = []
        canonical_format = str(row.get("canonical_format") or "").strip().lower()
        cadence = scheduled_cadence(row)
        for release in releases:
            if not isinstance(release, dict):
                findings.append(
                    Finding(
                        severity,
                        f"gs://{bucket}/{index_name}",
                        "release-index-release-entry",
                        "Release index contains a non-object release entry.",
                        uploader_hint(index_blob),
                        "Rebuild the release index from remote releases and run records.",
                    )
                )
                continue
            release_date = str(release.get("date") or "")
            if parse_iso_date(release_date):
                release_dates.append(release_date)
            canonical_file = release_file_for_format(release.get("files") or [], canonical_format)
            if canonical_format and not release_file_path(canonical_file):
                findings.append(
                    Finding(
                        severity,
                        index_uri,
                        "release-index-canonical-file",
                        f"Indexed release {release_date or '<unknown>'} is missing canonical {canonical_format} file.",
                        uploader_hint(index_blob),
                        release_index_repair_next_step(slug),
                        "Catalog generation and SDK consumers require every indexed release to expose its canonical file.",
                        "release-index",
                        canonical_file_missing_prompt(asset_slug=slug, release_date=release_date or "<unknown>"),
                    )
                )
            if parse_iso_date(release_date) and not isinstance(release.get("rows"), int):
                findings.append(
                    Finding(
                        "WARN",
                        index_uri,
                        "release-index-rows",
                        f"Indexed release {release_date} is missing an integer rows count.",
                        uploader_hint(index_blob),
                        "Backfill rows from validated release metadata when preparing the next reviewed release-index repair.",
                        "Release counts are useful for auditability but do not currently break consumers.",
                        "release-index-backfill",
                    )
                )
            run_record_path = str(release.get("run_record_path") or "")
            run_record_name = gcs_object_name_if_same_bucket(bucket, run_record_path) if run_record_path else ""
            if cadence and parse_iso_date(release_date) and not run_record_path:
                findings.append(
                    Finding(
                        "WARN",
                        index_uri,
                        "release-index-run-record",
                        f"Scheduled release {release_date} is missing run_record_path.",
                        uploader_hint(index_blob),
                        "Backfill or document the run record path when preparing the next reviewed release-index repair.",
                        "Run records are audit evidence for scheduled releases but missing historical records do not currently break consumers.",
                        "release-index-backfill",
                    )
                )
            if run_record_path and (not run_record_name or run_record_name not in object_names):
                findings.append(
                    Finding(
                        severity,
                        run_record_path or f"gs://{bucket}/{index_name}",
                        "release-index-run-record-exists",
                        f"Indexed release {release_date or '<unknown>'} has no existing run record.",
                        uploader_hint(index_blob),
                        "Confirm the run record path, then rebuild or repair the release index.",
                    )
                )
            for file_entry in release.get("files") or []:
                findings.extend(
                    validate_indexed_file_exists(
                        bucket=bucket,
                        root=f"gs://{bucket}/{root}",
                        release_date=release_date or "<unknown>",
                        file_entry=file_entry,
                        object_names=object_names,
                        severity=severity,
                    )
                )
                if isinstance(file_entry, dict) and release_file_path(file_entry):
                    sha256 = str(file_entry.get("sha256") or "").strip()
                    if not re.fullmatch(r"[a-fA-F0-9]{64}", sha256):
                        findings.append(
                            Finding(
                                "WARN",
                                index_uri,
                                "release-index-file-sha256",
                                f"Indexed release file for {release_date or '<unknown>'} is missing a valid sha256.",
                                uploader_hint(index_blob),
                                "Backfill sha256 from the immutable object bytes when preparing the next reviewed release-index repair.",
                                "Checksums are useful audit evidence but missing historical hashes do not currently break consumers.",
                                "release-index-backfill",
                            )
                        )

        latest_release = payload.get("latest_release") or {}
        latest_release_date = latest_release.get("date") if isinstance(latest_release, dict) else None
        newest_release_date = max(release_dates) if release_dates else None
        if newest_release_date and latest_release_date != newest_release_date:
            findings.append(
                Finding(
                    severity,
                    f"gs://{bucket}/{index_name}",
                    "release-index-latest-release",
                    f"latest_release is {latest_release_date!r}, expected newest successful release {newest_release_date!r}.",
                    uploader_hint(index_blob),
                    "Rebuild the release index so latest_release points at the newest success.",
                )
            )

        indexed_success_dates = set(release_dates)
        for run_blob in sorted(
            (blob for blob in blobs if blob.name.startswith(f"{root}/runs/") and blob.name.endswith(".json")),
            key=lambda item: item.name,
        ):
            try:
                run_payload = json.loads(download_object_text(bucket, run_blob.name, run_blob.generation))
            except (json.JSONDecodeError, OSError):
                continue
            run_date = str(run_payload.get("run_date") or Path(run_blob.name).stem)
            if run_payload.get("status") == "success" and run_date not in indexed_success_dates:
                run_record_uri = f"gs://{bucket}/{run_blob.name}"
                findings.append(
                    Finding(
                        severity,
                        run_record_uri,
                        "release-index-success-run-indexed",
                        f"Successful run record {run_date} is not present in the release index.",
                        uploader_hint(run_blob),
                        release_index_repair_next_step(slug),
                        "Successful scheduled runs are invisible to release-history consumers until indexed.",
                        "release-index",
                        successful_run_missing_prompt(asset_slug=slug, exact_run_record_uri=run_record_uri),
                    )
                )

        if cadence:
            latest_run = payload.get("latest_run") or {}
            latest_run_date = parse_iso_date(latest_run.get("date") if isinstance(latest_run, dict) else None)
            if not latest_run_date:
                findings.append(
                    Finding(
                        severity,
                        f"gs://{bucket}/{index_name}",
                        "release-index-latest-run",
                        "Scheduled asset release index has no dated latest_run.",
                        uploader_hint(index_blob),
                        "Update scheduled publishing so every success or meaningful skip refreshes latest_run.",
                    )
                )
                continue
            max_age = SCHEDULE_FRESHNESS_DAYS[cadence]
            age_days = (current_date - latest_run_date).days
            if age_days > max_age:
                findings.append(
                    Finding(
                        severity,
                        f"gs://{bucket}/{index_name}",
                        "release-index-latest-run-fresh",
                        f"Scheduled {cadence} asset latest_run is {age_days} day(s) old; expected <= {max_age}.",
                        uploader_hint(index_blob),
                        "Check the Cloud Scheduler/Cloud Run job and update latest_run after a successful retry or source-unchanged skip.",
                    )
                )

    return findings


def load_json_blob(bucket: str, blob: BlobInfo) -> dict[str, Any] | None:
    try:
        payload = json.loads(download_object_text(bucket, blob.name, blob.generation))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def feature_metadata_release_root(metadata_entry: dict[str, Any], release: str) -> str:
    path = release_file_path(metadata_entry)
    marker = f"/releases/{release}/"
    if marker not in path:
        return ""
    without_scheme = path[5:] if path.startswith("gs://") else path
    _bucket, separator, name = without_scheme.partition("/")
    if not separator:
        return ""
    return name.split(marker, 1)[0]


def index_status_policy_issue(
    *,
    bucket: str,
    asset_root: str,
    release: str,
    policy: Any,
    allow_relative_path: bool,
    label: str,
) -> str:
    if not isinstance(policy, dict) or policy.get("mode") != INDEX_STATUS_MODE:
        return f"{label} index_status_policy is missing or invalid"
    if policy.get("path") is not None:
        return f"{label} index_status_policy path must be null while Firestore serving is inactive"
    return ""


def index_load_matches(
    record: Any,
    *,
    asset_slug: str,
    release: str,
    metadata_entry: dict[str, Any],
    schema_entry: dict[str, Any],
    manifest_entry: dict[str, Any],
) -> bool:
    if not isinstance(record, dict):
        return False
    if record.get("status") != "success" or record.get("dry_run") is True:
        return False
    load_id = record.get("load_id")
    if not isinstance(load_id, str) or not LOAD_ID_RE.fullmatch(load_id):
        return False
    if record.get("asset_slug") != asset_slug or record.get("release") != release:
        return False
    return (
        record.get("sidecar_uri") == metadata_entry.get("path")
        and same_generation(record.get("sidecar_generation"), metadata_entry.get("generation"))
        and record.get("schema_uri") == schema_entry.get("path")
        and same_generation(record.get("schema_generation"), schema_entry.get("generation"))
        and record.get("manifest_uri") == manifest_entry.get("path")
        and same_generation(record.get("manifest_generation"), manifest_entry.get("generation"))
    )


def validate_feature_metadata_readiness(
    *,
    bucket: str,
    blobs: Sequence[BlobInfo],
    catalog_rows: Sequence[Dict[str, str]],
    feature_metadata_docs: Dict[str, dict[str, Any]],
) -> List[Finding]:
    findings: List[Finding] = []
    object_by_name = {blob.name: blob for blob in blobs}

    for row in catalog_rows:
        slug = (row.get("asset_slug") or "").strip()
        if not slug or slug not in feature_metadata_docs:
            continue
        if (row.get("status") or "").strip().lower() != "active":
            continue
        root = row_asset_root(bucket, row)
        issues: list[str] = []
        if not root:
            issues.append("catalog canonical_path does not resolve to a latest/ asset root")
        else:
            for role, suffix in FEATURE_METADATA_LATEST_FILES.items():
                latest_name = f"{root}/latest/{slug}{suffix}"
                if latest_name not in object_by_name:
                    issues.append(f"missing latest {role} object: gs://{bucket}/{latest_name}")

        index_name = f"{RELEASE_INDEX_PREFIX}/{slug}.json"
        index_blob = object_by_name.get(index_name)
        release = ""
        latest_release: dict[str, Any] | None = None
        metadata_entry: dict[str, Any] | None = None
        schema_entry: dict[str, Any] | None = None
        manifest_entry: dict[str, Any] | None = None
        if not index_blob:
            issues.append(f"missing release index: gs://{bucket}/{index_name}")
        else:
            index_payload = load_json_blob(bucket, index_blob)
            if not index_payload:
                issues.append(f"release index is not valid JSON object: gs://{bucket}/{index_name}")
            else:
                latest_release = index_payload.get("latest_release")
                if not isinstance(latest_release, dict):
                    issues.append("release index latest_release is missing or not an object")
                    latest_release = None
                else:
                    release = str(latest_release.get("date") or "")
                    if not parse_iso_date(release):
                        issues.append("release index latest_release.date is not a concrete YYYY-MM-DD date")
                    files = latest_release.get("files")
                    if not isinstance(files, list):
                        issues.append("release index latest_release.files is missing or not an array")
                        files = []
                    metadata_entry = canonical_metadata_release_file(files)
                    schema_entry = release_file_for_format(files, "schema")
                    manifest_entry = release_file_for_format(files, "manifest")
                    for role, entry in (
                        ("metadata", metadata_entry),
                        ("schema", schema_entry),
                        ("manifest", manifest_entry),
                    ):
                        if not entry:
                            issues.append(f"latest_release.files is missing {role} entry")
                            continue
                        object_name = gcs_object_name_if_same_bucket(bucket, release_file_path(entry))
                        blob = object_by_name.get(object_name)
                        if not blob:
                            issues.append(f"indexed {role} object is missing: {entry.get('path')}")
                            continue
                        if not same_generation(entry.get("generation"), blob.generation):
                            issues.append(
                                f"indexed {role} generation {entry.get('generation')!r} does not match "
                                f"live generation {blob.generation}"
                            )

        schema_payload: dict[str, Any] | None = None
        manifest_payload: dict[str, Any] | None = None
        if schema_entry:
            schema_name = gcs_object_name_if_same_bucket(bucket, release_file_path(schema_entry))
            schema_blob = object_by_name.get(schema_name)
            if schema_blob:
                schema_payload = load_json_blob(bucket, schema_blob)
                if not schema_payload:
                    issues.append(f"release schema is not a valid JSON object: {schema_entry.get('path')}")
                else:
                    try:
                        release_feature_model.validate_release_schema(
                            schema_payload,
                            expected_asset_slug=slug,
                            expected_release=release or None,
                        )
                    except release_feature_model.ReleaseFeatureModelError as exc:
                        issues.append(f"release schema is invalid: {exc}")
        if manifest_entry:
            manifest_name = gcs_object_name_if_same_bucket(bucket, release_file_path(manifest_entry))
            manifest_blob = object_by_name.get(manifest_name)
            if manifest_blob:
                manifest_payload = load_json_blob(bucket, manifest_blob)
                if not manifest_payload:
                    issues.append(f"release manifest is not a valid JSON object: {manifest_entry.get('path')}")
                else:
                    try:
                        artifacts = release_feature_model.validate_release_manifest(
                            manifest_payload,
                            expected_asset_slug=slug,
                            expected_release=release or None,
                            require_generations=True,
                        )
                    except release_feature_model.ReleaseFeatureModelError as exc:
                        issues.append(f"release manifest is invalid: {exc}")
                    else:
                        for role, entry in (
                            ("metadata", metadata_entry),
                            ("schema", schema_entry),
                            ("manifest", manifest_entry),
                        ):
                            if not entry:
                                continue
                            artifact = artifacts.get(role)
                            if not artifact:
                                continue
                            if artifact.get("path") != entry.get("path"):
                                issues.append(f"manifest {role} path does not match release index")
                            if role != "manifest" and not same_generation(
                                artifact.get("generation"),
                                entry.get("generation"),
                            ):
                                issues.append(f"manifest {role} generation does not match release index")

        if release and metadata_entry and schema_entry and manifest_entry:
            asset_root = feature_metadata_release_root(metadata_entry, release)
            if not asset_root:
                issues.append(f"metadata sidecar path is not under releases/{release}/")
            else:
                if latest_release:
                    if latest_release.get("index_load_status") != INDEX_LOAD_STATUS:
                        issues.append("release index latest_release is missing index_load_status")
                    policy_issue = index_status_policy_issue(
                        bucket=bucket,
                        asset_root=asset_root,
                        release=release,
                        policy=latest_release.get("index_status_policy"),
                        allow_relative_path=False,
                        label="release index latest_release",
                    )
                    if policy_issue:
                        issues.append(policy_issue)
                if manifest_payload:
                    policy_issue = index_status_policy_issue(
                        bucket=bucket,
                        asset_root=asset_root,
                        release=release,
                        policy=manifest_payload.get("index_status_policy"),
                        allow_relative_path=True,
                        label="manifest",
                    )
                    if policy_issue:
                        issues.append(policy_issue)
        if issues:
            findings.append(
                Finding(
                    "ERROR",
                    f"docs/assets/{slug}.md",
                    "feature-metadata-contract-ready",
                    "Asset doc advertises feature_metadata, but the live bucket metadata contract is not usable: "
                    + "; ".join(issues),
                    "unknown",
                    "Publish or repair a complete metadata-backed release through the approved workflow before advertising feature metadata.",
                    "Consumers may see feature metadata in the catalog but receive missing-object errors or metadata-service 409 responses.",
                    "feature-metadata",
                    feature_metadata_prompt(asset_slug=slug),
                )
            )

    return findings


def finding_blocks_exit(finding: Finding, *, release_integrity_mode: str, health_profile: str = "advisory") -> bool:
    if health_profile == "production":
        return finding.severity == "ERROR"
    if finding.check.startswith("release-index-") and release_integrity_mode == "warn":
        return False
    return finding.severity in {"ERROR", "WARN"}


def render_markdown(findings: Sequence[Finding], bucket: str, prefix: str, object_count: int) -> str:
    counts = {"ERROR": 0, "WARN": 0, "INFO": 0}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1

    lines = [
        "# Shared Datasets Compliance Audit",
        "",
        f"- Bucket: `gs://{bucket}/{prefix}`",
        f"- Objects inspected: {object_count}",
        "- Findings: "
        f"{counts.get('ERROR', 0)} error(s), "
        f"{counts.get('WARN', 0)} warning(s), "
        f"{counts.get('INFO', 0)} info item(s)",
        "",
    ]
    if not findings:
        lines.append("No compliance findings.")
        return "\n".join(lines)

    for severity in ("ERROR", "WARN", "INFO"):
        subset = [finding for finding in findings if finding.severity == severity]
        if not subset:
            continue
        lines.append(f"## {severity}")
        lines.append("")
        for finding in subset:
            lines.extend(
                [
                    f"- `{finding.path}`",
                    f"  - Check: `{finding.check}`",
                    f"  - Finding: {finding.message}",
                    f"  - Uploader hint: {finding.uploader_hint}",
                ]
            )
            if finding.suggested_next_step:
                lines.append(f"  - Suggested next step: {finding.suggested_next_step}")
            if finding.impact:
                lines.append(f"  - Impact: {finding.impact}")
            if finding.repair_category:
                lines.append(f"  - Repair category: `{finding.repair_category}`")
            if finding.codex_prompt:
                lines.extend(
                    [
                        "  - Codex repair prompt:",
                        "",
                        "```text",
                        finding.codex_prompt,
                        "```",
                    ]
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def apply_health_profile_defaults(args: argparse.Namespace) -> None:
    if args.health_profile == "production":
        args.release_integrity_mode = "enforce"
        args.fail_on_findings = True


def audit_exit_code(args: argparse.Namespace, findings: Sequence[Finding]) -> int:
    if args.fail_on_findings and any(
        finding_blocks_exit(
            f,
            release_integrity_mode=args.release_integrity_mode,
            health_profile=args.health_profile,
        )
        for f in findings
    ):
        return 1
    return 0


def run_audit(args: argparse.Namespace) -> AuditRunResult:
    apply_health_profile_defaults(args)
    categories = load_categories(Path(args.categories))
    catalog_rows, catalog_by_slug = load_catalog(Path(args.catalog))
    if args.local_only:
        blobs = []
        findings = validate_local_catalog(
            bucket=args.bucket,
            categories=categories,
            catalog_rows=catalog_rows,
        )
    else:
        blobs = list_blobs(args.bucket, args.prefix)
        findings = validate_asset_roots(
            args.bucket,
            blobs,
            categories,
            catalog_rows,
            catalog_by_slug,
            skip_readme_content=args.skip_readme_content,
            prefix=args.prefix,
        )
    if not args.local_only and not args.skip_remote_catalog_check and not args.prefix:
        findings.extend(validate_remote_catalog(args.bucket, Path(args.catalog)))
    if not args.local_only and not args.prefix:
        findings.extend(
            validate_release_integrity(
                bucket=args.bucket,
                blobs=blobs,
                catalog_rows=catalog_rows,
                mode=args.release_integrity_mode,
            )
        )
    if not args.local_only and not args.prefix and args.health_profile == "production":
        findings.extend(
            validate_feature_metadata_readiness(
                bucket=args.bucket,
                blobs=blobs,
                catalog_rows=catalog_rows,
                feature_metadata_docs=load_asset_doc_feature_metadata(Path(args.asset_docs_dir)),
            )
        )

    return AuditRunResult(
        findings=findings,
        object_count=len(blobs),
        exit_code=audit_exit_code(args, findings),
    )


def main() -> int:
    args = parse_args()
    result = run_audit(args)

    if args.format == "json":
        print(
            json.dumps(
                {
                    "bucket": args.bucket,
                    "prefix": args.prefix,
                    "objects_inspected": result.object_count,
                    "findings": [asdict(finding) for finding in result.findings],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(render_markdown(result.findings, args.bucket, args.prefix, result.object_count))

    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
