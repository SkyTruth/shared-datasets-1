"""Constrain independent Monitoring relay preparation; never enables public delivery."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PROJECT = "shared-datasets-1"
REGION = "us-central1"
SECRET_ID = "shared-datasets-slack-incident-bot-token"
SECRET = f"projects/{PROJECT}/secrets/{SECRET_ID}"
RUNTIME = f"monitoring-slack-relay@{PROJECT}.iam.gserviceaccount.com"
SCHEDULER = f"monitoring-slack-scheduler@{PROJECT}.iam.gserviceaccount.com"
DEPLOYER = f"serviceAccount:shared-datasets-terraform@{PROJECT}.iam.gserviceaccount.com"
BUCKET = "skytruth-shared-datasets-1-monitoring-slack-state"
IMAGE = re.compile(
    r"us-central1-docker\.pkg\.dev/shared-datasets-1/shared-datasets-jobs/monitoring-slack-relay@sha256:[0-9a-f]{64}"
)
ROOT = Path(__file__).resolve().parents[1]
RESOURCES = frozenset(
    {
        "google_storage_bucket.monitoring_slack_state",
        "module.monitoring_slack_scheduler_service_account.google_service_account.this",
        "google_project_iam_custom_role.monitoring_slack_reader",
        "google_project_iam_member.monitoring_slack_reader",
        "google_project_iam_custom_role.monitoring_slack_state",
        "google_project_iam_custom_role.monitoring_slack_execution_reader",
        "google_storage_bucket_iam_member.monitoring_slack_state",
        "google_service_account_iam_member.monitoring_slack_deployer",
        "google_service_account_iam_member.monitoring_slack_scheduler_deployer",
        "google_secret_manager_secret_iam_member.monitoring_slack_bot_token[0]",
        "module.monitoring_slack_job[0].google_cloud_run_v2_job.this",
        "google_cloud_run_v2_job_iam_member.monitoring_slack_scheduler[0]",
        "google_cloud_run_v2_job_iam_member.monitoring_slack_execution_reader[0]",
        "module.monitoring_slack_scheduler[0].google_cloud_scheduler_job.this",
    }
)
READ_PERMISSIONS = {
    "monitoring.alerts.get",
    "monitoring.alerts.list",
    "monitoring.alertPolicies.get",
    "monitoring.timeSeries.list",
}
STATE_PERMISSIONS = {
    "storage.objects.get",
    "storage.objects.create",
    "storage.objects.delete",
}


def require(value, message):
    if not value:
        raise ValueError("MONITORING_SLACK_DEPLOY: " + message)


def registry(root=ROOT):
    value = json.loads((root / "catalog/monitoring-slack-relay.json").read_text())
    require(
        value["schema_version"] == 1 and value["project"] == PROJECT,
        "invalid routing registry",
    )
    require(
        set(value["policies"]) == {"health", "unapproved_write", "deletion"},
        "all three independent policies are required",
    )
    require(
        len(set(value["policies"].values())) == 3, "policy identities must be distinct"
    )
    require(
        all(
            re.fullmatch(f"projects/{PROJECT}/alertPolicies/[0-9]+", v)
            for v in value["policies"].values()
        ),
        "foreign policy",
    )
    require(
        re.fullmatch(r"C[A-Z0-9]+", value["slack_channel"])
        and re.fullmatch(r"T[A-Z0-9]+", value["slack_team"])
        and re.fullmatch(r"U[A-Z0-9]+", value["slack_user"]),
        "pinned bot identity is required",
    )
    return value


def session():
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return AuthorizedSession(credentials, max_refresh_attempts=0, refresh_timeout=30)


def get(api, url):
    response = api.get(url, timeout=30, allow_redirects=False)
    require(
        response.status_code == 200, f"read rejected with HTTP {response.status_code}"
    )
    value = response.json()
    require(isinstance(value, dict), "malformed metadata response")
    return value


def output(key, value, variable="GITHUB_OUTPUT"):
    require("\n" not in value and "\r" not in value, "workflow output must be one line")
    path = os.environ.get(variable)
    require(bool(path), "protected workflow output file is required")
    with Path(path).open("a") as stream:
        stream.write(f"{key}={value}\n")


def version_page(api, url):
    value = get(api, url)
    # ListSecretVersionsResponse: the protobuf empty-object form is valid,
    # but an HTTP200 error/object of another type is never empty inventory.
    require(
        value.keys() <= {"versions", "nextPageToken", "totalSize"}
        and isinstance(value.get("versions", []), list)
        and all(isinstance(row, dict) for row in value.get("versions", []))
        and isinstance(value.get("nextPageToken", ""), str)
        and type(value.get("totalSize", 0)) is int
        and value.get("totalSize", 0) >= 0,
        "incomplete version inventory",
    )
    return value


def seed_bot_secret(api, token):
    """One creation request only. Existing/uncertain versions require inspection."""
    require(
        isinstance(token, str) and token.startswith("xoxb-") and "\n" not in token,
        "configured bot token is required",
    )
    metadata = get(api, "https://secretmanager.googleapis.com/v1/" + SECRET)
    require(
        metadata.get("name", "").endswith("/secrets/" + SECRET_ID),
        "different secret metadata",
    )
    versions = version_page(
        api,
        "https://secretmanager.googleapis.com/v1/" + SECRET + "/versions?pageSize=100",
    )
    require(
        not versions.get("versions")
        and not versions.get("nextPageToken")
        and versions.get("totalSize", 0) == 0,
        "secret has existing versions; do not blindly append or replay an uncertain seed",
    )
    # Secret bytes go only to the exact existing protected resource, never to
    # Terraform, an argument, a workflow output or arbitrary response logging.
    response = api.post(
        "https://secretmanager.googleapis.com/v1/" + SECRET + ":addVersion",
        json={"payload": {"data": base64.b64encode(token.encode()).decode()}},
        timeout=30,
        allow_redirects=False,
    )
    require(
        response.status_code == 200,
        f"seed outcome unconfirmed, HTTP {response.status_code}; inspect before retry",
    )
    value = response.json()
    match = re.fullmatch(
        r"projects/(?:"
        + PROJECT
        + r"|[0-9]+)/secrets/"
        + SECRET_ID
        + r"/versions/([1-9][0-9]*)",
        value.get("name", ""),
    )
    require(
        match and value.get("state") == "ENABLED",
        "seed returned an unconfirmed version; inspect before retry",
    )
    return match[1]


def confirm_bot_secret_version(api, version):
    """Reuse only an explicitly inspected version; never seed or select latest."""
    require(
        isinstance(version, str) and re.fullmatch(r"[1-9][0-9]*", version),
        "explicit confirmed numeric secret version required",
    )
    secret = get(api, "https://secretmanager.googleapis.com/v1/" + SECRET)
    parent = secret.get("name", "")
    require(
        re.fullmatch(
            r"projects/(?:" + PROJECT + r"|[1-9][0-9]*)/secrets/" + SECRET_ID, parent
        ),
        "unexpected canonical secret identity",
    )
    expected = parent + "/versions/" + version
    versions, tokens, selected, page = set(), set(), None, None
    for _ in range(1000):
        query = urlencode({"pageSize": 100, **({"pageToken": page} if page else {})})
        value = version_page(
            api,
            "https://secretmanager.googleapis.com/v1/" + SECRET + "/versions?" + query,
        )
        rows = value.get("versions", [])
        require(isinstance(rows, list), "incomplete version inventory")
        for row in rows:
            require(
                isinstance(row, dict)
                and re.fullmatch(
                    re.escape(parent) + r"/versions/[1-9][0-9]*", row.get("name", "")
                ),
                "unexpected secret version identity",
            )
            name = row["name"]
            require(name not in versions, "duplicate secret version")
            versions.add(name)
            if name == expected:
                require(
                    row.get("state") == "ENABLED",
                    "confirmed secret version is not enabled",
                )
                selected = version
        page = value.get("nextPageToken", "")
        require(
            isinstance(page, str) and page not in tokens,
            "incomplete or looping version inventory",
        )
        if not page:
            require(selected is not None, "explicit confirmed version is absent")
            return selected
        tokens.add(page)
    raise ValueError("MONITORING_SLACK_DEPLOY: incomplete version inventory")


def preserve_activation(api, prepared):
    """An image redeploy cannot replace the durable incident-ledger identity."""
    from services.monitoring_slack.model import Config, instant

    parent = f"projects/{PROJECT}/locations/{REGION}/jobs/monitoring-slack-relay"
    response = api.get(
        "https://run.googleapis.com/v2/" + parent, timeout=30, allow_redirects=False
    )
    value = response.json()
    require(isinstance(value, dict), "malformed installed relay metadata")
    if response.status_code == 404:
        require(
            value.get("error", {}).get("status") == "NOT_FOUND"
            and value["error"].get("code") == 404,
            "unverified missing relay",
        )
        return prepared
    require(
        response.status_code == 200 and value.get("name") == parent,
        "installed relay read failed",
    )
    container = one(value["template"]["template"], "containers")
    env = one(container, "env")
    require(
        env.get("name") == "MONITORING_SLACK_CONFIG",
        "unexpected installed relay environment",
    )
    installed = Config.parse(json.loads(env["value"]))
    expected = {
        "schema": "monitoring-slack-config-v1",
        "project": PROJECT,
        "relay_job": parent,
        "relay_image": installed["relay_image"],
        "policies": [
            {
                "name": prepared[key + "_policy_name"],
                "kind": "health" if key == "health" else "security",
            }
            for key in ("health", "unapproved_write", "deletion")
        ],
        "health_metric": "custom.googleapis.com/shared_datasets/operational_reconciliation_heartbeat_v2",
        "health_policy_created_at": prepared["health_policy_created_at"],
        "state_bucket": BUCKET,
        "slack_secret_version": installed["slack_secret_version"],
        **{key: prepared[key] for key in ("slack_channel", "slack_team", "slack_user")},
        "activated_at": installed["activated_at"],
        "mode": "shadow",
    }
    require(
        re.fullmatch(
            r"projects/(?:"
            + PROJECT
            + r"|[1-9][0-9]*)/secrets/"
            + SECRET_ID
            + r"/versions/"
            + prepared["secret_version"],
            installed["slack_secret_version"],
        ),
        "credential identity change requires reviewed state migration",
    )
    require(
        container.get("image") == installed["relay_image"]
        and installed.value == Config.parse(expected).value
        and instant(installed["activated_at"]) <= instant(prepared["activated_at"]),
        "installed relay identity changed; preserve state through a reviewed migration",
    )
    return {**prepared, "activated_at": installed["activated_at"]}


def prepare_config(api, image, version, *, root=ROOT, now=None):
    require(IMAGE.fullmatch(image), "immutable tested image required")
    require(
        re.fullmatch(r"[1-9][0-9]*", version), "exact numeric secret version required"
    )
    r = registry(root)
    policies = {
        key: get(api, "https://monitoring.googleapis.com/v3/" + name)
        for key, name in r["policies"].items()
    }
    for key, policy in policies.items():
        require(
            policy.get("name") == r["policies"][key]
            and policy.get("enabled", True) is True,
            "reviewed independent policy is missing or disabled",
        )
    health = policies["health"]
    conditions = health.get("conditions", [])
    require(
        len(conditions) == 1
        and conditions[0].get("conditionAbsent", {}).get("duration") == "1800s",
        "independent 30-minute absence protection changed",
    )
    selector = conditions[0]["conditionAbsent"]["filter"]
    require(
        "operational_reconciliation_heartbeat_v2" in selector
        and PROJECT in selector
        and "global" in selector,
        "health policy is not the fenced project heartbeat",
    )
    require(
        all(
            len(p.get("conditions", [])) == 1
            and "conditionMatchedLog" in p["conditions"][0]
            for k, p in policies.items()
            if k != "health"
        ),
        "security detection must remain log-based",
    )
    return preserve_activation(
        api,
        {
            "image": image,
            "secret_version": version,
            **{k: r[k] for k in ("slack_channel", "slack_team", "slack_user")},
            "health_policy_name": r["policies"]["health"],
            "health_policy_created_at": health["creationRecord"]["mutateTime"],
            "unapproved_write_policy_name": r["policies"]["unapproved_write"],
            "deletion_policy_name": r["policies"]["deletion"],
            "activated_at": (now or datetime.now(timezone.utc))
            .isoformat()
            .replace("+00:00", "Z"),
        },
    )


def one(value, field):
    rows = value.get(field)
    require(
        isinstance(rows, list) and len(rows) == 1,
        "one " + field + " configuration is required",
    )
    return rows[0]


def validate_plan(plan, image, *, root=ROOT):
    require(IMAGE.fullmatch(image), "immutable tested image required")
    require(
        not plan.get("errored") and not plan.get("deferred_changes"), "incomplete plan"
    )
    rows = plan.get("resource_changes")
    require(isinstance(rows, list), "complete resource changes required")
    require(
        len({row["address"] for row in rows}) == len(rows), "duplicate resource change"
    )
    from services.monitoring_slack.model import Config

    r = registry(root)
    require(
        RESOURCES <= {row["address"] for row in rows},
        "all exact preparation resources are required",
    )
    secret_versions = set()
    worker_version = None
    required_fields = {
        "google_project_iam_custom_role": {"project", "role_id", "permissions"},
        "google_project_iam_member": {"project", "member", "role"},
        "google_storage_bucket": {
            "name",
            "project",
            "uniform_bucket_level_access",
            "public_access_prevention",
            "force_destroy",
            "versioning",
        },
        "google_storage_bucket_iam_member": {"bucket", "member", "role", "condition"},
        "google_service_account": {"project", "account_id"},
        "google_service_account_iam_member": {"member", "role", "service_account_id"},
        "google_secret_manager_secret_iam_member": {
            "project",
            "secret_id",
            "member",
            "role",
            "condition",
        },
        "google_cloud_run_v2_job": {"project", "location", "name", "template"},
        "google_cloud_run_v2_job_iam_member": {
            "project",
            "location",
            "name",
            "member",
            "role",
        },
        "google_cloud_scheduler_job": {
            "project",
            "region",
            "name",
            "paused",
            "schedule",
            "time_zone",
            "http_target",
            "retry_config",
        },
    }
    for row in rows:
        change = row["change"]
        actions = change["actions"]
        if actions == ["no-op"] and row["address"] not in RESOURCES:
            continue
        require(
            row["address"] in RESOURCES
            and actions in (["create"], ["update"], ["no-op"]),
            "mutation outside exact relay preparation scope or destructive replacement",
        )
        value = change.get("after")
        require(isinstance(value, dict), "resolved resource values required")
        kind = row["type"]
        require(
            kind == row["address"].split(".")[-2] and kind in required_fields,
            "resource address and provider type differ",
        )
        unknown = change.get("after_unknown", {})
        require(
            isinstance(unknown, dict) and required_fields[kind] <= value.keys(),
            "required preparation values are unresolved",
        )
        require(
            all(unknown.get(field) is not True for field in required_fields[kind]),
            "required preparation values are unknown",
        )
        if kind == "google_project_iam_custom_role":
            role_id, permissions = {
                "google_project_iam_custom_role.monitoring_slack_reader": (
                    "sharedDatasetsMonitoringSlackReader", READ_PERMISSIONS
                ),
                "google_project_iam_custom_role.monitoring_slack_state": (
                    "sharedDatasetsMonitoringSlackState", STATE_PERMISSIONS
                ),
                "google_project_iam_custom_role.monitoring_slack_execution_reader": (
                    "sharedDatasetsMonitoringSlackExecutionReader", {"run.executions.get"}
                ),
            }[row["address"]]
            require(
                value["project"] == PROJECT
                and value["role_id"] == role_id
                and set(value["permissions"]) == permissions,
                "runtime role identity or exact read/state capability changed",
            )
        elif kind == "google_project_iam_member":
            require(
                value["project"] == PROJECT
                and value["member"] == "serviceAccount:" + RUNTIME
                and value["role"]
                == f"projects/{PROJECT}/roles/sharedDatasetsMonitoringSlackReader",
                "unexpected runtime project grant",
            )
        elif kind == "google_storage_bucket":
            require(
                value["name"] == BUCKET
                and value["project"] == PROJECT
                and value["uniform_bucket_level_access"] is True
                and value["public_access_prevention"] == "enforced"
                and not value["force_destroy"],
                "state bucket must remain private and separate from datasets",
            )
            require(
                one(value, "versioning").get("enabled") is True,
                "state history must be preserved",
            )
        elif kind == "google_storage_bucket_iam_member":
            require(
                value["bucket"] == BUCKET
                and value["member"] == "serviceAccount:" + RUNTIME
                and value["role"]
                == f"projects/{PROJECT}/roles/sharedDatasetsMonitoringSlackState",
                "state grant exceeds its dedicated bucket",
            )
            condition = one(value, "condition")["expression"]
            prefix = f"projects/_/buckets/{BUCKET}/objects/"
            expected = (
                "resource.type == 'storage.googleapis.com/Object' && ("
                + " || ".join(
                    "resource.name == '" + prefix + item + "'"
                    for item in (
                        "monitoring-slack/state-v1.json",
                        "monitoring-slack/capabilities-v1.json",
                    )
                )
                + ")"
            )
            require(
                re.sub(r"\s+", "", condition) == re.sub(r"\s+", "", expected),
                "state grant must name only its two exact private objects",
            )
        elif kind == "google_service_account":
            require(
                value["project"] == PROJECT
                and value["account_id"] == "monitoring-slack-scheduler",
                "unexpected service account",
            )
        elif kind == "google_service_account_iam_member":
            require(
                value["member"] == DEPLOYER
                and value["role"] == "roles/iam.serviceAccountUser"
                and value["service_account_id"]
                in {
                    f"projects/{PROJECT}/serviceAccounts/{RUNTIME}",
                    f"projects/{PROJECT}/serviceAccounts/{SCHEDULER}",
                },
                "actAs grant must remain on the two relay identities",
            )
        elif kind == "google_secret_manager_secret_iam_member":
            require(
                value["project"] == PROJECT
                and value["secret_id"] in {SECRET_ID, SECRET}
                and value["member"] == "serviceAccount:" + RUNTIME
                and value["role"] == "roles/secretmanager.secretAccessor",
                "secret access exceeds the bot credential",
            )
            expression = one(value, "condition")["expression"]
            clauses = expression.split(" || ")
            matches = [
                re.fullmatch(
                    r"resource.name == 'projects/("
                    + PROJECT
                    + r"|[0-9]+)/secrets/"
                    + SECRET_ID
                    + r"/versions/([1-9][0-9]*)'",
                    clause,
                )
                for clause in clauses
            ]
            require(
                len(matches) == 2
                and all(matches)
                and {match[1] for match in matches} >= {PROJECT}
                and len({match[1] for match in matches}) == 2
                and len({match[2] for match in matches}) == 1,
                "runtime secret access must pin one numeric version in its two canonical project forms",
            )
            secret_versions = {
                f"projects/{match[1]}/secrets/{SECRET_ID}/versions/{match[2]}"
                for match in matches
            }
        elif kind == "google_cloud_run_v2_job":
            require(
                value["project"] == PROJECT
                and value["location"] == REGION
                and value["name"] == "monitoring-slack-relay",
                "foreign worker",
            )
            task = one(value, "template")
            require(
                task["task_count"] == task["parallelism"] == 1,
                "one serialized task required",
            )
            execution = one(task, "template")
            require(
                execution["service_account"] == RUNTIME
                and execution["max_retries"] == 0
                and execution["timeout"] == "240s",
                "worker authority/retry limits changed",
            )
            container = one(execution, "containers")
            require(
                container.get("image") == image
                and container.get("command")
                == ["python", "-m", "services.monitoring_slack.run"]
                and container.get("args") in (None, []),
                "worker bytes or normal entrypoint differ from reviewed image",
            )
            require(
                one(container, "resources").get("limits")
                == {"cpu": "1", "memory": "512Mi"},
                "worker resource limits changed",
            )
            require(
                len(container.get("env", [])) == 1,
                "one exact worker environment entry is required",
            )
            env = {
                entry["name"]: entry.get("value") for entry in container.get("env", [])
            }
            require(
                set(env) == {"MONITORING_SLACK_CONFIG"}, "unexpected worker environment"
            )
            config = Config.parse(json.loads(env["MONITORING_SLACK_CONFIG"])).value
            worker_version = config["slack_secret_version"]
            require(
                config["health_metric"]
                == "custom.googleapis.com/shared_datasets/operational_reconciliation_heartbeat_v2",
                "worker must observe only the actual fenced heartbeat",
            )
            require(
                config["mode"] == "shadow"
                and config["project"] == PROJECT
                and config["relay_job"]
                == f"projects/{PROJECT}/locations/{REGION}/jobs/monitoring-slack-relay"
                and config["relay_image"] == image
                and config["state_bucket"] == BUCKET,
                "preparation cannot enable public delivery",
            )
            require(
                {(p["name"], p["kind"]) for p in config["policies"]}
                == {
                    (name, "health" if key == "health" else "security")
                    for key, name in r["policies"].items()
                }
                and all(
                    config[k] == r[k]
                    for k in ("slack_channel", "slack_team", "slack_user")
                ),
                "worker routing differs from reviewed registry",
            )
        elif kind == "google_cloud_run_v2_job_iam_member":
            member, role = {
                "google_cloud_run_v2_job_iam_member.monitoring_slack_scheduler[0]": (
                    "serviceAccount:" + SCHEDULER, "roles/run.invoker"
                ),
                "google_cloud_run_v2_job_iam_member.monitoring_slack_execution_reader[0]": (
                    "serviceAccount:" + RUNTIME,
                    f"projects/{PROJECT}/roles/sharedDatasetsMonitoringSlackExecutionReader",
                ),
            }[row["address"]]
            require(
                value["project"] == PROJECT
                and value["location"] == REGION
                and value["name"] == "monitoring-slack-relay"
                and value["member"] == member
                and value["role"] == role
                and not value.get("condition"),
                "relay Job grants must retain their exact reader/invoker identity and scope",
            )
        elif kind == "google_cloud_scheduler_job":
            require(
                value["project"] == PROJECT
                and value["region"] == REGION
                and value["name"] == "monitoring-slack-relay"
                and value["paused"] is True
                and value["schedule"] == "*/5 * * * *"
                and value["time_zone"] == "UTC"
                and one(value, "retry_config").get("retry_count") == 0,
                "preparation scheduler must remain paused with its exact bounded cadence",
            )
            target = one(value, "http_target")
            require(
                target["uri"]
                == f"https://{REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/{PROJECT}/jobs/monitoring-slack-relay:run"
                and target["http_method"] == "POST"
                and target.get("body") == "e30="
                and not target.get("oidc_token")
                and not value.get("pubsub_target")
                and not value.get("app_engine_http_target"),
                "scheduler target or invocation overrides changed",
            )
            require(
                one(target, "oauth_token")["service_account_email"] == SCHEDULER,
                "scheduler identity changed",
            )
        else:
            raise ValueError("unsupported relay resource")
    require(
        worker_version in secret_versions,
        "worker credential version differs from its exact accessor grant",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--targets", action="store_true")
    mode.add_argument("--seed-bot-secret", action="store_true")
    mode.add_argument("--confirm-bot-secret-version", metavar="VERSION")
    mode.add_argument("--prepare-config", action="store_true")
    mode.add_argument("--plan-json", type=Path)
    parser.add_argument("--secret-version")
    parser.add_argument("--image")
    args = parser.parse_args(argv)
    if args.targets:
        print("\n".join(sorted(RESOURCES)))
    elif args.seed_bot_secret:
        output(
            "secret_version",
            seed_bot_secret(
                session(), os.environ.get("SHARED_DATASETS_SLACK_BOT_TOKEN", "")
            ),
        )
    elif args.confirm_bot_secret_version:
        output(
            "secret_version",
            confirm_bot_secret_version(session(), args.confirm_bot_secret_version),
        )
    elif args.prepare_config:
        config = prepare_config(session(), args.image or "", args.secret_version or "")
        encoded = json.dumps(config, sort_keys=True, separators=(",", ":"))
        output("TF_VAR_monitoring_slack_relay", encoded, "GITHUB_ENV")
        output("config_sha256", hashlib.sha256(encoded.encode()).hexdigest())
    else:
        validate_plan(json.loads(args.plan_json.read_text()), args.image or "")
        print(
            "Exact shadow relay resource, permission and routing preparation verified."
        )


if __name__ == "__main__":
    main()
