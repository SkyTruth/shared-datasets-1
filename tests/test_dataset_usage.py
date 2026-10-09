from __future__ import annotations

import contextlib
import copy
import csv
import datetime as dt
import io
import json
import shutil
import sqlite3
import tracemalloc

import pytest

from ingestion.dataset_usage import run
from ingestion.dataset_usage.model import Classifier, SOURCES, iso, report
from ingestion.dataset_usage.stream import json_records, usage_records
from services.catalog_viewer import usage

NOW = dt.datetime(2026, 10, 7, 9, tzinfo=dt.UTC)
BUCKET = "shared"
ROWS = [{"asset_slug": "example", "title": "Example", "status": "active", "canonical_path": "gs://shared/category/example/latest/example.csv", "access_tier": "public"}]
CONFIG = {"raw_retention_days": 7, "cdn_host": "tiles.example.org", "principals": {"reader@example.org": "Application group"}, "referrers": {"app.example.org": "Inferred app"}, "maintenance_principals": ["worker@example.org"], "catalog_principals": ["viewer@example.org"]}


def classifier():
    return Classifier(ROWS, copy.deepcopy(CONFIG), BUCKET)


@pytest.mark.parametrize('days', [None, True, 2, 6, 31, 7.0])
def test_raw_retention_rejects_policies_without_delivery_and_recovery_room(days):
    config = {**CONFIG, 'raw_retention_days': days}
    with pytest.raises(ValueError, match='Raw retention'):
        Classifier(ROWS, config, BUCKET)


def activation(c):
    return {"verified_at": iso(NOW - dt.timedelta(days=400)), "configuration_sha256": c.version, "verified_sources": list(SOURCES), "estimated_monthly_cost_usd": 10,
            "evidence": {"inherited_audit_settings_reviewed": True, "existing_sinks_and_exemptions_reviewed": True,
                         "traffic_checks": dict.fromkeys(run.TRAFFIC_CHECKS, True),
                         "cost": {"measurement_days": 7, "project_wide_audit_volume_reviewed": True, "logging_retention_and_exclusions_reviewed": True,
                                  "monthly_usd": {key: (10 if key == "project_logging" else 0) for key in run.COST_COMPONENTS}}}}


def raw(path="category/example/latest/example.csv", *, method="GET", status=200, identity="one", when=NOW):
    return {"cs_bucket": BUCKET, "cs_object": path, "cs_uri": "/shared/" + path,
            "time_micros": str(int(when.timestamp() * 1_000_000)), "s_request_id": identity,
            "cs_method": method, "sc_status": str(status), "sc_bytes": "23"}


@pytest.mark.parametrize("path,method,status,kind", [
    ("category/example/latest/example.csv", "GET", 206, "read"),
    ("_catalog/releases/example.json", "GET", 200, "interest"),
    ("category/example/releases/2026-10-01/example.metadata.ndjson.gz", "HEAD", 200, "interest"),
    ("category/example/latest/example.csv", "GET", 304, "interest"),
    ("category/example/latest/example.csv", "GET", 403, "failed"),
])
def test_classification_preserves_cached_sdk_interest(path, method, status, kind):
    event = classifier().event("gcs_usage", raw(path, method=method, status=status), {})
    assert event["kind"] == kind
    assert event["confidence"] == "unknown"


def test_inferred_referrer_and_maintenance_user_agent_never_hide_activity():
    entry = raw()
    entry.update(cs_referer="https://app.example.org/secret?token=private", cs_user_agent="maintenance catalog crawler")
    event = classifier().event("gcs_usage", entry, {})
    assert (event["kind"], event["application"], event["confidence"]) == ("read", "Inferred app", "inferred")
    assert "private" not in json.dumps(event)


def test_audit_is_conservative_interest_with_verified_group_attribution():
    entry = {"timestamp": iso(NOW), "insertId": "a", "protoPayload": {"serviceName": "storage.googleapis.com", "methodName": "storage.objects.get", "resourceName": "projects/_/buckets/shared/objects/category/example/latest/example.csv", "authenticationInfo": {"principalEmail": "reader@example.org"}}}
    event = classifier().event("gcs_audit", entry, {})
    assert (event["kind"], event["application"], event["bytes"]) == ("interest", "Application group", None)
    entry["protoPayload"]["authenticationInfo"]["principalEmail"] = "viewer@example.org"
    assert classifier().event("gcs_audit", entry, {})["kind"] == "catalog"


