"""Exercise the installer with only the source files its Docker layer receives."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from unittest.mock import Mock

import pytest
from scripts import check_workflow_syntax as syntax, ci_install_tools as installer

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


@pytest.mark.parametrize('version', [None, 'version: 0.10.0', 'version: 0.11.0'])
def test_shellcheck_is_required_and_its_version_is_recorded(monkeypatch, version, capsys):
    monkeypatch.setattr(syntax.shutil, 'which', lambda _: '/tools/shellcheck' if version else None)
    monkeypatch.setattr(syntax.subprocess, 'check_output', lambda *_a, **_k: version)
    lint = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(syntax.subprocess, 'run', lint)
    if version != 'version: 0.11.0':
        with pytest.raises(ValueError, match='ShellCheck'):
            syntax.main()
        lint.assert_not_called()
    else:
        assert syntax.main() == 0
        assert '-shellcheck=/tools/shellcheck' in lint.call_args.args[0]
        assert 'ShellCheck 0.11.0' in capsys.readouterr().out


@pytest.mark.parametrize('architecture', ['x86_64', 'aarch64'])
@pytest.mark.parametrize('corrupt', [False, True])
def test_shellcheck_install_verifies_the_pinned_archive_before_writing(tmp_path, monkeypatch, architecture, corrupt):
    import hashlib
    import io
    import tarfile
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode='w:gz') as archive:
        member = tarfile.TarInfo('shellcheck-v0.11.0/shellcheck')
        member.size = len(b'tested binary')
        archive.addfile(member, io.BytesIO(b'tested binary'))
    raw = payload.getvalue()
    monkeypatch.setattr(installer.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(installer.platform, 'machine', lambda: architecture)
    monkeypatch.setattr(installer, 'download', lambda _: raw + (b'corruption' if corrupt else b''))
    monkeypatch.setattr(installer, 'SHELLCHECK_SHA256', dict.fromkeys(('amd64','arm64'), hashlib.sha256(raw).hexdigest()))
    if corrupt:
        with pytest.raises(ValueError, match='checksum mismatch'):
            installer.install(tmp_path, ['shellcheck'])
        assert not (tmp_path/'bin/shellcheck').exists()
    else:
        installer.install(tmp_path, ['shellcheck'])
        assert (tmp_path/'bin/shellcheck').read_bytes() == b'tested binary'
