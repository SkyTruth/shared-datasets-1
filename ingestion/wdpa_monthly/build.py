"""Build one requested month into immutable scratch objects; never publish."""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from ingestion.common import publication as p
from ingestion.common import decision_events, publication_readiness
from ingestion.common.feature_metadata import IdentityDecisionRequired
from ingestion.common.owned_publication import OwnedGeneratedPublisher
from ingestion.wdpa_monthly import run as wdpa, translations, identity_review
from ingestion.wdpa_monthly.artifact_bundle import BuildStager, check_reference
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


def replay(source, inputs, directory, run_date, *, sample, discover_only=False):
    command = [sys.executable, "scripts/local_wdpa_sample.py", "--source", str(source),
        "--baselines", str(inputs), "--translation-sources", str(inputs / "translation-sources.json"),
        "--workdir", str(directory), "--run-date", run_date]
    command += ["--discover-only"] if discover_only else (["--fraction", "0.001", "--seed", "7919", "--compare-legacy"] if sample else ["--stage-build"])
    if discover_only or not sample:
        command += ["--analysis-dir", str(inputs.parent / "analysis")]
    subprocess.run(command, check=True)
    report = json.loads((directory / "benchmark.json").read_text())
    if report["state"] == "blocked":
        blocked = report["blocked_record"]
        p.require(blocked["status"] == "blocked"
            and blocked["reason"] == "identity_decision_required"
            and blocked["asset_slug"] in {asset.slug for asset in wdpa.ASSETS}
            and blocked["release"] == run_date
            and type(blocked["awaiting_decisions"]) is int
            and blocked["awaiting_decisions"] > 0,
            "invalid identity-decision blocked report")
        records = report.get("blocked_records", [blocked])
        p.require(records and all(record["status"] == "blocked"
            and record["reason"] == "identity_decision_required"
            and record["asset_slug"] in {a.slug for a in wdpa.ASSETS}
            and record["release"] == run_date
            and type(record["awaiting_decisions"]) is int and record["awaiting_decisions"] > 0
            for record in records)
            and len({record["asset_slug"] for record in records}) == len(records),
            "invalid identity-decision blocked batch")
        error = IdentityDecisionRequired(blocked["next_step"],
            asset_slug=blocked["asset_slug"], release=blocked["release"],
            ambiguity_count=blocked["awaiting_decisions"])
        error.blocked_assets = [IdentityDecisionRequired(record["next_step"],
            asset_slug=record["asset_slug"], release=record["release"],
            ambiguity_count=record["awaiting_decisions"]) for record in records]
        raise error
    return report