def test_exact_nonce_excludes_only_successful_source_and_asset_probe():
    token = "usage-probe-" + "a" * 32
    entry = raw()
    entry["cs_uri"] += "?probe=" + token
    probes = {token: {"source": "gcs_usage", "slug": "example"}}
    assert classifier().event("gcs_usage", entry, probes)["kind"] == "maintenance"
    assert classifier().event("gcs_usage", entry, {token: {"source": "catalog", "slug": "example"}})["kind"] == "read"
    entry["sc_status"] = "403"
    assert classifier().event("gcs_usage", entry, probes)["kind"] == "failed"


def test_catalog_event_requires_platform_identity():
    entry = {"timestamp": iso(NOW), "insertId": "a", "jsonPayload": {"event": "dataset_usage", "asset_slug": "example"}, "resource": {"type": "cloud_run_revision", "labels": {"service_name": "catalog-viewer"}}}
    assert classifier().event("catalog", entry, {})["kind"] == "catalog"
    entry["resource"]["labels"]["service_name"] = "other"
    assert classifier().event("catalog", entry, {}) is None


@pytest.mark.parametrize("path", ["_catalog/catalog.json", "category/example/README.md", "category/example/runs/one.json", "storage-usage_usage_2026.csv"])
def test_general_catalog_and_non_dataset_paths_ignored(path):
    entry = raw(path)
    entry.pop("s_request_id")
    assert classifier().event("gcs_usage", entry, {}) is None


@pytest.mark.parametrize("data", ['[{"x":1},{"x":2}]', '{"x":1}\n{"x":2}\n'])
def test_streaming_json(data):
    assert list(json_records(io.StringIO(data))) == [{"x": 1}, {"x": 2}]


@pytest.mark.parametrize("data", ['[{"x":1},]', '[{"x":1}{"x":2}]', '[{"x":1}', '{"x":1}{"x":2}', '[1]', '[{}] garbage'])
def test_malformed_stream_fails(data):
    with pytest.raises(ValueError):
        list(json_records(io.StringIO(data)))


