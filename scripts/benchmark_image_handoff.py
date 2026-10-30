#!/usr/bin/env python3
"""Compare read-only PR artifact transport and local Docker loading on one runner.

This measures selected_image plus the current load_image, not production
authorization. It cannot publish, deploy, grant authority, or resolve incidents.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import sdk_release_authorization as proof
from scripts import tested_image_authorization as current
from scripts import tested_image_bundle as bundle
from scripts.deployment_revision import DeploymentError, require

BASELINE_SHA = "7bcded0ac55cebc67889321f11782895ffd729f0"
TARGET = "mpatlas-monthly"
VARIANTS = ("baseline", "level0", "level1")
ORDER = (*VARIANTS, *reversed(VARIANTS))
LIMITATIONS = (
    "Warm-image same-runner repeated handoff after one separately recorded level1 warmup; no cold-VM claim.",
    "Unchanged production main-only authorization is excluded; PR identity is checked for measurement only.",
    "Two trials per variant are descriptive samples, not a stable population benchmark.",
    "No images or tags are deleted; any preexisting source tag aborts the benchmark without changing it.",
)


class MeasuredAPI:
    """Record actual read identities; alias one comparison name only in memory."""

    def __init__(self, api, large_api, alias=None):
        self.api, self.large_api, self.alias = api, large_api, alias
        self.reads = []
        self.downloads = []

    def get(self, path):
        started = perf_counter()
        value = self.api.get(path)
        self.reads.append({"operation": "get", "path": path, "seconds": perf_counter() - started})
        return value

    def pages(self, path, *, field=None):
        started = perf_counter()
        value = self.api.pages(path, field=field)
        self.reads.append({"operation": "pages", "path": path, "seconds": perf_counter() - started})
        return value

    def archive(self, repository, artifact_id):
        started = perf_counter()
        raw = self.api.archive(repository, artifact_id)
        self.downloads.append({"artifact_id": artifact_id, "zip_bytes": len(raw), "seconds": perf_counter() - started})
        return raw

    def archive_to(self, repository, artifact_id, path):
        started = perf_counter()
        self.large_api.archive_to(repository, artifact_id, path)
        self.downloads.append({"artifact_id": artifact_id, "zip_bytes": path.stat().st_size,
                               "seconds": perf_counter() - started})

    def artifacts(self, artifacts):
        if self.alias is None:
            return artifacts
        actual, canonical = self.alias
        require(sum(item.get("name") == actual for item in artifacts) == 1, "benchmark comparison artifact missing or ambiguous")
        # The normal retained artifact also exists. Hide it from this isolated
        # inventory so only the explicitly chosen comparison can satisfy lookup.
        return [{**item, "name": canonical} if item.get("name") == actual else item
                for item in artifacts if item.get("name") != canonical]


def baseline_loader(ref, directory):
    resolved = subprocess.check_output(["git", "rev-parse", "--verify", "--end-of-options", ref + "^{commit}"], cwd=ROOT, text=True).strip()
    require(resolved == BASELINE_SHA, "benchmark baseline must be the pinned main revision")
    source = subprocess.check_output(["git", "show", resolved + ":scripts/tested_image_authorization.py"], cwd=ROOT)
    path = directory / "baseline_tested_image_authorization.py"
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location("benchmark_baseline_tested_image_authorization", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, {"commit": resolved, "source_sha256": hashlib.sha256(source).hexdigest()}


def successful_job(jobs, name):
    found = [job for job in jobs if job.get("name") == name]
    require(len(found) == 1 and found[0].get("status") == "completed" and found[0].get("conclusion") == "success",
            "benchmark producer job did not complete successfully: " + name)
    return {key: found[0].get(key) for key in ("id", "name", "status", "conclusion", "started_at", "completed_at")}


def source(api, repository, run_id, attempt, sha):
    repo = api.get(f"repos/{repository}")
    workflow = api.get(f"repos/{repository}/actions/workflows/ci.yml")
    latest = api.get(f"repos/{repository}/actions/runs/{run_id}")
    run = api.get(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}")
    require(type(repo.get("id")) is int and repo["id"] > 0 and repo.get("full_name") == repository,
            "benchmark repository identity mismatch")
    require(type(workflow.get("id")) is int and workflow["id"] > 0 and workflow.get("path") == ".github/workflows/ci.yml",
            "benchmark workflow identity mismatch")
    for item in (latest, run):
        require(item.get("id") == run_id and item.get("run_attempt") == attempt
                and item.get("workflow_id") == workflow["id"] and item.get("path") == workflow["path"]
                and item.get("event") == "pull_request"
                and all(item.get(key, {}).get("id") == repo["id"] and item.get(key, {}).get("full_name") == repository
                        for key in ("repository", "head_repository")), "benchmark is not the exact current same-repository PR CI attempt")
    require(run.get("head_sha") == latest.get("head_sha"), "benchmark current PR head changed")
    jobs = api.pages(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100", field="jobs")
    producers = [successful_job(jobs, name) for name in ("geospatial-changes", "production-images")]
    artifacts = api.pages(f"repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100", field="artifacts")
    def execution_artifact(prefix, producer):
        window = proof.gate_execution(producer)
        found = [artifact for artifact in artifacts if not artifact.get("expired")
                 and re.fullmatch(re.escape(prefix) + r"-attempt[1-9][0-9]*", str(artifact.get("name")))
                 and window[0] <= proof.execution_time(artifact.get("created_at")) <= window[1]]
        require(len(found) == 1, "benchmark execution artifact missing or ambiguous: " + prefix)
        reference = found[0]["name"]
        producing_attempt = proof.reference_attempt(reference, prefix, attempt)
        original = run if producing_attempt == attempt else api.get(f"repos/{repository}/actions/runs/{run_id}/attempts/{producing_attempt}")
        require(original.get("id") == run_id and original.get("run_attempt") == producing_attempt
                and original.get("workflow_id") == workflow["id"] and original.get("path") == workflow["path"]
                and original.get("event") == "pull_request" and original.get("head_sha") == latest.get("head_sha")
                and all(original.get(key, {}).get("id") == repo["id"] and original.get(key, {}).get("full_name") == repository
                        for key in ("repository", "head_repository")), "benchmark original producer identity mismatch")
        original_jobs = jobs if producing_attempt == attempt else api.pages(
            f"repos/{repository}/actions/runs/{run_id}/attempts/{producing_attempt}/jobs?per_page=100", field="jobs")
        require(proof.gate_execution(successful_job(original_jobs, producer["name"])) == window,
                "benchmark artifact belongs to another producer execution")
        current.retained_artifact(artifacts, reference, run_id, proof.MAX_ARCHIVE)
        producer["run_attempt"] = producing_attempt
        return reference, producing_attempt

    reference, plan_attempt = execution_artifact("ci-validation-plan", producers[0])
    _, production_attempt = execution_artifact("ci-result-production-images", producers[1])
    plan = bundle.read_json(proof.artifact_files(api, repository, run_id, artifacts, reference)["plan.json"])
    require(plan.get("schema_version") == 1 and plan.get("tested_sha") == sha
            and plan.get("source") == {"run_id": str(run_id), "run_attempt": plan_attempt}
            and "production-images" in plan.get("suites", []), "benchmark plan source or tested revision mismatch")
    require(run.get("head_sha") == latest.get("head_sha") == plan.get("head"), "benchmark PR source head mismatch")
    return plan, artifacts, producers, production_attempt


def result_image(api, repository, run_id, attempt, sha, plan, artifacts):
    reference = f"ci-result-production-images-attempt{attempt}"
    current.retained_artifact(artifacts, reference, run_id, proof.MAX_ARCHIVE)
    files = proof.artifact_files(api, repository, run_id, artifacts, reference)
    result = bundle.read_json(files["result.json"])
    manifest = bundle.read_json(files["images/manifest.json"])
    require(result.get("source") == {"run_id": str(run_id), "run_attempt": attempt}
            and result.get("suite") == "production-images" and result.get("status") == "success"
            and all(result.get(key) == plan.get(key) for key in proof.IDENTITY)
            and result.get("production_images") == manifest, "benchmark result or manifest differs from source plan")
    require(result.get("tools") == proof.expected_tools("production-images") and result.get("commands")
            and all(command.get("exit_code") == 0 for command in result["commands"]), "benchmark production validation was skipped or failed")
    images = bundle.validate_manifest(manifest, sha)
    require(set(images) == bundle.TARGETS, "benchmark requires the complete tested image manifest")
    return images[TARGET]


def tag_state(tag):
    completed = subprocess.run(["docker", "image", "inspect", tag], capture_output=True, text=True)
    if completed.returncode:
        require("No such image" in completed.stderr or "No such object" in completed.stderr,
                "cannot establish benchmark Docker tag state")
        return {"present": False}
    inspected = json.loads(completed.stdout)
    require(isinstance(inspected, list) and len(inspected) == 1, "ambiguous benchmark Docker tag")
    return {"present": True, "daemon_id": inspected[0]["Id"], "descriptor": inspected[0].get("Descriptor")}


def machine():
    docker = json.loads(subprocess.check_output(["docker", "version", "--format", "{{json .}}"], text=True))
    info = json.loads(subprocess.check_output(["docker", "info", "--format", "{{json .}}"], text=True))
    return {"docker_version": docker, "docker_platform": {key: info.get(key) for key in ("OSType", "Architecture", "Driver")},
            "python": sys.version, "platform": platform.platform(), "machine": platform.machine()}


def artifact_record(artifact):
    return {key: artifact.get(key) for key in ("id", "name", "digest", "size_in_bytes", "expired", "workflow_run")}


def trial(args, variant, index, baseline, expected, output, warm_daemon_id=None):
    api = MeasuredAPI(current.GitHub(), baseline.GitHub() if variant == "baseline" else current.GitHub())
    item = {"variant": variant, "sequence": index, "round": 1 if index <= 3 else 2, "status": "running"}
    directory = output / f"trial-{index}-{variant}"
    directory.mkdir()
    log = directory / "phases.log"
    started = perf_counter()
    try:
        with log.open("w") as stream, redirect_stdout(stream):
            item["tag_before"] = tag_state(expected["source_tag"])
            if warm_daemon_id is not None:
                require(item["tag_before"].get("daemon_id") == warm_daemon_id and item["tag_before"]["present"],
                        "timed benchmark must start with the exact warmed image present")
            plan, artifacts, producers, production_attempt = source(api, args.repository, args.run_id, args.attempt, args.sha)
            canonical = f"ci-image-{TARGET}-attempt{production_attempt}"
            actual = (f"benchmark-full-images-attempt{production_attempt}" if variant == "baseline"
                      else f"benchmark-image-mpatlas-level0-attempt{production_attempt}" if variant == "level0" else canonical)
            api.alias = (actual, canonical) if variant == "level0" else None
            chosen = current.retained_artifact(artifacts, actual, args.run_id,
                                               baseline.MAX_ARTIFACT if variant == "baseline" else current.MAX_ARTIFACT)
            window = proof.gate_execution(producers[1])
            require(window[0] <= proof.execution_time(chosen.get("created_at")) <= window[1],
                    "benchmark image artifact belongs to another producer execution")
            item["actual_artifact"] = artifact_record(chosen)
            item["producer_jobs"] = producers
            path = directory / expected["archive"]
            select_started = perf_counter()
            loader = baseline.selected_image if variant == "baseline" else current.selected_image
            image = loader(api, args.repository, args.run_id, api.artifacts(artifacts),
                           actual if variant == "baseline" else f"ci-result-production-images-attempt{production_attempt}",
                           path, TARGET, args.sha, plan, production_attempt)
            item["selection_seconds"] = perf_counter() - select_started
            require(image == expected, "benchmark variants do not identify exactly the same tested TAR and config")
            load_started = perf_counter()
            item["loaded_daemon_id"] = bundle.load_image(path, image)
            item["load_and_proof_seconds"] = perf_counter() - load_started
            require(image == expected, "benchmark image identity changed during loading")
        item.update(seconds=perf_counter() - started, status="success", verified_identity={
            key: image[key] for key in ("config_digest", "archive_sha256", "archive_size", "platform")})
        # load_image verifies the actual TAR hash, config and layers before Docker,
        # then verifies the loaded config. No additional warm-up or hash-only pass.
        path.unlink()
    except Exception as error:
        item.update(seconds=perf_counter() - started, status="failure", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        item["metadata_reads"] = api.reads
        item["downloads"] = api.downloads
        item["zip_bytes"] = sum(download["zip_bytes"] for download in api.downloads)
        item["phases"] = [json.loads(line.removeprefix("TESTED_IMAGE_HANDOFF "))
                          for line in log.read_text().splitlines() if line.startswith("TESTED_IMAGE_HANDOFF ")]
        (directory / "result.json").write_text(json.dumps(item, indent=2, sort_keys=True) + "\n")
    return item


def write_report(output, report):
    (output / "results.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    lines = ["# Hosted image handoff comparison", "", f"Status: {report['status']}", "",
             "| Variant | Trials | Median total seconds | Download ZIP bytes per trial |", "|---|---:|---:|---|" ]
    for variant in VARIANTS:
        trials = [item for item in report["trials"] if item["variant"] == variant and item["status"] == "success"]
        if trials:
            lines.append(f"| {variant} | {len(trials)} | {statistics.median(item['seconds'] for item in trials):.3f} | "
                         + ", ".join(str(item["zip_bytes"]) for item in trials) + " |")
    warmup = report.get("warmup", {})
    if warmup.get("status") == "success":
        lines += ["", f"Level1 warmup excluded from medians: {warmup['seconds']:.3f}s, {warmup['zip_bytes']} downloaded ZIP bytes."]
    lines += ["", *["- " + limitation for limitation in LIMITATIONS], "",
              "Totals include fresh GitHub metadata/plan reads, ZIP transport/checks, TAR and rootfs verification, Docker load and loaded-config proof.", ""]
    (output / "results.md").write_text("\n".join(lines))


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--repository", required=True)
    value.add_argument("--run-id", type=int, required=True)
    value.add_argument("--attempt", type=int, required=True)
    value.add_argument("--sha", required=True, help="actual tested PR merge SHA")
    value.add_argument("--baseline-ref", required=True, choices=(BASELINE_SHA, BASELINE_SHA[:7]))
    value.add_argument("--output", type=Path, required=True)
    return value


def main():
    arguments = parser()
    args = arguments.parse_args()
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository)
            and args.run_id > 0 and args.attempt > 0 and re.fullmatch(r"[0-9a-f]{40}", args.sha), "invalid benchmark source arguments")
    require(subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() == args.sha,
            "benchmark checkout differs from tested SHA")
    args.output.mkdir(parents=True, exist_ok=True)
    require(not (args.output / "results.json").exists(), "benchmark output already contains a result")
    report = {"schema_version": 1, "status": "running", "repository": args.repository, "run_id": args.run_id,
              "attempt": args.attempt, "tested_sha": args.sha, "order": list(ORDER), "limitations": list(LIMITATIONS), "trials": []}
    try:
        with tempfile.TemporaryDirectory(prefix="baseline-source-", dir=args.output) as work:
            baseline, report["baseline"] = baseline_loader(args.baseline_ref, Path(work))
            report["machine"] = machine()
            api = MeasuredAPI(current.GitHub(), current.GitHub())
            plan, artifacts, _, production_attempt = source(api, args.repository, args.run_id, args.attempt, args.sha)
            expected = result_image(api, args.repository, args.run_id, production_attempt, args.sha, plan, artifacts)
            report["image"] = expected
            report["setup_reads"] = api.reads
            report["setup_downloads"] = api.downloads
            initial = tag_state(expected["source_tag"])
            report["initial_tag"] = initial
            require(not initial["present"], "benchmark source tag already exists; it is left untouched")
            try:
                report["warmup"] = trial(args, "level1", 0, baseline, expected, args.output)
            except Exception as error:
                failed = args.output / "trial-0-level1" / "result.json"
                report["warmup"] = json.loads(failed.read_text()) if failed.exists() else {
                    "variant": "level1", "status": "failure", "error_type": type(error).__name__, "error": str(error)}
                raise
            for index, variant in enumerate(ORDER, 1):
                try:
                    item = trial(args, variant, index, baseline, expected, args.output,
                                 report["warmup"]["loaded_daemon_id"])
                except Exception as error:
                    failed = args.output / f"trial-{index}-{variant}" / "result.json"
                    report["trials"].append(json.loads(failed.read_text()) if failed.exists() else {
                        "variant": variant, "status": "failure", "error_type": type(error).__name__, "error": str(error)})
                    raise
                report["trials"].append(item)
                write_report(args.output, report)
        report["status"] = "success"
    except Exception as error:
        report.update(status="failure", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        write_report(args.output, report)


if __name__ == "__main__":
    try:
        main()
    except (DeploymentError, bundle.ImageError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(f"IMAGE_HANDOFF_BENCHMARK_FAILED: {type(error).__name__}: {error}")
