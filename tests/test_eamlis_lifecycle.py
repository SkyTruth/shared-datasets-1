"""e-AMLIS entrypoint -> persisted records, holds and reviewed candidates."""
import csv
import datetime as dt
import io
import json
from pathlib import Path
from unittest import mock

import pytest

from ingestion.common.source_quality import EAMLIS as quality
from ingestion.eamlis_monthly import run as job
from scripts import eamlis_source_loss_review as review, slack_notify
from tests.test_eamlis_monthly import TOOLS, sample_source_state
from tests.test_source_loss import Client, execute, fixture_output


def features(ids):
    return [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [index, 40]},
             "properties": {"OBJECTID": index, "PA_NAME": "Shared mine name"}} for index in ids]


def run_features(client, rows, date, *, source=None, builder=fixture_output):
    def download(*, source, dest, page_size):
        dest.write_text(json.dumps({"type": "FeatureCollection", "features": rows}))
        return job.SourceExtract(dest, len(rows), 0)
    with mock.patch.dict("os.environ", {"RUN_DATE": date}, clear=True), \
         mock.patch.object(job.storage, "Client", return_value=client), \
         mock.patch.object(job, "toolchain", return_value=TOOLS) as tools, \
         mock.patch.object(job, "fetch_source_state", return_value=source or sample_source_state(feature_count=len(rows))), \
         mock.patch.object(job, "download_source_geojson", side_effect=download), \
         mock.patch.object(job, "build_asset_output", side_effect=builder):
        result = job.run()[0]
        tools.assert_called_once()
        return result


@pytest.mark.parametrize("ids,status,lost", [(range(11, 111), "held", 10), (range(1, 99), "success", 2)])
def test_equal_size_swap_holds_and_small_net_drop_publishes(ids, status, lost):
    client = Client()
    run_features(client, features(range(1, 101)), "2026-04-02")
    bucket = client.bucket(quality.BUCKET)
    latest = bucket.blob(job.ASSET.latest_object(".fgb")).data
    with mock.patch.object(slack_notify, "notify") as send:
        result = run_features(client, features(ids), "2026-05-02")
    assert result["status"] == status
    if status == "held":
        assert result["reason"] == "objects_lost"
        assert result["hold"]["observation"]["lost_objects"] == lost
        assert result["hold"]["observation"]["maximum_lost_object_percent"] == 5
        assert result["hold"]["candidate"]
        assert bucket.blob(job.ASSET.latest_object(".fgb")).data == latest
        assert "10 of 100 published" in send.call_args.kwargs["body"]
        plan = review.prepare_plan(client)
        assert quality.validate_candidate_plan(client, plan)["geometry_count"] == len(ids)
    else:
        assert result["lost_objects"] == lost
        assert json.loads(bucket.blob(job.ASSET.run_record_object(dt.date(2026, 5, 2))).data)["tools"] == TOOLS
        send.assert_not_called()


def test_unchanged_checkin_names_baseline_and_writes_no_dataset_artifact():
    client = Client()
    first = execute(client, 2, "2026-04-02")
    bucket = client.bucket(quality.BUCKET)
    before = {name: (blob.generation, blob.data) for name, blob in bucket.blobs.items()
              if blob.exists and name.startswith(job.ASSET.root + "/") and ("/releases/" in name or "/latest/" in name)}
    result = execute(client, 2, "2026-05-02", build=mock.Mock(side_effect=AssertionError("unchanged built artifacts")))
    assert (result["status"], result["run_date"], result["release_date"]) == ("skipped", "2026-05-02", "2026-04-02")
    assert result["source_fingerprint_hash"] == first["source_fingerprint_hash"]
    assert {name: (blob.generation, blob.data) for name, blob in bucket.blobs.items()
            if blob.exists and name.startswith(job.ASSET.root + "/") and ("/releases/" in name or "/latest/" in name)} == before
    stored = json.loads(bucket.blob(job.ASSET.run_record_object(dt.date(2026, 5, 2))).data)
    assert stored["release_date"] == "2026-04-02"
    index = json.loads(bucket.blob(quality.INDEX).data)
    assert index["latest_run"]["date"] == "2026-05-02"
    assert index["latest_release"]["date"] == "2026-04-02"


