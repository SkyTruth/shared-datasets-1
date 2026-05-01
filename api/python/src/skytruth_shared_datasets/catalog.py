"""Static catalog resolver for SkyTruth shared datasets."""

from __future__ import annotations

import csv
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from importlib import resources
from io import StringIO
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping
from urllib.parse import quote
from urllib.request import Request, urlopen

DEFAULT_BUCKET = "skytruth-shared-datasets-1"
DEFAULT_CATALOG_URL = f"https://storage.googleapis.com/{DEFAULT_BUCKET}/_catalog/shared-datasets-catalog.csv"
USER_AGENT = "skytruth-shared-datasets/0.1"

FORMAT_EXTENSIONS = {
    "fgb": ".fgb",
    "pmtiles": ".pmtiles",
    "geojson": ".geojson",
    "ndgeojson": ".ndgeojson",
    "csv": ".csv",
    "cog": ".tif",
}


class SharedDatasetsError(Exception):
    """Base exception for shared dataset resolver failures."""


class CatalogLoadError(SharedDatasetsError):
    """Raised when a catalog source cannot be loaded or parsed."""


class DatasetNotFoundError(SharedDatasetsError, KeyError):
    """Raised when a requested asset slug is not present in the catalog."""


class UnsupportedFormatError(SharedDatasetsError, ValueError):
    """Raised when an asset does not publish the requested format."""


class UnsupportedVersionError(SharedDatasetsError, ValueError):
    """Raised when a version other than latest is requested."""


class FetchError(SharedDatasetsError, OSError):
    """Raised when a dataset file cannot be downloaded."""


@dataclass(frozen=True)
class CatalogAsset:
    """One row from the shared datasets catalog."""

    slug: str
    title: str
    category: str
    subcategory: str
    status: str
    owner: str
    update_cadence: str
    canonical_path: str
    canonical_format: str
    available_formats: tuple[str, ...]
    metadata_paths: tuple[str, ...]
    last_updated: str
    source: str
    license: str
    notes: str
    raw: Mapping[str, str] = field(repr=False)

    @classmethod
    def from_row(cls, row: Mapping[str, str]) -> "CatalogAsset":
        slug = _required(row, "asset_slug")
        canonical_path = _required(row, "canonical_path")
        canonical_format = _normalize_format(_required(row, "canonical_format"))
        available_formats = _split_semicolon(row.get("available_formats", ""))
        if canonical_format not in available_formats:
            available_formats = (canonical_format, *available_formats)
        return cls(
            slug=slug,
            title=row.get("title", "") or slug,
            category=row.get("category", ""),
            subcategory=row.get("subcategory", ""),
            status=row.get("status", ""),
            owner=row.get("owner", ""),
            update_cadence=row.get("update_cadence", ""),
            canonical_path=canonical_path,
            canonical_format=canonical_format,
            available_formats=available_formats,
            metadata_paths=_split_semicolon(row.get("metadata_paths", "")),
            last_updated=row.get("last_updated", ""),
            source=row.get("source", ""),
            license=row.get("license", ""),
            notes=row.get("notes", ""),
            raw=MappingProxyType(dict(row)),
        )

    def path_for_format(self, requested_format: str | None = None) -> str:
        """Return the current latest GCS URI for a published format."""
        resolved_format = _normalize_format(requested_format or self.canonical_format)
        if resolved_format not in self.available_formats:
            available = ", ".join(self.available_formats) or "none"
            raise UnsupportedFormatError(
                f"{self.slug!r} does not publish format {resolved_format!r}; available formats: {available}"
            )
        if resolved_format == self.canonical_format:
            return self.canonical_path
        if resolved_format == "zarr":
            raise UnsupportedFormatError(f"{self.slug!r} cannot infer a non-canonical Zarr path from the CSV catalog")
        extension = FORMAT_EXTENSIONS.get(resolved_format)
        if extension is None:
            raise UnsupportedFormatError(f"{self.slug!r} uses unsupported format {resolved_format!r}")
        return f"{self.latest_root}/{self.slug}{extension}"

    @property
    def latest_root(self) -> str:
        if "/latest/" not in self.canonical_path:
            raise UnsupportedFormatError(f"{self.slug!r} canonical path is not a latest/ object: {self.canonical_path}")
        return self.canonical_path.split("/latest/", 1)[0] + "/latest"


@dataclass(frozen=True)
class DatasetRef:
    """Resolved reference to a current dataset object."""

    slug: str
    title: str
    format: str
    gs_uri: str
    url: str
    last_updated: str
    cache_path: Path | None = None

    @property
    def filename(self) -> str:
        return self.gs_uri.rstrip("/").rsplit("/", 1)[-1]


