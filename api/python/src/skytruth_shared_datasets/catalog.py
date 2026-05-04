"""Static catalog resolver for SkyTruth shared datasets."""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from io import StringIO
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Literal, Mapping
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

DEFAULT_BUCKET = "skytruth-shared-datasets-1"
DEFAULT_CATALOG_GS_URI = f"gs://{DEFAULT_BUCKET}/_catalog/shared-datasets-catalog.csv"
DEFAULT_CATALOG_URL = f"https://storage.googleapis.com/{DEFAULT_BUCKET}/_catalog/shared-datasets-catalog.csv"
DEFAULT_PMTILES_CDN_BASE_URL = "https://tiles.skytruth.org/pmtiles"
RELEASE_INDEX_PREFIX = "_catalog/releases"
USER_AGENT = "skytruth-shared-datasets/0.1"
AUTHENTICATED_GCS_HINT = (
    "Use Application Default Credentials (ADC) with a runtime service account "
    "that has roles/storage.objectViewer on gs://skytruth-shared-datasets-1; "
    "do not use service account JSON keys."
)

AccessMode = Literal["public", "gcs"]
UrlStrategy = Literal["public_gcs", "cdn"]
AccessTier = Literal["public", "private"]
ACCESS_TIERS = {"public", "private"}

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
    access_tier: AccessTier
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
    raw: Mapping[str, str | None] = field(repr=False)

    @classmethod
    def from_row(cls, row: Mapping[str, str | None]) -> "CatalogAsset":
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
            access_tier=_normalize_access_tier(row.get("access_tier") or "public"),
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
    access_tier: AccessTier = "public"
    cache_path: Path | None = None

    @property
    def filename(self) -> str:
        return self.gs_uri.rstrip("/").rsplit("/", 1)[-1]

    @property
    def resolved_id(self) -> str:
        return f"{self.slug}@{self.last_updated or 'latest'}"


