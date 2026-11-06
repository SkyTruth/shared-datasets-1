import copy
from unittest.mock import Mock, patch

import pytest

from scripts import deployment_revision as deployment
from scripts import wdpa_schedule_readiness as readiness

IMAGE = "registry/wdpa@sha256:" + "a" * 64
CONFIG = "sha256:" + "b" * 64
RECORD = {"id": 2}
PAYLOAD = {"target": "wdpa-monthly", "artifact": IMAGE}
STATUS = {
    "state": "success", "description": "verified",
    "environment_url": "https://console.cloud.google.com/run/jobs/executions/details/"
                       "us-central1/wdpa-canary?project=shared-datasets-1",
}


def worker():
    return {"spec": {"template": {"spec": {"template": {"spec": {"containers": [{
        "image": IMAGE, "env": [
            {"name": "WDPA_BUILD_IMAGE", "value": IMAGE},
            {"name": "WDPA_BUILD_IMAGE_CONFIG_DIGEST", "value": CONFIG},
        ],
    }]}}}}}}


def execution():
    return {"spec": {"template": {"spec": {"containers": [{"image": IMAGE}]}}},
            "status": {"completionTime": "2026-11-01T12:00:00Z", "succeededCount": 1}}


def check(job=None, *, records=None, payload=None, status=None, canary=None):
    api = Mock()
    api.pages.return_value = records if records is not None else [RECORD, {"id": 1}]
    reader = Mock(return_value=canary if canary is not None else execution())
    with patch.object(deployment, "verify_record", return_value=payload or PAYLOAD) as verify, \
            patch.object(deployment, "verified_statuses", return_value=[STATUS if status is None else status]):
        readiness.require_ready(api, "SkyTruth/shared-datasets-1", job or worker(), reader)
        verify.assert_called_once_with(api, "SkyTruth/shared-datasets-1", RECORD)
    reader.assert_called_once_with("wdpa-canary")


def test_verified_automatic_worker_can_resume():
    check()


@pytest.mark.parametrize("name", ["RUN_DATE", "WDPA_PROMOTION_BUNDLE"])
def test_even_empty_monthly_pins_block_resume(name):
    job = worker()
    job["spec"]["template"]["spec"]["template"]["spec"]["containers"][0]["env"].append(
        {"name": name, "value": ""},
    )
    with pytest.raises(deployment.DeploymentError, match="persistent monthly"):
        check(job)


@pytest.mark.parametrize("field,value", [("WDPA_BUILD_IMAGE", "old"), ("WDPA_BUILD_IMAGE_CONFIG_DIGEST", "")])
def test_producer_configuration_must_match_live_image(field, value):
    job = worker()
    env = job["spec"]["template"]["spec"]["template"]["spec"]["containers"][0]["env"]
    next(entry for entry in env if entry["name"] == field)["value"] = value
    with pytest.raises(deployment.DeploymentError, match="automatic producer"):
        check(job)


def test_old_success_cannot_hide_a_new_pending_deployment():
    with pytest.raises(deployment.DeploymentError, match="signed verified"):
        check(status={**STATUS, "state": "in_progress", "description": "verification_pending"})


def test_verified_record_must_match_live_image():
    with pytest.raises(deployment.DeploymentError, match="differs"):
        check(payload={**PAYLOAD, "artifact": "registry/old@sha256:" + "c" * 64})


def test_missing_protected_deployment_blocks_resume():
    with pytest.raises(deployment.DeploymentError, match="no protected"):
        check(records=[])


@pytest.mark.parametrize("outcome", ["running", "failed", "wrong_image", "missing_identity"])
def test_canary_requires_terminal_exact_image_success(outcome):
    canary = copy.deepcopy(execution())
    status = STATUS.copy()
    if outcome == "running":
        canary["status"] = {}
    elif outcome == "failed":
        canary["status"]["failedCount"] = 1
    elif outcome == "wrong_image":
        canary["spec"]["template"]["spec"]["containers"][0]["image"] = "registry/old@sha256:" + "c" * 64
    else:
        status["environment_url"] = ""
    with pytest.raises(deployment.DeploymentError):
        check(canary=canary, status=status)


def test_unverified_receipt_cannot_authorize_resume():
    api = Mock()
    api.pages.return_value = [RECORD]
    with patch.object(deployment, "verify_record", side_effect=deployment.DeploymentError("invalid receipt")):
        with pytest.raises(deployment.DeploymentError, match="invalid receipt"):
            readiness.require_ready(api, "SkyTruth/shared-datasets-1", worker(), Mock())


@pytest.mark.parametrize('deferred_count', [1, 2])
def test_signed_deferrals_preserve_last_verified_live_image(deferred_count):
    api = Mock()
    deferred = [{'id': RECORD['id'] + n + 1} for n in range(deferred_count)]
    api.pages.return_value = [RECORD, *deferred]
    reader = Mock(return_value=execution())
    with patch.object(deployment, 'verify_record', side_effect=lambda api, repo, record: {
            **PAYLOAD, 'artifact': IMAGE if record == RECORD else 'registry/new@sha256:' + 'c' * 64}), \
            patch.object(deployment, 'verified_statuses', side_effect=lambda api, repo, record: [
                STATUS if record == RECORD else {'state': 'pending', 'description': 'deferred'}]):
        readiness.require_ready(api, 'SkyTruth/shared-datasets-1', worker(), reader)
    reader.assert_called_once_with('wdpa-canary')


def test_only_deferred_records_cannot_authorize_resume():
    with pytest.raises(deployment.DeploymentError, match='no .*verified'):
        check(status={'state': 'pending', 'description': 'deferred'})


def test_failed_status_cannot_be_skipped_as_a_deferral():
    with pytest.raises(deployment.DeploymentError, match='signed verified'):
        check(status={'state': 'failure', 'description': 'deferred'})
