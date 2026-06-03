"""IAP-protected catalog viewer with short-lived GCS PMTiles URLs."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import mimetypes
import os
import posixpath
import re
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import parse_qs, quote, unquote, urlsplit

from services.feature_preview_service import run as feature_preview_run


DEFAULT_BUCKET = "skytruth-shared-datasets-1"
DEFAULT_SITE_PREFIX = "_catalog/web"
DEFAULT_CATALOG_CACHE_TTL_SECONDS = 60.0
DEFAULT_SIGNED_URL_TTL_SECONDS = 900
DEFAULT_ALLOWED_EMAIL_DOMAINS = ("skytruth.org",)
NO_CACHE = "no-cache, max-age=0, must-revalidate"
NO_STORE = "no-store"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FIELD_SAFE_LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:_[a-z0-9]{2,8})*$")
LOCALIZED_METADATA_RE = re.compile(r"\.metadata(?:\.(?P<locale>[a-z]{2,3}(?:_[a-z0-9]{2,8})*))?\.ndjson\.gz$")
ROOT_STATIC_FILES = {"index.html", "styles.css", "app.js", "map-preview.js", "catalog.json"}


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes = b""


@dataclass(frozen=True)
class StaticObject:
    body: bytes
    content_type: str
    cache_control: str = NO_CACHE


class ObjectStore(Protocol):
    def read_static(self, object_name: str) -> StaticObject:
        ...

    def read_catalog_json(self) -> dict[str, Any]:
        ...


class UrlSigner(Protocol):
    def sign(self, gs_uri: str, expires_at: dt.datetime) -> str:
        ...


class CatalogUnavailable(RuntimeError):
    """Raised when no usable generated catalog is available."""


class StaticObjectNotFound(FileNotFoundError):
    """Raised when a static catalog web object is not found."""


class DownloadResolutionError(ValueError):
    """Raised when a requested dataset download cannot be resolved safely."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class CatalogJsonCache:
    def __init__(
        self,
        *,
        loader: Callable[[], dict[str, Any]],
        ttl_seconds: float = DEFAULT_CATALOG_CACHE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._loader = loader
        self._ttl_seconds = max(0.0, ttl_seconds)
        self._clock = clock
        self._catalog: dict[str, Any] | None = None
        self._loaded_at = 0.0

    def get(self) -> dict[str, Any]:
        now = self._clock()
        if self._catalog is not None and now - self._loaded_at < self._ttl_seconds:
            return self._catalog
        try:
            catalog = self._loader()
        except Exception as exc:
            if self._catalog is not None:
                return self._catalog
            raise CatalogUnavailable(str(exc)) from exc
        if not isinstance(catalog.get("assets"), list):
            raise CatalogUnavailable("generated catalog is missing an assets array")
        self._catalog = catalog
        self._loaded_at = now
        return catalog


class GcsCatalogWebStore:
    def __init__(self, *, bucket_name: str, site_prefix: str, client=None) -> None:
        self._bucket_name = bucket_name
        self._site_prefix = site_prefix.strip("/")
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from google.cloud import storage

            project = os.environ.get("GOOGLE_CLOUD_PROJECT")
            self._client = storage.Client(project=project) if project else storage.Client()
        return self._client

    @property
    def bucket(self):
        return self.client.bucket(self._bucket_name)

    def read_static(self, object_name: str) -> StaticObject:
        blob_name = f"_catalog/{object_name}" if object_name.startswith("releases/") else f"{self._site_prefix}/{object_name}"
        blob = self.bucket.blob(blob_name)
        try:
            blob.reload()
        except Exception as exc:
            if exc.__class__.__name__ == "NotFound":
                raise StaticObjectNotFound(object_name) from exc
            raise
        body = blob.download_as_bytes()
        return StaticObject(
            body=body,
            content_type=blob.content_type or content_type_for_name(object_name),
            cache_control=blob.cache_control or NO_CACHE,
        )

    def read_catalog_json(self) -> dict[str, Any]:
        static_object = self.read_static("catalog.json")
        return json.loads(static_object.body.decode("utf-8"))


class GcsV4UrlSigner:
    def __init__(
        self,
        *,
        bucket_name: str,
        service_account_email: str | None = None,
        client=None,
        credentials=None,
    ) -> None:
        self._bucket_name = bucket_name
        self._service_account_email = service_account_email
        self._client = client
        self._credentials = credentials

    @property
    def client(self):
        if self._client is None:
            from google.cloud import storage

            project = os.environ.get("GOOGLE_CLOUD_PROJECT")
            self._client = storage.Client(project=project) if project else storage.Client()
        return self._client

    def sign(self, gs_uri: str, expires_at: dt.datetime) -> str:
        bucket_name, object_name = split_gs_uri(gs_uri)
        if bucket_name != self._bucket_name:
            raise ValueError(f"PMTiles object must be in gs://{self._bucket_name}/")
        blob = self.client.bucket(bucket_name).blob(object_name)
        credentials = self._credentials or default_credentials()
        signing_email = self._service_account_email or getattr(credentials, "service_account_email", None)
        if not signing_email:
            raise ValueError("CATALOG_VIEWER_SIGNING_SERVICE_ACCOUNT is required for IAM-based URL signing")

        if credentials_support_direct_signing(credentials):
            return blob.generate_signed_url(
                version="v4",
                expiration=expires_at,
                method="GET",
                credentials=credentials,
            )

        request = google_auth_request()
        credentials.refresh(request)
        return blob.generate_signed_url(
            version="v4",
            expiration=expires_at,
            method="GET",
            service_account_email=signing_email,
            access_token=credentials.token,
        )


class CloudCdnSignedUrlSigner:
    def __init__(
        self,
        *,
        bucket_name: str,
        base_url: str,
        key_name: str,
        key: bytes,
    ) -> None:
        if len(key) != 16:
            raise ValueError("Cloud CDN signed URL key must decode to 16 raw bytes")
        self._bucket_name = bucket_name
        self._base_url = base_url.rstrip("/") + "/"
        self._key_name = key_name
        self._key = key

    def sign(self, gs_uri: str, expires_at: dt.datetime) -> str:
        bucket_name, object_name = split_gs_uri(gs_uri)
        if bucket_name != self._bucket_name:
            raise ValueError(f"CDN metadata object must be in gs://{self._bucket_name}/")
        object_path = quote(object_name, safe="/")
        url = f"{self._base_url}{object_path}"
        expires = int(expires_at.timestamp())
        unsigned_url = f"{url}?Expires={expires}&KeyName={quote(self._key_name, safe='')}"
        digest = hmac.new(self._key, unsigned_url.encode("utf-8"), hashlib.sha1).digest()
        signature = base64.urlsafe_b64encode(digest).decode("ascii")
        return f"{unsigned_url}&Signature={quote(signature, safe='')}"


def default_credentials():
    import google.auth

    credentials, _project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return credentials


def google_auth_request():
    from google.auth.transport.requests import Request

    return Request()


def credentials_support_direct_signing(credentials: Any) -> bool:
    try:
        from google.auth.credentials import Signing
    except Exception:
        return False
    return isinstance(credentials, Signing)


def handle_request(
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: bytes = b"",
    *,
    catalog_cache: CatalogJsonCache,
    object_store: ObjectStore,
    signer: UrlSigner,
    bucket_name: str = DEFAULT_BUCKET,
    signed_url_ttl_seconds: int = DEFAULT_SIGNED_URL_TTL_SECONDS,
    metadata_cdn_signer: UrlSigner | None = None,
    metadata_cdn_ttl_seconds: int | None = None,
    allowed_email_domains: tuple[str, ...] = DEFAULT_ALLOWED_EMAIL_DOMAINS,
    feature_release_resolver: feature_preview_run.ReleaseResolver | None = None,
    feature_index: feature_preview_run.FeatureIndex | None = None,
    feature_collection_root: str = feature_preview_run.DEFAULT_COLLECTION_ROOT,
    feature_max_ids: int = feature_preview_run.DEFAULT_MAX_IDS,
    feature_max_fields: int = feature_preview_run.DEFAULT_MAX_FIELDS,
    feature_max_response_bytes: int = feature_preview_run.DEFAULT_MAX_RESPONSE_BYTES,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> Response:
    method = method.upper()
    request_path = urlsplit(path).path
    if request_path == "/healthz":
        return text_response(HTTPStatus.OK, "ok", {"Cache-Control": NO_STORE}, include_body=method != "HEAD")
    if feature_preview_run.LOOKUP_RE.fullmatch(request_path):
        return handle_feature_lookup(
            method,
            path,
            headers,
            body,
            bucket_name=bucket_name,
            allowed_email_domains=allowed_email_domains,
            feature_release_resolver=feature_release_resolver,
            feature_index=feature_index,
            feature_collection_root=feature_collection_root,
            feature_max_ids=feature_max_ids,
            feature_max_fields=feature_max_fields,
            feature_max_response_bytes=feature_max_response_bytes,
        )
    if request_path == "/api/pmtiles/signed-url":
        return handle_signed_url(
            method,
            path,
            headers,
            catalog_cache=catalog_cache,
            signer=signer,
            bucket_name=bucket_name,
            signed_url_ttl_seconds=signed_url_ttl_seconds,
            allowed_email_domains=allowed_email_domains,
            now=now,
        )
    if request_path == "/api/download-url":
        return handle_download_url(
            method,
            path,
            headers,
            catalog_cache=catalog_cache,
            object_store=object_store,
            signer=signer,
            bucket_name=bucket_name,
            signed_url_ttl_seconds=signed_url_ttl_seconds,
            metadata_cdn_signer=metadata_cdn_signer,
            metadata_cdn_ttl_seconds=metadata_cdn_ttl_seconds,
            allowed_email_domains=allowed_email_domains,
            now=now,
        )
    return handle_static(method, request_path, object_store=object_store)


def handle_feature_lookup(
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: bytes,
    *,
    bucket_name: str,
    allowed_email_domains: tuple[str, ...],
    feature_release_resolver: feature_preview_run.ReleaseResolver | None,
    feature_index: feature_preview_run.FeatureIndex | None,
    feature_collection_root: str,
    feature_max_ids: int,
    feature_max_fields: int,
    feature_max_response_bytes: int,
) -> Response:
    if method != "OPTIONS":
        email = authenticated_user_email(headers)
        if not email:
            return json_response(HTTPStatus.UNAUTHORIZED, {"error": "IAP identity required"})
        if not email_domain_allowed(email, allowed_email_domains):
            return json_response(HTTPStatus.FORBIDDEN, {"error": "SkyTruth IAP identity required"})
    resolver = feature_release_resolver or feature_preview_run.CatalogReleaseResolver(bucket_name=bucket_name)
    index = feature_index or feature_preview_run.GcsSidecarFeatureIndex(bucket_name=bucket_name)
    return feature_preview_run.handle_request(
        method,
        path,
        headers,
        body,
        release_resolver=resolver,
        feature_index=index,
        allowed_email_domains=allowed_email_domains,
        require_iap=True,
        max_ids=feature_max_ids,
        max_fields=feature_max_fields,
        max_response_bytes=feature_max_response_bytes,
    )


def handle_signed_url(
    method: str,
    path: str,
    headers: Mapping[str, str],
    *,
    catalog_cache: CatalogJsonCache,
    signer: UrlSigner,
    bucket_name: str,
    signed_url_ttl_seconds: int,
    allowed_email_domains: tuple[str, ...],
    now: Callable[[], dt.datetime],
) -> Response:
    if method == "OPTIONS":
        return Response(HTTPStatus.NO_CONTENT, api_headers())
    if method not in {"GET", "HEAD"}:
        return json_response(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"})

    slug = first_query_value(path, "slug")
    if not slug or not SLUG_RE.fullmatch(slug):
        return json_response(HTTPStatus.BAD_REQUEST, {"error": "slug must be lowercase kebab-case"})

    try:
        asset = catalog_asset(catalog_cache.get(), slug)
    except CatalogUnavailable:
        return json_response(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "catalog unavailable"})
    if asset is None:
        return json_response(HTTPStatus.NOT_FOUND, {"error": "unknown asset slug"})
    if not asset_has_pmtiles(asset):
        return json_response(HTTPStatus.BAD_REQUEST, {"error": "asset does not publish PMTiles"})

    pmtiles_path = str(asset.get("pmtiles_path") or "")
    try:
        pmtiles_bucket, _object_name = split_gs_uri(pmtiles_path)
    except ValueError:
        return json_response(HTTPStatus.BAD_GATEWAY, {"error": "catalog PMTiles path is invalid"})
    if pmtiles_bucket != bucket_name:
        return json_response(HTTPStatus.BAD_GATEWAY, {"error": "catalog PMTiles path is outside the shared bucket"})

    access_tier = str(asset.get("access_tier") or "public").lower()
    if access_tier == "private":
        email = authenticated_user_email(headers)
        if not email:
            return json_response(HTTPStatus.UNAUTHORIZED, {"error": "IAP identity required"})
        if not email_domain_allowed(email, allowed_email_domains):
            return json_response(HTTPStatus.FORBIDDEN, {"error": "SkyTruth IAP identity required"})
        expires_at = now() + dt.timedelta(seconds=signed_url_ttl_seconds)
        payload = {
            "pmtiles_url": signer.sign(pmtiles_path, expires_at),
            "expires_at": expires_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }
        return json_response(HTTPStatus.OK, payload, include_body=method != "HEAD")

    return json_response(
        HTTPStatus.OK,
        {
            "pmtiles_url": gs_to_https(pmtiles_path),
            "expires_at": None,
        },
        include_body=method != "HEAD",
    )


def handle_download_url(
    method: str,
    path: str,
    headers: Mapping[str, str],
    *,
    catalog_cache: CatalogJsonCache,
    object_store: ObjectStore,
    signer: UrlSigner,
    bucket_name: str,
    signed_url_ttl_seconds: int,
    metadata_cdn_signer: UrlSigner | None,
    metadata_cdn_ttl_seconds: int | None,
    allowed_email_domains: tuple[str, ...],
    now: Callable[[], dt.datetime],
) -> Response:
    if method == "OPTIONS":
        return Response(HTTPStatus.NO_CONTENT, api_headers())
    if method not in {"GET", "HEAD"}:
        return json_response(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"})

    slug = first_query_value(path, "slug")
    if not slug or not SLUG_RE.fullmatch(slug):
        return json_response(HTTPStatus.BAD_REQUEST, {"error": "slug must be lowercase kebab-case"})

    format_name = (first_query_value(path, "format") or "fgb").lower()
    version = first_query_value(path, "version") or "latest"
    locale = first_query_value(path, "locale") if format_name == "metadata" else ""
    try:
        catalog = catalog_cache.get()
    except CatalogUnavailable:
        return json_response(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "catalog unavailable"})

    asset = catalog_asset(catalog, slug)
    if asset is None:
        return json_response(HTTPStatus.NOT_FOUND, {"error": "unknown asset slug"})

    try:
        gs_uri = resolve_download_gs_uri(asset, format_name, version, locale=locale, object_store=object_store)
        download_bucket, _object_name = split_gs_uri(gs_uri)
    except DownloadResolutionError as exc:
        return json_response(exc.status, {"error": exc.message})
    except ValueError:
        return json_response(HTTPStatus.BAD_GATEWAY, {"error": "catalog download path is invalid"})

    if download_bucket != bucket_name:
        return json_response(HTTPStatus.BAD_GATEWAY, {"error": "catalog download path is outside the shared bucket"})

    filename = basename(gs_uri) or f"{slug}.{format_name}"
    access_tier = str(asset.get("access_tier") or "public").lower()
    if access_tier == "private":
        email = authenticated_user_email(headers)
        if not email:
            return json_response(HTTPStatus.UNAUTHORIZED, {"error": "IAP identity required"})
        if not email_domain_allowed(email, allowed_email_domains):
            return json_response(HTTPStatus.FORBIDDEN, {"error": "SkyTruth IAP identity required"})
        private_signer = signer
        private_ttl_seconds = signed_url_ttl_seconds
        if format_name == "metadata" and metadata_cdn_signer is not None:
            private_signer = metadata_cdn_signer
            private_ttl_seconds = metadata_cdn_ttl_seconds or signed_url_ttl_seconds
        expires_at = now() + dt.timedelta(seconds=private_ttl_seconds)
        payload = {
            "download_url": private_signer.sign(gs_uri, expires_at),
            "expires_at": expires_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "filename": filename,
            "gs_uri": gs_uri,
        }
        if format_name == "metadata":
            payload.update(metadata_locale_payload(requested_locale=locale, resolved_uri=gs_uri))
        return json_response(HTTPStatus.OK, payload, include_body=method != "HEAD")

    payload = {
        "download_url": gs_to_https(gs_uri),
        "expires_at": None,
        "filename": filename,
        "gs_uri": gs_uri,
    }
    if format_name == "metadata":
        payload.update(metadata_locale_payload(requested_locale=locale, resolved_uri=gs_uri))
    return json_response(HTTPStatus.OK, payload, include_body=method != "HEAD")


def resolve_download_gs_uri(
    asset: Mapping[str, Any],
    format_name: str,
    version: str,
    *,
    locale: str = "",
    object_store: ObjectStore,
) -> str:
    if format_name == "metadata":
        return resolve_metadata_sidecar_gs_uri(asset, version, locale=locale, object_store=object_store)
    if format_name != "fgb":
        raise DownloadResolutionError(HTTPStatus.BAD_REQUEST, "format must be fgb or metadata")
    if str(asset.get("canonical_format") or "").strip() != "fgb":
        raise DownloadResolutionError(HTTPStatus.BAD_REQUEST, "asset does not publish canonical FGB")
    if version != "latest" and not DATE_RE.fullmatch(version):
        raise DownloadResolutionError(HTTPStatus.BAD_REQUEST, "version must be latest or YYYY-MM-DD")

    if version == "latest":
        gs_uri = str(asset.get("canonical_path") or "").strip()
    else:
        gs_uri = release_download_gs_uri(asset, version, object_store=object_store)
    if not gs_uri:
        raise DownloadResolutionError(HTTPStatus.NOT_FOUND, "requested release version was not found")
    if not gs_uri.lower().endswith(".fgb"):
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "catalog download path is not an FGB")
    return gs_uri


def resolve_metadata_sidecar_gs_uri(
    asset: Mapping[str, Any],
    version: str,
    *,
    locale: str = "",
    object_store: ObjectStore,
) -> str:
    if version != "latest" and not DATE_RE.fullmatch(version):
        raise DownloadResolutionError(HTTPStatus.BAD_REQUEST, "version must be latest or YYYY-MM-DD")
    normalized_locale = normalize_metadata_locale(locale)

    release_index = read_release_index(object_store, str(asset.get("slug") or ""))
    if release_index is None:
        raise DownloadResolutionError(HTTPStatus.NOT_FOUND, "release index was not found")
    if version == "latest":
        latest = release_index.get("latest_release") if isinstance(release_index.get("latest_release"), Mapping) else None
        latest_date = str(latest.get("date") or "") if latest else ""
        if not DATE_RE.fullmatch(latest_date):
            raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "release index latest_release.date is invalid")
        release = release_index_release(release_index, latest_date) or latest
    else:
        release = release_index_release(release_index, version)
    if release:
        gs_uri = release_file_for_metadata_locale(release.get("files"), normalized_locale)
        if gs_uri:
            return gs_uri

    raise DownloadResolutionError(HTTPStatus.NOT_FOUND, "release does not include a metadata sidecar")


