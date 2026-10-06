#!/usr/bin/env python3
"""Build actual production recipes and exercise their installed entrypoints.

No credentials, pushes or dataset writes. Missing scripts and dependencies fail
here instead of after release. CI and isolated agent preflight run this command.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.tested_image_bundle import TARGETS, pack_image

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


VIEWER_HEALTH_PROBE = r'''import time, urllib.request
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
        subprocess.run(["docker", "run", "--platform", "linux/amd64", "--detach", "--network", "none", "--cpus", "4", "--memory", "512m", "--name", name, image], check=True)
        subprocess.run(["docker", "exec", name, "python", "-c", VIEWER_HEALTH_PROBE], check=True)
    except subprocess.CalledProcessError:
        subprocess.run(["docker", "logs", name], check=False)
        raise
    finally:
        subprocess.run(["docker", "rm", "--force", name], check=False)


def commands(target, executor, *, image_id=None):
    image = f"shared-datasets-preflight/{target}:{executor}"
    tested_image = image_id or image
    if target == "catalog-viewer":
        return [
            ["docker", "build", "--platform", "linux/amd64", "-f", "services/catalog_viewer/Dockerfile", "-t", image, "."],
            ["docker", "run", "--platform", "linux/amd64", "--rm", "--network", "none", "--entrypoint", "python", tested_image, "-c", "from services.catalog_viewer import run; assert callable(run.main)"],
            [sys.executable, "scripts/production_image_contracts.py", "--viewer-image", tested_image],
        ]
    package = target.replace("-", "_")
    build = ["docker", "build", "--platform", "linux/amd64", "--build-arg", f"SHARED_DATASETS_EXECUTOR_SHA={executor}", "-f", f"ingestion/{package}/Dockerfile", "-t", image, "."]
    run = ["docker", "run", "--platform", "linux/amd64", "--rm", "--cpus", "4", "--memory", "8g", tested_image]
    checks = [
        run + ["python", "-c", f"import ingestion.{package}.run; import scripts.release_feature_model, scripts.vector_asset"],
        run + ["python", "-c", INTERPRETER_PROBE],
        run + ["python", "-c", NATIVE_VERSION_PROBE],
    ]
    if target == "wdpa-monthly":
        checks += [run + ["python", "scripts/wdpa_input_memory_probe.py", "--help"], run + ["python", "scripts/local_ingestion_smoke.py", "--workdir", "/work/smoke"]]
    return [build, *checks]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append", choices=["wdpa-monthly", "eamlis-monthly", "sea-ice-daily", "catalog-viewer"])
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
    for target in args.target or ["wdpa-monthly", "eamlis-monthly", "sea-ice-daily", "catalog-viewer"]:
        subprocess.run(commands(target, executor)[0], check=True)
        image_id = resolve_image(f"shared-datasets-preflight/{target}:{executor}")
        print(f"[{target}] testing immutable image {image_id}", flush=True)
        for command in commands(target, executor, image_id=image_id)[1:]:
            subprocess.run(command, check=True)
        if args.output and target in TARGETS:
            retained[target] = pack_image(target, executor, image_id, args.output / "images")
    if args.output and retained:
        (args.output / "images/manifest.json").write_text(json.dumps(
            {"schema_version": 1, "tested_sha": executor, "images": retained}, sort_keys=True
        ) + "\n")


if __name__ == "__main__":
    main()
