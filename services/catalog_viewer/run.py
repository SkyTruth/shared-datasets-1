"""IAP-protected catalog viewer with short-lived GCS PMTiles URLs."""

from __future__ import annotations

import datetime as dt
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


DEFAULT_BUCKET = "skytruth-shared-datasets-1"
DEFAULT_SITE_PREFIX = "_catalog/web"
DEFAULT_CATALOG_CACHE_TTL_SECONDS = 60.0
DEFAULT_SIGNED_URL_TTL_SECONDS = 900
DEFAULT_ALLOWED_EMAIL_DOMAINS = ("skytruth.org",)
NO_CACHE = "no-cache, max-age=0, must-revalidate"
NO_STORE = "no-store"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
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
    *,
    catalog_cache: CatalogJsonCache,
    object_store: ObjectStore,
    signer: UrlSigner,
    bucket_name: str = DEFAULT_BUCKET,
    signed_url_ttl_seconds: int = DEFAULT_SIGNED_URL_TTL_SECONDS,
    allowed_email_domains: tuple[str, ...] = DEFAULT_ALLOWED_EMAIL_DOMAINS,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> Response:
    method = method.upper()
    request_path = urlsplit(path).path
    if request_path == "/healthz":
        return text_response(HTTPStatus.OK, "ok", {"Cache-Control": NO_STORE}, include_body=method != "HEAD")
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
    return handle_static(method, request_path, object_store=object_store)


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
    raw = header_value(headers, "X-Goog-Authenticated-User-Email") or header_value(headers, "X-Forwarded-Email") or ""
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


def make_handler(
    *,
    catalog_cache: CatalogJsonCache,
    object_store: ObjectStore,
    signer: UrlSigner,
    bucket_name: str,
    signed_url_ttl_seconds: int,
    allowed_email_domains: tuple[str, ...],
):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._send(handle_request_from_self("GET", self))

        def do_HEAD(self) -> None:
            self._send(handle_request_from_self("HEAD", self), include_body=False)

        def do_OPTIONS(self) -> None:
            self._send(handle_request_from_self("OPTIONS", self))

        def _send(self, response: Response, *, include_body: bool = True) -> None:
            self.send_response(response.status)
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.end_headers()
            if include_body and response.body:
                self.wfile.write(response.body)

    def handle_request_from_self(method: str, handler: BaseHTTPRequestHandler) -> Response:
        return handle_request(
            method,
            handler.path,
            handler.headers,
            catalog_cache=catalog_cache,
            object_store=object_store,
            signer=signer,
            bucket_name=bucket_name,
            signed_url_ttl_seconds=signed_url_ttl_seconds,
            allowed_email_domains=allowed_email_domains,
        )

    return Handler


def main() -> None:
    bucket_name = bucket_from_env()
    site_prefix = site_prefix_from_env()
    object_store = GcsCatalogWebStore(bucket_name=bucket_name, site_prefix=site_prefix)
    catalog_cache = CatalogJsonCache(loader=object_store.read_catalog_json, ttl_seconds=catalog_cache_ttl_from_env())
    signer = GcsV4UrlSigner(
        bucket_name=bucket_name,
        service_account_email=os.environ.get("CATALOG_VIEWER_SIGNING_SERVICE_ACCOUNT") or None,
    )
    handler = make_handler(
        catalog_cache=catalog_cache,
        object_store=object_store,
        signer=signer,
        bucket_name=bucket_name,
        signed_url_ttl_seconds=signed_url_ttl_from_env(),
        allowed_email_domains=allowed_email_domains_from_env(),
    )
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), handler).serve_forever()


if __name__ == "__main__":
    main()
