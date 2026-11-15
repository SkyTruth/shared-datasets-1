import hashlib
import gzip
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from unittest.mock import Mock

import pytest

from scripts import python_sdk_publish as publish, python_sdk_release as policy
from scripts.deployment_revision import DeploymentError
from scripts import ci_preflight
from workflow_helpers import load_workflow

SHA = "a" * 40
VERSION = "0.3.1"


@pytest.mark.parametrize("before,after", [("0.3.0", "0.3.0"), ("0.3.1", "0.3.0")])
def test_package_changes_require_increasing_versions(before, after):
    with pytest.raises(ValueError, match="without a version increase"):
        policy.version_change(before, after, ["api/python/src/skytruth_shared_datasets/catalog.py"])


def test_version_gate_covers_build_inputs_and_packaged_readme():
    for path in ("api/python/README.md", "api/python/build-constraints.txt", policy.VERSION_FILE):
        with pytest.raises(ValueError, match="without a version increase"):
            policy.version_change("0.3.1", "0.3.1", [path])
        assert policy.version_change("0.3.1", "0.3.2", [path])
    assert not policy.version_change("0.3.1", "0.3.1", ["README.md"])


@pytest.fixture
def candidate(tmp_path):
    names = [f"skytruth_shared_datasets-{VERSION}-py3-none-any.whl", f"skytruth_shared_datasets-{VERSION}.tar.gz"]
    hashes = {}
    for name in names:
        (tmp_path / name).write_bytes(name.encode())
        hashes[name] = hashlib.sha256(name.encode()).hexdigest()
    (tmp_path / "provenance.json").write_text(json.dumps({"schema_version": 1, "source_sha": SHA, "version": VERSION,
        "files": hashes, "installed_python": ["3.10.20", "3.12.12"]}))
    (tmp_path / "SHA256SUMS").write_text("".join(f"{value}  {name}\n" for name, value in sorted(hashes.items())))
    return tmp_path


@pytest.mark.parametrize("change", ["source", "bytes", "extra", "proof"])
def test_untrusted_candidates_never_reach_github(candidate, monkeypatch, change):
    value = json.loads((candidate / "provenance.json").read_text())
    if change == "source":
        value["source_sha"] = None
    elif change == "bytes":
        next(candidate.glob("*.whl")).write_bytes(b"corrupt")
    elif change == "extra":
        (candidate / "surprise.txt").write_text("unreviewed")
    else:
        value["installed_python"] = ["3.12.12"]
    (candidate / "provenance.json").write_text(json.dumps(value))
    api = Mock()
    writer = Mock()
    monkeypatch.setattr(publish, "write", writer)
    with pytest.raises(DeploymentError):
        publish.publish(api, candidate, "SkyTruth/shared-datasets-1", SHA, VERSION)
    api.pages.assert_not_called()
    writer.assert_not_called()


def test_existing_tag_cannot_be_moved(candidate, monkeypatch):
    api = Mock()
    api.pages.return_value = [{"ref": "refs/tags/python-sdk-v" + VERSION,
                              "object": {"type": "commit", "sha": "b" * 40}}]
    writer = Mock()
    monkeypatch.setattr(publish, "write", writer)
    with pytest.raises(DeploymentError, match="another commit"):
        publish.publish(api, candidate, "SkyTruth/shared-datasets-1", SHA, VERSION)
    writer.assert_not_called()


def test_release_waits_for_all_verified_uploads(candidate, monkeypatch):
    repository = "SkyTruth/shared-datasets-1"
    hashes = publish.candidate(candidate, SHA, VERSION)
    api = Mock()
    api.pages.side_effect = [[], [], [], [{"name": name, "digest": "sha256:" + digest} for name, digest in hashes.items()]]
    writes = []
    def write(path, payload, **kwargs):
        writes.append((path, payload, kwargs))
        return {"id": 7, "draft": True}
    monkeypatch.setattr(publish, "write", write)
    uploaded = Mock()
    monkeypatch.setattr(publish.subprocess, "run", uploaded)
    publish.publish(api, candidate, repository, SHA, VERSION)
    assert len(writes) == 3
    assert writes[0][1] == {"ref": "refs/tags/python-sdk-v" + VERSION, "sha": SHA}
    assert writes[1][1]["draft"] is True
    assert writes[-1][1] == {"draft": False, "make_latest": "false"}
    assert uploaded.call_count == 4
    assert all("--clobber" not in call.args[0] for call in uploaded.call_args_list)


