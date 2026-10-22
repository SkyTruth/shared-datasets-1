"""The protected producer's real serialized config, seed and plan boundaries."""

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from scripts import monitoring_slack_deploy as deploy

ROOT = Path(__file__).resolve().parents[1]
IMAGE = (
    "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/monitoring-slack-relay@sha256:"
    + "a" * 64
)


class Response:
    status_code = 200

    def __init__(self, value, status=200):
        self.value = value
        self.status_code = status

    def json(self):
        return self.value


class API:
    def __init__(self, *, versions=None, foreign=False, installed=None):
        self.posts = []
        self.versions = versions or {}
        self.foreign = foreign
        self.installed = installed

    def get(self, url, **kwargs):
        assert kwargs == {"timeout": 30, "allow_redirects": False}
        if url.startswith("https://run.googleapis.com/v2/"):
            return self.installed or Response(
                {"error": {"code": 404, "status": "NOT_FOUND"}}, 404
            )
        if "/versions?" in url:
            return Response(self.versions)
        if "secretmanager.googleapis.com" in url:
            return Response({"name": deploy.SECRET})
        name = url.removeprefix("https://monitoring.googleapis.com/v3/")
        value = {
            "name": name if not self.foreign else "projects/foreign/alertPolicies/123",
            "enabled": True,
            "conditions": [{"conditionMatchedLog": {"filter": "severity=ERROR"}}],
        }
        if name == deploy.registry()["policies"]["health"]:
            value.update(
                creationRecord={"mutateTime": "2026-10-22T04:59:54.149734439Z"},
                conditions=[
                    {
                        "conditionAbsent": {
                            "duration": "1800s",
                            "filter": 'resource.type="global" AND resource.labels.project_id="shared-datasets-1" AND metric.type="custom.googleapis.com/shared_datasets/operational_reconciliation_heartbeat_v2"',
                        }
                    }
                ],
            )
        return Response(value)

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return Response({"name": deploy.SECRET + "/versions/1", "state": "ENABLED"})


def test_config_producer_emits_the_consumers_utc_contract():
    value = deploy.prepare_config(
        API(), IMAGE, "1", now=datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc)
    )
    assert value["activated_at"] == "2026-10-22T14:00:00Z"
    assert value["secret_version"] == "1" and value["image"] == IMAGE
    assert value["health_policy_name"] == deploy.registry()["policies"]["health"]


def test_real_cli_serializes_only_nonsecret_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(deploy, "session", API)
    environment = tmp_path / "environment"
    outputs = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_ENV", str(environment))
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    deploy.main(["--prepare-config", "--secret-version", "1", "--image", IMAGE])
    line = environment.read_text().strip()
    assert line.startswith("TF_VAR_monitoring_slack_relay=")
    value = json.loads(line.split("=", 1)[1])
    assert value["activated_at"].endswith("Z")
    assert set(value) == {
        "image",
        "secret_version",
        "slack_channel",
        "slack_team",
        "slack_user",
        "health_policy_name",
        "health_policy_created_at",
        "unapproved_write_policy_name",
        "deletion_policy_name",
        "activated_at",
    }
    assert outputs.read_text().startswith("config_sha256=") and "xoxb-" not in line


def test_seed_is_one_exact_request_and_never_a_terraform_value():
    api = API()
    assert deploy.seed_bot_secret(api, "xoxb-offline-test") == "1"
    assert len(api.posts) == 1
    url, request = api.posts[0]
    assert (
        url
        == "https://secretmanager.googleapis.com/v1/" + deploy.SECRET + ":addVersion"
    )
    assert request["timeout"] == 30 and request["allow_redirects"] is False
    assert set(request["json"]) == {"payload"}


@pytest.mark.parametrize(
    "versions",
    [
        {"versions": [{"name": deploy.SECRET + "/versions/1"}]},
        {"nextPageToken": "partial"},
    ],
)
def test_existing_or_incomplete_seed_metadata_cannot_authorize_replay(versions):
    api = API(versions=versions)
    with pytest.raises(ValueError, match="do not blindly"):
        deploy.seed_bot_secret(api, "xoxb-offline-test")
    assert api.posts == []


@pytest.mark.parametrize(
    "image,version", [("mutable:latest", "1"), (IMAGE, "latest"), (IMAGE, "0")]
)
def test_mutable_configuration_is_rejected_before_external_access(image, version):
    with pytest.raises(ValueError):
        deploy.prepare_config(object(), image, version)


def test_forged_policy_cannot_enable_a_worker():
    with pytest.raises(ValueError, match="missing or disabled"):
        deploy.prepare_config(API(foreign=True), IMAGE, "1")


def test_explicit_version_reuse_enumerates_all_pages_without_mutation():
    class Pages(API):
        def get(self, url, **kwargs):
            if "pageToken=second" in url:
                return Response(
                    {
                        "versions": [
                            {"name": deploy.SECRET + "/versions/2", "state": "ENABLED"}
                        ]
                    }
                )
            return super().get(url, **kwargs)

    api = Pages(
        versions={
            "versions": [{"name": deploy.SECRET + "/versions/1", "state": "DISABLED"}],
            "nextPageToken": "second",
        }
    )
    assert deploy.confirm_bot_secret_version(api, "2") == "2"
    assert api.posts == []


