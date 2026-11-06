"""Prove the new cron's protected deployment and exact bootstrap scope."""
from pathlib import Path
import re
from types import SimpleNamespace
from unittest import mock

import pytest

from scripts import ci_contract, deployment_revision, terraform_plan_allowlist as allowlist
from workflow_helpers import load_workflow
from test_ingestion_deploy_workflow import target_table

ROOT = Path(__file__).resolve().parents[1]
JOB = "module.mpatlas_monthly_job.google_cloud_run_v2_job.this"
SCHEDULER = "module.mpatlas_monthly_scheduler.google_cloud_scheduler_job.this"
INVOKER = "google_cloud_run_v2_job_iam_member.mpatlas_scheduler_invoker"


def test_new_cron_has_exact_deployment_scope_and_consumes_tested_image():
    assert set(target_table()['mpatlas-monthly']['targets']) == {JOB, SCHEDULER, INVOKER, "google_iam_deny_policy.canonical_destructive_actions[0]"}
    targets = set(ci_contract.select_deployments(["ingestion/mpatlas_monthly/run.py"]))
    assert targets == {"mpatlas"}
    caller = load_workflow(ROOT / ".github/workflows/ci.yml")["jobs"]["mpatlas"]
    assert "cron-alert-policy" in caller["needs"] and "ingestion-iam" in caller["needs"]
    tf = (ROOT / "terraform/envs/prod/mpatlas_monthly_variables.tf").read_text()
    assert 'default     = "0 11 2 * *"' in tf
    tf = (ROOT / "terraform/envs/prod/mpatlas_monthly.tf").read_text()
    assert 'time_zone             = "UTC"' in tf
    assert "130-protected-areas/mpatlas-zone-assessments/" in tf
    assert "_catalog/releases/mpatlas-zone-assessments.json" in tf


@pytest.mark.parametrize("actions,image,allowed", [
    (["create"], "expected@sha256:abc", True),
    (["update"], "expected@sha256:abc", True),
    (["create"], "wrong", False),
    (["delete", "create"], "expected@sha256:abc", False),
    (["delete"], "expected@sha256:abc", False),
])
def test_first_install_image_check_does_not_allow_wrong_images_or_replacements(actions, image, allowed):
    plan = {"resource_changes": [{"address": JOB, "change": {"actions": actions, "after": {
        "template": [{"template": [{"containers": [{"image": image}]}]}],
    }}}]}
    blocked = allowlist.blocked_changes(plan, allowed_exact={JOB}, allowed_patterns=[],
        block_deletes=True, job_image=(JOB, "expected@sha256:abc"))
    assert (not blocked) == allowed
    plan["resource_changes"][0]["address"] = "module.eamlis_monthly_job.google_cloud_run_v2_job.this"
    assert allowlist.blocked_changes(plan, allowed_exact={JOB}, allowed_patterns=[], block_deletes=True)


def test_first_install_window_requires_successful_regional_inventory(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    args = SimpleNamespace(target="mpatlas-monthly", canary_run_date="", allow_cancel="false")
    with mock.patch.object(deployment_revision.subprocess, "check_output", return_value=b"[]") as read:
        deployment_revision.runtime_window(args)
        assert read.call_count == 1
    assert (tmp_path / "output").read_text() == "ready=true\n"
    with mock.patch.object(deployment_revision.subprocess, "check_output", side_effect=RuntimeError("denied")):
        with pytest.raises(RuntimeError, match="denied"):
            deployment_revision.runtime_window(args)


def test_monitoring_and_bootstrap_register_only_owned_mpatlas_identity():
    sync = load_workflow(ROOT / ".github/workflows/scheduled-ingestion-deploy-iam-sync.yml")["jobs"]["sync"]["with"]
    for name in ("targets", "allowed_exact"):
        entries = sync[name].splitlines()
        assert 'module.mpatlas_job_service_account.google_service_account.this' in entries
        assert 'module.mpatlas_scheduler_service_account.google_service_account.this' in entries
        assert 'google_service_account_iam_member.mpatlas_deployer_act_as["job"]' in entries
    canonical = (ROOT / "terraform/envs/prod/canonical_mutation_iam.tf").read_text()
    assert len(re.findall(r'module\.mpatlas_job_service_account\.email', canonical)) == 2


@pytest.mark.parametrize('slug,seconds', [('mpatlas_monthly', '3600s'), ('eamlis_monthly', '3600s'), ('sea_ice_daily', '3600s'), ('wdpa_monthly', '46800s')])
def test_task_timeout_bounds_preserve_wdpa_batch_window(slug, seconds):
    import hcl2
    with (ROOT / 'terraform/envs/prod' / (slug + '.tf')).open() as stream:
        config = hcl2.load(stream, serialization_options=hcl2.utils.SerializationOptions(strip_string_quotes=True))
    job = next(module[slug + '_job'] for module in config['module'] if slug + '_job' in module)
    assert job['timeout'] == seconds
