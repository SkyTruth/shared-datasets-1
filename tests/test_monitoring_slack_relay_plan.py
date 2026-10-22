"""Saved-plan regressions from real Terraform 1.8.5 / Google 7.31 output.

The fixture is a projection of an offline mock-provider plan of the actual
monitoring_slack_relay*.tf and unchanged Run/Scheduler/identity modules. Only
provider bookkeeping and unrelated bootstrap rows were removed. No API values
or role/member strings were repaired in the fixture. Relay Terraform SHA256 at capture:
beefbc0e3f7119cd30a434263d37d1e7df89b6d52e4102559386e35277506d0e
"""
from copy import deepcopy
import json

import pytest

from scripts import monitoring_slack_deploy as deploy


IMAGE = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/monitoring-slack-relay@sha256:" + "a" * 64
JOB = "module.monitoring_slack_job[0].google_cloud_run_v2_job.this"
SCHEDULE = "module.monitoring_slack_scheduler[0].google_cloud_scheduler_job.this"
SECRET = "google_secret_manager_secret_iam_member.monitoring_slack_bot_token[0]"
STATE = "google_storage_bucket_iam_member.monitoring_slack_state"
EXECUTION_READER = "google_cloud_run_v2_job_iam_member.monitoring_slack_execution_reader[0]"
EXECUTION_ROLE = "google_project_iam_custom_role.monitoring_slack_execution_reader"

