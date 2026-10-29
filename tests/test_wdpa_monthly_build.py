"""The recurring build, review handoff and next-window readiness boundaries."""
import copy
import datetime as dt
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

from ingestion.common import publication as p
from ingestion.wdpa_monthly import build as producer
from scripts import wdpa_monthly_build as monthly, wdpa_processing_gate as gate
from scripts.release_contracts import retained_execution_errors
from test_wdpa_execution_observer import accepted_evidence
from test_wdpa_artifact_bundle import built as _built

built = _built


def test_monthly_acceptance_uses_independent_counts_and_matching_input_month():
    evidence = accepted_evidence()
    report = evidence["build"]
    report["run_date"] = evidence["compatibility_sample"]["run_date"] = "2026-11-01"
    for asset in report["assets"].values():
        asset["rows"] += 20
        asset["source_counts"]["rows"] += 20
    assert not gate.check_build(report)
    assert not gate.check_compatibility(evidence, [report])
    report["assets"]["wdpa-marine"]["rows"] -= 1
    assert any("independently counted" in error for error in gate.check_build(report))
    evidence["compatibility_sample"]["run_date"] = "2026-10-01"
    assert any("run_date" in error for error in gate.check_compatibility(evidence, [report]))


@pytest.mark.parametrize("date", [None, "", "2026-11", "20261101", "2026-11-02", "2026-13-01"])
def test_build_date_must_be_explicit_and_canonical(date):
    with pytest.raises(ValueError):
        gate.monthly_date(date)


@pytest.mark.parametrize("now,expected", [
    ("2026-10-29T20:00:00+00:00", "2026-11-01"),
    ("2026-11-01T08:00:00+00:00", "2026-11-01"),
    ("2026-11-10T08:59:59+00:00", "2026-11-01"),
    ("2026-11-10T09:00:00+00:00", "2026-12-01"),
    ("2026-12-31T23:00:00+00:00", "2027-01-01"),
])
def test_readiness_targets_the_next_real_window(now, expected):
    assert monthly.next_window(dt.datetime.fromisoformat(now)).isoformat() == expected


def job_for(evidence):
    return {"template": {"template": {"containers": [{"env": [
        {"name": "WDPA_PROMOTION_BUNDLE", "value": json.dumps(evidence["build"]["artifact_bundle"])}]}]}}}


@pytest.mark.parametrize("paused", [False, True])
def test_readiness_does_not_confuse_paused_or_enabled_with_month_acceptance(paused):
    evidence = json.loads(gate.EVIDENCE.read_text())
    scheduler = {"state": "PAUSED" if paused else "ENABLED", "schedule": "0 9 1-10 * *", "timeZone": "UTC"}
    assert monthly.readiness(evidence, job_for(evidence), scheduler, dt.date(2026, 10, 1))["ready"]
    result = monthly.readiness(evidence, job_for(evidence), scheduler, dt.date(2026, 11, 1))
    assert not result["ready"]
    assert result["accepted_date"] == "2026-10-01"
    assert "approve" in result["reasons"][0]


@pytest.mark.parametrize("defect", ["bundle", "date-override", "schedule", "zone", "state"])
def test_ready_month_requires_the_deployed_contract(defect):
    evidence = json.loads(gate.EVIDENCE.read_text())
    job = job_for(evidence)
    env = job["template"]["template"]["containers"][0]["env"]
    scheduler = {"state": "ENABLED", "schedule": "0 9 1-10 * *", "timeZone": "UTC"}
    if defect == "bundle":
        env[0]["value"] = "{}"
    elif defect == "date-override":
        env.append({"name": "RUN_DATE", "value": "2026-10-01"})
    else:
        scheduler[{"schedule": "schedule", "zone": "timeZone", "state": "state"}[defect]] = "wrong"
    assert not monthly.readiness(evidence, job, scheduler, dt.date(2026, 10, 1))["ready"]


def test_readiness_cli_exposes_missing_handoff_and_paused_status(monkeypatch, tmp_path, capsys):
    evidence = json.loads(gate.EVIDENCE.read_text())
    scheduler = {"state": "PAUSED", "schedule": "0 9 1-10 * *", "timeZone": "UTC"}
    monkeypatch.setattr(monthly, "cloud_get", lambda service, path: scheduler if service == "cloudscheduler" else job_for(evidence))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    monkeypatch.setattr(sys, "argv", ["wdpa_monthly_build.py", "readiness", "--run-date", "2026-11-01"])
    assert monthly.main() == 1  # Scheduler resume fails even while paused.
    assert json.loads(capsys.readouterr().out)["required_month"] == "2026-11"
    sys.argv.append("--allow-paused")
    assert monthly.main() == 0
    assert not json.loads(capsys.readouterr().out)["ready"]
    assert "prepare, approve and deploy" in (tmp_path / "summary.md").read_text()


