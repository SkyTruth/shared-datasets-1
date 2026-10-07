"""Private read-only usage report served independently of public catalog bytes."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import mimetypes
from pathlib import Path
import re

from services.http_base import NO_STORE, Response, authenticated_user_email, email_domain_allowed, json_response

MAX_REPORT_BYTES = 8 * 1024 * 1024
ASSETS = Path(__file__).with_name("usage_assets")


class ReportReader:
    def __init__(self, bucket, client=None):
        from google.cloud import storage
        self.bucket = (client or storage.Client()).bucket(bucket)

    def read(self):
        raw = self.bucket.blob("published/manifest.json").download_as_bytes(start=0, end=65536, timeout=15)
        if len(raw) > 65536:
            raise ValueError("Usage manifest exceeds its budget")
        manifest = json.loads(raw)
        if manifest["schema_version"] != 1:
            raise ValueError("Unsupported usage manifest")
        ref = manifest["report"]
        if not re.fullmatch(r"published/[0-9a-f]{32}/report\.json", ref["path"]) or not 0 < ref["size"] <= MAX_REPORT_BYTES:
            raise ValueError("Invalid published report reference")
        generation = int(ref["generation"])
        raw = self.bucket.blob(ref["path"], generation=generation).download_as_bytes(start=0, end=ref["size"], if_generation_match=generation, timeout=15)
        if len(raw) != ref["size"] or hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            raise ValueError("Usage report integrity mismatch")
        payload = json.loads(raw)
        if payload["schema_version"] != 1 or payload["generated_at"] != manifest["generated_at"]:
            raise ValueError("Usage report identity mismatch")
        return payload


def handle(method, path, headers, *, reader, domains, now):
    email = authenticated_user_email(headers)
    if not email:
        return json_response(401, {"error": "IAP identity required"})
    if not email_domain_allowed(email, domains):
        return json_response(403, {"error": "Catalog access required"})
    if method not in {"GET", "HEAD"}:
        return Response(405, {"Cache-Control": NO_STORE, "Content-Type": "application/json", "Allow": "GET, HEAD"}, b'{"error":"Method not allowed"}')
    if path == "/api/usage":
        if reader is None:
            return json_response(503, {"error": "Usage monitoring has not been deployed"})
        try:
            payload = reader.read()
            generated = dt.datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00"))
            if generated.tzinfo is None or generated > now + dt.timedelta(minutes=5):
                raise ValueError("Invalid usage report time")
        except Exception:
            # This is an external storage boundary. Never return a signed URL,
            # object path, credential, or cached success after a failed read.
            return json_response(503, {"error": "Usage report unavailable; activity is unknown"})
        payload["stale"] = (now - generated).total_seconds() > 48 * 3600
        if payload["stale"]:
            for asset in payload["assets"]:
                asset["state"], asset["reason"] = "stale", "Report is more than 48 hours old"
        return json_response(200, payload, include_body=method != "HEAD")
    name = {"/usage": "index.html", "/usage/": "index.html", "/usage/app.js": "app.js", "/usage/style.css": "style.css"}.get(path)
    if name is None:
        return json_response(404, {"error": "Not found"})
    body = (ASSETS / name).read_bytes()
    content_type = "text/javascript" if name.endswith(".js") else mimetypes.guess_type(name)[0]
    return Response(200, {"Content-Type": content_type, "Cache-Control": NO_STORE, "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'"}, body if method == "GET" else b"")


def emit(slug, operation):
    print(json.dumps({"event": "dataset_usage", "asset_slug": slug, "operation": operation}), flush=True)
