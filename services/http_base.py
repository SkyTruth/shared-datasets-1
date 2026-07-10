"""Shared HTTP plumbing for the stdlib-http Cloud Run services.

Only helpers that were duplicated verbatim (or near-verbatim with identical
behavior) across at least two services live here. Service-specific variants
(for example the metadata service's CORS ``api_headers`` or its
``Content-Length``-adding ``_send``) intentionally stay in their own modules.

Every Dockerfile under ``services/`` copies the whole ``services`` directory
into the image, so this module is available to all service containers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Any, Mapping


NO_STORE = "no-store"
FEATURE_ID_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")
LOOKUP_RE = re.compile(
    r"^/v1/assets/(?P<asset_slug>[a-z0-9]+(?:-[a-z0-9]+)*)/releases/"
    r"(?P<release>latest|\d{4}-\d{2}-\d{2}):lookup$"
)


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes = b""


def header_value(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


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


def split_gs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"expected gs:// URI, got {uri!r}")
    rest = uri[5:]
    bucket, separator, object_name = rest.partition("/")
    if not bucket or not separator or not object_name:
        raise ValueError(f"expected gs:// object URI, got {uri!r}")
    return bucket, object_name


def as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def api_headers() -> dict[str, str]:
    return {"Cache-Control": NO_STORE, "Content-Type": "application/json; charset=utf-8"}


def json_response(status: int, payload: Mapping[str, Any], *, include_body: bool = True) -> Response:
    body = b"" if not include_body else (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    return Response(status, {**api_headers(), "Content-Length": str(len(body))}, body)


def send_handler_response(
    handler: BaseHTTPRequestHandler,
    response: Response,
    *,
    include_body: bool = True,
) -> None:
    handler.send_response(response.status)
    for key, value in response.headers.items():
        handler.send_header(key, value)
    handler.end_headers()
    if include_body and response.body:
        handler.wfile.write(response.body)