_ACTUAL_HCL_PLAN = json.loads(r'''{
  "format_version": "1.2",
  "resource_changes": [
    {
      "address": "google_cloud_run_v2_job_iam_member.monitoring_slack_execution_reader[0]",
      "type": "google_cloud_run_v2_job_iam_member",
      "change": {
        "actions": [
          "create"
        ],
        "after": {
          "condition": [],
          "location": "us-central1",
          "member": "serviceAccount:monitoring-slack-relay@shared-datasets-1.iam.gserviceaccount.com",
          "name": "monitoring-slack-relay",
          "project": "shared-datasets-1",
          "role": "projects/shared-datasets-1/roles/sharedDatasetsMonitoringSlackExecutionReader"
        },
        "after_unknown": {
          "condition": []
        }
      }
    },
    {
      "address": "google_cloud_run_v2_job_iam_member.monitoring_slack_scheduler[0]",
      "type": "google_cloud_run_v2_job_iam_member",
      "change": {
        "actions": [
          "create"
        ],
        "after": {
          "condition": [],
          "location": "us-central1",
          "member": "serviceAccount:monitoring-slack-scheduler@shared-datasets-1.iam.gserviceaccount.com",
          "name": "monitoring-slack-relay",
          "project": "shared-datasets-1",
          "role": "roles/run.invoker"
        },
        "after_unknown": {
          "condition": []
        }
      }
    },
    {
      "address": "google_project_iam_custom_role.monitoring_slack_execution_reader",
      "type": "google_project_iam_custom_role",
      "change": {
        "actions": [
          "create"
        ],
        "after": {
          "permissions": [
            "run.executions.get"
          ],
          "project": "shared-datasets-1",
          "role_id": "sharedDatasetsMonitoringSlackExecutionReader"
        },
        "after_unknown": {
          "permissions": [
            false
          ]
        }
      }
    },
    {
      "address": "google_project_iam_custom_role.monitoring_slack_reader",
      "type": "google_project_iam_custom_role",
      "change": {
        "actions": [
          "create"
        ],
        "after": {
          "permissions": [
            "monitoring.alertPolicies.get",
            "monitoring.alerts.get",
            "monitoring.alerts.list",
            "monitoring.timeSeries.list"
          ],
          "project": "shared-datasets-1",
          "role_id": "sharedDatasetsMonitoringSlackReader"
        },
        "after_unknown": {
          "permissions": [
            false,
            false,
            false,
            false
          ]
        }
      }
    },
    {
      "address": "google_project_iam_custom_role.monitoring_slack_state",
      "type": "google_project_iam_custom_role",
      "change": {
        "actions": [
          "create"
        ],
        "after": {
          "permissions": [
            "storage.objects.create",
            "storage.objects.delete",
            "storage.objects.get"
          ],
          "project": "shared-datasets-1",
          "role_id": "sharedDatasetsMonitoringSlackState"
        },
        "after_unknown": {
          "permissions": [
            false,
            false,
            false
          ]
        }
      }
    },
    {
      "address": "google_project_iam_member.monitoring_slack_reader",
      "type": "google_project_iam_member",
      "change": {
        "actions": [
          "create"
        ],
        "after": {
          "condition": [],
          "member": "serviceAccount:monitoring-slack-relay@shared-datasets-1.iam.gserviceaccount.com",
          "project": "shared-datasets-1",
          "role": "projects/shared-datasets-1/roles/sharedDatasetsMonitoringSlackReader"
        },
        "after_unknown": {
          "condition": []
        }
      }
    },
    {
      "address": "google_secret_manager_secret_iam_member.monitoring_slack_bot_token[0]",
      "type": "google_secret_manager_secret_iam_member",
      "change": {
        "actions": [
          "create"
        ],
        "after": {
          "condition": [
            {
              "description": "Access only the exact numeric bot-token version selected after protected seeding.",
              "expression": "resource.name == 'projects/shared-datasets-1/secrets/shared-datasets-slack-incident-bot-token/versions/7' || resource.name == 'projects/12695949518/secrets/shared-datasets-slack-incident-bot-token/versions/7'",
              "title": "monitoring-slack-pinned-bot-version"
            }
          ],
          "member": "serviceAccount:monitoring-slack-relay@shared-datasets-1.iam.gserviceaccount.com",
          "project": "shared-datasets-1",
          "role": "roles/secretmanager.secretAccessor",
          "secret_id": "shared-datasets-slack-incident-bot-token"
        },
        "after_unknown": {
          "condition": [
            {}
          ]
        }
      }
    },
    {
      "address": "google_service_account_iam_member.monitoring_slack_deployer",
      "type": "google_service_account_iam_member",
      "change": {
        "actions": [
          "create"
        ],
        "after": {
          "condition": [],
          "member": "serviceAccount:shared-datasets-terraform@shared-datasets-1.iam.gserviceaccount.com",
          "role": "roles/iam.serviceAccountUser",
          "service_account_id": "projects/shared-datasets-1/serviceAccounts/monitoring-slack-relay@shared-datasets-1.iam.gserviceaccount.com"
        },
        "after_unknown": {
          "condition": []
        }
      }
    },
    {
      "address": "google_service_account_iam_member.monitoring_slack_scheduler_deployer",
      "type": "google_service_account_iam_member",
      "change": {
        "actions": [
          "create"
        ],
        "after": {
          "condition": [],
          "member": "serviceAccount:shared-datasets-terraform@shared-datasets-1.iam.gserviceaccount.com",
          "role": "roles/iam.serviceAccountUser",
          "service_account_id": "projects/shared-datasets-1/serviceAccounts/monitoring-slack-scheduler@shared-datasets-1.iam.gserviceaccount.com"
        },
        "after_unknown": {
          "condition": []
        }
      }
    },
    {
      "address": "google_storage_bucket.monitoring_slack_state",
      "type": "google_storage_bucket",
      "change": {
        "actions": [
          "create"
        ],
        "after": {
          "force_destroy": false,
          "name": "skytruth-shared-datasets-1-monitoring-slack-state",
          "project": "shared-datasets-1",
          "public_access_prevention": "enforced",
          "uniform_bucket_level_access": true,
          "versioning": [
            {
              "enabled": true
            }
          ]
        },
        "after_unknown": {
          "versioning": [
            {}
          ]
        }
      }
    },
    {
      "address": "google_storage_bucket_iam_member.monitoring_slack_state",
      "type": "google_storage_bucket_iam_member",
      "change": {
        "actions": [
          "create"
        ],
        "after": {
          "bucket": "skytruth-shared-datasets-1-monitoring-slack-state",
          "condition": [
            {
              "description": "Only the independent incident ledger and its private capability marker.",
              "expression": "resource.type == 'storage.googleapis.com/Object' && (resource.name == 'projects/_/buckets/skytruth-shared-datasets-1-monitoring-slack-state/objects/monitoring-slack/state-v1.json' || resource.name == 'projects/_/buckets/skytruth-shared-datasets-1-monitoring-slack-state/objects/monitoring-slack/capabilities-v1.json')",
              "title": "monitoring-slack-exact-state-objects"
            }
          ],
          "member": "serviceAccount:monitoring-slack-relay@shared-datasets-1.iam.gserviceaccount.com",
          "role": "projects/shared-datasets-1/roles/sharedDatasetsMonitoringSlackState"
        },
        "after_unknown": {
          "condition": [
            {}
          ]
        }
      }
    },
    {
      "address": "module.monitoring_slack_job[0].google_cloud_run_v2_job.this",
      "type": "google_cloud_run_v2_job",
      "change": {
        "actions": [
          "create"
        ],
        "after": {
          "deletion_protection": false,
          "launch_stage": "GA",
          "location": "us-central1",
          "name": "monitoring-slack-relay",
          "project": "shared-datasets-1",
          "template": [
            {
              "annotations": null,
              "labels": null,
              "parallelism": 1,
              "task_count": 1,
              "template": [
                {
                  "containers": [
                    {
                      "args": null,
                      "command": [
                        "python",
                        "-m",
                        "services.monitoring_slack.run"
                      ],
                      "depends_on": null,
                      "env": [
                        {
                          "name": "MONITORING_SLACK_CONFIG",
                          "value": "{\"activated_at\":\"2026-10-22T14:00:00Z\",\"health_metric\":\"custom.googleapis.com/shared_datasets/operational_reconciliation_heartbeat_v2\",\"health_policy_created_at\":\"2026-10-22T04:59:54.149734439Z\",\"mode\":\"shadow\",\"policies\":[{\"kind\":\"health\",\"name\":\"projects/shared-datasets-1/alertPolicies/1980534029880229379\"},{\"kind\":\"security\",\"name\":\"projects/shared-datasets-1/alertPolicies/16639688203190432625\"},{\"kind\":\"security\",\"name\":\"projects/shared-datasets-1/alertPolicies/9766115247324033694\"}],\"project\":\"shared-datasets-1\",\"relay_image\":\"us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/monitoring-slack-relay@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"relay_job\":\"projects/shared-datasets-1/locations/us-central1/jobs/monitoring-slack-relay\",\"schema\":\"monitoring-slack-config-v1\",\"slack_channel\":\"CEXAMPLE1\",\"slack_secret_version\":\"projects/12695949518/secrets/shared-datasets-slack-incident-bot-token/versions/7\",\"slack_team\":\"TEXAMPLE\",\"slack_user\":\"UEXAMPLE\",\"state_bucket\":\"skytruth-shared-datasets-1-monitoring-slack-state\"}",
                          "value_source": []
                        }
                      ],
                      "image": "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/monitoring-slack-relay@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                      "name": null,
                      "ports": [],
                      "resources": [
                        {
                          "limits": {
                            "cpu": "1",
                            "memory": "512Mi"
                          }
                        }
                      ],
                      "startup_probe": [],
                      "volume_mounts": [],
                      "working_dir": null
                    }
                  ],
                  "encryption_key": null,
                  "gpu_zonal_redundancy_disabled": null,
                  "max_retries": 0,
                  "node_selector": [],
                  "service_account": "monitoring-slack-relay@shared-datasets-1.iam.gserviceaccount.com",
                  "timeout": "240s",
                  "volumes": [],
                  "vpc_access": []
                }
              ]
            }
          ]
        },
        "after_unknown": {
          "template": [
            {
              "template": [
                {
                  "containers": [
                    {
                      "command": [
                        false,
                        false,
                        false
                      ],
                      "env": [
                        {
                          "value_source": []
                        }
                      ],
                      "ports": [],
                      "resources": [
                        {
                          "limits": {}
                        }
                      ],
                      "startup_probe": [],
                      "volume_mounts": []
                    }
                  ],
                  "execution_environment": true,
                  "node_selector": [],
                  "volumes": [],
                  "vpc_access": []
                }
              ]
            }
          ]
        }
      }
    },
    {
      "address": "module.monitoring_slack_scheduler[0].google_cloud_scheduler_job.this",
      "type": "google_cloud_scheduler_job",
      "change": {
        "actions": [
          "create"
        ],
        "after": {
          "app_engine_http_target": [],
          "attempt_deadline": "320s",
          "http_target": [
            {
              "body": "e30=",
              "headers": {
                "Content-Type": "application/json"
              },
              "http_method": "POST",
              "oauth_token": [
                {
                  "scope": null,
                  "service_account_email": "monitoring-slack-scheduler@shared-datasets-1.iam.gserviceaccount.com"
                }
              ],
              "oidc_token": [],
              "uri": "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/shared-datasets-1/jobs/monitoring-slack-relay:run"
            }
          ],
          "name": "monitoring-slack-relay",
          "paused": true,
          "project": "shared-datasets-1",
          "pubsub_target": [],
          "region": "us-central1",
          "retry_config": [
            {
              "retry_count": 0
            }
          ],
          "schedule": "*/5 * * * *",
          "time_zone": "UTC"
        },
        "after_unknown": {
          "app_engine_http_target": [],
          "http_target": [
            {
              "headers": {},
              "oauth_token": [
                {}
              ],
              "oidc_token": []
            }
          ],
          "pubsub_target": [],
          "retry_config": [
            {
              "max_backoff_duration": true,
              "max_doublings": true,
              "max_retry_duration": true,
              "min_backoff_duration": true
            }
          ]
        }
      }
    },
    {
      "address": "module.monitoring_slack_scheduler_service_account.google_service_account.this",
      "type": "google_service_account",
      "change": {
        "actions": [
          "create"
        ],
        "after": {
          "account_id": "monitoring-slack-scheduler",
          "project": "shared-datasets-1"
        },
        "after_unknown": {}
      }
    }
  ]
}''')


