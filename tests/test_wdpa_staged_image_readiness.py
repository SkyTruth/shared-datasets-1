"""Retained producer identity and availability precede isolated deployment."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
from unittest import mock
import urllib.error

import pytest

from scripts import ci_preflight, wdpa_processing_gate as gate
from scripts import wdpa_staged_image_readiness as ready
from workflow_helpers import load_workflow, workflow_steps_by_name

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)
HEAD = "1d67e856dda11e936abad95d6a92c5e421660857"
SOURCE = "1e7d60b2e1cbcf171894e072e0b72b92ee03933d34b5a715e6951485565b5c23"


@pytest.fixture
def evidence():
    return json.loads((ROOT / "catalog/wdpa-staged-validation.json").read_text())


@pytest.fixture
def artifact(evidence):
    image = evidence["tested_image_artifact"]
    return {"id": image["artifact_id"], "name": ready.ARTIFACT_NAME,
            "digest": image["artifact_sha256"], "size_in_bytes": 464284424,
            "expired": False, "expires_at": "2026-10-10T04:03:21Z",
            "workflow_run": {"id": 37095105175, "head_sha": HEAD,
                             "repository_id": ready.REPOSITORY_ID, "head_repository_id": ready.REPOSITORY_ID}}


@pytest.fixture
def original(monkeypatch):
    verify = mock.Mock(return_value=SOURCE)
    monkeypatch.setattr(ready, "original_source_digest", verify)
    return verify


def test_exact_reviewed_retained_artifact_is_ready(evidence, artifact, original):
    api = mock.Mock()
    api.get.return_value = artifact
    result = ready.verify(api, evidence, now=NOW)
    assert result == {"artifact_id": "11263851699", "run_id": "37095105175",
                      "config_digest": evidence["compatibility_sample"]["image_digest"],
                      "source_digest": SOURCE, "expires_at": artifact["expires_at"]}
    api.get.assert_called_once_with("/repos/SkyTruth/shared-datasets-1/actions/artifacts/11263851699")
    original.assert_called_once_with(HEAD)


@pytest.mark.parametrize("field,value", [
    ("id", 11263851698), ("name", "another-image"), ("digest", "sha256:" + "f" * 64),
    ("size_in_bytes", 0), ("size_in_bytes", True), ("size_in_bytes", ready.MAX_ARTIFACT_BYTES + 1),
    ("expired", True), ("expired", None),
])
def test_foreign_changed_unavailable_or_oversized_artifact_fails(evidence, artifact, original, field, value):
    artifact[field] = value
    with pytest.raises(ready.ReadinessError):
        ready.verify(mock.Mock(get=mock.Mock(return_value=artifact)), evidence, now=NOW)


@pytest.mark.parametrize("field,value", [
    ("id", 37095105174), ("head_sha", "f" * 40),
    ("repository_id", 1), ("head_repository_id", 1),
])
def test_original_run_head_and_both_repository_ids_are_binding(evidence, artifact, original, field, value):
    artifact["workflow_run"][field] = value
    with pytest.raises(ready.ReadinessError, match="provenance"):
        ready.verify(mock.Mock(get=mock.Mock(return_value=artifact)), evidence, now=NOW)


@pytest.mark.parametrize("hours,passes", [(-1, False), (0, False), (23, False), (24, False), (25, True)])
def test_expiry_must_exceed_twenty_four_hour_queue_download_margin(evidence, artifact, original, hours, passes):
    artifact["expires_at"] = (NOW + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    api = mock.Mock(get=mock.Mock(return_value=artifact))
    if passes:
        ready.verify(api, evidence, now=NOW)
    else:
        with pytest.raises(ready.ReadinessError, match="24-hour"):
            ready.verify(api, evidence, now=NOW)


@pytest.mark.parametrize("defect", ["small", "sample", "source", "image", "config-head", "config-bytes", "metadata"])
def test_reviewed_checks_and_config_original_producer_are_binding(tmp_path, evidence, artifact, original, defect):
    for name in ("small_fixture", "compatibility_sample"):
        path = Path(evidence[name]["image_metadata_path"])
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes((ROOT / path).read_bytes())
    config_path = Path(evidence["tested_image_artifact"]["configuration_blob_path"])
    (tmp_path / config_path).write_bytes((ROOT / config_path).read_bytes())
    if defect == "small":
        evidence["small_fixture"]["state"] = "failed"
    elif defect == "sample":
        evidence["compatibility_sample"]["compatibility_verified"] = False
    elif defect == "source":
        original.return_value = "f" * 64
    elif defect == "image":
        evidence["small_fixture"]["image_digest"] = "sha256:" + "f" * 64
    elif defect == "config-head":
        config = json.loads((tmp_path / config_path).read_bytes())
        config["config"]["Env"] = ["SHARED_DATASETS_EXECUTOR_SHA=" + "f" * 40]
        raw = json.dumps(config).encode()
        (tmp_path / config_path).write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        evidence["tested_image_artifact"].update(configuration_blob_sha256=digest, configuration_digest="sha256:" + digest)
        for name in ("small_fixture", "compatibility_sample"):
            evidence[name]["image_digest"] = "sha256:" + digest
            metadata = json.loads((tmp_path / evidence[name]["image_metadata_path"]).read_bytes())
            metadata["containerimage.config.digest"] = "sha256:" + digest
            raw_metadata = json.dumps(metadata).encode()
            (tmp_path / evidence[name]["image_metadata_path"]).write_bytes(raw_metadata)
            evidence[name]["image_metadata_sha256"] = hashlib.sha256(raw_metadata).hexdigest()
    elif defect == "config-bytes":
        (tmp_path / config_path).write_bytes(b'{}')
    elif defect == "metadata":
        evidence["compatibility_sample"]["image_metadata_sha256"] = "f" * 64
    with pytest.raises(ready.ReadinessError):
        ready.verify(mock.Mock(get=mock.Mock(return_value=artifact)), evidence, root=tmp_path, now=NOW)


def source_archive(paths, *, revision=HEAD, duplicate=False, source_link=None, trailing=False):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:") as archive:
        for path, data in paths.items():
            member = tarfile.TarInfo(f"shared-datasets-1-{revision}/{path}")
            member.size = len(data)
            if path == source_link:
                member.type = tarfile.SYMTYPE
                member.linkname = "/untrusted/producer"
                member.size = 0
            archive.addfile(member, io.BytesIO(data) if member.isfile() else None)
            if duplicate and path == "pyproject.toml":
                archive.addfile(member, io.BytesIO(data))
    raw = buffer.getvalue() + (b'unenumerated' if trailing else b'')
    return io.BytesIO(gzip.compress(raw))


@pytest.fixture
def original_paths():
    paths = {path: path.encode() for path in gate.source_paths([])}
    paths.update({"ingestion/common/original_only.py": b'original common',
                  "ingestion/wdpa_monthly/original_only.py": b'original producer',
                  "catalog/feature-identity-resolutions/wdpa-original.json": b'original identity',
                  "ingestion/common/nested/ignored.py": b'not a direct original source',
                  "docs/unrelated.md": b'not a source input'})
    return paths


def test_snapshot_hashes_only_the_original_inventory_and_exact_source_bytes(original_paths):
    selected = sorted(path for path in original_paths if path not in {
        "ingestion/common/nested/ignored.py", "docs/unrelated.md"})
    expected = hashlib.sha256(b''.join(path.encode() + b'\0' + original_paths[path] + b'\0' for path in selected)).hexdigest()
    assert ready.source_snapshot(source_archive(original_paths), HEAD) == expected
    changed = deepcopy(original_paths)
    changed["ingestion/common/original_only.py"] += b'changed'
    assert ready.source_snapshot(source_archive(changed), HEAD) != expected


@pytest.mark.parametrize("defect", ["revision", "missing", "duplicate", "alias", "link", "unsafe", "trailing", "download-bound", "expanded-bound", "file-bound"])
def test_original_source_capture_rejects_invalid_incomplete_or_unbounded_snapshots(original_paths, monkeypatch, defect):
    options = {}
    if defect == "revision":
        options["revision"] = "f" * 40
    elif defect == "missing":
        del original_paths["pyproject.toml"]
    elif defect == "duplicate":
        options["duplicate"] = True
    elif defect == "alias":
        original_paths["./pyproject.toml"] = b'ambiguous'
    elif defect == "link":
        options["source_link"] = "pyproject.toml"
    elif defect == "unsafe":
        original_paths["../../outside"] = b'unsafe'
    elif defect == "trailing":
        options["trailing"] = True
    elif defect == "download-bound":
        monkeypatch.setattr(ready, "MAX_SOURCE_ARCHIVE_BYTES", 1)
    elif defect == "expanded-bound":
        monkeypatch.setattr(ready, "MAX_SOURCE_EXPANSION_BYTES", 1)
    elif defect == "file-bound":
        monkeypatch.setattr(ready, "MAX_SOURCE_FILE_BYTES", 1)
    with pytest.raises(ready.ReadinessError):
        ready.source_snapshot(source_archive(original_paths, **options), HEAD)


def test_public_source_request_has_no_token_or_redirect_and_never_executes_old_code(monkeypatch, original_paths):
    monkeypatch.setenv("GH_TOKEN", "private-token-must-not-be-forwarded")
    response = source_archive(original_paths)
    opener = mock.Mock()
    opener.open.return_value = response
    with mock.patch.object(ready.urllib.request, "build_opener", return_value=opener) as build:
        ready.original_source_digest(HEAD)
    request = opener.open.call_args.args[0]
    assert request.full_url == f"https://codeload.github.com/{ready.REPOSITORY}/tar.gz/{HEAD}"
    assert not request.has_header("Authorization")
    assert build.call_args.args == (ready.NoRedirect,)
    with pytest.raises(ready.ReadinessError, match="redirected"):
        ready.NoRedirect().redirect_request(request, None, 302, '', {}, 'https://foreign.invalid')


@pytest.mark.parametrize("error", [urllib.error.HTTPError('url', 403, 'quota', None, None), urllib.error.URLError('offline'), TimeoutError('timeout')])
def test_metadata_api_errors_are_terminal_without_retry_or_fallback(monkeypatch, error):
    monkeypatch.setenv("GH_TOKEN", "sensitive")
    with mock.patch.object(ready, "open_without_redirects", side_effect=error) as call:
        with pytest.raises(ready.ReadinessError, match="unavailable"):
            ready.GitHub().get('/repos/SkyTruth/shared-datasets-1/actions/artifacts/11263851699')
    call.assert_called_once()
    request = call.call_args.args[0]
    assert request.full_url.startswith('https://api.github.com/') and request.has_header('Authorization')


@pytest.mark.parametrize("raw", [b'{"id":1,"id":2}', b'{}' + b' ' * ready.MAX_API_BYTES])
def test_metadata_rejects_duplicate_keys_and_oversized_responses(raw):
    with mock.patch.object(ready, 'open_without_redirects', return_value=io.BytesIO(raw)):
        with pytest.raises(ready.ReadinessError):
            ready.GitHub().get('/repos/SkyTruth/shared-datasets-1/actions/artifacts/11263851699')


def test_default_precloud_gate_still_checks_its_own_producer_not_a_cli_override(evidence, monkeypatch):
    monkeypatch.setattr(gate, 'source_digest', lambda: 'f' * 64)
    assert 'staged validation does not match the processing source tree' in gate.check_precloud(evidence)
    assert gate.check_precloud(evidence, producer_source_sha256=SOURCE) == []
    # The keyword is an internal independently verified source boundary; the
    # executable producer does not expose a skip or source override flag.
    source = (ROOT / 'scripts/wdpa_processing_gate.py').read_text()
    assert 'parser.add_argument("--producer-source' not in source


def test_guard_is_selected_in_preflight_and_independently_in_required_ci_ready():
    for deployments, selected in (([], False), (["wdpa"], False), (["wdpa_processing"], True)):
        plan = {"base": 'base', "tested_sha": 'head', "deployments": deployments}
        commands = [command for command, _ in ci_preflight.suite_commands('tests', ROOT, plan, ROOT)]
        checks = [command for command in commands if 'scripts/wdpa_staged_image_readiness.py' in command]
        assert bool(checks) is selected
        if selected:
            assert commands.index(checks[0]) < next(index for index, command in enumerate(commands) if 'pytest' in command)
    assert_ci_boundary(load_workflow(ROOT / '.github/workflows/ci.yml'))


def assert_ci_boundary(ci):
    job = ci['jobs']['ci-ready']
    steps = job['steps']
    checks = [step for step in steps if 'wdpa_staged_image_readiness.py' in step.get('run', '')]
    assert len(checks) == 1
    check = checks[0]
    assert check['if'] == "${{ needs.geospatial-changes.outputs.wdpa_processing == 'true' }}"
    assert check['env'] == {'GH_TOKEN': '${{ github.token }}'}
    assert job['permissions']['actions'] == 'read'
    assert steps.index(check) < next(index for index, step in enumerate(steps) if step.get('uses', '').startswith('actions/upload-artifact'))


def test_trusted_leaf_rechecks_before_authentication_and_downloads_exact_artifact_id():
    assert_leaf_boundary(load_workflow(ROOT / '.github/workflows/wdpa-processing-validation-deploy.yml'))


def assert_leaf_boundary(workflow):
    steps = workflow_steps_by_name(workflow, 'deploy')
    order = list(steps)
    name = 'Verify reviewed retained image availability before cloud authentication'
    check = steps[name]
    assert check['id'] == 'staged-image' and '--github-output "$GITHUB_OUTPUT"' in check['run']
    assert check['if'] == "${{ steps.replay.outputs.proceed == 'true' }}"
    assert order.index('Verify tested main revision') < order.index(name) < order.index('Authenticate to Google Cloud')
    download = steps['Download the tested deployment image']['with']
    assert download['artifact-ids'] == '${{ steps.staged-image.outputs.artifact_id }}'
    assert download['merge-multiple'] is True
    assert download['run-id'] == '${{ steps.staged-image.outputs.run_id }}'
    assert 'name' not in download and 'pattern' not in download


@pytest.mark.parametrize('defect', ['removed', 'disabled', 'late', 'wrong-selection'])
def test_ci_ready_cannot_omit_disable_or_defer_the_selected_readiness_check(defect):
    workflow = load_workflow(ROOT / '.github/workflows/ci.yml')
    steps = workflow['jobs']['ci-ready']['steps']
    check = next(step for step in steps if 'wdpa_staged_image_readiness.py' in step.get('run', ''))
    if defect == 'removed':
        steps.remove(check)
    elif defect == 'disabled':
        check['if'] = False
    elif defect == 'late':
        steps.remove(check)
        steps.append(check)
    else:
        check['if'] = "${{ needs.geospatial-changes.outputs.wdpa == 'true' }}"
    with pytest.raises(AssertionError):
        assert_ci_boundary(workflow)


@pytest.mark.parametrize('defect', ['removed', 'disabled', 'late', 'named-download'])
def test_leaf_cannot_authenticate_before_readiness_or_select_mutable_artifact_names(defect):
    workflow = load_workflow(ROOT / '.github/workflows/wdpa-processing-validation-deploy.yml')
    steps = workflow['jobs']['deploy']['steps']
    check = next(step for step in steps if step.get('id') == 'staged-image')
    if defect == 'removed':
        steps.remove(check)
    elif defect == 'disabled':
        check['if'] = False
    elif defect == 'late':
        steps.remove(check)
        steps.append(check)
    else:
        download = next(step for step in steps if step.get('name') == 'Download the tested deployment image')
        download['with']['artifact-ids'] = 'another-artifact'
        download['with']['name'] = ready.ARTIFACT_NAME
    with pytest.raises((AssertionError, KeyError)):
        assert_leaf_boundary(workflow)
