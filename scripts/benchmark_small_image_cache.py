#!/usr/bin/env python3
"""Compare two small-image cache policies on fresh builders in PR diagnostics.

Only job-owned builders, local images and run-scoped GHA caches are changed.
This does not publish images, authenticate to GCP or send Slack messages.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager, redirect_stdout
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
from time import perf_counter
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import production_image_contracts as production
from scripts.deployment_revision import require

TARGETS = ("catalog-viewer", "monitoring-slack")
ORDER = ("current", "proposed", "proposed", "current")
BUILDX = "0.38.1"
BUILDKIT = "v0.33.1"
PACKAGES = "import importlib.metadata as m,json; print(json.dumps(sorted((d.metadata['Name'],d.version) for d in m.distributions())))"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def baseline_policy(base, candidate):
    """Execute only the old cache-policy function after proving all else equal."""
    require(re.fullmatch(r"[0-9a-f]{40}", base), "baseline must be an immutable full commit SHA")
    original = git("show", base + ":scripts/production_image_contracts.py")
    parsed = [ast.parse(source) for source in (original, candidate)]
    functions = [[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "cached_build"] for tree in parsed]
    require(all(len(found) == 1 for found in functions), "cache policy function missing or ambiguous")
    for tree, found in zip(parsed, functions):
        tree.body.remove(found[0])
    require(ast.dump(parsed[0]) == ast.dump(parsed[1]), "production module changed outside cached_build; baseline is not comparable")
    namespace = {"os": os}
    exec(compile(ast.Module(body=functions[0], type_ignores=[]), base + ":cached_build", "exec"), namespace)
    return namespace["cached_build"], hashlib.sha256(original.encode()).hexdigest()


def policy_command(policy, original, target, scope, builder, tag, *, seed=False):
    command = policy(original, target)
    require(command[:4] == ["docker", "buildx", "build", "--load"], "benchmark requires the same Buildx --load path")
    command = list(command)
    if seed:
        index = command.index("--cache-from")
        del command[index:index + 2]
        index = command.index("--cache-to") + 1
        require("ignore-error=true" in command[index], "unexpected baseline cache exporter")
        command[index] = command[index].replace("ignore-error=true", "ignore-error=false")
    for flag in ("--cache-from", "--cache-to"):
        if flag in command:
            index = command.index(flag) + 1
            require(f"scope=production-{target}" in command[index], "unexpected production cache scope")
            command[index] = command[index].replace(f"scope=production-{target}", f"scope={scope}")
    command[command.index("-t") + 1] = tag
    return [*command[:3], "--builder", builder, "--progress", "plain", *command[3:]]


def require_cached_dependency(log, target):
    dependency = "python -m pip install" if target == "catalog-viewer" else "uv sync --locked"
    vertices = set(re.findall(r"^#(\d+) \[[^\]]+\] RUN [^\n]*" + re.escape(dependency) + r"[^\n]*$", log, re.MULTILINE))
    cached = set(re.findall(r"^#(\d+) CACHED$", log, re.MULTILINE))
    require(bool(vertices) and vertices <= cached, "current policy did not prove a cached dependency RUN: " + target)
    return sorted(vertices)


@contextmanager
def fresh_builder(directory):
    name = "small-cache-" + uuid.uuid4().hex
    created = False
    started = perf_counter()
    try:
        subprocess.run(["docker", "buildx", "create", "--name", name, "--driver", "docker-container",
                        "--driver-opt", "image=moby/buildkit:" + BUILDKIT], check=True, stdout=subprocess.DEVNULL)
        created = True
        inspected = subprocess.check_output(["docker", "buildx", "inspect", "--builder", name, "--bootstrap"], text=True)
        (directory / "builder.txt").write_text(inspected)
        require(re.search(r"BuildKit(?: version)?:\s+" + re.escape(BUILDKIT) + r"\b", inspected), "benchmark builder version mismatch")
        image_id = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", "moby/buildkit:" + BUILDKIT], text=True).strip()
        yield {"name": name, "buildkit": BUILDKIT, "image_id": image_id, "setup_seconds": perf_counter() - started}
    finally:
        if created:
            subprocess.run(["docker", "buildx", "rm", "--force", name], check=True, stdout=subprocess.DEVNULL)


def sample(report, target, variant, number, policy, scope, executor, output, *, seed=False):
    label = f"{target}-{number}-{variant}"
    directory = output / label
    directory.mkdir()
    result = {"target": target, "variant": variant, "seed": seed, "status": "running", "scope": scope}
    report["samples"].append(result)
    try:
        with fresh_builder(directory) as builder, (directory / "checks.log").open("w") as checks, redirect_stdout(checks):
            result["builder"] = builder
            require(report.setdefault("builder_image_id", builder["image_id"]) == builder["image_id"], "benchmark builder image changed between samples")
            tag = f"shared-datasets-cache-benchmark/{target}:{executor}-{uuid.uuid4().hex[:12]}"
            original = production.commands(target, executor)[0]
            command = policy_command(policy, original, target, scope, builder["name"], tag, seed=seed)
            result["command"] = command
            started = total_started = perf_counter()
            with (directory / "build.log").open("w") as log:
                subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
            result["build_seconds"] = perf_counter() - started
            result["resolved_sources"] = re.findall(r"^#\d+.*(?:FROM |resolve ).*@sha256:[0-9a-f]{64}.*$",
                                                    (directory / "build.log").read_text(), re.MULTILINE)
            if variant == "current" and not seed:
                result["cached_dependency_vertices"] = require_cached_dependency((directory / "build.log").read_text(), target)
            started = perf_counter()
            image_id = production.resolve_image(tag)
            result["built_daemon_id"] = image_id
            for check in production.commands(target, executor, image_id=image_id)[1:]:
                subprocess.run(check, check=True, stdout=checks, stderr=subprocess.STDOUT)
            interpreter = "/app/.venv/bin/python" if target == "monitoring-slack" else "python"
            versions = subprocess.check_output(["docker", "run", "--rm", "--platform", "linux/amd64", "--network", "none",
                                                "--entrypoint", interpreter, image_id, "-c", PACKAGES], text=True)
            result["installed_packages"] = json.loads(versions)
            result["checks_seconds"] = perf_counter() - started
            # pack_image's canonical source tag is immediately bound to the
            # exact ID that passed checks; unique build tags cannot select stale bytes.
            subprocess.run(["docker", "image", "tag", image_id, f"shared-datasets-preflight/{target}:{executor}"], check=True)
            started = perf_counter()
            image = production.pack_image(target, executor, image_id, directory / "images")
            result["pack_seconds"] = perf_counter() - started
            started = perf_counter()
            archive = directory / "images" / image["archive"]
            result["loaded_daemon_id"] = production.load_image(archive, image)
            result["load_and_proof_seconds"] = perf_counter() - started
            result["total_seconds"] = perf_counter() - total_started
            result["image"] = image
            # Delete only this newly created TAR after the real loader verified it.
            require(archive == directory / "images" / f"{target}.docker.tar", "unexpected benchmark archive path")
            archive.unlink()
        result["status"] = "success"
    except Exception as error:
        result.update(status="failure", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        (directory / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def write_report(output, report):
    (output / "results.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    lines = ["# Small-image GHA cache comparison", "", "Fresh builders on a shared warm host; not a cold-VM benchmark.", "",
             f"Status: {report['status']}", "", "| Target | Policy | Samples | Median build seconds | Median full-check seconds |",
             "|---|---|---:|---:|---:|"]
    for target in TARGETS:
        for variant in ("current", "proposed"):
            samples = [item for item in report["samples"] if item["target"] == target and item["variant"] == variant
                       and not item["seed"] and item["status"] == "success"]
            if samples:
                lines.append(f"| {target} | {variant} | {len(samples)} | {statistics.median(item['build_seconds'] for item in samples):.3f} | "
                             f"{statistics.median(item['total_seconds'] for item in samples):.3f} |")
    lines += ["", "Strict seed and builder setup are recorded separately and excluded from medians. All successful samples run actual smoke checks, image packing and loader verification.",
              "Samples are descriptive; future cache stalls and upstream network delays remain uncertain. Image timestamps may differ; installed package lists and each verified image identity are retained.", ""]
    for target, equal in report.get("package_inventory_equal", {}).items():
        if not equal:
            lines.append(f"Dependency inventories differ for {target}; inspect package lists before comparing timings.")
    (output / "results.md").write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(os.environ.get("GITHUB_EVENT_NAME") == "pull_request", "cache benchmark is a PR-only diagnostic")
    run_id, attempt = (os.environ.get(key, "") for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"))
    require(re.fullmatch(r"[1-9][0-9]*", run_id) and re.fullmatch(r"[1-9][0-9]*", attempt), "missing benchmark run identity")
    require(not git("status", "--porcelain", "--untracked-files=no"), "benchmark requires a clean tracked checkout")
    os.environ["SHARED_DATASETS_GITHUB_IMAGE_CACHE"] = "true"
    candidate = (ROOT / "scripts/production_image_contracts.py").read_text()
    baseline, baseline_hash = baseline_policy(args.base, candidate)
    executor = git("rev-parse", "HEAD")
    args.output.mkdir(parents=True, exist_ok=True)
    require(not (args.output / "results.json").exists(), "benchmark output already contains a result")
    report = {"schema_version": 1, "status": "running", "base": args.base, "commit": executor, "tree": git("rev-parse", "HEAD^{tree}"),
              "run_id": run_id, "attempt": attempt, "order": list(ORDER), "baseline_source_sha256": baseline_hash,
              "candidate_source_sha256": hashlib.sha256(candidate.encode()).hexdigest(), "samples": []}
    try:
        version = subprocess.check_output(["docker", "buildx", "version"], text=True).strip()
        require(re.search(r"\bv" + re.escape(BUILDX) + r"\b", version), "benchmark Buildx version mismatch")
        report["buildx"] = version
        report["docker_version"] = json.loads(subprocess.check_output(["docker", "version", "--format", "{{json .}}"], text=True))
        report["inputs"] = {}
        for relative in ("services/catalog_viewer/Dockerfile", "services/monitoring_slack/Dockerfile", "pyproject.toml", "uv.lock"):
            raw = (ROOT / relative).read_bytes()
            report["inputs"][relative] = {"sha256": hashlib.sha256(raw).hexdigest()}
            if relative.endswith("Dockerfile"):
                report["inputs"][relative]["from"] = [line for line in raw.decode().splitlines() if line.startswith("FROM ") or "COPY --from=" in line]
        for target in TARGETS:
            original = production.commands(target, executor)[0]
            require("--cache-from" in baseline(original, target) and "--cache-to" in baseline(original, target), "baseline must import and export cache")
            require("--cache-from" not in production.cached_build(original, target) and "--cache-to" not in production.cached_build(original, target),
                    "proposed policy must bypass remote cache for both measured targets")
            scope = f"benchmark-small-images-{run_id}-{attempt}-{target}"
            sample(report, target, "current", "seed", baseline, scope, executor, args.output, seed=True)
            for index, variant in enumerate(ORDER, 1):
                sample(report, target, variant, index, baseline if variant == "current" else production.cached_build,
                       scope, executor, args.output)
                write_report(args.output, report)
            report.setdefault("package_inventory_equal", {})[target] = len({
                json.dumps(item["installed_packages"], sort_keys=True) for item in report["samples"]
                if item["target"] == target and not item["seed"]}) == 1
        report["status"] = "success"
    except Exception as error:
        report.update(status="failure", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        write_report(args.output, report)


if __name__ == "__main__":
    main()
