"""Daily passive usage aggregation with one atomic publication boundary.

raw generations -> SQLite transaction -> immutable ledger + report
                                           |
                                manifest compare-and-swap
A checkpoint is visible only with the report produced from that checkpoint.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import hashlib
import json
import math
import os
import re
from pathlib import Path
import sqlite3
import tempfile
import uuid

from google.api_core.exceptions import GoogleAPICallError

from ingestion.dataset_usage.model import Classifier, SOURCES, VERSION, digest, iso, report, timestamp
from ingestion.dataset_usage.stream import json_records, text_stream, usage_records
from scripts.catalog_csv import read_catalog_rows_text

MAX_LEDGER_BYTES = 128 * 1024 * 1024
MAX_REPORT_BYTES = 8 * 1024 * 1024
MANIFEST = "published/manifest.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assets(slug TEXT PRIMARY KEY,title TEXT,status TEXT,registered TEXT,last_read TEXT,last_interest TEXT,last_catalog TEXT,mapping_started TEXT,root TEXT);
CREATE TABLE IF NOT EXISTS events(day TEXT,source TEXT,id TEXT,PRIMARY KEY(day,source,id));
CREATE TABLE IF NOT EXISTS inputs(name TEXT,generation TEXT,seen_at TEXT,source TEXT,PRIMARY KEY(name,generation));
CREATE TABLE IF NOT EXISTS daily(day TEXT,slug TEXT,source TEXT,kind TEXT,app TEXT,confidence TEXT,requests INTEGER,bytes INTEGER,last_at TEXT,PRIMARY KEY(day,slug,source,kind,app,confidence));
CREATE TABLE IF NOT EXISTS coverage(day TEXT,source TEXT,state TEXT,reason TEXT,PRIMARY KEY(day,source));
CREATE TABLE IF NOT EXISTS probes(token TEXT PRIMARY KEY,day TEXT,source TEXT,slug TEXT,policy TEXT NOT NULL,trusted INTEGER NOT NULL,seen INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS reconciliations(at TEXT,from_day TEXT,policy TEXT,catalog TEXT,previous_ledger TEXT);
CREATE TABLE IF NOT EXISTS evidence(day TEXT,source TEXT,policy TEXT,catalog TEXT,PRIMARY KEY(day,source,policy,catalog));
CREATE TABLE IF NOT EXISTS catalogs(hash TEXT PRIMARY KEY,payload TEXT NOT NULL);
"""


