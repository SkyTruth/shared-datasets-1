#!/usr/bin/env python3
"""Require one retained October build before publishing its exact artifacts."""

from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ingestion.wdpa_monthly.resources import validation_limits

EVIDENCE = ROOT / "catalog/wdpa-processing-acceptance.json"
PREFERRED_MEMORY_PEAK_BYTES = 6.4 * 1024**3
MEMORY_LIMIT_BYTES = 8 * 1024**3


def source_paths(inventory):
    """The producer contract's paths, including an original revision's globs."""
    paths = {
        "ingestion/wdpa_monthly/Dockerfile", "pyproject.toml", "uv.lock",
        "scripts/release_feature_model.py", "scripts/feature_metadata_translation_reuse.py",
        "scripts/feature_metadata_localization.py", "scripts/local_wdpa_sample.py",
        "scripts/local_ingestion_smoke.py", "scripts/cloud_wdpa_validation.py",
        "scripts/download_public_wdpa_benchmark.py", "docs/wdpa-processing-public-inputs.json",
        "ingestion/sea_ice_daily/run.py", "scripts/translation_local_io.py",
        "scripts/pmtiles_zoom.py", "scripts/vector_asset.py", "scripts/slack_notify.py",
        "scripts/wdpa_processing_gate.py",
    }
    for relative in inventory:
        path = Path(relative)
        if (path.parent.as_posix() in {"ingestion/common", "ingestion/wdpa_monthly"} and path.suffix == ".py") or (
            path.parent.as_posix() == "catalog/feature-identity-resolutions"
            and path.name.startswith("wdpa-") and path.suffix == ".json"
        ):
            paths.add(path.as_posix())
    return sorted(paths)


def source_digest():
    inventory = (str(path.relative_to(ROOT)) for directory in (
        "ingestion/common", "ingestion/wdpa_monthly", "catalog/feature-identity-resolutions"
    ) for path in (ROOT / directory).glob("*"))
    digest = hashlib.sha256()
    for relative in source_paths(inventory):
        path = ROOT / relative
        digest.update(
            relative.encode() + b"\0" + path.read_bytes() + b"\0"
        )
    return digest.hexdigest()


INPUT_KEYS = (
    "source_sha256",
    "baseline_snapshot_sha256",
    "translation_inputs_snapshot_sha256",
    "image_digest",
)


def check_compatibility(evidence, builds):
    """A sampled old/new comparison is separate from complete resource runs."""
    sample = evidence.get("compatibility_sample") or {}
    errors = []
    if (
        sample.get("state") != "succeeded"
        or sample.get("sample_fraction") != 0.001
        or sample.get("sample_seed") != 7919
        or sample.get("genesis") is not False
        or sample.get("run_date") != "2026-10-01"
        or sample.get("translation_index_built") is not True
        or sample.get("compatibility_verified") is not True
        or sample.get("contracts_verified") is not True
        or sample.get("source_counts_verified") is not True
        or sample.get("source_tree_sha256") != evidence.get("source_tree_sha256")
        or not validation_limits(
            sample.get("cpu_limit"), sample.get("memory_limit_bytes")
        )
    ):
        errors.append(
            "a deterministic October old/new compatibility sample is required"
        )
    assets = sample.get("assets", {})
    if set(assets) != {"wdpa-marine", "wdpa-terrestrial"} or any(
        asset.get("compatibility_verified") is not True
        or not isinstance(asset.get("rows"), int)
        or asset["rows"] <= 0
        for asset in assets.values()
    ):
        errors.append(
            "compatibility must compare both realms, including their IDs, geometry, field types and translations"
        )
    for build in builds:
        for key in (*INPUT_KEYS, "native_versions"):
            if not sample.get(key) or sample.get(key) != build.get(key):
                errors.append(
                    f"compatibility sample and complete build disagree on {key}"
                )
    return errors


