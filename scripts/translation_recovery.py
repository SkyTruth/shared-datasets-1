"""Read-only, current-generation proof of canonical translation maintenance.

The manifest is the publication commit point. Neither a notice receipt nor an
old complete generation proves the current release has been maintained.
"""
from __future__ import annotations

import base64
import binascii
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote

from google.api_core import exceptions as api_errors
import google_crc32c
from requests import exceptions as request_errors

from ingestion.common.translation_notices import debt_at_threshold
from scripts import catalog_csv, gcp_read_retry, operational_retry, release_feature_model as model

SCHEMA = "shared-datasets-translation-recovery-v1"
BUCKET = "skytruth-shared-datasets-1"
MAX_BYTES = 1024 * 1024
CATALOG = Path(__file__).resolve().parents[1] / "catalog/shared-datasets-catalog.csv"
FIELDS = "bucket,name,generation,size,timeCreated,updated,crc32c"
PROOF_FIELDS = frozenset({"schema", "uri", "generation", "created_at", "updated_at", "size", "crc32c", "sha256", "body_base64", "asset_slug", "release"})


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _timestamp(value):
    """Compare provider timestamps without discarding nanosecond precision."""
    if isinstance(value, datetime):
        require(value.tzinfo is not None, "translation proof timestamp must be timezone-aware")
        value = value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    require(isinstance(value, str), "translation proof timestamp is required")
    match = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:[.](\d{1,9}))?Z", value)
    require(match is not None, "translation proof timestamp must be UTC RFC3339")
    instant = datetime.strptime(match[1], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return int(instant.timestamp()) * 10**9 + int((match[2] or "").ljust(9, "0"))


def registered_assets():
    """Derive the maintained assets from the existing catalog, never log text."""
    result = {}
    for slug, row in catalog_csv.load_catalog(CATALOG).items():
        locales = tuple(filter(None, row.get("translation_locales", "").split(";")))
        if not locales:
            continue
        require(re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", slug) is not None, "invalid maintained asset slug")
        require(len(locales) == len(set(locales)) and all(re.fullmatch(r"[a-z]{2,3}(?:_[a-z0-9]{2,8})*", locale) for locale in locales), "invalid maintained locale configuration")
        canonical = row.get("canonical_path", "")
        match = re.fullmatch(re.escape(f"gs://{BUCKET}/") + r"([^?#]+)/(?:latest|releases/\d{4}-\d\d-\d\d)/" + re.escape(slug) + r"[.][^/]+", canonical)
        require(match is not None and all(part not in ("", ".", "..") for part in match[1].split("/")), "invalid maintained canonical asset path")
        result[slug] = {"root": match[1], "locales": locales}
    return result


def identity(uri):
    require(isinstance(uri, str), "translation manifest URI is required")
    matches = []
    for slug, asset in registered_assets().items():
        match = re.fullmatch(re.escape(f"gs://{BUCKET}/{asset['root']}/releases/") + r"(\d{4}-\d\d-\d\d)/" + re.escape(slug) + r"[.]manifest[.]json", uri)
        if match:
            require(date.fromisoformat(match[1]).isoformat() == match[1], "invalid canonical release date")
            matches.append((slug, match[1], asset))
    require(len(matches) == 1, "unregistered canonical translation manifest")
    return matches[0]


def _decode(value, *, maximum):
    require(isinstance(value, str) and len(value) <= 4 * ((maximum + 2) // 3), "translation proof exceeds bounded bytes")
    try:
        result = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("invalid translation proof base64") from error
    require(len(result) <= maximum and base64.b64encode(result).decode("ascii") == value, "noncanonical translation proof base64")
    return result


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate translation manifest JSON field")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"invalid translation manifest JSON number: {value}")


def _manifest(proof):
    require(isinstance(proof, dict) and set(proof) == PROOF_FIELDS and proof["schema"] == SCHEMA, "invalid translation recovery proof schema")
    slug, release, asset = identity(proof["uri"])
    require(proof["asset_slug"] == slug and proof["release"] == release, "translation proof identity differs")
    require(type(proof["generation"]) is int and proof["generation"] > 0, "invalid translation manifest generation")
    require(type(proof["size"]) is int and 0 < proof["size"] <= MAX_BYTES, "translation manifest exceeds bounded read")
    require(_timestamp(proof["updated_at"]) >= _timestamp(proof["created_at"]), "translation manifest metadata chronology differs")
    body = _decode(proof["body_base64"], maximum=MAX_BYTES)
    require(len(body) == proof["size"], "translation manifest bytes differ from metadata size")
    require(isinstance(proof["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", proof["sha256"]) is not None and hashlib.sha256(body).hexdigest() == proof["sha256"], "translation manifest bytes differ from SHA256")
    checksum = _decode(proof["crc32c"], maximum=4)
    require(len(checksum) == 4 and int.from_bytes(checksum, "big") == google_crc32c.value(body), "translation manifest bytes differ from GCS CRC32C")
    value = json.loads(body, object_pairs_hook=_object, parse_constant=_invalid_constant)
    require(isinstance(value, dict), "translation manifest must be a JSON object")
    artifacts = model.validate_release_manifest(value, expected_asset_slug=slug, expected_release=release, require_generations=True)
    model.validate_translation_bundle_manifest(value, asset["locales"])
    prefix = proof["uri"].rsplit("/", 1)[0] + "/"
    require(artifacts["manifest"]["path"] == proof["uri"], "manifest inventory names a different commit point")
    for role, artifact in artifacts.items():
        path = artifact["path"]
        require(path.startswith(prefix) and "/" not in path.removeprefix(prefix) and path.removeprefix(prefix) not in ("", ".", ".."), "translation inventory escapes the canonical release")
        if role != "manifest":
            require(type(artifact.get("generation")) is int and artifact["generation"] > 0, "translation inventory generation is required")
    return value


def validate(proof):
    _manifest(proof)
    return proof


def manifest(proof):
    return _manifest(proof)


def complete(proof):
    value = _manifest(proof)
    return not any(debt_at_threshold(counts) for counts in value["translations"]["locales"].values())


def observed_by(proof, now):
    """Future metadata cannot establish a recovery before it was observed."""
    validate(proof)
    return _timestamp(proof["created_at"]) <= _timestamp(now) and _timestamp(proof["updated_at"]) <= _timestamp(now)


def covers(proof, old_uri, old_generation, failed_at):
    """A strictly newer canonical publication may close this exact episode."""
    if not complete(proof) or proof["uri"] != old_uri or _timestamp(proof["created_at"]) <= _timestamp(failed_at):
        return False
    if old_generation is None:
        return True
    require(type(old_generation) is int and old_generation > 0, "invalid original translation manifest generation")
    # GCS generations identify versions; their numeric order is not guaranteed.
    return proof["generation"] != old_generation


def _decimal(value, label):
    require(isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value) is not None, f"invalid GCS translation manifest {label}")
    return int(value)


def _classify(error):
    return "google_transport" if isinstance(error, request_errors.ChunkedEncodingError) else gcp_read_retry.classify(error)


class Reader:
    """Only exact registered manifest GETs; there is no storage write surface."""

    def __init__(self, session):
        self.session = session

    def read(self, uri, generation=None):
        slug, release, _ = identity(uri)
        require(generation is None or type(generation) is int and generation > 0, "invalid expected translation generation")
        name = uri.removeprefix(f"gs://{BUCKET}/")
        url = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o/{quote(name, safe='')}"

        def snapshot():
            response = self.session.request("GET", url, params={"fields": FIELDS}, timeout=operational_retry.request_timeout(30))
            try:
                if response.status_code != 200:
                    raise api_errors.from_http_status(response.status_code, "Canonical translation manifest metadata read failed")
                metadata = response.json()
            finally:
                response.close()
            require(isinstance(metadata, dict) and metadata.get("bucket") == BUCKET and metadata.get("name") == name, "translation manifest metadata identity differs")
            current = _decimal(metadata.get("generation"), "generation")
            require(generation is None or current == generation, "current translation manifest generation differs from saved proof")
            size = _decimal(metadata.get("size"), "size")
            require(size <= MAX_BYTES, "translation manifest exceeds bounded read")
            response = self.session.request("GET", url, params={"generation": str(current), "alt": "media"}, timeout=operational_retry.request_timeout(30), stream=True)
            try:
                if response.status_code != 200:
                    raise api_errors.from_http_status(response.status_code, "Canonical translation manifest byte read failed")
                body = bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    operational_retry.request_timeout(30)
                    body.extend(chunk)
                    require(len(body) <= size, "translation manifest bytes exceed metadata size")
                require(len(body) == size, "translation manifest bytes are incomplete")
            finally:
                response.close()
            body = bytes(body)
            return validate({"schema": SCHEMA, "uri": uri, "generation": current, "created_at": metadata.get("timeCreated"), "updated_at": metadata.get("updated"), "size": size, "crc32c": metadata.get("crc32c"), "sha256": hashlib.sha256(body).hexdigest(), "body_base64": base64.b64encode(body).decode("ascii"), "asset_slug": slug, "release": release})

        with operational_retry.operation(profile=operational_retry.UNATTENDED):
            return operational_retry.run_read(snapshot, classify=_classify, label="canonical translation manifest read")


def read(reader, uri, generation=None):
    return (reader if isinstance(reader, Reader) else Reader(reader.session if hasattr(reader, "session") else reader)).read(uri, generation=generation)


def same_generation(saved, current):
    """Cosmetic metadata changes cannot invalidate identical canonical bytes."""
    validate(saved)
    validate(current)
    immutable = PROOF_FIELDS - {"updated_at"}
    return (all(saved[key] == current[key] for key in immutable)
            and _timestamp(current["updated_at"]) >= _timestamp(saved["updated_at"]))


def follows(saved, current):
    """Order distinct canonical versions by immutable creation time, not ID."""
    validate(saved)
    validate(current)
    return (saved["uri"] == current["uri"] and saved["generation"] != current["generation"]
            and _timestamp(current["created_at"]) > _timestamp(saved["created_at"]))


def reverify(reader, proof):
    validate(proof)
    current = read(reader, proof["uri"], generation=proof["generation"])
    require(same_generation(proof, current), "current translation recovery proof differs from saved evidence")
    return current
