import copy
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from google.api_core.exceptions import NotFound, PreconditionFailed
import pytest

from ingestion.wdpa_monthly import execution_observer as observer
from scripts import wdpa_processing_gate as gate

JOB = "projects/shared-datasets-1/locations/us-east1/jobs/wdpa-monthly"


def execution(
    identifier,
    state="CONDITION_SUCCEEDED",
    created="2026-10-01T09:00:00Z",
    completed="2026-10-01T10:00:00Z",
    **kwargs,
):
    return {
        "name": JOB + "/executions/" + identifier,
        "createTime": created,
        "completionTime": completed,
        "conditions": [{"type": "Completed", "state": state, **kwargs}],
    }


def test_new_running_execution_retains_late_failed_completed_execution():
    failed = execution(
        "wdpa-failed",
        "CONDITION_FAILED",
        executionReason="NON_ZERO_EXIT_CODE",
        message="secret path",
    )
    running = execution(
        "wdpa-running",
        "CONDITION_PENDING",
        created="2026-10-01T11:00:00Z",
        completed=None,
    )
    running["startTime"] = "2026-10-01T11:01:00Z"
    document = observer.observe(
        iter([running, failed]), job_name=JOB, observed_at="2026-10-01T11:03:00Z"
    )
    assert document["latest_execution"]["state"] == "running"
    assert document["latest_completed_execution"]["state"] == "failed"
    assert "secret" not in str(document)
    assert document["latest_completed_execution"]["reason_code"] == "NON_ZERO_EXIT_CODE"


@pytest.mark.parametrize(
    "state,expected",
    [
        ("CONDITION_SUCCEEDED", "succeeded"),
        ("CONDITION_FAILED", "failed"),
        ("CONDITION_PENDING", "unknown"),
    ],
)
def test_execution_state(state, expected):
    assert observer.execution_entry(execution("wdpa-test", state))["state"] == expected


def test_cancellation_and_reason_sanitization():
    raw = execution(
        "wdpa-cancelled",
        "CONDITION_FAILED",
        executionReason="JOB_STATUS_USER_CANCELLED",
    )
    assert observer.execution_entry(raw)["state"] == "cancelled"
    raw["conditions"][0]["executionReason"] = "unknown secret"
    assert observer.execution_entry(raw)["reason_code"] == "OTHER"


def test_generation_preconditions_and_newer_observation_wins():
    blob = Mock(generation=123)
    blob.download_as_text.return_value = (
        '{"schema_version":1,"job_name":"'
        + JOB
        + '","observed_at":"2026-10-01T11:00:00Z"}'
    )
    bucket = Mock()
    bucket.blob.return_value = blob
    observation = observer.observe([], job_name=JOB, observed_at="2026-10-01T11:05:00Z")
    assert observer.write_observation(bucket, observation)
    assert blob.upload_from_string.call_args.kwargs["if_generation_match"] == 123
    assert blob.cache_control == "public, max-age=0, must-revalidate"
    assert bucket.blob.call_args.args == (observer.STATUS_OBJECT,)
    blob.upload_from_string.reset_mock()
    observation["observed_at"] = "2026-10-01T10:59:00Z"
    assert observer.write_observation(bucket, observation) is False
    blob.upload_from_string.assert_not_called()
    observation["observed_at"] = "2026-10-01T11:06:00Z"
    blob.upload_from_string.side_effect = PreconditionFailed("race")
    assert observer.write_observation(bucket, observation) is False


def test_first_observation_requires_no_clobber():
    blob = Mock()
    blob.reload.side_effect = NotFound("missing")
    assert observer.write_observation(
        SimpleNamespace(blob=lambda _: blob),
        observer.observe([], job_name=JOB, observed_at="2026-10-01T11:00:00Z"),
    )
    assert blob.upload_from_string.call_args.kwargs["if_generation_match"] == 0


def test_lagging_api_cannot_hide_failure_or_rewind_terminal_execution():
    previous = observer.observe(
        [execution("wdpa-late", "CONDITION_FAILED")],
        job_name=JOB,
        observed_at="2026-10-01T11:00:00Z",
    )
    lagging = observer.observe(
        [execution("wdpa-late", "CONDITION_PENDING", completed=None)],
        job_name=JOB,
        observed_at="2026-10-01T11:05:00Z",
    )
    blob = Mock(generation=10)
    blob.download_as_text.return_value = json.dumps(previous)
    assert observer.write_observation(SimpleNamespace(blob=lambda _: blob), lagging)
    written = json.loads(blob.upload_from_string.call_args.args[0])
    assert written["latest_execution"]["state"] == "failed"
    assert written["latest_completed_execution"]["state"] == "failed"
    assert written["observed_at"] == lagging["observed_at"]
    blob.upload_from_string.reset_mock()
    blob.download_as_text.side_effect = PreconditionFailed("read raced")
    assert (
        observer.write_observation(SimpleNamespace(blob=lambda _: blob), lagging)
        is False
    )
    blob.upload_from_string.assert_not_called()