def test_unconfirmed_upload_keeps_release_draft(candidate, monkeypatch):
    api = Mock()
    api.pages.side_effect = [[], [], [], []]
    writer = Mock(return_value={"id": 7, "draft": True})
    monkeypatch.setattr(publish, "write", writer)
    monkeypatch.setattr(publish.subprocess, "run", Mock())
    with pytest.raises(DeploymentError, match="incomplete or corrupt"):
        publish.publish(api, candidate, "SkyTruth/shared-datasets-1", SHA, VERSION)
    assert writer.call_count == 2


def test_pull_requests_cannot_enter_release_authority(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "SkyTruth/shared-datasets-1")
    monkeypatch.setenv("GITHUB_SHA", SHA)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_REF", "refs/pull/42/merge")
    api = Mock()
    with pytest.raises(DeploymentError, match="main CI completion"):
        publish.authorize(api)
    api.get.assert_not_called()


@pytest.mark.parametrize("fault", [None, "fork", "wrong-workflow", "failed-gate", "new-main"])
def test_release_authority_binds_ci_repository_workflow_and_current_main(tmp_path, monkeypatch, fault):
    repository = "SkyTruth/shared-datasets-1"
    repo = {"id": 9, "full_name": repository}
    environment = {"GITHUB_REPOSITORY": repository, "GITHUB_SHA": SHA, "GITHUB_WORKFLOW_SHA": SHA,
        "GITHUB_EVENT_NAME": "workflow_run", "GITHUB_REF": "refs/heads/main", "GITHUB_RUN_ID": "20",
        "GITHUB_RUN_ATTEMPT": "1", "GITHUB_WORKFLOW_REF": f"{repository}/{publish.WORKFLOW}@refs/heads/main"}
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"workflow_run": {"head_sha": SHA, "id": 10, "run_attempt": 1, "conclusion": "success"}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    current = {"id": 20, "run_attempt": 1, "head_sha": SHA, "head_branch": "main", "event": "workflow_run",
               "path": publish.WORKFLOW, "workflow_id": 22, "repository": repo, "head_repository": repo}
    ci = {**current, "id": 10, "event": "push", "path": ".github/workflows/ci.yml", "workflow_id": 11,
          "conclusion": "success", "status": "completed"}
    if fault == "fork":
        ci["head_repository"] = {"id": 8, "full_name": "foreign/fork"}
    if fault == "wrong-workflow":
        ci["workflow_id"] = 99
    responses = {f"repos/{repository}": repo,
        f"repos/{repository}/actions/runs/20": current,
        f"repos/{repository}/actions/workflows/python-sdk.yml": {"id": 22, "path": publish.WORKFLOW},
        f"repos/{repository}/actions/workflows/ci.yml": {"id": 11, "path": ".github/workflows/ci.yml"},
        f"repos/{repository}/actions/runs/10/attempts/1": ci,
        f"repos/{repository}/git/ref/heads/main": {"object": {"sha": "b" * 40 if fault == "new-main" else SHA}}}
    api = Mock()
    api.get.side_effect = responses.__getitem__
    api.pages.return_value = [{"name": "ci-ready", "status": "completed", "conclusion": "failure" if fault == "failed-gate" else "success"}]
    acceptance = Mock()
    monkeypatch.setattr(publish, "executor_acceptance", acceptance)
    monkeypatch.setattr(publish, "git", lambda *args: SHA)
    if fault:
        with pytest.raises(DeploymentError):
            publish.authorize(api)
        acceptance.assert_not_called()
    else:
        assert publish.authorize(api) == (repository, SHA)
        acceptance.assert_called_once_with(api, repository, SHA)


