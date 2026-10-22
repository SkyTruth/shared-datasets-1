"""Native routing drift at the serialized policy boundary, without live APIs.

Filters captured from the exact policy HCL with Terraform 1.8.5 / Google 7.31
offline mock-provider planning. This is API-input evidence, not a live apply.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts import monitoring_routing_audit as routing
from test_monitoring_routing_audit import fixture, CHANNEL


ROOT = Path(__file__).resolve().parents[1]
RENDERED = {
    "google_monitoring_alert_policy.scheduled_ingestion_cloud_run_failure": '''resource.type="cloud_run_job"
resource.labels.project_id="shared-datasets-1"
resource.labels.location="us-central1"
NOT resource.labels.job_name="wdpa-execution-observer"
NOT resource.labels.job_name="monitoring-slack-relay"
severity>=ERROR
protoPayload.serviceName="run.googleapis.com"
protoPayload.methodName="/Jobs.RunJob"
protoPayload.status.code>0
NOT (
  protoPayload.response.metadata.annotations."run.googleapis.com/creator"="shared-datasets-terraform@shared-datasets-1.iam.gserviceaccount.com"
  AND protoPayload.response.spec.template.spec.containers.env.name="SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL"
)
''',
    "google_monitoring_alert_policy.scheduled_ingestion_scheduler_failure": '''resource.type="cloud_scheduler_job"
resource.labels.project_id="shared-datasets-1"
resource.labels.location="us-central1"
NOT resource.labels.job_id="wdpa-execution-observer"
NOT resource.labels.job_id="monitoring-slack-relay"
severity>=ERROR
''',
}


def reviewed_inventory():
    plan, live, channels = fixture()
    for index, (address, query) in enumerate(RENDERED.items(), 100):
        value = {"project": routing.PROJECT, "display_name": address, "enabled": True, "combiner": "OR",
                 "notification_channels": [CHANNEL], "conditions": [{"condition_matched_log": [{"filter": query}]}]}
        plan["planned_values"]["root_module"]["resources"].append({"address": address,
            "type": "google_monitoring_alert_policy", "values": value})
        plan["configuration"]["root_module"]["resources"].append({"address": address, "type": "google_monitoring_alert_policy"})
        live.append({"name": f"projects/{routing.PROJECT}/alertPolicies/{index}", "displayName": address,
                     "enabled": True, "combiner": "OR", "notificationChannels": [CHANNEL],
                     "conditions": [{"conditionMatchedLog": {"filter": query}}]})
    return plan, live, channels


def test_real_rendered_filters_exclude_two_exact_workers_and_keep_future_jobs_observable():
    text = (ROOT / "terraform/envs/prod/monitoring.tf").read_text()
    replacements = {"var.project_id": "shared-datasets-1", "var.region": "us-central1",
                    "local.wdpa_execution_observer_job_name": "wdpa-execution-observer",
                    "local.monitoring_slack_job_name": "monitoring-slack-relay",
                    "var.github_actions_terraform_service_account_email": "shared-datasets-terraform@shared-datasets-1.iam.gserviceaccount.com"}
    for address, rendered in RENDERED.items():
        body = text.split('resource "google_monitoring_alert_policy" "' + address.split(".")[-1] + '"', 1)[1].split("\nresource ", 1)[0]
        source_filter = body.split("filter = <<-EOT\n", 1)[1].split("EOT", 1)[0]
        for name, value in replacements.items():
            source_filter = source_filter.replace("${" + name + "}", value)
        assert source_filter == rendered
        field = "job_name" if "cloud_run" in address else "job_id"
        assert [line for line in rendered.splitlines() if "resource.labels." + field in line] == [
            f'NOT resource.labels.{field}="wdpa-execution-observer"',
            f'NOT resource.labels.{field}="monitoring-slack-relay"',
        ]
        assert "=~" not in rendered
    # These exact clauses do not enter security or primary pipeline-health
    # detection, and introduce no dependency on a relay Job/module instance.
    assert text.count("local.monitoring_slack_job_name") == 2
    assert "module.monitoring_slack_job" not in text


@pytest.mark.parametrize("address", list(RENDERED))
@pytest.mark.parametrize("change", ["missing", "prefix"])
def test_live_missing_or_broadened_relay_exclusion_is_reported(address, change, tmp_path, capsys):
    plan, policies, channels = reviewed_inventory()
    assert routing.audit(plan, policies, channels)["findings"] == []
    actual = next(policy for policy in policies if policy["displayName"] == address)
    query = actual["conditions"][0]["conditionMatchedLog"]["filter"]
    field = "job_name" if "cloud_run" in address else "job_id"
    exact = f'NOT resource.labels.{field}="monitoring-slack-relay"\n'
    changed = query.replace(exact, "" if change == "missing" else f'NOT resource.labels.{field}=~"monitoring-slack-.*"\n')
    actual["conditions"][0]["conditionMatchedLog"]["filter"] = changed
    paths = [tmp_path / name for name in ("plan.json", "policies.json", "channels.json")]
    for path, value in zip(paths, (deepcopy(plan), {"alertPolicies": policies}, {"notificationChannels": channels})):
        path.write_text(json.dumps(value))
    assert routing.main(["--terraform-json", str(paths[0]), "--policies-json", str(paths[1]), "--channels-json", str(paths[2])]) == 1
    assert json.loads(capsys.readouterr().out)["findings"] == [{"code": "detection_drift", "identity": address,
        "detail": "Live filters or condition configuration differ from the reviewed plan"}]
