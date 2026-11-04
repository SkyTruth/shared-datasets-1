"""One best-effort translation-debt notice per committed asset release."""

from __future__ import annotations

import csv
from contextlib import closing
import datetime as dt
import hashlib
import html
import json
import logging
import os
from pathlib import Path
from statistics import median
import sqlite3
import tempfile
from urllib.parse import quote

from google.api_core.exceptions import GoogleAPIError, NotFound, PreconditionFailed

from ingestion.common import release_index
from ingestion.common.publication import PublicationError, require, split_uri
from scripts import catalog_csv, compare_releases, feature_metadata_localization as localization, release_feature_model as model, slack_notify

LOGGER = logging.getLogger(__name__)


def _slack_length(value):
    return len(value.encode("utf-16-le")) // 2


def _coverage(manifest):
    locales = manifest.get("translations", {}).get("locales", {})
    total = sum(counts["translatable_values"] for counts in locales.values())
    return 100 * sum(counts["current"] for counts in locales.values()) / total if total else None


def _language_outlier(locales):
    rates = sorted((100 * counts["current"] / counts["translatable_values"], locale)
                   for locale, counts in locales.items() if counts["translatable_values"])
    if len(rates) < 2:
        return ""
    lowest, locale = rates[0]
    others = median(rate for rate, _ in rates[1:])
    if others - lowest < 5:
        return ""
    return f"\nLowest language coverage is *{locale} at {lowest:.2f}%*, versus {others:.2f}% median for the others."


def _previous_manifest(client, manifest, manifest_uri):
    """Use the immediately preceding published release, pinned by its index."""
    bucket_name, _ = split_uri(manifest_uri)
    bucket = client.bucket(bucket_name)
    slug = manifest["asset_slug"]
    try:
        blob = bucket.blob(release_index.release_index_object(slug))
        blob.reload()
        index = release_index.coerce_release_index(
            json.loads(blob.download_as_bytes(if_generation_match=int(blob.generation))), slug,
        )
        earlier = [entry for entry in index["releases"] if entry["date"] < manifest["release"]]
        if not earlier:
            return None
        previous = max(earlier, key=lambda entry: entry["date"])
        entry = release_index.release_file_for_format(previous["files"], "manifest")
        if entry is None or entry.get("generation") is None:
            return None
        expected_path = f"{manifest_uri.rsplit('/releases/', 1)[0]}/releases/{previous['date']}/{slug}.manifest.json"
        require(entry["path"] == expected_path, "previous manifest belongs to another asset")
        generation = int(entry["generation"])
        require(generation > 0, "previous manifest requires a positive generation")
        blob = bucket.blob(split_uri(entry["path"])[1], generation=generation)
        payload = json.loads(blob.download_as_bytes(if_generation_match=generation))
        model.validate_release_manifest(payload, expected_asset_slug=slug, expected_release=previous["date"], require_generations=True)
        return payload
    except NotFound:
        return None
    except (GoogleAPIError, ValueError, PublicationError, release_index.ReleaseIndexError):
        LOGGER.warning("Previous translation coverage unavailable for %s", manifest_uri, exc_info=True)
        return None


def _download_artifact(client, artifact, path):
    bucket_name, name = split_uri(artifact["path"])
    generation = artifact["generation"]
    blob = client.bucket(bucket_name).blob(name, generation=generation)
    blob.download_to_filename(str(path), if_generation_match=generation)
    with path.open("rb") as handle:
        require(hashlib.file_digest(handle, "sha256").hexdigest() == str(artifact["sha256"]).removeprefix("sha256:"), "artifact hash changed")


def _prepare_change_baseline(client, manifest, previous, directory, db):
    if previous is None:
        return None
    try:
        old_fields = model.validate_release_schema(previous["schema"])
        new_fields = model.validate_release_schema(manifest["schema"])
        if not compare_releases.identity_compatibility(previous, manifest, old_fields, new_fields)["compatible"]:
            return None
        artifact = next((entry for entry in previous["artifacts"] if entry["role"] == "metadata"), None)
        require(artifact is not None, "baseline manifest requires canonical metadata")
        path = directory / "previous.metadata.ndjson.gz"
        _download_artifact(client, artifact, path)
        db.executescript("""
            PRAGMA journal_mode=OFF;
            PRAGMA cache_size=-8192;
            CREATE TABLE baseline (id TEXT PRIMARY KEY) WITHOUT ROWID;
            CREATE TABLE affected (id TEXT PRIMARY KEY) WITHOUT ROWID;
        """)
        for feature in model.read_metadata_sidecar(path):
            require(feature["asset_slug"] == manifest["asset_slug"] and feature["release"] == previous["release"], "baseline metadata belongs to another release")
            model.validate_feature_id(feature["feature_id"])
            db.execute("INSERT INTO baseline VALUES (?)", (feature["feature_id"],))
        require(db.execute("SELECT COUNT(*) FROM baseline").fetchone()[0] == previous["validation"].get("feature_count"), "baseline feature count differs from manifest")
        db.commit()
        return set(new_fields) - set(old_fields)
    except (GoogleAPIError, ValueError, PublicationError, sqlite3.IntegrityError, OSError):
        LOGGER.warning("Translation gap change analysis unavailable for %s", manifest["asset_slug"], exc_info=True)
        return None


