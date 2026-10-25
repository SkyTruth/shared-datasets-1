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

from ingestion.dataset_usage.model import Classifier, SOURCES, VERSION, MANIFEST_VERSION, RETIRED_SOURCES, digest, iso, report, timestamp
from ingestion.dataset_usage.stream import text_stream, usage_records
from scripts.catalog_csv import read_catalog_rows_text

MAX_LEDGER_BYTES = 128 * 1024 * 1024
MAX_REPORT_BYTES = 8 * 1024 * 1024
MANIFEST = "published/manifest.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS retired_sources(source TEXT PRIMARY KEY,retired_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assets(slug TEXT PRIMARY KEY,title TEXT,status TEXT,registered TEXT,last_read TEXT,last_interest TEXT,last_catalog TEXT,root TEXT);
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
        if manifest["schema_version"] != MANIFEST_VERSION:
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
        for blob in self.client.list_blobs(self.raw, prefix="storage-usage", page_size=256):
            if blob.name.startswith("storage-usage") and "_usage_" in blob.name:
                source = "gcs_usage"
            else:
                continue
            yield {"name": blob.name, "generation": str(blob.generation), "size": blob.size, "source": source}

    @contextlib.contextmanager
    def records(self, item):
        blob = self.raw.blob(item["name"], generation=int(item["generation"]))
        with blob.open("rb", chunk_size=1024 * 1024, if_generation_match=int(item["generation"]), timeout=30) as binary:
            with text_stream(binary) as stream:
                yield usage_records(stream)

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
        manifest = {"schema_version": MANIFEST_VERSION, "generated_at": payload["generated_at"], **refs}
        blob = self.state.blob(MANIFEST)
        blob.cache_control = "no-store"
        blob.upload_from_string(json.dumps(manifest), content_type="application/json", if_generation_match=generation, timeout=30)
        return manifest


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def initialize(db, classifier, now, activation):
    existed = db.execute("SELECT 1 FROM sqlite_master WHERE name='settings'").fetchone() is not None
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, 2, VERSION):
        raise ValueError("Unsupported ledger schema")
    migrated = existed and version == 0
    db.executescript(SCHEMA)
    if migrated:
        # Fixed-width UTC timestamps make SQL MAX chronological. Preserve the
        # represented instants and the existing request-ID hash serialization.
        for table, fields in (("assets", ("last_read", "last_interest", "last_catalog")), ("daily", ("last_at",))):
            after = 0
            while rows := db.execute(f"SELECT rowid,{','.join(fields)} FROM {table} WHERE rowid>? ORDER BY rowid LIMIT 256", (after,)).fetchall():
                for row in rows:
                    values = [iso(timestamp(value)) if value else value for value in row[1:]]
                    db.execute(f"UPDATE {table} SET {','.join(field + '=?' for field in fields)} WHERE rowid=?", (*values, row[0]))
                after = rows[-1][0]
    for source in RETIRED_SOURCES:
        db.execute("INSERT OR IGNORE INTO retired_sources VALUES(?,?)", (source, iso(now)))
    db.execute(f"PRAGMA user_version={VERSION}")
    db.execute("INSERT OR REPLACE INTO settings VALUES('configuration',?)", (json.dumps(classifier.config),))
    db.execute("PRAGMA cache_size=-8192")
    db.execute("PRAGMA journal_mode=DELETE")
    today = now.date().isoformat()
    prior = db.execute("SELECT value FROM settings WHERE key='policy'").fetchone()
    if prior is None or prior[0] != classifier.version:
        db.execute("INSERT OR REPLACE INTO settings VALUES('policy',?)", (classifier.version,))
        db.execute("DELETE FROM coverage WHERE day>=? AND source='gcs_usage'", (today,))
    estimate = activation.get("estimated_monthly_cost_usd")
    cost = {"target_monthly_usd": 25, "estimated_monthly_usd": estimate,
            "verified_at": activation.get("verified_at"),
            "state": "unverified" if estimate is None else ("over_target" if estimate > 25 else "within_target")}
    db.execute("INSERT OR REPLACE INTO settings VALUES('cost',?)", (json.dumps(cost),))
    db.execute("UPDATE assets SET status='absent'")
    previous_mapping = db.execute("SELECT value FROM settings WHERE key='catalog'").fetchone()
    for slug, row in classifier.assets.items():
        root = next(root for root, name in classifier.roots.items() if name == slug)
        # Named columns also accept existing ledgers with extra unused columns.
        db.execute("""INSERT INTO assets(slug,title,status,registered,root) VALUES(?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET
        title=excluded.title,status=excluded.status,root=excluded.root""",
                   (slug, row.get("title", slug), row["status"], today, root))
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
    return not migrated and ((prior is not None and prior[0] != classifier.version)
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


COST_COMPONENTS = {"incremental_logging", "raw_storage", "state_storage", "operations", "worker", "viewer"}
TRAFFIC_CHECKS = {
    "gcs_usage": {"authenticated_read", "anonymous_download", "raw_delivery"},
}


def cost_evidence_valid(activation):
    evidence = activation.get("evidence")
    if not isinstance(evidence, dict):
        return False
    cost = evidence.get("cost", {})
    components = cost.get("monthly_usd", {})
    estimate = activation.get("estimated_monthly_cost_usd")
    return (type(estimate) in {int, float} and math.isfinite(estimate) and 0 <= estimate <= 25
            and type(cost.get("measurement_days")) in {int, float} and math.isfinite(cost["measurement_days"]) and cost["measurement_days"] >= 1
            and cost.get("scope") == "tracker_incremental"
            and set(components) == COST_COMPONENTS
            and all(type(value) in {int, float} and math.isfinite(value) and value >= 0 for value in components.values())
            and abs(sum(components.values()) - estimate) < 0.01)


def activation_valid(activation, classifier, source=None):
    if not (activation.get("schema_version") == VERSION and
            activation.get("configuration_sha256") == classifier.version and cost_evidence_valid(activation)):
        return False
    if source is None:
        return any(activation_valid(activation, classifier, item) for item in SOURCES)
    entry = activation.get("sources", {}).get(source, {})
    return (entry.get("verified_at") is not None and
            all(entry.get("checks", {}).get(check) is True for check in TRAFFIC_CHECKS[source]))


def usage_log_before(name, day):
    # Native hourly logs may contain earlier requests, never later ones.
    # An unknown name cannot prove that a missing generation is outside replay.
    match = re.fullmatch(r"storage-usage_usage_(\d{4}(?:_\d{2}){5})_[a-zA-Z0-9]+_v0", name)
    return bool(match and dt.datetime.strptime(match[1], "%Y_%m_%d_%H_%M_%S").date().isoformat() < day)


def collect(store, classifier, activation, now, directory, *, probe=None, configuration_healthy=False):
    ledger = directory / "ledger.sqlite"
    previous_manifest, generation = store.load(ledger)
    db = sqlite3.connect(ledger)
    db.create_function("usage_log_before", 2, usage_log_before)
    try:
        replay = initialize(db, classifier, now, activation)
        errors = []
        live = dict.fromkeys(SOURCES, configuration_healthy) if isinstance(configuration_healthy, bool) else configuration_healthy
        valid = {source: activation_valid(activation, classifier, source) and live.get(source, False)
                 and classifier.config.get("collection_enabled", False) for source in SOURCES}
        cutoff = (now - dt.timedelta(hours=48)).date() - dt.timedelta(days=1)
        retention_days = classifier.config["raw_retention_days"]
        oldest = (now.date() - dt.timedelta(days=retention_days)).isoformat()
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
            missing = db.execute("SELECT DISTINCT source FROM inputs WHERE source='gcs_usage' AND seen_at>=? AND NOT usage_log_before(name, ?) AND NOT EXISTS(SELECT 1 FROM inventory WHERE inventory.name=inputs.name AND inventory.generation=inputs.generation)", (oldest, oldest)).fetchall()
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
                db.execute("DELETE FROM daily WHERE source='gcs_usage' AND day>=?", (oldest,))
                db.execute("DELETE FROM events WHERE source='gcs_usage' AND day>=?", (oldest,))
                db.execute("DELETE FROM inputs WHERE source='gcs_usage' AND seen_at>=? AND NOT usage_log_before(name, ?)", (oldest, oldest))
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
                        event = classifier.event(item["source"], raw, probes, name=item["name"])
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
            except (ValueError, KeyError, TypeError, OverflowError, RecursionError, UnicodeError, csv.Error, GoogleAPICallError, OSError) as exc:
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
                if replay:
                    break
        if rebuilding:
            db.execute("RELEASE reconciliation")
        if replay:
            db.execute("INSERT OR REPLACE INTO settings VALUES('reconciliation_pending',?)", ('1' if errors else '0',))
            db.execute("INSERT INTO reconciliations VALUES(?,?,?,?,?)", (iso(now), oldest, classifier.version, classifier.catalog_version, previous_manifest["ledger"]["sha256"]))
            db.commit()
        last_run = db.execute("SELECT value FROM settings WHERE key='last_run'").fetchone()
        if last_run and (now - timestamp(last_run[0])).days >= retention_days:
            for source in SOURCES:
                mark_gap(db, source, cutoff, "Worker outage exceeds raw evidence retention")
        # Delivered probes establish pipeline health, independently of asset age.
        # Missing days between probes remain pending; earlier days stay unknown.
        first_days = {
            "gcs_usage": db.execute("SELECT MIN(day) FROM probes WHERE source='gcs_usage'").fetchone()[0],
        }
        first_probe = min((day for day in first_days.values() if day is not None), default=None)
        begin = max(dt.date.fromisoformat(first_probe), cutoff - dt.timedelta(days=459)) if first_probe else cutoff + dt.timedelta(days=1)
        for offset in range(max(0, (cutoff - begin).days + 1)):
            day = (begin + dt.timedelta(days=offset)).isoformat()
            for source in SOURCES:
                if first_days[source] is None or day < first_days[source]:
                    continue
                if db.execute("SELECT 1 FROM coverage WHERE day=? AND source=? AND state='gap'", (day, source)).fetchone():
                    continue
                seen = db.execute("SELECT MAX(seen) FROM probes WHERE day=? AND source=? AND trusted=1 AND policy=?", (day, source, classifier.version)).fetchone()[0]
                prior = db.execute("SELECT state FROM coverage WHERE day=? AND source=?", (day, source)).fetchone()
                healthy = seen == 1 or (prior and prior[0] == "healthy")
                reason = "No known gaps; probe observed" if healthy else "Missing probe, unverified configuration, or cost gate"
                db.execute("INSERT OR REPLACE INTO coverage VALUES(?,?,?,?)", (day, source, "healthy" if healthy else "pending", reason))
        for source in SOURCES:
            if not valid[source]:
                for offset in range(5):
                    db.execute("INSERT OR REPLACE INTO coverage VALUES(?,?,'gap','Unverified source configuration or incremental cost gate')", ((now.date()-dt.timedelta(days=offset)).isoformat(), source))
        if probe is not None and classifier.config.get("collection_enabled", False):
            token = "usage-probe-" + uuid.uuid4().hex
            slug = probe("gcs_usage", token)
            db.execute("INSERT INTO probes VALUES(?,?,?,?,?,?,0)", (token, now.date().isoformat(), "gcs_usage", slug, classifier.version, int(valid["gcs_usage"])))
        keep = (now.date() - dt.timedelta(days=460)).isoformat()
        db.execute("DELETE FROM events WHERE day < ?", (oldest,))
        db.execute("DELETE FROM probes WHERE day < ?", (keep,))
        db.execute("DELETE FROM inputs WHERE seen_at < ?", (keep,))
        db.execute("DELETE FROM daily WHERE day < ?", (keep,))
        # Version-2 ledgers retain retired application aggregates under the
        # original retention policy; fresh passive ledgers have no such table.
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_daily'").fetchone():
            db.execute("DELETE FROM app_daily WHERE day < ?", (keep,))
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
    # image must not show obsolete lifecycle status or miss newly mapped roots.
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
