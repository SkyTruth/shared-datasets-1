"""Native local Linux execution without host file-sharing dependencies."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def local_runtime() -> dict:
    server = json.loads(subprocess.check_output(["docker", "version", "--format", "{{json .Server}}"], text=True))
    architecture = server["Arch"]
    if server["Os"] != "linux" or architecture not in {"amd64", "arm64"}:
        raise ValueError("preflight requires a Linux Docker server on amd64 or arm64")
    return {"platform": f"linux/{architecture}", "server_architecture": architecture, "docker_version": server["Version"]}


def run_container(root: Path, work: Path, suite: str, image: str, platform: str) -> dict:
    """Copy a clean checkout into an owned container and retain its evidence.

    No host directories, Docker socket, environment credentials or user config
    are mounted. Each architecture gets its own dependency environment.
    """
    image_platform = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image], text=True).strip()
    if image_platform != platform:
        raise ValueError(f"{suite}: image platform {image_platform} differs from native runtime {platform}")
    command = ["docker", "create", "--platform", platform, "-w", "/workspace",
               "-e", "UV_PROJECT_ENVIRONMENT=/evidence/venv", "-e", "UV_CACHE_DIR=/evidence/uv-cache",
               "-e", "UV_LINK_MODE=copy"]
    environment = {"CI": "true"}
    if platform == "linux/arm64":
        # Older Apple Linux VMs report CPU extensions that bundled OpenSSL
        # cannot execute. Select its portable implementation, preserving the
        # locked cryptography wheel and the complete test corpus.
        environment["OPENSSL_armcap"] = "0"
    if suite == "browser":
        # Exercise GitHub's CI-only reporter behavior with owned public
        # metadata. Never forward tokens, run IDs or the caller's real event.
        plan = json.loads((work / "plan.json").read_text())
        (work / "browser-event.json").write_text(json.dumps({"pull_request": {
            "title": "Local preflight prospective merge", "number": 0,
            "base": {"sha": plan["base"]}, "head": {"sha": plan["head"]},
        }}) + "\n")
        environment.update({"GITHUB_ACTIONS": "true", "GITHUB_EVENT_PATH": "/browser-event.json",
                            "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "local/preflight",
                            "GITHUB_SHA": plan["tested_sha"]})
    for key, value in environment.items():
        command.extend(["-e", f"{key}={value}"])
    command.extend([image, "/usr/local/bin/python", "scripts/ci_preflight.py", "run-suite",
                    "--plan", "/plan.json", "--suite", suite, "--output", f"/evidence/{suite}"])
    identifier = subprocess.check_output(command, text=True).strip()
    record = {"platform": platform, "image": image, "container": identifier, "environment": environment}
    try:
        subprocess.run(["docker", "cp", f"{root}/.", f"{identifier}:/workspace"], check=True)
        subprocess.run(["docker", "cp", str(work / "plan.json"), f"{identifier}:/plan.json"], check=True)
        if suite == "browser":
            subprocess.run(["docker", "cp", str(work / "browser-event.json"), f"{identifier}:/browser-event.json"], check=True)
        subprocess.run(["docker", "start", "-a", identifier], check=False)
        state = json.loads(subprocess.check_output(["docker", "inspect", "--format", "{{json .State}}", identifier], text=True))
        if state["Running"] or state["Status"] != "exited":
            raise ValueError(f"{suite}: container did not reach a terminal state")
        record["exit_code"] = state["ExitCode"]
        record["image_id"] = subprocess.check_output(["docker", "inspect", "--format", "{{.Image}}", identifier], text=True).strip()
        subprocess.run(["docker", "cp", f"{identifier}:/evidence/{suite}/.", str(work / suite)], check=True)
    finally:
        subprocess.run(["docker", "rm", "-f", identifier], check=True)
    if record["exit_code"] != 0:
        raise ValueError(f"{suite}: container failed ({record['exit_code']}); evidence: {work / suite}")
    return record
