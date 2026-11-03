"""Allocation parity, interruption and native normalization contracts."""

from contextlib import closing, contextmanager
import errno
import json
import os
from pathlib import Path
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
    # This synthetic ambiguity must not queue a production decision event.
    with mock.patch("ingestion.common.decision_events.publish", return_value=True) as notify:
        with pytest.raises(metadata.IdentityDecisionRequired):
            disk_allocation(tmp_path, [feature("changed-key")], baseline)
    notify.assert_called_once()
    assert notify.call_args.kwargs["asset_slug"] == "wdpa-marine"
    assert notify.call_args.kwargs["release"] == "2026-10-01"
    case = notify.call_args.kwargs["ambiguities"][0]
    assert case["new_identity_key"] == ["changed-key"]
    assert case["matching_geometry_feature_ids"] == ["1"]
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
        "--run-date", "2026-10-01",
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


@pytest.mark.parametrize("mode", ["stage", "sample", "compare", "compare-failure"])
def test_successful_replay_modes_persist_per_realm_compatibility(
    monkeypatch, tmp_path, capsys, mode
):
    """Run real CLI/report/comparison/staging code with local native/GCS providers."""
    from dataclasses import replace
    from types import SimpleNamespace
    import shutil

    from ingestion.wdpa_monthly import artifact_bundle as bundle
    from scripts import local_wdpa_sample as replay
    from test_wdpa_artifact_bundle import Objects
    from test_wdpa_monthly import fake_asset_outputs

    wdpa = replay.wdpa
    source = tmp_path / "source.zip"
    source.write_bytes(b"frozen source fixture")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "pins.json").write_text("{}")
    translations = inputs / "translation-sources.json"
    translations.write_text("{}")
    work = tmp_path / "replay"
    argv = ["local_wdpa_sample.py", "--source", str(source), "--baselines", str(inputs),
            "--translation-sources", str(translations), "--workdir", str(work),
            "--run-date", "2026-04-01"]
    argv += ["--stage-build"] if mode == "stage" else ["--fraction", "0.001"]
    if mode.startswith("compare"):
        argv.append("--compare-legacy")
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(replay, "prepare_scratch", lambda: None)
    monkeypatch.setattr(replay, "cgroup_limits", lambda: (4, 8 * 1024**3))
    monkeypatch.setattr(resources, "cgroup_memory", lambda: (100, 123))
    monkeypatch.setattr(resources, "cgroup_memory_stat", lambda: {})
    monkeypatch.setattr(wdpa, "native_versions", lambda: {})
    fields = tuple(wdpa.FieldSpec(name, "String") for name in ("SITE_PID", "SITE_ID", "ISO3"))
    monkeypatch.setattr(wdpa, "prepare_source_datasets", lambda path, _: [str(path)])
    monkeypatch.setattr(wdpa, "discover_source_layers", lambda paths: (
        [wdpa.SourceLayer("fixture", fields, "GEOMETRY", paths[0])], "MARINE", fields))
    monkeypatch.setattr(wdpa, "assert_sample_field_available", lambda *_: None)
    monkeypatch.setattr(wdpa, "discover_assets", lambda **kwargs: {"state": "READY", "assets": {
        asset.slug: {} for asset in kwargs["assets"]}})
    rows = [feature("a", SITE_ID=1, ISO3="IND"), feature("b", 2, SITE_ID=2, ISO3="FRA")]
    monkeypatch.setattr(replay, "source_count_features", lambda *_: (r["properties"] for r in rows))
    snapshot = model.GeneratedIdentitySnapshot("gs://fixture/manifest.json", 1, "a" * 64)
    monkeypatch.setattr(replay, "load_baseline", lambda *_: model.GeneratedIdentityBaseline(
        (), 1, "2026-03-01", contract_id=wdpa.CONTRACT_ID, snapshot=snapshot))

    def memory(_config, directory):
        database = directory / "translation-memory.sqlite"
        database.write_bytes(b"translation index fixture")
        return SimpleNamespace(database=database, locales=bundle.LOCALES, close=lambda: None)

    monkeypatch.setattr(replay, "build_frozen_translation_memory", memory)
    objects = Objects()
    stager = bundle.BuildStager(objects, "wdpa-monthly-replay")
    monkeypatch.setattr(bundle.BuildStager, "from_runtime", lambda: stager)

    def native_outputs(*, asset, workdir, **_kwargs):
        output = fake_asset_outputs(workdir, asset=asset, release="2026-04-01")
        records = []
        for index, row in enumerate(rows, 1):
            gh, ph = model.content_hashes(geometry=row["geometry"], properties=row["properties"])
            records.append(dict(schema_version=1, asset_slug=asset.slug, release="2026-04-01",
                                feature_id=str(index), identity_key=[row["properties"]["SITE_PID"]],
                                geometry_hash=gh, properties_hash=ph, properties=row["properties"], provenance={}))
        model.write_metadata_sidecar(records, output.metadata)
        for path in output.localized_metadata.values():
            shutil.copyfile(output.metadata, path)
        output.schema.write_text(json.dumps(output.schema_payload))
        output.fgb.write_text(json.dumps([
            {**row, "properties": {**row["properties"], "feature_id": str(i)}}
            for i, row in enumerate(rows, 1)]))
        hashes = {role: wdpa.sha256_file(path) for role, path in bundle.artifact_paths(output).items()}
        hashes["csv"] = hashes["metadata_translations"]
        return replace(output, sha256=hashes, next_generated_feature_id=3)

    monkeypatch.setattr(wdpa, "build_asset_outputs", native_outputs)
    legacy_calls = []

    def legacy_outputs(directory, *, asset, **kwargs):
        directory.mkdir()
        legacy_calls.append(asset.slug)
        output = native_outputs(asset=asset, workdir=directory)
        if mode == "compare-failure":
            records = list(model.read_metadata_sidecar(output.metadata))
            records[0]["properties"]["NAME"] = "different legacy output"
            model.write_metadata_sidecar(records, output.metadata)
        localized = directory / "localized"
        localized.mkdir()
        for path in (*output.localized_metadata.values(), output.metadata_translations):
            shutil.move(path, localized / path.name)
        return output.metadata, output.schema, output.fgb, localized, SimpleNamespace(
            next_generated_feature_id=output.next_generated_feature_id,
            identity_decisions=output.identity_decisions)

    monkeypatch.setattr(replay, "legacy_sample", legacy_outputs)
    monkeypatch.setattr(wdpa, "layer_fields", lambda _: fields)

    @contextmanager
    def native_geometry(command, **_kwargs):
        yield iter(json.loads(Path(command[-1]).read_text()))

    monkeypatch.setattr(replay, "feature_stream", native_geometry)
    if mode == "compare-failure":
        with pytest.raises(RuntimeError, match="legacy IDs, hashes or properties differ"):
            replay.main()
        report = json.loads((work / "benchmark.json").read_text())
        assert report["state"] == "failed" and report["assets"] == {}
        assert not objects.data
        return
    replay.main()
    report = json.loads((work / "benchmark.json").read_text())
    assert report == json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert report["state"] == "succeeded"
    expected = mode == "compare"
    assert report["compatibility_verified"] is expected
    assert set(report["assets"]) == {a.slug for a in wdpa.ASSETS}
    assert all(v["compatibility_verified"] is expected for v in report["assets"].values())
    assert legacy_calls == ([a.slug for a in wdpa.ASSETS] if expected else [])
    if mode == "stage":
        assert set(report["staged_assets"]) == set(report["assets"])
        for asset in wdpa.ASSETS:
            for ref in report["staged_assets"][asset.slug]["artifacts"].values():
                name = bundle.check_reference(ref, execution=stager.execution)
                assert objects.data[name][1] and not (work / Path(name).name).exists()
    else:
        assert not objects.data


