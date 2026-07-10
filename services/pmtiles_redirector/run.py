"""Cloud Run service that redirects tiered public PMTiles URLs to GCS objects."""

from __future__ import annotations

import os
import re
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping
from urllib.parse import urlsplit

from skytruth_shared_datasets import (
    Catalog,
    DatasetNotFoundError,
    SharedDatasetsError,
    UnsupportedFormatError,
)

from services.http_base import Response, header_value, send_handler_response


DEFAULT_ALLOWED_ORIGINS = (
    "http://localhost:3000",
    "https://localhost:3000",
)
DEFAULT_ALLOWED_ORIGIN_REGEXES = (
    r"^https://(?:[A-Za-z0-9-]+\.)+skytruth\.org$",
)
DEFAULT_CATALOG_TTL_SECONDS = 300.0
PMTILES_PATH_RE = re.compile(
    r"^/pmtiles/(?P<access_tier>public|private|internal)/(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)\.pmtiles$"
)


class CatalogUnavailable(RuntimeError):
    """Raised when no usable catalog is available."""


class CatalogCache:
    def __init__(
        self,
        *,
        loader=None,
        ttl_seconds: float = DEFAULT_CATALOG_TTL_SECONDS,
        clock=time.monotonic,
    ) -> None:
        self._loader = loader or load_catalog_from_env
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._catalog = None
        self._loaded_at = 0.0

    def get(self):
        now = self._clock()
        if self._catalog is not None and now - self._loaded_at < self._ttl_seconds:
            return self._catalog
        try:
            catalog = self._loader()
        except Exception as exc:
            if self._catalog is not None:
                return self._catalog
            raise CatalogUnavailable(str(exc)) from exc
        self._catalog = catalog
        self._loaded_at = now
        return catalog


def load_catalog_from_env() -> Catalog:
    source = os.environ.get("SHARED_DATASETS_CATALOG_SOURCE") or None
    return Catalog.load(source)


def allowed_origins_from_env() -> tuple[str, ...]:
    raw = os.environ.get("PMTILES_ALLOWED_ORIGINS")
    if not raw:
        return DEFAULT_ALLOWED_ORIGINS
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def allowed_origin_regexes_from_env() -> tuple[str, ...]:
    raw = os.environ.get("PMTILES_ALLOWED_ORIGIN_REGEXES")
    if not raw:
        return DEFAULT_ALLOWED_ORIGIN_REGEXES
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def ttl_from_env() -> float:
    raw = os.environ.get("PMTILES_CATALOG_TTL_SECONDS")
    if not raw:
        return DEFAULT_CATALOG_TTL_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_CATALOG_TTL_SECONDS


def handle_request(
    method: str,
    path: str,
    headers: Mapping[str, str],
    *,
    catalog_cache: CatalogCache,
    allowed_origins: tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS,
    allowed_origin_regexes: tuple[str, ...] = DEFAULT_ALLOWED_ORIGIN_REGEXES,
) -> Response:
    method = method.upper()
    origin = header_value(headers, "Origin")
    cors_headers = cors_headers_for_origin(origin, allowed_origins, allowed_origin_regexes)
    request_path = urlsplit(path).path

    if method not in {"GET", "HEAD", "OPTIONS"}:
        return text_response(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed", cors_headers)

    match = PMTILES_PATH_RE.fullmatch(request_path)
    if not match:
        return text_response(HTTPStatus.NOT_FOUND, "not found", cors_headers)

    if method == "OPTIONS":
        if origin and not cors_headers:
            return text_response(HTTPStatus.FORBIDDEN, "origin not allowed", {})
        return Response(HTTPStatus.NO_CONTENT, cors_headers)

    access_tier = match.group("access_tier")
    slug = match.group("slug")
    try:
        catalog = catalog_cache.get()
        ref = catalog.resolve(slug, format="pmtiles", url_strategy="public_gcs")
    except CatalogUnavailable:
        return text_response(HTTPStatus.SERVICE_UNAVAILABLE, "catalog unavailable", cors_headers)
    except (DatasetNotFoundError, UnsupportedFormatError):
        return text_response(HTTPStatus.NOT_FOUND, "not found", cors_headers)
    except SharedDatasetsError:
        return text_response(HTTPStatus.BAD_GATEWAY, "catalog error", cors_headers)

    if access_tier != "public" or getattr(ref, "access_tier", "public") != access_tier:
        return text_response(HTTPStatus.NOT_FOUND, "not found", cors_headers)

    response_headers = {
        **cors_headers,
        "Cache-Control": "public, max-age=300",
        "Location": ref.url,
    }
    body = b"" if method == "HEAD" else f"Redirecting to {ref.url}\n".encode("utf-8")
    return Response(HTTPStatus.TEMPORARY_REDIRECT, response_headers, body)


def cors_headers_for_origin(
    origin: str | None,
    allowed_origins: tuple[str, ...],
    allowed_origin_regexes: tuple[str, ...],
) -> dict[str, str]:
    base = {
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Range",
        "Access-Control-Expose-Headers": "Accept-Ranges, Cache-Control, Content-Length, Content-Range, ETag, Location",
        "Access-Control-Max-Age": "3600",
    }
    if "*" in allowed_origins:
        return {"Access-Control-Allow-Origin": "*", **base}
    if origin and (origin in allowed_origins or _matches_origin_regex(origin, allowed_origin_regexes)):
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
            **base,
        }
    if origin:
        return {}
    return {"Access-Control-Allow-Origin": "*", **base}


def text_response(status: int, message: str, headers: dict[str, str]) -> Response:
    body = f"{message}\n".encode("utf-8")
    return Response(status, {"Content-Type": "text/plain; charset=utf-8", **headers}, body)


def _matches_origin_regex(origin: str, allowed_origin_regexes: tuple[str, ...]) -> bool:
    for pattern in allowed_origin_regexes:
        try:
            if re.fullmatch(pattern, origin):
                return True
        except re.error:
            continue
    return False


def make_handler(
    catalog_cache: CatalogCache,
    allowed_origins: tuple[str, ...],
    allowed_origin_regexes: tuple[str, ...],
):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._send(
                handle_request(
                    "GET",
                    self.path,
                    self.headers,
                    catalog_cache=catalog_cache,
                    allowed_origins=allowed_origins,
                    allowed_origin_regexes=allowed_origin_regexes,
                )
            )

        def do_HEAD(self) -> None:
            self._send(
                handle_request(
                    "HEAD",
                    self.path,
                    self.headers,
                    catalog_cache=catalog_cache,
                    allowed_origins=allowed_origins,
                    allowed_origin_regexes=allowed_origin_regexes,
                ),
                include_body=False,
            )

        def do_OPTIONS(self) -> None:
            self._send(
                handle_request(
                    "OPTIONS",
                    self.path,
                    self.headers,
                    catalog_cache=catalog_cache,
                    allowed_origins=allowed_origins,
                    allowed_origin_regexes=allowed_origin_regexes,
                )
            )

        def _send(self, response: Response, *, include_body: bool = True) -> None:
            send_handler_response(self, response, include_body=include_body)

    return Handler


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    cache = CatalogCache(ttl_seconds=ttl_from_env())
    handler = make_handler(cache, allowed_origins_from_env(), allowed_origin_regexes_from_env())
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
