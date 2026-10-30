"""Verify original PR producers before reusing exact-tree validation on main.

Main retains mutation authority. Missing eligible sources run fresh work;
malformed evidence selected as a source never falls back to a fresh pass.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import json
from pathlib import Path
import re
import subprocess

from scripts.ci_contract import CONTRACT_FILES, contract_digest, expected_tools, select_production_images
from scripts.ci_source_proof import job_name
from scripts.deployment_revision import DeploymentError, require
from scripts.sdk_release_authorization import IDENTITY, artifact_files, execution_time, gate_execution, reference_attempt
from scripts.tested_image_bundle import TARGETS, validate_manifest
from scripts.production_image_contracts import REUSABLE_IMAGE_TARGETS

REUSABLE_SUITES = frozenset({'browser', 'geospatial-integration', 'production-images'})


@dataclass(frozen=True)
class VerifiedSource:
    reference: dict
    run: dict
    artifacts: list
    plan: dict
    result: dict
    suite_artifact: str
    suite_attempt: int


class UnavailableRetention(DeploymentError):
    """Normal discovery miss; an already selected reference still fails."""


def _retained(artifacts, name, retention_floor):
    found = [artifact for artifact in artifacts if artifact.get('name') == name]
    require(len(found) <= 1, 'ambiguous reuse artifact: ' + name)
    if not found or found[0].get('expired'):
        raise UnavailableRetention('missing or expired reuse artifact: ' + name)
    if execution_time(found[0].get('expires_at')) < retention_floor:
        raise UnavailableRetention('reuse artifact retention is too short: ' + name)
    return found[0]


def _git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True).strip()


def eligible(root: Path, main_plan: dict, suite: str) -> bool:
    if suite not in REUSABLE_SUITES or suite not in main_plan['suites'] or main_plan['head'] != main_plan['tested_sha']:
        return False
    if suite == 'production-images':
        targets = set(select_production_images(main_plan['changed_paths']))
        if not targets or not targets <= REUSABLE_IMAGE_TARGETS:
            return False
    head, tree, parents = _git(root, 'show', '-s', '--format=%H%n%T%n%P', 'HEAD').splitlines()
    if not parents.split() or parents.split()[0] != main_plan['base']:
        return False
    if (head != main_plan['tested_sha'] or tree != main_plan['tree']
            or contract_digest(root) != main_plan['contract_digest']):
        return False
    if _git(root, 'diff', '--name-only', main_plan['base'], main_plan['tested_sha'], '--', *CONTRACT_FILES):
        return False
    return True


def _repository(api, repository):
    repo = api.get(f'repos/{repository}')
    workflow = api.get(f'repos/{repository}/actions/workflows/ci.yml')
    require(type(repo.get('id')) is int and repo['id'] > 0 and repo.get('full_name') == repository, 'reuse repository mismatch')
    require(type(workflow.get('id')) is int and workflow['id'] > 0 and workflow.get('path') == '.github/workflows/ci.yml', 'reuse workflow mismatch')
    return repo, workflow


def _same_repository(value, repo):
    return value.get('id') == repo['id'] and value.get('full_name') == repo['full_name']


def _pr_eligible(pr, main_plan, repo):
    return (pr.get('merged') is True and pr.get('state') == 'closed'
            and pr.get('merge_commit_sha') == main_plan['tested_sha']
            and pr.get('base', {}).get('ref') == 'main' and pr.get('base', {}).get('sha') == main_plan['base']
            and _same_repository(pr.get('base', {}).get('repo', {}), repo)
            and _same_repository(pr.get('head', {}).get('repo', {}), repo)
            and re.fullmatch(r'[0-9a-f]{40}', str(pr.get('head', {}).get('sha'))))


def _latest_run(api, repository, workflow, head):
    runs = api.pages(f'repos/{repository}/actions/workflows/{workflow["id"]}/runs?event=pull_request&head_sha={head}&per_page=100', field='workflow_runs')
    require(all(type(run.get('id')) is int and run['id'] > 0 for run in runs), 'invalid reuse run listing')
    require(len({run['id'] for run in runs}) == len(runs), 'duplicate reuse run identity')
    return max(runs, key=lambda run: run['id']) if runs else None


def _run_identity(run, run_id, attempt, pr, repo, workflow):
    require(run.get('id') == run_id and run.get('run_attempt') == attempt
            and run.get('head_sha') == pr['head']['sha'] and run.get('event') == 'pull_request'
            and run.get('workflow_id') == workflow['id'] and run.get('path') == workflow['path']
            and all(_same_repository(run.get(key, {}), repo) for key in ('repository', 'head_repository')),
            'reuse workflow or repository mismatch')


def _completed(run):
    return run.get('status') == 'completed' and run.get('conclusion') == 'success'


def discover(api, repository: str, main_plan: dict, root: Path, suite: str, *, main_source: dict) -> VerifiedSource | None:
    if not eligible(root, main_plan, suite):
        return None
    repo, workflow = _repository(api, repository)
    associated = api.pages(f'repos/{repository}/commits/{main_plan["tested_sha"]}/pulls?per_page=100')
    candidates = []
    for item in associated:
        require(type(item.get('number')) is int and item['number'] > 0, 'invalid associated PR')
        pr = api.get(f'repos/{repository}/pulls/{item["number"]}')
        if _pr_eligible(pr, main_plan, repo):
            candidates.append(pr)
    require(len(candidates) <= 1, 'ambiguous merged reuse PR')
    if not candidates:
        return None
    pr = candidates[0]
    latest = _latest_run(api, repository, workflow, pr['head']['sha'])
    if latest is None or not _completed(latest):
        return None
    reference = {'pr_number': pr['number'], 'run_id': latest['id'], 'run_attempt': latest['run_attempt']}
    try:
        return verify_source(api, repository, main_plan, root, suite, reference, main_source=main_source)
    except UnavailableRetention:
        return None


def verify_source(api, repository: str, main_plan: dict, root: Path, suite: str, reference: dict, *,
                  main_source: dict, require_latest: bool = True) -> VerifiedSource:
    require(eligible(root, main_plan, suite), 'main plan is ineligible for reuse')
    require(isinstance(reference, dict) and set(reference) == {'pr_number', 'run_id', 'run_attempt'}
            and all(type(value) is int and value > 0 for value in reference.values()), 'invalid reuse reference')
    repo, workflow = _repository(api, repository)
    require(isinstance(main_source, dict) and set(main_source) == {'run_id', 'run_attempt'}
            and re.fullmatch(r'[1-9][0-9]*', str(main_source.get('run_id')))
            and type(main_source.get('run_attempt')) is int and main_source['run_attempt'] > 0,
            'invalid main reuse source')
    require('source' not in main_plan or str(main_plan['source'].get('run_id')) == str(main_source['run_id']),
            'main reuse run differs from plan source')
    main_id, main_attempt = int(main_source['run_id']), main_source['run_attempt']
    main_prefix = f'repos/{repository}/actions/runs/{main_id}/attempts/{main_attempt}'
    main_run = api.get(main_prefix)
    require(main_run.get('id') == main_id and main_run.get('run_attempt') == main_attempt
            and main_run.get('head_sha') == main_plan['tested_sha']
            and main_run.get('event') == 'push' and main_run.get('head_branch') == 'main'
            and main_run.get('workflow_id') == workflow['id'] and main_run.get('path') == workflow['path']
            and all(_same_repository(main_run.get(key, {}), repo) for key in ('repository', 'head_repository')),
            'main reuse attempt identity mismatch')
    main_jobs = api.pages(main_prefix + '/jobs?per_page=100', field='jobs')
    main_suite = [job for job in main_jobs if job.get('name') == job_name(suite)]
    require(len(main_suite) == 1, 'missing or ambiguous main reuse suite job')
    # Job time is provider evidence for this exact producer attempt. Fifteen
    # days covers its <=60 minute execution and the wrapper's 14 day lifetime.
    retention_floor = execution_time(main_suite[0].get('started_at')) + timedelta(days=15)
    pr = api.get(f'repos/{repository}/pulls/{reference["pr_number"]}')
    require(pr.get('number') == reference['pr_number'] and _pr_eligible(pr, main_plan, repo), 'reuse PR source changed')
    run_id, attempt = reference['run_id'], reference['run_attempt']
    if require_latest:
        latest = _latest_run(api, repository, workflow, pr['head']['sha'])
        require(latest is not None and latest['id'] == run_id and latest.get('run_attempt') == attempt and _completed(latest), 'reuse source is not latest successful CI')
        current = api.get(f'repos/{repository}/actions/runs/{run_id}')
        _run_identity(latest, run_id, attempt, pr, repo, workflow)
        _run_identity(current, run_id, attempt, pr, repo, workflow)
        require(_completed(current), 'reuse source attempt changed')
    attempts, job_lists = {}, {}

    def source_attempt(number):
        if number in attempts:
            return attempts[number]
        run = api.get(f'repos/{repository}/actions/runs/{run_id}/attempts/{number}')
        _run_identity(run, run_id, number, pr, repo, workflow)
        attempts[number] = run
        return run

    run = source_attempt(attempt)
    require(_completed(run), 'reuse source attempt unsuccessful')

    def successful_job(number, name):
        source_attempt(number)
        if number not in job_lists:
            job_lists[number] = api.pages(f'repos/{repository}/actions/runs/{run_id}/attempts/{number}/jobs?per_page=100', field='jobs')
        jobs = job_lists[number]
        found = [job for job in jobs if job.get('name') == name]
        require(len(found) == 1 and _completed(found[0]), 'reuse producing job missing or unsuccessful: ' + name)
        return found[0]

    gate = successful_job(attempt, 'ci-ready')
    start, end = gate_execution(gate)
    artifacts = api.pages(f'repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100', field='artifacts')
    gates = [a for a in artifacts if not a.get('expired')
             and re.fullmatch(r'ci-ready-evidence-attempt[1-9][0-9]*', str(a.get('name')))
             and start <= execution_time(a.get('created_at')) <= end]
    require(len(gates) <= 1, 'ambiguous reuse gate evidence')
    if not gates:
        raise UnavailableRetention('missing reuse gate evidence for execution')
    gate_attempt = reference_attempt(gates[0]['name'], 'ci-ready-evidence', attempt)
    require(gate_execution(successful_job(gate_attempt, 'ci-ready')) == (start, end), 'reuse gate execution changed')

    def document(name, member):
        _retained(artifacts, name, retention_floor)
        return json.loads(artifact_files(api, repository, run_id, artifacts, name)[member])

    evidence = document(gates[0]['name'], 'evidence.json')
    require(evidence.get('source') == {'run_id': str(run_id), 'run_attempt': gate_attempt}
            and evidence.get('status') == 'success' and not evidence.get('reused_suites'), 'invalid reuse gate source')
    plan_attempt = reference_attempt(evidence.get('plan_artifact'), 'ci-validation-plan', gate_attempt)
    plan = document(evidence['plan_artifact'], 'plan.json')
    require(plan.get('source') == {'run_id': str(run_id), 'run_attempt': plan_attempt}
            and all(plan.get(key) == evidence.get(key) for key in (*IDENTITY, 'suites', 'changed_paths', 'selection_reason')), 'reuse plan differs from gate')
    require(plan.get('schema_version') == 1 and plan.get('head') == pr['head']['sha']
            and not plan.get('reused_from') and not plan.get('reused_suites')
            and all(plan.get(key) == main_plan.get(key) for key in ('base', 'tree', 'contract_digest', 'suites', 'changed_paths')), 'reuse plan differs from main tree or contract')
    commit = api.get(f'repos/{repository}/git/commits/{plan["tested_sha"]}')
    require(commit.get('sha') == plan['tested_sha'] and commit.get('tree', {}).get('sha') == main_plan['tree']
            and [p.get('sha') for p in commit.get('parents', [])] == [main_plan['base'], pr['head']['sha']], 'reuse merge tree or parents changed')
    successful_job(plan_attempt, 'geospatial-changes')
    suite_artifact = evidence.get('suite_artifacts', {}).get(suite)
    suite_attempt = reference_attempt(suite_artifact, f'ci-result-{suite}', gate_attempt)
    successful_job(suite_attempt, job_name(suite))
    _retained(artifacts, suite_artifact, retention_floor)
    files = artifact_files(api, repository, run_id, artifacts, suite_artifact)
    result = json.loads(files['result.json'])
    require(result.get('source') == {'run_id': str(run_id), 'run_attempt': suite_attempt}
            and result.get('suite') == suite and result.get('status') == 'success'
            and all(result.get(key) == plan.get(key) for key in IDENTITY)
            and not result.get('reused_from'), 'invalid original reuse suite result')
    require(result.get('tools') == expected_tools(suite) and result.get('commands')
            and all(command.get('exit_code') == 0 for command in result['commands']), 'reuse suite failed or used another toolchain')
    if suite == 'production-images':
        manifest = json.loads(files['images/manifest.json'])
        targets = set(select_production_images(plan['changed_paths']))
        require(targets == set(select_production_images(main_plan['changed_paths'])) and targets <= REUSABLE_IMAGE_TARGETS
                and result.get('production_images') == manifest
                and set(validate_manifest(manifest, plan['tested_sha'])) == targets & TARGETS, 'reuse image selection differs')
        for target in targets & TARGETS:
            retained = _retained(artifacts, f'ci-image-{target}-attempt{suite_attempt}', retention_floor)
            require(retained.get('workflow_run', {}).get('id') == run_id, 'reusable image belongs to another run')
    return VerifiedSource(reference, run, artifacts, plan, result, suite_artifact, suite_attempt)
