"""Recovery consumes executor data through trusted code and read-only identity."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from scripts import deployment_revision as deployment
from terraform_helpers import evaluate, load_terraform, resources, terraform_executable

ROOT = Path(__file__).resolve().parents[1]


def recovery_steps():
    return yaml.safe_load((ROOT / '.github/workflows/deployment-recovery.yml').read_text())['jobs']['reconcile']['steps']


def test_recovery_preserves_trusted_checkout_and_never_authenticates_as_deployer():
    steps = recovery_steps()
    checkouts = [s['with'] for s in steps if s.get('uses') == 'actions/checkout@v4']
    assert len(checkouts) == 2
    assert checkouts[0]['ref'] == '${{ github.workflow_sha }}'
    assert checkouts[1] == {'ref': '${{ steps.original.outputs.executor_sha }}', 'path': 'original-executor',
                            'fetch-depth': 0, 'persist-credentials': False}
    auth = [s for s in steps if s.get('uses') == 'google-github-actions/auth@v2']
    assert len(auth) == 1
    assert auth[0]['with']['service_account'] == 'shared-datasets-recovery@shared-datasets-1.iam.gserviceaccount.com'
    assert auth[0]['with']['workload_identity_provider'] == '${{ vars.GCP_TERRAFORM_WORKLOAD_IDENTITY_PROVIDER }}'
    assert steps.index(auth[0]) > next(i for i, s in enumerate(steps) if s.get('id') == 'original')


def test_recovery_grants_reuse_readonly_roles_and_only_the_exact_default_state():
    rows = {f'{kind}.{name}': body for path in (ROOT / 'terraform/envs/prod').glob('*.tf')
            for kind, name, body in resources(load_terraform(path))}
    metadata = rows['google_project_iam_member.recovery_reader']
    state = rows['google_storage_bucket_iam_member.recovery_state_reader']
    assert metadata['member'] == state['member'] == '${google_service_account.github_job["recovery"].member}'
    assert metadata['role'] == '${google_project_iam_custom_role.pr_plan_reader.name}'
    assert state['role'] == '${google_project_iam_custom_role.pr_plan_state_reader.name}'
    assert state['condition'][0]['expression'] == rows['google_storage_bucket_iam_member.pr_plan_state_reader']['condition'][0]['expression']
    assert rows['google_project_iam_custom_role.pr_plan_state_reader']['permissions'] == ['storage.objects.get', 'storage.objects.list']
    assert all(p.endswith(('.get', '.list', '.getIamPolicy', '.getAttestationRules', '.use'))
               for p in rows['google_project_iam_custom_role.pr_plan_reader']['permissions'])
    policy = json.loads((ROOT / 'terraform/envs/prod/github-workflow-identities.json').read_text())
    assert policy['recovery'] == ['deployment-recovery.yml']
    assert all('deployment-recovery.yml' not in workflows for identity, workflows in policy.items() if identity != 'recovery')
    assert 'deployment-recovery.yml' not in policy['terraform']


def test_unproven_planning_and_recovery_roles_are_manual_only():
    from scripts import terraform_target_contracts as contracts
    registry = contracts.registry()
    manual = registry['PR plan identity provisioning']
    protected = {'google_project_iam_member.recovery_reader', 'google_storage_bucket_iam_member.recovery_state_reader',
                 'google_project_iam_custom_role.pr_plan_reader', 'google_project_iam_custom_role.pr_plan_state_reader',
                 *(f'{kind}.github_job["recovery"]' for kind in ('google_project_iam_custom_role', 'google_project_iam_member')),
                 'google_service_account_iam_member.github_job_wif["recovery"]'}
    automatic = {row['workflow'] for row in contracts.registry(section='deployments').values()}
    assert manual['workflow'] not in automatic
    assert protected <= set(manual['resources'])
    # The bare account carries no authority. Creating it automatically keeps dependents
    # of google_service_account.github_job from planning it; every recovery grant stays manual.
    account = 'google_service_account.github_job["recovery"]'
    assert account in registry['Artifact Registry writer binding sync']['resources']
    assert account not in manual['resources']
    for name, scope in registry.items():
        if scope['workflow'] in automatic:
            assert not protected & set(scope['resources']), name


def test_every_recovery_refresh_dependency_has_read_authority(tmp_path):
    from scripts import terraform_target_contracts as contracts

    providers = Path(os.environ.get('TF_DATA_DIR', ROOT / 'terraform/envs/prod/.terraform')) / 'providers'
    prod = contracts.prepare(ROOT, tmp_path, providers)
    graph = contracts.parse_graph(contracts.graph(prod, terraform_executable(), contracts.isolated_environment(tmp_path), tmp_path))
    # Provider refresh contracts, including IAM policy reads distinct from
    # resource metadata. Unknown resource kinds require an explicit decision.
    reads = dict(line.split(':') for line in '''
google_artifact_registry_repository:artifactregistry.repositories.get
google_artifact_registry_repository_iam_member:artifactregistry.repositories.getIamPolicy
google_cloud_run_v2_job:run.jobs.get
google_cloud_run_v2_job_iam_member:run.jobs.getIamPolicy
google_cloud_run_v2_service:run.services.get
google_cloud_run_v2_service_iam_member:run.services.getIamPolicy
google_cloud_scheduler_job:cloudscheduler.jobs.get
google_compute_backend_bucket:compute.backendBuckets.get
google_compute_backend_service:compute.backendServices.get
google_compute_global_address:compute.globalAddresses.get
google_compute_global_forwarding_rule:compute.globalForwardingRules.get
google_compute_managed_ssl_certificate:compute.sslCertificates.get
google_compute_region_network_endpoint_group:compute.regionNetworkEndpointGroups.get
google_compute_target_http_proxy:compute.targetHttpProxies.get
google_compute_target_https_proxy:compute.targetHttpsProxies.get
google_compute_url_map:compute.urlMaps.get
google_iam_deny_policy:iam.denypolicies.get
google_iam_workload_identity_pool:iam.workloadIdentityPools.get,iam.workloadIdentityPools.getAttestationRules
google_iam_workload_identity_pool_provider:iam.workloadIdentityPoolProviders.get
google_iap_web_cloud_run_service_iam_member:iap.webServices.getIamPolicy
google_logging_metric:logging.logMetrics.get
google_logging_project_exclusion:logging.exclusions.get
google_logging_project_sink:logging.sinks.get
google_monitoring_alert_policy:monitoring.alertPolicies.get
google_monitoring_notification_channel:monitoring.notificationChannels.get
google_project:resourcemanager.projects.get
google_project_iam_audit_config:resourcemanager.projects.getIamPolicy
google_project_iam_custom_role:iam.roles.get
google_project_iam_member:resourcemanager.projects.getIamPolicy
google_project_service:serviceusage.services.get
google_pubsub_subscription:pubsub.subscriptions.get
google_pubsub_topic:pubsub.topics.get
google_pubsub_topic_iam_member:pubsub.topics.getIamPolicy
google_secret_manager_secret:secretmanager.secrets.get
google_secret_manager_secret_iam_member:secretmanager.secrets.getIamPolicy
google_service_account:iam.serviceAccounts.get
google_service_account_iam_binding:iam.serviceAccounts.getIamPolicy
google_service_account_iam_member:iam.serviceAccounts.getIamPolicy
google_storage_bucket:storage.buckets.get
google_storage_bucket_iam_member:storage.buckets.getIamPolicy
google_storage_managed_folder:storage.managedFolders.get
google_storage_managed_folder_iam_member:storage.managedFolders.getIamPolicy
terraform_data:
'''.strip().splitlines())
    rows = {f'{kind}.{name}': body for path in (ROOT / 'terraform/envs/prod').glob('*.tf')
            for kind, name, body in resources(load_terraform(path))}
    identity = load_terraform(ROOT / 'terraform/envs/prod/github_job_identities.tf')['locals'][0]
    granted = set(evaluate({key: identity[key] for key in ('observer_permissions', 'github_job_permissions')},
                           'local.github_job_permissions.recovery'))
    granted.update(rows['google_project_iam_custom_role.pr_plan_reader']['permissions'])
    assert 'secretmanager.versions.access' not in granted
    scopes = {name: scope['resources'] for name, scope in contracts.registry().items()}
    scopes.update({name: row['targets'] for name, row in deployment.INGESTION_CONFIGS.items() if row['targets']})
    # The usage reconciler resolves its runtime scope dynamically instead of
    # declaring static ingestion targets.
    usage = contracts.registry(section='plan_scopes')['dataset-usage-deploy.yml']['runtime']
    scopes['dataset-usage'] = usage.get('exact', []) + usage.get('instances', [])
    retired = {r for rows in (row.get('retired_resources', []) for row in json.loads((ROOT / '.github/terraform-targets.json').read_text())['targets'].values()) for r in rows}
    for name, targets in scopes.items():
        roots = {contracts.address(r) for r in targets}
        assert roots - graph.keys() <= retired
        # An empty state-only scope refreshes the full configuration.
        ancestors = contracts.closure(graph, roots & graph.keys()) if targets else graph
        required = {permission for node in ancestors for permission in reads[node.split('.')[-2]].split(',') if permission}
        assert not required - granted, (name, sorted(required - granted))
    automatic = {row['workflow'] for row in contracts.registry(section='deployments').values()}
    unproven = {'google_project_iam_custom_role.pr_plan_reader', 'google_project_iam_custom_role.pr_plan_state_reader'}
    for name, scope in contracts.registry().items():
        if scope['workflow'] in automatic:
            roots = {contracts.address(r) for r in scope['resources']} & graph.keys()
            assert not unproven & contracts.closure(graph, roots).keys(), name


def test_refresh_uses_original_config_trusted_retry_and_a_lock_free_refreshed_plan(tmp_path):
    workspace = tmp_path / 'trusted checkout'
    original = workspace / 'original-executor'
    (original / 'terraform/envs/prod').mkdir(parents=True)
    (original / 'scripts').mkdir()
    (original / 'scripts/terraform_retry.sh').write_text('exit 99\n')
    (workspace / 'scripts').symlink_to(ROOT / 'scripts', target_is_directory=True)
    binary = tmp_path / 'bin/terraform'
    binary.parent.mkdir()
    log = tmp_path / 'terraform-calls'
    binary.write_text(f'#!{sys.executable}\nimport json,os,sys\n'
                      'with open(os.environ["CALLS"],"a") as out: out.write(json.dumps(sys.argv[1:])+"\\n")\n'
                      'if "show" in sys.argv: print("{\\"resource_changes\\": []}")\n')
    binary.chmod(0o755)
    refresh = next(s['run'] for s in recovery_steps() if s.get('name') == 'Refresh original target scope without applying')
    environment = {**os.environ, 'PATH': str(binary.parent) + os.pathsep + os.environ['PATH'],
                   'GITHUB_WORKSPACE': str(workspace), 'RUNNER_TEMP': str(tmp_path), 'CALLS': str(log),
                   'TARGETS_JSON': '["module.reviewed"]', 'IMAGE_VARIABLES': '{"job_image":"image@sha256:abc"}',
                   'VIRTUAL_ENV': str(tmp_path / '.venv-mismatch')}
    result = subprocess.run(['bash', '-euo', 'pipefail', '-c', refresh], cwd=workspace, env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert all(call[0] == '-chdir=original-executor/terraform/envs/prod' for call in calls)
    assert len(calls) == 3
    assert '-lockfile=readonly' in calls[0]
    assert {'plan', '-lock=false', '-refresh=true', '-target=module.reviewed', '-var=job_image=image@sha256:abc'} <= set(calls[1])
    assert 'apply' not in str(calls)
    assert json.loads((tmp_path / 'reconciliation.json').read_text()) == {'resource_changes': []}


@pytest.mark.parametrize('kind', ['sdk', 'terraform', 'ingestion'])
def test_real_reconcile_step_runs_trusted_policy_with_separate_executor_data(tmp_path, kind):
    workspace = tmp_path / 'trusted checkout'
    original = workspace / 'original-executor'
    original.mkdir(parents=True)
    (workspace / 'scripts').symlink_to(ROOT / 'scripts', target_is_directory=True)
    (original / 'scripts').mkdir()
    (original / 'scripts/deployment_revision.py').write_text('raise RuntimeError("executor code ran")\n')
    subprocess.run(['git', 'init', '-q', str(original)], check=True)
    subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    '-C', str(original), 'commit', '--allow-empty', '-qm', 'Executor fixture'], check=True)
    sha = subprocess.check_output(['git', '-C', str(original), 'rev-parse', 'HEAD'], text=True).strip()
    target = 'typescript-sdk' if kind == 'sdk' else next(iter(deployment.TERRAFORM_WORKFLOWS)) if kind == 'terraform' else 'sea-ice-daily'
    image = 'image@sha256:' + 'a' * 64
    claim = {'id': 1, 'sha': sha, 'ref': sha, 'environment': 'production-' + target,
             'creator': {'login': 'github-actions[bot]', 'type': 'Bot'},
             'payload': {'schema': deployment.SCHEMA, 'target': target, 'artifact': image,
                         'ci_run_id': 1, 'ci_run_attempt': 1, 'execution_run_id': 1, 'execution_run_attempt': 1}}
    (tmp_path / 'reconciliation.json').write_text('{"resource_changes": []}')
    harness = tmp_path / 'trusted-harness.py'
    harness.write_text('import json,os,pathlib,runpy,sys\n'
                       f'assert pathlib.Path.cwd() == pathlib.Path({str(workspace)!r})\n'
                       'args=sys.argv[1:]; args=[a for a in args if a not in ("-I","-S")]\n'
                       'script=pathlib.Path(args.pop(0)).resolve()\n'
                       f'assert script == pathlib.Path({str(ROOT / "scripts/deployment_revision.py")!r})\n'
                       'module=runpy.run_path(str(script)); scope=module["recover"].__globals__\n'
                       f'claim={claim!r}\n'
                       'class API:\n'
                       ' def pages(self,*args): return [claim]\n'
                       ' def post(self,*args): return {"id":2}\n'
                       'scope["GitHub"]=API\n'
                       'scope["recovery_record"]=lambda *args: (claim,claim["payload"])\n'
                       'scope["post_status"]=lambda api,repo,record,phase,url="": pathlib.Path(os.environ["RESULT"]).write_text(json.dumps({"phase":phase,"url":url}))\n'
                       f'def sdk(record,payload,root):\n assert root == pathlib.Path({str(original)!r})\n'
                       'scope["reconcile_sdk"]=sdk\n'
                       'sys.argv=[str(script),*args]; module["main"]()\n')
    execution = {'metadata': {'name': 'execution', 'creationTimestamp': '2026-11-05T00:00:00Z'},
                 'spec': {'template': {'spec': {'containers': [{'image': image}]}}},
                 'status': {'completionTime': '2026-11-05T00:01:00Z', 'conditions': [{'type': 'Completed', 'status': 'True'}], 'succeededCount': 1}}
    binary = tmp_path / 'bin/gcloud'
    binary.parent.mkdir()
    job = {'spec': {'template': {'spec': {'template': execution['spec']['template']}}}}
    binary.write_text(f'#!{sys.executable}\nimport json,sys\nprint(json.dumps({[execution]!r} if "executions" in sys.argv else {job!r}))\n')
    binary.chmod(0o755)
    step = next(s['run'] for s in recovery_steps() if s.get('id') == 'deployment-outcome')
    wrapper = 'python() { "$PYTHON_BIN" -I -S "$HARNESS" "$@"; }\n'
    environment = {**os.environ, 'PATH': str(binary.parent) + os.pathsep + os.environ['PATH'],
                   'GITHUB_WORKSPACE': str(workspace), 'RUNNER_TEMP': str(tmp_path), 'DEPLOYMENT_ID': '1',
                   'KIND': kind, 'GITHUB_REPOSITORY': 'fixture/recovery', 'RESULT': str(tmp_path / 'result'),
                   'HARNESS': str(harness), 'PYTHON_BIN': sys.executable, 'VIRTUAL_ENV': str(tmp_path / '.venv-mismatch')}
    result = subprocess.run(['bash', '-euo', 'pipefail', '-c', wrapper + step], cwd=workspace, env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    outcome = json.loads((tmp_path / 'result').read_text())
    assert outcome['phase'] == ('applied' if kind == 'terraform' else 'verified')
    assert outcome['url'] == ('https://console.cloud.google.com/run/jobs/executions/details/us-central1/execution?project=shared-datasets-1' if kind == 'ingestion' else '')


def test_schedule_runbook_uses_protected_control_for_every_registered_publisher():
    from scripts.ingestion_targets import TARGETS
    text = (ROOT / '.claude/skills/deploy-scheduled-ingestion/references/job-operations.md').read_text()
    assert 'gcloud scheduler jobs pause' not in text
    assert 'ingestion-schedule-control.yml' in text
    assert all(target in text for target in TARGETS)


def test_secret_bearing_state_is_confined_to_the_recovery_leaf():
    from test_workflow_identity_policy import allowed, claims

    # Terraform sensitivity redacts output; it does not exclude this value from state.
    assert 'auth_token = var.cron_alert_slack_auth_token' in (ROOT / 'terraform/envs/prod/monitoring.tf').read_text()
    assert allowed('recovery', claims('deployment-recovery.yml'))
    policy = json.loads((ROOT / 'terraform/envs/prod/github-workflow-identities.json').read_text())
    for filename in policy['observer']:
        assert not allowed('recovery', claims(filename))
    rows = load_terraform(ROOT / 'terraform/envs/prod/github_job_identities.tf')
    for kind, name, body in resources(rows):
        if name in {'recovery_reader', 'recovery_state_reader'}:
            assert body['member'] == '${google_service_account.github_job["recovery"].member}'
    locals_ = rows['locals'][0]
    assert 'recovery' in locals_['github_job_permissions']
    assert 'recovery' not in next(body['for_each'] for kind, name, body in resources(rows)
                                  if name == 'github_job_reader')
