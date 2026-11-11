"""Synthetic vector builds executed inside their immutable production images."""
import datetime as dt
import json
from pathlib import Path
import sys

from scripts import release_feature_model as model


def vector_build(target, fixture, work):
    day = dt.date(2026, 5, 2)
    source = work / "source.geojson"
    if target == "mpatlas-monthly":
        from ingestion.mpatlas_monthly import run as job
        job.Refresh(None, work)  # the image must carry the loss-limit policy its lifecycle reads
        source.write_text(json.dumps(fixture))
        enriched, records, _ = job.prepare_metadata(source, work, day)
        output = job.build_outputs(enriched, records, work, day)
    else:
        from ingestion.eamlis_monthly import run as job, identity, identity_state
        source.write_text(json.dumps(fixture["geojson"]))
        state = fixture["source"]
        state["stats"] = job.SourceStats(**state["stats"])
        state["fields"] = tuple(state["fields"])
        ledger = identity.Ledger.genesis().reserve(identity.classify_release(
            row["properties"] for row in fixture["geojson"]["features"]))
        output = job.build_asset_output(source=job.SourceState(**state),
            extract=job.SourceExtract(source, 3, 0), workdir=work, release_date=day,
            identity_reservation=identity_state.Reservation(ledger, {"path": "fixture"}, 1, None))
    validation = model.validate_sidecar_records(model.read_metadata_sidecar(output.metadata),
        expected_asset_slug=job.ASSET.slug, expected_release=day.isoformat())
    assert output.row_count == validation.feature_count == 3 and validation.valid
    assert output.pmtiles.read_bytes()[:7] == b"PMTiles"
    if target == "mpatlas-monthly":
        hold_fixture(job, output, records, source, work, day)
    else:
        hold_fixture(job, output, output.sidecar_records, source, work, day, state=state)
    return {"rows": output.row_count, "artifact_sha256": output.sha256}


def hold_fixture(job, output, records, source, work, day, *, state=None):
    """Exercise every held-candidate runtime import inside the installed image."""
    from types import SimpleNamespace
    from unittest import mock
    from google.api_core.exceptions import NotFound
    from ingestion.common import lifecycle
    from scripts import slack_notify

    class Blob:
        def __init__(self, name):
            self.name, self.generation, self.data = name, 0, None

        @property
        def size(self):
            return len(self.data or b"")

        def reload(self):
            if self.data is None:
                raise NotFound("fixture object absent")

        def download_as_bytes(self, *, if_generation_match):
            assert if_generation_match == self.generation
            self.reload()
            return self.data

        def download_as_text(self):
            self.reload()
            return self.data.decode()

        def upload_from_string(self, data, *, content_type, if_generation_match):
            assert if_generation_match == self.generation
            self.data = data.encode() if isinstance(data, str) else data
            self.generation += 1

        def upload_from_filename(self, path, **kwargs):
            self.upload_from_string(Path(path).read_bytes(), **kwargs)

    flow = job.quality if state is None else job.source_loss

    class Bucket:
        name = flow.BUCKET

        def __init__(self):
            self.objects = {}

        def blob(self, name, *, generation=None):
            blob = self.objects.setdefault(name, Blob(name))
            assert generation is None or generation == blob.generation
            return blob

    bucket = Bucket()
    flow.put(bucket, flow.INDEX, flow.encode({"latest_release": {"date": "2026-04-02", "rows": 40}}), generation=0)
    if state is None:
        refresh = job.Refresh(SimpleNamespace(bucket=bucket), work)
        refresh.source, refresh.outputs = source, output
        content = lifecycle.Content(job.source_fingerprint(records).record_fields(), len(records), lost=3)
        source_info = lifecycle.Source(day, job.SOURCE_URL, job.sha256_file(source))
        tools = {"fixture": {"path": "/app/fixture", "version": "installed"}}
    else:
        refresh = job.Refresh(SimpleNamespace(bucket=bucket), work,
            layer_url=state["layer_url"], where=state["where"], page_size=2000)
        refresh.output = output
        refresh.extract = job.SourceExtract(source, len(records), 0)
        refresh.translation_source = work / "fixture.metadata-translations.csv"
        refresh.translation_source.write_text("feature_id,field,locale,source_value_hash,value,review_state,notes\n")
        job.reconcile_translations(output.metadata, refresh.translation_source, day)
        refresh.source = job.fingerprint_source(job.SourceState(**state), refresh.extract,
            refresh.translation_source, identity_contract=job.keyed_identity.CONTRACT)
        refresh.reports = job.prepare_changed_asset(output, refresh.translation_source, day)
        refresh.source_inputs = [{"uri": state["layer_url"], "source_fingerprint_hash": refresh.source.fingerprint_hash}]
        content = lifecycle.Content({"source_fingerprint": refresh.source.fingerprint,
            "source_fingerprint_hash": refresh.source.fingerprint_hash}, len(records), lost=3)
        source_info = lifecycle.Source(day, state["layer_url"], job.sha256_file(source))
        tools = job.toolchain()
    built = lifecycle.Built(output.row_count, tools, f"gs://{bucket.name}/{job.ASSET.release_prefix(day)}/")
    with flow.Guard(bucket) as refresh.gate, mock.patch.object(slack_notify, "notify") as send:
        result = refresh.hold(lifecycle.OBJECTS_LOST, source_info, content, built)
        assert result["status"] == "held" and result["hold"]["candidate"]
        refresh.gate.notify()
        send.assert_called_once()
    state, _ = flow.Guard(bucket).load()
    assert state["owner"] is None and state["hold"]["notification"] == "sent"
    baseline_fixture(job, output, records, day, Bucket())


def baseline_fixture(job, output, records, day, bucket):
    """Read the current publisher's serialized baseline inside each job image."""
    from types import SimpleNamespace
    from ingestion.common import published_baseline, source_quality, vector_pipeline
    from ingestion.common.gcs import GcsPublisher

    publisher = GcsPublisher(SimpleNamespace(bucket=lambda name: bucket), bucket.name)
    if job.ASSET.slug == "mpatlas-zone-assessments":
        identity = model.build_identity_metadata(strategy="source_field", source_fields=["zone_id"])
        algorithm = model.GEOMETRY_HASH_ALGORITHM
    else:
        identity, algorithm = output.identity_metadata, output.geometry_hash_algorithm
    bundle = vector_pipeline.publish_vector_bundle(publisher=publisher, asset=job.ASSET, run_date=day,
        outputs=output, object_metadata={}, source_inputs=[], identity=identity,
        geometry_hash_algorithm=algorithm)
    source_quality.put(bucket, job.ASSET.run_record_object(day), source_quality.encode({
        "status": "success", "asset_slug": job.ASSET.slug, "run_date": day.isoformat(),
        "release_date": day.isoformat(), "sha256": bundle.sha256}), generation=0)
    source_quality.put(bucket, f"_catalog/releases/{job.ASSET.slug}.json", source_quality.encode({
        "latest_release": {"date": day.isoformat(), "rows": len(records),
            "run_record_path": f"gs://{bucket.name}/{job.ASSET.run_record_object(day)}"}}), generation=0)
    verified = published_baseline.read(publisher, job.ASSET)
    assert verified.records == tuple(records)
    if job.ASSET.slug == "mpatlas-zone-assessments":
        assert job.load_baseline(publisher).fingerprint_hash == job.source_fingerprint(records).sha256

if __name__ == "__main__":
    work = Path("/tmp/shared-datasets-1/_scratch/installed-fixture")
    work.mkdir(parents=True)
    print(json.dumps(vector_build(sys.argv[1], json.loads(sys.argv[2]), work)))