def accepted_evidence():
    run = {
        "state": "succeeded",
        "sample_fraction": 1,
        "genesis": False,
        "run_date": "2026-10-01",
        "cpu_limit": 4,
        "memory_limit_bytes": 8 * 1024**3,
        "memory_peak_bytes": 5 * 1024**3,
        "scratch_peak_bytes": 40 * 1024**3,
        "elapsed_seconds": 4000,
        "source_counts_verified": True,
        "contracts_verified": True,
        "compatibility_verified": False,
        "native_versions": {"gdal_python": "3.6.2"},
        "source_tree_sha256": gate.source_digest(),
        "image_digest": "sha256:" + "a" * 64,
        "cloud_image": "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/wdpa-validation@sha256:"
        + "1" * 64,
        "cloud_execution": "wdpa-processing-validation-aaaaa",
        "source_sha256": "b" * 64,
        "baseline_snapshot_sha256": "c" * 64,
        "translation_memory_sha256": "d" * 64,
        "translation_inputs_snapshot_sha256": "d" * 64,
        "translation_index_built": True,
        "assets": {
            "wdpa-marine": {
                "rows": 17938,
                "india_rows": 304,
                "india_sites": 304,
                "semantic_sha256": "e" * 64,
                "next_generated_feature_id": 101,
            },
            "wdpa-terrestrial": {
                "rows": 497914,
                "india_rows": 193174,
                "india_sites": 193174,
                "semantic_sha256": "f" * 64,
                "next_generated_feature_id": 1001,
            },
        },
    }
    sample = copy.deepcopy(run)
    sample.update(sample_fraction=0.001, sample_seed=7919, compatibility_verified=True)
    for asset in sample["assets"].values():
        asset["compatibility_verified"] = True
    run["artifact_bundle"] = {
        "uri": "gs://skytruth-shared-datasets-1/_scratch/wdpa-builds/wdpa-processing-validation-aaaaa/build-bundle.json",
        "generation": 12,
        "size": 100,
        "sha256": "a" * 64,
    }
    return {
        "schema_version": 3,
        "source_tree_sha256": gate.source_digest(),
        "disk_quota_approved": True,
        "build": run,
        "promotion_pr": 7,
        "promotion_plan": ".github/dataset-plans/wdpa-build-" + "a" * 64 + ".json",
        "compatibility_sample": sample,
    }


@pytest.fixture(autouse=True)
def reviewed_plan(tmp_path, monkeypatch):
    evidence = accepted_evidence()
    build = evidence["build"]
    plan = {
        "schema_version": 1,
        "kind": "wdpa_owned_build_promotion",
        **{
            key: build[key]
            for key in (
                "artifact_bundle",
                "cloud_execution",
                "cloud_image",
                "image_digest",
                "source_tree_sha256",
                "run_date",
                "assets",
            )
        },
    }
    path = tmp_path / evidence["promotion_plan"]
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(plan))
    # Keep source_digest bound to the real repo while isolating immutable plans.
    original = gate.check_promotion_plan

    def check(evidence):
        with monkeypatch.context() as patch:
            patch.setattr(gate, "ROOT", tmp_path)
            return original(evidence)

    monkeypatch.setattr(gate, "check_promotion_plan", check)


def test_preferred_headroom_warns_without_rejecting_a_successful_build():
    evidence = accepted_evidence()
    assert gate.check(evidence) == []
    peak = 7732400128
    evidence["build"]["memory_peak_bytes"] = peak
    assert gate.check(evidence) == []
    assert gate.memory_warnings(evidence["build"])
    assert evidence["build"]["memory_peak_bytes"] == peak
    evidence["build"]["memory_limit_bytes"] = 16 * 1024**3
    assert any("4 CPU" in error for error in gate.check(evidence))
    evidence["build"]["memory_peak_bytes"] = None
    assert any("kernel lifetime peak" in error for error in gate.check(evidence))
    evidence = accepted_evidence()
    evidence["build"]["source_sha256"] = "different"
    assert any("disagree" in error for error in gate.check(evidence))
    evidence = accepted_evidence()
    evidence["build"]["translation_index_built"] = False
    assert gate.check(evidence), (
        "a cached index cannot certify complete production processing"
    )