def test_complete_discovery_cli_normalizes_both_realms_and_reuses_spools(tmp_path, monkeypatch):
    pytest.importorskip("osgeo")
    from scripts import local_wdpa_sample as cli
    from ingestion.wdpa_monthly.geometry_store import NormalizedGeometryStore
    source = tmp_path / "source.geojson"
    source.write_text(json.dumps({"type": "FeatureCollection", "features": [
        feature("marine", 1, SITE_ID=1, MARINE="1", ISO3="IND"),
        feature("land", 2, SITE_ID=2, MARINE="0", ISO3="FRA"),
    ]}))
    memory = tmp_path / "unused.sqlite"
    memory.write_bytes(b"discovery does not open translation memory")
    analysis = tmp_path / "retained"
    monkeypatch.setattr(cli.wdpa, "build_asset_outputs", lambda **_: pytest.fail("must discover before building"))
    for attempt in range(2):
        output = tmp_path / f"attempt-{attempt}"
        monkeypatch.setattr(sys, "argv", ["local_wdpa_sample.py", "--source", str(source),
            "--genesis", "--translation-memory", str(memory), "--run-date", "2026-04-01",
            "--discover-only", "--analysis-dir", str(analysis), "--workdir", str(output)])
        cli.main()
        report = json.loads((output / "benchmark.json").read_text())
        assert report["state"] == "discovered"
        for asset in cli.wdpa.ASSETS:
            result = report["readiness"]["assets"][asset.slug]
            assert result["state"] == "READY" and result["reused"] == bool(attempt)
            assert result["payload"]["expected_rows"] == 1
            spool = Path(result["analysis"]) / f"{asset.slug}.normalized.gpkg"
            before = cli.wdpa.sha256_file(spool)
            with closing(NormalizedGeometryStore.open(spool, layer_name=asset.tile_layer, read_only=True)) as store:
                assert store.source_feature(1)["properties"]["SITE_PID"] in {"marine", "land"}
            assert cli.wdpa.sha256_file(spool) == before


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
    from scripts.local_wdpa_sample import scope_identity_decisions
    candidates = [{"new_identity_key": [key]} for key in ("a", "b", "polygon", "absent")]
    assert scope_identity_decisions(
        candidates,
        layers=[wdpa.SourceLayer(asset.tile_layer, (), "GEOMETRY", str(filtered))],
        where="SITE_ID = 1", sample=wdpa.SampleSpec(0.001, 7919),
    ) == [candidates[0], candidates[2]]
    assert scope_identity_decisions(
        candidates, layers=[], where="unused", sample=None
    ) is candidates
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
    from test_wdpa_identity_review import native_review_pipeline
    native_review_pipeline(tmp_path / "identity-review")


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


