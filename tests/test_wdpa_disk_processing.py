"""Allocation parity, interruption and native normalization contracts."""

from contextlib import closing, contextmanager
import errno
import json
import os
import sqlite3
import sys
from unittest import mock

import pytest

from ingestion.common import feature_metadata as metadata
from ingestion.common.identity_index import DiskIdentityPlan, DiskIdentityRecords
from ingestion.common.process_stream import feature_stream, pipe_commands
from ingestion.wdpa_monthly.resources import prepare_scratch
from ingestion.wdpa_monthly import resources
from scripts import release_feature_model as model


def feature(key, x=1, **properties):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [x, 0]},
        "properties": {"SITE_PID": key, **properties},
    }


def disk_allocation(root, features, baseline, decisions=()):
    with closing(DiskIdentityPlan(root / "plan.sqlite")) as plan:
        for ordinal, row in enumerate(features, 1):
            plan.add(ordinal, row, source_fields=("SITE_PID",))
        next_id, evidence = plan.resolve(
            baseline=baseline,
            asset_slug="wdpa-marine",
            release="2026-10-01",
            decisions=decisions,
        )
        return (
            [
                plan.record(row[0])
                for row in plan.db.execute(
                    "SELECT ordinal FROM planned ORDER BY ordinal"
                )
            ],
            next_id,
            evidence,
        )


def legacy(root, features, baseline, decisions=()):
    result = metadata.write_generated_id_release(
        open_features=lambda: iter(features),
        asset_slug="wdpa-marine",
        release="2026-10-01",
        provenance={},
        source_fields=("SITE_PID",),
        enriched_features_path=root / "old.geojsonseq",
        sidecar_path=root / "old.ndjson.gz",
        baseline=baseline,
        identity_resolution_decisions=decisions,
    )
    return list(model.read_metadata_sidecar(root / "old.ndjson.gz")), result


@pytest.mark.parametrize(
    "features",
    [
        [feature("a", NAME=None), feature("b", 2, EXTRA=1.3)],
        [feature("a"), feature("a"), feature("b", 2)],
        [
            feature("a", 1.1234567),
            feature("b", 2.7654321, DATE="2026-10-01", FLAG=True),
        ],
    ],
)
def test_disk_allocation_matches_existing_rules(tmp_path, features):
    baseline = model.GeneratedIdentityBaseline.genesis(contract_id="test-v1")
    old, old_result = legacy(tmp_path, features, baseline)
    new, next_id, evidence = disk_allocation(tmp_path, features, baseline)
    assert next_id == old_result.next_generated_feature_id
    assert evidence == old_result.identity_decisions
    for before, after in zip(old, new, strict=True):
        for key in ("feature_id", "geometry_hash", "properties_hash"):
            assert before[key] == after[key]
        assert tuple(before["identity_key"]) == after["identity_key"]
        assert (
            before["provenance"].get("duplicate_source_row_numbers", [])
            == after["duplicate_source_row_numbers"]
        )


def test_conflicting_duplicates_and_ambiguity_emit_no_outputs(tmp_path):
    with closing(DiskIdentityPlan(tmp_path / "duplicate.sqlite")) as plan:
        plan.add(1, feature("a"), source_fields=("SITE_PID",))
        with pytest.raises(RuntimeError, match="different content"):
            plan.add(2, feature("a", 2), source_fields=("SITE_PID",))
    old, _ = legacy(
        tmp_path,
        [feature("a")],
        model.GeneratedIdentityBaseline.genesis(contract_id="test-v1"),
    )
    baseline = model.GeneratedIdentityBaseline(
        tuple(old), 2, "2026-09-01", contract_id="test-v1"
    )
    (tmp_path / "old.ndjson.gz").unlink()
    (tmp_path / "old.geojsonseq").unlink()
    # This synthetic ambiguity must not use a maintainer's real Slack webhook.
    with mock.patch("scripts.slack_notify.notify", return_value=True) as notify:
        with pytest.raises(metadata.IdentityDecisionRequired):
            disk_allocation(tmp_path, [feature("changed-key")], baseline)
    notify.assert_called_once()
    assert notify.call_args.kwargs["title"] == (
        "Decision needed: wdpa-marine release 2026-10-01"
    )
    assert not list(tmp_path.glob("*.ndjson.gz"))
    assert not list(tmp_path.glob("*.fgb"))


