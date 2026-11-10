"""Install the exact official Linux x64 GitHub CLI used for receipt verification."""
from __future__ import annotations

import hashlib
import http.client
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

VERSION = "2.96.0"
ASSET = f"gh_{VERSION}_linux_amd64.tar.gz"
# Official v2.96.0 release checksums: https://github.com/cli/cli/releases/tag/v2.96.0
SHA256 = "83d5c2ccad5498f58bf6368acb1ab32588cf43ab3a4b1c301bf36328b1c8bd60"
URL = f"https://github.com/cli/cli/releases/download/v{VERSION}/{ASSET}"
# GitHub release downloads intermittently return gateway errors for about a minute.
GATEWAY_FAILURES = frozenset({500, 502, 503, 504})
RETRY_DELAYS = (10, 30, 60, None)


def download():
    """Fetch the release bytes, retrying gateway and transport outages but no other HTTP status."""
    for attempt, delay in enumerate(RETRY_DELAYS, 1):
        try:
            with urllib.request.urlopen(URL, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if delay is None or error.code not in GATEWAY_FAILURES:
                raise
            failure = f"HTTP {error.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.IncompleteRead) as error:
            if delay is None:
                raise
            failure = type(error).__name__
        print(f"Verifier download failed ({failure}); retrying ({attempt + 1}/{len(RETRY_DELAYS)}) in {delay}s",
              file=sys.stderr)
        time.sleep(delay)


def install():
    if (os.environ.get("RUNNER_OS"), os.environ.get("RUNNER_ARCH")) != ("Linux", "X64"):
        raise RuntimeError("protected receipt verifier requires the GitHub-hosted Linux x64 runner")
    root = Path(os.environ["RUNNER_TEMP"]) / "deployment-receipts" / "tools"
    root.mkdir(parents=True, exist_ok=True)
    archive = root / ASSET
    if not archive.exists():
        archive.write_bytes(download())
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
