import datetime as dt
import json
import sqlite3
from unittest.mock import Mock

from google.api_core.exceptions import Forbidden, PreconditionFailed
import pytest

from skytruth_shared_datasets import UsageReporter
from ingestion.dataset_usage import run
from test_dataset_usage import NOW, Store, classifier, execute, raw


def receipt(day=NOW, **changes):
    return {"schema_version": 1, "asset_slug": "example", "activity": "use",
            "observed_at": day.isoformat(), **changes}


def item(identity="a", day=NOW, **changes):
    return {"name": f"app-usage/example-production/v1/{day.date().isoformat()}/{identity * 32}.json",
            "source": "app_usage", "generation": "1", "records": [receipt(day, **changes)]}


def test_receipts_from_processes_coalesce_without_inflating_bucket_counts(tmp_path):
    store = Store(tmp_path)
    store.items = [item(), item("b"), {"name": "native", "generation": "1", "source": "gcs_usage", "records": [raw()]}]
    execute(store, tmp_path)
    asset = store.payload["assets"][0]
    assert asset["windows"]["30"][0]["requests"] == 1
    assert asset["application_windows"]["30"] == [{"application": "Example app", "environment": "production",
        "activity": "use", "active_days": 1, "last_reported": run.iso(NOW)}]
    store.items.append(item("c", NOW + dt.timedelta(days=1)))
    execute(store, tmp_path, NOW + dt.timedelta(days=1))
    assert store.payload["assets"][0]["application_windows"]["30"][0]["active_days"] == 2
    assert store.payload["producers"][0]["connection"] == "reporting"


@pytest.mark.parametrize("changes", [{"schema_version": True}, {"activity": "download"},
    {"asset_slug": "unknown"}, {"user_id": "sensitive"}, {"application": "forged"}])
def test_receipt_boundary_rejects_extra_identity_and_invalid_activity(changes):
    row = item(**changes)
    with pytest.raises(ValueError):
        classifier().event("app_usage", row["records"][0], {}, name=row["name"])


def test_unregistered_writer_and_day_mismatch_fail():
    row = item()
    for name in (row["name"].replace("example-production", "forged"), row["name"].replace("2026-10-07", "2026-10-06")):
        with pytest.raises(ValueError):
            classifier().event("app_usage", row["records"][0], {}, name=name)


def test_registered_application_without_receipts_is_not_connected(tmp_path):
    store = Store(tmp_path)
    execute(store, tmp_path)
    assert store.payload["producers"][0]["connection"] == "not_connected"
    assert store.payload["assets"][0]["application_windows"]["30"] == []


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
    assert {entry["source"] for entry in store.payload["retired_sources"]} == {"cdn", "catalog", "gcs_audit"}
    execute(store, tmp_path)
    assert sum(group["requests"] for group in store.payload["assets"][0]["windows"]["30"]) == 24


def test_independent_activation_never_requires_org_review():
    from test_dataset_usage import activation
    c = classifier()
    proof = activation(c)
    proof["sources"]["gcs_usage"]["verified_at"] = None
    assert run.activation_valid(proof, c, "app_usage")
    assert run.activation_valid(proof, c)
    assert not run.activation_valid(proof, c, "gcs_usage")


def test_observed_producer_identity_cannot_be_silently_rebound(tmp_path):
    store = Store(tmp_path)
    store.items = [item()]
    execute(store, tmp_path)
    c = classifier()
    c.producers['example-production']['application'] = 'Another application'
    with sqlite3.connect(tmp_path / 'committed.sqlite') as db:
        with pytest.raises(ValueError, match='cannot be rebound'):
            run.initialize(db, c, NOW, {})


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


def test_reporter_coalesces_cache_use_utc_days_and_creates_only():
    client = Mock()
    clock = [NOW]
    reporter = UsageReporter("example-production", client=client, now=lambda: clock[0])
    reporter.recordUsage("example")
    reporter.recordUsage("example")
    assert reporter.flush() and reporter.flush()
    reporter.recordUsage("example")
    assert reporter.flush()
    assert client.bucket.return_value.blob.return_value.upload_from_string.call_count == 1
    clock[0] += dt.timedelta(days=1)
    reporter.recordUsage("example")
    assert reporter.flush()
    calls = client.bucket.return_value.blob.return_value.upload_from_string.call_args_list
    assert len(calls) == 2
    for call in calls:
        assert call.kwargs["if_generation_match"] == 0 and call.kwargs["retry"] is None
        assert set(json.loads(call.args[0])) == {"schema_version", "asset_slug", "activity", "observed_at"}
    client.bucket.return_value.list_blobs.assert_not_called()


def test_reporting_failure_and_lost_ack_are_bounded_and_do_not_escape():
    for failure, outcome, count in ((Forbidden("denied"), False, 2), (PreconditionFailed("already exists"), True, 1)):
        client, diagnostics = Mock(), []
        client.bucket.return_value.blob.return_value.upload_from_string.side_effect = failure
        reporter = UsageReporter("example-production", client=client, diagnostic=diagnostics.append)
        reporter.recordUsage("example")
        assert reporter.flush() is outcome
        assert client.bucket.return_value.blob.return_value.upload_from_string.call_count == count
        assert bool(diagnostics) is (not outcome)
