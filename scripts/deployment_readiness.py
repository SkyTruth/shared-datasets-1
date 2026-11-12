"""Observe protected executor readiness. This is never deployment authority."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import deployment_emission as emissions
from scripts import pr_terraform_plan as planner
from scripts.dataset_mutation_authorization import executor_acceptance
from scripts.deployment_permissions import checks, read_json, request, verify_checks
from scripts.deployment_revision import require
from scripts.ingestion_targets import DEPLOY_TARGETS
from scripts.sdk_release_authorization import GitHub, verified_plan
from scripts.terraform_plan_permissions import PROJECT, PROJECT_URL
from scripts.terraform_target_contracts import ROOT, registry

SCHEMA = 'shared-datasets-executor-readiness-v1'
LEAF = '.github/workflows/deployment-readiness.yml'
CALLER = '.github/workflows/ci.yml'
RUNTIME = f'shared-datasets-runtime@{PROJECT}.iam.gserviceaccount.com'
OBSERVER = f'shared-datasets-observer@{PROJECT}.iam.gserviceaccount.com'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode() + b'\n'


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def authorize(api, *, environment=None, root=ROOT):
    """Verify the current accepted main push and its persisted gate evidence."""
    env = os.environ if environment is None else environment
    repository, sha = env['GITHUB_REPOSITORY'], env['EXECUTOR_SHA']
    run_id, attempt = int(env['SOURCE_RUN_ID']), int(env['SOURCE_RUN_ATTEMPT'])
    require(repository == planner.REPOSITORY and env['GITHUB_EVENT_NAME'] == 'push'
            and env['GITHUB_REF'] == 'refs/heads/main'
            and env['GITHUB_WORKFLOW_REF'] == f'{repository}/{CALLER}@refs/heads/main',
            'readiness requires the repository CI main push')
    require(sha == env['GITHUB_WORKFLOW_SHA'] == env['GITHUB_SHA']
            == subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
            'readiness executor and trusted checkout differ')
    require((run_id, attempt) == (int(env['GITHUB_RUN_ID']), int(env['GITHUB_RUN_ATTEMPT'])),
            'readiness belongs to another invocation')
    run, _, evidence, plan = verified_plan(api, repository, run_id, attempt, sha, root)
    require(run['repository']['id'] == int(env['GITHUB_REPOSITORY_ID']), 'readiness repository ID differs')
    targets = plan['readiness_targets']
    require(bool(targets), 'CI has no readiness obligation')
    acceptance = executor_acceptance(api, repository, sha)
    context = {'repository': repository, 'repository_id': run['repository']['id'],
               'executor_sha': sha, 'tree': plan['tree'], 'contract_digest': plan['contract_digest'],
               'invocation': {'run_id': run_id, 'run_attempt': attempt, 'caller': CALLER, 'leaf': LEAF},
               'selector': plan['source'], 'gate': evidence['source'], 'gate_digest': digest(evidence),
               'accepted_executor': acceptance, 'targets': targets}
    selected_scopes(context, root)
    return run, context


def selected_scopes(context, root=ROOT):
    deployments = registry(root, section='deployments')
    workflows = {deployments[name]['workflow'] for name in context['targets']}
    scopes = planner.registered_scopes(workflows, root=root)
    require(bool(scopes), 'readiness has no registered scopes')
    # Every selected executor must own a scope; an empty adapter is not success.
    for name in context['targets']:
        require(bool(planner.registered_scopes({deployments[name]['workflow']}, root=root)),
                'readiness target has no registered scope')
    return scopes


def operation_permissions(targets):
    """Check each production principal's own phase, without executing it."""
    terraform, runtime, observer = [], [], []
    if 'cron_alert_policy' in targets:
        terraform += checks('artifact-registry-images')
    ingestion = [row for row in DEPLOY_TARGETS.values() if row['selection'] in targets]
    if ingestion or 'catalog_viewer' in targets:
        runtime += checks('artifact-registry-images')
    if ingestion:
        reads = ('run.jobs.get', 'run.jobs.list', 'run.executions.get', 'run.executions.list', 'run.operations.get')
        runtime.append((PROJECT_URL, (*reads, 'run.jobs.run', 'run.jobs.runWithOverrides', 'run.executions.cancel')))
        observer.append((PROJECT_URL, reads))
        if any(row['after_verify'] for row in ingestion):
            runtime.append((PROJECT_URL, ('cloudscheduler.jobs.get', 'cloudscheduler.jobs.enable')))
    if 'catalog_viewer' in targets:
        observer += checks('catalog-viewer-observe')
    if 'pmtiles_cdn' in targets:
        # The runtime account owns these permissions project-wide. Testing the
        # project avoids adding Compute collection-list authority just to probe.
        runtime.append((PROJECT_URL, ('compute.urlMaps.get', 'compute.urlMaps.invalidateCache', 'compute.globalOperations.get')))
    return {'terraform': terraform, 'runtime': runtime, 'observer': observer}


