"""Exercise the temporary cache benchmark's comparison and failure boundaries."""
from contextlib import contextmanager
import hashlib
import io
import json
import subprocess
import tarfile
from unittest.mock import Mock

import pytest

from scripts import benchmark_small_image_cache as benchmark
from scripts import tested_image_bundle as bundle
from scripts.deployment_revision import DeploymentError

BASE = "a" * 40
SHA = "b" * 40
ORIGINAL = '''import os
def cached_build(command, target):
    if os.environ.get("SHARED_DATASETS_GITHUB_IMAGE_CACHE") != "true":
        return command
    cache = f"type=gha,version=2,scope=production-{target}"
    return ["docker", "buildx", "build", "--load", "--cache-from", cache,
            "--cache-to", cache + ",mode=max,ignore-error=true", *command[2:]]
def actual_recipe():
    return "unchanged"
'''
PROPOSED = ORIGINAL.replace('    cache = f"type=gha', '    if target in {"catalog-viewer", "monitoring-slack"}:\n        return ["docker", "buildx", "build", "--load", *command[2:]]\n    cache = f"type=gha')


@pytest.fixture
def old_policy(monkeypatch):
    monkeypatch.setenv("SHARED_DATASETS_GITHUB_IMAGE_CACHE", "true")
    monkeypatch.setattr(benchmark, "git", lambda *args: ORIGINAL)
    policy, digest = benchmark.baseline_policy(BASE, PROPOSED)
    assert digest == hashlib.sha256(ORIGINAL.encode()).hexdigest()
    return policy


@pytest.mark.parametrize("alteration", ["recipe", "import", "missing-function", "mutable-ref"])
def test_baseline_rejects_changes_outside_policy_or_unpinned_ref(monkeypatch, alteration):
    monkeypatch.setattr(benchmark, "git", lambda *args: ORIGINAL)
    candidate = PROPOSED
    if alteration == "recipe":
        candidate = candidate.replace('return "unchanged"', 'return "changed"')
    elif alteration == "import":
        candidate += "\nimport shutil\n"
    elif alteration == "missing-function":
        candidate = candidate.replace("def cached_build(", "def other(")
    with pytest.raises(DeploymentError):
        benchmark.baseline_policy("main" if alteration == "mutable-ref" else BASE, candidate)


@pytest.mark.parametrize("seed", [False, True])
def test_policy_transport_changes_only_scope_tag_builder_and_strict_seed(old_policy, seed):
    original = ["docker", "build", "--platform", "linux/amd64", "-f", "actual/Dockerfile", "-t", "actual:source", "."]
    actual = benchmark.policy_command(old_policy, original, "catalog-viewer", "benchmark-run-1", "owned-builder", "unique:trial", seed=seed)
    assert original[-3:] == ["-t", "actual:source", "."]
    assert actual[:8] == ["docker", "buildx", "build", "--builder", "owned-builder", "--progress", "plain", "--load"]
    assert actual[-7:] == ["--platform", "linux/amd64", "-f", "actual/Dockerfile", "-t", "unique:trial", "."]
    assert "scope=production-" not in " ".join(actual)
    assert ("--cache-from" in actual) is not seed
    exporter = actual[actual.index("--cache-to") + 1]
    assert "scope=benchmark-run-1" in exporter
    assert f"ignore-error={'false' if seed else 'true'}" in exporter


@pytest.mark.parametrize("target,command", [("catalog-viewer", "python -m pip install --no-cache-dir dependency"),
                                           ("monitoring-slack", "uv sync --locked --no-dev")])
def test_cached_dependency_proof_uses_actual_build_vertex(target, command):
    log = f"#8 [stage-0 5/7] RUN {command}\n#8 CACHED\n#9 [stage-0 6/7] COPY source /app\n#9 DONE 0.1s\n"
    assert benchmark.require_cached_dependency(log, target) == ["8"]
    for changed in (log.replace("#8 CACHED", "#8 DONE 0.2s"), log.replace("#8 CACHED", "#9 CACHED"), ""):
        with pytest.raises(DeploymentError, match="cached dependency RUN"):
            benchmark.require_cached_dependency(changed, target)


@pytest.fixture
def sample_setup(tmp_path, monkeypatch, old_policy):
    @contextmanager
    def builder(directory):
        yield {"name": "owned-builder", "image_id": "same-builder-image", "setup_seconds": 1}
    monkeypatch.setattr(benchmark, "fresh_builder", builder)
    monkeypatch.setattr(benchmark.production, "commands", lambda target, executor, image_id=None: [
        ["docker", "build", "-f", "actual/Dockerfile", "-t", "original:tag", "."],
        ["docker", "run", "--network", "none", image_id, "actual-probe"]])
    monkeypatch.setattr(benchmark.production, "resolve_image", Mock(return_value="sha256:" + "c" * 64))
    monkeypatch.setattr(benchmark.production, "pack_image", Mock())
    monkeypatch.setattr(benchmark.production, "load_image", Mock())
    return {"report": {"samples": []}, "output": tmp_path, "policy": old_policy}


