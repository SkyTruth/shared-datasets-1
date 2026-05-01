"""Command-line interface for the shared dataset resolver SDK."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .catalog import Catalog, SharedDatasetsError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skytruth-datasets")
    parser.add_argument("--catalog", help="Catalog source: local path, HTTPS URL, gs:// URI, or 'packaged'.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List catalog assets.")
    list_parser.add_argument("--category", help="Filter by top-level category.")
    list_parser.add_argument("--format", help="Filter by available format.")
    list_parser.add_argument("--status", default="active", help="Filter by status. Use --all-statuses to disable.")
    list_parser.add_argument("--all-statuses", action="store_true", help="Include assets with any status.")

    url_parser = subparsers.add_parser("url", help="Resolve a dataset to a browser-facing URL.")
    url_parser.add_argument("slug")
    url_parser.add_argument("--format", dest="requested_format", help="Requested format, such as fgb or pmtiles.")
    url_parser.add_argument(
        "--url-strategy",
        choices=("public-gcs", "cdn"),
        default="public-gcs",
        help="Browser-facing URL strategy.",
    )
    url_parser.add_argument("--web-base-url", help="Base URL for CDN-style URLs, such as /pmtiles.")

    fetch_parser = subparsers.add_parser("fetch", help="Download a dataset file into the local cache.")
    fetch_parser.add_argument("slug")
    fetch_parser.add_argument("--format", dest="requested_format", help="Requested format, such as fgb or pmtiles.")
    fetch_parser.add_argument("--cache-dir", type=Path, help="Cache directory.")
    fetch_parser.add_argument("--force", action="store_true", help="Re-download even when the cached file exists.")
    fetch_parser.add_argument(
        "--access",
        choices=("public", "gcs"),
        default="public",
        help="Download via public HTTPS or authenticated GCS.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        catalog = Catalog.load(args.catalog)
        if args.command == "list":
            status = None if args.all_statuses else args.status
            _print_assets(catalog.search(category=args.category, format=args.format, status=status))
        elif args.command == "url":
            print(
                catalog.resolve(
                    args.slug,
                    args.requested_format,
                    url_strategy=args.url_strategy,
                    web_base_url=args.web_base_url,
                ).url
            )
        elif args.command == "fetch":
            print(
                catalog.fetch(
                    args.slug,
                    args.requested_format,
                    cache_dir=args.cache_dir,
                    force=args.force,
                    access=args.access,
                )
            )
        else:
            parser.error(f"unknown command: {args.command}")
    except (SharedDatasetsError, ValueError) as exc:
        print(f"skytruth-datasets: {exc}", file=sys.stderr)
        return 1
    return 0


def _print_assets(assets) -> None:
    print("asset_slug\ttitle\tformats\tlast_updated")
    for asset in assets:
        print(f"{asset.slug}\t{asset.title}\t{';'.join(asset.available_formats)}\t{asset.last_updated}")


if __name__ == "__main__":
    raise SystemExit(main())
