"""Build, validate and publish the current upstream month without a data PR."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
import tempfile

from google.cloud import storage

from ingestion.common import publication as p
from ingestion.common.runtime import run_job_main
from ingestion.wdpa_monthly import artifact_bundle, build, run as wdpa
from scripts import wdpa_processing_gate as gate


def check_in(publisher, month, attempt, reason):
    records = []
    for asset in wdpa.ASSETS:
        record = {
            "schema_version": 1, "asset_slug": asset.slug, "status": "skipped",
            "run_date": attempt.isoformat(), "target_release_date": month.isoformat(),
            "reason": reason, "source": wdpa.build_source_url(os.environ.get("WDPA_SOURCE_URL_TEMPLATE", wdpa.DEFAULT_SOURCE_URL_TEMPLATE), month),
            "source_version": wdpa.source_version_for(month),
        }
        record["release_index"] = publisher.update_latest_run_index(asset=asset, payload=record)
        records.append(record)
    return records


def run():
    wdpa.prepare_scratch()
    p.require("WDPA_PROMOTION_BUNDLE" not in os.environ,
              "automatic ingestion cannot select a separately approved bundle")
    p.require(not wdpa.parse_sample_spec(), "scheduled publication requires the complete source")
    attempt = dt.date.fromisoformat(os.environ["RUN_DATE"]) if os.environ.get("RUN_DATE") else dt.datetime.now(dt.UTC).date()
    month = attempt.replace(day=1)
    client = storage.Client(project=wdpa.DEFAULT_PROJECT_ID)
    publisher = wdpa.GcsPublisher.from_runtime(client, wdpa.DEFAULT_BUCKET, logger=wdpa.LOGGER)
    # Resume the captured transaction before any source download or ID allocation.
    resumed = []
    for asset in wdpa.ASSETS:
        resumed.append(publisher.resume(asset))
        state = publisher.state(asset).value
        p.require(state["active"] is None, "publication is incomplete; resume its original execution")
        p.require(state["current"]["release"] <= month.isoformat(), "cannot publish an older month")
    if all(record is not None for record in resumed):
        return resumed
    if all(publisher.load_successful_run_record(asset, month) for asset in wdpa.ASSETS):
        return check_in(publisher, month, attempt, "monthly source already published")

    with tempfile.TemporaryDirectory(prefix="wdpa-automatic-", dir=wdpa.work_root()) as directory:
        root = Path(directory)
        try:
            with tempfile.TemporaryDirectory(prefix="build-", dir=root) as build_directory:
                reference = build.build_month(client, month, Path(build_directory) / "outputs")
        except wdpa.SourceNotAvailableError as exc:
            return check_in(publisher, month, attempt, str(exc))
        # Only this execution's complete validated descriptor can reach publication.
        artifact_bundle.check_reference(reference, execution=os.environ["CLOUD_RUN_EXECUTION"])
        return artifact_bundle.promote(client, publisher, reference, root / "publish",
                                       producer_source=gate.source_digest(), requested_date=month)


def main():
    run_job_main(run, logger=wdpa.LOGGER, failure_message="wdpa monthly job failed")


if __name__ == "__main__":
    main()
