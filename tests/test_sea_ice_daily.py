from __future__ import annotations

import datetime as dt
from dataclasses import replace
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from google.api_core.exceptions import NotFound, PreconditionFailed

import pytest
from types import SimpleNamespace

from ingestion.common import lifecycle

from ingestion.sea_ice_daily import run as sea_ice

VALID_FGB_SHA = "a" * 64
VALID_PMTILES_SHA = "b" * 64
VALID_METADATA_SHA = "c" * 64
VALID_SCHEMA_SHA = "d" * 64
NATIVE_TOOLS = ("gdalinfo", "gdal_translate", "gdal_calc.py", "gdal_polygonize.py", "ogr2ogr", "ogrinfo",
                "tippecanoe", "tippecanoe-decode", "pmtiles")
TOOLS = {name: {"path": f"/usr/bin/{name}", "version": "fixture 1"} for name in NATIVE_TOOLS}


def publication_record(source, outputs, attempt, bucket="bucket"):
    """Exercise the shared record producer for direct owned-publisher fixtures."""
    return lifecycle.published(sea_ice.ASSET.slug, attempt=attempt, source=sea_ice.source_description(source),
        content=lifecycle.Content(sea_ice.fingerprints.Fingerprint("fixture", {}).record_fields(), None),
        built=lifecycle.Built(outputs.row_count, TOOLS,
            f"gs://{bucket}/{sea_ice.ASSET.release_prefix(source.documented_valid_date)}/"))