@pytest.mark.parametrize("broken", ["manifest", "metadata", "schema", "count"])
def test_broken_baseline_stops_before_source_read_or_rebuild(broken):
    client = Client()
    execute(client, 2, "2026-04-02")
    bucket = client.bucket(quality.BUCKET)
    name = job.ASSET.latest_object(".manifest.json") if broken in {"manifest", "count"} else job.ASSET.release_object(
        dt.date(2026, 4, 2), ".metadata.ndjson.gz" if broken == "metadata" else ".schema.json")
    blob = bucket.blob(name)
    if broken == "manifest":
        blob.data = b"{}"
    elif broken == "count":
        value = json.loads(blob.data)
        value["validation"]["feature_count"] = 3
        blob.data = quality.encode(value)
    else:
        blob.data = b"corrupted baseline"
    with mock.patch.object(job, "fetch_source_state") as fetch, \
         mock.patch.object(job, "build_asset_output") as build, \
         pytest.raises(RuntimeError, match="reviewed repair required"):
        # The real entrypoint uses the corrupted persisted publication.
        with mock.patch.dict("os.environ", {"RUN_DATE": "2026-05-02"}, clear=True), \
             mock.patch.object(job.storage, "Client", return_value=client), mock.patch.object(job, "toolchain", return_value=TOOLS):
            job.run()
    fetch.assert_not_called()
    build.assert_not_called()
    assert not bucket.blob(job.ASSET.run_record_object(dt.date(2026, 5, 2))).exists


@pytest.mark.parametrize("broken", ["replacement_pair", "missing_manifest_hash"])
def test_indexed_publication_authenticates_baseline_before_source_read(tmp_path, broken):
    from tests.test_source_loss import replace_baseline_sidecar

    client = Client()
    run_features(client, features([1, 2, 3]), "2026-04-02")
    bucket = client.bucket(quality.BUCKET)
    fgb = bucket.blob(job.ASSET.release_object(dt.date(2026, 4, 2), ".fgb")).data
    if broken == "replacement_pair":
        _, records = job.feature_metadata.enrich_features_with_source_field_ids(features([4, 5, 6]),
            asset_slug=job.ASSET.slug, release="2026-04-02", id_field="OBJECTID", provenance={"source": "fixture"})
        replace_baseline_sidecar(bucket, job.ASSET, records, tmp_path)
    else:
        blob = bucket.blob(job.ASSET.run_record_object(dt.date(2026, 4, 2)))
        record = json.loads(blob.data)
        del record["sha256"]["manifest"]
        blob.upload_from_string(quality.encode(record), if_generation_match=blob.generation)
    rows = features([4, 5, 6] if broken == "replacement_pair" else [1, 2, 3])
    def acquire(*, source, dest, page_size):
        dest.write_bytes(quality.encode({"type": "FeatureCollection", "features": rows}))
        return job.SourceExtract(dest, len(rows), 0)
    with mock.patch.object(job, "toolchain", return_value=TOOLS), \
         mock.patch.object(job, "fetch_source_state", return_value=sample_source_state(feature_count=3)) as fetch, \
         mock.patch.object(job, "download_source_geojson", side_effect=acquire) as download, \
         mock.patch.object(job, "build_asset_output", side_effect=fixture_output) as build, \
         mock.patch.object(slack_notify, "notify") as send:
        with pytest.raises(RuntimeError, match="baseline.*reviewed repair required"):
            job.ingest(job.GcsPublisher(client, quality.BUCKET), dt.date(2026, 5, 2), tmp_path,
                layer_url=job.DEFAULT_LAYER_URL, where=job.DEFAULT_WHERE, page_size=2000)
        fetch.assert_not_called()
        download.assert_not_called()
        build.assert_not_called()
        send.assert_not_called()
    assert bucket.blob(job.ASSET.release_object(dt.date(2026, 4, 2), ".fgb")).data == fgb
    assert quality.Guard(bucket).load()[0]["hold"] is None


