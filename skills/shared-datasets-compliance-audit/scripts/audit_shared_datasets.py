#!/usr/bin/env python3
"""Read-only compliance audit for the shared datasets bucket.

The script lists GCS objects, validates their paths against AGENTS.md conventions,
checks adjacent README content, and compares discovered asset roots with the repo
catalog. It reports findings only; it never mutates local or remote state.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import yaml
from google.cloud import storage


APPROVED_DATA_EXTENSIONS = {".fgb", ".pmtiles", ".geojson", ".csv"}
RESERVED_TOP_LEVEL = {"_catalog", "_templates", "_scratch", "_deprecated"}
SYSTEM_TOP_LEVEL = {"000-system"}
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RUN_RECORD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")
UPLOADER_METADATA_KEYS = ("uploaded_by", "uploader", "created_by", "creator", "owner", "author")

README_REQUIRED_SNIPPETS = {
    "status": "**Status:**",
    "owner": "**Owner:**",
    "last_updated": "**Last updated:**",
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only shared datasets compliance audit.")
    parser.add_argument(
        "--bucket",
        default=os.environ.get("SHARED_DATASETS_BUCKET", "skytruth-shared-datasets-1"),
        help="GCS bucket name, without gs://.",
    )
    parser.add_argument("--prefix", default="", help="Optional GCS object prefix to audit.")
    parser.add_argument("--catalog", default="catalog/shared-datasets-catalog.csv", help="Local catalog CSV path.")
    parser.add_argument("--categories", default="catalog/categories.yaml", help="Local categories YAML path.")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", help="Output format.")
    parser.add_argument("--skip-readme-content", action="store_true", help="Do not download README.md text for checks.")
    parser.add_argument("--skip-remote-catalog-check", action="store_true", help="Do not compare bucket _catalog CSV with local catalog.")
    parser.add_argument("--fail-on-findings", action="store_true", help="Exit 1 if WARN or ERROR findings exist.")
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
    if top in categories and len(parts) < 3 and parts[-1] == "README.md":
        return True
    if top in RESERVED_TOP_LEVEL or top in SYSTEM_TOP_LEVEL:
        return True
    return False


def validate_object_layout(root: str, blob: BlobInfo) -> List[Finding]:
    findings: List[Finding] = []
    rel = blob.name[len(root) :].lstrip("/")
    parts = rel.split("/") if rel else []
    hint = uploader_hint(blob)

    if rel == "README.md":
        return findings
    if len(parts) >= 2 and parts[0] == "latest":
        if len(parts) != 2:
            findings.append(
                Finding(
                    "WARN",
                    blob.name,
                    "latest-layout",
                    "`latest/` should usually contain files directly, not nested paths.",
                    hint,
                    "Confirm this nesting is intentional before changing it.",
                )
            )
        ext = Path(parts[-1]).suffix.lower()
        if ext not in APPROVED_DATA_EXTENSIONS:
            findings.append(
                Finding(
                    "ERROR",
                    blob.name,
                    "approved-format",
                    f"Data file extension {ext or '<none>'} is not approved by default.",
                    hint,
                    "Ask the uploader/owner whether the file should be converted or documented as an exception.",
                )
            )
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
        ext = Path(parts[-1]).suffix.lower()
        if ext not in APPROVED_DATA_EXTENSIONS:
            findings.append(
                Finding(
                    "ERROR",
                    blob.name,
                    "approved-format",
                    f"Release data file extension {ext or '<none>'} is not approved by default.",
                    hint,
                    "Ask the uploader/owner whether the file should be converted or documented as an exception.",
                )
            )
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


def validate_readme(root: str, readme_blob: Optional[BlobInfo], text: Optional[str]) -> List[Finding]:
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
                Finding("ERROR", root, "catalog-category", f"Catalog category is {row.get('category')!r}, expected {category!r}.", hint)
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
        if fmt and f".{fmt.lower()}" not in APPROVED_DATA_EXTENSIONS:
            findings.append(
                Finding("ERROR", root, "catalog-format", f"Catalog canonical_format {fmt!r} is not approved by default.", hint)
            )

    for row in catalog_rows:
        slug = row.get("asset_slug", "")
        canonical_path = row.get("canonical_path", "")
        if prefix and canonical_path.startswith(f"gs://{bucket}/") and not canonical_path[len(f"gs://{bucket}/") :].startswith(prefix):
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
            findings.extend(validate_object_layout(root, blob))

        readme_text = None
        if readme_blob and not skip_readme_content:
            readme_text = download_readme_text(bucket, readme_blob.name, readme_blob.generation)
        findings.extend(validate_readme(root, readme_blob, readme_text))

    findings.extend(validate_catalog(bucket, roots, catalog_rows, catalog_by_slug, object_names, prefix))
    return findings


def render_markdown(findings: Sequence[Finding], bucket: str, prefix: str, object_count: int) -> str:
    counts = {"ERROR": 0, "WARN": 0, "INFO": 0}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1

    lines = [
        "# Shared Datasets Compliance Audit",
        "",
        f"- Bucket: `gs://{bucket}/{prefix}`",
        f"- Objects inspected: {object_count}",
        f"- Findings: {counts.get('ERROR', 0)} error(s), {counts.get('WARN', 0)} warning(s), {counts.get('INFO', 0)} info item(s)",
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
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    categories = load_categories(Path(args.categories))
    catalog_rows, catalog_by_slug = load_catalog(Path(args.catalog))
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
    if not args.skip_remote_catalog_check and not args.prefix:
        findings.extend(validate_remote_catalog(args.bucket, Path(args.catalog)))

    if args.format == "json":
        print(
            json.dumps(
                {
                    "bucket": args.bucket,
                    "prefix": args.prefix,
                    "objects_inspected": len(blobs),
                    "findings": [asdict(finding) for finding in findings],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(render_markdown(findings, args.bucket, args.prefix, len(blobs)))

    if args.fail_on_findings and any(f.severity in {"ERROR", "WARN"} for f in findings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