def runnable_command(command: list[str]) -> bool:
    executable = shutil.which(command[0])
    if not executable:
        return False
    try:
        completed = subprocess.run(
            [executable, *command[1:]],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def fake_asset_outputs(tmp_path: Path, *, release: str = "2026-04-28") -> sea_ice.AssetOutputs:
    fgb = tmp_path / "out.fgb"
    pmtiles = tmp_path / "out.pmtiles"
    metadata = tmp_path / "out.metadata.ndjson.gz"
    schema = tmp_path / "out.schema.json"
    manifest = tmp_path / "out.manifest.json"
    for path, data in (
        (fgb, b"fgb"),
        (pmtiles, b"pmtiles"),
        (metadata, b"metadata"),
        (schema, b'{"schema_version":2}\n'),
    ):
        path.write_bytes(data)
    schema_payload = {
        "schema_version": 2,
        "asset_slug": sea_ice.ASSET.slug,
        "release": release,
        "fields": [{"name": "ice_date", "type": "String", "nullable": False, "projectable": True}],
    }
    return sea_ice.AssetOutputs(
        fgb=fgb,
        pmtiles=pmtiles,
        metadata=metadata,
        schema=schema,
        manifest=manifest,
        row_count=2,
        sha256={
            "fgb": VALID_FGB_SHA,
            "pmtiles": VALID_PMTILES_SHA,
            "metadata": VALID_METADATA_SHA,
            "schema": VALID_SCHEMA_SHA,
        },
        schema_payload=schema_payload,
        next_generated_feature_id=1,
        previous_generated_feature_id=1,
        previous_release=None,
        identity_baseline_snapshot=None,
        identity_contract="test-v1",
        identity_decisions={
            "schema_version": 1,
            "policy": "identity_key_corroboration_v1",
            "ambiguities_detected": 0,
            "auto_resolved_key_corroborated": 0,
            "escalated_for_review": 0,
            "reviewed_decisions_applied": 0,
        },
    )


class SeaIceDailyTests(unittest.TestCase):
    def test_filename_uses_year_and_day_of_year(self):
        self.assertEqual(
            sea_ice.ims_filename_for_day(dt.date(2026, 4, 28)),
            "ims2026118_4km_GIS_v1.3.tif.gz",
        )

    def test_source_url_template_and_root_forms(self):
        source_date = dt.date(2026, 4, 28)
        self.assertEqual(
            sea_ice.source_url_for_day(
                "https://example.test/{yyyy}/{file_name}",
                source_date,
            ),
            "https://example.test/2026/ims2026118_4km_GIS_v1.3.tif.gz",
        )
        self.assertEqual(
            sea_ice.source_url_for_day("https://example.test/root", source_date),
            "https://example.test/root/2026/ims2026118_4km_GIS_v1.3.tif.gz",
        )

    def test_iter_source_days_is_newest_first(self):
        self.assertEqual(
            list(sea_ice.iter_source_days(dt.date(2026, 4, 29), 3)),
            [
                dt.date(2026, 4, 29),
                dt.date(2026, 4, 28),
                dt.date(2026, 4, 27),
            ],
        )

    def test_asset_paths(self):
        asset = sea_ice.ASSET
        run_date = dt.date(2026, 4, 28)
        self.assertEqual(
            asset.release_object(run_date, ".fgb"),
            (
                "200-imagery-derived/250-weather-climate/ims-sea-ice-extent/"
                "releases/2026-04-28/ims-sea-ice-extent.fgb"
            ),
        )
        self.assertEqual(
            asset.latest_object(".pmtiles"),
            (
                "200-imagery-derived/250-weather-climate/ims-sea-ice-extent/"
                "latest/ims-sea-ice-extent.pmtiles"
            ),
        )
        self.assertEqual(
            asset.run_record_object(run_date),
            (
                "200-imagery-derived/250-weather-climate/ims-sea-ice-extent/"
                "runs/2026-04-28.json"
            ),
        )

    def test_filename_date_and_documented_valid_date_are_distinct(self):
        source = sea_ice.AvailableSource(
            filename_date=dt.date(2026, 4, 28),
            source_url="https://example.test/source.tif.gz",
            source_filename="ims2026118_4km_GIS_v1.3.tif.gz",
        )
        self.assertEqual(source.filename_date, dt.date(2026, 4, 28))
        self.assertEqual(source.documented_valid_date, dt.date(2026, 4, 29))

    def test_new_contract_publication_preserves_source_date_and_uses_valid_date(self):
        source = sea_ice.AvailableSource(
            filename_date=dt.date(2026, 9, 29),
            source_url="https://example.test/ims2026272_4km_GIS_v1.3.tif.gz",
            source_filename="ims2026272_4km_GIS_v1.3.tif.gz",
        )
        publisher = mock.Mock()
        publisher.bucket.name = "test-bucket"
        with tempfile.TemporaryDirectory() as tmp:
            outputs = fake_asset_outputs(Path(tmp), release="2026-09-30")
            readme = Path(tmp) / "README.md"
            readme.write_text("# Sea ice\n")
            sea_ice.publish_outputs(
                publisher=publisher, asset=sea_ice.ASSET, outputs=outputs, source=source, asset_readme=readme,
                record=publication_record(source, outputs, dt.date(2026, 10, 2), "test-bucket"),
            )
        call = publisher.publish_generated.call_args.kwargs
        self.assertEqual(call["run_date"], dt.date(2026, 9, 30))
        self.assertEqual(call["record"]["release_date"], "2026-09-30")
        self.assertEqual(call["record"]["run_date"], "2026-10-02")
        self.assertEqual(call["record"]["source_filename_date"], "2026-09-29")
        self.assertEqual(call["object_metadata"]["run_date"], "2026-09-30")
        self.assertEqual(call["object_metadata"]["source_filename_date"], "2026-09-29")
        self.assertEqual(call["source_inputs"][0]["documented_valid_date"], "2026-09-30")

    def test_parse_text_ogrinfo_output(self):
        text = """
Layer name: ims_sea_ice_extent
Geometry: Multi Polygon
Feature Count: 12
FID Column = fid
Geometry Column = geom
DN: Integer (0.0)
ice_date: String (0.0)
"""
        self.assertEqual(
            sea_ice.parse_text_feature_count(text, Path("example.fgb")),
            12,
        )
        self.assertEqual(
            sea_ice.parse_text_layer_fields(text),
            ("DN", "ice_date"),
        )

    def test_convert_geojsonseq_to_fgb_uses_geojsonseq_driver_prefix(self):
        with mock.patch.object(sea_ice, "remove_if_exists"), mock.patch.object(
            sea_ice,
            "run_command",
        ) as run_command:
            sea_ice.convert_geojsonseq_to_fgb(
                Path("features.geojsonseq"),
                Path("out.fgb"),
            )

        command = run_command.call_args.args[0]
        self.assertEqual(command[-1], "GeoJSONSeq:features.geojsonseq")

    def test_probe_source_uses_common_request_helper(self):
        source_url = "https://example.test/source.tif.gz"
        outcome = sea_ice.RequestOutcome(
            status=sea_ice.STATUS_SUCCESS,
            url=source_url,
            attempts=1,
            http_status=200,
        )
        with mock.patch.object(
            sea_ice,
            "request_with_retries",
            return_value=(outcome, None),
        ) as request_with_retries:
            result = sea_ice.probe_source(source_url)

        self.assertEqual(result, outcome)
        request_with_retries.assert_called_once()
        kwargs = request_with_retries.call_args.kwargs
        self.assertEqual(kwargs["not_ready_status_codes"], frozenset({404}))
        self.assertEqual(kwargs["timeout_seconds"], sea_ice.REQUEST_TIMEOUT_SECONDS)

    def test_find_latest_available_source_continues_after_transient_probe(self):
        anchor_day = dt.date(2026, 5, 12)

        def fake_probe(source_url: str) -> sea_ice.RequestOutcome:
            if "ims2026132" in source_url:
                return sea_ice.RequestOutcome(
                    status=sea_ice.STATUS_TRANSIENT,
                    url=source_url,
                    attempts=3,
                    http_status=500,
                    reason="HTTP 500",
                )
            if "ims2026131" in source_url:
                return sea_ice.RequestOutcome(
                    status=sea_ice.STATUS_SUCCESS,
                    url=source_url,
                    http_status=200,
                    attempts=1,
                )
            self.fail(f"unexpected probe URL: {source_url}")

        with mock.patch.object(sea_ice, "probe_source", side_effect=fake_probe):
            lookup = sea_ice.find_latest_available_source(
                "https://example.test/{yyyy}/{file_name}",
                anchor_day,
                2,
            )

        self.assertIsNotNone(lookup.source)
        self.assertEqual(lookup.source.filename_date, dt.date(2026, 5, 11))
        self.assertEqual(len(lookup.source_request_warnings), 1)
        self.assertEqual(lookup.source_request_warnings[0]["http_status"], 500)

    def test_find_latest_available_source_continues_after_missing_probe(self):
        anchor_day = dt.date(2026, 5, 12)

        def fake_probe(source_url: str) -> sea_ice.RequestOutcome:
            if "ims2026132" in source_url:
                return sea_ice.RequestOutcome(
                    status=sea_ice.STATUS_NOT_READY,
                    url=source_url,
                    http_status=404,
                    attempts=1,
                )
            if "ims2026131" in source_url:
                return sea_ice.RequestOutcome(
                    status=sea_ice.STATUS_SUCCESS,
                    url=source_url,
                    http_status=200,
                    attempts=1,
                )
            self.fail(f"unexpected probe URL: {source_url}")

        with mock.patch.object(sea_ice, "probe_source", side_effect=fake_probe):
            lookup = sea_ice.find_latest_available_source(
                "https://example.test/{yyyy}/{file_name}",
                anchor_day,
                2,
            )

        self.assertIsNotNone(lookup.source)
        self.assertEqual(lookup.source.filename_date, dt.date(2026, 5, 11))
        self.assertEqual(lookup.source_request_warnings, ())

class StoredBlob:
    """GCS read surface over the owned publisher's immutable fake generations."""
    def __init__(self, store, name, generation=None):
        self.store, self.name, self.generation = store, name, generation
        self.uri = f"gs://bucket/{name}"

    def reload(self):
        version = self.store.inspect(self.uri, self.generation)
        if version is None:
            raise NotFound(self.uri)
        self.generation, self.size = version.generation, version.size

    def download_as_bytes(self, *, if_generation_match=None):
        self.reload()
        if if_generation_match is not None and self.generation != if_generation_match:
            raise PreconditionFailed(self.uri)
        return self.store.history[(self.uri, self.generation)][1]

    def download_as_text(self):
        return self.download_as_bytes().decode()


@pytest.fixture
def daily(tmp_path, monkeypatch):
    from test_owned_publication import DATE, outputs_fixture, publisher_fixture

    publisher, store = publisher_fixture(sea_ice.ASSET)
    bucket = SimpleNamespace(name="bucket", blob=lambda name, generation=None: StoredBlob(store, name, generation))
    client = SimpleNamespace(bucket=lambda name: bucket)
    readme = tmp_path / "README.md"
    readme.write_text("# IMS Sea-Ice Extent\n")
    source = sea_ice.AvailableSource(DATE - dt.timedelta(days=1), "https://example.test/ims",
                                    sea_ice.ims_filename_for_day(DATE - dt.timedelta(days=1)))
    warnings = ({"http_status": 500, "source_date": DATE.isoformat()},)
    state = SimpleNamespace(source=source, samples=b"\x03\x01", warnings=warnings,
                            publisher=publisher, store=store, release=DATE, attempt=DATE + dt.timedelta(days=3))
    monkeypatch.setenv("SHARED_DATASETS_WORKDIR", str(tmp_path))
    monkeypatch.setattr(sea_ice.storage, "Client", lambda **kwargs: client)
    monkeypatch.setattr(sea_ice.GcsPublisher, "from_runtime", lambda *args, **kwargs: state.publisher)
    monkeypatch.setattr(sea_ice, "require_binary", mock.Mock())
    monkeypatch.setattr(sea_ice, "toolchain", mock.Mock(return_value=TOOLS), raising=False)
    lookup = mock.Mock(side_effect=lambda *args, **kwargs: sea_ice.SourceLookupResult(state.source, state.warnings))
    monkeypatch.setattr(sea_ice, "find_latest_available_source", lookup)
    state.lookup = lookup

    def download(url, path):
        import gzip
        path.write_bytes(gzip.compress(state.samples))

    def gdal(command, **kwargs):
        if command[0] == "gdalinfo":
            return {"size": [2, 1], "geoTransform": [0, 1, 0, 0, 0, -1],
                    "coordinateSystem": {"wkt": "EPSG:4326"}, "bands": [{"type": "Byte", "noDataValue": 0}]}
        assert command[0] == "gdal_translate"
        Path(command[-1]).write_bytes(Path(command[-2]).read_bytes())

    monkeypatch.setattr(sea_ice, "download_file", download)
    monkeypatch.setattr(sea_ice, "run_command", gdal)
    monkeypatch.setattr("ingestion.common.owned_publication.vector_asset.validate_metadata_lookup_bundle",
                        lambda *args: SimpleNamespace(valid=True, errors=()))

    def build(**kwargs):
        baseline = kwargs["baseline"]
        assert kwargs["identity_resolution_decisions"] == []
        output = outputs_fixture(kwargs["workdir"], sea_ice.ASSET,
                                 date=sea_ice.documented_valid_date_for_filename_date(kwargs["source_date"]))
        return replace(output, previous_release=baseline.release, identity_baseline_snapshot=baseline.snapshot,
                       previous_generated_feature_id=baseline.next_feature_id)

    state.build = mock.Mock(side_effect=build)
    monkeypatch.setattr(sea_ice, "build_outputs", state.build)
    decisions = mock.Mock(return_value=[])
    monkeypatch.setattr(sea_ice.feature_metadata.release_feature_model, "load_identity_resolution_decisions", decisions)
    state.decisions = decisions
    # The installed image supplies this path; the fixture supplies the same bytes.
    original_publish = sea_ice.publish_outputs
    def publish(**kwargs):
        kwargs["asset_readme"] = readme
        return original_publish(**kwargs)
    monkeypatch.setattr(sea_ice, "publish_outputs", publish)

    def execute(*, fresh=True, attempt=None):
        if fresh:
            state.publisher, _ = publisher_fixture(sea_ice.ASSET, execution=f"execution-{store.serial}", store=store)
        state.publisher.client, state.publisher.bucket = client, bucket
        monkeypatch.setenv("RUN_DATE", (attempt or state.attempt).isoformat())
        return sea_ice.run()
    state.execute = execute
    state.index = lambda: store.read_json("gs://bucket/_catalog/releases/ims-sea-ice-extent.json").value
    state.artifacts = lambda: {uri: version.identity() for uri, version in store.objects.items()
        if uri.startswith(f"gs://bucket/{sea_ice.ASSET.root}/")
        and any(part in uri for part in ("/releases/", "/latest/", "/runs/"))}
    return state


def test_success_record_carries_native_tools_and_both_dates(daily):
    record = daily.execute()
    stored = daily.store.read_json(record["run_record"]["path"]).value
    assert stored["tools"] == TOOLS
    assert (stored["run_date"], stored["release_date"]) == ("2026-10-04", "2026-10-01")
    assert stored["source_filename_date"] == "2026-09-30"
    assert stored["source_request_warnings"] == list(daily.warnings)
    assert record["run_record"]["path"].endswith("/runs/2026-10-01.json")
    assert daily.index()["latest_run"]["date"] == "2026-10-04"
    assert daily.index()["latest_release"]["date"] == "2026-10-01"
    daily.decisions.assert_called_once_with(asset_slug=sea_ice.ASSET.slug, release="2026-10-01")


def test_published_source_second_attempt_skips_without_dataset_writes(daily):
    published = daily.execute()
    artifacts = daily.artifacts()
    daily.build.reset_mock()
    # Same filename anchor, new execution: no run-record lookup by anchor date.
    skipped = daily.execute()
    assert skipped["status"] == "skipped"
    assert skipped["reason"] == lifecycle.UNCHANGED
    assert (skipped["run_date"], skipped["release_date"]) == ("2026-10-04", "2026-10-01")
    assert skipped["row_count"] == published["row_count"]
    assert skipped["source_fingerprint_hash"] == published["source_fingerprint_hash"]
    assert skipped["source_request_warnings"] == list(daily.warnings)
    assert daily.artifacts() == artifacts
    daily.build.assert_not_called()
    assert daily.index()["latest_release"]["date"] == "2026-10-01"
    assert daily.index()["latest_run"]["status"] == "skipped"
    assert daily.index()["latest_run"]["date"] == "2026-10-04"
    assert daily.publisher.latest_success_record(sea_ice.ASSET)["source_fingerprint_hash"] == published["source_fingerprint_hash"]


def test_changed_content_under_published_date_is_refused_before_build(daily):
    daily.execute()
    before = daily.artifacts()
    daily.samples = b"\x03\x03"
    daily.build.reset_mock()
    with pytest.raises(lifecycle.LifecycleError, match="cannot replace or predate"):
        daily.execute()
    daily.build.assert_not_called()
    assert daily.artifacts() == before


def test_new_filename_publishes_even_when_samples_are_identical(daily):
    first = daily.execute()
    source_day = daily.source.filename_date + dt.timedelta(days=1)
    daily.source = replace(daily.source, filename_date=source_day, source_filename=sea_ice.ims_filename_for_day(source_day))
    second = daily.execute(attempt=daily.attempt + dt.timedelta(days=1))
    assert second["status"] == "success"
    assert second["release_date"] == "2026-10-02"
    assert second["source_fingerprint_hash"] != first["source_fingerprint_hash"]
    assert [release["date"] for release in daily.index()["releases"]] == ["2026-10-02", "2026-10-01"]


def test_unavailable_source_skip_names_attempt_and_missing_valid_date(daily):
    daily.execute()
    before = daily.artifacts()
    daily.source = None
    daily.build.reset_mock()
    record = daily.execute(attempt=dt.date(2026, 10, 7))
    assert (record["status"], record["run_date"], record["release_date"]) == ("skipped", "2026-10-07", "2026-10-08")
    assert record["reason"] == "No IMS 4 km GeoTIFF available from 2026-10-07 within the last 14 day(s)"
    assert record["source_request_warnings"] == list(daily.warnings)
    assert daily.index()["latest_run"]["date"] == "2026-10-07"
    assert daily.index()["latest_run"]["status"] == "skipped"
    assert daily.index()["latest_release"]["date"] == "2026-10-01"
    daily.build.assert_not_called()
    assert daily.artifacts() == before
    # Skip receipt resumes before probing, just like a publication receipt.
    daily.lookup.reset_mock()
    assert daily.execute(fresh=False)["reason"] == record["reason"]
    daily.lookup.assert_not_called()


def test_deployed_success_without_tools_is_accepted_as_baseline(daily, monkeypatch):
    original = sea_ice.GcsPublisher.publish_generated
    def deployed_writer(publisher, **kwargs):
        kwargs["record"].pop("tools", None)
        return original(publisher, **kwargs)
    with monkeypatch.context() as old:
        old.setattr(sea_ice.GcsPublisher, "publish_generated", deployed_writer)
        result = daily.execute()
    assert "tools" not in daily.store.read_json(result["run_record"]["path"]).value
    assert daily.execute()["reason"] == lifecycle.UNCHANGED


def test_historical_success_without_fingerprint_cannot_skip_existing_date(daily, monkeypatch):
    original = sea_ice.GcsPublisher.publish_generated
    def historical_writer(publisher, **kwargs):
        kwargs["record"].pop("source_fingerprint_hash")
        kwargs["record"].pop("source_fingerprint")
        return original(publisher, **kwargs)
    with monkeypatch.context() as old:
        old.setattr(sea_ice.GcsPublisher, "publish_generated", historical_writer)
        daily.execute()
    before = daily.artifacts()
    with pytest.raises(lifecycle.LifecycleError, match="cannot replace or predate"):
        daily.execute()
    assert daily.artifacts() == before


def test_fingerprint_defect_is_not_reported_as_a_broken_baseline(daily, monkeypatch):
    daily.execute()
    monkeypatch.setattr(sea_ice, "fingerprint_source", mock.Mock(side_effect=TypeError("producer defect")))
    with pytest.raises(TypeError, match="producer defect"):
        daily.execute()


def test_completed_owned_execution_returns_before_polling(daily):
    first = daily.execute()
    daily.lookup.reset_mock()
    daily.build.reset_mock()
    before = list(daily.store.events)
    assert daily.execute(fresh=False) == first
    daily.lookup.assert_not_called()
    daily.build.assert_not_called()
    assert daily.store.events == before


@pytest.mark.parametrize("broken", ["manifest", "schema", "metadata"])
@pytest.mark.parametrize("changed", [False, True])
def test_broken_baseline_stops_before_source_or_build(daily, broken, changed):
    daily.execute()
    suffix = {"manifest": ".manifest.json", "schema": ".schema.json", "metadata": ".metadata.ndjson.gz"}[broken]
    uri = f"gs://bucket/{sea_ice.ASSET.release_object(daily.release, suffix)}"
    daily.store.write_bytes(uri, b"{}", daily.store.head(uri).generation, {}, "application/json", "")
    if changed:
        day = daily.source.filename_date + dt.timedelta(days=1)
        daily.source = replace(daily.source, filename_date=day, source_filename=sea_ice.ims_filename_for_day(day))
    daily.build.reset_mock()
    daily.lookup.reset_mock()
    before = list(daily.store.events)
    with pytest.raises(RuntimeError, match="reviewed repair"):
        daily.execute()
    daily.lookup.assert_not_called()
    daily.build.assert_not_called()
    assert daily.store.events == before


def test_empty_build_fails_without_claim_or_reservation(daily):
    original = daily.build.side_effect
    daily.build.side_effect = lambda **kwargs: replace(original(**kwargs), row_count=0)
    state = daily.publisher.state(sea_ice.ASSET).value
    with pytest.raises(RuntimeError, match="output contains no features"):
        daily.execute()
    assert daily.publisher.state(sea_ice.ASSET).value == state


def test_partial_daily_release_is_refused_before_build(daily):
    uri = f"gs://bucket/{sea_ice.ASSET.release_object(daily.release, '.fgb')}"
    daily.store.write_bytes(uri, b"partial", 0, {}, "application/octet-stream", "")
    with pytest.raises(RuntimeError, match="already exist without a successful run record"):
        daily.execute()
    daily.build.assert_not_called()


@pytest.mark.parametrize("checkpoint_writes", [4, 14])
def test_interrupted_publication_resumes_before_source_and_keeps_reservation(daily, checkpoint_writes):
    from test_publication import LostResponse
    from ingestion.common.publication import NotRecoverableSource
    daily.store.events.clear()
    daily.store.fail_after = checkpoint_writes
    with pytest.raises(LostResponse):
        daily.execute()
    interrupted = daily.publisher.state(sea_ice.ASSET).value
    assert interrupted["active"] is not None
    assert interrupted["reserved_next_feature_id"] == 3
    daily.store.fail_after = None
    daily.build.reset_mock()
    daily.lookup.reset_mock()
    if checkpoint_writes == 4:
        with pytest.raises(NotRecoverableSource, match="reservation retained"):
            daily.execute(fresh=False)
        assert daily.publisher.state(sea_ice.ASSET).value == interrupted
    else:
        assert daily.execute(fresh=False)["status"] == "success"
    daily.lookup.assert_not_called()
    daily.build.assert_not_called()
    assert daily.publisher.state(sea_ice.ASSET).value["reserved_next_feature_id"] == 3


def test_tools_are_checked_before_owned_resume_or_claim(daily, monkeypatch):
    resume = mock.Mock()
    monkeypatch.setattr(daily.publisher, "resume", resume)
    monkeypatch.setattr(sea_ice, "toolchain", mock.Mock(side_effect=RuntimeError("missing native tool")))
    before = list(daily.store.events)
    with pytest.raises(RuntimeError, match="missing native tool"):
        daily.execute(fresh=False)
    resume.assert_not_called()
    assert daily.store.events == before


def test_toolchain_records_all_paths_and_the_installed_suite_versions(tmp_path, monkeypatch):
    for name in NATIVE_TOOLS:
        path = tmp_path / name
        no_version = name in {"tippecanoe-decode", "gdal_calc.py", "gdal_polygonize.py"}
        path.write_text("#!/bin/sh\n" + ("exit 99\n" if no_version else f"echo '{name} 1.0'\n"))
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    tools = sea_ice.toolchain()
    assert set(tools) == set(NATIVE_TOOLS)
    for name, item in tools.items():
        reporter = {"gdal_calc.py": "gdalinfo", "gdal_polygonize.py": "gdalinfo",
                    "tippecanoe-decode": "tippecanoe"}.get(name, name)
        assert item == {"path": str(tmp_path / name), "version": f"{reporter} 1.0"}


def test_sea_ice_has_no_percentage_rule_or_hold_flow(tmp_path):
    refresh = sea_ice.Refresh(None, tmp_path, tools=TOOLS, source_template="fixture", max_lookback_days=14,
                              asset_readme=tmp_path / "README.md")
    assert refresh.loss_limit is None
    with pytest.raises(lifecycle.LifecycleError, match="has no hold flow: empty_source"):
        refresh.hold(lifecycle.EMPTY_SOURCE, None, None, None)


@unittest.skipUnless(
    os.environ.get("RUN_GDAL_INTEGRATION_TESTS") == "1"
    and all(
        runnable_command(command)
        for command in (
            ["gdal_create", "--help-general"],
            ["gdal_calc.py", "--help"],
            ["gdal_polygonize.py", "--help"],
            ["ogr2ogr", "--version"],
            ["ogrinfo", "--version"],
            ["tippecanoe", "--version"],
            ["pmtiles", "version"],
        )
    )
    and shutil.which("tippecanoe-decode"),
    "requires runnable GDAL, Tippecanoe, tippecanoe-decode, and PMTiles binaries",
)
class SeaIceDailyIntegrationTests(unittest.TestCase):
    def test_synthetic_raster_builds_fgb_and_pmtiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_tif = tmp_path / "source.tif"
            sea_ice.run_command(
                [
                    "gdal_create",
                    "-of",
                    "GTiff",
                    "-outsize",
                    "4",
                    "4",
                    "-bands",
                    "1",
                    "-burn",
                    "3",
                    "-ot",
                    "Byte",
                    "-a_srs",
                    "EPSG:3857",
                    "-a_ullr",
                    "-100000",
                    "100000",
                    "100000",
                    "-100000",
                    str(source_tif),
                ]
            )

            source_date = dt.date(2026, 4, 28)
            source = sea_ice.DownloadedSource(source_date, "https://example.test/ims",
                sea_ice.ims_filename_for_day(source_date), tmp_path / "source.tif.gz")
            fingerprint = sea_ice.fingerprint_source(source, source_tif, tmp_path)
            repacked = tmp_path / "repacked.tif"
            sea_ice.run_command(["gdal_translate", "-q", "-co", "COMPRESS=LZW", str(source_tif), str(repacked)])
            self.assertEqual(fingerprint, sea_ice.fingerprint_source(source, repacked, tmp_path))
            tomorrow = source_date + dt.timedelta(days=1)
            new_source = sea_ice.DownloadedSource(tomorrow, source.source_url,
                sea_ice.ims_filename_for_day(tomorrow), source.gz_path)
            self.assertNotEqual(fingerprint, sea_ice.fingerprint_source(new_source, source_tif, tmp_path))

            outputs = sea_ice.build_outputs(
                baseline=sea_ice.feature_metadata.release_feature_model.GeneratedIdentityBaseline.genesis(contract_id="test-v1"),
                source_tif=source_tif,
                source_date=dt.date(2026, 4, 28),
                workdir=tmp_path,
            )

            self.assertGreater(outputs.row_count, 0)
            self.assertTrue(outputs.fgb.exists())
            self.assertTrue(outputs.pmtiles.exists())
            self.assertTrue(outputs.metadata.exists())
            self.assertTrue(outputs.schema.exists())
            self.assertEqual(outputs.schema_payload["release"], "2026-04-29")
            records = list(sea_ice.feature_metadata.release_feature_model.read_metadata_sidecar(outputs.metadata))
            self.assertTrue(records)
            self.assertTrue(all(record["properties"]["ice_date"] == "2026-04-28" for record in records))


if __name__ == "__main__":
    unittest.main()
