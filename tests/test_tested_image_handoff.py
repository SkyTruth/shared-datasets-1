"""Retained bytes must survive a producer/consumer round trip and reject substitution."""
import copy
import hashlib
import gzip
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
    image = {"config_digest": image_id, "source_tag": bundle.source_tag(target, SHA), "archive": target + ".docker.tar",
             "archive_sha256": hashlib.sha256(raw).hexdigest(), "archive_size": len(raw), "platform": "linux/amd64"}
    return raw, image


def untagged_tar(raw):
    """Docker save by immutable ID drops RepoTags in both image stores."""
    output = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(raw)) as original, tarfile.open(fileobj=output, mode="w") as archive:
        for entry in original.getmembers():
            content = original.extractfile(entry).read()
            if entry.name == "manifest.json":
                manifest = json.loads(content)
                manifest[0]["RepoTags"] = None
                content = json.dumps(manifest).encode()
            info = tarfile.TarInfo(entry.name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def inspected(image, *, daemon_id=None, descriptor=None):
    payload = {"Id": daemon_id or image["config_digest"], "Architecture": "amd64", "Os": "linux", "RepoTags": [image["source_tag"]]}
    if descriptor is not None:
        payload["Descriptor"] = descriptor
    return json.dumps([payload]).encode()


def containerd_tar(raw, *, config_change=None):
    """Real Docker28 store shape: the local ID is a manifest, not its config.

    The graph has actual content-addressed descriptors; image save by immutable
    ID omits tags. Gzip transport can differ across daemons while config/diff_ids
    remain identical.
    """
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        original = json.loads(archive.extractfile("manifest.json").read())[0]
        config = archive.extractfile(original["Config"]).read()
        layer = archive.extractfile(original["Layers"][0]).read()
    if config_change is not None:
        payload = json.loads(config)
        config_change(payload)
        config = json.dumps(payload).encode()
    compressed = gzip.compress(layer, mtime=0)

    def descriptor(content, media_type):
        return {"mediaType": media_type, "digest": "sha256:" + hashlib.sha256(content).hexdigest(), "size": len(content)}

    config_descriptor = descriptor(config, "application/vnd.docker.container.image.v1+json")
    layer_descriptor = descriptor(compressed, "application/vnd.docker.image.rootfs.diff.tar.gzip")
    manifest = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
                           "config": config_descriptor, "layers": [layer_descriptor]}, separators=(",", ":")).encode()
    manifest_descriptor = descriptor(manifest, "application/vnd.docker.distribution.manifest.v2+json")
    manifest_descriptor["platform"] = {"architecture": "amd64", "os": "linux"}
    config_path = "blobs/sha256/" + config_descriptor["digest"][7:]
    layer_path = "blobs/sha256/" + layer_descriptor["digest"][7:]
    files = {config_path: config, layer_path: compressed,
             "blobs/sha256/" + manifest_descriptor["digest"][7:]: manifest,
             "oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
             "index.json": json.dumps({"schemaVersion": 2, "manifests": [manifest_descriptor]}).encode(),
             "manifest.json": json.dumps([{"Config": config_path, "RepoTags": None, "Layers": [layer_path]}]).encode()}
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, content in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
    return output.getvalue(), manifest_descriptor