def test_verified_baseline_and_completed_allocation_cannot_be_modified(tmp_path):
    baseline = model.GeneratedIdentityBaseline.genesis(contract_id="test-v1")
    with closing(DiskIdentityPlan(tmp_path / "sealed-plan.sqlite")) as plan:
        plan.add(1, feature("a"), source_fields=("SITE_PID",))
        plan.resolve(baseline=baseline, asset_slug="wdpa-marine", release="2026-10-01")
        with pytest.raises(RuntimeError, match="immutable"):
            plan.add(2, feature("b"), source_fields=("SITE_PID",))
        with pytest.raises(RuntimeError, match="already completed"):
            plan.resolve(
                baseline=baseline, asset_slug="wdpa-marine", release="2026-10-01"
            )
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            plan.db.execute("UPDATE planned SET feature_id='99'")
        with closing(DiskIdentityRecords(tmp_path / "sealed-baseline.sqlite")) as index:
            index.add(plan.record(1))
            index.seal()
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                index.db.execute("UPDATE records SET feature_id='99'")


def test_decimal_ids_keep_64_digit_range_and_exhaustion(tmp_path):
    maximum = model.GENERATED_SEQUENCE_EXHAUSTED - 1
    baseline = model.GeneratedIdentityBaseline(
        (
            dict(
                feature_id="1",
                identity_key=("old",),
                geometry_hash="sha256:" + "a" * 64,
                properties_hash="sha256:" + "b" * 64,
            ),
        ),
        maximum,
        "2026-09-01",
        contract_id="test-v1",
    )
    records, next_id, _ = disk_allocation(tmp_path, [feature("a")], baseline)
    assert records[0]["feature_id"] == str(maximum)
    assert next_id == maximum + 1
    with closing(DiskIdentityRecords(tmp_path / "baseline.sqlite")) as index:
        index.add(records[0])
        model.GeneratedIdentityBaseline(
            index.seal(), next_id, "2026-09-01", contract_id="test-v1"
        )
    with pytest.raises(model.ReleaseFeatureModelError, match="exhaust"):
        disk_allocation(
            tmp_path / "exhausted", [feature("a"), feature("b", 2)], baseline
        )


def test_native_pipeline_detects_producer_and_consumer_failures(tmp_path):
    with pytest.raises(RuntimeError, match="producer failed"):
        with feature_stream(
            [
                sys.executable,
                "-c",
                'import sys; print(\'{"type":"Feature"}\'); sys.exit(8)',
            ],
            log_path=tmp_path / "producer.log",
        ) as rows:
            assert list(rows) == [{"type": "Feature"}]
    with pytest.raises(RuntimeError, match="pipeline failed"):
        pipe_commands(
            [sys.executable, "-c", "import sys; sys.stdout.write('x'*1000000)"],
            [sys.executable, "-c", "raise SystemExit(10)"],
            log_dir=tmp_path / "pipes",
        )
    with pytest.raises(OSError) as failure:
        with feature_stream(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            log_path=tmp_path / "consumer.log",
        ):
            raise OSError(errno.ENOSPC, "disk full")
    assert failure.value.errno == errno.ENOSPC
    with pytest.raises(RuntimeError, match="not completely consumed"):
        with feature_stream(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            log_path=tmp_path / "unconsumed.log",
        ):
            pass


def test_cloud_run_requires_disk_before_work(monkeypatch, tmp_path):
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text("12 1 0:1 / /work rw - tmpfs tmpfs rw\n")
    monkeypatch.setenv("CLOUD_RUN_EXECUTION", "wdpa-test")
    with pytest.raises(RuntimeError, match="disk-backed"):
        prepare_scratch(mountinfo=mountinfo)
    mountinfo.write_text("12 1 0:1 / /work rw - ext4 /dev/sda rw\n")
    monkeypatch.setenv("TMPDIR", "/tmp")
    with pytest.raises(RuntimeError, match="TMPDIR"):
        prepare_scratch(mountinfo=mountinfo)