def release_download_gs_uri(asset: Mapping[str, Any], version: str, *, object_store: ObjectStore) -> str:
    release_index = read_release_index(object_store, str(asset.get("slug") or ""))
    if release_index is None:
        raise DownloadResolutionError(HTTPStatus.NOT_FOUND, "release index was not found")
    release = release_index_release(release_index, version)
    if not release:
        return ""
    gs_uri = release_file_for_canonical_format(release.get("files"), "fgb", str(asset.get("canonical_path") or ""))
    if not gs_uri:
        raise DownloadResolutionError(HTTPStatus.NOT_FOUND, "release does not include the canonical FGB")
    return gs_uri


def release_file_for_role(files: Any, role: str, suffix: str) -> str:
    if not isinstance(files, list):
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "release files field is invalid")
    for file_entry in files:
        if not isinstance(file_entry, Mapping):
            continue
        file_path = str(file_entry.get("path") or "").strip()
        if not file_path.startswith("gs://") or not file_path.endswith(suffix):
            continue
        entry_role = str(file_entry.get("role") or "").strip()
        entry_format = str(file_entry.get("format") or "").strip()
        if entry_role == role or entry_format == role:
            return file_path
    return ""


def normalize_metadata_locale(locale: str) -> str:
    normalized = str(locale or "").strip().lower().replace("-", "_")
    if not normalized:
        return ""
    if not FIELD_SAFE_LOCALE_RE.fullmatch(normalized):
        raise DownloadResolutionError(
            HTTPStatus.BAD_REQUEST,
            "locale must be a field-safe BCP 47 code such as es, fr, pt_br, or zh_hans",
        )
    return normalized