def build_month(client, month, root, *, retained_analysis=None):
    """Produce one complete, measured bundle for this scheduled execution."""
    run_date = gate.monthly_date(month.isoformat()).isoformat()
    p.require(gate.build_job(os.environ.get("CLOUD_RUN_EXECUTION")) == "wdpa-monthly", "wrong scheduled execution")
    prepare_scratch()
    p.require(validation_limits(*cgroup_limits()) and cgroup_memory()[1] is not None,
        "builder requires 4 CPU / 8 GiB and kernel peak-memory telemetry")
    root.mkdir(parents=True, exist_ok=False)
    profiler = PhaseProfiler(root, versions=wdpa.native_versions(), scratch_root=Path("/work"))
    started = time.monotonic()
    inputs, source = root / "inputs", root / "source.zip"
    with profiler.phase("freeze-inputs"):
        freeze_inputs(client, inputs)
        wdpa.download_file(wdpa.build_source_url(os.environ.get("WDPA_SOURCE_URL_TEMPLATE", wdpa.DEFAULT_SOURCE_URL_TEMPLATE),
            gate.monthly_date(run_date)), source)
    identity = {"cloud_execution": os.environ["CLOUD_RUN_EXECUTION"],
        "cloud_image": os.environ["WDPA_BUILD_IMAGE"], "image_digest": os.environ["WDPA_BUILD_IMAGE_CONFIG_DIGEST"]}
    if retained_analysis is not None:
        def download(ref, path):
            name = check_reference(ref)
            client.bucket(wdpa.DEFAULT_BUCKET).blob(name, generation=ref["generation"]).download_to_filename(
                str(path), if_generation_match=ref["generation"], checksum="crc32c")
            p.require(path.stat().st_size == ref["size"] and wdpa.sha256_file(path) == ref["sha256"],
                      "retained analysis object differs from its pinned reference")
        descriptor = root / "restored-analysis-reference.json"
        download(retained_analysis, descriptor)
        reference = json.loads(descriptor.read_text())
        for item in reference["assets"].values():
            binding = item["binding"]
            p.require(binding["release"] == run_date
                and binding["inputs"]["source_sha256"] == wdpa.sha256_file(source)
                and binding["inputs"]["baseline_snapshot_sha256"] == wdpa.sha256_file(inputs / "pins.json")
                and binding["inputs"]["native_versions"] == profiler.versions
                and binding["inputs"]["producer"] == wdpa.sha256_file(Path(wdpa.__file__)),
                "requested retained analysis does not match the current frozen inputs or producer")
        publication_readiness.restore_retained(reference, root / "analysis", download)
    results = []
    for name, sampled in (("discovery", False), ("sample", True), ("complete", False)):
        directory = root / name
        try:
            if name == "discovery":
                discovery = replay(source, inputs, directory, run_date, sample=False, discover_only=True)
            else:
                results.append({**replay(source, inputs, directory, run_date, sample=sampled), **identity})
        except (IdentityDecisionRequired, subprocess.CalledProcessError) as blocked:
            if name == "discovery":
                report_path = root / "analysis/readiness.json"
                if report_path.exists():
                    retained_report = json.loads(report_path.read_text())
                    retained = publication_readiness.stage_retained(retained_report,
                        BuildStager(client, identity["cloud_execution"]).upload)
                    if retained["assets"]:
                        # Retain complete normalized work even if a bounded review
                        # renderer failed; the incomplete batch still fails closed.
                        descriptor = root / "analysis-reference.json"
                        descriptor.write_bytes(p.canonical(retained))
                        reference = BuildStager(client, identity["cloud_execution"]).upload(
                            "wdpa-marine/analysis-reference.json", descriptor)
                        print(json.dumps({"event": "publication_analysis_retained", "reference": reference}), flush=True)
            if not isinstance(blocked, IdentityDecisionRequired):
                raise
            # Discovery collects the complete batch, so finish every realm's
            # evidence before propagating the expected decision pause.
            for pending in getattr(blocked, "blocked_assets", [blocked]):
                with profiler.phase(f"{pending.asset_slug}:identity-review"):
                    target = identity_review.complete(client, inputs=inputs, replay_directory=directory,
                        directory=root / f"identity-review-{pending.asset_slug}", blocked=pending, source=source,
                        source_url=wdpa.build_source_url(os.environ.get("WDPA_SOURCE_URL_TEMPLATE", wdpa.DEFAULT_SOURCE_URL_TEMPLATE), month),
                        producer={"execution": identity["cloud_execution"], "executor_sha": os.environ["SHARED_DATASETS_EXECUTOR_SHA"],
                                  "image": identity["cloud_image"], "image_digest": identity["image_digest"]})
                    expected_sha = wdpa.sha256_file(target)
                    reference = BuildStager(client, identity["cloud_execution"]).upload(f"{pending.asset_slug}/identity-review.json", target)
                    p.require(reference["sha256"] == expected_sha, "review evidence changed before staging")
                    print(json.dumps({"event": "wdpa_identity_review", "asset_slug": pending.asset_slug,
                        "release": pending.release, "awaiting_decisions": pending.ambiguity_count,
                        "review_bundle": reference}), flush=True)
                    bundle = identity_review.read_json(target)
                    decision_events.publish(asset_slug=pending.asset_slug, release=pending.release,
                        ambiguities=[item["ambiguity"] for item in bundle["capture"]["cases"]], review_bundle=reference)
            raise
    sample, report = results
    report["memory_peak_bytes"] = cgroup_memory()[1]
    report["resource_warnings"] = gate.memory_warnings(report)
    report["phases"] = profiler.records + discovery["phases"] + sample["phases"] + report["phases"]
    report["scratch_peak_bytes"] = max(phase["scratch_peak_bytes"] for phase in report["phases"])
    report["elapsed_seconds"] = time.monotonic() - started
    errors = gate.check_compatibility({"compatibility_sample": sample,
        "source_tree_sha256": report["source_tree_sha256"]}, [report])
    p.require(not errors, "; ".join(errors))
    # Bind the sample to the exact full-build descriptor for independent validation
    # at the publication boundary.
    report["compatibility_sample"] = sample
    reference = BuildStager(client, identity["cloud_execution"]).commit(report, root)
    print(json.dumps({"event": "wdpa_monthly_build", "run_date": run_date, "artifact_bundle": reference}), flush=True)
    return reference