@pytest.fixture
def reviewed_sample_cases(tmp_path):
    from dataclasses import asdict

    seed = tmp_path / "baseline"
    seed.mkdir()
    old, _ = legacy(seed, [feature("old-a", 1), feature("old-b", 2)],
                    model.GeneratedIdentityBaseline.genesis(contract_id="test-v1"))
    baseline = model.GeneratedIdentityBaseline(tuple(old), 3, "2026-09-01", contract_id="test-v1")
    incoming = [feature("new-a", 1, NAME="A"), feature("new-b", 2, NAME="B")]
    decisions = []
    for row in incoming:
        gh, ph = model.content_hashes(geometry=row["geometry"], properties=row["properties"])
        key = model.source_fields_identity_key(row["properties"], ("SITE_PID",))
        case = model.find_identity_ambiguities(
            [dict(identity_key=key, geometry_hash=gh, properties_hash=ph)],
            previous_records=baseline.records,
        )[0]
        decisions.append({
            "release": "2026-10-01", "action": "reuse_previous_feature_id",
            "new_identity_key": list(key), "new_geometry_hash": gh,
            "new_properties_hash": ph, "previous_feature_id_for_key": None,
            "reuse_feature_id": case.matching_geometry_feature_ids[0],
            "rationale": "Reviewed replacement with the old key absent",
            "reviewer": "jonaraphael", "pr_reference": "https://github.com/SkyTruth/shared-datasets-1/pull/133",
            **{k: list(v) for k, v in asdict(case).items() if k.startswith("matching_")},
        })
    return baseline, incoming, decisions


@pytest.mark.parametrize("count", [0, 1, 2])
def test_fractional_source_scope_feeds_both_strict_allocators(
    tmp_path, monkeypatch, reviewed_sample_cases, count
):
    from scripts import local_wdpa_sample as replay
    from ingestion.wdpa_monthly import run as wdpa

    baseline, incoming, decisions = reviewed_sample_cases
    sample_rows = incoming[:count]
    def source(layer, where):
        assert layer == "source-layer" and where == "exact realm and SITE_ID filter"
        return (row["properties"] for row in sample_rows)
    monkeypatch.setattr(replay, "source_count_features", source)
    scoped = replay.scope_identity_decisions(
        decisions, layers=["source-layer"], where="exact realm and SITE_ID filter",
        sample=wdpa.SampleSpec(0.001, 7919),
    )
    assert scoped == decisions[:count]
    if count == 1:
        # Reproduce the deployed failure at both real allocator boundaries.
        for name, allocate in [("legacy-unscoped", legacy), ("disk-unscoped", disk_allocation)]:
            root = tmp_path / name
            root.mkdir()
            with pytest.raises((RuntimeError, model.ReleaseFeatureModelError), match="stale feature identity resolution"):
                allocate(root, sample_rows, baseline, decisions)
    if count:
        old_root, new_root = tmp_path / "legacy", tmp_path / "disk"
        old_root.mkdir()
        new_root.mkdir()
        old, result = legacy(old_root, sample_rows, baseline, scoped)
        rows, next_id, evidence = disk_allocation(new_root, sample_rows, baseline, scoped)
        assert [r["feature_id"] for r in rows] == [r["feature_id"] for r in old]
        assert next_id == result.next_generated_feature_id
        assert evidence == result.identity_decisions


