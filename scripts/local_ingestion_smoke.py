#!/usr/bin/env python3
"""Exercise the real sea-ice geometry/identity toolchain with a tiny local raster."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingestion.sea_ice_daily import run as sea_ice
from ingestion.wdpa_monthly import run as wdpa
from ingestion.wdpa_monthly.resources import PhaseProfiler, prepare_scratch
from scripts import release_feature_model as model
from scripts.local_wdpa_sample import cgroup_limit
from scripts.wdpa_processing_gate import source_digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, required=True)
    args = parser.parse_args()
    wdpa.configure_logging()
    prepare_scratch()
    args.workdir.mkdir(parents=True, exist_ok=False)
    profiler = PhaseProfiler(args.workdir, versions=wdpa.native_versions())
    started = time.monotonic()
    report = {
        "schema_version": 1,
        "scope": "small-sea-ice-fixture",
        "source_tree_sha256": source_digest(),
        "state": "failed",
        "source": "synthetic IMS classes, 4 by 4 cells",
        "cpu_limit": cgroup_limit("cpu.max"),
        "memory_limit_bytes": cgroup_limit("memory.max"),
        "native_versions": profiler.versions,
    }
    try:
        with profiler.phase("sea-ice:synthetic-source"):
            source = args.workdir / "source.tif"
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
                    str(source),
                ]
            )
        with profiler.phase("sea-ice:production-build"):
            outputs = sea_ice.build_outputs(
                baseline=model.GeneratedIdentityBaseline.genesis(contract_id="test-v1"),
                source_tif=source,
                source_date=dt.date(2026, 4, 28),
                workdir=args.workdir,
            )
            validation = model.validate_sidecar_records(
                model.read_metadata_sidecar(outputs.metadata),
                expected_asset_slug=sea_ice.ASSET.slug,
                expected_release="2026-04-29",
            )
            if not validation.valid or validation.feature_count != outputs.row_count:
                raise RuntimeError("Small fixture metadata/count validation failed")
            if sea_ice.feature_count(outputs.fgb) != outputs.row_count:
                raise RuntimeError("Small fixture FlatGeobuf count differs")
            report.update(
                rows=outputs.row_count,
                artifact_sha256=outputs.sha256,
                contracts_verified=True,
                state="succeeded",
            )
        if source_digest() != report["source_tree_sha256"]:
            raise RuntimeError("Processing source changed during the smoke test")
    except BaseException:
        report["state"] = "failed"
        raise
    finally:
        report.update(
            elapsed_seconds=time.monotonic() - started, phases=profiler.records
        )
        (args.workdir / "benchmark.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
