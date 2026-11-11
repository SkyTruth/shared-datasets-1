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
    return {"rows": output.row_count, "artifact_sha256": output.sha256}


def hold_fixture(job, output, records, source, work, day):
    """Exercise every held-candidate runtime import inside the installed image."""
    from types import SimpleNamespace
    from unittest import mock
    from google.api_core.exceptions import NotFound
    from ingestion.common import lifecycle
    from scripts import slack_notify

    class Blob:
        generation = 0
        data = None

        def reload(self):
            if self.data is None:
                raise NotFound("fixture object absent")

        def download_as_bytes(self, *, if_generation_match):
            assert if_generation_match == self.generation
            self.reload()
            return self.data

        def upload_from_string(self, data, *, content_type, if_generation_match):
            assert if_generation_match == self.generation
            self.data = data
            self.generation += 1

        def upload_from_filename(self, path, **kwargs):
            self.upload_from_string(Path(path).read_bytes(), **kwargs)

    class Bucket:
        name = job.quality.BUCKET

        def __init__(self):
            self.objects = {}

        def blob(self, name):
            return self.objects.setdefault(name, Blob())

    bucket = Bucket()
    flow = job.quality
    flow.put(bucket, flow.INDEX, flow.encode({"latest_release": {"date": "2026-04-02", "rows": 40}}), generation=0)
    refresh = job.Refresh(SimpleNamespace(bucket=bucket), work)
    refresh.source, refresh.outputs = source, output
    content = lifecycle.Content(job.source_fingerprint(records).record_fields(), len(records), lost=3)
    tools = {"fixture": {"path": "/app/fixture", "version": "installed"}}
    built = lifecycle.Built(output.row_count, tools, f"gs://{bucket.name}/{job.ASSET.release_prefix(day)}/")
    with flow.Guard(bucket) as refresh.gate, mock.patch.object(slack_notify, "notify") as send:
        result = refresh.hold(lifecycle.OBJECTS_LOST,
            lifecycle.Source(day, job.SOURCE_URL, job.sha256_file(source)), content, built)
        assert result["status"] == "held" and result["hold"]["candidate"]
        refresh.gate.notify()
        send.assert_called_once()
    state, _ = flow.Guard(bucket).load()
    assert state["owner"] is None and state["hold"]["notification"] == "sent"

if __name__ == "__main__":
    work = Path("/tmp/shared-datasets-1/_scratch/installed-fixture")
    work.mkdir(parents=True)
    print(json.dumps(vector_build(sys.argv[1], json.loads(sys.argv[2]), work)))