class GcsStore:
    def __init__(self, raw_bucket, state_bucket, client=None):
        from google.cloud import storage
        self.client = client or storage.Client()
        self.raw = self.client.bucket(raw_bucket)
        self.state = self.client.bucket(state_bucket)

    def load(self, destination):
        from google.api_core.exceptions import NotFound
        blob = self.state.blob(MANIFEST)
        try:
            data = blob.download_as_bytes(start=0, end=65536, timeout=30)
        except NotFound:
            return None, 0
        if len(data) > 65536:
            raise ValueError("Usage manifest exceeds its budget")
        manifest = json.loads(data)
        if manifest["schema_version"] != VERSION:
            raise ValueError("Unsupported usage manifest")
        ref = manifest["ledger"]
        if not re.fullmatch(r"state/[0-9a-f]{32}/ledger\.sqlite", ref["path"]) or not 0 < ref["size"] <= MAX_LEDGER_BYTES:
            raise ValueError("Usage ledger exceeds its budget")
        pinned = self.state.blob(ref["path"], generation=int(ref["generation"]))
        pinned.reload(if_generation_match=int(ref["generation"]), timeout=30)
        if pinned.size != ref["size"]:
            raise ValueError("Usage ledger size mismatch")
        pinned.download_to_filename(str(destination), if_generation_match=int(ref["generation"]), timeout=60)
        if destination.stat().st_size != ref["size"] or file_digest(destination) != ref["sha256"]:
            raise ValueError("Usage ledger integrity mismatch")
        return manifest, int(blob.generation)

    def inputs(self):
        # Listing is paged; SQLite holds the durable receipt inventory on disk.
        for blob in self.client.list_blobs(self.raw, page_size=256):
            if blob.name.startswith("storage-usage") and "_usage_" in blob.name:
                source = "gcs_usage"
            elif "cloudaudit.googleapis.com" in blob.name:
                source = "gcs_audit"
            elif blob.name.startswith("requests/"):
                source = "cdn"
            elif "run.googleapis.com" in blob.name and "/stdout/" not in blob.name:
                # Exported log ID is run.googleapis.com%2Fstdout or its decoded
                # hierarchy. Classification verifies the platform resource.
                source = "catalog"
            elif "stdout" in blob.name:
                source = "catalog"
            else:
                continue
            yield {"name": blob.name, "generation": str(blob.generation), "size": blob.size, "source": source}

    @contextlib.contextmanager
    def records(self, item):
        blob = self.raw.blob(item["name"], generation=int(item["generation"]))
        with blob.open("rb", chunk_size=1024 * 1024, if_generation_match=int(item["generation"]), timeout=30) as binary:
            with text_stream(binary) as stream:
                yield usage_records(stream) if item["source"] == "gcs_usage" else json_records(stream)

    def publish(self, ledger, payload, generation):
        snapshot = uuid.uuid4().hex
        refs = {}
        for kind, path, data in (("ledger", f"state/{snapshot}/ledger.sqlite", None), ("report", f"published/{snapshot}/report.json", json.dumps(payload).encode())):
            blob = self.state.blob(path)
            blob.cache_control = "no-store"
            if data is None:
                blob.upload_from_filename(str(ledger), if_generation_match=0, timeout=60)
                size, sha = ledger.stat().st_size, file_digest(ledger)
            else:
                if len(data) > MAX_REPORT_BYTES:
                    raise ValueError("Usage report exceeds its budget")
                blob.upload_from_string(data, content_type="application/json", if_generation_match=0, timeout=30)
                size, sha = len(data), hashlib.sha256(data).hexdigest()
            refs[kind] = {"path": path, "generation": str(blob.generation), "size": size, "sha256": sha}
        manifest = {"schema_version": VERSION, "generated_at": payload["generated_at"], **refs}
        blob = self.state.blob(MANIFEST)
        blob.cache_control = "no-store"
        blob.upload_from_string(json.dumps(manifest), content_type="application/json", if_generation_match=generation, timeout=30)
        return manifest


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def initialize(db, classifier, now, activation):
    db.executescript(SCHEMA)
    db.execute("PRAGMA cache_size=-8192")
    db.execute("PRAGMA journal_mode=DELETE")
    today = now.date().isoformat()
    # Observation partitions must be whole UTC days. A registration or verified
    # activation partway through today cannot certify its earlier hours.
    first_complete_day = (now.date() + dt.timedelta(days=1)).isoformat()
    verified = activation.get("verified_at")
    start = max(first_complete_day, (timestamp(verified).date()+dt.timedelta(days=1)).isoformat()) if activation_valid(activation, classifier) else "9999-12-31"
    prior = db.execute("SELECT value FROM settings WHERE key='policy'").fetchone()
    if prior is None or prior[0] != classifier.version:
        db.execute("INSERT OR REPLACE INTO settings VALUES('observation_start',?)", (start,))
        db.execute("INSERT OR REPLACE INTO settings VALUES('policy',?)", (classifier.version,))
        db.execute("DELETE FROM coverage WHERE day>=?", (today,))
    elif db.execute("SELECT value FROM settings WHERE key='observation_start'").fetchone()[0] == "9999-12-31" and start != "9999-12-31":
        db.execute("UPDATE settings SET value=? WHERE key='observation_start'", (start,))
    estimate = activation.get("estimated_monthly_cost_usd")
    cost = {"target_monthly_usd": 25, "estimated_monthly_usd": estimate,
            "verified_at": activation.get("verified_at"),
            "state": "unverified" if estimate is None else ("over_target" if estimate > 25 else "within_target")}
    db.execute("INSERT OR REPLACE INTO settings VALUES('cost',?)", (json.dumps(cost),))
    db.execute("UPDATE assets SET status='absent'")
    previous_mapping = db.execute("SELECT value FROM settings WHERE key='catalog'").fetchone()
    for slug, row in classifier.assets.items():
        root = next(root for root, name in classifier.roots.items() if name == slug)
        db.execute("""INSERT INTO assets VALUES(?,?,?,?,NULL,NULL,NULL,?,?) ON CONFLICT(slug) DO UPDATE SET
        title=excluded.title,status=excluded.status,mapping_started=CASE WHEN root=excluded.root THEN mapping_started ELSE excluded.mapping_started END,root=excluded.root""",
                   (slug, row.get("title", slug), row["status"], today, first_complete_day, root))
    snapshot = {"assets": classifier.assets, "roots": classifier.roots}
    classifier.catalog_version = digest(snapshot)
    db.execute("INSERT OR REPLACE INTO settings VALUES('catalog',?)", (classifier.catalog_version,))
    db.execute("INSERT OR IGNORE INTO catalogs VALUES(?,?)", (classifier.catalog_version, json.dumps(snapshot)))
    # Retain old roots for legitimate historical paths. A slug rename requires
    # human approval elsewhere; this collector cannot create lifecycle changes.
    for (data,) in db.execute("SELECT payload FROM catalogs"):
        for root, slug in json.loads(data)["roots"].items():
            classifier.roots.setdefault(root, slug)
    db.commit()
    return ((prior is not None and prior[0] != classifier.version)
            or (previous_mapping is not None and previous_mapping[0] != classifier.catalog_version)
            or db.execute("SELECT 1 FROM settings WHERE key='reconciliation_pending' AND value='1'").fetchone() is not None)