def test_publish_then_rerun_fingerprints_reconciled_translation_source():
    client = Client()
    run_features(client, features([1]), "2026-04-02")
    bucket = client.bucket(quality.BUCKET)
    csv_blob = bucket.blob(job.ASSET.latest_object(".metadata-translations.csv"))
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=job.translation_reuse.COLUMNS)
    writer.writeheader()
    writer.writerow({"feature_id": "99", "field": "PA_NAME", "locale": "es",
        "source_value_hash": job.localization.source_value_hash("Shared mine name"),
        "value": "Nombre compartido", "review_state": "human_reviewed", "notes": "reviewed source"})
    csv_blob.upload_from_string(stream.getvalue(), if_generation_match=csv_blob.generation)
    published = run_features(client, features([1, 2]), "2026-05-02")
    assert published["status"] == "success"
    csv_blob = bucket.blob(job.ASSET.latest_object(".metadata-translations.csv"))
    assert {row["feature_id"] for row in csv.DictReader(io.StringIO(csv_blob.data.decode()))} == {"99", "1", "2"}
    manifest = json.loads(bucket.blob(job.ASSET.latest_object(".manifest.json")).data)
    assert manifest["source_inputs"][0]["source_fingerprint_hash"] == published["source_fingerprint_hash"]
    stored = json.loads(bucket.blob(job.ASSET.run_record_object(dt.date(2026, 5, 2))).data)
    assert stored["source_fingerprint_hash"] == published["source_fingerprint_hash"]
    second = run_features(client, features([1, 2]), "2026-05-03",
        builder=mock.Mock(side_effect=AssertionError("fingerprint drift rebuilt a release")))
    assert (second["status"], second["release_date"]) == ("skipped", "2026-05-02")
    assert second["source_fingerprint_hash"] == published["source_fingerprint_hash"]


def legacy_candidate(client, tmp_path):
    execute(client, 3, "2026-04-02")
    bucket = client.bucket(quality.BUCKET)
    # Earlier successful records have no native toolchain.
    prior = bucket.blob(job.ASSET.run_record_object(dt.date(2026, 4, 2)))
    record = json.loads(prior.data)
    record.pop("tools", None)
    prior.data = quality.encode(record)
    prior.text = prior.data.decode()
    with quality.Guard(bucket) as gate:
        gate.block("geometry_count_drop", {"geometry_count": 2, "maximum_drop_percent": 5})
        source = sample_source_state(feature_count=2)
        extract = job.SourceExtract(tmp_path / "source.geojson", 2, 0)
        extract.geojson.write_text(json.dumps({"type": "FeatureCollection", "features": features([1, 2])}))
        output = fixture_output(source=source, extract=extract, workdir=tmp_path, release_date=dt.date(2026, 5, 2))
        translation, translation_inputs = job.translation_input(job.GcsPublisher(client, quality.BUCKET), tmp_path)
        state = job.fingerprint_source(source, extract, translation)
        reports = job.localization.materialize_locale_sidecars(canonical_sidecar=output.metadata,
            translation_source=translation, output_dir=tmp_path, locales=job.TRANSLATION_LOCALES,
            translatable_fields=set(job.TRANSLATION_FIELDS), expected_asset_slug=quality.SLUG,
            expected_release="2026-05-02", protected_inputs=[output.schema])
        # This is the previous image's seed/record shape: no lifecycle evidence or tools.
        quality.stage_candidate(gate, output=output, fingerprint=state.fingerprint, release="2026-05-02",
            source_inputs=[{"uri": state.layer_url, "where": state.where}, *translation_inputs], identity=output.identity_metadata,
            geometry_hash_algorithm=output.geometry_hash_algorithm,
            record={"source": state.layer_url, "source_version": state.fingerprint_hash,
                    "source_fingerprint_hash": state.fingerprint_hash}, source_input=extract.geojson,
            translations=job.localization.translations_payload(reports),
            extra_files=[(".metadata-translations.csv", translation),
                *((f".metadata.{r.locale}.ndjson.gz", Path(r.output_sidecar)) for r in reports)])
    legacy = review.prepare_plan(client)
    assert quality.validate_candidate_plan(client, legacy)["geometry_count"] == 2
    return legacy


def test_legacy_hold_candidate_and_records_remain_reviewable(tmp_path):
    client = Client()
    legacy = legacy_candidate(client, tmp_path)
    with mock.patch.object(slack_notify, "notify") as send:
        # Restaging the exact source/release must not overwrite the old seed's
        # manifest or run record with this image's changed record contract.
        migrated = run_features(client, features([1, 2]), "2026-05-02")
        assert migrated["hold"]["candidate"] != legacy["source_loss_review"]["candidate"]
        held = run_features(client, features([1, 2, 3]), "2026-05-03")
        again = run_features(client, features([1, 2, 3]), "2026-05-04")
    assert held["reason"] == again["reason"] == "geometry_count_drop"
    assert held["hold"]["id"] == legacy["source_loss_review"]["hold_id"]
    assert send.call_count == 1
    assert quality.validate_candidate_plan(client, review.prepare_plan(client))["geometry_count"] == 3