def metadata_locale_from_uri(uri: str) -> str:
    match = LOCALIZED_METADATA_RE.search(basename(uri))
    return match.group("locale") if match and match.group("locale") else ""


def metadata_locale_payload(*, requested_locale: str, resolved_uri: str) -> dict[str, str | bool | None]:
    normalized_requested = normalize_metadata_locale(requested_locale)
    resolved_locale = metadata_locale_from_uri(resolved_uri)
    return {
        "requested_locale": normalized_requested or None,
        "resolved_locale": resolved_locale or None,
        "metadata_locale_fallback": bool(normalized_requested and normalized_requested != resolved_locale),
    }


def release_file_for_metadata_locale(files: Any, locale: str) -> str:
    if not isinstance(files, list):
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "release files field is invalid")
    normalized_locale = normalize_metadata_locale(locale)
    if normalized_locale:
        localized = release_file_for_exact_metadata_locale(files, normalized_locale)
        if localized:
            return localized
    return release_file_for_exact_metadata_locale(files, "")


def release_file_for_exact_metadata_locale(files: list[Any], locale: str) -> str:
    suffix = f".metadata.{locale}.ndjson.gz" if locale else ".metadata.ndjson.gz"
    for file_entry in files:
        if not isinstance(file_entry, Mapping):
            continue
        file_path = str(file_entry.get("path") or "").strip()
        if not file_path.startswith("gs://") or not file_path.endswith(suffix):
            continue
        entry_role = str(file_entry.get("role") or "").strip()
        entry_format = str(file_entry.get("format") or "").strip()
        if entry_role != "metadata" and entry_format != "metadata":
            continue
        declared_locale = normalize_metadata_locale(str(file_entry.get("locale") or ""))
        if locale and declared_locale and declared_locale != locale:
            continue
        if not locale and declared_locale:
            continue
        return file_path
    return ""


