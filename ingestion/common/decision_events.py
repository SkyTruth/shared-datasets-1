"""Typed wire contract for scheduled feature-identity review notifications."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import re

PROJECT = "shared-datasets-1"
TOPIC = f"projects/{PROJECT}/topics/identity-decision-events"
SUBSCRIPTION = f"projects/{PROJECT}/subscriptions/identity-decision-events"
SCHEMA = "feature-identity-decision-event-v1"
ASSET_JOBS = {
    "wdpa-marine": "wdpa-monthly", "wdpa-terrestrial": "wdpa-monthly",
    "ims-sea-ice-extent": "sea-ice-daily",
    "eamlis-abandoned-mine-land-inventory": "eamlis-monthly",
    "mpatlas-zone-assessments": "mpatlas-monthly",
}
CASE_FIELDS = (
    "new_identity_key", "new_geometry_hash", "new_properties_hash",
    "matching_geometry_feature_ids", "matching_properties_feature_ids",
    "matching_geometry_properties_hashes", "matching_properties_geometry_hashes",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def context(value):
    require(set(value) == {"project", "job", "execution", "asset_slug", "release"}, "invalid decision event context")
    require(value["project"] == PROJECT and ASSET_JOBS.get(value["asset_slug"]) == value["job"], "foreign decision producer")
    require(isinstance(value["execution"], str) and re.fullmatch(re.escape(value["job"]) + r"-[a-z0-9]{5}", value["execution"]), "invalid decision execution")
    require(isinstance(value["release"], str) and dt.date.fromisoformat(value["release"]).isoformat() == value["release"], "invalid decision release")
    return dict(value)


def event_key(value):
    return digest(context({key: value[key] for key in ("project", "job", "execution", "asset_slug", "release")}))


def case(value):
    require(isinstance(value, dict) and set(value) == set(CASE_FIELDS), "invalid identity case fields")
    key = value["new_identity_key"]
    require(isinstance(key, list) and key and all(isinstance(part, str) and part and len(part) <= 1024 for part in key), "invalid identity key")
    result = {"new_identity_key": key}
    for field in CASE_FIELDS[1:]:
        raw = value[field]
        if field.startswith("new_"):
            require(isinstance(raw, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", raw), "invalid identity hash")
            result[field] = raw
        else:
            pattern = r"[1-9][0-9]*" if field.endswith("feature_ids") else r"sha256:[0-9a-f]{64}"
            require(isinstance(raw, list) and all(isinstance(item, str) and re.fullmatch(pattern, item) for item in raw)
                    and len(raw) == len(set(raw)), "invalid identity match set")
            result[field] = sorted(raw)
    require(result["matching_geometry_feature_ids"] or result["matching_properties_feature_ids"], "identity case has no previous match")
    return result


def case_key(asset_slug, release, value):
    require(asset_slug in ASSET_JOBS, "unmanaged decision asset")
    require(isinstance(release, str) and dt.date.fromisoformat(release).isoformat() == release, "invalid decision release")
    return digest({"asset_slug": asset_slug, "release": release, "case": case(value)})


def event(value):
    require(isinstance(value, dict) and set(value) == {"schema", "project", "job", "execution", "asset_slug", "release", "cases"}
            and value["schema"] == SCHEMA, "invalid identity decision event")
    source = context({key: value[key] for key in ("project", "job", "execution", "asset_slug", "release")})
    require(isinstance(value["cases"], list) and value["cases"], "empty identity decision event")
    cases = sorted([case(item) for item in value["cases"]], key=canonical)
    require(len({digest(item) for item in cases}) == len(cases), "duplicate identity case")
    return {"schema": SCHEMA, **source, "cases": cases}


def publish(*, asset_slug, release, ambiguities, env=None, session=None):
    """Queue a durable production request; local diagnostics do not post alerts."""
    env = os.environ if env is None else env
    if not env.get("CLOUD_RUN_EXECUTION"):
        return False
    value = event({"schema": SCHEMA, "project": PROJECT, "job": env.get("CLOUD_RUN_JOB"),
                   "execution": env["CLOUD_RUN_EXECUTION"], "asset_slug": asset_slug,
                   "release": release, "cases": [{key: item[key] for key in CASE_FIELDS} for item in ambiguities]})
    raw = canonical(value)
    # The push envelope base64-encodes the message inside Pub/Sub's 10 MB limit.
    require(len(raw) <= 7 * 1024 * 1024, "identity decision event exceeds Pub/Sub message limit")
    if session is None:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/pubsub"])
        session = AuthorizedSession(credentials)
    response = session.post("https://pubsub.googleapis.com/v1/" + TOPIC + ":publish",
                            json={"messages": [{"data": base64.b64encode(raw).decode()}]}, timeout=30)
    response.raise_for_status()
    ids = response.json().get("messageIds")
    require(isinstance(ids, list) and len(ids) == 1 and isinstance(ids[0], str) and ids[0], "unconfirmed identity event publication")
    return True
