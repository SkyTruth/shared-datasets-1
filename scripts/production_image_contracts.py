#!/usr/bin/env python3
"""Build actual production recipes and exercise their installed entrypoints.

No credentials, pushes or dataset writes. Missing scripts and dependencies fail
here instead of after release. CI and isolated agent preflight run this command.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import uuid

INTERPRETER_PROBE = r'''
import pathlib, shlex, shutil, subprocess
script = pathlib.Path(shutil.which("gdal_calc.py"))
line = script.open().readline().strip()
assert line.startswith("#!"), "GDAL CLI has no interpreter contract"
interpreter = shlex.split(line[2:])
subprocess.run(interpreter + ["-c", "import numpy; from osgeo import gdal_array; print(numpy.__version__)"], check=True)
subprocess.run([str(script), "--help"], check=True, stdout=subprocess.DEVNULL)
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


def verify_viewer_image(image):
    """Start the actual default CMD with no network or credentials, then check health."""
    name = "catalog-viewer-preflight-" + uuid.uuid4().hex
    subprocess.run(["docker", "run", "--platform", "linux/amd64", "--detach", "--network", "none", "--cpus", "4", "--memory", "512m", "--name", name, image], check=True)
    try:
        subprocess.run(["docker", "exec", name, "python", "-c", VIEWER_HEALTH_PROBE], check=True)
    except subprocess.CalledProcessError:
        subprocess.run(["docker", "logs", name], check=False)
        raise
    finally:
        subprocess.run(["docker", "rm", "--force", name], check=False)


def commands(target, executor):
    if target == "catalog-viewer":
        image = f"shared-datasets-preflight/{target}:{executor}"
        return [
            ["docker", "build", "--platform", "linux/amd64", "-f", "services/catalog_viewer/Dockerfile", "-t", image, "."],
            ["docker", "run", "--platform", "linux/amd64", "--rm", "--network", "none", "--entrypoint", "python", image, "-c", "from services.catalog_viewer import run; assert callable(run.main)"],
            [sys.executable, "scripts/production_image_contracts.py", "--viewer-image", image],
        ]
    package = target.replace("-", "_")
    image = f"shared-datasets-preflight/{target}:{executor}"
    build = ["docker", "build", "--platform", "linux/amd64", "--build-arg", f"SHARED_DATASETS_EXECUTOR_SHA={executor}", "-f", f"ingestion/{package}/Dockerfile", "-t", image, "."]
    run = ["docker", "run", "--platform", "linux/amd64", "--rm", "--cpus", "4", "--memory", "8g", image]
    checks = [
        run + ["python", "-c", f"import ingestion.{package}.run; import scripts.release_feature_model, scripts.vector_asset"],
        run + ["python", "-c", INTERPRETER_PROBE],
        run + ["sh", "-c", "ogr2ogr --version && tippecanoe --version && pmtiles version && command -v tippecanoe-decode"],
    ]
    if target == "wdpa-monthly":
        checks += [run + ["python", "scripts/wdpa_input_memory_probe.py", "--help"], run + ["python", "scripts/local_ingestion_smoke.py", "--workdir", "/work/smoke"]]
    return [build, *checks]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append", choices=["wdpa-monthly", "eamlis-monthly", "sea-ice-daily", "catalog-viewer"])
    parser.add_argument("--viewer-image", help="Smoke an already built viewer image without rebuilding or pushing")
    args = parser.parse_args()
    if args.viewer_image:
        if args.target:
            parser.error("viewer-image cannot be combined with build targets")
        verify_viewer_image(args.viewer_image)
        return
    executor = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    subprocess.run(["docker", "info"], check=True, stdout=subprocess.DEVNULL)
    for target in args.target or ["wdpa-monthly", "eamlis-monthly", "sea-ice-daily", "catalog-viewer"]:
        for command in commands(target, executor):
            subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
