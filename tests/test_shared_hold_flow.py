"""Real hold serialization and protected promotion for registered assets."""
import copy
import datetime as dt
import json
from unittest import mock

import pytest

from ingestion.common import lifecycle, source_quality
from ingestion.common.gcs import GcsPublisher
from ingestion.mpatlas_monthly import run as job
from scripts import eamlis_source_loss_review as review, slack_notify, publish_workflow, gcs_asset
from scripts import finalize_promoted_release_metadata as finalizer, reviewed_dataset_plan
from tests.test_source_loss import Client, copy_transport
from tests.test_mpatlas_monthly import feature, sidecar, fixture_outputs, TOOLS, DAY


@pytest.mark.parametrize("flow", list(source_quality.FLOWS.values()), ids=lambda f: f.SLUG)
def test_shared_claim_and_single_notification_with_asset_scoped_paths(flow):
    client = Client()
    bucket = client.bucket(flow.BUCKET)
    with mock.patch.object(slack_notify, "notify") as send:
        with flow.Guard(bucket) as guard:
            guard.block(lifecycle.EMPTY_SOURCE, {"geometry_count": 0})
            guard.notify()
            first = guard.record()
        with flow.Guard(bucket) as guard:
            guard.block(lifecycle.EMPTY_SOURCE, {"geometry_count": 0, "checked_at": "later"})
            guard.notify()
            assert guard.state["hold"]["id"] == first["hold"]["id"]
        assert send.call_count == 1
        assert flow.label in send.call_args.kwargs["body"]
        assert lifecycle.EMPTY_SOURCE in send.call_args.kwargs["body"]
    assert first["control_path"].endswith(flow.CONTROL)
    other = next(f for f in source_quality.FLOWS.values() if f is not flow)
    assert other.Guard(bucket).load()[0]["hold"] is None


@pytest.mark.parametrize("flow", list(source_quality.FLOWS.values()), ids=lambda f: f.SLUG)
def test_notification_format_failure_does_not_claim_delivery(flow):
    bucket = Client().bucket(flow.BUCKET)
    with mock.patch.object(slack_notify, "notify") as send:
        with flow.Guard(bucket) as guard:
            guard.block(lifecycle.OBJECTS_LOST, {"geometry_count": 1})
            with pytest.raises(KeyError, match="lost_objects"):
                guard.notify()
            assert guard.load()[0]["hold"]["notification"] == "pending"
            send.assert_not_called()
        with flow.Guard(bucket) as guard:
            guard.block(lifecycle.OBJECTS_LOST,
                {"geometry_count": 1, "lost_objects": 2, "maximum_lost_object_percent": 5})
            guard.notify()
            guard.notify()
            assert guard.load()[0]["hold"]["notification"] == "sent"
        send.assert_called_once()


def seed(client, directory, *, records=None, source_inputs=None):
    directory.mkdir()
    publisher = GcsPublisher(client, job.quality.BUCKET)
    prior = dt.date(2026, 10, 16)
    records = sidecar([feature(z) for z in range(1, 41)]) if records is None else records
    output = fixture_outputs(directory, records)
    raw = json.dumps({"type": "FeatureCollection", "features": [feature(z) for z in range(1, 41)]}).encode()
    fingerprint = job.source_fingerprint(records).record_fields()
    bundle = job.vector_pipeline.publish_vector_bundle(publisher=publisher, asset=job.ASSET, run_date=prior,
        outputs=output, object_metadata={}, source_inputs=source_inputs if source_inputs is not None else [{"uri": job.SOURCE_URL,
            "sha256": source_quality.digest(raw), "source_fingerprint_hash": fingerprint["source_fingerprint_hash"]}],
        identity=job.model.build_identity_metadata(strategy="source_field", source_fields=["zone_id"]))
    record = {"schema_version": 1, "asset_slug": job.ASSET.slug, "status": "success", "source": job.SOURCE_URL,
        "source_version": "prior", "run_date": prior.isoformat(), "release_date": prior.isoformat(),
        "row_count": len(records), "release_path": f"gs://{publisher.bucket.name}/{job.ASSET.release_prefix(prior)}/",
        "release_paths": bundle.release_paths, "latest_paths": bundle.latest_paths, "sha256": bundle.sha256,
        "tools": TOOLS, **fingerprint}
    publisher.write_run_record(asset=job.ASSET, run_date=prior, payload=record)
    return publisher, raw