def test_interrupted_draft_verifies_existing_bytes_and_adds_only_missing(candidate, monkeypatch):
    repository = "SkyTruth/shared-datasets-1"
    hashes = publish.candidate(candidate, SHA, VERSION)
    existing = next(candidate.glob("*.whl"))
    api = Mock()
    api.pages.side_effect = [[{"ref": "refs/tags/python-sdk-v" + VERSION, "object": {"type": "commit", "sha": SHA}}],
        [{"id": 7, "draft": True, "tag_name": "python-sdk-v" + VERSION}],
        [{"id": 8, "name": existing.name}],
        [{"name": name, "digest": "sha256:" + digest} for name, digest in hashes.items()]]
    monkeypatch.setattr(publish.subprocess, "check_output", lambda *args, **kwargs: existing.read_bytes())
    uploader = Mock()
    writer = Mock()
    monkeypatch.setattr(publish.subprocess, "run", uploader)
    monkeypatch.setattr(publish, "write", writer)
    publish.publish(api, candidate, repository, SHA, VERSION)
    assert uploader.call_count == 3
    assert all(str(existing) not in call.args[0] for call in uploader.call_args_list)
    writer.assert_called_once_with(f"repos/{repository}/releases/7", {"draft": False, "make_latest": "false"}, method="PATCH")


def test_failed_publisher_keeps_the_successful_producer_artifact_id():
    jobs = load_workflow(Path(__file__).resolve().parents[1] / ".github/workflows/python-sdk.yml")["jobs"]
    validator = jobs["validate"]
    upload = next(step for step in validator["steps"] if step.get("id") == "candidate")
    assert "upload-artifact@" in upload["uses"]
    assert validator["outputs"]["artifact_id"] == "${{ steps.candidate.outputs.artifact-id }}"
    download = next(step for step in jobs["publish"]["steps"] if "download-artifact@" in step.get("uses", ""))
    assert download["with"]["artifact-ids"] == "${{ needs.validate.outputs.artifact_id }}"
    assert not {"name", "run-id", "repository"} & download["with"].keys()
    assert download["with"]["merge-multiple"] is True
    # Failed-job retry changes run_attempt, but retains successful needs outputs.
    assert "github.run_attempt" not in str(download)
    build = next(step for step in validator["steps"] if "python_sdk_release.py build" in step.get("run", ""))
    assert build["env"]["SOURCE_SHA"] == "${{ steps.release.outputs.source_sha || github.sha }}"
    assert '--source-sha "$SOURCE_SHA"' in build["run"]


def test_plan_outputs_match_the_workflow_consumers(tmp_path, monkeypatch):
    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/python-sdk.yml"
    outputs = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    monkeypatch.setattr(sys, "argv", ["python_sdk_publish.py", "plan"])
    monkeypatch.setattr(publish, "GitHub", Mock)
    monkeypatch.setattr(publish, "authorize", lambda api: ("SkyTruth/shared-datasets-1", "b" * 40))
    monkeypatch.setattr(publish, "version", lambda ref: VERSION)
    monkeypatch.setattr(publish, "release_state", lambda *args: (SHA, True))
    assert publish.main() == 0
    values = dict(line.split("=", 1) for line in outputs.read_text().splitlines())
    assert values == {"source_sha": SHA, "release_needed": "true"}
    import re
    assert set(values) == set(re.findall(r"steps\.release\.outputs\.([a-z_]+)", workflow.read_text()))


