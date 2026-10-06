"""Install the exact official Linux x64 GitHub CLI used for receipt verification."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tarfile
import urllib.request

VERSION = "2.96.0"
ASSET = f"gh_{VERSION}_linux_amd64.tar.gz"
# Official v2.96.0 release checksums: https://github.com/cli/cli/releases/tag/v2.96.0
SHA256 = "83d5c2ccad5498f58bf6368acb1ab32588cf43ab3a4b1c301bf36328b1c8bd60"
URL = f"https://github.com/cli/cli/releases/download/v{VERSION}/{ASSET}"


def install():
    if (os.environ.get("RUNNER_OS"), os.environ.get("RUNNER_ARCH")) != ("Linux", "X64"):
        raise RuntimeError("protected receipt verifier requires the GitHub-hosted Linux x64 runner")
    root = Path(os.environ["RUNNER_TEMP"]) / "deployment-receipts" / "tools"
    root.mkdir(parents=True, exist_ok=True)
    archive = root / ASSET
    if not archive.exists():
        with urllib.request.urlopen(URL, timeout=60) as response:
            archive.write_bytes(response.read())
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise RuntimeError("official GitHub CLI archive checksum mismatch")
    # Read only the expected regular binary; do not extract archive paths.
    with tarfile.open(archive) as bundle:
        entry = bundle.getmember(f"gh_{VERSION}_linux_amd64/bin/gh")
        if not entry.isfile():
            raise RuntimeError("official verifier archive has no regular gh binary")
        raw = bundle.extractfile(entry).read()
    binary = root / "bin" / "gh"
    binary.parent.mkdir(exist_ok=True)
    binary.write_bytes(raw)
    binary.chmod(0o755)
    actual = subprocess.check_output([str(binary), "--version"], text=True)
    if not actual.startswith(f"gh version {VERSION} "):
        raise RuntimeError("installed receipt verifier version mismatch")
    with Path(os.environ["GITHUB_PATH"]).open("a") as stream:
        stream.write(str(binary.parent) + "\n")
    print(f"GitHub receipt verifier {VERSION} installed from checked official bytes.")


if __name__ == "__main__":
    install()