@pytest.mark.parametrize(
    "versions,version",
    [
        (
            {
                "versions": [
                    {"name": deploy.SECRET + "/versions/1", "state": "DISABLED"}
                ]
            },
            "1",
        ),
        ({"versions": []}, "1"),
        (
            {
                "versions": [
                    {
                        "name": "projects/foreign/secrets/other/versions/1",
                        "state": "ENABLED",
                    }
                ]
            },
            "1",
        ),
        (
            {
                "versions": [
                    {"name": deploy.SECRET + "/versions/1", "state": "ENABLED"}
                ],
                "nextPageToken": "loop",
            },
            "1",
        ),
        ({}, "latest"),
    ],
)
def test_uncertain_or_unpinned_reuse_refuses_without_mutation(versions, version):
    api = API(versions=versions)
    with pytest.raises(ValueError):
        deploy.confirm_bot_secret_version(api, version)
    assert api.posts == []


def installed_job():
    from services.monitoring_slack.model import Config
    from tests.test_monitoring_slack_relay_plan import _ACTUAL_HCL_PLAN, JOB, after

    task = after(_ACTUAL_HCL_PLAN, JOB)["template"][0]["template"][0]
    value = Config.parse(json.loads(task["containers"][0]["env"][0]["value"])).value
    value.update(
        {
            key: deploy.registry()[key]
            for key in ("slack_channel", "slack_team", "slack_user")
        }
    )
    return {
        "name": f"projects/{deploy.PROJECT}/locations/{deploy.REGION}/jobs/monitoring-slack-relay",
        "template": {
            "template": {
                "containers": [
                    {
                        "image": value["relay_image"],
                        "env": [
                            {
                                "name": "MONITORING_SLACK_CONFIG",
                                "value": json.dumps(value),
                            }
                        ]
                    }
                ]
            }
        },
    }


def test_redeploy_preserves_exact_persisted_activation():
    api = API(installed=Response(installed_job()))
    value = deploy.prepare_config(
        api, IMAGE, "7", now=datetime(2026, 10, 22, 15, 0, tzinfo=timezone.utc)
    )
    assert value["activated_at"] == "2026-10-22T14:00:00Z"
    assert api.posts == []


def test_compatible_new_image_keeps_existing_activation_and_delivery_identity():
    api = API(installed=Response(installed_job()))
    newer = IMAGE[:-1] + "b"
    value = deploy.prepare_config(
        api, newer, "7", now=datetime(2026, 10, 22, 15, 0, tzinfo=timezone.utc)
    )
    assert value["image"] == newer
    assert value["activated_at"] == "2026-10-22T14:00:00Z"
    assert api.posts == []


def test_installed_configuration_cannot_impersonate_another_sender_image():
    job = installed_job()
    job["template"]["template"]["containers"][0]["image"] = IMAGE[:-1] + "b"
    with pytest.raises(ValueError, match="installed relay identity changed"):
        deploy.prepare_config(API(installed=Response(job)), IMAGE, "7")


@pytest.mark.parametrize(
    "which", ["identity", "version", "permission", "unstructured-absence"]
)
def test_redeploy_cannot_reset_or_adopt_another_ledger(which):
    job = installed_job()
    status = 200
    version = "7"
    if which == "identity":
        entry = job["template"]["template"]["containers"][0]["env"][0]
        value = json.loads(entry["value"])
        value["slack_user"] = "UFOREIGN1"
        entry["value"] = json.dumps(value)
    elif which == "version":
        version = "8"
    elif which == "permission":
        status, job = 403, {"error": {"code": 403, "status": "PERMISSION_DENIED"}}
    else:
        status, job = 404, {"error": {"code": 404}}
    api = API(installed=Response(job, status))
    with pytest.raises(ValueError):
        deploy.prepare_config(
            api, IMAGE, version, now=datetime(2026, 10, 22, 15, 0, tzinfo=timezone.utc)
        )
    assert api.posts == []


def test_existing_secret_readiness_never_demands_expired_creation_authority():
    from scripts.deployment_permissions import checks
    from scripts.terraform_plan_permissions import plan_checks

    permissions = {
        permission
        for _, values in checks("monitoring-slack-secret-setup")
        for permission in values
    }
    assert "secretmanager.secrets.create" not in permissions
    row = {
        "address": "google_secret_manager_secret.monitoring_slack_bot_token",
        "type": "google_secret_manager_secret",
        "change": {
            "actions": ["no-op"],
            "before": {"project": deploy.PROJECT, "secret_id": deploy.SECRET_ID},
            "after": {"project": deploy.PROJECT, "secret_id": deploy.SECRET_ID},
        },
    }
    plan = {"format_version": "1.2", "resource_changes": [row]}
    assert not any(
        "secretmanager.secrets.create" in values
        for _, values in plan_checks(plan, project_number="12695949518")
    )
    row["change"]["actions"] = ["create"]
    assert any(
        "secretmanager.secrets.create" in values
        for _, values in plan_checks(plan, project_number="12695949518")
    )


@pytest.mark.parametrize(
    "last_page", [{"error": {"code": 500}}, {"unknown": []}, {"totalSize": "2"}]
)
def test_selected_version_cannot_hide_an_incomplete_final_page(last_page):
    class Pages(API):
        def get(self, url, **kwargs):
            if "pageToken=second" in url:
                return Response(last_page)
            return super().get(url, **kwargs)

    api = Pages(
        versions={
            "versions": [{"name": deploy.SECRET + "/versions/1", "state": "ENABLED"}],
            "nextPageToken": "second",
        }
    )
    with pytest.raises(ValueError):
        deploy.confirm_bot_secret_version(api, "1")
    assert api.posts == []


@pytest.mark.parametrize(
    "versions", [{"error": {"code": 500}}, {"totalSize": 2}, {"totalSize": False}]
)
def test_malformed_or_nonempty_inventory_never_authorizes_initial_seed(versions):
    api = API(versions=versions)
    with pytest.raises(ValueError):
        deploy.seed_bot_secret(api, "xoxb-offline-test")
    assert api.posts == []
