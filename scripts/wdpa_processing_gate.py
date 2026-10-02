#!/usr/bin/env python3
"""Fail closed until reviewed complete October builds meet the resource target."""

from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "catalog/wdpa-processing-acceptance.json"


def source_digest():
    paths = sorted(
        [
            *ROOT.glob("ingestion/common/*.py"),
            *ROOT.glob("ingestion/wdpa_monthly/*.py"),
            *ROOT.glob("catalog/feature-identity-resolutions/wdpa-*.json"),
            ROOT / "ingestion/wdpa_monthly/Dockerfile",
            ROOT / "pyproject.toml",
            ROOT / "uv.lock",
            ROOT / "scripts/release_feature_model.py",
            ROOT / "scripts/feature_metadata_translation_reuse.py",
            ROOT / "scripts/feature_metadata_localization.py",
            ROOT / "scripts/local_wdpa_sample.py",
        ]
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(
            str(path.relative_to(ROOT)).encode() + b"\0" + path.read_bytes() + b"\0"
        )
    return digest.hexdigest()


def check(evidence):
    errors = []
    if (
        evidence.get("schema_version") != 1
        or evidence.get("source_tree_sha256") != source_digest()
    ):
        errors.append("benchmark evidence does not match the processing source tree")
    if evidence.get("disk_quota_approved") is not True:
        errors.append("100 GiB ephemeral disk per-instance quota has not been approved")
    runs = evidence.get("runs", [])
    if len(runs) != 2:
        errors.append("two complete October deployment-image builds are required")
    for run in runs:
        if (
            run.get("state") != "succeeded"
            or run.get("sample_fraction") != 1
            or run.get("genesis") is not False
            or run.get("run_date") != "2026-10-01"
            or run.get("translation_index_built") is not True
        ):
            errors.append(
                "a benchmark is incomplete, sampled, or uses a genesis baseline"
            )
        if run.get("cpu_limit") != 4 or run.get("memory_limit_bytes") != 8 * 1024**3:
            errors.append("a benchmark did not use 4 CPU / 8 GiB limits")
        peak, scratch, elapsed = (
            run.get(key)
            for key in ("memory_peak_bytes", "scratch_peak_bytes", "elapsed_seconds")
        )
        if not isinstance(peak, int) or peak <= 0 or peak > 6.4 * 1024**3:
            errors.append("a benchmark missed the memory/headroom target")
        if not isinstance(scratch, int) or scratch <= 0 or scratch >= 80 * 1024**3:
            errors.append("a benchmark missed the scratch target")
        if not isinstance(elapsed, (float, int)) or elapsed <= 0 or elapsed > 86400:
            errors.append("a benchmark missed the timeout target")
        if (
            run.get("source_counts_verified") is not True
            or run.get("contracts_verified") is not True
            or run.get("compatibility_verified") is not True
        ):
            errors.append(
                "benchmark counts, artifact contracts and compatibility must be verified"
            )
        if set(run.get("assets", {})) != {"wdpa-marine", "wdpa-terrestrial"}:
            errors.append("both realms must be built")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(run.get("image_digest", ""))):
            errors.append("benchmark image digest is missing")
    if len(runs) == 2:
        for key in (
            "source_sha256",
            "baseline_snapshot_sha256",
            "translation_inputs_snapshot_sha256",
            "image_digest",
        ):
            if not runs[0].get(key) or runs[0].get(key) != runs[1].get(key):
                errors.append(f"complete builds disagree on {key}")
        for slug in ("wdpa-marine", "wdpa-terrestrial"):
            for key in (
                "rows",
                "india_rows",
                "india_sites",
                "semantic_sha256",
                "next_generated_feature_id",
            ):
                values = [run.get("assets", {}).get(slug, {}).get(key) for run in runs]
                if values[0] is None or values[0] != values[1]:
                    errors.append(f"complete builds disagree on {slug} {key}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print-source-digest", action="store_true")
    args = parser.parse_args()
    if args.print_source_digest:
        print(source_digest())
        return
    errors = check(json.loads(EVIDENCE.read_text()))
    if errors:
        raise SystemExit("WDPA rollout blocked:\n" + "\n".join(errors))


if __name__ == "__main__":
    main()