def add_event(db, event):
    inserted = db.execute("INSERT OR IGNORE INTO events VALUES(?,?,?)", (event["day"], event["source"], event["id"])).rowcount
    if not inserted:
        return
    db.execute("""INSERT INTO daily VALUES(?,?,?,?,?,?,1,?,?) ON CONFLICT(day,slug,source,kind,app,confidence)
    DO UPDATE SET requests=requests+1,bytes=CASE WHEN excluded.bytes IS NULL THEN bytes ELSE COALESCE(bytes,0)+excluded.bytes END,last_at=MAX(last_at,excluded.last_at)""",
               (event["day"], event["slug"], event["source"], event["kind"], event["application"], event["confidence"], event["bytes"], event["at"]))
    field = {"read": "last_read", "interest": "last_interest", "catalog": "last_catalog"}.get(event["kind"])
    if field:
        db.execute(f"UPDATE assets SET {field}=MAX(COALESCE({field},''),?) WHERE slug=?", (event["at"], event["slug"]))
    if event["probe"] and event["kind"] == "maintenance":
        db.execute("UPDATE probes SET seen=1 WHERE token=? AND source=?", (event["probe"], event["source"]))


COST_COMPONENTS = {"project_logging", "raw_storage", "state_storage", "operations", "worker", "viewer"}
TRAFFIC_CHECKS = {"authenticated_read", "anonymous_download", "cdn_cache_hit", "cached_sdk_interest", "catalog_cache_lookup"}


def cost_evidence_valid(activation):
    evidence = activation.get("evidence")
    if not isinstance(evidence, dict):
        return False
    cost = evidence.get("cost", {})
    components = cost.get("monthly_usd", {})
    estimate = activation.get("estimated_monthly_cost_usd")
    return (type(estimate) in {int, float} and math.isfinite(estimate) and 0 <= estimate <= 25
            and type(cost.get("measurement_days")) in {int, float} and math.isfinite(cost["measurement_days"]) and cost["measurement_days"] >= 1
            and cost.get("project_wide_audit_volume_reviewed") is True
            and cost.get("logging_retention_and_exclusions_reviewed") is True
            and set(components) == COST_COMPONENTS
            and all(type(value) in {int, float} and math.isfinite(value) and value >= 0 for value in components.values())
            and abs(sum(components.values()) - estimate) < 0.01)