@pytest.mark.parametrize("built", [{"run_date": "2026-11-01"}], indirect=True)
@pytest.mark.parametrize("failed", [False, True])
def test_collect_serialized_bundle_requires_terminal_success_and_preserves_exact_bytes(built, monkeypatch, tmp_path, failed):
    from google.cloud import storage
    store, reference, report = built
    execution = report["cloud_execution"]
    name = reference["uri"].split("/", 3)[3]
    generation, raw = store.data[name]
    document = json.loads(raw)
    sample = copy.deepcopy(report)
    sample.update(sample_fraction=0.001, sample_seed=7919, compatibility_verified=True)
    for asset in sample["assets"].values():
        asset["compatibility_verified"] = True
    document["report"]["compatibility_sample"] = sample
    raw = p.canonical(document)
    blob = Mock(name=name, generation=generation, size=len(raw))
    blob.name = name
    blob.download_as_bytes.return_value = raw
    client = Mock()
    client.bucket.return_value.blob.return_value = blob
    monkeypatch.setattr(storage, "Client", lambda **_: client)
    facts = {"name": f"{monthly.LOCATION}/jobs/wdpa-monthly-build/executions/{execution}",
        "completionTime": "2026-11-01T12:00:00Z", "taskCount": 1, "succeededCount": 1,
        "failedCount": int(failed), "conditions": [{"type": "Completed", "state": "CONDITION_SUCCEEDED"}],
        "template": {"containers": [{"image": report["cloud_image"]}]}}
    monkeypatch.setattr(monthly, "cloud_get", lambda *_: facts)
    output = tmp_path / "review"
    if failed:
        with pytest.raises(p.PublicationError, match="terminal success"):
            monthly.collect(execution, output)
        assert not output.exists()
        return
    monthly.collect(execution, output)
    blob.download_as_bytes.assert_called_once_with(if_generation_match=generation, checksum="crc32c")
    assert (output / f"docs/wdpa-processing-evidence/{execution}/build-bundle.json").read_bytes() == raw
    evidence = json.loads((output / "catalog/wdpa-processing-acceptance.json").read_text())
    assert evidence["promotion_pr"] is None  # Collected bytes grant no authority.
    monkeypatch.setattr(gate, "ROOT", output)
    assert gate.check(evidence) == ["the merged PR approving this build promotion must be identified"]
    evidence["promotion_pr"] = 123
    assert not gate.check(evidence)
    assert not retained_execution_errors(evidence, {"execution": facts})


def test_replay_passes_one_explicit_month_to_real_cli_and_reads_its_report(monkeypatch, tmp_path):
    import subprocess
    directory = tmp_path / "output"
    expected = {"state": "succeeded", "run_date": "2026-11-01"}
    def command(argv, *, check):
        assert check and argv[1] == "scripts/local_wdpa_sample.py"
        assert argv[argv.index("--run-date") + 1] == expected["run_date"]
        assert "--stage-build" in argv and "--genesis" not in argv
        directory.mkdir()
        (directory / "benchmark.json").write_text(json.dumps(expected))
    monkeypatch.setattr(subprocess, "run", command)
    assert producer.replay(tmp_path / "source.zip", tmp_path / "inputs", directory,
        expected["run_date"], sample=False) == expected


def test_build_job_cannot_start_without_explicit_month(monkeypatch):
    monkeypatch.delenv("RUN_DATE", raising=False)
    client = Mock()
    monkeypatch.setattr(producer.storage, "Client", client)
    with pytest.raises(ValueError):
        producer.run()
    client.assert_not_called()


def test_reviewed_month_selects_publication_and_builder_iam_has_its_owner():
    from scripts.ci_contract import select_deployments
    assert "wdpa" in select_deployments(["catalog/wdpa-processing-acceptance.json"])
    assert select_deployments(["terraform/envs/prod/wdpa_monthly_build.tf"]) == ["ingestion_iam"]


