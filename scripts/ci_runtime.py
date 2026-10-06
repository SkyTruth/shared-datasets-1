"""Pinned local Linux execution without host file-sharing dependencies.

ARM Docker hosts use a process-owned, checksum-verified BuildKit emulator.
Nothing installs or changes a system-wide binfmt handler.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tarfile
import urllib.request
from pathlib import Path

EMULATOR_RELEASE = "v0.18.2"
EMULATOR_VERSION = "7.1.0"
EMULATOR_ARCHIVE_SHA256 = "de0ae01abc689102de6a765f8f40a30e86203897d3a7b44511a3aa66824bbc48"
EMULATOR_URL = f"https://github.com/moby/buildkit/releases/download/{EMULATOR_RELEASE}/buildkit-{EMULATOR_RELEASE}.linux-arm64.tar.gz"


def extract_emulator(archive: Path, destination: Path) -> str:
    if hashlib.sha256(archive.read_bytes()).hexdigest() != EMULATOR_ARCHIVE_SHA256:
        raise ValueError("local emulator archive checksum mismatch")
    with tarfile.open(archive) as bundle:
        candidates = [member for member in bundle.getmembers() if member.name == "bin/buildkit-qemu-x86_64"]
        if len(candidates) != 1 or not candidates[0].isfile():
            raise ValueError("local emulator archive must contain exactly one regular emulator")
        source = bundle.extractfile(candidates[0])
        assert source is not None
        executable = source.read()
    # A native ARM64 ELF entrypoint launches the unchanged amd64 guest image.
    if executable[:4] != b"\x7fELF" or executable[4:6] != b"\x02\x01" or executable[18:20] != b"\xb7\x00":
        raise ValueError("local emulator must be a little-endian ARM64 ELF executable")
    destination.write_bytes(executable)
    destination.chmod(0o755)
    return hashlib.sha256(executable).hexdigest()


def local_runtime(work: Path) -> tuple[list[str], dict]:
    server = json.loads(subprocess.check_output(["docker", "version", "--format", "{{json .Server}}"], text=True))
    architecture = server["Arch"]
    if server["Os"] != "linux" or architecture not in {"amd64", "arm64"}:
        raise ValueError("preflight requires a Linux Docker server on amd64 or arm64")
    record = {"platform": "linux/amd64", "server_architecture": architecture, "docker_version": server["Version"]}
    if architecture == "amd64":
        return [], record
    directory = work / "runtime"
    directory.mkdir()
    archive = directory / "buildkit-arm64.tar.gz"
    with urllib.request.urlopen(EMULATOR_URL, timeout=60) as response, archive.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    executable = directory / "buildkit-qemu-x86_64"
    executable_hash = extract_emulator(archive, executable)
    record["emulator"] = {"release": EMULATOR_RELEASE, "required_version": EMULATOR_VERSION, "archive_sha256": EMULATOR_ARCHIVE_SHA256, "executable_sha256": executable_hash}
    return ["-v", f"{directory}:/ci-runtime:ro", "--entrypoint", "/ci-runtime/buildkit-qemu-x86_64"], record


def prove_runtime(arguments: list[str], record: dict, image: str) -> None:
    if not arguments:
        return
    executable = Path(arguments[1].removesuffix(":/ci-runtime:ro")) / "buildkit-qemu-x86_64"
    identifier = subprocess.check_output(["docker", "create", "--platform", "linux/amd64", "--entrypoint", "/buildkit-qemu-x86_64", image, "--version"], text=True).strip()
    try:
        subprocess.run(["docker", "cp", str(executable), f"{identifier}:/buildkit-qemu-x86_64"], check=True)
        output = subprocess.check_output(["docker", "start", "-a", identifier], text=True)
    finally:
        subprocess.run(["docker", "rm", "-f", identifier], check=True)
    match = re.search(r"qemu-x86_64 version (\d+\.\d+\.\d+)", output)
    if match is None or match.group(1) != EMULATOR_VERSION:
        raise ValueError("local emulator reported an unexpected version")
    record["emulator"]["version"] = match.group(1)


def suite_platform(suite: str, record: dict) -> str:
    # Chromium requires native fork/GPU behavior, and Node child processes have
    # faulted under cross-architecture emulation, as has UV during lint setup.
    # Run unchanged pinned tools natively; release CLI fixtures exercise amd64.
    if suite in {"lint", "tests", "browser", "sdk-node22", "sdk-node24"} and record["server_architecture"] == "arm64":
        return "linux/arm64"
    return "linux/amd64"


def run_container(root: Path, work: Path, suite: str, image: str, platform: str, arguments: list[str]) -> dict:
    """Copy a clean checkout into an owned container and retain its evidence.

    No host directories, Docker socket, environment credentials or user config
    are mounted. Each architecture gets its own dependency environment.
    """
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
    emulator = platform == "linux/amd64" and bool(arguments)
    if emulator:
        command.extend(["--entrypoint", "/buildkit-qemu-x86_64"])
    command.extend([image, "/usr/local/bin/python", "scripts/ci_preflight.py", "run-suite",
                    "--plan", "/plan.json", "--suite", suite, "--output", f"/evidence/{suite}"])
    identifier = subprocess.check_output(command, text=True).strip()
    record = {"platform": platform, "image": image, "container": identifier, "environment": environment}
    try:
        subprocess.run(["docker", "cp", f"{root}/.", f"{identifier}:/workspace"], check=True)
        subprocess.run(["docker", "cp", str(work / "plan.json"), f"{identifier}:/plan.json"], check=True)
        if suite == "browser":
            subprocess.run(["docker", "cp", str(work / "browser-event.json"), f"{identifier}:/browser-event.json"], check=True)
        if emulator:
            executable = Path(arguments[1].removesuffix(":/ci-runtime:ro")) / "buildkit-qemu-x86_64"
            subprocess.run(["docker", "cp", str(executable), f"{identifier}:/buildkit-qemu-x86_64"], check=True)
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
