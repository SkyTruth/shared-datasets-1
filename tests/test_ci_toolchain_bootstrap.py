"""Exercise the installer with only the source files its Docker layer receives."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("missing_pins", [False, True])
def test_fresh_image_tool_installer_has_its_complete_bootstrap_source(tmp_path, missing_pins):
    copied = tmp_path / "ci-source" / "scripts"
    copied.mkdir(parents=True)
    for line in (ROOT / ".github/docker/preflight.Dockerfile").read_text().splitlines():
        if not line.startswith("COPY "):
            continue
        fields = shlex.split(line)
        if fields[-1] != "/opt/ci-source/scripts/":
            continue
        for source in fields[1:-1]:
            shutil.copyfile(ROOT / source, copied / Path(source).name)
    if missing_pins:
        (copied / "ci_toolchain.py").unlink()
    environment = {**os.environ, "PYTHONPATH": "", "PYTHONNOUSERSITE": "1"}
    completed = subprocess.run(
        [sys.executable, str(copied / "ci_install_tools.py"), "--help"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if missing_pins:
        assert completed.returncode != 0
        assert "scripts.ci_toolchain" in completed.stderr
    else:
        assert completed.returncode == 0, completed.stderr
        assert "--destination" in completed.stdout