def activation_valid(activation, classifier):
    evidence = activation.get("evidence") or {}
    return (activation.get("verified_at") is not None
            and activation.get("configuration_sha256") == classifier.version
            and set(activation.get("verified_sources", [])) == set(SOURCES)
            and cost_evidence_valid(activation)
            and evidence.get("inherited_audit_settings_reviewed") is True
            and evidence.get("existing_sinks_and_exemptions_reviewed") is True
            and all(evidence.get("traffic_checks", {}).get(check) is True for check in TRAFFIC_CHECKS))


def collect(store, classifier, activation, now, directory, *, probe=None, configuration_healthy=False):
    ledger = directory / "ledger.sqlite"
    previous_manifest, generation = store.load(ledger)
    db = sqlite3.connect(ledger)
    try:
        replay = initialize(db, classifier, now, activation)
        errors = []
        valid = activation_valid(activation, classifier) and configuration_healthy
        cutoff = (now - dt.timedelta(hours=48)).date() - dt.timedelta(days=1)
        oldest = (now.date() - dt.timedelta(days=30)).isoformat()
        db.execute("PRAGMA temp_store=FILE")
        db.execute("CREATE TEMP TABLE inventory(name TEXT,generation TEXT,size INTEGER,source TEXT,PRIMARY KEY(name,generation))")
        for index, item in enumerate(store.inputs()):
            if index >= 100000 or len(item["name"]) > 1024:
                raise ValueError("Raw input inventory exceeds its bounded budget")
            db.execute("INSERT OR IGNORE INTO inventory VALUES(?,?,?,?)", (item["name"], item["generation"], item.get("size", 0), item["source"]))
        db.commit()
        rebuilding = False
        if replay:
            assert previous_manifest is not None, "Reconciliation requires a committed ledger reference"
            missing = db.execute("SELECT DISTINCT source FROM inputs WHERE seen_at>=? AND NOT EXISTS(SELECT 1 FROM inventory WHERE inventory.name=inputs.name AND inventory.generation=inputs.generation)", (oldest,)).fetchall()
            if missing:
                # Never erase old totals when their original generations cannot
                # be replayed. Keep original evidence versions and mark unknown.
                for (source,) in missing:
                    mark_gap(db, source, cutoff, "Unavailable input generation prevents policy reconciliation")
                errors.append("IncompleteReconciliation")
            else:
                # Keep all original aggregates and receipts until the complete
                # retained replay succeeds. Per-input success is insufficient
                # when it is replacing previously committed historical evidence.
                db.execute("SAVEPOINT reconciliation")
                rebuilding = True
                db.execute("DELETE FROM daily WHERE day>=?", (oldest,))
                db.execute("DELETE FROM events WHERE day>=?", (oldest,))
                db.execute("DELETE FROM inputs WHERE seen_at>=?", (oldest,))
        probes = {token: {"day": day, "source": source, "slug": slug} for token, day, source, slug in db.execute("SELECT token,day,source,slug FROM probes WHERE day>=?", (oldest,))}
        for name, item_generation, size, source in db.execute("SELECT name,generation,size,source FROM inventory ORDER BY name,generation"):
            item = {"name": name, "generation": item_generation, "size": size, "source": source}
            if db.execute("SELECT 1 FROM inputs WHERE name=? AND generation=?", (item["name"], item["generation"])).fetchone():
                continue
            try:
                db.execute("SAVEPOINT input")
                with store.records(item) as records:
                    for index, raw in enumerate(records):
                        if index % 256 == 0:
                            check_budget(db)
                        event = classifier.event(item["source"], raw, probes)
                        if event is None:
                            continue
                        if event["day"] < oldest:
                            db.execute("INSERT OR REPLACE INTO coverage VALUES(?,?, 'gap','Late input beyond retained deduplication evidence')", (event["day"], event["source"]))
                        elif timestamp(event["at"]) > now + dt.timedelta(minutes=5):
                            raise ValueError("Future activity timestamp")
                        else:
                            add_event(db, event)
                            db.execute("INSERT OR IGNORE INTO evidence VALUES(?,?,?,?)", (event["day"], event["source"], classifier.version, classifier.catalog_version))
                    check_budget(db)
                    db.execute("INSERT INTO inputs VALUES(?,?,?,?)", (item["name"], item["generation"], iso(now), item["source"]))
                db.execute("RELEASE input")
                if not rebuilding:
                    db.commit()
            except (ValueError, KeyError, TypeError, UnicodeError, csv.Error, GoogleAPICallError, OSError) as exc:
                # Publish the failure evidence with the unchanged checkpoint for
                # this input. Never skip the malformed object on the next run.
                db.execute("ROLLBACK TO input")
                db.execute("RELEASE input")
                if rebuilding:
                    db.execute("ROLLBACK TO reconciliation")
                    db.execute("RELEASE reconciliation")
                    rebuilding = False
                errors.append(type(exc).__name__)
                # A malformed shard has unknown event times. Suspend all retained
                # coverage for that source until a reviewed reconciliation.
                mark_gap(db, item["source"], cutoff, "Input parsing or budget failure")
                db.commit()
                break
        if rebuilding:
            db.execute("RELEASE reconciliation")
        if replay:
            db.execute("INSERT OR REPLACE INTO settings VALUES('reconciliation_pending',?)", ('1' if errors else '0',))
            db.execute("INSERT INTO reconciliations VALUES(?,?,?,?,?)", (iso(now), oldest, classifier.version, classifier.catalog_version, previous_manifest["ledger"]["sha256"]))
            db.commit()
        last_run = db.execute("SELECT value FROM settings WHERE key='last_run'").fetchone()
        if last_run and (now - timestamp(last_run[0])).days >= 30:
            for source in SOURCES:
                mark_gap(db, source, cutoff, "Worker outage exceeds raw evidence retention")
        begin = dt.date.fromisoformat(db.execute("SELECT value FROM settings WHERE key='observation_start'").fetchone()[0])
        begin = max(begin, cutoff - dt.timedelta(days=459))
        for offset in range(max(0, (cutoff - begin).days + 1)):
            day = (begin + dt.timedelta(days=offset)).isoformat()
            for source in SOURCES:
                if db.execute("SELECT 1 FROM coverage WHERE day=? AND source=? AND state='gap'", (day, source)).fetchone():
                    continue
                seen = db.execute("SELECT MAX(seen) FROM probes WHERE day=? AND source=? AND trusted=1 AND policy=?", (day, source, classifier.version)).fetchone()[0]
                prior = db.execute("SELECT state FROM coverage WHERE day=? AND source=?", (day, source)).fetchone()
                healthy = seen == 1 or (prior and prior[0] == "healthy")
                reason = "No known gaps; probe observed" if healthy else "Missing probe, unverified configuration, or cost gate"
                db.execute("INSERT OR REPLACE INTO coverage VALUES(?,?,?,?)", (day, source, "healthy" if healthy else "pending", reason))
        if not valid:
            for source in SOURCES:
                for offset in range(5):
                    db.execute("INSERT OR REPLACE INTO coverage VALUES(?,?,'gap','Unverified live collection configuration or cost gate')", ((now.date()-dt.timedelta(days=offset)).isoformat(), source))
        if probe is not None:
            for source in SOURCES:
                token = "usage-probe-" + uuid.uuid4().hex
                slug = probe(source, token)
                db.execute("INSERT INTO probes VALUES(?,?,?,?,?,?,0)", (token, now.date().isoformat(), source, slug, classifier.version, int(valid)))
        keep = (now.date() - dt.timedelta(days=460)).isoformat()
        db.execute("DELETE FROM events WHERE day < ?", (oldest,))
        db.execute("DELETE FROM probes WHERE day < ?", (keep,))
        db.execute("DELETE FROM inputs WHERE seen_at < ?", (keep,))
        db.execute("DELETE FROM daily WHERE day < ?", (keep,))
        db.execute("DELETE FROM coverage WHERE day < ?", (keep,))
        db.execute("DELETE FROM evidence WHERE day < ?", (keep,))
        db.execute("DELETE FROM reconciliations WHERE at < ?", (keep,))
        db.execute("INSERT OR REPLACE INTO settings VALUES('last_run',?)", (iso(now),))
        db.commit()
        payload = report(db, now)
        payload["collection_errors"] = errors
        db.execute("VACUUM")
        db.close()
        if ledger.stat().st_size > MAX_LEDGER_BYTES:
            raise ValueError("Usage ledger exceeds its bounded disk budget")
        manifest = store.publish(ledger, payload, generation)
        if errors:
            raise ValueError("Usage collection failed; explicit gap published")
        return manifest
    finally:
        db.close()