def test_legacy_hold_raised_after_recording_is_announced_once():
    client = Client()
    execute(client, 2, "2026-04-02")
    with quality.Guard(client.bucket(quality.BUCKET)) as gate:
        gate.block("geometry_count_drop", {"geometry_count": 1, "maximum_drop_percent": 5})
    with mock.patch.object(slack_notify, "notify") as send:
        first = run_features(client, features([1, 2]), "2026-04-02")
        second = run_features(client, features([1, 2]), "2026-04-02")
    assert first["status"] == second["status"] == "held"
    assert first["reason"] == "geometry_count_drop"
    send.assert_called_once()


def test_empty_source_reserves_nothing_and_needs_no_translation_bundle():
    client = Client()
    with mock.patch.object(job.identity_state, "reserve") as reserve, \
         mock.patch.object(job, "translation_input", side_effect=AssertionError("empty source read translations")), \
         mock.patch.object(slack_notify, "notify") as send:
        result = run_features(client, [], "2026-05-02", builder=mock.Mock(side_effect=AssertionError("empty source built")))
    assert result["status"] == "held" and result["reason"] == "empty_source"
    assert result["hold"]["candidate"] is None
    reserve.assert_not_called()
    send.assert_called_once()


def test_selection_change_retains_candidate_and_holds_without_count_loss():
    client = Client()
    run_features(client, features([1, 2]), "2026-04-02")
    selected = job.replace(sample_source_state(), where="LAT_DEG >= 0")
    with mock.patch.object(slack_notify, "notify") as send:
        result = run_features(client, features([1, 2]), "2026-05-02", source=selected)
    assert result["status"] == "held" and result["reason"] == "source_selection_changed"
    assert result["hold"]["candidate"] and result["hold"]["observation"]["lost_objects"] == 0
    send.assert_called_once()


def test_failure_after_legacy_publication_started_persists_and_announces_hold():
    client = Client()
    with mock.patch.object(job.vector_pipeline, "publish_vector_bundle", side_effect=RuntimeError("write failed")), \
         mock.patch.object(slack_notify, "notify") as send, pytest.raises(RuntimeError, match="write failed"):
        run_features(client, features([1]), "2026-05-02")
    state, _ = quality.Guard(client.bucket(quality.BUCKET)).load()
    assert state["hold"]["reason"] == "identity_publication_requires_review"
    assert state["owner"] is None
    send.assert_called_once()


def test_toolchain_records_every_native_binary_and_suite_version(tmp_path, monkeypatch):
    for name in ("ogrinfo", "ogr2ogr", "pmtiles", "tippecanoe", "tippecanoe-decode"):
        path = tmp_path / name
        path.write_text("#!/bin/sh\n" + ("exit 99\n" if name == "tippecanoe-decode" else f"echo '{name} 1.0'\n"))
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    tools = job.toolchain()
    assert set(tools) == set(TOOLS)
    for name, value in tools.items():
        assert value["path"] == str((tmp_path / name).resolve())
        assert value["version"] == ("tippecanoe 1.0" if name == "tippecanoe-decode" else f"{name} 1.0")


def test_missing_native_tool_precedes_keyed_reservation_and_control_writes(tmp_path):
    from tests.test_eamlis_identity_transition import prepared, install, run, output
    from tests.test_eamlis_identity import problem, feature

    original = [problem("P1", OBJECTID=1)]
    client, plan, _ = prepared(tmp_path, history=[original])
    install(client, plan, tmp_path)
    run(client, original, "2026-05-02")
    bucket = client.bucket(quality.BUCKET)
    before = {name: (blob.generation, blob.data) for name, blob in bucket.blobs.items() if blob.exists}
    rows = [feature(p) for p in [*original, problem("NEW", OBJECTID=2)]]
    def download(*, source, dest, page_size):
        dest.write_bytes(quality.encode({"type": "FeatureCollection", "features": rows}))
        return job.SourceExtract(dest, len(rows), 0)
    with mock.patch.object(job.shutil, "which", return_value=None), \
         mock.patch.object(job, "fetch_source_state", return_value=sample_source_state(feature_count=2)), \
         mock.patch.object(job, "download_source_geojson", side_effect=download), \
         mock.patch.object(job, "build_asset_output", side_effect=output), \
         mock.patch.object(job.identity_state, "reserve", wraps=job.identity_state.reserve) as reserve, \
         mock.patch.object(slack_notify, "notify") as send:
        with pytest.raises(RuntimeError, match="Required executable not found on PATH: ogr2ogr"):
            job.ingest(job.GcsPublisher(client, quality.BUCKET), dt.date(2026, 5, 3), tmp_path,
                layer_url=job.DEFAULT_LAYER_URL, where=job.DEFAULT_WHERE, page_size=2000)
        reserve.assert_not_called()
        send.assert_not_called()
    state = json.loads(bucket.blob(quality.CONTROL).data)
    assert state["hold"] is None
    assert {name: (blob.generation, blob.data) for name, blob in bucket.blobs.items() if blob.exists} == before


