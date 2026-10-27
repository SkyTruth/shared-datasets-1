"""The measurement adapter cannot substitute bytes or confer deployment authority."""
import copy
import hashlib
import io
import json
from subprocess import check_output as real_check_output
from subprocess import run as real_run
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import pytest

from scripts import benchmark_image_handoff as benchmark
from scripts.deployment_revision import DeploymentError
from ci_result_fixtures import production_images
from test_tested_image_handoff import handoff as handoff
from test_tested_image_handoff import zipped as handoff_zip


SHA = "a" * 40
HEAD = "b" * 40
REPOSITORY = "SkyTruth/shared-datasets-1"


def zipped(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, json.dumps(value))
    return output.getvalue()


@pytest.fixture
def source_api():
    repository = {"id": 9, "full_name": REPOSITORY}
    workflow = {"id": 7, "path": ".github/workflows/ci.yml"}
    run = {"id": 123, "run_attempt": 1, "workflow_id": 7, "path": workflow["path"], "head_sha": HEAD,
           "repository": repository, "head_repository": repository, "event": "pull_request"}
    plan = {"schema_version": 1, "base": "c" * 40, "head": HEAD, "tested_sha": SHA,
            "source": {"run_id": "123", "run_attempt": 1}, "suites": ["production-images"]}
    raw = zipped({"plan.json": plan})
    artifacts = [{"id": 41, "name": "ci-validation-plan-attempt1", "expired": False, "workflow_run": {"id": 123},
                  "size_in_bytes": len(raw), "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}]
    jobs = [{"id": index, "name": name, "status": "completed", "conclusion": "success"}
            for index, name in enumerate(("geospatial-changes", "production-images"), 1)]
    api = Mock()
    api.get.side_effect = lambda path: repository if path == f"repos/{REPOSITORY}" else workflow if "/workflows/" in path else run
    api.pages.side_effect = lambda path, field=None: artifacts if field == "artifacts" else jobs
    api.archive.return_value = raw
    return SimpleNamespace(api=api, run=run, plan=plan, artifacts=artifacts, jobs=jobs)


def test_pr_measurement_binds_api_source_head_to_actual_tested_merge_plan(source_api):
    plan, artifacts, jobs = benchmark.source(source_api.api, REPOSITORY, 123, 1, SHA)
    assert plan["tested_sha"] == SHA and plan["head"] == HEAD and HEAD != SHA
    assert artifacts == source_api.artifacts
    assert [job["name"] for job in jobs] == ["geospatial-changes", "production-images"]


@pytest.mark.parametrize("defect", ["superseded", "wrong-sha", "not-pr", "foreign-repository", "failed-producer", "skipped-producer", "ambiguous-producer", "expired-plan", "wrong-plan-attempt"])
def test_source_metadata_or_failed_producing_attempt_cannot_enter_measurement(source_api, defect):
    if defect == "superseded":
        source_api.run["run_attempt"] = 2
    elif defect == "wrong-sha":
        source_api.run["head_sha"] = "d" * 40
    elif defect == "not-pr":
        source_api.run["event"] = "push"
    elif defect == "foreign-repository":
        source_api.run["head_repository"] = {"id": 99, "full_name": "fork/repository"}
    elif defect in {"failed-producer", "skipped-producer"}:
        source_api.jobs[-1]["conclusion"] = "failure" if defect == "failed-producer" else "skipped"
    elif defect == "ambiguous-producer":
        source_api.jobs.append(copy.deepcopy(source_api.jobs[-1]))
    elif defect == "expired-plan":
        source_api.artifacts[0]["expired"] = True
    else:
        source_api.plan["source"]["run_attempt"] = 2
        raw = zipped({"plan.json": source_api.plan})
        source_api.api.archive.return_value = raw
        source_api.artifacts[0].update(size_in_bytes=len(raw), digest="sha256:" + hashlib.sha256(raw).hexdigest())
    with pytest.raises(DeploymentError):
        benchmark.source(source_api.api, REPOSITORY, 123, 1, SHA)


def test_alias_preserves_actual_id_digest_and_original_inventory(tmp_path):
    real = {"id": 1, "name": "ci-image-mpatlas-monthly-attempt1", "digest": "real-digest"}
    comparison = {"id": 2, "name": "benchmark-image-mpatlas-level1-attempt1", "digest": "comparison-digest"}
    original = [real, comparison]
    saved = copy.deepcopy(original)
    transport = Mock()
    transport.archive_to.side_effect = lambda repository, artifact_id, path: path.write_bytes(b"actual chosen ZIP")
    api = benchmark.MeasuredAPI(Mock(), transport, (comparison["name"], real["name"]))
    inventory = api.artifacts(original)
    assert inventory == [{**comparison, "name": real["name"]}]
    assert original == saved
    api.archive_to(REPOSITORY, inventory[0]["id"], tmp_path / "chosen.zip")
    transport.archive_to.assert_called_once_with(REPOSITORY, 2, tmp_path / "chosen.zip")
    assert api.downloads[0]["artifact_id"] == 2
    assert api.downloads[0]["zip_bytes"] == len(b"actual chosen ZIP")


@pytest.mark.parametrize("matches", [[], [{"name": "comparison"}, {"name": "comparison"}]])
def test_alias_cannot_choose_a_missing_or_ambiguous_comparison(matches):
    api = benchmark.MeasuredAPI(Mock(), Mock(), ("comparison", "canonical"))
    with pytest.raises(DeploymentError, match="missing or ambiguous"):
        api.artifacts(matches)


@pytest.mark.parametrize("field", ["config_digest", "archive_sha256"])
def test_different_variant_identity_fails_before_docker_load_and_records_failure(tmp_path, monkeypatch, field):
    expected = production_images(SHA)["images"][benchmark.TARGET]
    altered = {**expected, field: ("sha256:" if field == "config_digest" else "") + "f" * 64}
    artifact = {"id": 7, "name": "benchmark-full-images-attempt1", "workflow_run": {"id": 123},
                "size_in_bytes": 100, "expired": False, "digest": "sha256:" + "1" * 64}
    monkeypatch.setattr(benchmark, "source", lambda *args: ({}, [artifact], []))
    monkeypatch.setattr(benchmark, "tag_state", lambda *args: {"present": False})
    loaded = Mock()
    monkeypatch.setattr(benchmark.bundle, "load_image", loaded)
    baseline = SimpleNamespace(GitHub=Mock, MAX_ARTIFACT=1000, selected_image=Mock(return_value=altered))
    args = SimpleNamespace(repository=REPOSITORY, run_id=123, attempt=1, sha=SHA)
    with pytest.raises(DeploymentError, match="exactly the same tested TAR and config"):
        benchmark.trial(args, "baseline", 1, baseline, expected, tmp_path)
    loaded.assert_not_called()
    result = json.loads((tmp_path / "trial-1-baseline/result.json").read_text())
    assert result["status"] == "failure" and result["error_type"] == "DeploymentError"


@pytest.mark.parametrize("state", [{"present": False}, {"present": True, "daemon_id": "foreign-image"}])
def test_timed_trial_requires_the_exact_warmed_image(tmp_path, monkeypatch, state):
    expected = production_images(SHA)["images"][benchmark.TARGET]
    monkeypatch.setattr(benchmark, "tag_state", lambda *args: state)
    source = Mock()
    monkeypatch.setattr(benchmark, "source", source)
    args = SimpleNamespace(repository=REPOSITORY, run_id=123, attempt=1, sha=SHA)
    baseline = SimpleNamespace(GitHub=Mock)
    with pytest.raises(DeploymentError, match="exact warmed image"):
        benchmark.trial(args, "baseline", 1, baseline, expected, tmp_path, "verified-warm-image")
    source.assert_not_called()


def test_benchmark_is_pinned_and_balanced():
    assert benchmark.ORDER == ("baseline", "level0", "level1", "level1", "level0", "baseline")
    with pytest.raises(SystemExit):
        benchmark.parser().parse_args(["--repository", REPOSITORY, "--run-id", "123", "--attempt", "1",
                                      "--sha", SHA, "--baseline-ref", "main", "--output", "unused"])


def test_failed_variant_makes_whole_cli_nonzero_and_preserves_report(tmp_path, monkeypatch):
    expected = production_images(SHA)["images"][benchmark.TARGET]
    args = SimpleNamespace(repository=REPOSITORY, run_id=123, attempt=1, sha=SHA,
                           baseline_ref=benchmark.BASELINE_SHA, output=tmp_path)
    monkeypatch.setattr(benchmark, "parser", lambda: SimpleNamespace(parse_args=lambda: args))
    monkeypatch.setattr(benchmark.subprocess, "check_output", lambda *args, **kwargs: SHA)
    monkeypatch.setattr(benchmark, "baseline_loader", lambda *args: (None, {}))
    monkeypatch.setattr(benchmark, "machine", lambda: {})
    monkeypatch.setattr(benchmark, "source", lambda *args: ({}, [], []))
    monkeypatch.setattr(benchmark, "result_image", lambda *args: expected)
    monkeypatch.setattr(benchmark, "tag_state", lambda *args: {"present": False})

    def failed(*args):
        if args[2] == 0:
            return {"variant": "level1", "status": "success", "loaded_daemon_id": "warm-id", "seconds": 1, "zip_bytes": 1}
        directory = tmp_path / "trial-1-baseline"
        directory.mkdir()
        (directory / "result.json").write_text(json.dumps({"variant": "baseline", "status": "failure"}))
        raise DeploymentError("genuine corrupted TAR")

    monkeypatch.setattr(benchmark, "trial", failed)
    with pytest.raises(DeploymentError, match="genuine corrupted TAR"):
        benchmark.main()
    report = json.loads((tmp_path / "results.json").read_text())
    assert report["status"] == "failure" and report["trials"] == [{"variant": "baseline", "status": "failure"}]


def test_baseline_ref_rejected_before_source_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark.subprocess, "check_output", Mock(return_value="a" * 40))
    with pytest.raises(DeploymentError, match="pinned main revision"):
        benchmark.baseline_loader("untrusted-ref", tmp_path)
    assert not list(tmp_path.iterdir())


def test_real_baseline_and_current_loaders_consume_identical_bytes_through_benchmark_adapter(handoff, monkeypatch):
    with zipfile.ZipFile(io.BytesIO(handoff["payloads"][1])) as archive:
        plan = json.loads(archive.read("plan.json"))
    with monkeypatch.context() as context:
        context.setattr(benchmark.subprocess, "check_output", real_check_output)
        context.setattr(benchmark.subprocess, "run", real_run)
        baseline, provenance = benchmark.baseline_loader(benchmark.BASELINE_SHA, handoff["root"])
    assert provenance["commit"] == benchmark.BASELINE_SHA
    expected = handoff["manifest"]["images"][benchmark.TARGET]
    canonical_id = handoff["image_artifact_ids"][benchmark.TARGET]
    chosen_ids = {}
    for variant in benchmark.VARIANTS:
        if variant == "level1":
            chosen_ids[variant] = canonical_id
            continue
        payload = handoff_zip(handoff["files"]) if variant == "baseline" else handoff["payloads"][canonical_id - 1]
        artifact_id = len(handoff["payloads"]) + 1
        handoff["payloads"].append(payload)
        name = "benchmark-full-images-attempt1" if variant == "baseline" else f"benchmark-image-mpatlas-{variant}-attempt1"
        handoff["artifacts"].append({"id": artifact_id, "name": name, "expired": False, "workflow_run": {"id": 123},
                                      "size_in_bytes": len(payload), "digest": "sha256:" + hashlib.sha256(payload).hexdigest()})
        chosen_ids[variant] = artifact_id
    monkeypatch.setattr(benchmark.current, "GitHub", lambda: handoff["api"])
    monkeypatch.setattr(baseline, "GitHub", lambda: handoff["api"])
    monkeypatch.setattr(benchmark, "source", lambda *args: (plan, handoff["artifacts"], []))
    monkeypatch.setattr(benchmark, "tag_state", lambda *args: {"present": True, "daemon_id": expected["config_digest"]})
    args = SimpleNamespace(repository=REPOSITORY, run_id=123, attempt=1, sha=SHA)
    results = [benchmark.trial(args, variant, index, baseline, expected, handoff["root"], expected["config_digest"])
               for index, variant in enumerate(benchmark.VARIANTS, 1)]
    assert all(item["status"] == "success" and item["loaded_daemon_id"] == expected["config_digest"] for item in results)
    assert len({json.dumps(item["verified_identity"], sort_keys=True) for item in results}) == 1
    for item in results:
        assert item["actual_artifact"]["id"] == chosen_ids[item["variant"]]
        assert item["downloads"][-1]["artifact_id"] == chosen_ids[item["variant"]]
        assert any(phase["phase"] == "tar-and-rootfs-verification" for phase in item["phases"])
    assert len(handoff["calls"]) == 3 and all(call[:3] == ["docker", "image", "load"] for call in handoff["calls"])
