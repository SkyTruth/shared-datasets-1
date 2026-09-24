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
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import parse_qs, quote, unquote, urlsplit

from services.feature_preview_service import run as feature_preview_run
from services.http_base import (
    NO_STORE,
    Response,
    api_headers,
    authenticated_user_email,
    email_domain_allowed,
    json_response,
    send_handler_response,
    split_gs_uri,
)


DEFAULT_BUCKET = "skytruth-shared-datasets-1"
DEFAULT_SITE_PREFIX = "_catalog/web"
DEFAULT_PUBLIC_ARTIFACTS_BASE_URL = "https://tiles.skytruth.org/artifacts"
DEFAULT_CATALOG_CACHE_TTL_SECONDS = 60.0
DEFAULT_SIGNED_URL_TTL_SECONDS = 900
DEFAULT_ALLOWED_EMAIL_DOMAINS = ("skytruth.org",)
NO_CACHE = "no-cache, max-age=0, must-revalidate"
ACCESS_TIERS = {"public", "private", "internal"}
RESTRICTED_ACCESS_TIERS = {"private", "internal"}
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FIELD_SAFE_LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:_[a-z0-9]{2,8})*$")
LOCALIZED_METADATA_RE = re.compile(r"\.metadata(?:\.(?P<locale>[a-z]{2,3}(?:_[a-z0-9]{2,8})*))?\.ndjson\.gz$")
ROOT_STATIC_FILES = {"index.html", "styles.css", "app.js", "map-preview.js", "release-reference.js", "catalog.json"}


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
    def sign(self, gs_uri: str, expires_at: dt.datetime, *, generation: str | None = None) -> str:
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


class LocalCatalogWebStore:
    def __init__(self, root: Path | str, *, fallback: ObjectStore | None = None) -> None:
        self._root = Path(root).resolve()
        self._fallback = fallback

    def read_static(self, object_name: str) -> StaticObject:
        target = (self._root / object_name).resolve()
        try:
            target.relative_to(self._root)
        except ValueError as exc:
            raise StaticObjectNotFound(object_name) from exc
        if not target.is_file():
            if self._fallback is not None:
                return self._fallback.read_static(object_name)
            raise StaticObjectNotFound(object_name)
        return StaticObject(
            body=target.read_bytes(),
            content_type=content_type_for_name(object_name),
            cache_control=NO_CACHE,
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

    def sign(self, gs_uri: str, expires_at: dt.datetime, *, generation: str | None = None) -> str:
        if generation is not None:
            generation = artifact_generation(generation)
        bucket_name, object_name = split_gs_uri(gs_uri)
        if bucket_name != self._bucket_name:
            raise ValueError(f"PMTiles object must be in gs://{self._bucket_name}/")
        blob = self.client.bucket(bucket_name).blob(object_name)
        query_parameters = {"generation": generation} if generation else None
        credentials = self._credentials or default_credentials()
        signing_email = self._service_account_email or getattr(credentials, "service_account_email", None)
        if not signing_email:
            raise ValueError("CATALOG_VIEWER_SIGNING_SERVICE_ACCOUNT is required for IAM-based URL signing")

        if credentials_support_direct_signing(credentials):
            return blob.generate_signed_url(
                version="v4",
                expiration=expires_at,
                method="GET",
                query_parameters=query_parameters,
                credentials=credentials,
            )

        request = google_auth_request()
        credentials.refresh(request)
        return blob.generate_signed_url(
            version="v4",
            expiration=expires_at,
            method="GET",
            query_parameters=query_parameters,
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

    def sign(self, gs_uri: str, expires_at: dt.datetime, *, generation: str | None = None) -> str:
        if generation is not None:
            generation = artifact_generation(generation)
        bucket_name, object_name = split_gs_uri(gs_uri)
        if bucket_name != self._bucket_name:
            raise ValueError(f"CDN metadata object must be in gs://{self._bucket_name}/")
        object_path = quote(object_name, safe="/")
        url = f"{self._base_url}{object_path}"
        expires = int(expires_at.timestamp())
        generation_query = f"generation={generation}&" if generation else ""
        unsigned_url = f"{url}?{generation_query}Expires={expires}&KeyName={quote(self._key_name, safe='')}"
        digest = hmac.new(self._key, unsigned_url.encode("utf-8"), hashlib.sha1).digest()
        signature = base64.urlsafe_b64encode(digest).decode("ascii")
        # Cloud CDN expects raw base64url signature padding; %3D padding is rejected.
        return f"{unsigned_url}&Signature={signature}"


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
    feature_require_iap: bool = True,
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
            feature_require_iap=feature_require_iap,
        )
    if request_path == "/api/pmtiles/signed-url":
        return handle_signed_url(
            method,
            path,
            headers,
            catalog_cache=catalog_cache,
            object_store=object_store,
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
    feature_require_iap: bool,
) -> Response:
    if feature_require_iap and method != "OPTIONS":
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
        require_iap=feature_require_iap,
        max_ids=feature_max_ids,
        max_fields=feature_max_fields,
        max_response_bytes=feature_max_response_bytes,
    )