@pytest.mark.parametrize("target", benchmark.TARGETS)
def test_failed_real_build_is_terminal_before_inspection_checks_or_retention(sample_setup, monkeypatch, target):
    calls = []
    def failed(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(benchmark.subprocess, "run", failed)
    with pytest.raises(subprocess.CalledProcessError):
        benchmark.sample(sample_setup["report"], target, "current", 1, sample_setup["policy"], "benchmark-scope", SHA, sample_setup["output"])
    assert len(calls) == 1 and calls[0][:3] == ["docker", "buildx", "build"]
    benchmark.production.resolve_image.assert_not_called()
    benchmark.production.pack_image.assert_not_called()
    benchmark.production.load_image.assert_not_called()
    result = json.loads(next(sample_setup["output"].glob("*/result.json")).read_text())
    assert result["status"] == "failure"


@pytest.mark.parametrize("stage", ["checks", "pack", "load"])
def test_failed_checks_or_image_proof_cannot_be_reported_as_success(sample_setup, monkeypatch, stage):
    target = "catalog-viewer"
    def run(command, **kwargs):
        if command[:3] == ["docker", "buildx", "build"]:
            kwargs["stdout"].write("#8 [3/6] RUN python -m pip install dependency\n#8 CACHED\n")
        elif command[:2] == ["docker", "run"] and stage == "checks":
            raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(benchmark.subprocess, "run", run)
    monkeypatch.setattr(benchmark.subprocess, "check_output", lambda *args, **kwargs: "[]")
    def pack(target, executor, image_id, directory):
        if stage == "pack":
            raise bundle.ImageError("genuine pack failure")
        directory.mkdir()
        (directory / f"{target}.docker.tar").write_bytes(b"new archive requiring proof")
        return {"archive": f"{target}.docker.tar"}
    benchmark.production.pack_image.side_effect = pack
    benchmark.production.load_image.side_effect = bundle.ImageError("genuine load failure")
    with pytest.raises((subprocess.CalledProcessError, bundle.ImageError)):
        benchmark.sample(sample_setup["report"], target, "current", 1, sample_setup["policy"], "benchmark-scope", SHA, sample_setup["output"])
    assert sample_setup["report"]["samples"][0]["status"] == "failure"
    if stage == "checks":
        benchmark.production.pack_image.assert_not_called()
    if stage in {"checks", "pack"}:
        benchmark.production.load_image.assert_not_called()
    if stage == "load":
        assert list(sample_setup["output"].glob("*/images/*.docker.tar")), "unverified archives cannot be deleted as verified"


def test_builder_cleanup_targets_only_created_builder_even_if_bootstrap_fails(tmp_path, monkeypatch):
    run = Mock()
    monkeypatch.setattr(benchmark.subprocess, "run", run)
    monkeypatch.setattr(benchmark.subprocess, "check_output", Mock(side_effect=subprocess.CalledProcessError(1, ["inspect"])))
    with pytest.raises(subprocess.CalledProcessError):
        with benchmark.fresh_builder(tmp_path):
            pytest.fail("bootstrap failure cannot run a sample")
    created, removed = [call.args[0] for call in run.call_args_list]
    assert created[:3] == ["docker", "buildx", "create"]
    assert removed == ["docker", "buildx", "rm", "--force", created[created.index("--name") + 1]]


def test_order_is_balanced_without_counting_seed():
    assert benchmark.ORDER == ("current", "proposed", "proposed", "current")


def test_dirty_tracked_checkout_cannot_supply_benchmark_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["benchmark_small_image_cache.py", "--base", BASE, "--output", str(tmp_path / "result")])
    for key, value in (("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_RUN_ID", "123"), ("GITHUB_RUN_ATTEMPT", "1")):
        monkeypatch.setenv(key, value)
    git = Mock(return_value=" M services/catalog_viewer/Dockerfile")
    monkeypatch.setattr(benchmark, "git", git)
    baseline = Mock()
    monkeypatch.setattr(benchmark, "baseline_policy", baseline)
    with pytest.raises(DeploymentError, match="clean tracked checkout"):
        benchmark.main()
    git.assert_called_once_with("status", "--porcelain", "--untracked-files=no")
    baseline.assert_not_called()
    assert not (tmp_path / "result").exists()


def test_actual_retained_tar_verification_rejects_corruption_before_docker(tmp_path, monkeypatch):
    # A genuine serialized archive reaches the unchanged loader boundary; the
    # external Docker transport is mocked, never the archive/hash verifier.
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        member = tarfile.TarInfo("manifest.json")
        content = b"[]"
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    path = tmp_path / "catalog-viewer.docker.tar"
    path.write_bytes(raw.getvalue())
    image = {"archive_size": path.stat().st_size, "archive_sha256": "f" * 64}
    docker = Mock()
    monkeypatch.setattr(bundle.subprocess, "run", docker)
    with pytest.raises(bundle.ImageError, match="archive digest or size mismatch"):
        bundle.load_image(path, image)
    docker.assert_not_called()