@pytest.mark.parametrize("peak", [None, True, 0, -1, 7.2, 8 * 1024**3 + 1])
def test_memory_peak_must_be_measured_and_within_the_enforced_limit(peak):
    build = accepted_evidence()["build"]
    build["memory_peak_bytes"] = peak
    assert any("kernel lifetime peak" in error for error in gate.check_build(build))


@pytest.mark.parametrize(
    "peak,warns",
    [
        (int(6.4 * 1024**3), False),
        (int(6.4 * 1024**3) + 1, True),
        (7732400128, True),
        (8 * 1024**3, True),
    ],
)
def test_advisory_boundary_does_not_change_acceptance(peak, warns):
    build = accepted_evidence()["build"]
    build["memory_peak_bytes"] = peak
    assert gate.check_build(build) == []
    assert bool(gate.memory_warnings(build)) == warns


def test_acceptance_cli_warns_and_succeeds_for_preferred_headroom_excess(
    monkeypatch, tmp_path, capsys
):
    evidence = accepted_evidence()
    evidence["build"]["memory_peak_bytes"] = 7732400128
    path = tmp_path / "acceptance.json"
    path.write_text(json.dumps(evidence))
    monkeypatch.setattr(gate, "EVIDENCE", path)
    monkeypatch.setattr(sys, "argv", ["wdpa_processing_gate.py"])
    gate.main()
    warning = capsys.readouterr().err
    assert "WARNING:" in warning and "7732400128 bytes (7.20 GiB)" in warning


def test_build_must_match_the_reviewed_processing_tree():
    evidence = accepted_evidence()
    evidence["build"]["source_tree_sha256"] = "0" * 64
    assert any("processing source tree" in error for error in gate.check(evidence))


def test_reviewed_producer_is_independent_of_new_publication_code(monkeypatch):
    evidence = accepted_evidence()
    monkeypatch.setattr(gate, "source_digest", lambda: "0" * 64)
    assert gate.check(evidence) == []


def test_complete_resource_reports_do_not_claim_a_legacy_comparison():
    evidence = accepted_evidence()
    assert evidence["build"]["compatibility_verified"] is False
    assert gate.check(evidence) == []
    evidence.pop("compatibility_sample")
    assert any("compatibility" in error for error in gate.check(evidence))


@pytest.mark.parametrize(
    "defect",
    [
        "full",
        "genesis",
        "unverified",
        "one_realm",
        "source",
        "baseline",
        "translations",
        "image",
        "native",
        "seed",
    ],
)
def test_compatibility_evidence_cannot_be_substituted_with_artifact_checks(defect):
    evidence = accepted_evidence()
    sample = evidence["compatibility_sample"]
    if defect == "full":
        sample["sample_fraction"] = 1
    elif defect == "genesis":
        sample["genesis"] = True
    elif defect == "unverified":
        sample["compatibility_verified"] = False
    elif defect == "one_realm":
        sample["assets"].pop("wdpa-marine")
    elif defect == "seed":
        sample["sample_seed"] = 1
    else:
        key = {
            "source": "source_sha256",
            "baseline": "baseline_snapshot_sha256",
            "translations": "translation_inputs_snapshot_sha256",
            "image": "image_digest",
            "native": "native_versions",
        }[defect]
        sample[key] = "mismatched"
    assert gate.check(evidence)


@pytest.mark.parametrize("cpu", [None, 0, -1, 4.01, 8, True])
def test_cpu_budget_cannot_be_missing_or_increased(cpu):
    evidence = accepted_evidence()
    evidence["build"]["cpu_limit"] = cpu
    assert any("4 CPU" in error for error in gate.check(evidence))


def test_cloud_kernel_quota_is_retained_without_increasing_the_budget():
    evidence = accepted_evidence()
    evidence["build"]["cpu_limit"] = 3.72
    assert gate.check(evidence) == []
    assert evidence["build"]["cpu_limit"] == 3.72


def test_acceptance_requires_retained_bytes_instead_of_a_replay_list():
    evidence = accepted_evidence()
    evidence["runs"] = [evidence["build"], evidence["build"]]
    assert any("replay list" in error for error in gate.check(evidence))
    evidence = accepted_evidence()
    evidence["build"].pop("artifact_bundle")
    assert any("retained" in error for error in gate.check(evidence))