def _source_example(field, locale, value):
    text = " ".join(value.split())
    if text.lower().startswith(("http://", "https://", "www.")) or "`" in field + text:
        return None
    excerpt = text if len(text) <= 80 else text[:79] + "…"
    rendered = html.escape(f"{field} ({locale}) “{excerpt}”", quote=False)
    return rendered if _slack_length(rendered) <= 240 else None


def _notice_body(manifest, manifest_uri, previous=None, *, examples=(), changes=None):
    bucket, name = split_uri(manifest_uri)
    coverage = _coverage(manifest)
    require(coverage is not None, "translation notice requires a positive coverage denominator")
    locales = manifest["translations"]["locales"]
    total = sum(counts["translatable_values"] for counts in locales.values())
    outdated = 100 * sum(counts["stale"] for counts in locales.values()) / total
    untranslated = 100 * sum(counts["missing"] for counts in locales.values()) / total
    details = [f"Outdated translations *{outdated:.2f}%* · Untranslated text *{untranslated:.2f}%*."]
    if changes is not None:
        details.append(f"New records with gaps *{changes['new_rows']:,}* · New columns with gaps *{changes['new_columns']:,}* (since {previous['release']}).")
    if examples:
        details.append("Needs translation — " + " · ".join(examples))
    detail_text = "\n".join(details)
    comparison = "Previous-release comparison unavailable."
    if previous is not None and set(previous.get("translations", {}).get("locales", {})) == set(locales):
        prior_coverage = _coverage(previous)
        if prior_coverage is not None:
            change = round(coverage - prior_coverage, 2)
            if change == 0:
                comparison = f"Coverage is unchanged to two decimal places since {previous['release']}."
            else:
                direction = "up" if change > 0 else "down"
                comparison = f"Coverage is {direction} {abs(change):.2f} percentage points since {previous['release']}."
    body = (
        "Published successfully.\n"
        f"*{coverage:.2f}%* of translation entries are current across *{len(locales)}* languages. "
        f"*{100 - coverage:.2f}%* fall back to the original text.\n"
        f"{comparison}{_language_outlier(locales)}\n\n"
        f"{detail_text}\n\n"
        "To handle this, ask Codex\n"
        f"> Fill in missing translations and refresh outdated translations for {manifest['asset_slug']}, "
        f"release {manifest['release']}, and prepare a review PR.\n\n"
        f"<https://storage.cloud.google.com/{quote(bucket)}/{quote(name)}|Coverage details>"
    )
    require(_slack_length(body) <= 3000, "translation notice exceeds Slack section limit")
    return body


def debt_at_threshold(counts):
    return counts["translatable_values"] > 0 and 100 * (counts["stale"] + counts["missing"]) >= counts["translatable_values"]


def notify_release(client, manifest_uri, *, generation=None):
    """Notification outages never undo or fail a completed dataset release."""
    try:
        return _notify_release(client, manifest_uri, generation=generation)
    except Exception:  # external storage/Slack failures after the publication commit
        LOGGER.warning("Translation notice failed for %s; release remains published", manifest_uri, exc_info=True)
        return "failed"


