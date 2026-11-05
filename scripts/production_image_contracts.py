#!/usr/bin/env python3
"""Build actual production recipes and exercise their installed entrypoints.

No credentials, pushes or dataset writes. Missing scripts and dependencies fail
here instead of after release. CI and isolated agent preflight run this command.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.terraform_target_contracts import registry
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


MONITORING_PROBE = r'''import base64, json
from types import SimpleNamespace
from services.monitoring_slack import run as receiver
incident = {"incident_id": "installed-probe", "scoping_project_id": receiver.PROJECT,
            "state": "open", "policy_name": "Installed receiver <probe>"}
def envelope(subscription):
    return json.dumps({"subscription": subscription, "message": {"data": base64.b64encode(
        json.dumps({"version": "1.2", "incident": incident}).encode()).decode()}})
assert receiver.event(envelope(receiver.SUBSCRIPTION)) == incident
assert "&lt;probe&gt;" in receiver.message(incident, False)["text"]
assert "separate verification" in receiver.message(incident, True)["text"]
try:
    receiver.event(envelope("projects/foreign/subscriptions/foreign"))
except ValueError:
    pass
else:
    raise AssertionError("Installed receiver accepted a foreign subscription")
state = SimpleNamespace(raw=None, generation=0, posts=[], updates=[])
def save(key, record, generation):
    assert key == incident["incident_id"] and generation == state.generation
    state.raw, state.generation = json.dumps(record), generation + 1
    return state.generation
def post(payload):
    assert json.loads(state.raw)["ts"] is None, "Receipt claim must precede delivery"
    state.posts.append(payload)
    return "1792830681.000001"
store = SimpleNamespace(load=lambda key: (json.loads(state.raw) if state.raw else None, state.generation), save=save)
slack = SimpleNamespace(post=post, update=lambda ts, payload: state.updates.append((ts, payload)))
identity = {"channel": "C12345678", "team": "T12345678", "user": "U12345678"}
for value in (incident, incident, {**incident, "state": "closed"}, incident):
    receiver.deliver(value, store, slack, identity)
assert len(state.posts) == 1 and len(state.updates) == 2
assert json.loads(state.raw)["closed"] and state.updates[-1][0] == "1792830681.000001"
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
        subprocess.run(["docker", "run", "--platform", "linux/amd64", "--detach", "--network", "none", "--tmpfs", "/tmp", *resource_limits("catalog-viewer"), "--name", name, image], check=True)
        subprocess.run(["docker", "exec", name, "python", "-c", VIEWER_HEALTH_PROBE], check=True)
    except subprocess.CalledProcessError:
        subprocess.run(["docker", "logs", name], check=False)
        raise
    finally:
        subprocess.run(["docker", "rm", "--force", name], check=False)


def resource_limits(target):
    """Read the declared job/service limits; reject nonliteral configured values."""
    package = target.replace("-", "_")
    text = (ROOT / f"terraform/envs/prod/{package}.tf").read_text()
    text = re.sub(r'/\*.*?\*/|(?m:^[ \t]*(?:#|//)[^\n]*$)', '', text, flags=re.S)
    if target in {"catalog-viewer", "monitoring-slack"}:
        block = re.search(rf'^([ \t]*)resource "google_cloud_run_v2_service" "{package}" {{(.*?)^\1}}', text, re.S | re.M)[2]
        limits = re.search(r'limits\s*=\s*{([^}]+)}', block, re.S)
        if limits is None and re.search(r'\bresources\s*{', block):
            raise ValueError(f"{target}: configured production limits must be literal")
        # Cloud Run service defaults when Terraform omits resources entirely.
        block = limits[1] if limits else 'cpu = "1"\nmemory = "512Mi"'
    else:
        block = re.search(rf'^([ \t]*)module "{package}_job" {{(.*?)^\1}}', text, re.S | re.M)[2]
    values = {}
    for key in ("cpu", "memory"):
        match = re.search(rf'(?:^|,)\s*{key}\s*=\s*"([0-9]+(?:\.[0-9]+)?(?:Mi|Gi)?)"(?=\s*(?:,|$))', block, re.M)
        if match is None:
            raise ValueError(f"{target}: production {key} limit must be a literal")
        values[key] = match[1]
    # CFS quotas preserve the configured cap even on a smaller CI host. Docker
    # rejects --cpus above daemon capacity before the container can start.
    return ["--cpu-period", "100000", "--cpu-quota", str(int(Decimal(values["cpu"]) * 100000)),
            "--memory", values["memory"].replace("Gi", "g").replace("Mi", "m")]


def commands(target, executor, *, image_id=None):
    image = f"shared-datasets-preflight/{target}:{executor}"
    tested_image = image_id or image
    build = ["docker", "build", "--platform", "linux/amd64", "--build-arg",
             f"SHARED_DATASETS_EXECUTOR_SHA={executor}", "-f", RECIPES[target], "-t", image, "."]
    run = ["docker", "run", "--platform", "linux/amd64", "--rm", "--network", "none", "--tmpfs", "/tmp", *resource_limits(target)]
    checks = registry(section="image_checks")[target]
    if not checks or set(checks) - {"host", "container"} or any(not rows or any(not args for args in rows) for rows in checks.values()):
        raise ValueError("Invalid registered installed image checks")
    values = dict(python=sys.executable, image=tested_image, interpreter_probe=INTERPRETER_PROBE,
                  native_probe=NATIVE_VERSION_PROBE, monitoring_probe=MONITORING_PROBE)
    fixture = ROOT / f"tests/fixtures/production_images/{target}.json"
    if any("{fixture}" in args for rows in checks.values() for args in rows):
        values["fixture"] = fixture.read_text()
    files = dict(usage_probe=ROOT / "scripts/dataset_usage_cold_start.py",
        fixture_script=ROOT / "scripts/installed_ingestion_fixture.py", smoke=ROOT / "scripts/local_ingestion_smoke.py")
    values.update({key: path.read_text() for key, path in files.items()
                   if any("{" + key + "}" in args for rows in checks.values() for args in rows)})
    return [build, *[({"container": run, "host": []}[mode]) + [arg.format_map(values) for arg in args]
                    for mode, rows in checks.items() for args in rows]]


def cached_build(command, target):
    """Reuse build layers in Actions; checks and retained images always run anew."""
    if os.environ.get("SHARED_DATASETS_GITHUB_IMAGE_CACHE") != "true" or target in {"catalog-viewer", "monitoring-slack"}:
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
