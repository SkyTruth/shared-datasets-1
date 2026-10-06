"""Install the versioned CI binaries from their official release archives."""

from __future__ import annotations

import argparse
import hashlib
import io
import platform
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_toolchain import TOOLCHAIN


def download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def checked_archive(url: str, sums_url: str, filename: str) -> bytes:
    sums = download(sums_url).decode()
    checksums = {line.split()[-1].lstrip("*"): line.split()[0] for line in sums.splitlines() if line.split()}
    expected = checksums[filename]
    data = download(url)
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f"release checksum mismatch: {filename}")
    return data


def install(destination: Path, names: list[str]) -> None:
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64", "aarch64", "arm64"}:
        raise ValueError("CI tools installer requires Linux amd64 or arm64; use preflight's Linux container")
    architecture = "amd64" if platform.machine() in {"x86_64", "amd64"} else "arm64"
    bindir = destination / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    for name in names:
        version = TOOLCHAIN[name]
        if name.startswith("node"):
            node_arch = "x64" if architecture == "amd64" else "arm64"
            filename = f"node-v{version}-linux-{node_arch}.tar.gz"
            base = f"https://nodejs.org/dist/v{version}"
            data = checked_archive(f"{base}/{filename}", f"{base}/SHASUMS256.txt", filename)
            # Official archive has one known top-level directory. Strip it and
            # reject paths outside the destination instead of extracting blindly.
            root = destination / name
            root.mkdir(parents=True, exist_ok=True)
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                for member in archive.getmembers():
                    member.name = member.name.partition("/")[2]
                    if member.name:
                        archive.extract(member, root, filter="data")
            continue
        if name == "terraform":
            filename = f"terraform_{version}_linux_{architecture}.zip"
            base = f"https://releases.hashicorp.com/terraform/{version}"
            data = checked_archive(f"{base}/{filename}", f"{base}/terraform_{version}_SHA256SUMS", filename)
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                binary = archive.read("terraform")
        else:
            owner = "rhysd/actionlint" if name == "actionlint" else "gitleaks/gitleaks"
            arch = architecture if name == "actionlint" else ("x64" if architecture == "amd64" else "arm64")
            filename = f"{name}_{version}_linux_{arch}.tar.gz"
            base = f"https://github.com/{owner}/releases/download/v{version}"
            data = checked_archive(f"{base}/{filename}", f"{base}/{name}_{version}_checksums.txt", filename)
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                extracted = archive.extractfile(name)
                if extracted is None:
                    raise ValueError(f"release binary missing: {name}")
                binary = extracted.read()
        target = bindir / name
        target.write_bytes(binary)
        target.chmod(0o755)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("tools", nargs="+", choices=("node22", "node24", "terraform", "gitleaks", "actionlint"))
    args = parser.parse_args()
    install(args.destination, args.tools)


if __name__ == "__main__":
    main()