def resource(plan, address):
    return next(row for row in plan["resource_changes"] if row["address"] == address)


def after(plan, address):
    return resource(plan, address)["change"]["after"]


def container(plan):
    return after(plan, JOB)["template"][0]["template"][0]["containers"][0]


def config(plan):
    return json.loads(container(plan)["env"][0]["value"])


@pytest.fixture
def prepared(tmp_path):
    plan = deepcopy(_ACTUAL_HCL_PLAN)
    routing = deploy.registry()
    generated = config(plan)
    for key in ("slack_channel", "slack_team", "slack_user"):
        routing[key] = generated[key]
    (tmp_path / "catalog").mkdir()
    (tmp_path / "catalog/monitoring-slack-relay.json").write_text(json.dumps(routing))
    return plan, tmp_path


def test_real_hcl_plan_and_noop_remain_accepted(prepared):
    plan, root = prepared
    assert {row["address"] for row in plan["resource_changes"]} == deploy.RESOURCES
    deploy.validate_plan(plan, IMAGE, root=root)
    for row in plan["resource_changes"]:
        row["change"]["actions"] = ["no-op"]
    deploy.validate_plan(plan, IMAGE, root=root)


@pytest.mark.parametrize("address", [
    "google_project_iam_member.monitoring_slack_reader", STATE,
    "google_service_account_iam_member.monitoring_slack_deployer",
    "google_service_account_iam_member.monitoring_slack_scheduler_deployer",
    "google_cloud_run_v2_job_iam_member.monitoring_slack_scheduler[0]",
    EXECUTION_READER,
])
def test_initial_hcl_plan_has_known_authority_names(prepared, address):
    plan, _ = prepared
    value = resource(plan, address)["change"]
    for field in ("member", "role", "service_account_id"):
        assert value["after_unknown"].get(field) is not True