class Catalog:
    """In-memory shared datasets catalog."""

    def __init__(self, assets: Iterable[CatalogAsset], *, source: str = "") -> None:
        self._assets = tuple(assets)
        self._by_slug = {asset.slug: asset for asset in self._assets}
        self.source = source

    @classmethod
    def load(cls, source: str | os.PathLike[str] | None = None, *, timeout: float = 10.0) -> "Catalog":
        """Load the catalog from a URL, path, gs:// URI, or packaged snapshot.

        With no source, the public bucket catalog is used first and the packaged
        snapshot is used as an offline fallback.
        """
        if source is None:
            try:
                text = _read_url(DEFAULT_CATALOG_URL, timeout=timeout)
                return cls.from_csv_text(text, source=DEFAULT_CATALOG_URL)
            except Exception:
                text = _read_packaged_catalog()
                return cls.from_csv_text(text, source="packaged")

        source_text = os.fspath(source)
        if source_text == "packaged":
            return cls.from_csv_text(_read_packaged_catalog(), source="packaged")
        if _is_url(source_text):
            try:
                return cls.from_csv_text(_read_url(source_text, timeout=timeout), source=source_text)
            except Exception as exc:
                raise CatalogLoadError(f"Could not load catalog from {source_text}: {exc}") from exc
        if source_text.startswith("gs://"):
            url = gs_to_https(source_text)
            try:
                return cls.from_csv_text(_read_url(url, timeout=timeout), source=source_text)
            except Exception as exc:
                raise CatalogLoadError(f"Could not load catalog from {source_text}: {exc}") from exc
        try:
            return cls.from_csv_text(Path(source_text).read_text(), source=source_text)
        except OSError as exc:
            raise CatalogLoadError(f"Could not load catalog from {source_text}: {exc}") from exc

    @classmethod
    def from_csv_text(cls, text: str, *, source: str = "") -> "Catalog":
        reader = csv.DictReader(StringIO(text))
        if not reader.fieldnames:
            raise CatalogLoadError("Catalog CSV has no header row")
        assets: list[CatalogAsset] = []
        for line_number, row in enumerate(reader, start=2):
            try:
                assets.append(CatalogAsset.from_row(row))
            except ValueError as exc:
                raise CatalogLoadError(f"Invalid catalog row at line {line_number}: {exc}") from exc
        return cls(assets, source=source)

    def __iter__(self) -> Iterable[CatalogAsset]:
        return iter(self._assets)

    def __len__(self) -> int:
        return len(self._assets)

    @property
    def slugs(self) -> tuple[str, ...]:
        return tuple(self._by_slug)

    def get(self, slug: str) -> CatalogAsset:
        try:
            return self._by_slug[slug]
        except KeyError as exc:
            raise DatasetNotFoundError(f"Unknown shared dataset asset slug: {slug}") from exc

    def search(
        self,
        *,
        category: str | None = None,
        format: str | None = None,
        status: str | None = "active",
    ) -> list[CatalogAsset]:
        requested_format = _normalize_format(format) if format else None
        matches = []
        for asset in self._assets:
            if status is not None and asset.status != status:
                continue
            if category is not None and asset.category != category:
                continue
            if requested_format is not None and requested_format not in asset.available_formats:
                continue
            matches.append(asset)
        return matches

    def resolve(self, slug: str, format: str | None = None, *, version: str = "latest") -> DatasetRef:
        if version != "latest":
            raise UnsupportedVersionError("Only version='latest' is supported by the static CSV catalog resolver")
        asset = self.get(slug)
        gs_uri = asset.path_for_format(format)
        resolved_format = _normalize_format(format or asset.canonical_format)
        return DatasetRef(
            slug=asset.slug,
            title=asset.title,
            format=resolved_format,
            gs_uri=gs_uri,
            url=gs_to_https(gs_uri),
            last_updated=asset.last_updated,
        )

    def fetch(
        self,
        slug: str,
        format: str | None = None,
        *,
        cache_dir: str | os.PathLike[str] | None = None,
        force: bool = False,
        timeout: float = 60.0,
    ) -> Path:
        ref = self.resolve(slug, format)
        destination = _cache_path(ref, cache_dir)
        if destination.exists() and not force:
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            request = Request(ref.url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=timeout) as response, temp_path.open("wb") as file_obj:
                shutil.copyfileobj(response, file_obj)
            temp_path.replace(destination)
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            raise FetchError(f"Could not download {ref.url}: {exc}") from exc
        return destination


def gs_to_https(uri: str) -> str:
    """Convert a gs:// object URI to the public storage.googleapis.com URL."""
    if not uri.startswith("gs://"):
        if _is_url(uri):
            return uri
        raise ValueError(f"Expected gs:// URI, got: {uri}")
    rest = uri[5:]
    if "/" not in rest:
        raise ValueError(f"Expected gs:// object URI, got bucket root: {uri}")
    bucket, name = rest.split("/", 1)
    if not bucket or not name:
        raise ValueError(f"Expected gs:// object URI, got: {uri}")
    return f"https://storage.googleapis.com/{bucket}/{quote(name)}"


def _cache_path(ref: DatasetRef, cache_dir: str | os.PathLike[str] | None) -> Path:
    root = Path(cache_dir) if cache_dir is not None else _default_cache_dir()
    last_updated = ref.last_updated or "latest"
    return root / ref.slug / ref.format / last_updated / ref.filename


def _default_cache_dir() -> Path:
    override = os.environ.get("SKYTRUTH_SHARED_DATASETS_CACHE")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "skytruth-shared-datasets"


def _read_packaged_catalog() -> str:
    return resources.files(__package__).joinpath("data/shared-datasets-catalog.csv").read_text()


def _read_url(url: str, *, timeout: float) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def _is_url(value: str) -> bool:
    return value.startswith("https://") or value.startswith("http://")


def _split_semicolon(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(";") if part.strip())


def _required(row: Mapping[str, str], field: str) -> str:
    value = row.get(field, "").strip()
    if not value:
        raise ValueError(f"missing required field {field!r}")
    return value


def _normalize_format(format_name: str) -> str:
    normalized = format_name.strip().lower()
    aliases = {
        "flatgeobuf": "fgb",
        "geotiff": "cog",
        "tif": "cog",
        "tiff": "cog",
        ".fgb": "fgb",
        ".pmtiles": "pmtiles",
        ".geojson": "geojson",
        ".ndgeojson": "ndgeojson",
        ".csv": "csv",
        ".tif": "cog",
        ".tiff": "cog",
    }
    return aliases.get(normalized, normalized)
