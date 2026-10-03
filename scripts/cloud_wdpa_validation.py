#!/usr/bin/env python3
"""Build and retain frozen WDPA artifacts without canonical dataset writes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingestion.wdpa_monthly import run as wdpa
from ingestion.wdpa_monthly.resources import (
    PhaseProfiler,
    cgroup_limits,
    cgroup_memory,
    prepare_scratch,
    validation_limits,
)
from scripts.wdpa_processing_gate import source_digest


def main():
    wdpa.configure_logging()
    if os.environ.get("WDPA_FAIL_BEFORE_DATASET_WRITES") == "1":
        raise RuntimeError(
            "Controlled WDPA validation failure before any dataset writes"
        )
    prepare_scratch()
    cpu_limit, memory_limit = cgroup_limits()
    if not validation_limits(cpu_limit, memory_limit):
        raise RuntimeError(
            f"Cloud validation requires a kernel CPU quota in (0,4] and 8 GiB; "
            f"observed cpu={cpu_limit!r}, memory_bytes={memory_limit!r}"
        )
    if cgroup_memory()[1] is None:
        raise RuntimeError(
            "Cloud validation requires kernel cgroup peak-memory telemetry"
        )
    root = Path(os.environ["SHARED_DATASETS_WORKDIR"]) / "cloud-validation"
    root.mkdir(parents=True, exist_ok=False)
    inputs, replay = root / "inputs", root / "replay"
    profiler = PhaseProfiler(
        root, versions=wdpa.native_versions(), scratch_root=Path("/work")
    )
    report = {
        "state": "failed",
        "source_tree_sha256": source_digest(),
        "cloud_execution": os.environ.get("CLOUD_RUN_EXECUTION"),
        "cloud_image": os.environ["WDPA_BUILD_IMAGE"],
        "image_digest": os.environ["WDPA_BUILD_IMAGE_CONFIG_DIGEST"],
    }
    cloud_image, config_digest = report["cloud_image"], report["image_digest"]
    try:
        with profiler.phase("cloud-input-download"):
            subprocess.run(
                [
                    sys.executable,
                    "scripts/download_public_wdpa_benchmark.py",
                    "--recipe",
                    "docs/wdpa-processing-public-inputs.json",
                    "--out",
                    str(inputs),
                ],
                check=True,
            )
        subprocess.run(
            [
                sys.executable,
                "scripts/local_wdpa_sample.py",
                "--source",
                str(inputs / "WDPA_WDOECM_Oct2026_Public_all_shp.zip"),
                "--baselines",
                str(inputs / "frozen-inputs"),
                "--translation-sources",
                str(inputs / "frozen-inputs/translation-sources.json"),
                "--workdir",
                str(replay),
                "--run-date",
                "2026-10-01",
                "--stage-build",
            ],
            check=True,
        )
        report = json.loads((replay / "benchmark.json").read_text())
    except BaseException:
        if (replay / "benchmark.json").exists():
            report = json.loads((replay / "benchmark.json").read_text())
        report["state"] = "failed"
        raise
    finally:
        report["cloud_execution"] = os.environ.get("CLOUD_RUN_EXECUTION")
        report["cloud_image"] = cloud_image
        report["image_digest"] = config_digest
        report["phases"] = profiler.records + report.get("phases", [])
        report["memory_peak_bytes"] = cgroup_memory()[1]
        report["scratch_peak_bytes"] = max(
            (p["scratch_peak_bytes"] for p in report["phases"]),
            default=None,
        )
        report["elapsed_seconds"] = sum(
            p["elapsed_seconds"] for p in profiler.records
        ) + report.get("elapsed_seconds", 0)
        try:
            if report["state"] == "succeeded":
                from ingestion.wdpa_monthly.artifact_bundle import BuildStager

                report["artifact_bundle"] = BuildStager.from_runtime().commit(
                    report, root
                )
        except BaseException:
            report["state"] = "failed"
            raise
        finally:
            (root / "benchmark.json").write_text(json.dumps(report, indent=2) + "\n")
            print(
                json.dumps(
                    {"event": "wdpa_cloud_validation_report", "report": report},
                    sort_keys=True,
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