@pytest.mark.parametrize("actions", [["delete"], ["delete", "create"], ["create", "delete"]])
def test_destructive_plan_cannot_pass(prepared, actions):
    plan, root = prepared
    resource(plan, JOB)["change"]["actions"] = actions
    with pytest.raises(ValueError):
        deploy.validate_plan(plan, IMAGE, root=root)


@pytest.mark.parametrize("address,field,value", [
    ("google_project_iam_custom_role.monitoring_slack_reader", "permissions",
     ["monitoring.alerts.get", "monitoring.alerts.list", "monitoring.alertPolicies.get", "monitoring.timeSeries.list", "monitoring.timeSeries.create"]),
    ("google_project_iam_custom_role.monitoring_slack_state", "permissions",
     ["storage.objects.get", "storage.objects.create", "storage.objects.delete", "storage.objects.list"]),
    ("google_project_iam_custom_role.monitoring_slack_reader", "role_id", "unreviewedRole"),
    ("google_project_iam_custom_role.monitoring_slack_reader", "project", "foreign-project"),
    (EXECUTION_ROLE, "permissions", ["run.executions.get", "run.executions.list"]),
    (EXECUTION_ROLE, "permissions", ["run.executions.get", "run.jobs.run"]),
    (EXECUTION_ROLE, "permissions", ["run.executions.get", "run.jobs.runWithOverrides"]),
    (EXECUTION_ROLE, "role_id", "unreviewedReader"),
    (EXECUTION_READER, "name", "wdpa-monthly"),
    (EXECUTION_READER, "location", "us-east1"),
    (EXECUTION_READER, "project", "another-project"),
    (EXECUTION_READER, "member", "serviceAccount:" + deploy.SCHEDULER),
    (EXECUTION_READER, "role", "roles/run.admin"),
    (EXECUTION_READER, "condition", [{"expression": "false"}]),
    (SECRET, "secret_id", "another-secret"),
    ("google_storage_bucket.monitoring_slack_state", "name", "skytruth-shared-datasets-1"),
    ("google_storage_bucket.monitoring_slack_state", "public_access_prevention", "inherited"),
    (SCHEDULE, "paused", False),
    (SCHEDULE, "schedule", "* * * * *"),
    (SCHEDULE, "time_zone", "America/New_York"),
])
def test_authority_privacy_and_schedule_mutations_fail(prepared, address, field, value):
    plan, root = prepared
    after(plan, address)[field] = value
    with pytest.raises(ValueError):
        deploy.validate_plan(plan, IMAGE, root=root)


