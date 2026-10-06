"""Pinned local execution of the same amd64 images used by hosted CI.

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
    output = subprocess.check_output(["docker", "run", "--rm", "--platform", "linux/amd64", *arguments, image, "--version"], text=True)
    match = re.search(r"qemu-x86_64 version (\d+\.\d+\.\d+)", output)
    if match is None or match.group(1) != EMULATOR_VERSION:
        raise ValueError("local emulator reported an unexpected version")
    record["emulator"]["version"] = match.group(1)