def check(context, tokens, terraform_account, *, root=ROOT):
    require(re.fullmatch(r'[a-z0-9-]+@' + PROJECT + r'\.iam\.gserviceaccount\.com', terraform_account),
            'invalid Terraform principal')
    require(set(tokens) == {'terraform', 'runtime', 'observer'} and all(tokens.values()), 'readiness credentials are incomplete')
    observed_at = datetime.now(timezone.utc).isoformat()
    scopes = selected_scopes(context, root)
    project = read_json(urllib.request.Request('https://cloudresourcemanager.googleapis.com/v1/projects/' + PROJECT,
                       headers={'Authorization': 'Bearer ' + tokens['terraform']}), timeout=30)
    require(re.fullmatch(r'[1-9][0-9]*', str(project.get('projectNumber'))), 'project number unavailable')
    def verify(required, principal='terraform'):
        verify_checks(required, lambda url, permissions: request(url, permissions, tokens[principal]))
    summaries = planner.run_scopes(scopes, verify, str(project['projectNumber']), root=root)
    require([row['scope'] for row in summaries] == [scope['name'] for scope in scopes], 'incomplete readiness scope coverage')
    operations = operation_permissions(context['targets'])
    for principal, permissions in operations.items():
        verify(permissions, principal)
    principals = {'terraform': terraform_account, 'runtime': RUNTIME, 'observer': OBSERVER}
    return {'schema': SCHEMA, 'context': context, 'status': 'ready', 'observed_at': observed_at,
            'completed_at': datetime.now(timezone.utc).isoformat(), 'scopes': summaries,
            'principals': principals, 'operation_permissions_digest': digest(operations),
            'backend_digest': hashlib.sha256((root / 'terraform/envs/prod/versions.tf').read_bytes()).hexdigest(),
            'workspace': 'default', 'terraform_version': planner.TOOLCHAIN['terraform'],
            'provider_lock_digest': hashlib.sha256((root / 'terraform/envs/prod/.terraform.lock.hcl').read_bytes()).hexdigest()}


def verify_result(value, context, run, path, *, root=ROOT):
    require(value.get('schema') == SCHEMA and value.get('status') == 'ready' and value.get('context') == context,
            'readiness result differs from the current verified gate')
    require([row['scope'] for row in value['scopes']] == [scope['name'] for scope in selected_scopes(context, root)],
            'readiness result has incomplete scope coverage')
    require(path.read_bytes() == canonical(value), 'readiness result is not canonical')
    expected = emissions.run_policy(context['repository'], context['repository_id'], run, LEAF)
    subject = {'name': path.name, 'digest': {'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}}
    command = ['gh', 'attestation', 'verify', str(path), '--repo', context['repository'],
               '--signer-workflow', context['repository'] + '/' + LEAF, '--signer-digest', context['executor_sha'],
               '--source-digest', context['executor_sha'], '--source-ref', 'refs/heads/main',
               '--deny-self-hosted-runners', '--format', 'json']
    verified = json.loads(emissions.verified_output(command, context['repository'], subject['digest']['sha256']))
    emissions.verify_subject(verified, subject, expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('authorize', 'check', 'verify'))
    parser.add_argument('--context', required=True, type=Path)
    parser.add_argument('--result', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.mode == 'check':
            context = json.loads(args.context.read_text())
            value = check(context, {name: os.environ['READINESS_TOKEN_' + name.upper()] for name in ('terraform', 'runtime', 'observer')},
                          os.environ['TERRAFORM_SERVICE_ACCOUNT'])
            args.result.write_bytes(canonical(value))
        else:
            run, context = authorize(GitHub())
            if args.mode == 'authorize':
                args.context.write_bytes(canonical(context))
                args.context.chmod(0o600)
            else:
                verify_result(json.loads(args.result.read_bytes()), context, run, args.result)
        print('Executor readiness ' + args.mode + ' passed; no deployment authority created.')
    except planner.PlanFailure as error:
        parser.exit(1, 'Executor readiness: ' + str(error) + '\n')
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        # State, provider diagnostics and permission-error bodies may be secret.
        message = 'provider and resource values withheld' if args.mode == 'check' else str(error)
        parser.exit(1, f'Executor readiness {args.mode} failed: {message}\n')


if __name__ == '__main__':
    main()
