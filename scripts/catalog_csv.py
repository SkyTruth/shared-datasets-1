#!/usr/bin/env python3
"""Shared reader for the generated shared-datasets catalog CSV.

Single owner for parsing `catalog/shared-datasets-catalog.csv`. Consumers that
need custom error types should catch `CatalogCsvError` at their boundary. Rows
are keyed by non-empty `asset_slug`; slug validity is enforced by catalog
generation and validation, not here.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

DEFAULT_CATALOG_CSV = Path("catalog/shared-datasets-catalog.csv")


class CatalogCsvError(ValueError):
    """Raised when the catalog CSV is unreadable or malformed."""


def read_catalog_rows_text(text: str, *, label: str = "catalog") -> list[dict[str, str]]:
    """Return all catalog rows from CSV text in order."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise CatalogCsvError(f"{label}: catalog has no header row")
    return [dict(row) for row in reader]


def read_catalog_rows(path: Path = DEFAULT_CATALOG_CSV) -> list[dict[str, str]]:
    """Return all catalog rows in file order."""
    with path.open(newline="") as handle:
        return read_catalog_rows_text(handle.read(), label=str(path))


def load_catalog(path: Path = DEFAULT_CATALOG_CSV, *, missing_ok: bool = False) -> dict[str, dict[str, str]]:
    """Return catalog rows keyed by asset_slug, skipping rows without a slug."""
    if missing_ok and not path.exists():
        return {}
    return {row["asset_slug"]: row for row in read_catalog_rows(path) if row.get("asset_slug")}


def catalog_row(asset_slug: str, path: Path = DEFAULT_CATALOG_CSV) -> dict[str, str] | None:
    """Return the catalog row for one asset, or None when it is not cataloged."""
    return load_catalog(path).get(asset_slug)


def catalog_row_from_text(text: str, asset_slug: str, *, label: str = "catalog") -> dict[str, str] | None:
    """Return the catalog row for one asset from CSV text, or None when absent."""
    for row in read_catalog_rows_text(text, label=label):
        if row.get("asset_slug") == asset_slug:
            return row
    return None