def read_release_index(object_store: ObjectStore, slug: str) -> Mapping[str, Any] | None:
    if not SLUG_RE.fullmatch(slug):
        return None
    try:
        static_object = object_store.read_static(f"releases/{slug}.json")
    except StaticObjectNotFound:
        return None
    try:
        payload = json.loads(static_object.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "release index is invalid") from exc
    if not isinstance(payload, Mapping):
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "release index is invalid")
    if payload.get("schema_version") != 1:
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "release index schema_version is invalid")
    release_slug = str(payload.get("asset_slug") or "")
    if release_slug != slug:
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "release index asset slug does not match")
    return payload


def release_index_release(release_index: Mapping[str, Any], version: str) -> Mapping[str, Any] | None:
    releases = release_index.get("releases") or []
    if not isinstance(releases, list):
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "release index releases field is invalid")
    for release in releases:
        if isinstance(release, Mapping) and str(release.get("date") or "") == version:
            return release
    return None


def release_file_for_canonical_format(files: Any, format_name: str, canonical_path: str) -> str:
    if not isinstance(files, list):
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "release files field is invalid")
    canonical_name = basename(canonical_path)
    if not canonical_name:
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "catalog canonical path is invalid")
    for file_entry in files:
        if not isinstance(file_entry, Mapping):
            continue
        if str(file_entry.get("format") or "").strip() != format_name:
            continue
        file_path = str(file_entry.get("path") or "").strip()
        if file_path.startswith("gs://") and basename(file_path) == canonical_name:
            return file_path
    return ""


