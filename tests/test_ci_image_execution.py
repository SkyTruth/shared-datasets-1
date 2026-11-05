"""Exercise target scheduling and manifest assembly through the real suite CLI."""
from __future__ import annotations

import json
import hashlib
import io
import sys
import zipfile

import pytest

from scripts import ci_preflight as preflight
from scripts.ci_contract import expected_tools, resolve_selection
from ci_result_fixtures import production_images
from test_ci_preflight import fixture_repo
from test_tested_image_handoff import download, handoff as handoff, zipped
from scripts.deployment_revision import DeploymentError


PRODUCER = '''
import argparse, fcntl, json, subprocess, time, signal
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--target', action='append')
parser.add_argument('--output', type=Path)
args = parser.parse_args()
assert len(args.target) == 1, 'one independent target per command'
target = args.target[0]
metrics = args.output.parent / 'metrics.json'
def update(delta):
    with metrics.open('a+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        state = json.loads(stream.read() or '{"active":0,"peak":0,"started":0}')
        state['active'] += delta
        state['started'] += delta > 0
        state['peak'] = max(state['peak'], state['active'])
        stream.seek(0)
        stream.truncate()
        json.dump(state, stream)
        return state
state = update(1)
def stop(*_):
    raise SystemExit(143)
signal.signal(signal.SIGTERM, stop)
try:
    deadline = time.monotonic() + 5
    while COUNT > 1 and state['started'] < 2:
        assert time.monotonic() < deadline, 'targets did not execute concurrently'
        time.sleep(0.01)
        state = update(0)
    if DEFECT == 'command':
        if target == BROKEN:
            raise SystemExit(7)
        time.sleep(30)  # Must be terminated when the other target fails.
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    images = {target: dict(IMAGES[target], source_tag=f'shared-datasets-preflight/{target}:{sha}')}
    if target == BROKEN and DEFECT == 'wrong-set':
        images = {}
    directory = args.output / 'images'
    directory.mkdir(parents=True)
    if not (target == BROKEN and DEFECT == 'missing-tar'):
        (directory / f'{target}.docker.tar').write_bytes(target.encode())
    if not (target == BROKEN and DEFECT == 'missing-manifest'):
        (directory / 'manifest.json').write_text(json.dumps({
            'schema_version': 1, 'tested_sha': 'f' * 40 if target == BROKEN and DEFECT == 'stale' else sha,
            'images': images,
        }))
finally:
    update(-1)
'''


def test_empty_image_selection_cannot_start_a_producer(tmp_path):
    with pytest.raises(ValueError, match="without image targets"):
        preflight.suite_commands("production-images", tmp_path,
                                 {"base": "base", "tested_sha": "head", "changed_paths": [], "production_image_targets": [], "suites": []}, tmp_path)


@pytest.mark.parametrize("suite", ["browser", "geospatial-integration", "production-images"])
def test_historical_fresh_images_remain_loadable_with_unrelated_reused_suites(handoff, suite):
    with zipfile.ZipFile(io.BytesIO(handoff["payloads"][0])) as archive:
        evidence = json.loads(archive.read("evidence.json"))
    evidence["reused_suites"] = {suite: {"pr_number": 42, "run_id": 100, "run_attempt": 1}}
    payload = zipped({"evidence.json": evidence})
    handoff["payloads"][0] = payload
    handoff["artifacts"][0].update(size_in_bytes=len(payload), digest="sha256:" + hashlib.sha256(payload).hexdigest())
    if suite == "production-images":
        with pytest.raises(DeploymentError, match="cross-run image reuse"):
            download(handoff)
        assert not handoff["calls"]
    else:
        assert download(handoff)["config_digest"] == handoff["manifest"]["images"]["sea-ice-daily"]["config_digest"]


@pytest.mark.parametrize("matrix", [False, True])
@pytest.mark.parametrize("paths", [["ingestion/eamlis_monthly/run.py"], ["pyproject.toml"]])
@pytest.mark.parametrize("defect", [None, "command", "missing-manifest", "wrong-set", "stale", "missing-tar"])
def test_real_suite_cli_bounds_parallel_targets_and_rejects_incomplete_output(tmp_path, monkeypatch, paths, defect, matrix):
    source, git = fixture_repo(tmp_path)
    targets = resolve_selection(paths)["production_image_targets"][:1] if matrix else resolve_selection(paths)["production_image_targets"]
    producer = source / "scripts/production_image_contracts.py"
    # The external Docker producer is a small executable fixture. The real
    # command builder, child processes, JSON files and suite CLI remain in use.
    producer.write_text(f"COUNT={len(targets)!r}\nBROKEN={targets[0]!r}\nDEFECT={defect!r}\n"
                        f"IMAGES={production_images('a' * 40)['images']!r}\n" + PRODUCER)
    git("add", ".")
    git("commit", "-m", "install external image producer fixture")
    base = git("rev-parse", "HEAD")
    for path in paths:
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(target.read_text() + "\n# changed input\n")
    git("add", ".")
    git("commit", "-m", "select image targets")
    monkeypatch.setenv("GITHUB_RUN_ID", "42094352596")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.chdir(source)
    plan_path, output = tmp_path / "plan.json", tmp_path / "images-result"
    monkeypatch.setattr(sys, "argv", ["ci_preflight.py", "plan", "--base", base, "--head", "HEAD", "--output", str(plan_path)])
    assert preflight.main() == 0
    assert json.loads(plan_path.read_text())["source"] == {"run_id": "42094352596", "run_attempt": 1}
    check_output = preflight.subprocess.check_output
    monkeypatch.setattr(preflight.subprocess, "check_output", lambda argv, **kwargs:
                        "{}" if argv[:2] == ["docker", "version"] else check_output(argv, **kwargs))
    monkeypatch.setattr(preflight, "probe_tools", lambda suite, environment: expected_tools(suite))
    monkeypatch.chdir(source)
    monkeypatch.setattr(sys, "argv", ["ci_preflight.py", "run-suite", "--plan", str(plan_path),
                                      "--suite", "production-images", "--output", str(output), *(["--image-target", targets[0]] if matrix else [])])
    assert preflight.main() == (1 if defect else 0)
    result = json.loads((output / "result.json").read_text())
    metrics = json.loads((output / "metrics.json").read_text())
    assert metrics["active"] == 0, "the suite returned while a child was still running"
    assert metrics["peak"] == min(2, len(targets))
    if defect == "command":
        assert metrics["started"] <= 2, "failure started queued image work"
    if defect:
        assert result["status"] == "failure"
        assert "production_images" not in result
        assert not (output / "images/manifest.json").exists()
    else:
        assert result["status"] == "success"
        assert len(result["commands"]) == len(targets)
        assert all(command["exit_code"] == 0 for command in result["commands"])
        manifest = json.loads((output / "images/manifest.json").read_text())
        assert result["production_images"] == manifest
        assert set(manifest["images"]) == set(targets)
        assert {path.stem.removesuffix('.docker') for path in (output / "images").glob("*.tar")} == set(targets)
        assert all(not (output / target / "images" / f"{target}.docker.tar").exists() for target in targets)
