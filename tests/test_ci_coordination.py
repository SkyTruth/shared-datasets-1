"""Exercise coordination through real kernel locks and the CLI boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import ci_preflight


ROOT = Path(__file__).resolve().parents[1]


def child(directory: Path, program: str, **kwargs):
    prefix = (
        "from pathlib import Path\n"
        "from scripts import ci_preflight as p\n"
        f"p.LOCAL_COORDINATION_DIRECTORY = Path({str(directory)!r})\n"
    )
    return subprocess.Popen([sys.executable, "-c", prefix + program], cwd=ROOT, text=True, **kwargs)


def test_busy_cli_fails_before_clone_build_or_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(ci_preflight, "LOCAL_COORDINATION_DIRECTORY", tmp_path)
    with ci_preflight.local_preflight_slot(ROOT, "base-owner", "head-owner"):
        program = (
            "import sys\n"
            "sys.argv = ['ci_preflight.py', '--base', 'base-other', '--head', 'head-other']\n"
            "def expensive(args):\n"
            "    raise AssertionError('must not start a clone or validation')\n"
            "p.preflight = expensive\n"
            "raise SystemExit(p.main())\n"
        )
        with child(tmp_path, program, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   env={**os.environ, "SHARED_DATASETS_WORKDIR": str(tmp_path / "other-root"),
                        "TMPDIR": str(tmp_path / "other-temp")}) as process:
            stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 1
        assert not stdout
        assert "another local preflight owns" in stderr
        assert "head-owner" in stderr
        assert not list(tmp_path.rglob("preflight.json"))


@pytest.mark.parametrize("termination", ["terminate", "kill"])
def test_crashed_or_cancelled_owner_releases_without_deleting_file(tmp_path, monkeypatch, termination):
    monkeypatch.setattr(ci_preflight, "LOCAL_COORDINATION_DIRECTORY", tmp_path)
    program = (
        "import time\n"
        "with p.local_preflight_slot(Path('.'), 'base', 'abandoned-head'):\n"
        "    print('locked', flush=True)\n"
        "    time.sleep(30)\n"
    )
    with child(tmp_path, program, stdout=subprocess.PIPE) as process:
        try:
            assert process.stdout.readline().strip() == "locked"
            with pytest.raises(ValueError, match="another local preflight"):
                with ci_preflight.local_preflight_slot(ROOT, "base", "next-head"):
                    pytest.fail("concurrent holder admitted")
            getattr(process, termination)()
            process.wait(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
    path = tmp_path / f"preflight-{os.getuid()}.lock"
    inode = path.stat().st_ino
    # Persisted text still names the dead owner. Only kernel ownership matters.
    assert json.loads(path.read_text())["head"] == "abandoned-head"
    with ci_preflight.local_preflight_slot(ROOT, "new-base", "next-head"):
        assert json.loads(path.read_text())["head"] == "next-head"
        assert path.stat().st_ino == inode


def test_exception_and_stale_diagnostic_text_do_not_block_next_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(ci_preflight, "LOCAL_COORDINATION_DIRECTORY", tmp_path)
    path = tmp_path / f"preflight-{os.getuid()}.lock"
    path.write_text("invalid old diagnostic text")
    with pytest.raises(RuntimeError, match="failed validation"):
        with ci_preflight.local_preflight_slot(ROOT, "base", "failed-head"):
            raise RuntimeError("failed validation")
    with ci_preflight.local_preflight_slot(ROOT, "base", "new-head"):
        assert json.loads(path.read_text())["head"] == "new-head"