def execute(publisher, directory, zones, *, raw=None):
    directory.mkdir(exist_ok=True)
    features = [feature(z) for z in zones]
    records = sidecar(features, DAY.isoformat()) if features else []
    def prepare(source, work, day):
        path = work / "enriched.geojson"
        path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
        return path, records, {"published_rows": len(records)}
    with mock.patch.object(job, "download_file", side_effect=lambda url, path, **kw: path.write_bytes(
            raw if raw is not None else json.dumps(features).encode())), \
         mock.patch.object(job, "prepare_metadata", side_effect=prepare) as prepared, \
         mock.patch.object(job, "build_outputs", side_effect=lambda *args: fixture_outputs(directory, records)) as build, \
         mock.patch.object(job, "toolchain", return_value=TOOLS):
        result = job.ingest(publisher, DAY, directory)
    return result, prepared, build


def test_mpatlas_hold_reruns_and_exact_reviewed_promotion(tmp_path):
    client = Client()
    publisher, original_source = seed(client, tmp_path / "baseline")
    flow = job.quality
    latest = publisher.bucket.blob(job.ASSET.latest_object(".fgb")).data
    with mock.patch.object(slack_notify, "notify") as send:
        held, _, _ = execute(publisher, tmp_path / "attempt", [*range(1, 38), 41, 42, 43])
        assert held["status"] == "held" and held["reason"] == lifecycle.OBJECTS_LOST
        assert held["hold"]["notification"] == "sent"
        assert held["hold"]["candidate"]["path"].startswith(
            f"gs://{flow.BUCKET}/_scratch/pending-publishes/{flow.SLUG}/source-loss-")
        assert held["hold"]["observation"]["lost_objects"] == 3
        text = send.call_args.kwargs["body"]
        assert "MPAtlas" in text and "3 of 40 published" in text and "limit: 5%" in text
        assert "--asset mpatlas-zone-assessments status" in text
        # Even bytes/fingerprint equal to the published baseline must rebuild while held.
        healthy, prepared, build = execute(publisher, tmp_path / "attempt", range(1, 41), raw=original_source)
        assert healthy["status"] == "held" and healthy["hold"]["id"] == held["hold"]["id"]
        assert prepared.call_count == build.call_count == 1
        assert healthy["hold"]["observation"]["lost_objects"] == 0
        # Restore the lost-zone candidate and prove repeated restaging is immutable.
        held, _, _ = execute(publisher, tmp_path / "attempt", [*range(1, 38), 41, 42, 43])
        again, _, _ = execute(publisher, tmp_path / "attempt", [*range(1, 38), 41, 42, 43])
        assert held["hold"]["candidate"] == again["hold"]["candidate"]
        assert send.call_count == 1
    assert publisher.load_json(job.ASSET.run_record_object(DAY)) is None
    assert publisher.bucket.blob(job.ASSET.latest_object(".fgb")).data == latest
    assert not list(publisher.bucket.list_blobs(prefix=job.ASSET.release_prefix(DAY)))
    with mock.patch.object(job, "run", return_value=held):
        job.main()  # run_job_main accepts a held result and exits normally.

    plan = review.prepare_plan(client, asset=flow.SLUG)
    document = {"plan_version": 1, "finalization_version": reviewed_dataset_plan.FINALIZATION_VERSION, "publish": plan}
    path = reviewed_dataset_plan.write_document(document, repo_root=tmp_path)
    plan = reviewed_dataset_plan.read_document(path.read_bytes(), path=path.relative_to(tmp_path).as_posix())["publish"]
    candidate = flow.validate_candidate_plan(client, plan)
    approved = {p["destination_uri"]: source_quality.pinned(client, f)
                for f in candidate["files"] for p in plan["promotions"] if p["source_uri"] == f["path"]}
    # The shared preview loader accepts both assets without rebuilding native bytes.
    preview = tmp_path / "preview"
    review.download_preview(client, preview, candidate["candidate_id"], asset=flow.SLUG)
    result = review.upload_preview(client, preview, asset=flow.SLUG)
    assert result[-1]["rows"] == 40
    plan_path = tmp_path / "plan.json"
    plan_path.write_bytes(source_quality.encode(plan))
    with mock.patch.object(flow, "protected_authority", return_value="reviewed-pr-key"), \
         mock.patch.object(gcs_asset, "get_client", return_value=client), \
         mock.patch.dict("os.environ", {"SHARED_DATASETS_ALLOW_CANONICAL_MUTATION": "1", "SHARED_DATASETS_BUCKET": flow.BUCKET}):
        invalid = copy.deepcopy(plan)
        invalid["promotions"][0]["source_generation"] = "999"
        with pytest.raises(source_quality.SourceLossError, match="differs from held candidate"):
            source_quality.begin_promotion(client, invalid)
        transport, copies = copy_transport(client)
        with mock.patch.object(publish_workflow.subprocess, "run", side_effect=transport):
            assert publish_workflow.main(["promote", "--plan-json", str(plan_path)]) == 0
        assert set(copies) == set(approved)
        for uri, raw in approved.items():
            assert publisher.bucket.blob(uri.split("/", 3)[3]).data == raw
        finalizer.finalize_promoted_release_metadata(plan, client=client)
        publisher.record_existing_successful_release(job.ASSET, DAY)
        source_quality.finish_promotion(client, plan)
    state, _ = flow.Guard(publisher.bucket).load()
    assert state["hold"] is None and state["owner"] is None
    assert flow.baseline(publisher.bucket)["release"]["date"] == DAY.isoformat()
    assert job.load_baseline(publisher, job.quality.baseline(publisher.bucket)["release"]).row_count == 40