def test_builder_iam_cannot_write_canonical_objects():
    from test_scheduled_ingestion_iam_tf import terraform_resource_block
    root = Path(__file__).resolve().parents[1]
    tf = (root / "terraform/envs/prod/wdpa_monthly_build.tf").read_text()
    role = terraform_resource_block(tf, "google_project_iam_custom_role", "wdpa_monthly_build_stager")
    assert 'permissions = ["storage.folders.create", "storage.objects.create"]' in role
    binding = terraform_resource_block(tf, "google_storage_bucket_iam_member", "wdpa_monthly_build_stager")
    assert "/objects/_scratch/wdpa-builds/wdpa-monthly-build-" in binding
    assert "/folders/_scratch/wdpa-builds/wdpa-monthly-build-" in binding
    assert "100-geographic" not in binding
    assert "roles/storage.objectUser" not in tf


@pytest.mark.parametrize("defect", [None, "canonical-job", "delete", "writer", "image", "command", "date", "memory", "disk", "tasks"])
def test_saved_plan_refuses_changes_outside_scratch_builder_contract(defect):
    image = f"us-central1-docker.pkg.dev/{monthly.PROJECT}/shared-datasets-jobs/wdpa-monthly-build@sha256:" + "a" * 64
    config = "sha256:" + "b" * 64
    task = {"service_account": f"wdpa-monthly-build@{monthly.PROJECT}.iam.gserviceaccount.com",
        "timeout": "86400s", "max_retries": 0,
        "volumes": [{"name": "work", "empty_dir": [{"medium": "DISK", "size_limit": "100Gi"}]}],
        "containers": [{"image": image, "command": ["python", "-m", "ingestion.wdpa_monthly.build"],
            "resources": [{"limits": {"cpu": "4", "memory": "8Gi"}}],
            "volume_mounts": [{"name": "work", "mount_path": "/work"}],
            "env": [{"name": name, "value": value} for name, value in {
                "GOOGLE_CLOUD_PROJECT": monthly.PROJECT, "TMPDIR": "/work/tmp",
                "SHARED_DATASETS_WORKDIR": "/work/shared-datasets-1",
                "WDPA_BUILD_IMAGE": image, "WDPA_BUILD_IMAGE_CONFIG_DIGEST": config}.items()]}]}
    job = {"name": "wdpa-monthly-build", "template": [{"task_count": 1, "parallelism": 1, "template": [task]}]}
    plan = {"resource_changes": [{"address": monthly.BUILD_JOB, "change": {"actions": ["create"], "after": job}}]}
    if defect == "canonical-job":
        plan["resource_changes"].append({"address": "module.wdpa_monthly_job.google_cloud_run_v2_job.this", "change": {"actions": ["update"]}})
    elif defect == "delete":
        plan["resource_changes"][0]["change"]["actions"] = ["delete", "create"]
    elif defect == "writer":
        task["service_account"] = "wdpa-monthly-job@shared-datasets-1.iam.gserviceaccount.com"
    elif defect in {"image", "command"}:
        task["containers"][0][defect] = "wrong"
    elif defect == "date":
        task["containers"][0]["env"].append({"name": "RUN_DATE", "value": "2026-10-01"})
    elif defect == "memory":
        task["containers"][0]["resources"][0]["limits"]["memory"] = "16Gi"
    elif defect == "disk":
        task["volumes"][0]["empty_dir"][0]["medium"] = "MEMORY"
    elif defect == "tasks":
        job["template"][0]["task_count"] = 2
    if defect:
        with pytest.raises(p.PublicationError):
            monthly.check_plan(plan, image, config)
    else:
        monthly.check_plan(plan, image, config)


def test_workflow_keeps_build_and_publication_authority_separate():
    import yaml
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/wdpa-monthly-build.yml").read_text())
    job = workflow["jobs"]["build"]
    assert job["environment"] == "shared-datasets-production"
    assert job["concurrency"] == {"group": "prod-terraform-state", "queue": "max", "cancel-in-progress": False}
    steps = job["steps"]
    commands = "\n".join(step.get("run", "") for step in steps)
    assert commands.index("verify --bootstrap") < commands.index("docker push")
    assert commands.index("check-plan") < commands.index("--plan-json \"$RUNNER_TEMP/wdpa-build.tfplan.json\"", commands.index("deployment_permissions.py --target wdpa-monthly-build"))
    assert "ingestion.wdpa_monthly.publication_only" not in commands
    assert "--update-env-vars=\"RUN_DATE=$RUN_DATE\" --async" in commands
    resume = yaml.safe_load((root / ".github/workflows/ingestion-schedule-control.yml").read_text())
    steps = resume["jobs"]["control"]["steps"]
    checks = next(step for step in steps if step.get("name") == "Verify publication before resuming")["run"]
    assert "wdpa_build_authorization.py" in checks and "wdpa_monthly_build.py readiness" in checks
