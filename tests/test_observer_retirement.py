"""The temporary retirement probes only the installed observer's exact grants."""
import pytest

from scripts.terraform_plan_permissions import PROJECT, plan_checks


def saved_plan(**overrides):
    value = {
        "parent": f"projects/{PROJECT}", "location": "global", "bucket": "_Default",
        "name": f"projects/{PROJECT}/locations/global/buckets/_Default/views/shared-datasets-operations-run-activity", "role": "roles/logging.viewAccessor",
        "member": f"serviceAccount:shared-datasets-alert-observer@{PROJECT}.iam.gserviceaccount.com",
    }
    value.update(overrides)
    return {"format_version": "1.2", "resource_changes": [{"address": "google_logging_log_view_iam_member.operational_observer",
        "type": "google_logging_log_view_iam_member", "change": {"actions": ["delete"], "before": value, "after": None}}]}


@pytest.mark.parametrize("bucket", ["_Default", "_Required"])
@pytest.mark.parametrize("family", ["run-activity", "run-data-access", "run-stderr", "scheduler-executions"])
def test_retirement_checks_exact_existing_view(bucket, family):
    name = f"projects/{PROJECT}/locations/global/buckets/{bucket}/views/shared-datasets-operations-{family}"
    result = plan_checks(saved_plan(bucket=bucket, name=name), project_number="12695949518")
    assert result == [(f"https://logging.googleapis.com/v2/projects/{PROJECT}/locations/global/buckets/{bucket}/views/shared-datasets-operations-{family}:testIamPermissions",
                      ("logging.views.getIamPolicy", "logging.views.setIamPolicy"))]


@pytest.mark.parametrize("change", [
    {"parent": "projects/foreign"}, {"location": "us-central1"}, {"bucket": "another-bucket"},
    {"name": "_AllLogs"}, {"role": "roles/owner"}, {"member": "allUsers"},
])
def test_retirement_rejects_foreign_grant(change):
    with pytest.raises(ValueError):
        plan_checks(saved_plan(**change), project_number="12695949518")


def test_retirement_cannot_create_a_grant():
    value = saved_plan()
    row = value["resource_changes"][0]
    row["change"] = {"actions": ["create"], "after": row["change"]["before"], "before": None}
    with pytest.raises(ValueError, match="only revokes"):
        plan_checks(value, project_number="12695949518")