def zipped(files, compression=zipfile.ZIP_STORED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression, compresslevel=1) as archive:
        for name, value in files.items():
            archive.writestr(name, json.dumps(value) if isinstance(value, dict) else value)
    return output.getvalue()


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    images = {}
    files = {}
    for target in sorted(bundle.TARGETS):
        raw, images[target] = docker_tar(target)
        files["images/" + images[target]["archive"]] = raw
    manifest = {"schema_version": 1, "tested_sha": SHA, "images": images}
    identity = {"schema_version": 1, "base": "b" * 40, "head": SHA, "tested_sha": SHA, "tree": "tree", "contract_digest": "contract"}
    plan = {**identity, "suites": ["production-images"], "changed_paths": ["scripts/ci_contract.py"], "selection_reason": "classified",
            "source": {"run_id": "123", "run_attempt": 1}}
    evidence = {**plan, "source": {"run_id": "123", "run_attempt": 2}, "status": "success", "plan_artifact": "ci-validation-plan-attempt1",
                "suite_artifacts": {"production-images": "ci-result-production-images-attempt1"}}
    result = {**identity, "source": {"run_id": "123", "run_attempt": 1}, "status": "success", "suite": "production-images",
              "tools": proof.expected_tools("production-images"), "commands": [{"argv": ["images"], "exit_code": 0}], "production_images": manifest}
    files.update({"result.json": result, "images/manifest.json": manifest, "logs/untrusted-script.py": b"never extract me"})
    result_files = {name: content for name, content in files.items() if not name.endswith(".docker.tar")}
    payloads = [zipped({"evidence.json": evidence}), zipped({"plan.json": plan}), zipped(result_files)]
    artifact_names = ["ci-ready-evidence-attempt2", "ci-validation-plan-attempt1", "ci-result-production-images-attempt1"]
    image_artifact_ids = {}
    for target, image in images.items():
        payloads.append(zipped({image["archive"]: files["images/" + image["archive"]]}))
        artifact_names.append(f"ci-image-{target}-attempt1")
        image_artifact_ids[target] = len(payloads)
    artifacts = [{"id": index, "name": name, "expired": False, "created_at": "2026-10-14T01:31:53Z", "workflow_run": {"id": 123},
                  "size_in_bytes": len(raw), "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}
                 for index, (name, raw) in enumerate(zip(artifact_names, payloads), 1)]
    repository = {"id": 9, "full_name": REPO}
    workflow = {"id": 7, "path": ".github/workflows/ci.yml"}
    run = {"id": 123, "workflow_id": 7, "repository": repository, "head_repository": repository, "event": "push", "head_branch": "main",
           "head_sha": SHA, "path": workflow["path"], "status": "in_progress"}
    jobs = [{"name": name, "status": "completed", "conclusion": "success", "started_at": "2026-10-14T01:31:38Z", "completed_at": "2026-10-14T01:31:54Z"} for name in ("ci-ready", "geospatial-changes", "production-images")]
    api = Mock()
    api.get.side_effect = lambda endpoint: repository if endpoint == f"repos/{REPO}" else workflow if "/workflows/" in endpoint else {**run, "run_attempt": int(endpoint.rsplit("/", 1)[-1])}
    api.pages.side_effect = lambda endpoint, field=None: artifacts if field == "artifacts" else jobs
    api.archive.side_effect = lambda repo, artifact: payloads[artifact - 1] if artifact <= 3 else pytest.fail("image ZIP must not be loaded into memory")
    api.archive_to.side_effect = lambda repo, artifact, path: path.write_bytes(payloads[artifact - 1])
    monkeypatch.setattr(proof, "contract_digest", lambda root: "contract")
    calls = []

    def output(args, **kwargs):
        if args[0] == "git":
            return "tree"
        image = next(image for image in images.values() if args[-1] in {image["source_tag"], image["config_digest"]})
        return json.dumps([{"Id": image["config_digest"], "Architecture": "amd64", "Os": "linux", "RepoTags": [image["source_tag"]]}]).encode()

    monkeypatch.setattr(bundle.subprocess, "check_output", output)
    monkeypatch.setattr(bundle.subprocess, "run", lambda args, **kwargs: calls.append(args))
    return {"api": api, "manifest": manifest, "files": files, "result": result, "payloads": payloads, "artifacts": artifacts,
            "jobs": jobs, "run": run, "calls": calls, "root": tmp_path, "image_artifact_ids": image_artifact_ids}


def refresh(fixture):
    fixture["payloads"][2] = zipped({name: content for name, content in fixture["files"].items()
                                    if not name.endswith(".docker.tar")})
    fixture["artifacts"][2].update(size_in_bytes=len(fixture["payloads"][2]), digest="sha256:" + hashlib.sha256(fixture["payloads"][2]).hexdigest())
    for target, image in fixture["manifest"]["images"].items():
        member = "images/" + image["archive"]
        refresh_image(fixture, target, {image["archive"]: fixture["files"][member]} if member in fixture["files"] else {})


def refresh_image(fixture, target, members):
    artifact_id = fixture["image_artifact_ids"][target]
    payload = zipped(members)
    fixture["payloads"][artifact_id - 1] = payload
    artifact = next(item for item in fixture["artifacts"] if item["id"] == artifact_id)
    artifact.update(size_in_bytes=len(payload), digest="sha256:" + hashlib.sha256(payload).hexdigest())


def download(fixture, target="sea-ice-daily"):
    return consumer.download(fixture["api"], REPO, 123, 2, SHA, target, fixture["root"] / "loaded", fixture["root"])


@pytest.mark.parametrize("alteration", [None, "wrapper-reference", "wrapper-source", "wrapper-identity", "wrapper-tools", "wrapper-command", "source-rejected"])
def test_main_gate_authorizes_original_pr_image_without_rebinding(handoff, monkeypatch, alteration):
    from types import SimpleNamespace
    from scripts import ci_reuse

    with zipfile.ZipFile(io.BytesIO(handoff["payloads"][1])) as archive:
        source_plan = json.loads(archive.read("plan.json"))
    source_plan["changed_paths"] = ["ingestion/mpatlas_monthly/run.py"]
    handoff["manifest"]["images"] = {"mpatlas-monthly": handoff["manifest"]["images"]["mpatlas-monthly"]}
    refresh(handoff)
    main_sha = "c" * 40
    main_plan = {**source_plan, "head": main_sha, "tested_sha": main_sha, "source": {"run_id": "456", "run_attempt": 2}}
    reference = {"pr_number": 42, "run_id": 123, "run_attempt": 1}
    wrapper = {**main_plan, "suite": "production-images", "status": "success", "tools": proof.expected_tools("production-images"),
               "commands": [{"argv": ["reuse"], "exit_code": 0}], "reused_from": reference}
    if alteration == "wrapper-reference":
        wrapper["reused_from"] = {**reference, "pr_number": 43}
    elif alteration == "wrapper-source":
        wrapper["source"] = {"run_id": "123", "run_attempt": 2}
    elif alteration == "wrapper-identity":
        wrapper["tested_sha"] = SHA
    elif alteration == "wrapper-tools":
        wrapper["tools"] = {}
    elif alteration == "wrapper-command":
        wrapper["commands"][0]["exit_code"] = 1
    payload = zipped({"result.json": wrapper})
    artifact = {"id": 99, "name": "ci-result-production-images-attempt2", "expired": False, "workflow_run": {"id": 456},
                "digest": "sha256:" + hashlib.sha256(payload).hexdigest()}
    original_archive = handoff["api"].archive.side_effect
    handoff["api"].archive.side_effect = lambda repo, identity: payload if identity == 99 else original_archive(repo, identity)
    evidence = {"source": main_plan["source"], "suite_artifacts": {"production-images": artifact["name"]},
                "reused_suites": {"production-images": reference}}
    verified = Mock(return_value=({**handoff["run"], "id": 456}, [artifact], evidence, main_plan))
    monkeypatch.setattr(proof, "verified_plan", verified)
    producing = Mock()
    monkeypatch.setattr(proof, "producing_job", producing)
    source = SimpleNamespace(run=handoff["run"], artifacts=handoff["artifacts"], plan=source_plan,
                             suite_artifact="ci-result-production-images-attempt1", suite_attempt=1)
    verifier = Mock(side_effect=ValueError("source rejected")) if alteration == "source-rejected" else Mock(return_value=source)
    monkeypatch.setattr(ci_reuse, "verify_source", verifier)
    if alteration is not None:
        with pytest.raises((DeploymentError, ValueError), match="wrapper|source rejected"):
            consumer.download(handoff["api"], REPO, 456, 2, main_sha, "mpatlas-monthly", handoff["root"] / "loaded", handoff["root"])
        assert not handoff["calls"]
        return
    result = consumer.download(handoff["api"], REPO, 456, 2, main_sha, "mpatlas-monthly", handoff["root"] / "loaded", handoff["root"])
    assert result["tested_sha"] == SHA and result["authorized_sha"] == main_sha
    assert result["source_tag"] == bundle.source_tag("mpatlas-monthly", SHA)
    assert Path(result["archive"]).read_bytes() == handoff["files"]["images/mpatlas-monthly.docker.tar"]
    producing.assert_called_once_with(handoff["api"], REPO, 456, 2, main_sha, "production-images", 7, 9)
    verifier.assert_called_once_with(handoff["api"], REPO, main_plan, handoff["root"], "production-images", reference,
                                     main_source=wrapper["source"], require_latest=False)


@pytest.mark.parametrize("target", sorted(bundle.TARGETS))
@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_selective_retry_loads_only_proven_tested_bytes_while_ci_deployments_still_run(handoff, target, compression):
    image = handoff["manifest"]["images"][target]
    artifact_id = handoff["image_artifact_ids"][target]
    payload = zipped({image["archive"]: handoff["files"]["images/" + image["archive"]]}, compression)
    handoff["payloads"][artifact_id - 1] = payload
    next(artifact for artifact in handoff["artifacts"] if artifact["id"] == artifact_id).update(
        size_in_bytes=len(payload), digest="sha256:" + hashlib.sha256(payload).hexdigest())
    result = download(handoff, target)
    image = handoff["manifest"]["images"][target]
    assert result["image_id"] == image["config_digest"]
    assert result["artifact"] == target + "@" + image["config_digest"]
    assert Path(result["archive"]).read_bytes() == handoff["files"]["images/" + image["archive"]]
    assert list((handoff["root"] / "loaded").iterdir()) == [Path(result["archive"])]
    assert handoff["calls"] == [["docker", "image", "load", "--input", result["archive"]]]
    assert handoff["api"].archive.call_count == 3
    handoff["api"].archive_to.assert_called_once()
    assert handoff["api"].archive_to.call_args.args[:2] == (REPO, handoff["image_artifact_ids"][target])
    with zipfile.ZipFile(io.BytesIO(handoff["payloads"][2])) as diagnostic:
        assert not any(name.endswith(".docker.tar") for name in diagnostic.namelist())


@pytest.mark.parametrize("trigger_attempt", [2, 3])
def test_downstream_only_rerun_loads_images_from_the_reused_original_gate(handoff, trigger_attempt):
    with zipfile.ZipFile(io.BytesIO(handoff["payloads"][0])) as archive:
        evidence = json.loads(archive.read("evidence.json"))
    evidence["source"]["run_attempt"] = 1
    handoff["payloads"][0] = zipped({"evidence.json": evidence})
    handoff["artifacts"][0].update(name="ci-ready-evidence-attempt1", digest="sha256:" + hashlib.sha256(handoff["payloads"][0]).hexdigest())
    result = consumer.download(handoff["api"], REPO, 123, trigger_attempt, SHA, "sea-ice-daily", handoff["root"] / "loaded", handoff["root"])
    assert Path(result["archive"]).read_bytes() == handoff["files"]["images/sea-ice-daily.docker.tar"]
    assert handoff["api"].archive_to.call_args.args[1] == handoff["image_artifact_ids"]["sea-ice-daily"]


def test_failed_selected_producer_does_not_fall_back_to_older_successful_image_artifacts(handoff):
    with zipfile.ZipFile(io.BytesIO(handoff["payloads"][0])) as archive:
        evidence = json.loads(archive.read("evidence.json"))
    evidence["suite_artifacts"]["production-images"] = "ci-result-production-images-attempt2"
    handoff["payloads"][0] = zipped({"evidence.json": evidence})
    handoff["artifacts"][0].update(digest="sha256:" + hashlib.sha256(handoff["payloads"][0]).hexdigest())
    original_pages = handoff["api"].pages.side_effect

    def pages(endpoint, field=None):
        found = original_pages(endpoint, field=field)
        if "/attempts/2/jobs?" in endpoint:
            return [{**job, "conclusion": "failure" if job["name"] == "production-images" else job["conclusion"]}
                    for job in found]
        return found

    handoff["api"].pages.side_effect = pages
    with pytest.raises(DeploymentError, match="producing job did not complete successfully"):
        download(handoff)
    handoff["api"].archive_to.assert_not_called()
    assert not handoff["calls"]


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


@pytest.mark.parametrize("alteration", ["expired", "wrong_run", "digest", "missing_image", "wrong_manifest", "unsafe_path", "oversized"])
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
        refresh(handoff)
    with pytest.raises((DeploymentError, bundle.ImageError)):
        download(handoff)
    assert not handoff["calls"]


@pytest.mark.parametrize("alteration", ["missing", "duplicate", "expired", "wrong-run", "wrong-target", "wrong-attempt", "digest", "oversized"])
def test_selected_image_requires_one_exact_producer_attempt_artifact(handoff, alteration):
    artifact_id = handoff["image_artifact_ids"]["sea-ice-daily"]
    artifact = next(item for item in handoff["artifacts"] if item["id"] == artifact_id)
    if alteration == "missing":
        handoff["artifacts"].remove(artifact)
    elif alteration == "duplicate":
        handoff["artifacts"].append({**copy.deepcopy(artifact), "id": 999})
    elif alteration == "expired":
        artifact["expired"] = True
    elif alteration == "wrong-run":
        artifact["workflow_run"]["id"] = 999
    elif alteration == "wrong-target":
        artifact["name"] = "ci-image-eamlis-monthly-attempt1"
    elif alteration == "wrong-attempt":
        artifact["name"] = "ci-image-sea-ice-daily-attempt2"
    elif alteration == "digest":
        artifact["digest"] = "sha256:" + "0" * 64
    else:
        artifact["size_in_bytes"] = consumer.MAX_ARTIFACT + 1
    with pytest.raises(DeploymentError):
        download(handoff)
    assert not handoff["calls"]
    if alteration != "digest":
        handoff["api"].archive_to.assert_not_called()


@pytest.mark.parametrize("alteration", ["empty", "other-target", "nested", "unsafe", "extra", "duplicate", "invalid-zip"])
def test_image_zip_contains_only_the_selected_tar_at_its_root(handoff, alteration):
    target = "sea-ice-daily"
    name = target + ".docker.tar"
    raw = handoff["files"]["images/" + name]
    members = {name: raw}
    if alteration == "empty":
        members = {}
    elif alteration == "other-target":
        members = {"eamlis-monthly.docker.tar": handoff["files"]["images/eamlis-monthly.docker.tar"]}
    elif alteration == "nested":
        members = {"images/" + name: raw}
    elif alteration == "unsafe":
        members = {"../" + name: raw}
    elif alteration == "extra":
        members["untrusted.py"] = b"never execute or extract"
    refresh_image(handoff, target, members)
    if alteration in {"duplicate", "invalid-zip"}:
        artifact_id = handoff["image_artifact_ids"][target]
        if alteration == "duplicate":
            output = io.BytesIO()
            with pytest.warns(UserWarning, match="Duplicate name"):
                with zipfile.ZipFile(output, "w") as archive:
                    archive.writestr(name, raw)
                    archive.writestr(name, raw)
            payload = output.getvalue()
        else:
            payload = b"not a ZIP archive"
        handoff["payloads"][artifact_id - 1] = payload
        artifact = next(item for item in handoff["artifacts"] if item["id"] == artifact_id)
        artifact.update(size_in_bytes=len(payload), digest="sha256:" + hashlib.sha256(payload).hexdigest())
    with pytest.raises((DeploymentError, zipfile.BadZipFile)):
        download(handoff)
    assert not handoff["calls"]
    assert not (handoff["root"] / name).exists()


@pytest.mark.parametrize("kind", ["archive-hash", "layer-hash"])
def test_split_image_transport_still_verifies_tar_and_runtime_hashes(handoff, kind):
    target = "sea-ice-daily"
    image = handoff["manifest"]["images"][target]
    member = "images/" + image["archive"]
    raw = handoff["files"][member].replace(b"rootfs tar bytes", b"changed tar XYZ ")
    handoff["files"][member] = raw
    if kind == "layer-hash":
        image.update(archive_sha256=hashlib.sha256(raw).hexdigest(), archive_size=len(raw))
    refresh(handoff)
    with pytest.raises(bundle.ImageError):
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
        member = next(name for name in files if name.endswith("layer.tar")) if kind == "layer" else image["config_digest"][7:] + ".json"
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
    monkeypatch.setattr(bundle.subprocess, "check_output", lambda *args, **kwargs: inspected(image))
    def save(args, **kwargs):
        assert args[:4] == ["docker", "image", "save", "--output"] and args[-1] == image["config_digest"]
        assert Path(args[4]).parent.parent == tmp_path
        Path(args[4]).write_bytes(untagged_tar(raw))
    monkeypatch.setattr(bundle.subprocess, "run", save)
    actual = bundle.pack_image("eamlis-monthly", SHA, image["config_digest"], tmp_path)
    assert actual["config_digest"] == image["config_digest"] and actual["source_tag"] == image["source_tag"]
    bundle.validate_manifest({"schema_version": 1, "tested_sha": SHA, "images": {"eamlis-monthly": actual}}, SHA)
    bundle.verify_archive(tmp_path / actual["archive"], actual)


@pytest.mark.parametrize("paths,target", [(None, "sea-ice-daily"), (["ingestion/eamlis_monthly/run.py"], "eamlis-monthly"), (["ingestion/wdpa_monthly/run.py"], None)])
def test_actual_ci_result_and_image_producers_feed_the_authenticated_consumer(handoff, monkeypatch, paths, target):
    from scripts import ci_preflight

    output = handoff["root"] / "producer"
    with zipfile.ZipFile(io.BytesIO(handoff["payloads"][1])) as archive:
        plan = json.loads(archive.read("plan.json"))
    plan["changed_paths"] = paths
    with zipfile.ZipFile(io.BytesIO(handoff["payloads"][0])) as archive:
        evidence = json.loads(archive.read("evidence.json"))
    evidence["changed_paths"] = paths
    handoff["payloads"][0] = zipped({"evidence.json": evidence})
    handoff["payloads"][1] = zipped({"plan.json": plan})
    for index in (0, 1):
        payload = handoff["payloads"][index]
        handoff["artifacts"][index].update(size_in_bytes=len(payload), digest="sha256:" + hashlib.sha256(payload).hexdigest())
    expected = set(ci_preflight.select_production_images(paths))
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setattr(ci_preflight, "validate_checkout", lambda *_: None)
    monkeypatch.setattr(ci_preflight, "probe_tools", lambda *_: proof.expected_tools("production-images"))
    original_output = bundle.subprocess.check_output

    def inspect(args, **kwargs):
        if args == ["git", "rev-parse", "HEAD"]:
            return SHA
        if args[:2] == ["docker", "version"]:
            return "{}"
        if "{{json .Config.Env}}" in args:
            return "[]"
        if args[:4] == ["docker", "image", "inspect", "--format"]:
            target = args[-1].partition("/")[2].partition(":")[0]
            return handoff["manifest"]["images"].get(target, {"config_digest": "sha256:" + "c" * 64})["config_digest"]
        return original_output(args, **kwargs)

    built_targets = []

    def run(args, **kwargs):
        if "scripts/production_image_contracts.py" in args:
            with monkeypatch.context() as context:
                context.setattr(producer.sys, "argv", args[args.index("scripts/production_image_contracts.py"):])
                producer.main()
        elif args[:3] == ["docker", "image", "save"]:
            target = next(target for target, image in handoff["manifest"]["images"].items() if image["config_digest"] == args[-1])
            Path(args[4]).write_bytes(untagged_tar(handoff["files"]["images/" + target + ".docker.tar"]))
        elif args[:2] == ["docker", "build"]:
            built_targets.append(args[args.index("-t") + 1].partition("/")[2].partition(":")[0])
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(bundle.subprocess, "check_output", inspect)
    monkeypatch.setattr(bundle.subprocess, "run", run)
    actual = ci_preflight.run_suite(handoff["root"], plan, "production-images", output)
    assert actual["status"] == "success" and set(actual["production_images"]["images"]) == expected & bundle.TARGETS
    assert set(built_targets) == expected
    handoff["files"] = {path.relative_to(output).as_posix(): path.read_bytes() for path in output.rglob("*") if path.is_file()}
    handoff["manifest"] = actual["production_images"]
    refresh(handoff)
    if target is not None:
        loaded = download(handoff, target)
        assert loaded["image_id"] == actual["production_images"]["images"][target]["config_digest"]
    if expected != set(bundle.BUILD_TARGETS):
        with pytest.raises(DeploymentError, match="requested target"):
            download(handoff, "monitoring-slack")


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


def test_failed_ci_image_roundtrip_cannot_produce_a_deployable_manifest(tmp_path, monkeypatch):
    _, image = docker_tar("sea-ice-daily")
    monkeypatch.setattr(producer.subprocess, "check_output", lambda args, **kwargs: SHA if args[0] == "git" else image["config_digest"])
    monkeypatch.setattr(producer.subprocess, "run", Mock())
    monkeypatch.setattr(producer, "pack_image", Mock(return_value=image))
    loader = Mock(side_effect=bundle.ImageError("CI daemon cannot load the retained image"))
    monkeypatch.setattr(producer, "load_image", loader)
    monkeypatch.setattr(producer.sys, "argv", ["images", "--target", "sea-ice-daily", "--output", str(tmp_path)])
    with pytest.raises(bundle.ImageError, match="CI daemon cannot load"):
        producer.main()
    loader.assert_called_once_with(tmp_path / "images" / image["archive"], image)
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
    with pytest.raises(bundle.ImageError, match="local image identity or platform mismatch"):
        download(handoff)


@pytest.mark.parametrize("format", ["legacy", "docker28", "containerd-gzip"])
def test_exporter_normalization_preserves_tested_config_and_rootfs(tmp_path, format):
    raw, image = docker_tar("catalog-viewer")
    with tarfile.open(fileobj=io.BytesIO(raw)) as original:
        config = original.extractfile(image["config_digest"][7:] + ".json").read()
        layer = original.extractfile("b" * 64 + "/layer.tar").read()
    if format != "legacy":
        stored_layer = gzip.compress(layer) if format == "containerd-gzip" else layer
        layer_digest = hashlib.sha256(stored_layer).hexdigest()
        config_path = "blobs/sha256/" + image["config_digest"][7:]
        layer_path = "blobs/sha256/" + layer_digest
        modern = {config_path: config, layer_path: stored_layer,
                  "manifest.json": json.dumps([{"Config": config_path, "RepoTags": [image["source_tag"]], "Layers": [layer_path],
                                               "LayerSources": {"sha256:" + hashlib.sha256(layer).hexdigest(): {"digest": "sha256:" + layer_digest, "size": len(stored_layer)}}}]).encode(),
                  "oci-layout": b'{"imageLayoutVersion":"1.0.0"}', "index.json": b'{"schemaVersion":2,"manifests":[]}',
                  "blobs/sha256/" + "f" * 64: b'{"legacy-config":"discarded"}'}
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for name, value in modern.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(value)
                archive.addfile(entry, io.BytesIO(value))
        raw = buffer.getvalue()
    source = tmp_path / "raw.tar"
    destination = tmp_path / "canonical.tar"
    source.write_bytes(raw)
    bundle.canonicalize_archive(source, destination, image["config_digest"], image["source_tag"])
    image.update(archive_size=destination.stat().st_size, archive_sha256=bundle.hash_file(destination))
    bundle.verify_archive(destination, image)
    with tarfile.open(destination) as canonical:
        manifest = json.loads(canonical.extractfile("manifest.json").read())[0]
        assert canonical.extractfile(manifest["Config"]).read() == config
        assert canonical.extractfile(manifest["Layers"][0]).read() == layer
        assert set(canonical.getnames()) == {"manifest.json", manifest["Config"], manifest["Layers"][0]}


@pytest.mark.parametrize("change", ["tag", "platform", "layer"])
def test_normalization_rejects_substitution_before_retaining(tmp_path, change):
    raw, image = docker_tar("catalog-viewer", architecture="arm64" if change == "platform" else "amd64",
                            tag="extra:tag" if change == "tag" else None)
    if change == "layer":
        raw = raw.replace(b"rootfs tar bytes", b"changed tar XYZ ")
    source = tmp_path / "raw.tar"
    source.write_bytes(raw)
    expected_id = docker_tar("catalog-viewer")[1]["config_digest"]
    with pytest.raises(bundle.ImageError):
        bundle.canonicalize_archive(source, tmp_path / "canonical.tar", expected_id, image["source_tag"])


def test_containerd_producer_retains_config_digest_from_exact_smoked_daemon_target(tmp_path, monkeypatch):
    raw, image = docker_tar("catalog-viewer")
    exported, descriptor = containerd_tar(raw)
    daemon_id = descriptor["digest"]
    assert daemon_id != image["config_digest"]
    inspections = []
    monkeypatch.setattr(bundle.subprocess, "check_output", lambda args, **kwargs: inspections.append(args[-1]) or inspected(image, daemon_id=daemon_id, descriptor=descriptor))
    commands = []
    def save(args, **kwargs):
        commands.append(args)
        assert args[-1] == daemon_id
        Path(args[4]).write_bytes(exported)
    monkeypatch.setattr(bundle.subprocess, "run", save)
    retained = bundle.pack_image("catalog-viewer", SHA, daemon_id, tmp_path)
    assert retained["config_digest"] == image["config_digest"] and "image_id" not in retained
    assert inspections == [image["source_tag"], image["source_tag"]]
    assert len(commands) == 1 and commands[0][:4] == ["docker", "image", "save", "--output"]
    bundle.verify_archive(tmp_path / retained["archive"], retained)


@pytest.mark.parametrize("when", ["before", "during"])
def test_containerd_producer_rejects_tag_reassignment_around_immutable_retention(tmp_path, monkeypatch, when):
    raw, image = docker_tar("catalog-viewer")
    exported, descriptor = containerd_tar(raw)
    daemon_id = descriptor["digest"]
    calls = []
    count = 0
    def output(args, **kwargs):
        nonlocal count
        count += 1
        observed = "sha256:" + "1" * 64 if when == "before" or count > 1 else daemon_id
        return inspected(image, daemon_id=observed, descriptor={**descriptor, "digest": observed})
    def save(args, **kwargs):
        calls.append(args)
        assert args[-1] == daemon_id
        Path(args[4]).write_bytes(exported)
    monkeypatch.setattr(bundle.subprocess, "check_output", output)
    monkeypatch.setattr(bundle.subprocess, "run", save)
    with pytest.raises(bundle.ImageError, match="tested image tag changed"):
        bundle.pack_image("catalog-viewer", SHA, daemon_id, tmp_path)
    assert len(calls) == (1 if when == "during" else 0)


def test_containerd_consumer_proves_config_and_rootfs_then_returns_distinct_local_id(handoff, monkeypatch):
    target = "sea-ice-daily"
    image = handoff["manifest"]["images"][target]
    exported, descriptor = containerd_tar(handoff["files"]["images/" + image["archive"]])
    daemon_id = descriptor["digest"]
    original_output = bundle.subprocess.check_output
    def output(args, **kwargs):
        if args[0] == "git":
            return original_output(args, **kwargs)
        assert args[-1] in {image["source_tag"], daemon_id}
        return inspected(image, daemon_id=daemon_id, descriptor=descriptor)
    def run(args, **kwargs):
        handoff["calls"].append(args)
        if args[:3] == ["docker", "image", "save"]:
            assert args[-1] == daemon_id
            Path(args[4]).write_bytes(exported)
    monkeypatch.setattr(bundle.subprocess, "check_output", output)
    monkeypatch.setattr(bundle.subprocess, "run", run)
    result = download(handoff, target)
    assert result["image_id"] == daemon_id != image["config_digest"]
    assert result["config_digest"] == image["config_digest"]
    assert result["artifact"] == target + "@" + image["config_digest"]
    assert [command[:3] for command in handoff["calls"]] == [["docker", "image", "load"], ["docker", "image", "save"]]
    assert list((handoff["root"] / "loaded").iterdir()) == [Path(result["archive"])]


@pytest.mark.parametrize("change", ["config", "layer", "tag", "index", "descriptor", "retag"])
def test_containerd_post_load_substitution_cannot_reach_push(handoff, monkeypatch, change):
    image = handoff["manifest"]["images"]["sea-ice-daily"]
    exported, descriptor = containerd_tar(handoff["files"]["images/" + image["archive"]],
                                         config_change=(lambda config: config.update(config={"Cmd": ["unreviewed"]})) if change == "config" else None)
    if change in {"layer", "tag"}:
        # Alter runtime bytes/metadata while keeping the TAR container intact.
        buffer = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(exported)) as original, tarfile.open(fileobj=buffer, mode="w") as archive:
            for entry in original.getmembers():
                content = original.extractfile(entry).read()
                if change == "layer" and content.startswith(b"\x1f\x8b"):
                    content = gzip.compress(b"unreviewed rootfs", mtime=0)
                if change == "tag" and entry.name == "manifest.json":
                    payload = json.loads(content)
                    payload[0]["RepoTags"] = ["unreviewed:tag"]
                    content = json.dumps(payload).encode()
                info = tarfile.TarInfo(entry.name)
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
        exported = buffer.getvalue()
    daemon_id = descriptor["digest"]
    descriptor = dict(descriptor)
    if change == "index":
        descriptor["mediaType"] = "application/vnd.oci.image.index.v1+json"
    if change == "descriptor":
        descriptor["digest"] = "sha256:" + "0" * 64
    checks = []
    original_output = bundle.subprocess.check_output
    def output(args, **kwargs):
        if args[0] == "git":
            return original_output(args, **kwargs)
        checks.append(args[-1])
        actual_id = "sha256:" + "1" * 64 if change == "retag" and len(checks) > 1 else daemon_id
        actual_descriptor = {**descriptor, "digest": actual_id} if change == "retag" else descriptor
        return inspected(image, daemon_id=actual_id, descriptor=actual_descriptor)
    def run(args, **kwargs):
        handoff["calls"].append(args)
        if args[:3] == ["docker", "image", "save"]:
            Path(args[4]).write_bytes(exported)
    monkeypatch.setattr(bundle.subprocess, "check_output", output)
    monkeypatch.setattr(bundle.subprocess, "run", run)
    with pytest.raises((bundle.ImageError, OSError, tarfile.TarError)):
        download(handoff)
    assert all(command[:2] != ["docker", "push"] for command in handoff["calls"])