@pytest.mark.parametrize("lost,status", [(1, "success"), (3, "held")])
def test_keyed_gate_counts_permanent_ids_after_cutover(tmp_path, lost, status):
    from tests.test_eamlis_identity_transition import prepared, install, run
    from tests.test_eamlis_identity import problem
    from ingestion.eamlis_monthly import identity_state
    original = [problem(f"P{n:03}", OBJECTID=100-n) for n in range(40)]
    client, plan, _ = prepared(tmp_path, history=[original])
    install(client, plan, tmp_path)
    first = run(client, original, "2026-05-02")
    # The cutover must translate legacy IDs to permanent keys before counting.
    assert first["status"] == "success" and first["lost_objects"] == 0
    replacement = [*original[lost:], *(problem(f"NEW{n}", OBJECTID=200+n) for n in range(lost))]
    result = run(client, replacement, "2026-05-03")
    assert result["status"] == status
    if status == "held":
        assert result["reason"] == "objects_lost"
        assert result["hold"]["observation"]["lost_objects"] == lost
    else:
        assert result["lost_objects"] == lost
    with quality.Guard(client.bucket(quality.BUCKET)) as gate:
        ledger = identity_state.selected_ledger(gate)
        assert ledger.next_feature_id == 41 + lost
        assert ledger.ids[("problem", original[0]["PROBLEM_KEY"])]


def test_discovery_failure_precedes_reservation_and_persists_identity_hold():
    client = Client()
    with mock.patch.object(job.publication_readiness, "discover_batch", return_value={"state": "BLOCKED"}), \
         mock.patch.object(job.identity_state, "reserve") as reserve, \
         mock.patch.object(slack_notify, "notify") as send, \
         pytest.raises(job.keyed_identity.IdentityError, match="discovery did not complete"):
        run_features(client, features([1]), "2026-05-02")
    reserve.assert_not_called()
    assert quality.Guard(client.bucket(quality.BUCKET)).load()[0]["hold"]
    send.assert_called_once()


def test_keyed_candidate_staging_failure_preserves_loss_request_and_original_error(tmp_path):
    from tests.test_eamlis_identity_transition import prepared, install, run, output
    from tests.test_eamlis_identity import problem, feature
    from ingestion.eamlis_monthly import identity_state

    original = [problem(f"P{n:03}", OBJECTID=100-n) for n in range(40)]
    client, plan, _ = prepared(tmp_path, history=[original])
    install(client, plan, tmp_path)
    run(client, original, "2026-05-02")
    replacement = [*original[3:], *(problem(f"NEW{n}", OBJECTID=200+n) for n in range(3))]
    rows = [feature(p) for p in replacement]
    def download(*, source, dest, page_size):
        dest.write_bytes(quality.encode({"type": "FeatureCollection", "features": rows}))
        return job.SourceExtract(dest, len(rows), 0)
    error = RuntimeError("candidate staging failed")
    bucket = client.bucket(quality.BUCKET)
    with mock.patch.object(job, "toolchain", return_value=TOOLS), \
         mock.patch.object(job, "fetch_source_state", return_value=sample_source_state(feature_count=40)), \
         mock.patch.object(job, "download_source_geojson", side_effect=download), \
         mock.patch.object(job, "build_asset_output", side_effect=output), \
         mock.patch.object(quality, "stage_candidate", side_effect=error), \
         mock.patch.object(slack_notify, "notify") as send:
        with pytest.raises(RuntimeError, match="candidate staging failed") as raised:
            job.ingest(job.GcsPublisher(client, quality.BUCKET), dt.date(2026, 5, 3), tmp_path,
                layer_url=job.DEFAULT_LAYER_URL, where=job.DEFAULT_WHERE, page_size=2000)
        assert raised.value is error
        with quality.Guard(bucket) as gate:
            assert identity_state.selected_ledger(gate).next_feature_id == 44
            hold = gate.state["hold"]
            assert hold["reason"] == "objects_lost"
            assert hold["observation"]["error"] == str(error)
            assert hold["observation"]["lost_objects"] == 3
            assert hold["observation"]["maximum_lost_object_percent"] == 5
            assert hold["notification"] == "sent"
            gate.notify()
        send.assert_called_once()
        assert "Lost objects: 3 of 40 published; limit: 5%" in send.call_args.kwargs["body"]


