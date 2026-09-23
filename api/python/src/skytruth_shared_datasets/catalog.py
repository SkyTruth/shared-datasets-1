"""Static catalog resolver for SkyTruth shared datasets."""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
import re
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
DEFAULT_CATALOG_CDN_BASE_URL = "https://tiles.skytruth.org"
DEFAULT_CATALOG_URL = f"{DEFAULT_CATALOG_CDN_BASE_URL}/_catalog/shared-datasets-catalog.csv"
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
AccessTier = Literal["public", "private", "internal"]
ACCESS_TIERS = {"public", "private", "internal"}
LATEST_FILE_EXTENSIONS = {
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
    lifecycle_reason: str
    lifecycle_date: str
    successor_asset_slug: str
    consumer_guidance: str
    access_tier: AccessTier
    owner: str
    update_cadence: str
    canonical_path: str
    canonical_format: str
    available_formats: tuple[str, ...]
    metadata_paths: tuple[str, ...]
    localized_name_locales: tuple[str, ...]
    localized_name_review_states: Mapping[str, str]
    last_updated: str
    source: str
    license: str
    citation: str
    notes: str
    raw: Mapping[str, str | None] = field(repr=False)

    @classmethod
    def from_row(cls, row: Mapping[str, str | None]) -> "CatalogAsset":
        slug = _required(row, "asset_slug")
        title = _required(row, "title")
        canonical_path = _required(row, "canonical_path")
        canonical_format = _normalize_format(_required(row, "canonical_format"))
        available_formats = tuple(
            _normalize_format(format_name)
            for format_name in _split_semicolon(_required(row, "available_formats"))
        )
        if canonical_format not in available_formats:
            raise ValueError("available_formats must include canonical_format")
        return cls(
            slug=slug,
            title=title,
            category=row.get("category", ""),
            subcategory=row.get("subcategory", ""),
            status=row.get("status", ""),
            lifecycle_reason=row.get("lifecycle_reason", ""),
            lifecycle_date=row.get("lifecycle_date", ""),
            successor_asset_slug=row.get("successor_asset_slug", ""),
            consumer_guidance=row.get("consumer_guidance", ""),
            access_tier=_normalize_access_tier(_required(row, "access_tier")),
            owner=row.get("owner", ""),
            update_cadence=row.get("update_cadence", ""),
            canonical_path=canonical_path,
            canonical_format=canonical_format,
            available_formats=available_formats,
            metadata_paths=_split_semicolon(row.get("metadata_paths", "")),
            localized_name_locales=_split_semicolon(row.get("localized_name_locales", "")),
            localized_name_review_states=MappingProxyType(
                _split_locale_review_states(row.get("localized_name_review_states", ""))
            ),
            last_updated=row.get("last_updated", ""),
            source=row.get("source", ""),
            license=row.get("license", ""),
            citation=row.get("citation", ""),
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
        extension = LATEST_FILE_EXTENSIONS.get(resolved_format)
        if extension is None:
            raise UnsupportedFormatError(
                f"{self.slug!r} latest path for non-canonical format {resolved_format!r} "
                "cannot be inferred from the catalog"
            )
        marker = "/latest/"
        if marker not in self.canonical_path:
            raise ValueError(f"canonical_path must contain {marker!r}: {self.canonical_path}")
        latest_root = self.canonical_path.split(marker, 1)[0] + "/latest"
        return f"{latest_root}/{self.slug}{extension}"


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
    generation: int | None = None
    sha256: str | None = None
    size: int | None = None
    release_index_generation: int | None = None

    @property
    def filename(self) -> str:
        return self.gs_uri.rstrip("/").rsplit("/", 1)[-1]

    @property
    def resolved_id(self) -> str:
        identity = f"{self.slug}@{self.last_updated or 'latest'}"
        return f"{identity}#generation={self.generation}" if self.generation is not None else identity


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
        payload, _generation = _read_release_index(self.get(slug), access=access, client=client, timeout=timeout)
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

        Latest resolution is a local, unpinned alias mapping, with no verified
        release date. Use :meth:`fetch` for exact artifact identity and bytes.
        For PMTiles, ``url`` defaults
        to the shared SkyTruth PMTiles CDN. Other formats retain
        ``storage.googleapis.com`` URL construction for diagnostics and exact
        object references, but production consumers should download through
        authenticated GCS. Pass ``url_strategy="public_gcs"`` to inspect the
        direct GCS URL, or pass ``web_base_url`` to shape alternate
        CDN/application URLs.
        """
        asset = self.get(slug)
        if version != "latest":
            version_date = _parse_version(version)
            try:
                release_index, index_generation = _read_release_index(asset, access=access, client=client, timeout=timeout)
            except _ReleaseIndexNotFound as exc:
                raise UnsupportedVersionError(f"{slug!r} has no indexed release {version_date}") from exc
            if web_base_url or (url_strategy and _normalize_url_strategy(url_strategy) != "public_gcs"):
                raise ValueError("Dated releases resolve to exact GCS object URLs; CDN URL strategy is only supported for latest")
            return _release_ref(asset, release_index, version_date, format, index_generation=index_generation)
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
            last_updated="",
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
        """Fetch one generation-pinned artifact and verify its local cache bytes."""
        asset = self.get(slug)
        access_mode = _normalize_access(access)
        if version != "latest":
            _parse_version(version)
        try:
            release_index, index_generation = _read_release_index(asset, access=access_mode, client=client, timeout=timeout)
        except _ReleaseIndexNotFound as exc:
            if version != "latest":
                raise UnsupportedVersionError(f"{slug!r} has no indexed release {version}") from exc
            release_index, index_generation = {"releases": [], "latest_release": None}, None
        if version == "latest" and not release_index["releases"] and release_index.get("latest_release") is None:
            # A missing/empty index is the explicit legacy latest-only contract.
            ref = self.resolve(slug, format)
        else:
            ref = _release_ref(asset, release_index, version, format, index_generation=index_generation)
        try:
            if ref.generation is None:
                ref = _observe_artifact(ref, access=access_mode, client=client, timeout=timeout)
            destination = _cache_path(ref, cache_dir)
            if not force:
                cached = _verified_cache(ref, destination)
                if cached is not None:
                    return cached
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
            os.close(fd)
            temp_path = Path(temp_name)
            try:
                _download_artifact(ref, temp_path, access=access_mode, client=client, timeout=timeout)
                sha256, size = _file_digest(temp_path)
                _check_integrity(ref, sha256, size)
                fetched = replace(ref, cache_path=destination, sha256=sha256, size=size)
                temp_path.replace(destination)
                _write_cache_record(fetched, destination)
                return fetched
            finally:
                temp_path.unlink(missing_ok=True)
        except Exception as exc:
            hint = f" {AUTHENTICATED_GCS_HINT}" if access_mode == "gcs" else ""
            raise FetchError(f"Could not fetch {ref.gs_uri} with {access!r} access: {exc}.{hint}") from exc


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
    """Convert a gs:// object URI to its storage.googleapis.com HTTPS URL."""
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


def gs_to_catalog_url(uri: str) -> str:
    """Convert a shared bucket _catalog object URI to the public catalog CDN URL."""
    bucket, object_name = split_gs_uri(uri)
    if bucket == DEFAULT_BUCKET and object_name.startswith("_catalog/"):
        return f"{DEFAULT_CATALOG_CDN_BASE_URL}/{quote(object_name)}"
    return gs_to_https(uri)


def gs_to_web_url(
    uri: str,
    *,
    url_strategy: str | None = None,
    web_base_url: str | None = None,
    access_tier: str = "public",
) -> str:
    """Convert a GCS URI into a browser-facing URL.

    The default strategy returns the ``storage.googleapis.com`` URL.
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


class _ReleaseIndexNotFound(CatalogLoadError):
    """The index object is definitively absent, not unreadable or malformed."""


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CatalogLoadError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]*", value):
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CatalogLoadError(f"{label} must be an integer >= {minimum}")
    return value


def _sha256(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise CatalogLoadError("sha256 must contain exactly 64 hexadecimal characters")
    return value.lower()


def _index_date(value: Any) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError("not a string")
        return _parse_version(value)
    except ValueError as exc:
        raise CatalogLoadError(f"Invalid release date: {value!r}") from exc


def _read_release_index(asset: CatalogAsset, *, access: AccessMode, client, timeout: float) -> tuple[dict[str, Any], int | None]:
    uri = release_index_uri_for_asset(asset)
    access_mode = _normalize_access(access)
    try:
        if access_mode == "public":
            request = Request(gs_to_catalog_url(uri), headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
            with urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8")
                raw_generation = response.headers.get("x-goog-generation")
        else:
            bucket, name = split_gs_uri(uri)
            blob = (client or _default_storage_client()).bucket(bucket).blob(name)
            try:
                blob.reload(timeout=timeout)
            except Exception as exc:
                if getattr(exc, "code", None) == 404:
                    raise _ReleaseIndexNotFound(f"Release index for {asset.slug!r} was not found") from exc
                raise
            raw_generation = blob.generation
            generation = _integer(raw_generation, "release index generation", minimum=1)
            text = blob.download_as_text(timeout=timeout, if_generation_match=generation)
    except _ReleaseIndexNotFound:
        raise
    except Exception as exc:
        if access_mode == "public" and getattr(exc, "code", None) == 404:
            raise _ReleaseIndexNotFound(f"Release index for {asset.slug!r} was not found") from exc
        raise CatalogLoadError(f"Could not load release index for {asset.slug!r}: {exc}") from exc
    try:
        payload = json.loads(text, object_pairs_hook=_unique_json_object)
    except (ValueError, UnicodeError) as exc:
        raise CatalogLoadError(f"Invalid release index JSON for {asset.slug!r}: {exc}") from exc
    _validate_release_index(payload, asset)
    generation = None if raw_generation is None else _integer(raw_generation, "release index generation", minimum=1)
    return payload, generation


def _validate_release_index(payload: Any, asset: CatalogAsset) -> None:
    if not isinstance(payload, dict):
        raise CatalogLoadError(f"Release index for {asset.slug!r} is not a JSON object")
    if "schema_version" in payload and (type(payload["schema_version"]) is not int or payload["schema_version"] != 1):
        raise CatalogLoadError("Unsupported release index schema_version")
    if payload.get("asset_slug", asset.slug) != asset.slug:
        raise CatalogLoadError(f"Release index asset_slug mismatch for {asset.slug!r}")
    releases = payload.get("releases")
    if not isinstance(releases, list):
        raise CatalogLoadError("Release index releases must be an array")
    dates = set()
    for release in releases:
        if not isinstance(release, dict):
            raise CatalogLoadError("Release entries must be JSON objects")
        date = _index_date(release.get("date"))
        if date in dates:
            raise CatalogLoadError(f"Duplicate release date: {date}")
        dates.add(date)
        files = release.get("files")
        if not isinstance(files, list):
            raise CatalogLoadError(f"Release {date} files must be an array")
        paths = set()
        for entry in files:
            if not isinstance(entry, dict):
                raise CatalogLoadError("Release file entries must be JSON objects")
            path = entry.get("path")
            if not isinstance(path, str) or not path:
                raise CatalogLoadError("Release file is missing an explicit path")
            _validate_artifact_path(asset, path, date)
            if path in paths:
                raise CatalogLoadError(f"Duplicate release artifact: {path}")
            paths.add(path)
            if not isinstance(entry.get("format"), str) or not entry["format"].strip():
                raise CatalogLoadError("Release artifact format must be a nonempty string")
            for key, minimum in (("generation", 1), ("size", 0)):
                if key in entry:
                    _integer(entry[key], key, minimum=minimum)
            if "sha256" in entry:
                _sha256(entry["sha256"])
    latest = payload.get("latest_release")
    if latest is not None:
        if not isinstance(latest, dict):
            raise CatalogLoadError("latest_release must be an object or null")
        latest_date = _index_date(latest.get("date"))
        selected = next((release for release in releases if release["date"] == latest_date), None)
        if selected is None:
            raise CatalogLoadError("latest_release does not name an indexed release")
        if "files" in latest and latest["files"] != selected["files"]:
            raise CatalogLoadError("latest_release files disagree with the indexed release")


def _validate_artifact_path(asset: CatalogAsset, path: str, date: str) -> None:
    try:
        bucket, name = split_gs_uri(path)
        catalog_bucket, canonical_name = split_gs_uri(asset.canonical_path)
    except ValueError as exc:
        raise CatalogLoadError(f"Invalid release artifact URI: {path!r}") from exc
    root = re.split(r"/(?:latest|releases)/", canonical_name, maxsplit=1)[0]
    if (bucket != catalog_bucket or not name.startswith(f"{root}/releases/{date}/")
            or any(part in {"", ".", ".."} for part in name.split("/"))):
        raise CatalogLoadError("Release artifact must be inside this asset's dated release root")


def _release_ref(asset: CatalogAsset, index: Mapping[str, Any], version: str, format: str | None, *, index_generation: int | None) -> DatasetRef:
    if version == "latest":
        latest = index.get("latest_release")
        if not isinstance(latest, dict):
            raise CatalogLoadError("Nonempty release history has no latest_release pointer")
        version = latest["date"]
    release = next((entry for entry in index["releases"] if entry["date"] == version), None)
    if release is None:
        raise UnsupportedVersionError(f"{asset.slug!r} does not have release version {version}")
    resolved_format = _normalize_format(format or asset.canonical_format)
    candidates = [entry for entry in release["files"] if _normalize_format(entry["format"]) == resolved_format]
    if resolved_format == "metadata":
        candidates = [entry for entry in candidates if entry["path"].endswith(".metadata.ndjson.gz") and not entry.get("locale")]
    if not candidates:
        raise UnsupportedFormatError(f"{asset.slug!r} release {version} does not publish format {resolved_format!r}")
    if resolved_format == asset.canonical_format:
        preferred_name = asset.canonical_path.rsplit("/", 1)[-1]
    elif resolved_format in LATEST_FILE_EXTENSIONS:
        preferred_name = f"{asset.slug}{LATEST_FILE_EXTENSIONS[resolved_format]}"
    else:
        preferred_name = None
    preferred = [entry for entry in candidates if entry["path"].rsplit("/", 1)[-1] == preferred_name]
    if preferred:
        candidates = preferred
    if len(candidates) != 1:
        raise CatalogLoadError(f"Ambiguous {resolved_format!r} artifacts for {asset.slug!r} release {version}")
    entry = candidates[0]
    generation = _integer(entry["generation"], "generation", minimum=1) if "generation" in entry else None
    return DatasetRef(
        slug=asset.slug, title=asset.title, format=resolved_format, gs_uri=entry["path"],
        url=_artifact_url(entry["path"], generation), last_updated=version, access_tier=asset.access_tier,
        generation=generation, sha256=_sha256(entry["sha256"]) if "sha256" in entry else None,
        size=_integer(entry["size"], "size") if "size" in entry else None, release_index_generation=index_generation,
    )


def _artifact_url(uri: str, generation: int | None) -> str:
    url = gs_to_https(uri)
    return f"{url}?generation={generation}" if generation is not None else url


def _observe_artifact(ref: DatasetRef, *, access: AccessMode, client, timeout: float) -> DatasetRef:
    if access == "public":
        request = Request(gs_to_https(ref.gs_uri), method="HEAD", headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
        with urlopen(request, timeout=timeout) as response:
            generation = _integer(response.headers.get("x-goog-generation"), "object generation", minimum=1)
            size = _integer(response.headers.get("Content-Length"), "object size")
    else:
        bucket, name = split_gs_uri(ref.gs_uri)
        blob = (client or _default_storage_client()).bucket(bucket).blob(name)
        blob.reload(timeout=timeout)
        generation = _integer(blob.generation, "object generation", minimum=1)
        size = _integer(blob.size, "object size")
    if ref.size is not None and size != ref.size:
        raise FetchError("Observed object size disagrees with release index")
    return replace(ref, generation=generation, size=size, url=_artifact_url(ref.gs_uri, generation))


def _download_artifact(ref: DatasetRef, destination: Path, *, access: AccessMode, client, timeout: float) -> None:
    assert ref.generation is not None
    if access == "public":
        request = Request(ref.url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
        with urlopen(request, timeout=timeout) as response, destination.open("wb") as handle:
            actual = response.headers.get("x-goog-generation")
            if actual is not None and _integer(actual, "response generation", minimum=1) != ref.generation:
                raise FetchError("Downloaded object generation disagrees with resolved artifact")
            shutil.copyfileobj(response, handle)
            length = response.headers.get("Content-Length")
            if length is not None and handle.tell() != _integer(length, "response size"):
                raise FetchError("Downloaded object is truncated or has an incorrect Content-Length")
    else:
        bucket, name = split_gs_uri(ref.gs_uri)
        blob = (client or _default_storage_client()).bucket(bucket).blob(name, generation=ref.generation)
        blob.download_to_filename(str(destination), timeout=timeout, if_generation_match=ref.generation, raw_download=True)


def _file_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _check_integrity(ref: DatasetRef, sha256: str, size: int) -> None:
    if ref.sha256 is not None and sha256 != ref.sha256:
        raise FetchError("Artifact SHA-256 disagrees with release index")
    if ref.size is not None and size != ref.size:
        raise FetchError("Artifact size disagrees with release index")


def _cache_path(ref: DatasetRef, cache_dir: str | os.PathLike[str] | None) -> Path:
    assert ref.generation is not None
    root = Path(cache_dir) if cache_dir is not None else _default_cache_dir()
    identity = hashlib.sha256(f"{ref.gs_uri}\n{ref.generation}".encode()).hexdigest()
    if any(part in {"", ".", ".."} for part in (ref.slug, ref.format, ref.filename)):
        raise FetchError("Artifact cache path contains an invalid component")
    return root / "v2" / quote(ref.slug, safe="") / quote(ref.format, safe="") / (ref.last_updated or "latest") / identity / quote(ref.filename, safe="")


def _cache_record_path(destination: Path) -> Path:
    return destination.with_name(destination.name + ".verified.json")


def _verified_cache(ref: DatasetRef, destination: Path) -> DatasetRef | None:
    try:
        record = json.loads(_cache_record_path(destination).read_text(), object_pairs_hook=_unique_json_object)
        if not isinstance(record, dict) or record.get("gs_uri") != ref.gs_uri:
            return None
        if _integer(record.get("generation"), "cached generation", minimum=1) != ref.generation:
            return None
        recorded_sha = _sha256(record.get("sha256"))
        recorded_size = _integer(record.get("size"), "cached size")
        sha256, size = _file_digest(destination)
        if sha256 != recorded_sha or size != recorded_size:
            return None
        _check_integrity(ref, sha256, size)
        return replace(ref, sha256=sha256, size=size, cache_path=destination)
    except (OSError, ValueError, CatalogLoadError, FetchError):
        # Local cache is disposable. It cannot establish upstream release identity.
        return None


def _write_cache_record(ref: DatasetRef, destination: Path) -> None:
    record = {"gs_uri": ref.gs_uri, "generation": ref.generation, "sha256": ref.sha256, "size": ref.size}
    fd, name = tempfile.mkstemp(prefix=".verification.", dir=destination.parent)
    temp_path = Path(name)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(record, handle, sort_keys=True)
            handle.write("\n")
        temp_path.replace(_cache_record_path(destination))
    finally:
        temp_path.unlink(missing_ok=True)


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


def _split_locale_review_states(value: str | None) -> dict[str, str]:
    states: dict[str, str] = {}
    for entry in _split_semicolon(value):
        locale, separator, review_state = entry.partition(":")
        if not separator or not locale.strip() or not review_state.strip():
            continue
        states[locale.strip()] = review_state.strip()
    return states


def _required(row: Mapping[str, str | None], field: str) -> str:
    value = (row.get(field) or "").strip()
    if not value:
        raise ValueError(f"missing required field {field!r}")
    return value


def _normalize_format(format_name: str) -> str:
    return format_name.strip().lower()


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
        allowed = "', '".join(sorted(ACCESS_TIERS))
        raise ValueError(f"access_tier must be '{allowed}'")
    return normalized  # type: ignore[return-value]


def _normalize_url_strategy(url_strategy: str) -> UrlStrategy:
    normalized = url_strategy.strip().lower().replace("-", "_")
    if normalized not in {"public_gcs", "cdn"}:
        raise ValueError("url_strategy must be 'public_gcs' or 'cdn'")
    return normalized  # type: ignore[return-value]