def test_cache_release_preserves_bytes_and_skips_links_and_special_files(monkeypatch, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    artifact = scratch / "artifact.bin"
    artifact.write_bytes(b"normalized geometry and identity hashes")
    outside = tmp_path / "baseline.bin"
    outside.write_bytes(b"verified frozen baseline")
    (scratch / "link").symlink_to(outside)
    os.mkfifo(scratch / "pipe")
    calls = []

    def advise(fd, offset, length, advice):
        calls.append((os.read(fd, 1024), offset, length, advice))

    monkeypatch.setattr(os, "posix_fadvise", advise, raising=False)
    monkeypatch.setattr(os, "POSIX_FADV_DONTNEED", 4, raising=False)
    resources.release_file_cache(scratch)
    assert calls == [(artifact.read_bytes(), 0, 0, 4)]
    assert artifact.read_bytes() == b"normalized geometry and identity hashes"
    assert outside.read_bytes() == b"verified frozen baseline"


def test_cache_release_allows_completed_intermediate_removal(monkeypatch, tmp_path):
    artifact = tmp_path / "completed.bin"
    artifact.write_bytes(b"finished")
    real_open = os.open

    def disappearing(path, flags):
        artifact.unlink()
        return real_open(path, flags)

    monkeypatch.setattr(os, "open", disappearing)
    monkeypatch.setattr(os, "posix_fadvise", lambda *_: None, raising=False)
    resources.release_file_cache(tmp_path)


def test_pressure_release_keeps_total_cgroup_peak_and_original_artifacts(monkeypatch, tmp_path):
    work = tmp_path / "work"
    inputs = tmp_path / "inputs"
    work.mkdir()
    inputs.mkdir()
    artifact = work / "normalized.gpkg"
    artifact.write_bytes(b"publishable bytes unchanged")
    calls = []
    monkeypatch.setattr(resources, "cgroup_memory", lambda: (6 * 1024**3, 7 * 1024**3))
    monkeypatch.setattr(resources, "release_file_cache", calls.append)
    profiler = resources.PhaseProfiler(work, interval=60, input_cache_roots=(inputs,))
    with profiler.phase("normalization"):
        pass
    assert calls == [work, inputs, work, inputs]
    assert profiler.records[0]["scratch_cache_releases"] == 2
    assert profiler.records[0]["cgroup_memory_peak_bytes"] == 7 * 1024**3
    assert profiler.records[0]["phase_memory_peak_bytes"] == 6 * 1024**3
    assert artifact.read_bytes() == b"publishable bytes unchanged"


def test_cache_advice_failure_fails_measurement(monkeypatch, tmp_path):
    monkeypatch.setattr(resources, "cgroup_memory", lambda: (5 * 1024**3, 5 * 1024**3))

    def denied(_):
        raise PermissionError("cannot release owned scratch pages")

    monkeypatch.setattr(resources, "release_file_cache", denied)
    profiler = resources.PhaseProfiler(tmp_path)
    with pytest.raises(PermissionError, match="owned scratch"):
        with profiler.phase("normalization"):
            pytest.fail("processing must not start after sampler failure")


@pytest.mark.parametrize("failed_input", [
    "source.zip", "memory.sqlite", "translation-sources.json", "pins.json",
])
def test_replay_verifies_inputs_with_cache_control_and_retains_hash_failures(
    monkeypatch, tmp_path, failed_input
):
    from scripts import local_wdpa_sample as replay

    active_phases = []

    class ObservedProfiler(resources.PhaseProfiler):
        @contextmanager
        def phase(self, name):
            with super().phase(name):
                active_phases.append(name)
                try:
                    yield
                finally:
                    active_phases.pop()

    def verify(path):
        assert active_phases == ["frozen-inputs"], "input read escaped cache control"
        if path.name == failed_input:
            raise RuntimeError("frozen input hash verification failed")
        return "1" * 64

    work = tmp_path / "replay"
    monkeypatch.setattr(sys, "argv", [
        "local_wdpa_sample.py", "--source", str(tmp_path / "source.zip"),
        "--workdir", str(work), "--baselines", str(tmp_path / "baselines"),
        "--translation-sources" if failed_input == "translation-sources.json" else "--translation-memory",
        str(tmp_path / ("translation-sources.json" if failed_input == "translation-sources.json" else "memory.sqlite")),
        "--fraction", "0.001",
    ])
    monkeypatch.setattr(replay, "prepare_scratch", lambda: None)
    monkeypatch.setattr(replay, "PhaseProfiler", ObservedProfiler)
    monkeypatch.setattr(replay, "cgroup_limits", lambda: (3.72, 8 * 1024**3))
    monkeypatch.setattr(replay.wdpa, "native_versions", lambda: {})
    monkeypatch.setattr(replay.wdpa, "sha256_file", verify)
    monkeypatch.setattr(resources, "cgroup_memory", lambda: (100, 123))
    monkeypatch.setattr(resources, "cgroup_memory_stat", lambda: {})
    with pytest.raises(RuntimeError, match="hash verification failed"):
        replay.main()
    report = json.loads((work / "benchmark.json").read_text())
    assert report["state"] == "failed"
    assert report["memory_peak_bytes"] == 123
    assert report["phases"][0]["phase"] == "frozen-inputs"
    assert report["phases"][0]["state"] == "failed"
    assert report["assets"] == {}
    assert list(work.iterdir()) == [work / "benchmark.json"]


def test_replay_source_counts_deduplicate_across_layers(monkeypatch, tmp_path):
    from ingestion.wdpa_monthly.run import SourceLayer
    from scripts import local_wdpa_sample as replay

    rows = [feature("a", SITE_ID=1, ISO3="IND;NPL"), feature("a", SITE_ID=1, ISO3="IND;NPL"),
            feature("b", SITE_ID=1, ISO3="IND"), feature("c", SITE_ID=2, ISO3=None)]
    def stream(layer, where):
        return (row["properties"] for row in (rows[:2] if layer.name == "one" else rows[2:]))
    monkeypatch.setattr(replay, "source_count_features", stream)
    counts = replay.source_count_summary(
        [SourceLayer(name, (), "GEOMETRY", "fixture") for name in ("one", "two")], "1=1", tmp_path
    )
    assert counts == dict(rows=3, india_rows=2, india_sites=1, raw_rows=4)
    assert not (tmp_path / "source-counts.sqlite").exists()


def test_normalized_store_matches_old_export_semantically(tmp_path):
    pytest.importorskip("osgeo")
    from ingestion.wdpa_monthly.geometry_store import NormalizedGeometryStore
    from ingestion.wdpa_monthly import run as wdpa

    raw = tmp_path / "source.geojsonseq"
    fixtures = [
        feature("a", 1.123456789, FLAG=True, NAME=None, SITE_ID=1, ISO3="IND"),
        feature("b", 2, COUNT=2**35, DATE="2026-10-01", SITE_ID=2, ISO3=None),
    ]
    fixtures += [
        {
            "type": "Feature",
            "properties": {"SITE_PID": "polygon", "NAME": "poly", "SITE_ID": 1, "ISO3": "IND;NPL"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]],
            },
        }
    ]
    raw.write_text("".join(json.dumps(row) + "\n" for row in fixtures))
    asset = wdpa.ASSETS[0]
    filtered = tmp_path / "filtered.gpkg"
    wdpa.run_command(
        [
            "ogr2ogr",
            "-f",
            "GPKG",
            str(filtered),
            "GeoJSONSeq:" + str(raw),
            "-nln",
            asset.tile_layer,
            "-nlt",
            "GEOMETRY",
        ]
    )
    from scripts.local_wdpa_sample import source_count_summary
    assert source_count_summary(
        [wdpa.SourceLayer(asset.tile_layer, (), "GEOMETRY", str(filtered))], "1=1", tmp_path
    ) == dict(rows=3, india_rows=2, india_sites=1, raw_rows=3)
    normalized_path = tmp_path / "normalized.geojsonseq"
    wdpa.convert_gpkg_to_geojsonseq(filtered, asset, normalized_path)
    normalized = list(metadata.iter_geojsonseq(normalized_path))
    old, _ = legacy(
        tmp_path,
        normalized,
        model.GeneratedIdentityBaseline.genesis(contract_id="test-v1"),
    )
    with (
        closing(DiskIdentityPlan(tmp_path / "native-plan.sqlite")) as plan,
        closing(
            NormalizedGeometryStore(
                tmp_path / "spool.gpkg", layer_name=asset.tile_layer
            )
        ) as store,
    ):
        for ordinal, row in enumerate(normalized, 1):
            plan.add(ordinal, row, source_fields=("SITE_PID",))
            store.add(row, ordinal)
        plan.resolve(
            baseline=model.GeneratedIdentityBaseline.genesis(contract_id="test-v1"),
            asset_slug=asset.slug,
            release="2026-10-01",
        )
        store.prepare_fields()
        records = list(
            store.sidecar_records(
                plan=plan, asset_slug=asset.slug, release="2026-10-01", provenance={}
            )
        )
        sql = store.projection_sql()
        store.close()
    assert records == old
    wdpa.convert_geojsonseq_to_fgb(
        tmp_path / "old.geojsonseq", asset, tmp_path / "old.fgb"
    )
    wdpa.run_command(
        [
            "ogr2ogr",
            "-f",
            "FlatGeobuf",
            str(tmp_path / "new.fgb"),
            str(tmp_path / "spool.gpkg"),
            "-sql",
            sql,
            "-nlt",
            "GEOMETRY",
        ]
    )
    assert wdpa.layer_fields(tmp_path / "old.fgb") == wdpa.layer_fields(
        tmp_path / "new.fgb"
    )

    def contents(path):
        with feature_stream(
            ["ogr2ogr", "-f", "GeoJSONSeq", "-lco", "RS=NO", "/vsistdout/", str(path)],
            log_path=path.with_suffix(".log"),
        ) as rows:
            return {row["properties"]["feature_id"]: row for row in rows}

    assert contents(tmp_path / "old.fgb") == contents(tmp_path / "new.fgb")


