"""Normalize passive observations and make inactivity evidence explicit.

Input logs -> classified event -> deduplicated ledger -> committed report.
Unknown attribution is activity. Unknown collection is never zero.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

VERSION = 1
SOURCES = ("gcs_audit", "cdn", "gcs_usage", "catalog")
KINDS = ("read", "interest", "catalog", "maintenance", "failed")
DISCLAIMER = "Observed activity only. Public usage logs are best-effort; downloaded copies may still be used. Retirement requires steward review."


def timestamp(value):
    result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Usage timestamps require a timezone")
    return result.astimezone(dt.UTC)


def iso(value):
    return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Classifier:
    def __init__(self, catalog, config, bucket):
        self.bucket = bucket
        self.config = config
        for mapping in (config.get("principals", {}), config.get("referrers", {})):
            if len(mapping) > 100 or any(not isinstance(label, str) or not 1 <= len(label) <= 120 or "@" in label for label in mapping.values()):
                raise ValueError("Application mappings require bounded display names, not personal identities")
        self.assets = {row["asset_slug"]: row for row in catalog}
        self.roots = {}
        for row in catalog:
            path = row["canonical_path"]
            if not path.startswith(f"gs://{bucket}/"):
                raise ValueError("Usage catalog must describe the shared bucket")
            root = path[len(f"gs://{bucket}/"):].rsplit("/", 2)[0] + "/"
            self.roots[root] = row["asset_slug"]
        self.version = digest({"version": VERSION, "config": config, "code": {
            name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("model.py", "run.py", "stream.py", "health.py")}})

    def asset(self, path):
        path = unquote(path).lstrip("/")
        if path.startswith(f"storage/v1/b/{self.bucket}/o/") or path.startswith(f"b/{self.bucket}/o/"):
            path = path.split("/o/", 1)[1]
        if path.startswith(f"{self.bucket}/"):
            path = path[len(self.bucket) + 1:]
        for prefix in ("artifacts/", "private/"):
            if path.startswith(prefix):
                path = path[len(prefix):]
                break
        index = re.fullmatch(r"_catalog/releases/([a-z0-9-]+)\.json", path)
        if index:
            return (index[1], True) if index[1] in self.assets else (None, False)
        alias = re.fullmatch(r"pmtiles/(public|private|internal)/([a-z0-9-]+)\.pmtiles", path)
        if alias:
            return (alias[2], False) if alias[2] in self.assets else (None, False)
        for root, slug in self.roots.items():
            if path.startswith(root):
                suffix = path[len(root):]
                if suffix == "README.md" or suffix.startswith(("runs/", "source/", "archive/")):
                    return None, False
                if suffix.startswith(("latest/", "releases/")):
                    return slug, False
        return None, False

    def event(self, source, raw, probes):
        principal = ""
        referrer = ""
        bytes_served = None
        status = 200
        method = "GET"
        operation = ""
        catalog_event = False
        if source == "gcs_usage":
            if raw["cs_bucket"] != self.bucket:
                return None
            when = iso(dt.datetime.fromtimestamp(int(raw["time_micros"]) / 1_000_000, dt.UTC))
            path = raw.get("cs_object") or urlsplit(raw["cs_uri"]).path
            method = raw["cs_method"]
            operation = raw.get("cs_operation", "") or ""
            status = int(raw["sc_status"])
            bytes_served = int(raw["sc_bytes"]) if raw.get("sc_bytes") else None
            referrer = raw.get("cs_referer", "")
            identity = raw.get("s_request_id")
        else:
            when = iso(timestamp(raw["timestamp"]))
            identity = raw.get("insertId")
            if source == "gcs_audit":
                payload = raw["protoPayload"]
                if payload["serviceName"] != "storage.googleapis.com":
                    return None
                name = payload.get("resourceName", "")
                marker = f"/buckets/{self.bucket}/objects/"
                if marker not in name:
                    labels = raw.get("resource", {}).get("labels", {})
                    if labels.get("bucket_name") == self.bucket or f"/buckets/{self.bucket}" in name:
                        raise ValueError("Unresolvable shared-bucket audit observation")
                    return None
                path = name.split(marker, 1)[1]
                operation = payload["methodName"]
                if operation != "storage.objects.get":
                    return None
                principal = payload.get("authenticationInfo", {}).get("principalEmail", "")
                status = 403 if payload.get("status", {}).get("code", 0) else 200
                # Audit get operations do not prove a bytes download. Preserve
                # interest instead of guessing from an unavailable HTTP method.
                method = "AUDIT_GET"
            elif source == "cdn":
                http = raw["httpRequest"]
                url = urlsplit(http["requestUrl"])
                if url.hostname != self.config["cdn_host"]:
                    return None
                path = url.path
                status = int(http["status"])
                method = http["requestMethod"]
                referrer = http.get("referer", "")
                bytes_served = int(http["responseSize"]) if "responseSize" in http else None
            elif source == "catalog":
                payload = raw.get("jsonPayload", {})
                if payload.get("event") not in {"dataset_usage", "dataset_usage_probe"}:
                    return None
                resource = raw.get("resource", {})
                if not ((resource.get("type") == "cloud_run_revision" and resource.get("labels", {}).get("service_name") == "catalog-viewer") or (payload.get("event") == "dataset_usage_probe" and resource.get("type") == "cloud_run_job" and resource.get("labels", {}).get("job_name") == "dataset-usage")):
                    return None
                path = f"_catalog/releases/{payload['asset_slug']}.json"
                catalog_event = True
            else:
                raise ValueError("Unknown usage source")
        if bytes_served is not None and bytes_served < 0:
            raise ValueError("Negative response byte count")
        slug, index = self.asset(path)
        if slug is None or method not in {"GET", "HEAD", "AUDIT_GET"}:
            return None
        if not identity:
            raise ValueError("Relevant log entry has no deduplication identifier")
        tokens = re.findall(r"usage-probe-[a-f0-9]{32}", json.dumps(raw))
        matched = next((token for token in tokens if (probes.get(token, {}).get("source") == source or (source == "gcs_usage" and probes.get(token, {}).get("source") in {"gcs_audit", "cdn"})) and probes[token]["slug"] == slug), None)
        app = self.config.get("principals", {}).get(principal)
        confidence = "verified" if app else "unknown"
        if not app:
            app = self.config.get("referrers", {}).get(urlsplit(referrer).hostname)
            confidence = "inferred" if app else "unknown"
        app = app or "Unknown"
        if matched and status in {200, 206, 304}:
            kind, app, confidence = "maintenance", "Shared datasets maintenance", "verified"
        elif catalog_event:
            kind, app, confidence = "catalog", "Shared datasets catalog", "verified"
        elif status not in {200, 206, 304}:
            kind = "failed"
        elif principal in self.config.get("maintenance_principals", []):
            kind, app, confidence = "maintenance", "Shared datasets maintenance", "verified"
        elif principal in self.config.get("catalog_principals", []):
            kind, app, confidence = "catalog", "Shared datasets catalog", "verified"
        elif principal.endswith("@cloud-cdn-fill.iam.gserviceaccount.com"):
            kind, app, confidence = "maintenance", "CDN origin fill", "verified"
        elif index or method in {"HEAD", "AUDIT_GET"} or status == 304 or operation == "GET_Object_Metadata":
            kind = "interest"
        else:
            kind = "read"
        return {"day": when[:10], "at": when, "slug": slug, "source": source,
                "id": digest([source, when, identity]), "kind": kind, "application": app,
                "confidence": confidence, "bytes": bytes_served, "probe": matched}


def report(db, now, manifest_time=None):
    generated = manifest_time or iso(now)
    stale = (now - timestamp(generated)).total_seconds() > 48 * 3600
    completed = (now - dt.timedelta(hours=48)).date() - dt.timedelta(days=1)
    sources = [dict(zip(("day", "source", "state", "reason"), row)) for row in db.execute(
        "SELECT day,source,state,reason FROM coverage ORDER BY day,source")]
    window_start = completed - dt.timedelta(days=364)
    source_health = []
    for source in SOURCES:
        counts = dict(db.execute("SELECT state,COUNT(*) FROM coverage WHERE source=? AND day BETWEEN ? AND ? GROUP BY state", (source, window_start.isoformat(), completed.isoformat())))
        source_health.append({"source": source, "window_days": 365, "healthy_days": counts.get('healthy', 0),
                              "gap_days": counts.get('gap', 0), "pending_days": counts.get('pending', 0),
                              "unknown_days": 365 - sum(counts.values())})
    assets = []
    group_count = 0
    for slug, title, status, registered, last_read, last_interest, last_catalog, mapping_started in db.execute("SELECT slug,title,status,registered,last_read,last_interest,last_catalog,mapping_started FROM assets ORDER BY slug"):
        start = max(dt.date.fromisoformat(registered), dt.date.fromisoformat(mapping_started), dt.date.fromisoformat(db.execute("SELECT value FROM settings WHERE key='observation_start'").fetchone()[0]))
        observed_days = max(0, (completed - start).days + 1)
        latest = max((item for item in (last_read, last_interest) if item), default=None)
        window_healthy = all(source['healthy_days'] == 365 for source in source_health)
        if status != "active":
            state, reason = "not_active", "Lifecycle status is not active or asset is absent from the current catalog"
        elif stale:
            state, reason = "stale", "Report is more than 48 hours old"
        elif observed_days < 365:
            state, reason = "collecting_history", f"{observed_days} fully elapsed observation days"
        elif not window_healthy:
            state, reason = "coverage_incomplete", "Missing or unresolved collection evidence in the 365-day window"
        elif latest and timestamp(latest).date() >= window_start:
            state, reason = "activity_observed", "Dataset reads or dataset-specific interest observed"
        else:
            state, reason = "qualified_candidate", "365 observed days without downstream access or interest; no known collection gaps"
        windows = {}
        for days in (30, 90, 365):
            begin = (now.date() - dt.timedelta(days=days - 1)).isoformat()
            groups = []
            for source, kind, app, confidence, requests, byte_count, active_days in db.execute(
                "SELECT source,kind,app,confidence,SUM(requests),SUM(bytes),COUNT(DISTINCT day) FROM daily WHERE slug=? AND day>=? GROUP BY source,kind,app,confidence ORDER BY source,kind,app,confidence", (slug, begin)):
                groups.append({"source": source, "activity": kind, "application": app, "confidence": confidence,
                               "requests": requests, "bytes": byte_count, "active_days": active_days})
                if len(groups) > 2000:
                    raise ValueError("Usage report group budget exceeded")
            group_count += len(groups)
            if group_count > 20000:
                raise ValueError("Usage report group budget exceeded")
            windows[str(days)] = groups
        assets.append({"slug": slug, "title": title, "lifecycle_status": status, "registered_on": registered,
                       "last_read": last_read, "last_interest": last_interest, "last_catalog": last_catalog,
                       "state": state, "reason": reason, "observation_days": observed_days, "observed_from": start.isoformat() if start.year < 9999 else None, "windows": windows})
    return {"schema_version": VERSION, "generated_at": generated, "stale": stale, "observed_through": completed.isoformat(),
            "disclaimer": DISCLAIMER, "assets": assets, "coverage": sources[-120:], "source_health": source_health,
            "evidence_versions": [dict(zip(("day", "source", "policy", "catalog"), row)) for row in db.execute("SELECT day,source,policy,catalog FROM evidence ORDER BY day,source")],
            "cost": json.loads(db.execute("SELECT value FROM settings WHERE key='cost'").fetchone()[0])}