def test_source_archive_normalizes_metadata_but_preserves_all_contents(tmp_path):
    files = {"sdk/src/module.py": b"print('sdk')\n", "sdk/README.md": b"Read me\n"}
    archives = []
    for number in (1, 2):
        path = tmp_path / f"build-{number}.tar.gz"
        with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename=f"builder-{number}", mtime=1000 + number) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for name in (list(files) if number == 1 else list(reversed(files))):
                    member = tarfile.TarInfo(name)
                    member.size = len(files[name])
                    member.uid = 500 + number
                    member.uname = f"builder-{number}"
                    member.mtime = 1000 + number
                    member.pax_headers = {"atime": str(2000 + number)}
                    archive.addfile(member, io.BytesIO(files[name]))
        archives.append(path)
    assert archives[0].read_bytes() != archives[1].read_bytes()
    for path in archives:
        policy.normalize_sdist(path, 1000)
        with tarfile.open(path) as archive:
            assert {member.name: archive.extractfile(member).read() for member in archive} == files
    assert archives[0].read_bytes() == archives[1].read_bytes()


@pytest.fixture
def sdk_history(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()
    git("init", "-q")
    package = tmp_path / "api/python"
    package.mkdir(parents=True)
    (package / "pyproject.toml").write_text('[project]\nname="example"\nversion="0.3.1"\n')
    (package / "README.md").write_text("Packaged documentation\n")
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(policy.ROOT / "scripts/python_sdk_release.py", tmp_path / "scripts/python_sdk_release.py")
    def commit(message):
        git("add", ".")
        git("-c", "user.name=SDK test", "-c", "user.email=sdk-test@example.invalid", "commit", "-qm", message)
        return git("rev-parse", "HEAD")
    first = commit("SDK version bump")
    (tmp_path / "unrelated.txt").write_text("Later main merge\n")
    second = commit("Unrelated merge")
    monkeypatch.setattr(policy, "ROOT", tmp_path)
    return tmp_path, first, second, commit


@pytest.mark.parametrize("state,needed", [("absent", True), ("draft", True), ("published", False)])
def test_later_green_main_reconciles_unpublished_version_and_preserves_tag_source(sdk_history, state, needed):
    root, first, second, _ = sdk_history
    api = Mock()
    refs = [] if state == "absent" else [{"ref": "refs/tags/python-sdk-v" + VERSION, "object": {"type": "commit", "sha": first}}]
    releases = [] if state == "absent" else [{"tag_name": "python-sdk-v" + VERSION, "draft": state == "draft"}]
    api.pages.side_effect = [refs, releases]
    source, result = publish.release_state(api, "SkyTruth/shared-datasets-1", second, VERSION)
    assert result is needed
    assert source == (second if state == "absent" else first)
    assert policy.git("diff", "--name-only", first, second) == "unrelated.txt"


def test_old_tag_cannot_cover_a_changed_package_tree(sdk_history):
    root, first, _, commit = sdk_history
    (root / "api/python/README.md").write_text("Changed package\n")
    commit("Package changed")
    with pytest.raises(ValueError, match="differs from the accepted package tree"):
        policy.source_revision(first)


@pytest.mark.parametrize("path,move_out", [("api/python/README.md", False), ("api/python/src/new.py", False),
                                           ("api/python/README.md", True)], ids=["readme-edit", "code-edit", "move-out"])
def test_required_lint_rejects_unbumped_sdk_including_docs_only_changes(sdk_history, path, move_out, monkeypatch):
    root, _, before, commit = sdk_history
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if move_out:
        target.rename(root / "moved-readme.md")
    else:
        target.write_text("# Changed packaged file\n")
    after = commit("Unbumped package change")
    monkeypatch.setattr(ci_preflight, "documentation_only", lambda *args, **kwargs: path.endswith(".md"))
    commands = ci_preflight.suite_commands("lint", root, {"base": before, "tested_sha": after, "changed_paths": [path]}, root / "evidence")
    args, cwd = commands[0]
    assert args == [sys.executable, "scripts/python_sdk_release.py", "check", "--base", before, "--head", after]
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    assert result.returncode != 0
    assert "without a version increase" in result.stderr