def test_empty_mpatlas_source_is_held_without_a_build(tmp_path):
    client = Client()
    publisher, _ = seed(client, tmp_path / "baseline")
    with mock.patch.object(slack_notify, "notify") as send:
        result, _, build = execute(publisher, tmp_path / "empty", [])
    assert result["status"] == "held" and result["reason"] == lifecycle.EMPTY_SOURCE
    assert result["hold"]["candidate"] is None
    build.assert_not_called()
    send.assert_called_once()
    assert publisher.load_json(job.ASSET.run_record_object(DAY)) is None


def test_empty_source_normalization_reaches_lifecycle_gate_without_native_tools(tmp_path):
    source = tmp_path / "source.geojson"
    source.write_text('{"type":"FeatureCollection","features":[]}')
    with mock.patch.object(job, "run_command") as native:
        enriched, records, counts = job._prepare_metadata(source, tmp_path, DAY)
    assert records == [] and counts["published_rows"] == 0
    assert json.loads(enriched.read_text())["features"] == []
    native.assert_not_called()


def test_installed_image_hold_probe_exercises_runtime_imports(tmp_path):
    from scripts.installed_ingestion_fixture import hold_fixture
    records = sidecar([feature(z) for z in (1, 2, 3)], DAY.isoformat())
    output = fixture_outputs(tmp_path, records)
    source = tmp_path / "source.geojson"
    source.write_text(json.dumps({"type": "FeatureCollection", "features": [feature(z) for z in (1, 2, 3)]}))
    hold_fixture(job, output, records, source, tmp_path, DAY)