def handle_static(method: str, request_path: str, *, object_store: ObjectStore) -> Response:
    if method not in {"GET", "HEAD"}:
        return text_response(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed", {"Cache-Control": NO_STORE})
    object_name = static_object_name(request_path)
    if object_name is None:
        return text_response(HTTPStatus.NOT_FOUND, "not found", {"Cache-Control": NO_STORE})
    try:
        static_object = object_store.read_static(object_name)
    except StaticObjectNotFound:
        return text_response(HTTPStatus.NOT_FOUND, "not found", {"Cache-Control": NO_STORE})
    headers = {
        "Content-Type": static_object.content_type,
        "Cache-Control": static_object.cache_control or NO_CACHE,
        "Content-Length": str(len(static_object.body)),
    }
    return Response(HTTPStatus.OK, headers, b"" if method == "HEAD" else static_object.body)


def static_object_name(request_path: str) -> str | None:
    if request_path in {"", "/"}:
        return "index.html"
    raw = unquote(request_path).lstrip("/")
    normalized = posixpath.normpath(raw)
    if normalized in {".", ""} or normalized.startswith("../") or "/../" in f"/{normalized}/":
        return None
    if normalized in ROOT_STATIC_FILES:
        return normalized
    if normalized.startswith("releases/") and normalized.endswith(".json"):
        slug = normalized.removeprefix("releases/").removesuffix(".json")
        return normalized if SLUG_RE.fullmatch(slug) else None
    if normalized.startswith("docs/assets/") and normalized.endswith(".md"):
        return normalized
    return None


def catalog_asset(catalog: Mapping[str, Any], slug: str) -> Mapping[str, Any] | None:
    for asset in catalog.get("assets") or []:
        if isinstance(asset, Mapping) and asset.get("slug") == slug:
            return asset
    return None


def asset_has_pmtiles(asset: Mapping[str, Any]) -> bool:
    formats = asset.get("available_formats") or []
    return bool(asset.get("pmtiles_path")) and (
        asset.get("has_pmtiles") is True or (isinstance(formats, list) and "pmtiles" in formats)
    )


def first_query_value(path: str, key: str) -> str:
    values = parse_qs(urlsplit(path).query).get(key) or []
    return values[0].strip() if values else ""


def authenticated_user_email(headers: Mapping[str, str]) -> str:
    raw = header_value(headers, "X-Goog-Authenticated-User-Email") or ""
    raw = raw.strip()
    if ":" in raw:
        raw = raw.rsplit(":", 1)[-1]
    return raw.lower()


def email_domain_allowed(email: str, allowed_domains: tuple[str, ...]) -> bool:
    if not email or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[-1].lower()
    return domain in {item.lower().lstrip("@") for item in allowed_domains if item}


def header_value(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def split_gs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"expected gs:// URI, got {uri!r}")
    rest = uri[5:]
    bucket, separator, object_name = rest.partition("/")
    if not bucket or not separator or not object_name:
        raise ValueError(f"expected gs:// object URI, got {uri!r}")
    return bucket, object_name


def basename(path: str) -> str:
    return next(reversed([part for part in str(path or "").split("/") if part]), "")


def gs_to_https(uri: str) -> str:
    bucket, object_name = split_gs_uri(uri)
    return f"https://storage.googleapis.com/{bucket}/{quote(object_name)}"


def content_type_for_name(name: str) -> str:
    suffix = os.path.splitext(name)[1].lower()
    if suffix == ".js":
        return "application/javascript"
    if suffix == ".md":
        return "text/markdown; charset=utf-8"
    if suffix == ".json":
        return "application/json; charset=utf-8"
    guessed, _encoding = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def api_headers() -> dict[str, str]:
    return {
        "Cache-Control": NO_STORE,
        "Content-Type": "application/json; charset=utf-8",
    }


def json_response(status: int, payload: Mapping[str, Any], *, include_body: bool = True) -> Response:
    body = b"" if not include_body else (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    return Response(status, {**api_headers(), "Content-Length": str(len(body))}, body)


def text_response(status: int, message: str, headers: dict[str, str], *, include_body: bool = True) -> Response:
    body = b"" if not include_body else f"{message}\n".encode("utf-8")
    return Response(status, {"Content-Type": "text/plain; charset=utf-8", "Content-Length": str(len(body)), **headers}, body)


def bucket_from_env() -> str:
    return os.environ.get("SHARED_DATASETS_BUCKET", DEFAULT_BUCKET)


def site_prefix_from_env() -> str:
    return os.environ.get("SHARED_DATASETS_SITE_PREFIX", DEFAULT_SITE_PREFIX).strip("/")


def catalog_cache_ttl_from_env() -> float:
    raw = os.environ.get("CATALOG_VIEWER_CATALOG_TTL_SECONDS", "")
    try:
        return max(0.0, float(raw)) if raw else DEFAULT_CATALOG_CACHE_TTL_SECONDS
    except ValueError:
        return DEFAULT_CATALOG_CACHE_TTL_SECONDS


def signed_url_ttl_from_env() -> int:
    raw = os.environ.get("CATALOG_VIEWER_SIGNED_URL_TTL_SECONDS", "")
    try:
        return max(1, int(raw)) if raw else DEFAULT_SIGNED_URL_TTL_SECONDS
    except ValueError:
        return DEFAULT_SIGNED_URL_TTL_SECONDS


def allowed_email_domains_from_env() -> tuple[str, ...]:
    raw = os.environ.get("CATALOG_VIEWER_ALLOWED_EMAIL_DOMAINS", "")
    if not raw:
        return DEFAULT_ALLOWED_EMAIL_DOMAINS
    return tuple(part.strip().lstrip("@") for part in raw.split(",") if part.strip())


def feature_collection_root_from_env() -> str:
    return os.environ.get("FEATURE_PREVIEW_COLLECTION_ROOT", feature_preview_run.DEFAULT_COLLECTION_ROOT)


def int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        return max(1, int(raw)) if raw else default
    except ValueError:
        return default


def metadata_cdn_signed_url_ttl_from_env(default: int) -> int:
    return int_env("CATALOG_VIEWER_METADATA_CDN_SIGNED_URL_TTL_SECONDS", default)


def metadata_cdn_signer_from_env(bucket_name: str) -> UrlSigner | None:
    base_url = os.environ.get("CATALOG_VIEWER_METADATA_CDN_BASE_URL", "").strip()
    key_name = os.environ.get("CATALOG_VIEWER_CDN_SIGNING_KEY_NAME", "").strip()
    secret_id = os.environ.get("CATALOG_VIEWER_CDN_SIGNING_SECRET_ID", "").strip()
    if not any((base_url, key_name, secret_id)):
        return None
    if not all((base_url, key_name, secret_id)):
        raise ValueError(
            "CATALOG_VIEWER_METADATA_CDN_BASE_URL, CATALOG_VIEWER_CDN_SIGNING_KEY_NAME, "
            "and CATALOG_VIEWER_CDN_SIGNING_SECRET_ID must be set together"
        )
    secret_name = secret_manager_version_name(secret_id)
    encoded_key = read_secret_manager_text(secret_name)
    return CloudCdnSignedUrlSigner(
        bucket_name=bucket_name,
        base_url=base_url,
        key_name=key_name,
        key=decode_cdn_signing_key(encoded_key),
    )


def secret_manager_version_name(secret_id: str) -> str:
    secret_id = secret_id.strip().strip("/")
    if not re.fullmatch(r"projects/[^/]+/secrets/[^/]+/versions/[^/]+", secret_id):
        raise ValueError(
            "CATALOG_VIEWER_CDN_SIGNING_SECRET_ID must be a full Secret Manager version resource "
            "like projects/{project}/secrets/{secret}/versions/{version}"
        )
    return secret_id


def read_secret_manager_text(version_name: str) -> str:
    from google.auth.transport.requests import AuthorizedSession

    session = AuthorizedSession(default_credentials())
    response = session.get(f"https://secretmanager.googleapis.com/v1/{version_name}:access", timeout=10)
    if response.status_code != 200:
        raise RuntimeError(f"unable to access Secret Manager version {version_name}: HTTP {response.status_code}")
    payload = response.json().get("payload", {})
    encoded = payload.get("data")
    if not isinstance(encoded, str) or not encoded:
        raise RuntimeError(f"Secret Manager version {version_name} returned no payload data")
    return base64.b64decode(encoded).decode("utf-8").strip()


def decode_cdn_signing_key(encoded_key: str) -> bytes:
    normalized = encoded_key.strip()
    padded = normalized + ("=" * (-len(normalized) % 4))
    try:
        key = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("Cloud CDN signing key secret must contain base64url text") from exc
    if len(key) != 16:
        raise ValueError("Cloud CDN signing key secret must decode to 16 raw bytes")
    return key


def make_handler(
    *,
    catalog_cache: CatalogJsonCache,
    object_store: ObjectStore,
    signer: UrlSigner,
    bucket_name: str,
    signed_url_ttl_seconds: int,
    metadata_cdn_signer: UrlSigner | None = None,
    metadata_cdn_ttl_seconds: int | None = None,
    allowed_email_domains: tuple[str, ...],
    feature_release_resolver: feature_preview_run.ReleaseResolver | None = None,
    feature_index: feature_preview_run.FeatureIndex | None = None,
    feature_collection_root: str = feature_preview_run.DEFAULT_COLLECTION_ROOT,
    feature_max_ids: int = feature_preview_run.DEFAULT_MAX_IDS,
    feature_max_fields: int = feature_preview_run.DEFAULT_MAX_FIELDS,
    feature_max_response_bytes: int = feature_preview_run.DEFAULT_MAX_RESPONSE_BYTES,
):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._send(handle_request_from_self("GET", self, b""))

        def do_HEAD(self) -> None:
            self._send(handle_request_from_self("HEAD", self, b""), include_body=False)

        def do_OPTIONS(self) -> None:
            self._send(handle_request_from_self("OPTIONS", self, b""))

        def do_POST(self) -> None:
            content_length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(content_length) if content_length > 0 else b""
            self._send(handle_request_from_self("POST", self, body))

        def _send(self, response: Response, *, include_body: bool = True) -> None:
            self.send_response(response.status)
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.end_headers()
            if include_body and response.body:
                self.wfile.write(response.body)

    def handle_request_from_self(method: str, handler: BaseHTTPRequestHandler, body: bytes) -> Response:
        return handle_request(
            method,
            handler.path,
            handler.headers,
            body,
            catalog_cache=catalog_cache,
            object_store=object_store,
            signer=signer,
            bucket_name=bucket_name,
            signed_url_ttl_seconds=signed_url_ttl_seconds,
            metadata_cdn_signer=metadata_cdn_signer,
            metadata_cdn_ttl_seconds=metadata_cdn_ttl_seconds,
            allowed_email_domains=allowed_email_domains,
            feature_release_resolver=feature_release_resolver,
            feature_index=feature_index,
            feature_collection_root=feature_collection_root,
            feature_max_ids=feature_max_ids,
            feature_max_fields=feature_max_fields,
            feature_max_response_bytes=feature_max_response_bytes,
        )

    return Handler


def main() -> None:
    bucket_name = bucket_from_env()
    site_prefix = site_prefix_from_env()
    object_store = GcsCatalogWebStore(bucket_name=bucket_name, site_prefix=site_prefix)
    catalog_cache = CatalogJsonCache(loader=object_store.read_catalog_json, ttl_seconds=catalog_cache_ttl_from_env())
    signed_url_ttl_seconds = signed_url_ttl_from_env()
    signer = GcsV4UrlSigner(
        bucket_name=bucket_name,
        service_account_email=os.environ.get("CATALOG_VIEWER_SIGNING_SERVICE_ACCOUNT") or None,
    )
    feature_release_resolver = feature_preview_run.CatalogReleaseResolver(
        bucket_name=bucket_name,
        ttl_seconds=catalog_cache_ttl_from_env(),
    )
    feature_index = feature_preview_run.GcsSidecarFeatureIndex(bucket_name=bucket_name)
    handler = make_handler(
        catalog_cache=catalog_cache,
        object_store=object_store,
        signer=signer,
        bucket_name=bucket_name,
        signed_url_ttl_seconds=signed_url_ttl_seconds,
        metadata_cdn_signer=metadata_cdn_signer_from_env(bucket_name),
        metadata_cdn_ttl_seconds=metadata_cdn_signed_url_ttl_from_env(signed_url_ttl_seconds),
        allowed_email_domains=allowed_email_domains_from_env(),
        feature_release_resolver=feature_release_resolver,
        feature_index=feature_index,
        feature_collection_root=feature_collection_root_from_env(),
        feature_max_ids=int_env("FEATURE_PREVIEW_MAX_IDS", feature_preview_run.DEFAULT_MAX_IDS),
        feature_max_fields=int_env("FEATURE_PREVIEW_MAX_FIELDS", feature_preview_run.DEFAULT_MAX_FIELDS),
        feature_max_response_bytes=int_env(
            "FEATURE_PREVIEW_MAX_RESPONSE_BYTES",
            feature_preview_run.DEFAULT_MAX_RESPONSE_BYTES,
        ),
    )
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), handler).serve_forever()


if __name__ == "__main__":
    main()