def check_budget(db):
    size = db.execute("PRAGMA page_count").fetchone()[0] * db.execute("PRAGMA page_size").fetchone()[0]
    if size > MAX_LEDGER_BYTES:
        raise ValueError("Usage ledger exceeds its bounded disk budget")


def mark_gap(db, source, cutoff, reason):
    for offset in range(460):
        db.execute("INSERT OR REPLACE INTO coverage VALUES(?,?,'gap',?)", ((cutoff - dt.timedelta(days=offset)).isoformat(), source, reason))


def work_root():
    return Path(os.environ.get("SHARED_DATASETS_WORKDIR", str(Path(tempfile.gettempdir()) / "shared-datasets-1"))) / "dataset-usage"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="catalog/dataset-usage.json")
    parser.add_argument("--activation", default="catalog/dataset-usage-activation.json")
    parser.add_argument("--work-dir", type=Path, default=work_root())
    parser.add_argument("--print-policy-hash", action="store_true")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    activation = json.loads(Path(args.activation).read_text())
    bucket = os.environ.get("SHARED_DATASETS_BUCKET", "skytruth-shared-datasets-1")
    catalog = read_catalog_rows_text(Path("catalog/shared-datasets-catalog.csv").read_text())
    classifier = Classifier(catalog, config, bucket)
    if args.print_policy_hash:
        print(classifier.version)
        return
    from ingestion.dataset_usage.health import Health
    from google.cloud import storage
    client = storage.Client()
    # Read the current published control-plane snapshot each day. An old worker
    # image must not keep a deprecated asset eligible or miss newly mapped roots.
    catalog_blob = client.bucket(bucket).blob("_catalog/shared-datasets-catalog.csv")
    catalog_blob.reload(timeout=30)
    if not 0 < catalog_blob.size <= 4 * 1024 * 1024:
        raise ValueError("Published catalog exceeds its bounded budget")
    catalog_bytes = catalog_blob.download_as_bytes(if_generation_match=int(catalog_blob.generation), timeout=30)
    if len(catalog_bytes) != catalog_blob.size:
        raise ValueError("Published catalog size mismatch")
    classifier = Classifier(read_catalog_rows_text(catalog_bytes.decode()), config, bucket)
    store = GcsStore(os.environ["DATASET_USAGE_RAW_BUCKET"], os.environ["DATASET_USAGE_STATE_BUCKET"], client)
    health = Health(classifier, store.client, os.environ["GOOGLE_CLOUD_PROJECT"], os.environ["DATASET_USAGE_RAW_BUCKET"])
    args.work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run-", dir=args.work_dir) as directory:
        manifest = collect(store, classifier, activation, dt.datetime.now(dt.UTC), Path(directory), probe=health.probe, configuration_healthy=health.configuration_healthy())
        print(json.dumps({"event": "dataset_usage_published", "generated_at": manifest["generated_at"]}))


if __name__ == "__main__":
    main()
