"""IAP-protected feature metadata lookup API backed by a rebuildable index."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit


DEFAULT_BUCKET = "skytruth-shared-datasets-1"
DEFAULT_COLLECTION_ROOT = "feature_metadata"
DEFAULT_ALLOWED_EMAIL_DOMAINS = ("skytruth.org",)
DEFAULT_MAX_IDS = 500
DEFAULT_MAX_FIELDS = 500
DEFAULT_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
DEFAULT_RELEASE_CACHE_TTL_SECONDS = 60.0
NO_STORE = "no-store"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RELEASE_RE = re.compile(r"^(latest|\d{4}-\d{2}-\d{2})$")
FEATURE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
LOAD_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
LOOKUP_RE = re.compile(
    r"^/v1/assets/(?P<asset_slug>[a-z0-9]+(?:-[a-z0-9]+)*)/releases/"
    r"(?P<release>latest|\d{4}-\d{2}-\d{2}):lookup$"
)
LATEST_LOOKUP_RE = re.compile(r"^/v1/assets/(?P<asset_slug>[a-z0-9]+(?:-[a-z0-9]+)*):lookup$")


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes = b""


@dataclass(frozen=True)
class ResolvedRelease:
    requested_release: str
    resolved_release: str
    release_index_generation: int | None = None
    schema_generation: int | None = None
    manifest_generation: int | None = None
    index_load_id: str | None = None
    schema_fields: tuple[str, ...] = ()
    schema_path: str = ""
    manifest_path: str = ""
    metadata_path: str = ""


class ReleaseResolver(Protocol):
    def resolve(self, asset_slug: str, release: str) -> ResolvedRelease:
        ...


class FeatureIndex(Protocol):
    def lookup(
        self,
        *,
        asset_slug: str,
        release: str,
        index_load_id: str,
        feature_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        ...


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = dict(details or {})


class ReleaseNotFound(ApiError):
    def __init__(self, message: str) -> None:
        super().__init__(HTTPStatus.NOT_FOUND, "not_found", message)


class IndexNotReady(ApiError):
    def __init__(self, message: str) -> None:
        super().__init__(HTTPStatus.CONFLICT, "index_not_ready", message)


class CatalogReleaseResolver:
    def __init__(
        self,
        *,
        bucket_name: str,
        client: Any = None,
        ttl_seconds: float = DEFAULT_RELEASE_CACHE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bucket_name = bucket_name
        self._client = client
        self._ttl_seconds = max(0.0, ttl_seconds)
        self._clock = clock
        self._cache: dict[str, tuple[float, dict[str, Any], int | None]] = {}

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

    def resolve(self, asset_slug: str, release: str) -> ResolvedRelease:
        payload, generation = self._load_release_index(asset_slug)
        releases = payload.get("releases") or []
        if release == "latest":
            latest = payload.get("latest_release")
            if not isinstance(latest, dict) or not latest.get("date"):
                raise ReleaseNotFound(f"{asset_slug} has no latest release")
            release_entry = self._release_entry(releases, str(latest["date"]))
            if release_entry is None and latest.get("date"):
                release_entry = latest
            if not isinstance(release_entry, dict):
                raise ReleaseNotFound(f"{asset_slug} latest release is not indexed")
            return self._resolve_release_metadata(
                asset_slug=asset_slug,
                requested_release=release,
                resolved_release=str(latest["date"]),
                release_index_generation=generation,
                release_entry=release_entry,
            )
        release_entry = self._release_entry(releases, release)
        if release_entry is None:
            raise ReleaseNotFound(f"{asset_slug} release {release} is not indexed")
        return self._resolve_release_metadata(
            asset_slug=asset_slug,
            requested_release=release,
            resolved_release=release,
            release_index_generation=generation,
            release_entry=release_entry,
        )

    def _release_entry(self, releases: Any, release: str) -> dict[str, Any] | None:
        if not isinstance(releases, list):
            return None
        for item in releases:
            if isinstance(item, dict) and item.get("date") == release:
                return item
        return None

    def _resolve_release_metadata(
        self,
        *,
        asset_slug: str,
        requested_release: str,
        resolved_release: str,
        release_index_generation: int | None,
        release_entry: Mapping[str, Any],
    ) -> ResolvedRelease:
        files = release_entry.get("files") or []
        file_by_format = {str(item.get("format") or ""): item for item in files if isinstance(item, dict)}
        metadata_entry = file_by_format.get("metadata")
        schema_entry = file_by_format.get("schema")
        manifest_entry = file_by_format.get("manifest")
        if not isinstance(metadata_entry, Mapping) or not isinstance(schema_entry, Mapping) or not isinstance(manifest_entry, Mapping):
            raise IndexNotReady(f"{asset_slug} release {resolved_release} is missing metadata bundle entries")

        schema_path = str(schema_entry.get("path") or "")
        manifest_path = str(manifest_entry.get("path") or "")
        metadata_path = str(metadata_entry.get("path") or "")
        schema_payload = self._load_gcs_json(schema_path, expected_generation=schema_entry.get("generation"))
        manifest_payload = self._load_gcs_json(manifest_path, expected_generation=manifest_entry.get("generation"))
        schema_fields = schema_field_names(schema_payload, asset_slug=asset_slug, release=resolved_release)
        validate_manifest_bundle(
            manifest_payload,
            asset_slug=asset_slug,
            release=resolved_release,
            metadata_entry=metadata_entry,
            schema_entry=schema_entry,
            manifest_entry=manifest_entry,
        )
        index_load = self._successful_index_load(
            asset_slug=asset_slug,
            release=resolved_release,
            metadata_entry=metadata_entry,
            schema_entry=schema_entry,
            manifest_entry=manifest_entry,
        )
        if index_load is None:
            raise IndexNotReady(f"{asset_slug} release {resolved_release} metadata index is not ready")
        index_load_id = str(index_load.get("load_id") or "")
        if not LOAD_ID_RE.fullmatch(index_load_id):
            raise IndexNotReady(f"{asset_slug} release {resolved_release} metadata index load ID is invalid")
        return ResolvedRelease(
            requested_release=requested_release,
            resolved_release=resolved_release,
            release_index_generation=release_index_generation,
            schema_generation=as_int(schema_entry.get("generation")),
            manifest_generation=as_int(manifest_entry.get("generation")),
            index_load_id=index_load_id,
            schema_fields=tuple(schema_fields),
            schema_path=schema_path,
            manifest_path=manifest_path,
            metadata_path=metadata_path,
        )

    def _load_gcs_json(self, uri: str, *, expected_generation: Any = None) -> dict[str, Any]:
        bucket_name, object_name = split_gs_uri(uri)
        if bucket_name != self._bucket_name:
            raise IndexNotReady(f"metadata bundle object is outside configured bucket: {uri}")
        blob = self.bucket.blob(object_name)
        try:
            blob.reload()
        except Exception as exc:
            if exc.__class__.__name__ == "NotFound":
                raise IndexNotReady(f"metadata bundle object is missing: {uri}") from exc
            raise
        expected = as_int(expected_generation)
        actual = as_int(getattr(blob, "generation", None))
        if expected is not None and actual != expected:
            raise IndexNotReady(f"metadata bundle object generation changed: {uri}")
        payload = json.loads(blob.download_as_text())
        if not isinstance(payload, dict):
            raise IndexNotReady(f"metadata bundle JSON is not an object: {uri}")
        return payload

    def _successful_index_load(
        self,
        *,
        asset_slug: str,
        release: str,
        metadata_entry: Mapping[str, Any],
        schema_entry: Mapping[str, Any],
        manifest_entry: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        prefix = f"{asset_root_from_release_entry(release_entry_path=metadata_entry.get('path'), release=release)}/index-loads/{release}/"
        newest: dict[str, Any] | None = None
        for blob in self.bucket.list_blobs(prefix=prefix):
            if not blob.name.endswith(".json"):
                continue
            try:
                blob.reload()
                record = json.loads(blob.download_as_text())
            except Exception:
                continue
            if not successful_index_load_matches(
                record,
                asset_slug=asset_slug,
                release=release,
                metadata_entry=metadata_entry,
                schema_entry=schema_entry,
                manifest_entry=manifest_entry,
            ):
                continue
            if newest is None or str(record.get("completed_at") or "") >= str(newest.get("completed_at") or ""):
                newest = record
        return newest

    def _load_release_index(self, asset_slug: str) -> tuple[dict[str, Any], int | None]:
        cached = self._cache.get(asset_slug)
        now = self._clock()
        if cached and now - cached[0] < self._ttl_seconds:
            return cached[1], cached[2]
        blob = self.bucket.blob(f"_catalog/releases/{asset_slug}.json")
        try:
            blob.reload()
        except Exception as exc:
            if exc.__class__.__name__ == "NotFound":
                raise ReleaseNotFound(f"{asset_slug} is not indexed") from exc
            raise
        payload = json.loads(blob.download_as_text())
        generation = int(blob.generation) if getattr(blob, "generation", None) is not None else None
        self._cache[asset_slug] = (now, payload, generation)
        return payload, generation


class FirestoreFeatureIndex:
    def __init__(
        self,
        *,
        collection_root: str = DEFAULT_COLLECTION_ROOT,
        client: Any = None,
    ) -> None:
        self._collection_root = collection_root
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from google.cloud import firestore

            project = os.environ.get("GOOGLE_CLOUD_PROJECT")
            self._client = firestore.Client(project=project) if project else firestore.Client()
        return self._client

    def lookup(
        self,
        *,
        asset_slug: str,
        release: str,
        index_load_id: str,
        feature_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        refs = [
            self.client.collection(self._collection_root)
            .document(asset_slug)
            .collection("releases")
            .document(release)
            .collection("loads")
            .document(index_load_id)
            .collection("features")
            .document(feature_id)
            for feature_id in feature_ids
        ]
        try:
            snapshots = self.client.get_all(refs)
        except Exception as exc:
            raise ApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "index_unavailable",
                "feature metadata index lookup failed",
            ) from exc
        found: dict[str, dict[str, Any]] = {}
        for snapshot in snapshots:
            if not getattr(snapshot, "exists", False):
                continue
            payload = snapshot.to_dict() or {}
            feature_id = str(payload.get("feature_id") or getattr(snapshot.reference, "id", ""))
            if feature_id:
                found[feature_id] = payload
        return found


def handle_request(
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: bytes = b"",
    *,
    release_resolver: ReleaseResolver,
    feature_index: FeatureIndex,
    allowed_email_domains: tuple[str, ...] = DEFAULT_ALLOWED_EMAIL_DOMAINS,
    require_iap: bool = True,
    max_ids: int = DEFAULT_MAX_IDS,
    max_fields: int = DEFAULT_MAX_FIELDS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> Response:
    method = method.upper()
    request_path = urlsplit(path).path
    if request_path == "/healthz":
        return json_response(HTTPStatus.OK, {"status": "ok"}, {"Cache-Control": NO_STORE})
    try:
        match = LOOKUP_RE.fullmatch(request_path)
        latest_match = LATEST_LOOKUP_RE.fullmatch(request_path)
        lookup_match = match or latest_match
        if lookup_match and method == "OPTIONS":
            return Response(HTTPStatus.NO_CONTENT, api_headers())
        if require_iap:
            require_authenticated_user(headers, allowed_email_domains=allowed_email_domains)
        if lookup_match:
            if method != "POST":
                raise ApiError(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed", "use POST for lookup")
            return handle_lookup(
                asset_slug=lookup_match.group("asset_slug"),
                release=match.group("release") if match else "latest",
                headers=headers,
                body=body,
                release_resolver=release_resolver,
                feature_index=feature_index,
                max_ids=max_ids,
                max_fields=max_fields,
                max_response_bytes=max_response_bytes,
            )
        raise ApiError(HTTPStatus.NOT_FOUND, "not_found", "unknown endpoint")
    except ApiError as exc:
        log_api_error(exc, method=method, path=request_path)
        return error_response(exc)
    except Exception as exc:  # noqa: BLE001 - keep API failures predictable
        error = ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, "internal", "unexpected metadata service failure")
        log_unexpected_error(exc, method=method, path=request_path)
        return error_response(error)


def handle_lookup(
    *,
    asset_slug: str,
    release: str,
    headers: Mapping[str, str],
    body: bytes,
    release_resolver: ReleaseResolver,
    feature_index: FeatureIndex,
    max_ids: int,
    max_fields: int,
    max_response_bytes: int,
) -> Response:
    request = parse_lookup_request(body, max_ids=max_ids, max_fields=max_fields)
    resolved = release_resolver.resolve(asset_slug, release)
    if not resolved.index_load_id or not LOAD_ID_RE.fullmatch(resolved.index_load_id):
        raise IndexNotReady(f"{asset_slug} release {resolved.resolved_release} metadata index load ID is invalid")
    validate_requested_fields(request["fields"], resolved.schema_fields)
    unique_ids = list(dict.fromkeys(request["ids"]))
    documents = feature_index.lookup(
        asset_slug=asset_slug,
        release=resolved.resolved_release,
        index_load_id=resolved.index_load_id,
        feature_ids=unique_ids,
    )
    items = [
        response_item(
            feature_id=feature_id,
            document=documents.get(feature_id),
            fields=request["fields"],
            include_provenance=request["include_provenance"],
        )
        for feature_id in request["ids"]
    ]
    payload = {
        "asset_slug": asset_slug,
        "requested_release": resolved.requested_release,
        "resolved_release": resolved.resolved_release,
        "release_index_generation": resolved.release_index_generation,
        "manifest_generation": resolved.manifest_generation,
        "schema_generation": resolved.schema_generation,
        "index_load_id": resolved.index_load_id,
        "items": items,
        "limits": {
            "max_ids": max_ids,
            "max_fields": max_fields,
            "max_response_bytes": max_response_bytes,
        },
        "deduplicated_lookup_count": len(unique_ids),
    }
    response_bytes = json_bytes(payload)
    if len(response_bytes) > max_response_bytes:
        raise ApiError(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            "response_too_large",
            "lookup response exceeds max_response_bytes; request fewer IDs or explicit fields",
            details={"max_response_bytes": max_response_bytes, "actual_response_bytes": len(response_bytes)},
        )
    etag = weak_etag(response_bytes)
    if headers.get("If-None-Match") == etag:
        return Response(HTTPStatus.NOT_MODIFIED, {"ETag": etag, "Cache-Control": NO_STORE})
    return Response(
        HTTPStatus.OK,
        {**api_headers(), "Cache-Control": NO_STORE, "ETag": etag},
        response_bytes,
    )


def parse_lookup_request(body: bytes, *, max_ids: int, max_fields: int) -> dict[str, Any]:
    if not body:
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_json", "request body is required")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_json", "request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_json", "request body must be a JSON object")
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids:
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_argument", "ids must be a non-empty array")
    if len(ids) > max_ids:
        raise ApiError(HTTPStatus.BAD_REQUEST, "limit_exceeded", f"ids may contain at most {max_ids} values")
    normalized_ids = []
    for index, value in enumerate(ids, start=1):
        if not isinstance(value, str) or not FEATURE_ID_RE.fullmatch(value):
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_argument", f"ids[{index}] is not a valid feature_id")
        normalized_ids.append(value)
    fields = payload.get("fields")
    normalized_fields: list[str] | None
    if fields is None:
        normalized_fields = None
    else:
        if not isinstance(fields, list):
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_argument", "fields must be an array when provided")
        if len(fields) > max_fields:
            raise ApiError(HTTPStatus.BAD_REQUEST, "limit_exceeded", f"fields may contain at most {max_fields} values")
        normalized_fields = []
        for index, value in enumerate(fields, start=1):
            if not isinstance(value, str) or not value:
                raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_argument", f"fields[{index}] is not a valid field name")
            if value not in normalized_fields:
                normalized_fields.append(value)
    include_provenance = payload.get("include_provenance", True)
    if not isinstance(include_provenance, bool):
        raise ApiError(HTTPStatus.BAD_REQUEST, "invalid_argument", "include_provenance must be boolean")
    return {"ids": normalized_ids, "fields": normalized_fields, "include_provenance": include_provenance}


def response_item(
    *,
    feature_id: str,
    document: Mapping[str, Any] | None,
    fields: list[str] | None,
    include_provenance: bool,
) -> dict[str, Any]:
    if document is None:
        return {"feature_id": feature_id, "found": False}
    properties = document.get("properties") if isinstance(document.get("properties"), Mapping) else {}
    ext_id = str(document.get("ext_id") or properties.get("ext_id") or "").strip()
    if fields is None:
        projected = dict(properties)
    else:
        projected = {field: properties.get(field) for field in fields}
    item = {
        "feature_id": feature_id,
        "found": True,
        "feature_hash": document.get("feature_hash"),
        "properties": projected,
    }
    if ext_id:
        item["ext_id"] = ext_id
    if include_provenance:
        item["provenance"] = document.get("provenance") or {}
    return item


def validate_requested_fields(fields: list[str] | None, schema_fields: tuple[str, ...]) -> None:
    if fields is None:
        return
    allowed = set(schema_fields)
    unknown = [field for field in fields if field not in allowed]
    if unknown:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            "invalid_field",
            "requested field is not present in this release schema",
            details={"fields": unknown},
        )


def schema_field_names(schema: Mapping[str, Any], *, asset_slug: str, release: str) -> tuple[str, ...]:
    if schema.get("asset_slug") != asset_slug:
        raise IndexNotReady("release schema asset_slug does not match release index")
    if schema.get("release") != release:
        raise IndexNotReady("release schema release does not match release index")
    fields = schema.get("fields")
    if not isinstance(fields, list):
        raise IndexNotReady("release schema fields must be an array")
    names: list[str] = []
    for field in fields:
        if not isinstance(field, dict):
            raise IndexNotReady("release schema field entries must be objects")
        name = field.get("name")
        projectable = field.get("projectable", True)
        if isinstance(name, str) and name and projectable is not False and name not in names:
            names.append(name)
    return tuple(names)


def validate_manifest_bundle(
    manifest: Mapping[str, Any],
    *,
    asset_slug: str,
    release: str,
    metadata_entry: Mapping[str, Any],
    schema_entry: Mapping[str, Any],
    manifest_entry: Mapping[str, Any],
) -> None:
    if manifest.get("asset_slug") != asset_slug or manifest.get("release") != release:
        raise IndexNotReady("release manifest does not match release index")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise IndexNotReady("release manifest artifacts must be an array")
    by_role = {
        str(item.get("role") or item.get("format") or ""): item
        for item in artifacts
        if isinstance(item, dict)
    }
    for role, entry in (("metadata", metadata_entry), ("schema", schema_entry), ("manifest", manifest_entry)):
        artifact = by_role.get(role)
        if not artifact:
            raise IndexNotReady(f"release manifest is missing {role} artifact")
        if artifact.get("path") != entry.get("path"):
            raise IndexNotReady(f"release manifest {role} artifact path changed")
        if role != "manifest" and as_int(artifact.get("generation")) != as_int(entry.get("generation")):
            raise IndexNotReady(f"release manifest {role} artifact generation changed")


def successful_index_load_matches(
    record: Any,
    *,
    asset_slug: str,
    release: str,
    metadata_entry: Mapping[str, Any],
    schema_entry: Mapping[str, Any],
    manifest_entry: Mapping[str, Any],
) -> bool:
    if not isinstance(record, Mapping):
        return False
    if record.get("status") != "success" or record.get("dry_run") is True:
        return False
    load_id = record.get("load_id")
    if not isinstance(load_id, str) or not LOAD_ID_RE.fullmatch(load_id):
        return False
    if record.get("asset_slug") != asset_slug or record.get("release") != release:
        return False
    return (
        record.get("sidecar_uri") == metadata_entry.get("path")
        and as_int(record.get("sidecar_generation")) == as_int(metadata_entry.get("generation"))
        and record.get("schema_uri") == schema_entry.get("path")
        and as_int(record.get("schema_generation")) == as_int(schema_entry.get("generation"))
        and record.get("manifest_uri") == manifest_entry.get("path")
        and as_int(record.get("manifest_generation")) == as_int(manifest_entry.get("generation"))
    )


def split_gs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise IndexNotReady(f"expected gs:// URI, got: {uri}")
    bucket, separator, name = uri[5:].partition("/")
    if not bucket or not separator or not name:
        raise IndexNotReady(f"expected gs:// object URI, got: {uri}")
    return bucket, name


def asset_root_from_release_entry(*, release_entry_path: Any, release: str) -> str:
    uri = str(release_entry_path or "")
    _bucket_name, object_name = split_gs_uri(uri)
    marker = f"/releases/{release}/"
    if marker not in object_name:
        raise IndexNotReady(f"metadata sidecar path is not under releases/{release}/")
    return object_name.split(marker, 1)[0]


def as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def require_authenticated_user(headers: Mapping[str, str], *, allowed_email_domains: tuple[str, ...]) -> None:
    email = authenticated_user_email(headers)
    if not email:
        raise ApiError(HTTPStatus.UNAUTHORIZED, "unauthenticated", "IAP identity required")
    domain = email.rsplit("@", 1)[-1].lower()
    if domain not in {value.lower() for value in allowed_email_domains}:
        raise ApiError(HTTPStatus.FORBIDDEN, "permission_denied", "SkyTruth IAP identity required")


def authenticated_user_email(headers: Mapping[str, str]) -> str:
    raw = headers.get("X-Goog-Authenticated-User-Email") or headers.get("x-goog-authenticated-user-email") or ""
    if ":" in raw:
        raw = raw.split(":", 1)[1]
    return raw.strip().lower()


def api_headers(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, If-None-Match",
    }
    if extra:
        headers.update(extra)
    return headers


def json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def json_response(status: int, payload: Mapping[str, Any], headers: Mapping[str, str] | None = None) -> Response:
    return Response(status, api_headers(headers), json_bytes(payload))


def error_response(error: ApiError) -> Response:
    payload = {
        "error": {
            "code": error.code,
            "message": error.message,
            "details": error.details,
        }
    }
    return json_response(error.status, payload, {"Cache-Control": NO_STORE})


def log_api_error(error: ApiError, *, method: str, path: str) -> None:
    if int(error.status) < 500:
        return
    print(
        json.dumps(
            {
                "severity": "ERROR",
                "message": "metadata service request failed",
                "method": method,
                "path": path,
                "status": int(error.status),
                "code": error.code,
                "time": dt.datetime.now(dt.UTC).isoformat(),
            },
            sort_keys=True,
        ),
        flush=True,
    )


def log_unexpected_error(error: Exception, *, method: str, path: str) -> None:
    print(
        json.dumps(
            {
                "severity": "ERROR",
                "message": "unexpected metadata service failure",
                "method": method,
                "path": path,
                "status": int(HTTPStatus.INTERNAL_SERVER_ERROR),
                "code": "internal",
                "exception_class": error.__class__.__name__,
                "time": dt.datetime.now(dt.UTC).isoformat(),
            },
            sort_keys=True,
        ),
        flush=True,
    )


def weak_etag(body: bytes) -> str:
    return 'W/"' + hashlib.sha256(body).hexdigest() + '"'


class MetadataRequestHandler(BaseHTTPRequestHandler):
    release_resolver: ReleaseResolver
    feature_index: FeatureIndex
    allowed_email_domains: tuple[str, ...]
    max_ids: int
    max_fields: int
    max_response_bytes: int

    def do_OPTIONS(self) -> None:
        self._send(handle_request("OPTIONS", self.path, self.headers, release_resolver=self.release_resolver, feature_index=self.feature_index))

    def do_GET(self) -> None:
        self._send(
            handle_request(
                "GET",
                self.path,
                self.headers,
                release_resolver=self.release_resolver,
                feature_index=self.feature_index,
                allowed_email_domains=self.allowed_email_domains,
                max_ids=self.max_ids,
                max_fields=self.max_fields,
                max_response_bytes=self.max_response_bytes,
            )
        )

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length) if length else b""
        self._send(
            handle_request(
                "POST",
                self.path,
                self.headers,
                body,
                release_resolver=self.release_resolver,
                feature_index=self.feature_index,
                allowed_email_domains=self.allowed_email_domains,
                max_ids=self.max_ids,
                max_fields=self.max_fields,
                max_response_bytes=self.max_response_bytes,
            )
        )

    def log_message(self, format: str, *args: Any) -> None:
        payload = {
            "severity": "INFO",
            "message": format % args,
            "time": dt.datetime.now(dt.UTC).isoformat(),
        }
        print(json.dumps(payload, sort_keys=True), flush=True)

    def _send(self, response: Response) -> None:
        self.send_response(int(response.status))
        for name, value in response.headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(response.body)))
        self.end_headers()
        if self.command != "HEAD" and response.body:
            self.wfile.write(response.body)


def parse_allowed_domains(value: str | None) -> tuple[str, ...]:
    domains = [part.strip().lower() for part in (value or "").split(",") if part.strip()]
    return tuple(domains or DEFAULT_ALLOWED_EMAIL_DOMAINS)


def main() -> None:
    bucket_name = os.environ.get("SHARED_DATASETS_BUCKET", DEFAULT_BUCKET)
    collection_root = os.environ.get("FEATURE_METADATA_COLLECTION_ROOT", DEFAULT_COLLECTION_ROOT)
    resolver = CatalogReleaseResolver(bucket_name=bucket_name)
    index = FirestoreFeatureIndex(collection_root=collection_root)
    MetadataRequestHandler.release_resolver = resolver
    MetadataRequestHandler.feature_index = index
    MetadataRequestHandler.allowed_email_domains = parse_allowed_domains(os.environ.get("METADATA_ALLOWED_EMAIL_DOMAINS"))
    MetadataRequestHandler.max_ids = int(os.environ.get("FEATURE_METADATA_MAX_IDS", DEFAULT_MAX_IDS))
    MetadataRequestHandler.max_fields = int(os.environ.get("FEATURE_METADATA_MAX_FIELDS", DEFAULT_MAX_FIELDS))
    MetadataRequestHandler.max_response_bytes = int(
        os.environ.get("FEATURE_METADATA_MAX_RESPONSE_BYTES", DEFAULT_MAX_RESPONSE_BYTES)
    )
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), MetadataRequestHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