def test_legacy_skipped_record_returns_its_original_dates_without_tools():
    client = Client()
    execute(client, 2, "2026-04-02")
    execute(client, 2, "2026-05-02")
    blob = client.bucket(quality.BUCKET).blob(job.ASSET.run_record_object(dt.date(2026, 5, 2)))
    record = json.loads(blob.data)
    record["release_date"] = record["run_date"]
    record.pop("tools", None)
    blob.data = quality.encode(record)
    blob.text = blob.data.decode()
    with mock.patch.object(job, "fetch_source_state", side_effect=AssertionError("completed attempt polled source")):
        with mock.patch.dict("os.environ", {"RUN_DATE": "2026-05-02"}, clear=True), \
             mock.patch.object(job.storage, "Client", return_value=client), mock.patch.object(job, "toolchain", return_value=TOOLS):
            result = job.run()[0]
    assert result["release_date"] == "2026-05-02" and "tools" not in result
    assert blob.data == quality.encode(record)


def test_previous_image_candidate_promotes_through_reviewed_plan(tmp_path):
    from scripts import publish_workflow, gcs_asset, reviewed_dataset_plan
    from scripts import finalize_promoted_release_metadata as finalizer
    from tests.test_source_loss import copy_transport
    client = Client()
    plan = legacy_candidate(client, tmp_path)
    document = {"plan_version": 1, "finalization_version": reviewed_dataset_plan.FINALIZATION_VERSION, "publish": plan}
    document_path = reviewed_dataset_plan.write_document(document, repo_root=tmp_path)
    plan = reviewed_dataset_plan.read_document(document_path.read_bytes(),
        path=document_path.relative_to(tmp_path).as_posix())["publish"]
    path = tmp_path / "legacy-plan.json"
    path.write_bytes(quality.encode(plan))
    with mock.patch.object(quality, "protected_authority", return_value="reviewed-legacy-candidate"), \
         mock.patch.dict("os.environ", {"SHARED_DATASETS_ALLOW_CANONICAL_MUTATION": "1", "SHARED_DATASETS_BUCKET": quality.BUCKET}), \
         mock.patch.object(gcs_asset, "get_client", return_value=client):
        transport, copies = copy_transport(client)
        with mock.patch.object(publish_workflow.subprocess, "run", side_effect=transport):
            assert publish_workflow.main(["promote", "--plan-json", str(path)]) == 0
        assert len(copies) == len(plan["promotions"])
        finalizer.finalize_promoted_release_metadata(plan, client=client)
        job.GcsPublisher(client, quality.BUCKET, release_suffixes=job.RELEASE_SUFFIXES).record_existing_successful_release(
            job.ASSET, dt.date(2026, 5, 2))
        quality.finish_promotion(client, plan)
    state, _ = quality.Guard(client.bucket(quality.BUCKET)).load()
    assert state["hold"] is None and state["owner"] is None
    assert quality.baseline(client.bucket(quality.BUCKET))["release"]["date"] == "2026-05-02"
    verified = job.published_baseline.read(job.GcsPublisher(client, quality.BUCKET), job.ASSET)
    assert len(verified.records) == 2


def test_installed_image_baseline_probe_reads_keyed_publisher_output(tmp_path):
    from ingestion.eamlis_monthly import identity as ids, identity_state
    from scripts.installed_ingestion_fixture import baseline_fixture
    from tests.test_eamlis_identity import problem, feature
    from tests.test_eamlis_identity_transition import output

    rows = [feature(problem(f"P{n}", OBJECTID=n)) for n in (1, 2, 3)]
    ledger = ids.Ledger.genesis().reserve(ids.classify_release(row["properties"] for row in rows))
    source = tmp_path / "source.geojson"
    source.write_bytes(quality.encode({"type": "FeatureCollection", "features": rows}))
    day = dt.date(2026, 5, 2)
    built = output(source=sample_source_state(feature_count=3), extract=job.SourceExtract(source, 3, 0),
        workdir=tmp_path, release_date=day,
        identity_reservation=identity_state.Reservation(ledger, {"path": "fixture"}, 1, None))
    baseline_fixture(job, built, built.sidecar_records, day, Client().bucket(quality.BUCKET))