def test_streamed_download_bounds_bytes_and_stops_only_its_process(tmp_path, monkeypatch):
    process = Mock(stdout=io.BytesIO(b"more than allowed"))
    process.__enter__ = Mock(return_value=process)
    process.__exit__ = Mock(return_value=False)
    process.poll.return_value = None
    monkeypatch.setattr(consumer.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(consumer, "MAX_ARTIFACT", 4)
    with pytest.raises(DeploymentError, match="download exceeds"):
        consumer.GitHub().archive_to(REPO, 3, tmp_path / "archive.zip")
    process.kill.assert_called_once()
    process.wait.assert_called_once()


def test_failed_github_stream_command_remains_a_failure(tmp_path, monkeypatch):
    process = Mock(stdout=io.BytesIO(b"partial bytes"), returncode=1)
    process.__enter__ = Mock(return_value=process)
    process.__exit__ = Mock(return_value=False)
    process.wait.return_value = 1
    process.poll.return_value = 1
    commands = []
    def popen(command, **kwargs):
        commands.append(command)
        return process
    monkeypatch.setattr(consumer.subprocess, "Popen", popen)
    with pytest.raises(subprocess.CalledProcessError):
        consumer.GitHub().archive_to(REPO, 3, tmp_path / "archive.zip")
    assert commands == [["gh", "api", f"repos/{REPO}/actions/artifacts/3/zip"]]
    process.kill.assert_not_called()
