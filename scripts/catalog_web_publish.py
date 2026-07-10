#!/usr/bin/env python3
"""Publish a generated catalog web bundle with generation preconditions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from google.api_core.exceptions import NotFound, PreconditionFailed

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import gcs_asset


DEFAULT_DESTINATION = "gs://skytruth-shared-datasets-1/_catalog/web"
DEFAULT_CACHE_CONTROL = "no-cache, max-age=0, must-revalidate"
DEFAULT_CATALOG_DESTINATION = "gs://skytruth-shared-datasets-1/_catalog/shared-datasets-catalog.csv"
ROOT_FILES = {"index.html", "styles.css", "app.js", "map-preview.js", "catalog.json"}


class CatalogWebPublishError(RuntimeError):
    """Raised when the catalog web bundle cannot be safely published."""


def generated_files(source_dir: Path) -> list[Path]:
    if not source_dir.is_dir():
        raise CatalogWebPublishError(f"source directory does not exist: {source_dir}")
    files = sorted(path for path in source_dir.rglob("*") if path.is_file())
    missing = sorted(name for name in ROOT_FILES if not (source_dir / name).is_file())
    if missing:
        raise CatalogWebPublishError(f"generated bundle is missing required file(s): {', '.join(missing)}")
    return files


def destination_for(source_dir: Path, path: Path, destination_uri: str) -> str:
    bucket_name, prefix = gcs_asset.parse_gs_uri(destination_uri.rstrip("/"))
    if not bucket_name or not prefix:
        raise CatalogWebPublishError("destination must be a gs:// object prefix")
    relative = path.relative_to(source_dir).as_posix()
    return f"gs://{bucket_name}/{prefix.rstrip('/')}/{relative}"


def publish_file(
    source: Path,
    destination_uri: str,
    *,
    cache_control: str,
    dry_run: bool,
) -> dict[str, Any]:
    gcs_asset.require_mutation_allowed(destination_uri, operation="catalog web publish")
    blob = gcs_asset.get_blob(destination_uri)
    try:
        blob.reload()
        generation: int | None = int(blob.generation)
    except NotFound:
        generation = 0

    if dry_run:
        return {
            "source": str(source),
            "destination_uri": destination_uri,
            "destination_generation": generation,
            "would_replace": generation != 0,
            "cache_control": cache_control,
            "content_type": gcs_asset.content_type_for(source, None),
        }

    upload_kwargs = {"if_generation_match": generation}
    blob.cache_control = cache_control
    try:
        blob.upload_from_filename(
            source,
            content_type=gcs_asset.content_type_for(source, None),
            **upload_kwargs,
        )
    except PreconditionFailed as exc:
        raise CatalogWebPublishError(f"generation precondition failed for {destination_uri}") from exc
    blob.reload()
    return {
        "source": str(source),
        "destination_uri": destination_uri,
        "generation": int(blob.generation),
        "size": int(blob.size or 0),
        "cache_control": blob.cache_control,
        "content_type": blob.content_type,
    }


def publish_bundle(
    *,
    source_dir: Path,
    destination_uri: str,
    cache_control: str,
    dry_run: bool,
) -> list[dict[str, Any]]:
    results = []
    for path in generated_files(source_dir):
        results.append(
            publish_file(
                path,
                destination_for(source_dir, path, destination_uri),
                cache_control=cache_control,
                dry_run=dry_run,
            )
        )
    return results


def publish_catalog_contract(
    *,
    catalog_source: Path | None,
    catalog_destination: str,
    cache_control: str,
    dry_run: bool,
) -> dict[str, Any] | None:
    if catalog_source is None:
        return None
    if not catalog_source.is_file():
        raise CatalogWebPublishError(f"catalog source does not exist: {catalog_source}")
    return publish_file(
        catalog_source,
        catalog_destination,
        cache_control=cache_control,
        dry_run=dry_run,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Generated catalog web bundle directory.")
    parser.add_argument("--destination", default=DEFAULT_DESTINATION, help="Destination gs:// prefix.")
    parser.add_argument(
        "--catalog-source",
        type=Path,
        default=None,
        help="Optional root CSV catalog contract to publish alongside the web bundle.",
    )
    parser.add_argument("--catalog-destination", default=DEFAULT_CATALOG_DESTINATION)
    parser.add_argument("--cache-control", default=DEFAULT_CACHE_CONTROL)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        results = publish_bundle(
            source_dir=args.source,
            destination_uri=args.destination,
            cache_control=args.cache_control,
            dry_run=args.dry_run,
        )
        catalog_result = publish_catalog_contract(
            catalog_source=args.catalog_source,
            catalog_destination=args.catalog_destination,
            cache_control=args.cache_control,
            dry_run=args.dry_run,
        )
    except CatalogWebPublishError as exc:
        print(f"catalog web publish failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"dry_run": args.dry_run, "published": results, "catalog_contract": catalog_result},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