def test_review_cli_selects_mpatlas_and_serializes_immutable_plan(tmp_path, capsys):
    client = Client()
    publisher, _ = seed(client, tmp_path / "baseline")
    with mock.patch.object(slack_notify, "notify"):
        execute(publisher, tmp_path / "attempt", range(1, 38))
    with mock.patch("google.cloud.storage.Client", return_value=client):
        assert review.main(["--asset", job.ASSET.slug, "status"]) == 0
        assert json.loads(capsys.readouterr().out)["asset_slug"] == job.ASSET.slug
        assert review.main(["--asset", job.ASSET.slug, "prepare-plan", "--repo-root", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "shared-datasets-publish-plan" in output and '"source_loss_review"' in output
    documents = list((tmp_path / ".github/dataset-plans").rglob("*.json"))
    assert len(documents) == 1
    document = reviewed_dataset_plan.read_document(documents[0].read_bytes(), path=documents[0].relative_to(tmp_path).as_posix())
    assert document["publish"]["asset_slug"] == job.ASSET.slug
    # A registered review cannot borrow another asset's immutable staging path.
    wrong = copy.deepcopy(document["publish"])
    wrong["asset_slug"] = source_quality.EAMLIS.SLUG
    with pytest.raises(reviewed_dataset_plan.PlanValidationError, match="source_uri must start"):
        reviewed_dataset_plan.normalize_publish_plan(wrong)
    with pytest.raises(source_quality.SourceLossError, match="outside EAMLIS staging"):
        source_quality.EAMLIS.normalize_review(document["publish"]["source_loss_review"])


@pytest.mark.parametrize("flow", list(source_quality.FLOWS.values()), ids=lambda f: f.SLUG)
def test_interrupted_execution_lookup_is_bound_to_its_registered_job(flow):
    job_name = f"projects/shared-datasets-1/locations/us-central1/jobs/{flow.job}"
    execution = flow.job + "-fixture"
    response = mock.Mock()
    response.json.return_value = {"name": job_name + "/executions/" + execution, "job": job_name,
                                  "completionTime": "2026-11-11T00:00:00Z", "runningCount": 0}
    with mock.patch("google.auth.default", return_value=(object(), "shared-datasets-1")), \
         mock.patch("google.auth.transport.requests.AuthorizedSession") as session:
        session.return_value.__enter__.return_value.get.return_value = response
        assert flow.execution_completed(execution)
        session.return_value.__enter__.return_value.get.assert_called_once_with(
            "https://run.googleapis.com/v2/" + job_name + "/executions/" + execution, timeout=30)
        with pytest.raises(source_quality.SourceLossError, match="unrecognized interrupted claim"):
            flow.execution_completed("unregistered-job-fixture")


def test_held_retry_with_reformatted_source_stages_another_candidate(tmp_path):
    client = Client()
    publisher, _ = seed(client, tmp_path / "baseline")
    zones = [*range(1, 38), 41, 42, 43]
    with mock.patch.object(slack_notify, "notify") as send:
        first, _, _ = execute(publisher, tmp_path / "attempt", zones, raw=b'{"type":"FeatureCollection"}')
        second, _, _ = execute(publisher, tmp_path / "attempt", zones, raw=b'{ "type": "FeatureCollection" }')
    # Same zones and fingerprint, other raw bytes: the retained source, manifest and record differ.
    assert second["status"] == "held" and second["hold"]["id"] == first["hold"]["id"]
    assert second["hold"]["candidate"]["path"] != first["hold"]["candidate"]["path"]
    assert send.call_count == 1


def test_hold_raised_after_a_recorded_run_still_asks_once(tmp_path):
    client = Client()
    publisher, original_source = seed(client, tmp_path / "baseline")
    flow = job.quality
    with mock.patch.object(slack_notify, "notify") as send:
        checked, _, _ = execute(publisher, tmp_path / "attempt", range(1, 41), raw=original_source)
        assert checked["status"] == "skipped"
        # An execution that recorded its run and died before releasing its claim.
        flow.Guard(publisher.bucket, owner="mpatlas-monthly-dead1").__enter__()
        with mock.patch.object(flow, "execution_completed", return_value=True):
            first, prepared, _ = execute(publisher, tmp_path / "attempt", range(1, 41), raw=original_source)
            second, _, _ = execute(publisher, tmp_path / "attempt", range(1, 41), raw=original_source)
    assert first["status"] == "held" and first["reason"] == "interrupted_execution"
    assert first["hold"]["candidate"] is None and first["hold"]["notification"] == "sent"
    assert second["hold"]["id"] == first["hold"]["id"]
    prepared.assert_not_called()
    send.assert_called_once()
    state, _ = flow.Guard(publisher.bucket).load()
    assert state["owner"] is None


@pytest.mark.parametrize("fingerprinted", [False, True], ids=["legacy", "fingerprinted"])
def test_counted_generation_pinned_mpatlas_baseline_needs_no_run_record(tmp_path, fingerprinted):
    client = Client()
    publisher, original_source = seed(client, tmp_path / "counted",
        source_inputs=None if fingerprinted else [{"uri": job.SOURCE_URL, "sha256": "a" * 64}])
    bucket, flow = publisher.bucket, job.quality
    prior = dt.date(2026, 10, 16)
    # The real scheduled producer supplies counted, generation-pinned state.
    # Authentication needs no auxiliary run record; an uncounted manual entry
    # would instead fail at the Guard, before the reader is reached.
    bucket.blob(job.ASSET.run_record_object(prior)).exists = False
    indexed = flow.baseline(bucket)["release"]
    assert indexed["rows"] == 40 and all("generation" in ref for ref in indexed["files"])
    assert publisher.load_json(job.ASSET.run_record_object(prior)) is None
    result, prepared, built = execute(publisher, tmp_path / "next", range(1, 41), raw=original_source)
    assert (result["status"], result["release_date"]) == ("skipped", prior.isoformat())
    assert prepared.call_count == (0 if fingerprinted else 1)
    built.assert_not_called()


def test_mpatlas_resume_preserves_reviewed_manifest_index(tmp_path):
    from ingestion.common import release_index

    client = Client()
    publisher, raw = seed(client, tmp_path / "baseline")
    bucket, flow = publisher.bucket, job.quality
    prior = dt.date(2026, 10, 16)
    manifest = publisher.load_json(job.ASSET.release_object(prior, ".manifest.json"))
    manifest["source_inputs"].append({"uri": "https://example.org/reviewed-metadata"})
    local = tmp_path / "reviewed.manifest.json"
    local.write_bytes(source_quality.encode(manifest))
    source = source_quality.immutable_file(bucket, f"_scratch/pending-publishes/{flow.SLUG}/reviewed/manifest.json", local)
    promotions = []
    for name in (job.ASSET.release_object(prior, ".manifest.json"), job.ASSET.latest_object(".manifest.json")):
        destination = bucket.blob(name)
        promotions.append({"source_uri": source["path"], "source_generation": str(source["generation"]),
            "destination_uri": f"gs://{bucket.name}/{name}", "destination_generation": str(destination.generation),
            "content_type": "application/json", "cache_control": flow.NO_CACHE})
    plan = {"asset_slug": flow.SLUG, "promotions": promotions, "release_index_asset_slugs": []}
    path = tmp_path / "metadata-plan.json"
    path.write_bytes(source_quality.encode(plan))
    with mock.patch.object(flow, "protected_authority", return_value="reviewed-metadata"), \
         mock.patch.object(gcs_asset, "get_client", return_value=client), \
         mock.patch.dict("os.environ", {"SHARED_DATASETS_ALLOW_CANONICAL_MUTATION": "1", "SHARED_DATASETS_BUCKET": flow.BUCKET}):
        transport, copied = copy_transport(client)
        with mock.patch.object(publish_workflow.subprocess, "run", side_effect=transport):
            assert publish_workflow.main(["promote", "--plan-json", str(path)]) == 0
        assert len(copied) == 2
        finalized = finalizer.finalize_promoted_release_metadata(plan, client=client)
        assert len(finalized["finalized_manifests"]) == 2 and not finalized["finalized_run_records"]
        rebuilt = release_index.rebuild_index_from_bucket(bucket, {
            "asset_slug": flow.SLUG, "canonical_format": "fgb",
            "canonical_path": f"gs://{bucket.name}/{job.ASSET.latest_object('.fgb')}"})
        current = release_index.load_release_index(bucket, flow.SLUG)
        release_index.write_release_index(bucket, flow.SLUG, rebuilt, generation=current.generation)
        flow.finish_promotion(client, plan)
    index = bucket.blob(flow.INDEX)
    before = (index.generation, index.data)
    with mock.patch.object(publisher, "record_existing_successful_release",
                           wraps=publisher.record_existing_successful_release) as replay:
        result = job.ingest(publisher, prior, tmp_path)
    assert result["status"] == "success"
    assert (index.generation, index.data) == before
    replay.assert_not_called()
    result, _, built = execute(publisher, tmp_path / "next", range(1, 41), raw=raw)
    assert result["status"] == "skipped"
    built.assert_not_called()