@pytest.mark.parametrize("invalid", ["hash", "duplicate", "missing", "full-build", "illegal", "stale-present"])
def test_source_scope_preserves_strict_rejections(
    tmp_path, monkeypatch, reviewed_sample_cases, invalid
):
    from scripts import local_wdpa_sample as replay
    from ingestion.wdpa_monthly import run as wdpa

    baseline, incoming, decisions = reviewed_sample_cases
    monkeypatch.setattr(replay, "source_count_features", lambda *_: iter([incoming[0]["properties"]]))
    expected = "new_geometry_hash"
    if invalid == "hash":
        decisions[0] = {**decisions[0], "new_geometry_hash": "sha256:" + "0" * 64}
    elif invalid == "duplicate":
        decisions.append(decisions[0])
        expected = "duplicate"
    elif invalid == "missing":
        decisions = decisions[1:]
        expected = "identity"
    elif invalid == "illegal":
        decisions[0] = {**decisions[0], "action": "keep_previous_key_mapping"}
        decisions[0].pop("reuse_feature_id")
        expected = "no mapping to keep"
    else:
        expected = "stale feature identity resolution"
        if invalid == "stale-present":
            incoming[0] = feature("new-a", 99, NAME="New entity")
    scoped = replay.scope_identity_decisions(
        decisions, layers=["source"], where="sample",
        sample=None if invalid == "full-build" else wdpa.SampleSpec(0.001, 7919),
    )
    for name, allocate in [("legacy", legacy), ("disk", disk_allocation)]:
        root = tmp_path / name
        root.mkdir()
        with mock.patch("ingestion.common.decision_events.publish", return_value=True):
            with pytest.raises((RuntimeError, model.ReleaseFeatureModelError), match=expected):
                allocate(root, incoming[:1], baseline, scoped)
        assert not list(root.glob("*.ndjson.gz"))


@pytest.fixture
def proc_cache_view(monkeypatch, tmp_path):
    """A kernel boundary with one inaccessible child and one readable sibling."""
    root = tmp_path / "scratch"
    root.mkdir()
    path = root / "native.tmp"
    path.write_bytes(b"unlinked native bytes")
    descriptor = os.open(path, os.O_RDONLY)
    path.unlink()
    state = {"pid": 200, "stage": "fd", "error": PermissionError,
             "self_fd": False, "target": str(path) + " (deleted)",
             "pinned_target": str(path) + " (deleted)", "advised": []}
    real_iterdir, real_read_text = Path.iterdir, Path.read_text
    real_readlink, real_open = os.readlink, os.open

    def denied(pid, stage, source):
        if pid == state["pid"] and stage == state["stage"]:
            code = {FileNotFoundError: errno.ENOENT, ProcessLookupError: errno.ESRCH}.get(state["error"], errno.EACCES)
            raise state["error"](code, "kernel process entry unavailable", str(source))

    def directory(source):
        parts = source.parts
        if len(parts) == 4 and parts[1] == "proc":
            pid, kind = int(parts[2]), parts[3]
            denied(pid, kind, source)
            if kind == "task":
                return iter([source / str(pid)])
            if kind == "fd":
                return iter([source / "7"] if pid != 100 or state["self_fd"] else [])
        return real_iterdir(source)

    def children(source, *args, **kwargs):
        if str(source).startswith("/proc/") and source.name == "children":
            pid = int(source.parts[2])
            denied(pid, "children", source)
            return "200 300" if pid == 100 else ""
        return real_read_text(source, *args, **kwargs)

    def link(source, *args, **kwargs):
        name = str(source)
        if name.startswith("/proc/self/fd/"):
            return state["pinned_target"]
        if name.startswith("/proc/"):
            denied(int(Path(name).parts[2]), "readlink", source)
            return state["target"]
        return real_readlink(source, *args, **kwargs)

    def open_descriptor(source, flags, *args, **kwargs):
        name = str(source)
        if name.startswith("/proc/"):
            denied(int(Path(name).parts[2]), "open", source)
            return os.dup(descriptor)
        return real_open(source, flags, *args, **kwargs)

    monkeypatch.setattr(resources.sys, "platform", "linux")
    monkeypatch.setattr(os, "getpid", lambda: 100)
    monkeypatch.setattr(Path, "iterdir", directory)
    monkeypatch.setattr(Path, "read_text", children)
    monkeypatch.setattr(os, "readlink", link)
    monkeypatch.setattr(os, "open", open_descriptor)
    monkeypatch.setattr(os, "posix_fadvise", lambda fd, *_: state["advised"].append(os.pread(fd, 64, 0)), raising=False)
    monkeypatch.setattr(os, "POSIX_FADV_DONTNEED", 4, raising=False)
    try:
        yield root, state
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("stage", ["task", "children", "fd", "readlink", "open"])
def test_proc_cache_allows_unreadable_descendant(proc_cache_view, stage):
    root, state = proc_cache_view
    state["stage"] = stage
    resources.release_file_cache(root)
    assert state["advised"] == [b"unlinked native bytes"]


