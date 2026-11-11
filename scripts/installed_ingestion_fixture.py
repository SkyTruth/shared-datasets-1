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
    return {"rows": output.row_count, "artifact_sha256": output.sha256}

if __name__ == "__main__":
    work = Path("/tmp/shared-datasets-1/_scratch/installed-fixture")
    work.mkdir(parents=True)
    print(json.dumps(vector_build(sys.argv[1], json.loads(sys.argv[2]), work)))
