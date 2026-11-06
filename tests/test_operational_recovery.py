"""Operational boundaries work with the same credential-free hosted interpreter."""
from datetime import timedelta
import json
import os
import subprocess
import shlex
from unittest import mock

import pytest

from scripts import operational_safety_check as safety, slack_notify
from test_operational_safety_check import NOW, OperationalAPI, stamp
from test_operational_safety_check import probe_inputs  # noqa: F401 - pytest fixture


@pytest.fixture(scope="module")
def hosted_python(tmp_path_factory):
    directory = tmp_path_factory.mktemp("hosted-probe")
    environment = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(directory / "env")}
    environment.pop("VIRTUAL_ENV", None)
    result = subprocess.run(["uv", "sync", "--frozen", "--no-dev", "--python", "3.12.12"], cwd=safety.ROOT,
        env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return str(directory / "env/bin/python")


@pytest.mark.parametrize("enabled,since", [("false", ""), ("false", "2026-10-06T17:28:14Z"), ("true", ""), ("true", " ")])
def test_unconfigured_incidents_skip_slack_and_keep_discovery_in_clean_interpreter(tmp_path, enabled, since, hosted_python):
    # The hosted interpreter contains only uv sync --no-dev dependencies.
    # Service transports alone are replaced; CLI and probe serialization run.
    code = '''
import json, os, sys
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, os.getcwd())
from scripts import operational_safety_check as s, catalog_drift_guard as c, deployment_revision as d, slack_incidents as i
s.GitHub = Mock(return_value=Mock())
s.alerts.context = Mock(return_value={"head_sha":"a"*40})
c.get_client = Mock(return_value=object())
s.catalog_inventory = Mock(return_value={})
s.catalog_changed = Mock(return_value=True)
s.source_inventory = Mock(return_value=([], None, None))
d.pending = Mock(return_value=[])
i.Slack = Mock(side_effect=AssertionError("disabled incident transport called"))
s.main()
assert s.catalog_changed.call_count == 1
assert s.source_inventory.call_count == 0
i.Slack.assert_not_called()
assert json.loads((Path(sys.argv[-1]) / "result.json").read_text()) == {"catalog":True,"verify":False,"reconcile":False}
'''
    result = subprocess.run([hosted_python, "-I", "-c", code, "--work-dir", str(tmp_path)], cwd=safety.ROOT,
        env={**os.environ, "SHARED_DATASETS_SLACK_INCIDENTS_ENABLED": enabled,
             "SHARED_DATASETS_SLACK_INCIDENTS_SINCE": since, "GITHUB_OUTPUT": str(tmp_path / "outputs")},
        text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "catalog=true" in (tmp_path / "outputs").read_text()


def health_cli(tmp_path, monkeypatch):
    api = OperationalAPI()
    monkeypatch.setattr(safety, "GitHub", lambda: api)
    monkeypatch.setattr(safety.alerts, "context", lambda *_a, **_k: api.current)
    monkeypatch.setattr(safety, "policy_warnings", lambda _: ([], []))
    monkeypatch.setattr(safety, "audit_freshness", lambda *_a, **_k: [])
    monkeypatch.setattr(safety, "deployed_image_lag", lambda *_a, **_k: [], raising=False)
    from scripts import catalog_drift_guard as catalog
    monkeypatch.setattr(catalog, "get_client", lambda: None)
    monkeypatch.setattr(safety, "freshness", lambda *_a, **_k: [])
    monkeypatch.setattr(safety, "previous_health", lambda *_: {"problems": [], "warnings": [], "notified_at": None})
    monkeypatch.setattr(safety, "heartbeat_series", lambda *_: [])
    monkeypatch.setattr(safety, "recovery_identity", lambda *_: [])
    monkeypatch.setenv("SHARED_DATASETS_SLACK_INCIDENTS_ENABLED", "true")
    monkeypatch.setenv("SHARED_DATASETS_SLACK_INCIDENTS_SINCE", stamp(NOW))
    monkeypatch.setenv("SHARED_DATASETS_SLACK_BOT_TOKEN", "test-token")
    monkeypatch.setattr(safety.sys, "argv", ["safety", "--health", "--work-dir", str(tmp_path)])
    (tmp_path / "result.json").write_text('{"reconcile":false}')
    (tmp_path / "claims.json").write_text('[]')
    return api


@pytest.mark.parametrize("delivered", [True, False])
def test_heartbeat_follows_health_delivery(tmp_path, monkeypatch, delivered):
    health_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("SHARED_DATASETS_SLACK_INCIDENTS_ENABLED", "false")
    events = []
    def notify(**_kwargs):
        events.append("delivery")
        if not delivered:
            raise RuntimeError("webhook unavailable")
    monkeypatch.setattr(slack_notify, "notify", notify)
    monkeypatch.setattr(safety.subprocess, "check_call", lambda *_a, **_k: events.append("heartbeat"))
    if delivered:
        safety.main()
        assert events == ["delivery", "heartbeat"]
    else:
        with pytest.raises(RuntimeError, match="webhook unavailable"):
            safety.main()
        assert events == ["delivery"]


@pytest.mark.parametrize('failed', ['policy_warnings', 'audit_freshness', 'deployed_image_lag', 'freshness', 'heartbeat_series', 'recovery_identity'])
def test_health_reader_failure_preserves_other_reads_and_delivered_heartbeat(tmp_path, monkeypatch, failed):
    health_cli(tmp_path, monkeypatch)
    readers = {}
    for name in ('policy_warnings', 'audit_freshness', 'deployed_image_lag', 'freshness', 'heartbeat_series', 'recovery_identity'):
        readers[name] = mock.Mock(side_effect=RuntimeError('reader unavailable') if name == failed else None,
                                  return_value=([], []) if name == 'policy_warnings' else [])
        monkeypatch.setattr(safety, name, readers[name])
    events = []
    monkeypatch.setattr(slack_notify, 'notify', lambda **_k: events.append('delivery'))
    monkeypatch.setattr(safety.subprocess, 'check_call', lambda *_a, **_k: events.append('heartbeat'))
    safety.main()
    assert all(reader.call_count == 1 for reader in readers.values())
    report = json.loads((tmp_path / 'health/cache.json').read_text())
    assert len(report['problems']) == 1 and failed in report['problems'][0]
    assert events == ['delivery', 'heartbeat']


def test_safety_signed_lag_reader_has_hosted_verifier_and_read_permissions():
    from workflow_helpers import load_workflow
    from scripts.terraform_target_contracts import resource_index
    job = load_workflow(safety.ROOT / '.github/workflows/operational-safety-check.yml')['jobs']['safety']
    assert job['permissions']['attestations'] == 'read'
    steps = job['steps']
    verifier = next(i for i, step in enumerate(steps) if step.get('id') == 'verifier')
    health = next(i for i, step in enumerate(steps) if step.get('id') == 'health')
    assert verifier < health and steps[verifier]['run'] == 'python scripts/install_deployment_verifier.py'
    import hcl2
    from hcl2.utils import SerializationOptions
    local, = hcl2.loads((safety.ROOT / 'terraform/envs/prod/github_job_identities.tf').read_text(),
                       serialization_options=SerializationOptions(strip_string_quotes=True))['locals']
    assert 'run.jobs.get' in local['observer_permissions']
    assert local['github_job_permissions']['heartbeat'] == '${local.observer_permissions}'
    declarations = resource_index()["terraform/envs/prod/github_job_identities.tf"]
    assert declarations['google_project_iam_member.github_job']['member'] == 'serviceAccount:${google_service_account.github_job[each.key].email}'


@pytest.mark.parametrize("enabled,since,text", [("false", stamp(NOW), "disabled"), ("true", "", "SINCE")])
def test_incident_configuration_finding_is_delivered(tmp_path, monkeypatch, enabled, since, text):
    health_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("SHARED_DATASETS_SLACK_INCIDENTS_ENABLED", enabled)
    monkeypatch.setenv("SHARED_DATASETS_SLACK_INCIDENTS_SINCE", since)
    monkeypatch.setattr(slack_notify, "notify", mock.Mock())
    monkeypatch.setattr(safety.subprocess, "check_call", mock.Mock())
    safety.main()
    assert any(text in item for item in json.loads((tmp_path / "health/cache.json").read_text())["problems"])


@pytest.mark.parametrize("job", list(safety.TARGETS))
@pytest.mark.parametrize("action", ["pause", "resume"])
def test_schedule_admission_uses_registry_in_bare_interpreter(job, action):
    from workflow_helpers import load_workflow, workflow_triggers
    workflow = load_workflow(safety.ROOT / ".github/workflows/ingestion-schedule-control.yml")
    assert workflow_triggers(workflow)["workflow_dispatch"]["inputs"]["job"]["type"] == "string"
    step = next(row for row in workflow["jobs"]["control"]["steps"] if row.get("name") == "Validate fixed schedule scope")
    result = subprocess.run(["bash", "-c", step["run"]], cwd=safety.ROOT, capture_output=True, text=True,
                            env={**os.environ, "JOB_NAME": job, "SCHEDULE_ACTION": action, "PYTHONPATH": ""})
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("age,matching,hold,count", [(73, False, None, 1), (72, False, None, 0),
    (73, True, None, 0), (73, False, 1, 0), (73, False, -1, 1)])
@pytest.mark.parametrize('phase,state', [('started', 'in_progress'), ('deferred', 'pending')])
def test_signed_selected_deployment_lag_bounds_age_and_active_holds(tmp_path, monkeypatch, age, matching, hold, count, phase, state):
    from scripts import deployment_revision as deployments
    from test_deployment_revision import DeploymentProvenanceTests, ReplayTests, CURRENT, IMAGE
    fixtures = DeploymentProvenanceTests()
    fixtures.setUp()
    api = fixtures.api
    record = ReplayTests.record(CURRENT)
    record["created_at"] = stamp(NOW - timedelta(hours=age))
    monkeypatch.setattr(safety, "ROOT", tmp_path)
    path = tmp_path / safety.INGESTION_ROLLOUT_HOLDS
    path.parent.mkdir()
    path.write_text(json.dumps({"wdpa": {"owner": "jonaraphael", "reason": "reviewed hold",
        "expires_at": stamp(NOW + timedelta(hours=hold))}} if hold is not None else {}))
    monkeypatch.setattr(safety, "DEPLOY_TARGETS", {"wdpa-monthly": safety.TARGETS["wdpa-monthly"]})
    def pages(path, field=None):
        if "/deployments?" in path:
            return [record]
        if "/statuses?" in path:
            # Deferrals still report image lag although resume ignores them.
            return [{"description": phase, "state": state}]
        return [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]
    api.pages.side_effect = pages
    monkeypatch.setattr(deployments, "verify_receipt", mock.Mock())
    read = mock.Mock(return_value=IMAGE if matching else IMAGE.replace("d" * 64, "e" * 64))
    monkeypatch.setattr(safety.subprocess, "check_output", read)
    assert len(safety.deployed_image_lag(api, now=NOW)) == count
    api.post.assert_not_called()
    if age > 72 and hold != 1:
        assert read.call_args.args[0] == ["gcloud", "run", "jobs", "describe", "wdpa-monthly",
            "--region=us-central1", "--project=shared-datasets-1",
            "--format=value(spec.template.spec.template.spec.containers[0].image)"]
        deployments.verify_receipt.assert_called_once()
        record["creator"] = {"login": "human", "type": "User"}
        with pytest.raises(deployments.DeploymentError, match="Actions"):
            safety.deployed_image_lag(api, now=NOW)


def test_disabled_incidents_still_wake_pending_execution_observer(tmp_path, monkeypatch):
    from scripts import deployment_revision as deployments
    api = OperationalAPI()
    monkeypatch.setattr(deployments, "pending", lambda *_: [99])
    source = mock.Mock(return_value=([], NOW - timedelta(hours=24), NOW))
    monkeypatch.setattr(safety, "source_inventory", source)
    monkeypatch.setattr(safety, "catalog_inventory", lambda *_: {})
    monkeypatch.setattr(safety, "catalog_changed", lambda *_: False)
    monkeypatch.setattr(safety, "verification_due", mock.Mock(return_value=True))
    result = safety.probe(api, api.current, None, "", object(), tmp_path)
    assert result == {"catalog": False, "verify": True, "reconcile": False}
    assert source.call_args.args[2].endswith("Z")
    safety.verification_due.assert_called_once_with(api, [], [99], now=NOW)
    assert api.posts == []


def test_mpatlas_resume_uses_generation_bound_published_baseline():
    from workflow_helpers import load_workflow
    workflow = load_workflow(safety.ROOT / ".github/workflows/ingestion-schedule-control.yml")
    steps = workflow["jobs"]["control"]["steps"]
    step = next(row for row in steps if row.get("name") == "Verify publication before resuming")
    recorder = 'uv() { printf "%s\\n" "$*"; }\n'
    result = subprocess.run(["bash", "-c", recorder + step["run"]], cwd=safety.ROOT, capture_output=True,
                            text=True, env={**os.environ, "JOB_NAME": "mpatlas-monthly"})
    assert result.returncode == 0, result.stderr
    assert "load_baseline(GcsPublisher" in result.stdout
    assert "publication_rollout_gate.py" not in result.stdout


def test_mpatlas_resume_command_runs_with_only_hosted_dependencies(hosted_python):
    from workflow_helpers import load_workflow
    workflow = load_workflow(safety.ROOT / ".github/workflows/ingestion-schedule-control.yml")
    step = next(row for row in workflow["jobs"]["control"]["steps"] if row.get("name") == "Verify publication before resuming")
    command = next(line.strip() for line in step["run"].splitlines() if "load_baseline" in line)
    code = shlex.split(command)[-1]
    harness = '''
import os, sys
sys.path.insert(0, os.getcwd())
from unittest.mock import Mock, patch
from google.cloud import storage
from ingestion.mpatlas_monthly import run
with patch.object(storage, "Client") as client, patch.object(run, "load_baseline") as baseline:
    exec(sys.argv[1])
    client.assert_called_once_with(project="shared-datasets-1")
    client.return_value.bucket.assert_called_once_with("skytruth-shared-datasets-1")
    baseline.assert_called_once()
'''
    result = subprocess.run([hosted_python, "-I", "-c", harness, code], cwd=safety.ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_heartbeat_write_failure_stays_visible_after_report(tmp_path, monkeypatch):
    health_cli(tmp_path, monkeypatch)
    monkeypatch.setattr(slack_notify, "notify", mock.Mock())
    monkeypatch.setattr(safety.subprocess, "check_call", mock.Mock(side_effect=RuntimeError("logging unavailable")))
    with pytest.raises(RuntimeError, match="logging unavailable"):
        safety.main()
    assert (tmp_path / "health/cache.json").is_file()


def test_capacity_table_matches_deployed_cpu_and_memory():
    import hcl2
    docs = (safety.ROOT / "docs/alert-routing.md").read_text()
    for name, label in (("eamlis_monthly", "EAMLIS"), ("mpatlas_monthly", "MPAtlas")):
        config = hcl2.loads((safety.ROOT / f"terraform/envs/prod/{name}.tf").read_text(),
            serialization_options=hcl2.utils.SerializationOptions(strip_string_quotes=True))
        job = next(module[name + "_job"] for module in config["module"] if name + "_job" in module)
        row = next(row for row in docs.splitlines() if row.startswith("| " + label + " |"))
        assert row.endswith(f"| {job['cpu']} / {job['memory'].replace('Gi', ' GiB')} |")
