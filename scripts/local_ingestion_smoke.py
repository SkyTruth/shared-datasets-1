#!/usr/bin/env python3
"""Exercise the real sea-ice geometry/identity toolchain with a tiny local raster."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys
import subprocess
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingestion.sea_ice_daily import run as sea_ice
from scripts import release_feature_model as model


def sea_ice_build(workdir):
    """The installed-image and local smoke use the same complete synthetic build."""
    source = workdir / "source.tif"
    sea_ice.run_command([
        "gdal_create", "-of", "GTiff", "-outsize", "4", "4", "-bands", "1",
        "-burn", "3", "-ot", "Byte", "-a_srs", "EPSG:3857",
        "-a_ullr", "-100000", "100000", "100000", "-100000", str(source),
    ])
    outputs = sea_ice.build_outputs(
        baseline=model.GeneratedIdentityBaseline.genesis(contract_id="test-v1"),
        source_tif=source, source_date=dt.date(2026, 4, 28), workdir=workdir,
    )
    if outputs.row_count <= 0 or not all(path.is_file() for path in (outputs.fgb, outputs.pmtiles, outputs.metadata, outputs.schema)):
        raise RuntimeError("Small fixture did not produce its nonempty release bundle")
    validation = model.validate_sidecar_records(
        model.read_metadata_sidecar(outputs.metadata),
        expected_asset_slug=sea_ice.ASSET.slug, expected_release="2026-04-29",
    )
    if not validation.valid or validation.feature_count != outputs.row_count:
        raise RuntimeError("Small fixture metadata/count validation failed")
    if sea_ice.feature_count(outputs.fgb) != outputs.row_count:
        raise RuntimeError("Small fixture FlatGeobuf count differs")
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--sea-ice-only", action="store_true")
    args = parser.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=False)
    if args.sea_ice_only:
        outputs = sea_ice_build(args.workdir)
        print(json.dumps({"rows": outputs.row_count, "artifact_sha256": outputs.sha256}))
        return
    from ingestion.wdpa_monthly import run as wdpa
    from ingestion.wdpa_monthly.resources import PhaseProfiler, cgroup_limits, prepare_scratch
    from scripts.wdpa_processing_gate import source_digest

    wdpa.configure_logging()
    prepare_scratch()
    profiler = PhaseProfiler(args.workdir, versions=wdpa.native_versions())
    started = time.monotonic()
    cpu_limit, memory_limit = cgroup_limits()
    report = {
        "schema_version": 1,
        "scope": "small-sea-ice-fixture",
        "source_tree_sha256": source_digest(),
        "state": "failed",
        "source": "synthetic IMS classes, 4 by 4 cells",
        "cpu_limit": cpu_limit,
        "memory_limit_bytes": memory_limit,
        "native_versions": profiler.versions,
    }
    try:
        with profiler.phase("publication:complete-discovery"):
            source = args.workdir / "decision-source.geojson"
            source.write_text(json.dumps({"type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [x, 1]},
                 "properties": {"SITE_PID": str(x), "SITE_ID": x, "MARINE": realm, "ISO3": "IND"}}
                for x, realm in ((1, "1"), (2, "0"))
            ]}))
            memory = args.workdir / "unused-memory.sqlite"
            memory.write_bytes(b"Discovery must not open translation memory")
            subprocess.run([sys.executable, "scripts/local_wdpa_sample.py", "--source", str(source),
                "--genesis", "--translation-memory", str(memory), "--run-date", "2026-04-01",
                "--discover-only", "--workdir", str(args.workdir / "discovery")], check=True, timeout=60)
            discovery = json.loads((args.workdir / "discovery/benchmark.json").read_text())
            if discovery["state"] != "discovered" or discovery["readiness"]["state"] != "READY":
                raise RuntimeError("Complete native publication discovery failed")
        with profiler.phase("sea-ice:production-build"):
            outputs = sea_ice_build(args.workdir)
            report.update(rows=outputs.row_count, artifact_sha256=outputs.sha256,
                          contracts_verified=True, state="succeeded")
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