@pytest.mark.parametrize("stage", ["task", "children", "fd", "readlink", "open"])
def test_proc_cache_keeps_self_access_strict(proc_cache_view, stage):
    root, state = proc_cache_view
    state.update(pid=100, stage=stage, self_fd=True)
    with pytest.raises(PermissionError, match="kernel process entry"):
        resources.release_file_cache(root)


@pytest.mark.parametrize("error", [FileNotFoundError, ProcessLookupError])
def test_proc_cache_allows_self_descriptor_close_race(proc_cache_view, error):
    root, state = proc_cache_view
    state.update(pid=100, stage="readlink", self_fd=True, error=error)
    resources.release_file_cache(root)
    assert state["advised"] == [b"unlinked native bytes", b"unlinked native bytes"]


@pytest.mark.parametrize("target", ["target", "pinned_target"])
def test_proc_cache_never_advises_outside_scope_or_reused_descriptor(proc_cache_view, tmp_path, target):
    root, state = proc_cache_view
    state.update(pid=-1)
    state[target] = str(tmp_path / "outside.bin") + " (deleted)"
    resources.release_file_cache(root)
    assert state["advised"] == []


@pytest.mark.parametrize("producer_fails", [False, True])
def test_background_sampler_failure_records_failed_phase(monkeypatch, tmp_path, caplog, producer_fails):
    import logging
    import threading

    seen = threading.Event()
    calls = 0
    def advice(_):
        nonlocal calls
        calls += 1
        if calls == 2:
            seen.set()
            raise PermissionError("owned scratch advice denied")
    monkeypatch.setattr(resources, "cgroup_memory", lambda: (5 * 1024**3, 6 * 1024**3))
    monkeypatch.setattr(resources, "cgroup_memory_stat", lambda: {})
    monkeypatch.setattr(resources, "release_file_cache", advice)
    profiler = resources.PhaseProfiler(tmp_path, interval=0.001)
    expected = ValueError if producer_fails else RuntimeError
    message = "producer failed" if producer_fails else "resource sampler failed"
    with caplog.at_level(logging.INFO, logger=resources.LOGGER.name):
        with pytest.raises(expected, match=message) as failure:
            with profiler.phase("pmtiles"):
                assert seen.wait(3), "background sampler did not run"
                if producer_fails:
                    raise ValueError("producer failed")
    if not producer_fails:
        assert isinstance(failure.value.__cause__, PermissionError)
    records = [json.loads(row.message) for row in caplog.records]
    result = next(row for row in records if row.get("event") == "wdpa_phase_resources")
    assert result["state"] == "failed"
    assert result["cgroup_memory_peak_bytes"] == 6 * 1024**3
    assert profiler.records[-1] == result



def test_proc_cache_keeps_pinned_inode_advice_errors_visible(proc_cache_view, monkeypatch):
    root, _state = proc_cache_view
    def denied(*_):
        raise PermissionError("owned inode cache advice denied")
    monkeypatch.setattr(os, "posix_fadvise", denied)
    with pytest.raises(PermissionError, match="owned inode"):
        resources.release_file_cache(root)