@pytest.mark.parametrize("which", ["extra-state-or", "secret-prefix", "wrong-kind", "unknown-role", "out-of-scope"])
def test_scope_and_unknown_authority_cannot_pass(prepared, which):
    plan, root = prepared
    if which == "extra-state-or":
        after(plan, STATE)["condition"][0]["expression"] += " || resource.name.startsWith('projects/_/buckets/')"
    elif which == "secret-prefix":
        after(plan, SECRET)["condition"][0]["expression"] = "resource.name.startsWith('projects/')"
    elif which == "wrong-kind":
        resource(plan, "google_project_iam_custom_role.monitoring_slack_reader")["type"] = "google_storage_bucket"
    elif which == "unknown-role":
        row = resource(plan, "google_project_iam_member.monitoring_slack_reader")
        row["change"]["after_unknown"]["role"] = True
        row["change"]["after"].pop("role")
    else:
        row = deepcopy(resource(plan, JOB))
        row["address"] = "module.unreviewed.google_cloud_run_v2_job.this"
        plan["resource_changes"].append(row)
    with pytest.raises(ValueError):
        deploy.validate_plan(plan, IMAGE, root=root)


@pytest.mark.parametrize("field,value", [
    ("mode", "live"), ("schema", "unreviewed-schema"),
    ("slack_user", "UFOREIGN1"), ("slack_channel", "CFOREIGN1"),
    ("slack_secret_version", "projects/12695949518/secrets/foreign-secret/versions/7"),
    ("slack_secret_version", "projects/12695949518/secrets/shared-datasets-slack-incident-bot-token/versions/latest"),
    ("slack_secret_version", "projects/12695949518/secrets/shared-datasets-slack-incident-bot-token/versions/8"),
    ("health_metric", "custom.googleapis.com/unrelated/heartbeat"),
    ("health_policy_created_at", "invalid-timestamp"),
    ("relay_job", "projects/shared-datasets-1/locations/us-east1/jobs/monitoring-slack-relay"),
    ("relay_image", IMAGE[:-1] + "b"),
])
def test_config_is_the_strict_consumers_contract_and_version_matches_iam(prepared, field, value):
    plan, root = prepared
    settings = config(plan)
    settings[field] = value
    container(plan)["env"][0]["value"] = json.dumps(settings)
    with pytest.raises(ValueError):
        deploy.validate_plan(plan, IMAGE, root=root)


def test_wrong_policy_kind_cannot_pass(prepared):
    plan, root = prepared
    settings = config(plan)
    settings["policies"][0]["kind"] = "security"
    container(plan)["env"][0]["value"] = json.dumps(settings)
    with pytest.raises(ValueError):
        deploy.validate_plan(plan, IMAGE, root=root)


@pytest.mark.parametrize("field,value", [
    ("image", IMAGE[:-1] + "b"),
    ("command", ["python", "-m", "another.entrypoint"]),
    ("args", ["--verify-capabilities"]),
])
def test_only_the_tested_normal_worker_entrypoint_is_installed(prepared, field, value):
    plan, root = prepared
    container(plan)[field] = value
    with pytest.raises(ValueError):
        deploy.validate_plan(plan, IMAGE, root=root)


@pytest.mark.parametrize("which", ["body", "retry"])
def test_scheduler_never_adds_overrides_or_dispatch_retry(prepared, which):
    plan, root = prepared
    value = after(plan, SCHEDULE)
    if which == "body":
        value["http_target"][0]["body"] = "eyJvdmVycmlkZXMiOnt9fQ=="
    else:
        value["retry_config"][0]["retry_count"] = 3
    with pytest.raises(ValueError):
        deploy.validate_plan(plan, IMAGE, root=root)


def test_reader_install_permission_probe_is_bound_to_actual_existing_job(prepared):
    from scripts.terraform_plan_permissions import plan_checks

    plan, _ = prepared
    # The custom role is project metadata; the binding is a policy mutation on
    # this existing Job. A project-wide Run permission probe is insufficient.
    row = deepcopy(resource(plan, EXECUTION_READER))
    row["change"]["actions"] = ["update"]
    row["change"]["before"] = deepcopy(row["change"]["after"])
    assert plan_checks(
        {"format_version": "1.2", "resource_changes": [row]},
        project_number="12695949518",
    ) == [(
        "https://run.googleapis.com/v2/projects/shared-datasets-1/locations/us-central1/jobs/monitoring-slack-relay:testIamPermissions",
        ("run.jobs.getIamPolicy", "run.jobs.setIamPolicy"),
    )]
