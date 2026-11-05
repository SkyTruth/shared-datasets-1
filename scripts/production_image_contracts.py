#!/usr/bin/env python3
"""Build actual production recipes and exercise their installed entrypoints.

No credentials, pushes or dataset writes. Missing scripts and dependencies fail
here instead of after release. CI and isolated agent preflight run this command.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.tested_image_bundle import BUILD_TARGETS, RECIPES, ROOT, load_image, pack_image

INTERPRETER_PROBE = r'''
import pathlib, shlex, shutil, subprocess
script = pathlib.Path(shutil.which("gdal_calc.py"))
line = script.open().readline().strip()
assert line.startswith("#!"), "GDAL CLI has no interpreter contract"
interpreter = shlex.split(line[2:])
subprocess.run(interpreter + ["-c", "import numpy; from osgeo import gdal_array; print(numpy.__version__)"], check=True)
subprocess.run([str(script), "--help"], check=True, stdout=subprocess.DEVNULL)
'''


NATIVE_VERSION_PROBE = r'''
import json, pathlib, re, shutil, subprocess
tools = {}
specifications = {
    "ogr2ogr": ("--version", r"^GDAL ([^\s,]+)", "3.6.2"),
    "tippecanoe": ("--version", r"^tippecanoe v([^\s,]+)", "2.52.0"),
    "pmtiles": ("version", r"^pmtiles ([^\s,]+)", "1.30.1"),
}
for name in [*specifications, "tippecanoe-decode"]:
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"Missing native image tool: {name}")
    path = str(pathlib.Path(executable).resolve(strict=True))
    tools[name] = {"path": path}
    if name in specifications:
        argument, pattern, expected = specifications[name]
        completed = subprocess.run([path, argument], check=True, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        output = completed.stdout.strip()
        matched = re.search(pattern, output, re.MULTILINE)
        if matched is None or matched.group(1) != expected:
            raise RuntimeError(f"{name} must report pinned version {expected}: {output!r}")
        tools[name].update(version=matched.group(1), output=output)
print(json.dumps({"schema_version": 1, "native_tools": tools}, sort_keys=True))
'''


VIEWER_HEALTH_PROBE = r'''import time, urllib.error, urllib.request
for attempt in range(20):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/healthz", timeout=1) as response:
            assert response.status == 200
            assert response.read().strip() == b"ok"
        break
    except (OSError, AssertionError, ValueError):
        if attempt == 19:
            raise
        time.sleep(0.5)
try:
    urllib.request.urlopen("http://127.0.0.1:8080/api/identity-review?uri=gs://foreign/object", timeout=2)
except urllib.error.HTTPError as error:
    assert error.code == 401, "Installed evidence reader must authenticate before storage access"
else:
    raise AssertionError("Installed evidence reader admitted an anonymous request")
'''


def resolve_image(image):
    """Bind every installed-code check to one local immutable image identity."""
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image], text=True
    ).strip()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        raise ValueError(f"Docker did not resolve an immutable image ID for {image}")
    return image_id


def verify_viewer_image(image):
    """Start the actual default CMD with no network or credentials, then check health."""
    image = resolve_image(image)
    name = "catalog-viewer-preflight-" + uuid.uuid4().hex
    try:
        subprocess.run(["docker", "run", "--platform", "linux/amd64", "--detach", "--network", "none", *resource_limits("catalog-viewer"), "--name", name, image], check=True)
        subprocess.run(["docker", "exec", name, "python", "-c", VIEWER_HEALTH_PROBE], check=True)
    except subprocess.CalledProcessError:
        subprocess.run(["docker", "logs", name], check=False)
        raise
    finally:
        subprocess.run(["docker", "rm", "--force", name], check=False)


@lru_cache(maxsize=None)
def resource_limits(target):
    """Read the declared job/service limits; reject nonliteral configured values."""
    package = target.replace("-", "_")
    text = (ROOT / f"terraform/envs/prod/{package}.tf").read_text()
    if target in {"catalog-viewer", "monitoring-slack"}:
        block = re.search(rf'^resource "google_cloud_run_v2_service" "{package}" {{(.*?)^}}', text, re.S | re.M)[1]
        limits = re.search(r'limits\s*=\s*{([^}]+)}', block, re.S)
        if limits is None and re.search(r'\bresources\s*{', block):
            raise ValueError(f"{target}: configured production limits must be literal")
        # Cloud Run service defaults when Terraform omits resources entirely.
        block = limits[1] if limits else 'cpu = "1"\nmemory = "512Mi"'
    else:
        block = re.search(rf'^module "{package}_job" {{(.*?)^}}', text, re.S | re.M)[1]
    values = {}
    for key in ("cpu", "memory"):
        match = re.search(rf'^\s*{key}\s*=\s*"([0-9]+(?:\.[0-9]+)?(?:Mi|Gi)?)"\s*$', block, re.M)
        if match is None:
            raise ValueError(f"{target}: production {key} limit must be a literal")
        values[key] = match[1]
    # CFS quotas preserve the configured cap even on a smaller CI host. Docker
    # rejects --cpus above daemon capacity before the container can start.
    return ["--cpu-period", "100000", "--cpu-quota", str(int(Decimal(values["cpu"]) * 100000)),
            "--memory", values["memory"].replace("Gi", "g").replace("Mi", "m")]


FIXTURE_BUILD = r'''import datetime as dt, json, pathlib
from scripts import release_feature_model as model
work = pathlib.Path("/tmp/shared-datasets-1/_scratch/installed-fixture")
work.mkdir(parents=True)
day = dt.date(2026, 5, 2)
if target == "mpatlas-monthly":
    from ingestion.mpatlas_monthly import run as job
    source = work / "source.geojson"
    source.write_text(json.dumps(fixture))
    enriched, records, _ = job.prepare_metadata(source, work, day)
    output = job.build_outputs(enriched, records, work, day)
else:
    from ingestion.eamlis_monthly import run as job, identity, identity_state
    source = work / "source.geojson"
    source.write_text(json.dumps(fixture["geojson"]))
    state = fixture["source"]
    state["stats"] = job.SourceStats(**state["stats"])
    state["fields"] = tuple(state["fields"])
    ledger = identity.Ledger.genesis().reserve(identity.classify_release(
        row["properties"] for row in fixture["geojson"]["features"]))
    output = job.build_asset_output(source=job.SourceState(**state),
        extract=job.SourceExtract(source, 3, 0), workdir=work, release_date=day,
        identity_reservation=identity_state.Reservation(ledger, {"path": "fixture"}, 1, None))
assert output.row_count == 3
validation = model.validate_sidecar_records(model.read_metadata_sidecar(output.metadata),
    expected_asset_slug=job.ASSET.slug, expected_release=day.isoformat())
assert validation.valid and validation.feature_count == 3
assert output.pmtiles.read_bytes()[:7] == b"PMTiles"
'''


def commands(target, executor, *, image_id=None):
    image = f"shared-datasets-preflight/{target}:{executor}"
    tested_image = image_id or image
    build = ["docker", "build", "--platform", "linux/amd64", "--build-arg",
             f"SHARED_DATASETS_EXECUTOR_SHA={executor}", "-f", RECIPES[target], "-t", image, "."]
    run = ["docker", "run", "--platform", "linux/amd64", "--rm", "--network", "none", *resource_limits(target)]
    if target == "monitoring-slack":
        return [build, run + ["--entrypoint", "/app/.venv/bin/python", tested_image, "-c", Path("tests/test_monitoring_slack.py").read_text()]]
    if target == "catalog-viewer":
        return [build, [sys.executable, "scripts/production_image_contracts.py", "--viewer-image", tested_image]]
    if target == "dataset-usage":
        return [build, run + [tested_image, "--print-policy-hash"],
                run + ["--entrypoint", "python", tested_image, "-c", Path(__file__).with_name("dataset_usage_cold_start.py").read_text()]]
    run += [tested_image, "python"]
    checks = [run + ["-c", INTERPRETER_PROBE], run + ["-c", NATIVE_VERSION_PROBE]]
    if target in {"eamlis-monthly", "mpatlas-monthly"}:
        fixture = (ROOT / f"tests/fixtures/production_images/{target}.json").read_text()
        checks.append(run + ["-c", f"target={target!r}\nfixture=__import__('json').loads({fixture!r})\n" + FIXTURE_BUILD])
    elif target == "wdpa-monthly":
        checks += [run + ["scripts/wdpa_input_memory_probe.py", "--help"],
                   run + ["scripts/local_ingestion_smoke.py", "--workdir", "/work/smoke"]]
    else:
        checks.append(run + ["-c", f"import ingestion.{target.replace('-', '_')}.run"])
    return [build, *checks]


def cached_build(command, target):
    """Reuse build layers in Actions; checks and retained images always run anew."""
    if os.environ.get("SHARED_DATASETS_GITHUB_IMAGE_CACHE") != "true":
        return command
    # These small Python recipes rebuild cheaply; remote cache reads have stalled.
    if target in {"catalog-viewer", "monitoring-slack"}:
        return command
    cache = f"type=gha,version=2,scope=production-{target}"
    return ["docker", "buildx", "build", "--load", "--cache-from", cache,
            "--cache-to", cache + ",mode=max,ignore-error=true", *command[2:]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append", choices=BUILD_TARGETS)
    parser.add_argument("--viewer-image", help="Smoke an already built viewer image without rebuilding or pushing")
    parser.add_argument("--output", type=Path, help="Retain exact tested deployment images in this suite evidence directory")
    args = parser.parse_args()
    if args.viewer_image:
        if args.target or args.output:
            parser.error("viewer-image cannot be combined with build targets")
        verify_viewer_image(args.viewer_image)
        return
    executor = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    subprocess.run(["docker", "info"], check=True, stdout=subprocess.DEVNULL)
    retained = {}
    for target in args.target or BUILD_TARGETS:
        subprocess.run(cached_build(commands(target, executor)[0], target), check=True)
        image_id = resolve_image(f"shared-datasets-preflight/{target}:{executor}")
        print(f"[{target}] testing immutable image {image_id}", flush=True)
        for command in commands(target, executor, image_id=image_id)[1:]:
            subprocess.run(command, check=True)
        if args.output:
            retained[target] = pack_image(target, executor, image_id, args.output / "images")
            # Exercise the deployment loader on this CI daemon before admitting
            # the handoff. Docker's exporter/store representation can differ
            # from the locally tested image's identity.
            load_image(args.output / "images" / retained[target]["archive"], retained[target])
    if args.output:
        (args.output / "images").mkdir(parents=True, exist_ok=True)
        (args.output / "images/manifest.json").write_text(json.dumps(
            {"schema_version": 1, "tested_sha": executor, "images": retained}, sort_keys=True
        ) + "\n")


if __name__ == "__main__":
    main()
