#!/usr/bin/env python3
"""Build actual production recipes and exercise their installed entrypoints.

No credentials, pushes or dataset writes. Missing scripts and dependencies fail
here instead of after release. CI and isolated agent preflight run this command.
"""
from __future__ import annotations

import argparse
import subprocess

INTERPRETER_PROBE = r'''
import pathlib, shlex, shutil, subprocess
script = pathlib.Path(shutil.which("gdal_calc.py"))
line = script.open().readline().strip()
assert line.startswith("#!"), "GDAL CLI has no interpreter contract"
interpreter = shlex.split(line[2:])
subprocess.run(interpreter + ["-c", "import numpy; from osgeo import gdal_array; print(numpy.__version__)"], check=True)
subprocess.run([str(script), "--help"], check=True, stdout=subprocess.DEVNULL)
'''


def commands(target, executor):
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
    parser.add_argument("--target", action="append", choices=["wdpa-monthly", "eamlis-monthly", "sea-ice-daily"])
    args = parser.parse_args()
    executor = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    subprocess.run(["docker", "info"], check=True, stdout=subprocess.DEVNULL)
    for target in args.target or ["wdpa-monthly", "eamlis-monthly", "sea-ice-daily"]:
        for command in commands(target, executor):
            subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
