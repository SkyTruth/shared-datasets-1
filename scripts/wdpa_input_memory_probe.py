#!/usr/bin/env python3
"""Measure the production replay's frozen-input phase; never acceptance evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import resource
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.local_wdpa_sample import load_baseline, source_digest, wdpa
from ingestion.wdpa_monthly.resources import PhaseProfiler, prepare_scratch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--baselines", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    args = parser.parse_args()
    wdpa.configure_logging()
    prepare_scratch()
    args.workdir.mkdir(parents=True, exist_ok=False)
    profiler = PhaseProfiler(
        args.workdir, versions=wdpa.native_versions(), scratch_root=Path("/work"),
        input_cache_roots=(args.source.parent,),
    )
    source_sha256 = wdpa.sha256_file(args.source)
    with profiler.phase("frozen-inputs"):
        source_copy = args.workdir / args.source.name
        shutil.copyfile(args.source, source_copy)
        sources = wdpa.prepare_source_datasets(source_copy, args.workdir)
        wdpa.discover_source_layers(sources)
        for asset in wdpa.ASSETS:
            baseline = load_baseline(args.baselines, asset, args.workdir)
            baseline.records.close()
    report = {
        "schema_version": 1,
        "scope": "input-preparation-only",
        "state": "diagnostic",
        "source_tree_sha256": source_digest(),
        "source_sha256": source_sha256,
        "phases": profiler.records,
        "child_rss_peak_bytes": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024,
        "cgroup_memory_stat": {
            key: int(value) for key, value in
            (line.split() for line in Path("/sys/fs/cgroup/memory.stat").read_text().splitlines())
        },
    }
    (args.workdir / "benchmark.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