def memory_warnings(run):
    """Report preferred headroom independently of artifact acceptance."""
    peak = run.get("memory_peak_bytes")
    if type(peak) is int and PREFERRED_MEMORY_PEAK_BYTES < peak <= MEMORY_LIMIT_BYTES:
        return [
            f"kernel lifetime peak {peak} bytes ({peak / 1024**3:.2f} GiB) exceeds "
            "the preferred 6.4 GiB headroom target; the enforced limit remains "
            "8 GiB and this warning does not reject validated artifacts"
        ]
    return []


def check_build(run, *, require_bundle=True):
    """A single measured complete build, with retained promotable bytes."""
    errors = []
    if require_bundle:
        from ingestion.wdpa_monthly.artifact_bundle import check_reference

        try:
            check_reference(
                run.get("artifact_bundle"), execution=run.get("cloud_execution")
            )
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"retained build bundle is required: {exc}")
    if not re.fullmatch(
        r"wdpa-processing-validation-[a-z0-9]+", str(run.get("cloud_execution", ""))
    ):
        errors.append("complete builds require their Cloud Run execution IDs")
    if not re.fullmatch(
        r"us-central1-docker\.pkg\.dev/shared-datasets-1/shared-datasets-jobs/wdpa-validation@sha256:[0-9a-f]{64}",
        str(run.get("cloud_image", "")),
    ):
        errors.append(
            "complete builds require the immutable Cloud Run deployment image"
        )
    if (
        run.get("state") != "succeeded"
        or run.get("sample_fraction") != 1
        or run.get("genesis") is not False
        or run.get("run_date") != "2026-10-01"
        or run.get("translation_index_built") is not True
    ):
        errors.append("a benchmark is incomplete, sampled, or uses a genesis baseline")
    if not validation_limits(run.get("cpu_limit"), run.get("memory_limit_bytes")):
        errors.append("a benchmark did not use 4 CPU / 8 GiB limits")
    peak, scratch, elapsed = (
        run.get(key)
        for key in ("memory_peak_bytes", "scratch_peak_bytes", "elapsed_seconds")
    )
    if type(peak) is not int or not 0 < peak <= MEMORY_LIMIT_BYTES:
        errors.append(
            "the kernel lifetime peak is missing, invalid, or exceeds the enforced 8 GiB limit"
        )
    if type(scratch) is not int or not 0 < scratch < 80 * 1024**3:
        errors.append("a benchmark missed the scratch target")
    if type(elapsed) not in (float, int) or not 0 < elapsed <= 86400:
        errors.append("a benchmark missed the timeout target")
    if (
        run.get("source_counts_verified") is not True
        or run.get("contracts_verified") is not True
    ):
        errors.append("benchmark counts and artifact contracts must be verified")
    if set(run.get("assets", {})) != {"wdpa-marine", "wdpa-terrestrial"}:
        errors.append("both realms must be built")
    for slug, (rows, india) in {
        "wdpa-marine": (17938, 304),
        "wdpa-terrestrial": (497914, 193174),
    }.items():
        asset = run.get("assets", {}).get(slug, {})
        if (
            asset.get("rows") != rows
            or asset.get("india_rows") != india
            or asset.get("india_sites") != india
        ):
            errors.append(
                f"complete October {slug} counts differ from the frozen source"
            )
        if (
            type(asset.get("next_generated_feature_id")) is not int
            or asset["next_generated_feature_id"] <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", str(asset.get("semantic_sha256", "")))
        ):
            errors.append(f"{slug} identity/semantic evidence is incomplete")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(run.get("image_digest", ""))):
        errors.append("benchmark image digest is missing")
    return errors