@dataclass(frozen=True)
class SelectedArtifact:
    uri: str
    release: str | None
    generation: str | None

    def identity(self) -> dict[str, str | None]:
        return {"gs_uri": self.uri, "resolved_release": self.release, "generation": self.generation}


def artifact_generation(value: Any) -> str:
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value) or int(value) > 2**64 - 1:
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "artifact has no valid exact generation")
    return value


def request_parameter(path: str, key: str, default: str = "") -> str:
    values = parse_qs(urlsplit(path).query, keep_blank_values=True).get(key)
    if values is None:
        return default
    if len(values) != 1 or not values[0]:
        raise DownloadResolutionError(HTTPStatus.BAD_REQUEST, f"invalid {key} parameter")
    return values[0]


def resolve_artifact(asset, format_name, version, *, locale, object_store) -> SelectedArtifact:
    if format_name not in {"pmtiles", "fgb", "metadata", "schema"}:
        raise DownloadResolutionError(HTTPStatus.BAD_REQUEST, "format must be fgb, metadata, or schema")
    if format_name == "fgb" and asset.get("canonical_format") != "fgb":
        raise DownloadResolutionError(HTTPStatus.BAD_REQUEST, "asset does not publish canonical FGB")
    if format_name == "pmtiles" and not asset_has_pmtiles(asset):
        raise DownloadResolutionError(HTTPStatus.BAD_REQUEST, "asset does not publish PMTiles")
    if version != "latest":
        try:
            if not DATE_RE.fullmatch(version):
                raise ValueError()
            dt.date.fromisoformat(version)
        except ValueError as exc:
            raise DownloadResolutionError(HTTPStatus.BAD_REQUEST, "version must be latest or YYYY-MM-DD") from exc
    locale = normalize_metadata_locale(locale)
    preferred = str(asset.get("pmtiles_path" if format_name == "pmtiles" else "canonical_path") or "")
    index = read_release_index(object_store, str(asset.get("slug") or ""))
    if index is None:
        if version == "latest" and format_name in {"pmtiles", "fgb"}:
            return SelectedArtifact(preferred, None, None)
        raise DownloadResolutionError(HTTPStatus.NOT_FOUND, "release index was not found")
    releases = index.get("releases")
    if not isinstance(releases, list):
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "release index releases field is invalid")
    by_date = {}
    for release in releases:
        if not isinstance(release, dict) or not isinstance(release.get("files"), list):
            raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "invalid release entry")
        date = release.get("date")
        try:
            if not isinstance(date, str) or not DATE_RE.fullmatch(date) or date in by_date:
                raise ValueError()
            dt.date.fromisoformat(date)
        except ValueError as exc:
            raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "invalid or duplicate release date") from exc
        by_date[date] = release
    latest = index.get("latest_release")
    if not isinstance(latest, dict) or latest.get("date") not in by_date:
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "release index latest pointer is invalid")
    date = latest["date"] if version == "latest" else version
    release = by_date.get(date)
    if release is None:
        raise DownloadResolutionError(HTTPStatus.NOT_FOUND, "requested release version was not found")
    files = release["files"]
    if any(not isinstance(f, dict) for f in files):
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "release files field is invalid")
    candidates = [f for f in files if f.get("format") == format_name or f.get("role") == format_name]
    if format_name == "metadata":
        matches = []
        for language in ([locale, ""] if locale else [""]):
            suffix = f".metadata.{language}.ndjson.gz" if language else ".metadata.ndjson.gz"
            matches = [f for f in candidates if str(f.get("path") or "").endswith(suffix)
                       and normalize_metadata_locale(str(f.get("locale") or "")) in {"", language}]
            if matches:
                break
        candidates = matches
    elif format_name == "schema":
        candidates = [f for f in candidates if str(f.get("path") or "").endswith(".schema.json")]
    else:
        matches = [f for f in candidates if basename(str(f.get("path") or "")) == basename(preferred)]
        candidates = matches or candidates
    if not candidates:
        raise DownloadResolutionError(HTTPStatus.NOT_FOUND, f"release does not include {format_name}")
    if len(candidates) != 1:
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, f"ambiguous release {format_name} files")
    file = candidates[0]
    uri = str(file.get("path") or "")
    root = re.split(r"/(?:latest|releases)/", str(asset.get("canonical_path") or preferred))[0]
    expected_prefix = f"{root}/releases/{date}/"
    if not uri.startswith(expected_prefix) or "/" in uri[len(expected_prefix):] or any(p in {".", "..", ""} for p in uri[5:].split("/")):
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "artifact is outside the catalog asset release")
    suffixes = {"fgb": ".fgb", "pmtiles": ".pmtiles", "schema": ".schema.json", "metadata": ".ndjson.gz"}
    if not uri.endswith(suffixes[format_name]):
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "artifact format/path mismatch")
    generation = artifact_generation(file.get("generation"))
    if "size" in file and (type(file["size"]) is not int or file["size"] < 0):
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "invalid artifact size")
    if "sha256" in file and (not isinstance(file["sha256"], str) or not re.fullmatch(r"[a-fA-F0-9]{64}", file["sha256"])):
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "invalid artifact checksum")
    return SelectedArtifact(uri, date, generation)


