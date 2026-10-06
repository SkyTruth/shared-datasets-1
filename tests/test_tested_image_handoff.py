"""Retained bytes must survive a producer/consumer round trip and reject substitution."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
from unittest.mock import Mock
import zipfile

import pytest

from scripts import production_image_contracts as producer
from scripts import sdk_release_authorization as proof
from scripts import tested_image_authorization as consumer
from scripts import tested_image_bundle as bundle
from scripts.deployment_revision import DeploymentError

SHA = "a" * 40
REPO = "SkyTruth/shared-datasets-1"


def docker_tar(target, *, architecture="amd64", extra=None, tag=None):
    layer = b"rootfs tar bytes for " + target.encode()
    config = json.dumps({"os": "linux", "architecture": architecture,
                         "rootfs": {"type": "layers", "diff_ids": ["sha256:" + hashlib.sha256(layer).hexdigest()]}}).encode()
    image_id = "sha256:" + hashlib.sha256(config).hexdigest()
    directory = "b" * 64
    source_tag = tag or bundle.source_tag(target, SHA)
    files = {image_id[7:] + ".json": config, directory + "/layer.tar": layer,
             "manifest.json": json.dumps([{"Config": image_id[7:] + ".json", "RepoTags": [source_tag], "Layers": [directory + "/layer.tar"]}]).encode()}
    if extra:
        files.update(extra)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    raw = output.getvalue()
    image = {"image_id": image_id, "source_tag": bundle.source_tag(target, SHA), "archive": target + ".docker.tar",
             "archive_sha256": hashlib.sha256(raw).hexdigest(), "archive_size": len(raw), "platform": "linux/amd64"}
    return raw, image


def zipped(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, json.dumps(value) if isinstance(value, dict) else value)
    return output.getvalue()


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    images = {}
    files = {}
    for target in bundle.TARGETS:
        raw, images[target] = docker_tar(target)
        files["images/" + images[target]["archive"]] = raw
    manifest = {"schema_version": 1, "tested_sha": SHA, "images": images}
    identity = {"schema_version": 1, "base": "b" * 40, "head": SHA, "tested_sha": SHA, "tree": "tree", "contract_digest": "contract"}
    plan = {**identity, "suites": ["production-images"], "changed_paths": ["ingestion/eamlis_monthly/run.py"], "selection_reason": "classified",
            "source": {"run_id": "123", "run_attempt": 1}}
    evidence = {**plan, "source": {"run_id": "123", "run_attempt": 2}, "status": "success", "plan_artifact": "ci-validation-plan-attempt1",
                "suite_artifacts": {"production-images": "ci-result-production-images-attempt1"}}
    result = {**identity, "source": {"run_id": "123", "run_attempt": 1}, "status": "success", "suite": "production-images",
              "tools": proof.expected_tools("production-images"), "commands": [{"argv": ["images"], "exit_code": 0}], "production_images": manifest}
    files.update({"result.json": result, "images/manifest.json": manifest, "logs/untrusted-script.py": b"never extract me"})
    payloads = [zipped({"evidence.json": evidence}), zipped({"plan.json": plan}), zipped(files)]
    artifacts = [{"id": index, "name": name, "expired": False, "workflow_run": {"id": 123},
                  "size_in_bytes": len(raw), "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}
                 for index, (name, raw) in enumerate(zip(["ci-ready-evidence-attempt2", "ci-validation-plan-attempt1", "ci-result-production-images-attempt1"], payloads), 1)]
    repository = {"id": 9, "full_name": REPO}
    workflow = {"id": 7, "path": ".github/workflows/ci.yml"}
    run = {"id": 123, "workflow_id": 7, "repository": repository, "head_repository": repository, "event": "push", "head_branch": "main",
           "head_sha": SHA, "path": workflow["path"], "status": "in_progress"}
    jobs = [{"name": name, "status": "completed", "conclusion": "success"} for name in ("ci-ready", "geospatial-changes", "production-images")]
    api = Mock()
    api.get.side_effect = lambda endpoint: repository if endpoint == f"repos/{REPO}" else workflow if "/workflows/" in endpoint else {**run, "run_attempt": int(endpoint.rsplit("/", 1)[-1])}
    api.pages.side_effect = lambda endpoint, field=None: artifacts if field == "artifacts" else jobs
    api.archive.side_effect = lambda repo, artifact: payloads[artifact - 1] if artifact < 3 else pytest.fail("large ZIP must not be loaded into memory")
    api.archive_to.side_effect = lambda repo, artifact, path: path.write_bytes(payloads[artifact - 1])
    monkeypatch.setattr(proof, "contract_digest", lambda root: "contract")
    calls = []

    def output(args, **kwargs):
        if args[0] == "git":
            return "tree"
        image = next(image for image in images.values() if args[-1] in {image["source_tag"], image["image_id"]})
        return json.dumps([{"Id": image["image_id"], "Architecture": "amd64", "Os": "linux", "RepoTags": [image["source_tag"]]}]).encode()

    monkeypatch.setattr(bundle.subprocess, "check_output", output)
    monkeypatch.setattr(bundle.subprocess, "run", lambda args, **kwargs: calls.append(args))
    return {"api": api, "manifest": manifest, "files": files, "result": result, "payloads": payloads, "artifacts": artifacts,
            "jobs": jobs, "run": run, "calls": calls, "root": tmp_path}


def refresh(fixture):
    fixture["payloads"][2] = zipped(fixture["files"])
    fixture["artifacts"][2].update(size_in_bytes=len(fixture["payloads"][2]), digest="sha256:" + hashlib.sha256(fixture["payloads"][2]).hexdigest())


def download(fixture, target="sea-ice-daily"):
    return consumer.download(fixture["api"], REPO, 123, 2, SHA, target, fixture["root"] / "loaded", fixture["root"])


def test_selective_retry_loads_only_proven_tested_bytes_while_ci_deployments_still_run(handoff):
    result = download(handoff)
    image = handoff["manifest"]["images"]["sea-ice-daily"]
    assert result["image_id"] == image["image_id"]
    assert result["artifact"] == "sea-ice-daily@" + image["image_id"]
    assert Path(result["archive"]).read_bytes() == handoff["files"]["images/sea-ice-daily.docker.tar"]
    assert list((handoff["root"] / "loaded").iterdir()) == [Path(result["archive"])]
    assert handoff["calls"] == [["docker", "image", "load", "--input", result["archive"]]]
    assert handoff["api"].archive.call_count == 2
    assert handoff["api"].archive_to.call_count == 1


@pytest.mark.parametrize("job", ["ci-ready", "geospatial-changes", "production-images"])
@pytest.mark.parametrize("conclusion", ["failure", "skipped", "cancelled"])
def test_failed_or_skipped_source_job_cannot_load(handoff, job, conclusion):
    next(item for item in handoff["jobs"] if item["name"] == job)["conclusion"] = conclusion
    with pytest.raises(DeploymentError):
        download(handoff)
    assert not handoff["calls"]
    handoff["api"].archive_to.assert_not_called()


@pytest.mark.parametrize("field,value", [("workflow_id", 999), ("head_sha", "c" * 40), ("event", "pull_request"), ("head_branch", "feature"), ("head_repository", {"id": 99, "full_name": "evil/fork"})])
def test_wrong_actual_api_source_identity_cannot_download(handoff, field, value):
    handoff["run"][field] = value
    with pytest.raises(DeploymentError):
        download(handoff)
    handoff["api"].archive_to.assert_not_called()


@pytest.mark.parametrize("field,value", [("tested_sha", "c" * 40), ("schema_version", 2), ("status", "failure"), ("suite", "tests"), ("source", {"run_id": "123", "run_attempt": 2}), ("tools", {}), ("commands", []), ("commands", [{"exit_code": 1}])])
def test_result_must_bind_all_validation_identity_and_execution(handoff, field, value):
    handoff["result"][field] = value
    refresh(handoff)
    with pytest.raises(DeploymentError):
        download(handoff)
    assert not handoff["calls"]


@pytest.mark.parametrize("alteration", ["expired", "wrong_run", "digest", "missing_image", "wrong_manifest", "unsafe_path", "extra_image", "oversized"])
def test_artifact_boundary_rejects_invalid_or_incomplete_retention(handoff, alteration):
    artifact = handoff["artifacts"][2]
    if alteration == "expired":
        artifact["expired"] = True
    elif alteration == "wrong_run":
        artifact["workflow_run"]["id"] = 999
    elif alteration == "digest":
        artifact["digest"] = "sha256:" + "0" * 64
    elif alteration == "oversized":
        artifact["size_in_bytes"] = consumer.MAX_ARTIFACT + 1
    else:
        if alteration == "missing_image":
            del handoff["files"]["images/sea-ice-daily.docker.tar"]
        elif alteration == "wrong_manifest":
            handoff["files"]["images/manifest.json"] = {**handoff["manifest"], "tested_sha": "d" * 40}
        elif alteration == "unsafe_path":
            handoff["files"]["../malicious.py"] = b"bad"
        elif alteration == "extra_image":
            handoff["files"]["images/unreviewed.docker.tar"] = b"bad"
        refresh(handoff)
    with pytest.raises((DeploymentError, bundle.ImageError)):
        download(handoff)
    assert not handoff["calls"]


@pytest.mark.parametrize("kind", ["platform", "tag", "extra", "layer", "config"])
def test_consistent_archive_hash_does_not_authorize_changed_runtime_bytes(tmp_path, kind):
    extra = {"injected.sh": b"bad"} if kind == "extra" else None
    raw, image = docker_tar("catalog-viewer", architecture="arm64" if kind == "platform" else "amd64",
                            tag="unreviewed:tag" if kind == "tag" else None, extra=extra)
    if kind in {"layer", "config"}:
        with tarfile.open(fileobj=io.BytesIO(raw)) as saved:
            files = {entry.name: saved.extractfile(entry).read() for entry in saved.getmembers()}
        member = next(name for name in files if name.endswith("layer.tar")) if kind == "layer" else image["image_id"][7:] + ".json"
        files[member] += b" "
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as archive:
            for name, value in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(value)
                archive.addfile(info, io.BytesIO(value))
        raw = output.getvalue()
        image.update(archive_sha256=hashlib.sha256(raw).hexdigest(), archive_size=len(raw))
    path = tmp_path / image["archive"]
    path.write_bytes(raw)
    with pytest.raises(bundle.ImageError):
        bundle.verify_archive(path, image)


def test_real_packing_function_produces_consumer_accepted_manifest_and_bytes(tmp_path, monkeypatch):
    raw, image = docker_tar("eamlis-monthly")
    monkeypatch.setattr(bundle.subprocess, "check_output", lambda *args, **kwargs: image["image_id"])
    def save(args, **kwargs):
        assert args == ["docker", "image", "save", "--output", str(tmp_path / image["archive"]), image["source_tag"]]
        (tmp_path / image["archive"]).write_bytes(raw)
    monkeypatch.setattr(bundle.subprocess, "run", save)
    actual = bundle.pack_image("eamlis-monthly", SHA, image["image_id"], tmp_path)
    assert actual == image
    bundle.validate_manifest({"schema_version": 1, "tested_sha": SHA, "images": {"eamlis-monthly": actual}}, SHA)
    bundle.verify_archive(tmp_path / actual["archive"], actual)


def test_failed_installed_checks_prevent_retaining_untested_image(tmp_path, monkeypatch):
    monkeypatch.setattr(producer.subprocess, "check_output", lambda args, **kwargs: SHA if args[0] == "git" else "sha256:" + "c" * 64)
    def failed(args, **kwargs):
        if args[:2] == ["docker", "run"]:
            raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(producer.subprocess, "run", failed)
    packed = Mock()
    monkeypatch.setattr(producer, "pack_image", packed)
    monkeypatch.setattr(producer.sys, "argv", ["images", "--target", "sea-ice-daily", "--output", str(tmp_path)])
    with pytest.raises(subprocess.CalledProcessError):
        producer.main()
    packed.assert_not_called()
    assert not (tmp_path / "images/manifest.json").exists()


def test_failed_docker_load_is_terminal_without_build_or_push(handoff, monkeypatch):
    def failed(args, **kwargs):
        handoff["calls"].append(args)
        raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(bundle.subprocess, "run", failed)
    with pytest.raises(subprocess.CalledProcessError):
        download(handoff)
    assert len(handoff["calls"]) == 1 and handoff["calls"][0][:3] == ["docker", "image", "load"]


def test_loaded_tag_must_resolve_to_exact_tested_platform_and_config(handoff, monkeypatch):
    original = bundle.subprocess.check_output
    monkeypatch.setattr(bundle.subprocess, "check_output", lambda args, **kwargs: original(args, **kwargs) if args[0] == "git" else json.dumps([{"Id": "sha256:" + "0" * 64, "Architecture": "arm64", "Os": "linux"}]).encode())
    with pytest.raises(bundle.ImageError, match="loaded image differs"):
        download(handoff)
