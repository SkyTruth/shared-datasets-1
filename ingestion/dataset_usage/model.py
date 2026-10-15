"""Normalize passive observations for dataset usage visibility.

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

VERSION = 3
MANIFEST_VERSION = 1
RETIRED_SOURCES = ("gcs_audit", "cdn", "catalog", "app_usage")
SOURCES = ("gcs_usage",)
KINDS = ("read", "interest", "maintenance", "failed")
DISCLAIMER = "Observed activity only. Public usage logs are best-effort; logs cannot prove non-use of downloaded copies. Missing information never means zero use."


def timestamp(value):
    result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Usage timestamps require a timezone")
    return result.astimezone(dt.UTC)


def iso(value):
    return value.astimezone(dt.UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Classifier:
    def __init__(self, catalog, config, bucket):
        if type(config.get("schema_version")) is not int or config["schema_version"] != VERSION:
            raise ValueError("Unsupported usage configuration schema")
        if set(config) - {"schema_version", "raw_retention_days", "collection_enabled", "referrers"}:
            raise ValueError("Unsupported passive usage configuration field")
        self.bucket = bucket
        self.config = config
        retention = config["raw_retention_days"]
        if type(retention) is not int or not 7 <= retention <= 30:
            raise ValueError("Raw retention must be 7–30 days to allow delayed delivery and recovery")
        for mapping in (config.get("referrers", {}),):
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
        if any(path.startswith(f"{prefix}{self.bucket}/o/") for prefix in ("storage/v1/b/", "download/storage/v1/b/", "b/")):
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

    def event(self, source, raw, probes, *, name=None):
        if source != "gcs_usage":
            raise ValueError("Retired or unknown usage source")
        if raw["cs_bucket"] != self.bucket:
            return None
        when = iso(dt.datetime.fromtimestamp(int(raw["time_micros"]) / 1_000_000, dt.UTC))
        path = raw.get("cs_object") or urlsplit(raw["cs_uri"]).path
        method, status = raw["cs_method"], int(raw["sc_status"])
        operation = raw.get("cs_operation", "") or ""
        bytes_served = int(raw["sc_bytes"]) if raw.get("sc_bytes") else None
        if bytes_served is not None and bytes_served < 0:
            raise ValueError("Negative response byte count")
        slug, index = self.asset(path)
        if slug is None or method not in {"GET", "HEAD"}:
            return None
        identity = raw.get("s_request_id")
        if not identity:
            raise ValueError("Relevant log entry has no deduplication identifier")
        tokens = re.findall(r"usage-probe-[a-f0-9]{32}", json.dumps(raw))
        matched = next((token for token in tokens if probes.get(token, {}).get("source") == source and probes[token]["slug"] == slug), None)
        app = self.config.get("referrers", {}).get(urlsplit(raw.get("cs_referer", "")).hostname)
        confidence = "inferred" if app else "unknown"
        app = app or "Unknown"
        if matched and status in {200, 206, 304}:
            kind, app, confidence = "maintenance", "Shared datasets maintenance", "verified"
        elif status not in {200, 206, 304}:
            kind = "failed"
        elif index or method == "HEAD" or status == 304 or operation == "GET_Object_Metadata":
            kind = "interest"
        else:
            kind = "read"
        return {"day": when[:10], "at": when, "slug": slug, "source": source,
                "id": digest([source, timestamp(when).isoformat().replace("+00:00", "Z"), identity]), "kind": kind, "application": app,
                "confidence": confidence, "bytes": bytes_served, "probe": matched}


def report(db, now, manifest_time=None):
    generated = manifest_time or iso(now)
    stale = (now - timestamp(generated)).total_seconds() > 48 * 3600
    completed = (now - dt.timedelta(hours=48)).date() - dt.timedelta(days=1)
    sources = [dict(zip(("day", "source", "state", "reason"), row)) for row in db.execute(
        "SELECT day,source,state,reason FROM coverage WHERE source='gcs_usage' ORDER BY day,source")]
    health_days = 460
    window_start = completed - dt.timedelta(days=health_days - 1)
    source_health = []
    for source in SOURCES:
        counts = dict(db.execute("SELECT state,COUNT(*) FROM coverage WHERE source=? AND day BETWEEN ? AND ? GROUP BY state", (source, window_start.isoformat(), completed.isoformat())))
        source_health.append({"source": source, "window_days": health_days, "healthy_days": counts.get('healthy', 0),
                              "gap_days": counts.get('gap', 0), "pending_days": counts.get('pending', 0),
                              "unknown_days": health_days - sum(counts.values())})
    assets = []
    group_count = 0
    for slug, title, status, registered, last_read, last_interest, last_catalog in db.execute("SELECT slug,title,status,registered,last_read,last_interest,last_catalog FROM assets ORDER BY slug"):
        windows = {}
        for days in (30, 90):
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
                       "windows": windows})
    retired = [dict(zip(("source", "retired_at"), row)) for row in db.execute("SELECT source,retired_at FROM retired_sources ORDER BY source")]
    return {"retired_sources": retired, "limitations": ["Bucket logs are best-effort", "CDN cache hits do not reach the bucket", "Cached and offline use cannot be observed", "Referrer attribution is inferred; missing referrers remain unknown"], "schema_version": VERSION, "generated_at": generated, "stale": stale, "observed_through": completed.isoformat(),
            "disclaimer": DISCLAIMER, "assets": assets, "coverage": sources[-120:], "source_health": source_health,
            "evidence_versions": [dict(zip(("day", "source", "policy", "catalog"), row)) for row in db.execute("SELECT day,source,policy,catalog FROM evidence ORDER BY day,source")],
            "cost": json.loads(db.execute("SELECT value FROM settings WHERE key='cost'").fetchone()[0])}