def handle_signed_url(method, path, headers, *, catalog_cache, object_store, signer, bucket_name,
                      signed_url_ttl_seconds, allowed_email_domains, now):
    return handle_artifact_url(method, path, headers, catalog_cache=catalog_cache, object_store=object_store,
                              signer=signer, bucket_name=bucket_name, signed_url_ttl_seconds=signed_url_ttl_seconds,
                              allowed_email_domains=allowed_email_domains, now=now, pmtiles=True)


def handle_download_url(method, path, headers, *, catalog_cache, object_store, signer, bucket_name,
                        signed_url_ttl_seconds, metadata_cdn_signer, metadata_cdn_ttl_seconds,
                        allowed_email_domains, now):
    return handle_artifact_url(method, path, headers, catalog_cache=catalog_cache, object_store=object_store,
                              signer=signer, bucket_name=bucket_name, signed_url_ttl_seconds=signed_url_ttl_seconds,
                              allowed_email_domains=allowed_email_domains, now=now,
                              metadata_cdn_signer=metadata_cdn_signer, metadata_cdn_ttl_seconds=metadata_cdn_ttl_seconds)


def handle_artifact_url(method, path, headers, *, catalog_cache, object_store, signer, bucket_name,
                        signed_url_ttl_seconds, allowed_email_domains, now, pmtiles=False,
                        metadata_cdn_signer=None, metadata_cdn_ttl_seconds=None):
    if method == "OPTIONS":
        return Response(HTTPStatus.NO_CONTENT, api_headers())
    if method not in {"GET", "HEAD"}:
        return json_response(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"})
    try:
        allowed = {"slug", "version", "generation"} if pmtiles else {"slug", "version", "generation", "format", "locale"}
        if set(parse_qs(urlsplit(path).query, keep_blank_values=True)) - allowed:
            raise DownloadResolutionError(HTTPStatus.BAD_REQUEST, "unsupported artifact request parameter")
        slug = request_parameter(path, "slug")
        if not SLUG_RE.fullmatch(slug):
            raise DownloadResolutionError(HTTPStatus.BAD_REQUEST, "slug must be lowercase kebab-case")
        asset = catalog_asset(catalog_cache.get(), slug)
        if asset is None:
            raise DownloadResolutionError(HTTPStatus.NOT_FOUND, "unknown asset slug")
        format_name = "pmtiles" if pmtiles else request_parameter(path, "format", "fgb").lower()
        version = request_parameter(path, "version", "latest")
        locale = request_parameter(path, "locale") if format_name == "metadata" else ""
        expected = request_parameter(path, "generation")
        if expected:
            try:
                artifact_generation(expected)
            except DownloadResolutionError as exc:
                raise DownloadResolutionError(HTTPStatus.BAD_REQUEST, "invalid generation parameter") from exc
        access_tier = catalog_access_tier(asset)
        if access_tier in RESTRICTED_ACCESS_TIERS:
            email = authenticated_user_email(headers)
            if not email:
                return json_response(HTTPStatus.UNAUTHORIZED, {"error": "IAP identity required"})
            if not email_domain_allowed(email, allowed_email_domains):
                return json_response(HTTPStatus.FORBIDDEN, {"error": "SkyTruth IAP identity required"})
        selected = resolve_artifact(asset, format_name, version, locale=locale, object_store=object_store)
        if split_gs_uri(selected.uri)[0] != bucket_name:
            raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "catalog artifact path is outside the shared bucket")
        if expected and expected != selected.generation:
            raise DownloadResolutionError(HTTPStatus.CONFLICT, "selected artifact changed; reload the catalog and reselect")
    except CatalogUnavailable:
        return json_response(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "catalog unavailable"})
    except DownloadResolutionError as exc:
        return json_response(exc.status, {"error": exc.message})
    except ValueError:
        return json_response(HTTPStatus.BAD_GATEWAY, {"error": "catalog artifact identity is invalid"})
    expires_at = None
    if access_tier in RESTRICTED_ACCESS_TIERS:
        ttl = signed_url_ttl_seconds
        if format_name == "metadata" and metadata_cdn_signer is not None:
            signer = metadata_cdn_signer
            ttl = metadata_cdn_ttl_seconds or ttl
        expires_at = now() + dt.timedelta(seconds=ttl)
        url = signer.sign(selected.uri, expires_at, generation=selected.generation)
    else:
        url = gs_to_public_artifact_url(selected.uri, bucket_name=bucket_name) if selected.release else gs_to_https(selected.uri)
        if selected.generation:
            url += f"?generation={selected.generation}"
    payload = {**selected.identity(), "pmtiles_url" if pmtiles else "download_url": url,
               "expires_at": expires_at.replace(microsecond=0).isoformat().replace("+00:00", "Z") if expires_at else None}
    if not pmtiles:
        payload["filename"] = basename(selected.uri)
    if format_name == "metadata":
        payload.update(metadata_locale_payload(requested_locale=locale, resolved_uri=selected.uri))
    return json_response(HTTPStatus.OK, payload, include_body=method != "HEAD")


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
    if type(payload.get("schema_version")) is not int or payload.get("schema_version") != 1:
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "release index schema_version is invalid")
    release_slug = str(payload.get("asset_slug") or "")
    if release_slug != slug:
        raise DownloadResolutionError(HTTPStatus.BAD_GATEWAY, "release index asset slug does not match")
    return payload


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
        slug = normalized[len("releases/") : -len(".json")]
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