@pytest.mark.parametrize(
    "action", ["reuse_previous_feature_id", "assign_new_feature_id"]
)
def test_reviewed_decisions_match_the_existing_allocator(tmp_path, action):
    before = feature("old", NAME="Old")
    previous, _ = legacy(
        tmp_path,
        [before],
        model.GeneratedIdentityBaseline.genesis(contract_id="test-v1"),
    )
    baseline = model.GeneratedIdentityBaseline(
        tuple(previous), 2, "2026-09-01", contract_id="test-v1"
    )
    after = feature("new", NAME="Renamed")
    gh, ph = model.content_hashes(
        geometry=after["geometry"], properties=after["properties"]
    )
    ambiguity = model.find_identity_ambiguities(
        [dict(identity_key=("new",), geometry_hash=gh, properties_hash=ph)],
        previous_records=baseline.records,
    )[0]
    from dataclasses import asdict

    decision = {
        "release": "2026-10-01",
        "action": action,
        "new_identity_key": ["new"],
        "new_geometry_hash": gh,
        "new_properties_hash": ph,
        "rationale": "Reviewed upstream rename",
        "reviewer": "jonaraphael",
        "pr_reference": "https://github.com/SkyTruth/shared-datasets-1/pull/133",
        "previous_feature_id": None,
        **{
            key: list(value)
            for key, value in asdict(ambiguity).items()
            if key.startswith("matching_")
        },
    }
    if action == "reuse_previous_feature_id":
        decision["reuse_feature_id"] = "1"
    old, result = legacy(tmp_path, [after], baseline, [decision])
    rows, next_id, evidence = disk_allocation(tmp_path, [after], baseline, [decision])
    assert rows[0]["feature_id"] == old[0]["feature_id"]
    assert next_id == result.next_generated_feature_id
    assert evidence == result.identity_decisions


def test_controlled_failure_precedes_any_publisher(monkeypatch):
    from ingestion.wdpa_monthly import run as wdpa

    monkeypatch.setenv("WDPA_FAIL_BEFORE_WRITES", "true")
    from unittest.mock import patch

    with (
        patch.object(wdpa, "prepare_scratch"),
        patch.object(wdpa.GcsPublisher, "from_runtime") as publisher,
    ):
        with pytest.raises(RuntimeError, match="before any dataset writes"):
            wdpa.run()
        publisher.assert_not_called()