class Catalog:
    """In-memory shared datasets catalog."""

    def __init__(self, assets: Iterable[CatalogAsset], *, source: str = "") -> None:
        self._assets = tuple(assets)
        self._by_slug = {asset.slug: asset for asset in self._assets}
        self.source = source

    @classmethod
    def load(cls, source: str | os.PathLike[str] | None = None, *, timeout: float = 10.0) -> "Catalog":
        """Load the catalog from a URL, path, or gs:// URI.

        With no source, the public bucket catalog is used.
        """
        if source is None:
            try:
                text = _read_url(DEFAULT_CATALOG_URL, timeout=timeout)
            except Exception as exc:
                raise _catalog_load_error(DEFAULT_CATALOG_URL, exc) from exc
            return cls.from_csv_text(text, source=DEFAULT_CATALOG_URL)

        source_text = os.fspath(source)
        if source_text == "packaged":
            raise CatalogLoadError(
                "Packaged catalog snapshots are no longer shipped. "
                "Pass a local catalog path, HTTPS URL, or gs:// URI instead."
            )
        if _is_url(source_text):
            try:
                return cls.from_csv_text(_read_url(source_text, timeout=timeout), source=source_text)
            except Exception as exc:
                raise _catalog_load_error(source_text, exc) from exc
        if source_text.startswith("gs://"):
            url = gs_to_https(source_text)
            try:
                return cls.from_csv_text(_read_url(url, timeout=timeout), source=source_text)
            except Exception as exc:
                raise _catalog_load_error(source_text, exc) from exc
        try:
            return cls.from_csv_text(Path(source_text).read_text(), source=source_text)
        except OSError as exc:
            raise CatalogLoadError(f"Could not load catalog from {source_text}: {exc}") from exc

    @classmethod
    def load_gcs(
        cls,
        source: str = DEFAULT_CATALOG_GS_URI,
        *,
        client=None,
        timeout: float = 10.0,
    ) -> "Catalog":
        """Load the catalog from GCS with Application Default Credentials.

        This path requires the optional ``google-cloud-storage`` dependency
        unless a compatible client is supplied by the caller.
        """
        if not source.startswith("gs://"):
            raise CatalogLoadError(f"Authenticated catalog loading requires a gs:// URI, got: {source}")
        try:
            text = _read_gcs_text(source, client=client, timeout=timeout)
            return cls.from_csv_text(text, source=source)
        except Exception as exc:
            if isinstance(exc, CatalogLoadError):
                raise
            raise CatalogLoadError(
                f"Could not load catalog from {source} with authenticated GCS access: {exc}. "
                f"{AUTHENTICATED_GCS_HINT}"
            ) from exc

    @classmethod
    def from_csv_text(cls, text: str, *, source: str = "") -> "Catalog":
        reader = csv.DictReader(StringIO(text))
        if not reader.fieldnames:
            raise CatalogLoadError("Catalog CSV has no header row")
        assets: list[CatalogAsset] = []
        seen_slugs: set[str] = set()
        for line_number, row in enumerate(reader, start=2):
            try:
                asset = CatalogAsset.from_row(row)
            except ValueError as exc:
                raise CatalogLoadError(f"Invalid catalog row at line {line_number}: {exc}") from exc
            if asset.slug in seen_slugs:
                raise CatalogLoadError(f"Invalid catalog row at line {line_number}: duplicate asset_slug {asset.slug!r}")
            seen_slugs.add(asset.slug)
            assets.append(asset)
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
        access_tier: str | None = None,
    ) -> list[CatalogAsset]:
        requested_format = _normalize_format(format) if format else None
        requested_access_tier = _normalize_access_tier(access_tier) if access_tier else None
        matches = []
        for asset in self._assets:
            if status is not None and asset.status != status:
                continue
            if category is not None and asset.category != category:
                continue
            if requested_access_tier is not None and asset.access_tier != requested_access_tier:
                continue
            if requested_format is not None and requested_format not in asset.available_formats:
                continue
            matches.append(asset)
        return matches

    def versions(
        self,
        slug: str,
        *,
        access: AccessMode = "public",
        client=None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """Fetch the JSON release index for one asset."""
        asset = self.get(slug)
        release_index_uri = release_index_uri_for_asset(asset)
        try:
            access_mode = _normalize_access(access)
            if access_mode == "public":
                text = _read_url(gs_to_https(release_index_uri), timeout=timeout)
            else:
                text = _read_gcs_text(release_index_uri, client=client, timeout=timeout)
            payload = json.loads(text)
        except Exception as exc:
            if isinstance(exc, SharedDatasetsError):
                raise
            raise CatalogLoadError(f"Could not load release index for {slug!r}: {exc}") from exc
        if not isinstance(payload, dict):
            raise CatalogLoadError(f"Release index for {slug!r} is not a JSON object")
        if payload.get("asset_slug") not in (None, slug):
            raise CatalogLoadError(f"Release index asset_slug mismatch for {slug!r}")
        releases = payload.get("releases")
        if releases is not None and not isinstance(releases, list):
            raise CatalogLoadError(f"Release index for {slug!r} has invalid releases")
        return payload

    def resolve(
        self,
        slug: str,
        format: str | None = None,
        *,
        version: str = "latest",
        url_strategy: str | None = None,
        web_base_url: str | None = None,
        access: AccessMode = "public",
        client=None,
        timeout: float = 10.0,
    ) -> DatasetRef:
        """Resolve an asset to its canonical GCS URI and a browser-facing URL.

        ``gs_uri`` is the stable object identity. For PMTiles, ``url`` defaults
        to the shared SkyTruth PMTiles CDN. Other formats default to public
        storage.googleapis.com URLs while public access remains available. Pass
        ``url_strategy="public_gcs"`` to force public GCS URLs, or pass
        ``web_base_url`` to shape alternate CDN/application URLs.
        """
        asset = self.get(slug)
        if version != "latest":
            version_date = _parse_version(version)
            release_index = self.versions(slug, access=access, client=client, timeout=timeout)
            gs_uri, resolved_format = release_path_for_version(
                asset=asset,
                release_index=release_index,
                version=version_date,
                format=format,
            )
            if web_base_url or (url_strategy and _normalize_url_strategy(url_strategy) != "public_gcs"):
                raise ValueError("Dated releases resolve to exact GCS object URLs; CDN URL strategy is only supported for latest")
            return DatasetRef(
                slug=asset.slug,
                title=asset.title,
                format=resolved_format,
                gs_uri=gs_uri,
                url=gs_to_https(gs_uri),
                last_updated=version_date,
                access_tier=asset.access_tier,
            )
        gs_uri = asset.path_for_format(format)
        resolved_format = _normalize_format(format or asset.canonical_format)
        resolved_web_base_url = web_base_url
        if resolved_format == "pmtiles" and url_strategy != "public_gcs" and resolved_web_base_url is None:
            resolved_web_base_url = DEFAULT_PMTILES_CDN_BASE_URL
        return DatasetRef(
            slug=asset.slug,
            title=asset.title,
            format=resolved_format,
            gs_uri=gs_uri,
            url=gs_to_web_url(
                gs_uri,
                url_strategy=url_strategy,
                web_base_url=resolved_web_base_url,
                access_tier=asset.access_tier,
            ),
            last_updated=asset.last_updated,
            access_tier=asset.access_tier,
        )

    def fetch(
        self,
        slug: str,
        format: str | None = None,
        *,
        cache_dir: str | os.PathLike[str] | None = None,
        force: bool = False,
        timeout: float = 60.0,
        access: AccessMode = "public",
        client=None,
        version: str = "latest",
    ) -> DatasetRef:
        """Fetch a dataset into the local cache and return its resolved reference."""
        ref = self.resolve(
            slug,
            format,
            version=version,
            access=access,
            client=client,
            timeout=timeout,
        )
        destination = _cache_path(ref, cache_dir)
        fetched_ref = replace(ref, cache_path=destination)
        if destination.exists() and not force:
            return fetched_ref
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            access_mode = _normalize_access(access)
            if access_mode == "public":
                public_url = gs_to_https(ref.gs_uri)
                request = Request(public_url, headers={"User-Agent": USER_AGENT})
                with urlopen(request, timeout=timeout) as response, temp_path.open("wb") as file_obj:
                    shutil.copyfileobj(response, file_obj)
            else:
                _download_gcs_to_path(ref.gs_uri, temp_path, client=client, timeout=timeout)
            temp_path.replace(destination)
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            hint = f" {AUTHENTICATED_GCS_HINT}" if str(access).strip().lower() == "gcs" else ""
            raise FetchError(f"Could not download {ref.gs_uri} with {access!r} access: {exc}.{hint}") from exc
        return fetched_ref


def resolve_dataset(
    slug: str,
    format: str | None = None,
    *,
    version: str = "latest",
    timeout: float = 10.0,
    client=None,
    catalog_source: str = DEFAULT_CATALOG_GS_URI,
    web_base_url: str | None = None,
) -> DatasetRef:
    """Resolve a dataset through authenticated GCS using ADC/service accounts."""
    catalog = Catalog.load_gcs(catalog_source, client=client, timeout=timeout)
    return catalog.resolve(
        slug,
        format,
        version=version,
        access="gcs",
        client=client,
        timeout=timeout,
        web_base_url=web_base_url,
    )


def fetch_dataset(
    slug: str,
    format: str | None = None,
    *,
    version: str = "latest",
    cache_dir: str | os.PathLike[str] | None = None,
    force: bool = False,
    timeout: float = 60.0,
    client=None,
    catalog_source: str = DEFAULT_CATALOG_GS_URI,
) -> DatasetRef:
    """Fetch a dataset through authenticated GCS and return its resolved reference."""
    catalog = Catalog.load_gcs(catalog_source, client=client, timeout=timeout)
    return catalog.fetch(
        slug,
        format,
        version=version,
        cache_dir=cache_dir,
        force=force,
        timeout=timeout,
        access="gcs",
        client=client,
    )


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


def gs_to_web_url(
    uri: str,
    *,
    url_strategy: str | None = None,
    web_base_url: str | None = None,
    access_tier: str = "public",
) -> str:
    """Convert a GCS URI into a browser-facing URL.

    The default strategy returns the public ``storage.googleapis.com`` URL.
    Passing ``web_base_url`` without an explicit strategy selects the CDN-style
    strategy, mapping the object filename under ``{base}/{access_tier}/``.
    """
    strategy = _normalize_url_strategy(url_strategy or ("cdn" if web_base_url else "public_gcs"))
    if strategy == "public_gcs":
        return gs_to_https(uri)
    if web_base_url is None:
        raise ValueError("web_base_url is required when url_strategy='cdn'")
    resolved_access_tier = _normalize_access_tier(access_tier)
    filename = uri.rstrip("/").rsplit("/", 1)[-1]
    if not filename or filename == uri:
        raise ValueError(f"Expected gs:// object URI with filename, got: {uri}")
    return f"{web_base_url.rstrip('/')}/{quote(resolved_access_tier)}/{quote(filename)}"


def split_gs_uri(uri: str) -> tuple[str, str]:
    """Return ``(bucket, object_name)`` for a gs:// object URI."""
    if not uri.startswith("gs://"):
        raise ValueError(f"Expected gs:// URI, got: {uri}")
    rest = uri[5:]
    bucket, separator, object_name = rest.partition("/")
    if not bucket or not separator or not object_name:
        raise ValueError(f"Expected gs:// object URI, got: {uri}")
    return bucket, object_name


def release_index_uri_for_asset(asset: CatalogAsset) -> str:
    bucket_name, _object_name = split_gs_uri(asset.canonical_path)
    return f"gs://{bucket_name}/{RELEASE_INDEX_PREFIX}/{asset.slug}.json"


def release_path_for_version(
    *,
    asset: CatalogAsset,
    release_index: Mapping[str, Any],
    version: str,
    format: str | None,
) -> tuple[str, str]:
    resolved_format = _normalize_format(format or asset.canonical_format)
    releases = release_index.get("releases") or []
    for release in releases:
        if not isinstance(release, Mapping) or release.get("date") != version:
            continue
        files = release.get("files") or []
        for file_entry in files:
            if not isinstance(file_entry, Mapping):
                continue
            if _normalize_format(str(file_entry.get("format") or "")) != resolved_format:
                continue
            path = str(file_entry.get("path") or "")
            if not path:
                break
            split_gs_uri(path)
            return path, resolved_format
        available = ", ".join(
            sorted(
                {
                    _normalize_format(str(item.get("format") or ""))
                    for item in files
                    if isinstance(item, Mapping) and item.get("format")
                }
            )
        ) or "none"
        raise UnsupportedFormatError(
            f"{asset.slug!r} release {version} does not publish format {resolved_format!r}; "
            f"available formats: {available}"
        )
    raise UnsupportedVersionError(f"{asset.slug!r} does not have release version {version}")


def _cache_path(ref: DatasetRef, cache_dir: str | os.PathLike[str] | None) -> Path:
    root = Path(cache_dir) if cache_dir is not None else _default_cache_dir()
    last_updated = ref.last_updated or "latest"
    return root / ref.slug / ref.format / last_updated / ref.filename


def _default_cache_dir() -> Path:
    override = os.environ.get("SKYTRUTH_SHARED_DATASETS_CACHE")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "skytruth-shared-datasets"


def _read_url(url: str, *, timeout: float) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def _read_gcs_text(uri: str, *, client=None, timeout: float) -> str:
    bucket_name, object_name = split_gs_uri(uri)
    storage_client = client or _default_storage_client()
    blob = storage_client.bucket(bucket_name).blob(object_name)
    return blob.download_as_text(timeout=timeout)


def _download_gcs_to_path(uri: str, destination: Path, *, client=None, timeout: float) -> None:
    bucket_name, object_name = split_gs_uri(uri)
    storage_client = client or _default_storage_client()
    blob = storage_client.bucket(bucket_name).blob(object_name)
    blob.download_to_filename(str(destination), timeout=timeout)


def _default_storage_client():
    try:
        from google.cloud import storage
    except ImportError as exc:
        raise CatalogLoadError(
            "Authenticated GCS access requires google-cloud-storage; install "
            "skytruth-shared-datasets[gcs] or pass a compatible client. "
            f"{AUTHENTICATED_GCS_HINT}"
        ) from exc
    return storage.Client()


def _catalog_load_error(source: str, exc: BaseException) -> CatalogLoadError:
    if isinstance(exc, HTTPError) and exc.code in {403, 404}:
        return CatalogLoadError(
            f"Could not load catalog from {source}: HTTP {exc.code}. "
            "If the bucket is private, use Catalog.load_gcs() with Application Default Credentials."
        )
    return CatalogLoadError(f"Could not load catalog from {source}: {exc}")


def _is_url(value: str) -> bool:
    return value.startswith("https://") or value.startswith("http://")


def _split_semicolon(value: str | None) -> tuple[str, ...]:
    return tuple(part.strip() for part in (value or "").split(";") if part.strip())


def _required(row: Mapping[str, str | None], field: str) -> str:
    value = (row.get(field) or "").strip()
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


def _parse_version(version: str) -> str:
    try:
        parsed = dt.date.fromisoformat(version)
    except ValueError as exc:
        raise UnsupportedVersionError("version must be 'latest' or an exact YYYY-MM-DD release date") from exc
    if parsed.isoformat() != version:
        raise UnsupportedVersionError("version must be 'latest' or an exact zero-padded YYYY-MM-DD release date")
    return version


def _normalize_access(access: str) -> AccessMode:
    normalized = access.strip().lower()
    if normalized not in {"public", "gcs"}:
        raise ValueError("access must be 'public' or 'gcs'")
    return normalized  # type: ignore[return-value]


def _normalize_access_tier(access_tier: str) -> AccessTier:
    normalized = access_tier.strip().lower().replace("-", "_")
    if normalized not in ACCESS_TIERS:
        raise ValueError("access_tier must be 'public' or 'private'")
    return normalized  # type: ignore[return-value]


def _normalize_url_strategy(url_strategy: str) -> UrlStrategy:
    normalized = url_strategy.strip().lower().replace("-", "_")
    if normalized not in {"public_gcs", "cdn"}:
        raise ValueError("url_strategy must be 'public_gcs' or 'cdn'")
    return normalized  # type: ignore[return-value]
