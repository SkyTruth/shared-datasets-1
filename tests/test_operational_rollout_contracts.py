"""The default reviewable cutover is dormant and cannot weaken native safety."""
import json
from pathlib import Path

from scripts import ci_contract, deployment_permissions, release_contracts

ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT / "terraform/envs/prod"


def test_cutover_default_is_native_without_fabricated_evidence():
    assert json.loads((ROOT / "catalog/operational-slack-rollout.json").read_text()) == {
        "schema": "shared-datasets-operational-slack-rollout-v1", "mode": "native", "cutover_evidence": None,
        "retire_runtime_webhook_access": False, "retirement_evidence": None,
    }
    assert list((ROOT / ".github/operational-cutovers").glob("*.json")) == []


def test_only_operational_policies_have_gated_native_routes():
    text = (PROD / "monitoring.tf").read_text()
    for name in ("scheduled_ingestion_cloud_run_failure", "wdpa_execution_observer_unhealthy", "release_awaiting_identity_decision", "scheduled_ingestion_scheduler_failure"):
        body = text.split(f'resource "google_monitoring_alert_policy" "{name}"', 1)[1].split('\nresource ', 1)[0]
        assert "notification_channels = local.operational_alert_notification_channels" in body
        assert "enabled      = var.cron_alerts_enabled" in body
    for name in ("dataset_object_deleted", "dataset_object_written_by_unapproved_principal"):
        body = text.split(f'resource "google_monitoring_alert_policy" "{name}"', 1)[1].split('\nresource ', 1)[0]
        assert "notification_channels = local.cron_alert_notification_channels" in body
    health = (PROD / "operational_observer.tf").read_text().split('resource "google_monitoring_alert_policy" "operational_reconciliation_unhealthy"', 1)[1]
    assert 'duration = "1800s"' in health
    assert "notification_channels = local.cron_alert_notification_channels" in health


def test_mode_is_deployed_to_all_bridge_jobs_and_secret_access_is_independently_gated():
    for name in ("wdpa_monthly", "eamlis_monthly", "sea_ice_daily", "mpatlas_monthly"):
        body = (PROD / (name + ".tf")).read_text()
        assert "SHARED_DATASETS_OPERATIONAL_DELIVERY_MODE = local.operational_slack_rollout.mode" in body
        if name in {"wdpa_monthly", "eamlis_monthly"}:
            assert "secret_env = local.operational_slack_rollout.retire_runtime_webhook_access ? {} : {" in body
    grants = (PROD / "translation_notices.tf").read_text()
    assert "for_each = local.operational_slack_rollout.retire_runtime_webhook_access ? {} : local.translation_notice_jobs" in grants


def test_read_only_gate_permission_is_probed_without_adding_metric_write_authority():
    permissions = release_contracts.role_permissions((PROD / "monitoring_alert_policy_iam.tf").read_text(), "monitoring_alert_policy_manager")
    assert "monitoring.timeSeries.list" in permissions
    assert "monitoring.timeSeries.create" not in permissions
    probes = set(next(iter(deployment_permissions.checks("monitoring-alerts")))[1])
    assert "monitoring.timeSeries.list" in probes
    views = deployment_permissions.checks("monitoring-alerts")[1:]
    assert len(views) == 8 and all(permissions == ("logging.views.get",) for _, permissions in views)
    from scripts.operational_log_views import VIEWS
    assert {url for url, _ in views} == {f"https://logging.googleapis.com/v2/{name}:testIamPermissions" for name in VIEWS}


def test_rollout_and_evidence_changes_select_both_policy_and_runtime_protected_paths():
    expected = {"wdpa", "eamlis", "mpatlas", "sea_ice", "ingestion_iam", "cron_alert_policy", "artifact_registry_iam"}
    for path in ("catalog/operational-slack-rollout.json", ".github/operational-cutovers/reviewed.json"):
        assert set(ci_contract.select_deployments([path])) == expected
