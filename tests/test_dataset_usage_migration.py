import datetime as dt
import sqlite3
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from ingestion.dataset_usage import run
from test_dataset_usage import NOW, Store, classifier, execute, raw


def test_v2_application_history_is_preserved_without_receipt_collection_or_live_metrics(tmp_path):
    store = Store(tmp_path)
    store.items = [{"name": "native", "generation": "1", "source": "gcs_usage", "records": [raw()]}]
    execute(store, tmp_path)
    historical = ("2026-10-06", "example", "old-production", "Historical app", "production", "use", run.iso(NOW))
    with sqlite3.connect(tmp_path / "committed.sqlite") as db:
        db.execute("PRAGMA user_version=2")
        db.execute("CREATE TABLE app_daily(day,slug,producer,application,environment,activity,last_at)")
        db.execute("CREATE TABLE app_last(slug,producer,application,environment,activity,last_at)")
        db.execute("INSERT INTO app_daily VALUES(?,?,?,?,?,?,?)", historical)
        db.execute("INSERT INTO app_last VALUES(?,?,?,?,?,?)", historical[1:])
        db.execute("DELETE FROM retired_sources WHERE source='app_usage'")
        db.execute("UPDATE settings SET value='previous-v2-policy' WHERE key='policy'")
    execute(store, tmp_path)
    payload = store.payload
    assert payload["schema_version"] == 3 and "producers" not in payload
    assert all("application_windows" not in asset and "last_application_activity" not in asset for asset in payload["assets"])
    assert {entry["source"] for entry in payload["source_health"]} == {"gcs_usage"}
    assert sum(group["requests"] for group in payload["assets"][0]["windows"]["30"]) == 1
    with sqlite3.connect(tmp_path / "committed.sqlite") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.execute("SELECT * FROM app_daily").fetchall() == [historical]
        assert db.execute("SELECT * FROM app_last").fetchall() == [historical[1:]]
        cutoff = db.execute("SELECT retired_at FROM retired_sources WHERE source='app_usage'").fetchone()[0]
    execute(store, tmp_path, NOW + dt.timedelta(days=1))
    with sqlite3.connect(tmp_path / "committed.sqlite") as db:
        assert db.execute("SELECT * FROM app_daily").fetchall() == [historical]
        assert db.execute("SELECT retired_at FROM retired_sources WHERE source='app_usage'").fetchone()[0] == cutoff
    store.items = []
    execute(store, tmp_path, NOW + dt.timedelta(days=461))
    with sqlite3.connect(tmp_path / "committed.sqlite") as db:
        assert db.execute("SELECT * FROM app_daily").fetchall() == []
        assert db.execute("SELECT * FROM app_last").fetchall() == [historical[1:]]
        assert db.execute("SELECT retired_at FROM retired_sources WHERE source='app_usage'").fetchone()[0] == cutoff


def test_native_inventory_ignores_retired_receipts_and_new_ledgers_have_no_reporter_tables(tmp_path):
    client = Mock()
    client.list_blobs.return_value = [
        SimpleNamespace(name="app-usage/old-production/v1/2026-10-06/receipt.json", generation=1, size=50),
        SimpleNamespace(name="storage-usage_usage_2026_10_06", generation=2, size=200),
    ]
    store = run.GcsStore("raw", "state", client=client)
    inputs = list(store.inputs())
    assert len(inputs) == 1 and inputs[0]["source"] == "gcs_usage"
    client.list_blobs.assert_called_once_with(store.raw, prefix="storage-usage", page_size=256)
    local = Store(tmp_path)
    execute(local, tmp_path)
    with sqlite3.connect(tmp_path / "committed.sqlite") as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name IN ('app_daily','app_last')").fetchall()
    with pytest.raises(ValueError, match="Retired or unknown"):
        classifier().event("app_usage", {}, {})
    config = dict(classifier().config, producers={})
    with pytest.raises(ValueError, match="configuration field"):
        type(classifier())([], config, "shared")


def test_v1_migration_keeps_retired_counts_and_original_last_observation(tmp_path):
    store = Store(tmp_path)
    store.items = [{"name": "native", "generation": "1", "source": "gcs_usage", "records": [raw()]}]
    execute(store, tmp_path)
    with sqlite3.connect(tmp_path / "committed.sqlite") as db:
        db.execute("PRAGMA user_version=0")
        db.execute("INSERT INTO daily VALUES(?,?,?,?,?,?,?, ?,?)", ("2026-10-06", "example", "cdn", "read", "Historical app", "inferred", 23, 99, run.iso(NOW)))
        db.execute("UPDATE settings SET value='old-policy' WHERE key='policy'")
    execute(store, tmp_path)
    asset = store.payload["assets"][0]
    assert sum(group["requests"] for group in asset["windows"]["30"]) == 24
    assert asset["last_read"] == run.iso(NOW)
    assert {entry["source"] for entry in store.payload["retired_sources"]} == {"cdn", "catalog", "gcs_audit", "app_usage"}
    execute(store, tmp_path)
    assert sum(group["requests"] for group in store.payload["assets"][0]["windows"]["30"]) == 24


def test_normalized_timestamps_keep_chronological_order_without_changing_old_ids(tmp_path):
    store = Store(tmp_path)
    store.items = [{'name': 'original', 'generation': '1', 'source': 'gcs_usage', 'records': [raw()]}]
    execute(store, tmp_path)
    with sqlite3.connect(tmp_path / 'committed.sqlite') as db:
        db.execute('PRAGMA user_version=0')
        db.execute('UPDATE assets SET last_read=?', (NOW.isoformat().replace('+00:00', 'Z'),))
    later = NOW + dt.timedelta(microseconds=500000)
    store.items.append({'name': 'late-shard', 'generation': '1', 'source': 'gcs_usage', 'records': [raw(), raw(when=later, identity='later')]})
    execute(store, tmp_path, later)
    asset = store.payload['assets'][0]
    assert asset['last_read'] == run.iso(later)
    assert asset['windows']['30'][0]['requests'] == 2