def check_promotion_plan(evidence):
    """Require an immutable reviewed document for the owned build promotion."""
    build = evidence.get("build") or {}
    reference = build.get("artifact_bundle") or {}
    expected = f".github/dataset-plans/wdpa-build-{reference.get('sha256', '')}.json"
    if evidence.get("promotion_plan") != expected or not re.fullmatch(
        r"\.github/dataset-plans/wdpa-build-[0-9a-f]{64}\.json", expected
    ):
        return ["a checked-in immutable WDPA build promotion plan is required"]
    path = ROOT / expected
    if not path.is_file():
        return ["reviewed WDPA build promotion plan is missing"]
    plan = json.loads(path.read_text())
    if plan != {
        "schema_version": 1,
        "kind": "wdpa_owned_build_promotion",
        "artifact_bundle": reference,
        "cloud_execution": build.get("cloud_execution"),
        "cloud_image": build.get("cloud_image"),
        "image_digest": build.get("image_digest"),
        "source_tree_sha256": build.get("source_tree_sha256"),
        "run_date": build.get("run_date"),
        "assets": build.get("assets"),
    }:
        return ["WDPA promotion plan differs from accepted build evidence"]
    return []


def check(evidence):
    errors = []
    if (
        evidence.get("schema_version") != 3
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(evidence.get("source_tree_sha256", ""))
        )
    ):
        errors.append("benchmark evidence is missing its producer source fingerprint")
    if evidence.get("disk_quota_approved") is not True:
        errors.append("100 GiB ephemeral disk per-instance quota has not been approved")
    build = evidence.get("build") or {}
    if "runs" in evidence:
        errors.append("acceptance must identify one build, not a replay list")
    if type(evidence.get("promotion_pr")) is not int or evidence["promotion_pr"] <= 0:
        errors.append("the merged PR approving this build promotion must be identified")
    if build.get("source_tree_sha256") != evidence.get("source_tree_sha256"):
        errors.append("a benchmark does not match the processing source tree")
    return (
        errors
        + check_build(build)
        + check_compatibility(evidence, [build])
        + check_promotion_plan(evidence)
    )


def check_precloud(evidence, *, producer_source_sha256=None):
    """Small/sampled checks permit one artifact build, never publication."""
    errors = []
    if (
        evidence.get("schema_version") != 3
        or evidence.get("source_tree_sha256") != (
            source_digest() if producer_source_sha256 is None else producer_source_sha256
        )
    ):
        errors.append("staged validation does not match the processing source tree")
    if evidence.get("disk_quota_approved") is not True:
        errors.append("100 GiB ephemeral disk quota has not been approved")
    small = evidence.get("small_fixture") or {}
    sample = evidence.get("compatibility_sample") or {}
    if (
        small.get("scope") != "small-sea-ice-fixture"
        or small.get("state") != "succeeded"
        or small.get("contracts_verified") is not True
    ):
        errors.append("the small sea-ice production-path smoke test must pass first")
    for run in (small, sample):
        if run.get("source_tree_sha256") != evidence.get("source_tree_sha256"):
            errors.append("a staged test uses different processing code")
        if not validation_limits(run.get("cpu_limit"), run.get("memory_limit_bytes")):
            errors.append("a staged test did not use 4 CPU / 8 GiB")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(run.get("image_digest", ""))):
            errors.append("a staged test is missing its immutable image digest")
    if not small.get("image_digest") or small.get("image_digest") != sample.get(
        "image_digest"
    ):
        errors.append("small and sampled checks must use the same build image")
    return errors + check_compatibility(evidence, [sample])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print-source-digest", action="store_true")
    parser.add_argument(
        "--pre-cloud",
        action="store_true",
        help="Gate isolated cloud validation, without permitting publication",
    )
    args = parser.parse_args()
    if args.print_source_digest:
        print(source_digest())
        return
    evidence = (
        ROOT / "catalog/wdpa-staged-validation.json" if args.pre_cloud else EVIDENCE
    )
    payload = json.loads(evidence.read_text())
    errors = (check_precloud if args.pre_cloud else check)(payload)
    if not args.pre_cloud:
        for warning in memory_warnings(payload.get("build") or {}):
            print("WARNING: " + warning, file=sys.stderr)
    if errors:
        raise SystemExit("WDPA rollout blocked:\n" + "\n".join(errors))


if __name__ == "__main__":
    main()
