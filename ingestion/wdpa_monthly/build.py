"""Build one requested month into immutable scratch objects; never publish."""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from google.cloud import storage

from ingestion.common import publication as p
from ingestion.common.owned_publication import OwnedGeneratedPublisher
from ingestion.wdpa_monthly import run as wdpa, translations
from ingestion.wdpa_monthly.artifact_bundle import BuildStager
from ingestion.wdpa_monthly.resources import PhaseProfiler, cgroup_limits, cgroup_memory, prepare_scratch, validation_limits
from scripts import wdpa_processing_gate as gate


def freeze_inputs(client, directory):
    """Snapshot verified committed identities and translations with read-only calls."""
    directory.mkdir()
    reader = OwnedGeneratedPublisher(client, wdpa.DEFAULT_BUCKET,
        execution_id="wdpa-build-read-only", executor_sha="0" * 40, configuration={"build": True})
    pins = {}
    with ExitStack() as stack:
        for asset in wdpa.ASSETS:
            baseline = reader.load_generated_identity_baseline(asset, contract_id=wdpa.CONTRACT_ID,
                identity_index_path=directory / f"{asset.slug}.sqlite")
            p.require(baseline.snapshot is not None, "monthly builds require a committed identity baseline")
            stack.callback(baseline.records.close)
            snapshot = baseline.snapshot
            bucket, name = p.split_uri(snapshot.path)
            p.require(bucket == wdpa.DEFAULT_BUCKET, "foreign baseline bucket")
            manifest_path = directory / f"{asset.slug}.manifest.json"
            client.bucket(bucket).blob(name, generation=snapshot.generation).download_to_filename(
                str(manifest_path), if_generation_match=snapshot.generation, checksum="crc32c")
            p.require(wdpa.sha256_file(manifest_path) == snapshot.sha256, "baseline manifest hash changed")
            manifest = json.loads(manifest_path.read_text())
            metadata = wdpa.release_feature_model.validate_release_manifest(manifest,
                expected_asset_slug=asset.slug, expected_release=baseline.release, require_generations=True)["metadata"]
            version = p.ObjectVersion(metadata["path"], metadata["generation"],
                metadata["sha256"].removeprefix("sha256:"), metadata["size"])
            translations.download_source(reader.bucket, version,
                directory / f"{asset.slug}.metadata.ndjson.gz", compress=False)
            pins[asset.slug] = {"manifest_uri": snapshot.path, "manifest_generation": snapshot.generation,
                "manifest_sha256": snapshot.sha256, "metadata": metadata}
        sources, supplement = translations.download_inputs(reader, wdpa.ASSETS, directory)
        p.require(supplement is None, "monthly builds require both completed reset publications")
        config = {"schema_version": 1, "sources": sources, "files": {}, "supplement": None}
        for source in sources:
            for key in ("canonical_sidecar", "translation_source"):
                path = Path(source[key])
                relative = path.relative_to(directory).as_posix()
                config["files"][relative] = wdpa.sha256_file(path)
                source[key] = relative
        (directory / "translation-sources.json").write_bytes(p.canonical(config))
        (directory / "pins.json").write_bytes(p.canonical(pins))
    for asset in wdpa.ASSETS:
        (directory / f"{asset.slug}.sqlite").unlink()


def replay(source, inputs, directory, run_date, *, sample):
    command = [sys.executable, "scripts/local_wdpa_sample.py", "--source", str(source),
        "--baselines", str(inputs), "--translation-sources", str(inputs / "translation-sources.json"),
        "--workdir", str(directory), "--run-date", run_date]
    command += ["--fraction", "0.001", "--seed", "7919", "--compare-legacy"] if sample else ["--stage-build"]
    subprocess.run(command, check=True)
    return json.loads((directory / "benchmark.json").read_text())


def run():
    run_date = gate.monthly_date(os.environ.get("RUN_DATE")).isoformat()
    p.require(gate.build_job(os.environ.get("CLOUD_RUN_EXECUTION")) == "wdpa-monthly-build", "wrong builder execution")
    prepare_scratch()
    p.require(validation_limits(*cgroup_limits()) and cgroup_memory()[1] is not None,
        "builder requires 4 CPU / 8 GiB and kernel peak-memory telemetry")
    root = Path(os.environ["SHARED_DATASETS_WORKDIR"]) / "monthly-build"
    root.mkdir(parents=True, exist_ok=False)
    client = storage.Client(project=wdpa.DEFAULT_PROJECT_ID)
    profiler = PhaseProfiler(root, versions=wdpa.native_versions(), scratch_root=Path("/work"))
    started = time.monotonic()
    inputs, source = root / "inputs", root / "source.zip"
    with profiler.phase("freeze-inputs"):
        freeze_inputs(client, inputs)
        wdpa.download_file(wdpa.build_source_url(wdpa.DEFAULT_SOURCE_URL_TEMPLATE,
            gate.monthly_date(run_date)), source)
    identity = {"cloud_execution": os.environ["CLOUD_RUN_EXECUTION"],
        "cloud_image": os.environ["WDPA_BUILD_IMAGE"], "image_digest": os.environ["WDPA_BUILD_IMAGE_CONFIG_DIGEST"]}
    sample = {**replay(source, inputs, root / "sample", run_date, sample=True), **identity}
    report = {**replay(source, inputs, root / "complete", run_date, sample=False), **identity}
    report["memory_peak_bytes"] = cgroup_memory()[1]
    report["phases"] = profiler.records + sample["phases"] + report["phases"]
    report["scratch_peak_bytes"] = max(phase["scratch_peak_bytes"] for phase in report["phases"])
    report["elapsed_seconds"] = time.monotonic() - started
    errors = gate.check_compatibility({"compatibility_sample": sample,
        "source_tree_sha256": report["source_tree_sha256"]}, [report])
    p.require(not errors, "; ".join(errors))
    # Retain the sample in the hash-bound descriptor so the acceptance PR needs
    # no mutable log scraping. Only a complete validated build commits this file.
    report["compatibility_sample"] = sample
    reference = BuildStager(client, identity["cloud_execution"]).commit(report, root)
    print(json.dumps({"event": "wdpa_monthly_build", "run_date": run_date, "artifact_bundle": reference}), flush=True)


if __name__ == "__main__":
    wdpa.configure_logging()
    run()