def test_large_shard_stream_has_bounded_memory():
    class Shard:
        remaining = 300_000
        def read(self, size):
            assert 0 < size <= 65536
            count = min(self.remaining, size // 9)
            self.remaining -= count
            return '{"a":1}\n' * count
    tracemalloc.start()
    assert sum(1 for _ in json_records(Shard())) == 300_000
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 3 * 1024 * 1024
    with pytest.raises(ValueError, match="budget"):
        list(json_records(io.StringIO('{"x":"' + "x" * 1000 + '"}'), limit=100))


def test_csv_header_forward_compatible_and_record_bounded():
    header = 'time_micros,cs_bucket,cs_method,cs_uri,sc_status,s_request_id,new_optional\n'
    assert list(usage_records(io.StringIO(header+'1,shared,GET,/shared/file,200,a,extra\n')))[0]['new_optional'] == 'extra'
    with pytest.raises((ValueError, csv.Error)):
        list(usage_records(io.StringIO('x\n' + 'x' * 600000)))


class Store:
    def __init__(self, root):
        self.root = root
        self.items = []
        self.generation = 0
        self.payload = None
        self.fail = False
    def load(self, destination):
        if self.generation:
            shutil.copyfile(self.root / "committed.sqlite", destination)
        manifest = {"ledger": {"sha256": run.file_digest(self.root / "committed.sqlite")}} if self.generation else None
        return manifest, self.generation
    def inputs(self):
        yield from self.items
    @contextlib.contextmanager
    def records(self, item):
        original = next(row for row in self.items if row["name"] == item["name"] and row["generation"] == item["generation"])
        yield iter(original["records"])
    def publish(self, ledger, payload, generation):
        if self.fail or generation != self.generation:
            raise RuntimeError("publication interrupted or concurrent writer")
        shutil.copyfile(ledger, self.root / "committed.sqlite")
        self.payload = payload
        self.generation += 1
        return {"generated_at": payload["generated_at"]}


def execute(store, tmp_path, when=NOW, **kwargs):
    directory = tmp_path / f"run-{len(list(tmp_path.iterdir()))}"
    directory.mkdir()
    c = classifier()
    return run.collect(store, c, activation(c), when, directory, configuration_healthy=True, **kwargs)


def test_duplicate_generations_and_late_shards_count_once(tmp_path):
    store = Store(tmp_path)
    store.items = [{"name": "a", "generation": "1", "source": "gcs_usage", "records": [raw(), raw()]}]
    execute(store, tmp_path)
    store.items.append({"name": "late", "generation": "2", "source": "gcs_usage", "records": [raw(), raw(identity="two", when=NOW-dt.timedelta(days=5))]})
    execute(store, tmp_path)
    groups = store.payload["assets"][0]["windows"]["30"]
    assert sum(g["requests"] for g in groups) == 2
    assert sum(g["active_days"] for g in groups) == 2


def test_expired_request_ids_leave_aggregates_receipts_and_last_access_intact(tmp_path):
    store = Store(tmp_path)
    store.items = [{'name': 'a', 'generation': '1', 'source': 'gcs_usage', 'records': [raw()]}]
    execute(store, tmp_path)
    store.items = []
    execute(store, tmp_path, NOW + dt.timedelta(days=8))
    asset = store.payload['assets'][0]
    assert asset['last_read'] == iso(NOW)
    assert sum(group['requests'] for group in asset['windows']['30']) == 1
    with sqlite3.connect(tmp_path / 'committed.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM inputs').fetchone()[0] == 1
        assert db.execute('SELECT SUM(requests) FROM daily').fetchone()[0] == 1
    execute(store, tmp_path, NOW + dt.timedelta(days=461))
    assert store.payload['assets'][0]['last_read'] == iso(NOW)
    with sqlite3.connect(tmp_path / 'committed.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM daily').fetchone()[0] == 0


@pytest.mark.parametrize('days,expired', [(6, False), (7, True)])
def test_outage_boundary_uses_the_reviewed_raw_retention(tmp_path, days, expired):
    store = Store(tmp_path)
    execute(store, tmp_path)
    execute(store, tmp_path, NOW + dt.timedelta(days=days))
    with sqlite3.connect(tmp_path / 'committed.sqlite') as db:
        gaps = db.execute("SELECT COUNT(*) FROM coverage WHERE reason='Worker outage exceeds raw evidence retention'").fetchone()[0]
        assert bool(gaps) is expired


def test_crash_before_publish_does_not_advance_checkpoint(tmp_path):
    store = Store(tmp_path)
    store.items = [{"name": "a", "generation": "1", "source": "gcs_usage", "records": [raw()]}]
    store.fail = True
    with pytest.raises(RuntimeError):
        execute(store, tmp_path)
    assert store.generation == 0
    store.fail = False
    execute(store, tmp_path)
    execute(store, tmp_path)
    assert store.payload["assets"][0]["windows"]["30"][0]["requests"] == 1


def test_malformed_input_rolls_back_events_and_receipt_with_explicit_gap(tmp_path):
    store = Store(tmp_path)
    store.items = [{"name": "bad", "generation": "1", "source": "gcs_usage", "records": [raw(), {"cs_bucket": "shared"}]}]
    with pytest.raises(ValueError, match="explicit gap"):
        execute(store, tmp_path)
    with sqlite3.connect(tmp_path / "committed.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM inputs").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM coverage WHERE state='gap'").fetchone()[0] == 458


def test_unverified_probes_cannot_retroactively_verify_history(tmp_path):
    db = sqlite3.connect(tmp_path / "ledger.sqlite")
    c = classifier()
    run.initialize(db, c, NOW, {})
    assert db.execute("SELECT value FROM settings WHERE key='observation_start'").fetchone()[0] == "9999-12-31"
    run.initialize(db, c, NOW + dt.timedelta(days=10), activation(c))
    assert db.execute("SELECT value FROM settings WHERE key='observation_start'").fetchone()[0] == "2026-10-18"


def seeded(tmp_path, days=365):
    db = sqlite3.connect(tmp_path / "ledger.sqlite")
    c = classifier()
    completed = (NOW-dt.timedelta(hours=48)).date()-dt.timedelta(days=1)
    start = completed-dt.timedelta(days=days-1)
    run.initialize(db, c, dt.datetime.combine(start-dt.timedelta(days=1), dt.time(9), dt.UTC), activation(c))
    for offset in range(days):
        for source in SOURCES:
            db.execute("INSERT INTO coverage VALUES(?,?, 'healthy','probe verified')", ((start+dt.timedelta(days=offset)).isoformat(), source))
    return db


@pytest.mark.parametrize("days,state", [(364, "collecting_history"), (365, "qualified_candidate")])
def test_365_fully_elapsed_days_boundary(tmp_path, days, state):
    db = seeded(tmp_path, days)
    assert report(db, NOW)["assets"][0]["state"] == state
    assert report(db, NOW)["observed_through"] == "2026-10-04"


def test_unknown_interest_gaps_policy_changes_new_assets_and_stale_block_candidate(tmp_path):
    db = seeded(tmp_path)
    run.add_event(db, classifier().event("gcs_usage", raw("_catalog/releases/example.json"), {}))
    assert report(db, NOW)["assets"][0]["state"] == "activity_observed"
    db.execute("UPDATE assets SET last_interest=NULL")
    db.execute("UPDATE coverage SET state='gap' WHERE source='cdn'")
    assert report(db, NOW)["assets"][0]["state"] == "coverage_incomplete"
    assert report(db, NOW, iso(NOW-dt.timedelta(days=3)))["assets"][0]["state"] == "stale"
    c = classifier()
    c.version = "new-policy"
    run.initialize(db, c, NOW, activation(c))
    assert report(db, NOW)["assets"][0]["state"] == "collecting_history"
    run.initialize(db, Classifier([], CONFIG, BUCKET), NOW, {})
    assert report(db, NOW)["assets"][0]["state"] == "not_active"


@pytest.mark.parametrize("headers,status", [({}, 401), ({"X-Goog-Authenticated-User-Email": "accounts.google.com:person@other.org"}, 403)])
def test_private_usage_direct_endpoint_requires_iap(headers, status):
    response = usage.handle("GET", "/api/usage", headers, reader=None, domains=("example.org",), now=NOW)
    assert response.status == status
    assert response.headers["Cache-Control"] == "no-store"


def test_private_endpoint_missing_summary_and_independent_staleness():
    headers = {"X-Goog-Authenticated-User-Email": "accounts.google.com:person@example.org"}
    assert usage.handle("GET", "/api/usage", headers, reader=None, domains=("example.org",), now=NOW).status == 503
    class Reader:
        def read(self):
            return {"generated_at": iso(NOW-dt.timedelta(days=3)), "stale": False, "assets": [{"state": "qualified_candidate"}]}
    response = usage.handle("GET", "/api/usage", headers, reader=Reader(), domains=("example.org",), now=NOW)
    assert json.loads(response.body)["assets"][0]["state"] == "stale"
    assert usage.handle("GET", "/usage/../secrets", headers, reader=None, domains=("example.org",), now=NOW).status == 404


def test_budget_failure_rolls_back_processing_state(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.items = [{"name": "a", "generation": "1", "source": "gcs_usage", "records": [raw()]}]
    def budget(db):
        if db.execute("SELECT COUNT(*) FROM events").fetchone()[0]:
            raise ValueError("bounded budget")
    monkeypatch.setattr(run, "check_budget", budget)
    with pytest.raises(ValueError, match="explicit gap"):
        execute(store, tmp_path)
    with sqlite3.connect(tmp_path / "committed.sqlite") as db:
        assert not db.execute("SELECT * FROM inputs").fetchall()
        assert not db.execute("SELECT * FROM daily").fetchall()


@pytest.mark.parametrize('days', [8, 40])
def test_expired_late_input_is_unknown_instead_of_estimated(tmp_path, days):
    store = Store(tmp_path)
    store.items = [{"name": "old", "generation": "1", "source": "gcs_usage", "records": [raw(when=NOW-dt.timedelta(days=days))]}]
    execute(store, tmp_path)
    with sqlite3.connect(tmp_path / "committed.sqlite") as db:
        assert not db.execute("SELECT * FROM daily").fetchall()
        assert db.execute("SELECT state FROM coverage WHERE source='gcs_usage'").fetchone()[0] == "gap"


class Blob:
    def __init__(self, bucket, name, generation=None):
        self.bucket, self.name, self.generation = bucket, name, generation
    def upload_from_filename(self, path, **kwargs):
        self.upload_from_string(open(path, 'rb').read(), **kwargs)
    def upload_from_string(self, data, *, if_generation_match, **kwargs):
        from google.api_core.exceptions import PreconditionFailed
        previous = self.bucket.objects.get(self.name)
        if if_generation_match != (previous[0] if previous else 0):
            raise PreconditionFailed('concurrent publication')
        if self.bucket.fail_at == self.name.split('/')[0]:
            raise OSError('interrupted upload')
        self.bucket.counter += 1
        self.generation = self.bucket.counter
        self.bucket.objects[self.name] = (self.generation, data.encode() if isinstance(data, str) else data)
        if self.name == run.MANIFEST and self.bucket.lost_ack:
            self.bucket.lost_ack = False
            raise OSError('response lost after committed publication')
    def download_as_bytes(self, **kwargs):
        from google.api_core.exceptions import NotFound
        if self.name not in self.bucket.objects:
            raise NotFound('absent')
        generation, data = self.bucket.objects[self.name]
        if self.generation is not None and generation != self.generation:
            raise OSError('generation mismatch')
        self.generation = generation
        return data
    def reload(self, **kwargs):
        self.size = len(self.download_as_bytes())
    def download_to_filename(self, path, **kwargs):
        with open(path, 'wb') as stream:
            stream.write(self.download_as_bytes())


class Bucket:
    def __init__(self):
        self.objects = {}
        self.counter = 0
        self.fail_at = None
        self.lost_ack = False
    def blob(self, name, generation=None):
        return Blob(self, name, generation)


def gcs_store():
    state = Bucket()
    class Client:
        def bucket(self, name):
            return state
    return run.GcsStore('raw', 'state', Client()), state


@pytest.mark.parametrize('failure', ['state', 'published'])
def test_immutable_upload_failure_keeps_previous_manifest(tmp_path, failure):
    store, state = gcs_store()
    ledger = tmp_path/'ledger.sqlite'
    ledger.write_bytes(b'checkpoint one')
    payload = {'schema_version': 1, 'generated_at': iso(NOW), 'assets': []}
    first = store.publish(ledger, payload, 0)
    previous = state.objects[run.MANIFEST]
    state.fail_at = failure
    ledger.write_bytes(b'checkpoint two')
    with pytest.raises(OSError):
        store.publish(ledger, payload, previous[0])
    assert state.objects[run.MANIFEST] == previous
    assert usage.ReportReader('state', store.client).read() == payload
    assert first['ledger']['sha256'] != run.file_digest(ledger)


def test_manifest_cas_and_lost_ack_recover_committed_checkpoint(tmp_path):
    from google.api_core.exceptions import PreconditionFailed
    store, state = gcs_store()
    ledger = tmp_path/'ledger.sqlite'
    ledger.write_bytes(b'committed checkpoint')
    payload = {'schema_version': 1, 'generated_at': iso(NOW), 'assets': []}
    state.lost_ack = True
    with pytest.raises(OSError, match='response lost'):
        store.publish(ledger, payload, 0)
    recovered = tmp_path/'recovered.sqlite'
    manifest, generation = store.load(recovered)
    assert recovered.read_bytes() == ledger.read_bytes()
    with pytest.raises(PreconditionFailed):
        store.publish(ledger, payload, 0)
    assert json.loads(state.objects[run.MANIFEST][1]) == manifest
    assert generation == state.objects[run.MANIFEST][0]


def test_viewer_checks_report_integrity_and_cannot_follow_ledger_reference(tmp_path):
    store, state = gcs_store()
    ledger = tmp_path/'ledger.sqlite'
    ledger.write_bytes(b'checkpoint')
    manifest = store.publish(ledger, {'schema_version': 1, 'generated_at': iso(NOW), 'assets': []}, 0)
    state.objects[manifest['report']['path']] = (int(manifest['report']['generation']), b'corrupt')
    with pytest.raises(ValueError, match='integrity'):
        usage.ReportReader('state', store.client).read()
    manifest['report'] = manifest['ledger']
    state.objects[run.MANIFEST] = (state.counter, json.dumps(manifest).encode())
    with pytest.raises(ValueError, match='Invalid published'):
        usage.ReportReader('state', store.client).read()


def test_mapping_policy_replay_keeps_missing_generations_as_historical_evidence(tmp_path):
    store = Store(tmp_path)
    store.items = [{"name": "a", "generation": "1", "source": "gcs_usage", "records": [raw()]}]
    execute(store, tmp_path)
    store.items = []  # The original raw generation expired/disappeared.
    c = classifier()
    c.version = "corrected-policy"
    directory = tmp_path / 'replay'
    directory.mkdir()
    with pytest.raises(ValueError, match='explicit gap'):
        run.collect(store, c, activation(c), NOW+dt.timedelta(days=1), directory, configuration_healthy=True)
    assert store.payload['assets'][0]['windows']['30'][0]['requests'] == 1
    assert store.payload['assets'][0]['last_read'] == iso(NOW)
    assert store.payload['collection_errors'] == ['IncompleteReconciliation']
    with sqlite3.connect(tmp_path/'committed.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM reconciliations').fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM coverage WHERE source='gcs_usage' AND state='gap'").fetchone()[0] > 365


def test_retained_generation_replays_with_new_mapping_without_double_counts(tmp_path):
    store = Store(tmp_path)
    entry = raw()
    entry['cs_referer'] = 'https://new-app.example.org/'
    store.items = [{"name": "a", "generation": "1", "source": "gcs_usage", "records": [entry]}]
    execute(store, tmp_path)
    c = classifier()
    c.config['referrers']['new-app.example.org'] = 'New app'
    c.version = 'corrected-policy'
    directory = tmp_path/'replay'
    directory.mkdir()
    run.collect(store, c, activation(c), NOW+dt.timedelta(days=1), directory, configuration_healthy=True)
    groups = store.payload['assets'][0]['windows']['30']
    assert len(groups) == 1
    assert (groups[0]['application'], groups[0]['requests']) == ('New app', 1)


def test_failed_reconciliation_preserves_all_totals_and_retries_same_policy(tmp_path):
    store = Store(tmp_path)
    first, second = raw(identity='first'), raw(identity='second')
    for entry in (first, second):
        entry['cs_referer'] = 'https://new-app.example.org/'
    store.items = [{'name': 'a', 'generation': '1', 'source': 'gcs_usage', 'records': [first]},
                   {'name': 'b', 'generation': '2', 'source': 'gcs_usage', 'records': [second]}]
    execute(store, tmp_path)
    c = classifier()
    c.config['referrers']['new-app.example.org'] = 'New app'
    c.version = 'corrected-policy'
    store.items[1]['records'] = [second, {'cs_bucket': BUCKET}]
    directory = tmp_path/'failed-replay'
    directory.mkdir()
    with pytest.raises(ValueError, match='explicit gap'):
        run.collect(store, c, activation(c), NOW+dt.timedelta(days=1), directory, configuration_healthy=True)
    groups = store.payload['assets'][0]['windows']['30']
    assert [(g['application'], g['requests']) for g in groups] == [('Unknown', 2)]
    with sqlite3.connect(tmp_path/'committed.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM inputs').fetchone()[0] == 2
        assert db.execute("SELECT value FROM settings WHERE key='reconciliation_pending'").fetchone()[0] == '1'
    store.items[1]['records'] = [second]
    directory = tmp_path/'retry-replay'
    directory.mkdir()
    run.collect(store, c, activation(c), NOW+dt.timedelta(days=2), directory, configuration_healthy=True)
    groups = store.payload['assets'][0]['windows']['30']
    assert [(g['application'], g['requests']) for g in groups] == [('New app', 2)]
    with sqlite3.connect(tmp_path/'committed.sqlite') as db:
        assert db.execute("SELECT value FROM settings WHERE key='reconciliation_pending'").fetchone()[0] == '0'
        assert db.execute("SELECT COUNT(*) FROM coverage WHERE state='gap'").fetchone()[0] > 365


def test_report_exposes_old_gaps_outside_recent_daily_health(tmp_path):
    db = seeded(tmp_path)
    day = (NOW-dt.timedelta(days=200)).date().isoformat()
    db.execute("UPDATE coverage SET state='gap' WHERE source='cdn' AND day=?", (day,))
    payload = report(db, NOW)
    assert payload['assets'][0]['state'] == 'coverage_incomplete'
    assert not any(row['state'] == 'gap' for row in payload['coverage'])
    assert next(row for row in payload['source_health'] if row['source'] == 'cdn')['gap_days'] == 1


def test_root_changes_restart_only_affected_dataset_observation(tmp_path):
    db = seeded(tmp_path)
    row = {**ROWS[0], 'canonical_path': 'gs://shared/new-root/example/latest/example.csv'}
    c = Classifier([row], CONFIG, BUCKET)
    run.initialize(db, c, NOW, activation(c))
    result = report(db, NOW)['assets'][0]
    assert result['state'] == 'collecting_history'
    assert result['registered_on'] < result['observed_from']
    assert result['observed_from'] == (NOW.date()+dt.timedelta(days=1)).isoformat()


def test_native_csv_multiline_record_budget_is_total_not_per_line():
    fields = 'time_micros,cs_bucket,cs_method,cs_uri,sc_status,s_request_id,' + ','.join('x'+str(i) for i in range(20))
    values = '1,shared,GET,/shared/file,200,a,' + ','.join('"' + ('a'*30000) + '\n"' for _ in range(20))
    with pytest.raises(ValueError, match='budget'):
        list(usage_records(io.StringIO(fields+'\n'+values+'\n')))


def test_untrusted_probe_does_not_certify_historical_coverage(tmp_path):
    store = Store(tmp_path)
    execute(store, tmp_path, probe=lambda source, token: 'example')
    with sqlite3.connect(tmp_path/'committed.sqlite') as db:
        db.execute('UPDATE probes SET seen=1,trusted=0')
    execute(store, tmp_path, NOW+dt.timedelta(days=3))
    with sqlite3.connect(tmp_path/'committed.sqlite') as db:
        assert not db.execute("SELECT * FROM coverage WHERE state='healthy'").fetchall()


def test_nonce_overlap_does_not_substitute_for_independent_source_probe(tmp_path):
    db = seeded(tmp_path)
    token = 'usage-probe-'+'a'*32
    db.execute('INSERT INTO probes VALUES(?,?,?,?,?,?,?)', (token, NOW.date().isoformat(), 'cdn', 'example', classifier().version, 1, 0))
    entry = raw()
    entry['cs_user_agent'] = token
    event = classifier().event('gcs_usage', entry, {token: {'source': 'cdn', 'slug': 'example'}})
    assert event['kind'] == 'maintenance'
    run.add_event(db, event)
    assert db.execute('SELECT seen FROM probes WHERE token=?', (token,)).fetchone()[0] == 0


def test_ambiguous_audit_shapes_cannot_become_zero_interest():
    entry = {'timestamp': iso(NOW), 'insertId': 'a', 'resource': {'labels': {'bucket_name': BUCKET}},
             'protoPayload': {'serviceName': 'storage.googleapis.com', 'methodName': 'storage.objects.get', 'resourceName': 'projects/_/buckets/shared'}}
    with pytest.raises(ValueError, match='Unresolvable'):
        classifier().event('gcs_audit', entry, {})


def test_missing_usage_headers_and_misaligned_rows_are_explicit_failures():
    with pytest.raises(ValueError, match='header'):
        list(usage_records(io.StringIO('s_request_id,sc_status\na,200\n')))
    header = 'time_micros,cs_bucket,cs_method,cs_uri,sc_status,s_request_id\n'
    for row in ('1,shared,GET\n', '1,shared,GET,/shared/file,200,a,extra\n'):
        with pytest.raises(ValueError, match='header'):
            list(usage_records(io.StringIO(header+row)))
    with pytest.raises(KeyError):
        classifier().event('gcs_usage', {'time_micros': '1'}, {})