def catalog_access_tier(asset: Mapping[str, Any]) -> str:
    access_tier = str(asset.get("access_tier") or "").strip().lower()
    if access_tier not in ACCESS_TIERS:
        raise ValueError("invalid access_tier")
    return access_tier


def basename(path: str) -> str:
    return next(reversed([part for part in str(path or "").split("/") if part]), "")


def gs_to_https(uri: str) -> str:
    bucket, object_name = split_gs_uri(uri)
    return f"https://storage.googleapis.com/{bucket}/{quote(object_name)}"


def gs_to_public_artifact_url(
    uri: str,
    *,
    bucket_name: str = DEFAULT_BUCKET,
    base_url: str = DEFAULT_PUBLIC_ARTIFACTS_BASE_URL,
) -> str:
    bucket, object_name = split_gs_uri(uri)
    if bucket != bucket_name:
        raise ValueError(f"public artifact must be in gs://{bucket_name}/")
    return f"{base_url.rstrip('/')}/{quote(object_name, safe='/')}"


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


def text_response(status: int, message: str, headers: dict[str, str], *, include_body: bool = True) -> Response:
    body = b"" if not include_body else f"{message}\n".encode("utf-8")
    return Response(status, {"Content-Type": "text/plain; charset=utf-8", "Content-Length": str(len(body)), **headers}, body)


def bucket_from_env() -> str:
    return os.environ.get("SHARED_DATASETS_BUCKET", DEFAULT_BUCKET)


def site_prefix_from_env() -> str:
    return os.environ.get("SHARED_DATASETS_SITE_PREFIX", DEFAULT_SITE_PREFIX).strip("/")


def object_store_from_env(*, bucket_name: str, site_prefix: str) -> ObjectStore:
    gcs_store = GcsCatalogWebStore(bucket_name=bucket_name, site_prefix=site_prefix)
    local_web_dir = os.environ.get("CATALOG_VIEWER_LOCAL_WEB_DIR", "").strip()
    if local_web_dir:
        return LocalCatalogWebStore(local_web_dir, fallback=gcs_store)
    return gcs_store


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


def bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
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
    feature_require_iap: bool = True,
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
            send_handler_response(self, response, include_body=include_body)

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
            feature_require_iap=feature_require_iap,
        )

    return Handler


def main() -> None:
    bucket_name = bucket_from_env()
    site_prefix = site_prefix_from_env()
    object_store = object_store_from_env(bucket_name=bucket_name, site_prefix=site_prefix)
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
        feature_require_iap=bool_env("CATALOG_VIEWER_FEATURE_LOOKUP_REQUIRE_IAP", True),
    )
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), handler).serve_forever()


if __name__ == "__main__":
    main()