def _notify_release(client, manifest_uri, *, generation=None):
    bucket_name, name = split_uri(manifest_uri)
    bucket = client.bucket(bucket_name)
    manifest_blob = bucket.blob(name, generation=generation)
    manifest_blob.reload()
    manifest_generation = int(manifest_blob.generation)
    manifest = json.loads(manifest_blob.download_as_bytes(if_generation_match=manifest_generation))
    model.validate_release_manifest(manifest, require_generations=True)
    translations = manifest.get("translations")
    if not translations or not any(debt_at_threshold(counts) for counts in translations["locales"].values()):
        return "below_threshold"
    slug, release = manifest["asset_slug"], manifest["release"]
    root, separator, filename = name.partition(f"/releases/{release}/")
    require(separator and filename == f"{slug}.manifest.json", "notice requires a concrete release manifest")
    row = catalog_csv.catalog_row(slug)
    require(row is not None, "translation notice requires a catalog entry")
    require(set(translations["locales"]) == set(row["translation_locales"].split(";")), "manifest locales differ from maintained configuration")
    marker = bucket.blob(f"{root}/runs/{release}.translation-notice.json")
    record = {"schema_version": 1, "asset_slug": slug, "release": release,
              "manifest": {"path": manifest_uri, "generation": manifest_generation},
              "status": "claimed", "claimed_at": dt.datetime.now(dt.UTC).isoformat(), "debt_files": []}
    try:
        marker.upload_from_string(json.dumps(record), content_type="application/json", if_generation_match=0)
    except PreconditionFailed:
        return "already_claimed"
    marker_generation = int(marker.generation)

    # Claim before export or delivery. A crashed/unknown attempt is deliberately
    # not retried: an incoming webhook cannot provide exactly-once delivery.
    root_dir = Path(os.environ.get("SHARED_DATASETS_WORKDIR") or Path(tempfile.gettempdir()) / "shared-datasets-1") / "translation-debt"
    root_dir.mkdir(parents=True, exist_ok=True)
    previous = _previous_manifest(client, manifest, manifest_uri)
    examples, changed_columns = {}, set()
    changes = None
    with (
        tempfile.TemporaryDirectory(prefix=f"{slug}-{release}-", dir=root_dir) as temporary,
        closing(sqlite3.connect(Path(temporary) / "changes.sqlite")) as db,
    ):
        directory = Path(temporary)
        new_columns = _prepare_change_baseline(client, manifest, previous, directory, db)
        artifacts = {entry["path"]: entry for entry in manifest["artifacts"]}
        for locale, counts in sorted(translations["locales"].items()):
            debt_count = counts["stale"] + counts["missing"]
            if not debt_count:
                continue
            sidecar_uri = f"gs://{bucket_name}/{root}/releases/{release}/{slug}.metadata.{locale}.ndjson.gz"
            artifact = artifacts[sidecar_uri]
            path = directory / f"{locale}.ndjson.gz"
            _download_artifact(client, artifact, path)
            csv_path = directory / f"{locale}.csv"
            exported = 0
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=localization.DEBT_COLUMNS)
                writer.writeheader()
                for feature in model.read_metadata_sidecar(path):
                    require(feature["asset_slug"] == slug and feature["release"] == release and feature["translation"]["locale"] == locale, "localized record belongs to another release or locale")
                    fallback_fields = feature["translation"]["fallback_fields"]
                    if new_columns is not None and fallback_fields:
                        db.execute("INSERT OR IGNORE INTO affected VALUES (?)", (feature["feature_id"],))
                        changed_columns.update(new_columns.intersection(fallback_fields))
                    for field in fallback_fields:
                        value = feature["properties"][field]
                        require(isinstance(value, str) and bool(value.strip()), "translation debt must contain nonblank source strings")
                        debt_row = (feature["feature_id"], field, locale, localization.source_value_hash(value), value)
                        writer.writerow(dict(zip(localization.DEBT_COLUMNS, debt_row)))
                        exported += 1
                        if row["access_tier"] == "public" and field not in examples and len(examples) < 2:
                            example = _source_example(field, locale, value)
                            if example is not None:
                                examples[field] = example
            require(exported == debt_count, "localized fallback count differs from manifest coverage")
            target_name = f"_scratch/translation-debt/{slug}/{release}/{locale}.csv"
            target = bucket.blob(target_name)
            target.upload_from_filename(str(csv_path), content_type="text/csv", if_generation_match=0)
            record["debt_files"].append({"path": f"gs://{bucket_name}/{target_name}", "generation": int(target.generation), "rows": exported})
        if new_columns is not None:
            new_rows = db.execute("SELECT COUNT(*) FROM affected LEFT JOIN baseline USING (id) WHERE baseline.id IS NULL").fetchone()[0]
            changes = {"new_rows": new_rows, "new_columns": len(changed_columns)}
    body = _notice_body(manifest, manifest_uri, previous, examples=examples.values(), changes=changes)
    delivered = slack_notify.notify(title=f"Translations · {slug} · {release}", body=body, status="info", emoji="🌐")
    if not delivered:
        LOGGER.warning("Translation notice delivery unconfirmed for %s; automatic retry suppressed", manifest_uri)
    record.update(status="delivered" if delivered else "delivery_unknown", finished_at=dt.datetime.now(dt.UTC).isoformat())
    marker.upload_from_string(json.dumps(record), content_type="application/json", if_generation_match=marker_generation)
    return record["status"]


def notify_records(records):
    """Shared job completion hook, including a resumed successful release."""
    from google.cloud import storage
    client = storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT", "shared-datasets-1"))
    for record in records:
        if record.get("status") == "success" and record.get("localization"):
            artifact = next(entry for entry in record["release_paths"] if entry["path"].endswith(".manifest.json"))
            notify_release(client, artifact["path"], generation=artifact["generation"])
