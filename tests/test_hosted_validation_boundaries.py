"""Hosted runner boundaries that must also be reproducible on developer hosts."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

import pytest

from scripts import terraform_target_contracts as contracts, wdpa_processing_gate as wdpa
from workflow_helpers import load_workflow, workflow_triggers

ROOT = Path(__file__).resolve().parents[1]


def test_cron_failure_filter_runtime_identity_is_a_declared_prerequisite():
    graph = {'google_monitoring_alert_policy.scheduled_ingestion_cloud_run_failure': {'google_service_account.github_job'},
             'google_service_account.github_job': set()}
    # Other roots need no dependencies in this focused graph. The full offline
    # Terraform command independently verifies actual transitive closure.
    for job in contracts.callers(ROOT).values():
        if not job['with']['targets']:
            continue
        graph.update({node: graph.get(node, set()) for node in contracts.declared(job['with']['targets'])
                      if job['with']['targets'] and node not in {
                          row for rows in (row.get('retired_resources', []) for row in json.loads((ROOT / '.github/terraform-targets.json').read_text())['targets'].values())
                          for row in rows}})
    assert 'google_service_account.github_job' in contracts.check_graph(ROOT, graph)['cron-alert-policy-sync.yml/sync']['managed_prerequisites']


def test_wdpa_digest_uses_only_inputs_installed_by_its_dockerfile(tmp_path, monkeypatch):
    # Materialize only COPY inputs, so a host checkout cannot mask missing bytes.
    for line in (ROOT / 'ingestion/wdpa_monthly/Dockerfile').read_text().splitlines():
        if not line.startswith('COPY ') or '--from=' in line:
            continue
        for name in shlex.split(line)[1:-1]:
            source, destination = ROOT / name, tmp_path / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, destination, ignore=shutil.ignore_patterns('__pycache__'))
            else:
                shutil.copyfile(source, destination)
    expected = wdpa.source_digest()
    monkeypatch.setattr(wdpa, 'ROOT', tmp_path)
    assert wdpa.source_digest() == expected


def test_premerge_acceptance_is_in_gate_and_binds_real_head():
    jobs = load_workflow(ROOT / '.github/workflows/ci.yml')['jobs']
    job = jobs['ci-ready']
    step = next(s for s in job['steps'] if s.get('name') == 'Owner approval of this exact head required before merge')
    assert job['permissions']['pull-requests'] == 'read'
    assert job['timeout-minutes'] <= 10
    assert 'owner-acceptance' not in jobs
    assert workflow_triggers(load_workflow(ROOT / '.github/workflows/ci.yml'))['pull_request']['types'] == [
        'opened', 'synchronize', 'reopened', 'ready_for_review']
    assert jobs['pr-terraform-plan']['needs'] == 'geospatial-changes'
    assert 'draft' not in step['if']
    for clause in ("github.event_name == 'pull_request'", 'github.event.pull_request.head.repo.full_name == github.repository',
                   "github.event.pull_request.user.login != 'jonaraphael'"):
        assert clause in step['if']
    assert step['env']['EXECUTOR_SHA'] == '${{ github.event.pull_request.head.sha }}'
    assert step['env']['PR_NUMBER'] == '${{ github.event.pull_request.number }}'
    assert 'python -I -S scripts/deployment_revision.py accept --pr-number "$PR_NUMBER"' in step['run']


@pytest.mark.parametrize('selection,approval,expected', [([], 9, 0), (['sea_ice'], 0, 0),
    (['sea_ice'], 1, 1), (['sea_ice'], 2, 2), ('malformed', 0, None), (None, 0, 2)])
def test_premerge_acceptance_shell_rejects_missing_plan_or_approval(tmp_path, selection, approval, expected):
    step = next(s for s in load_workflow(ROOT / '.github/workflows/ci.yml')['jobs']['ci-ready']['steps']
                if s.get('name') == 'Owner approval of this exact head required before merge')
    if selection is not None:
        plan = tmp_path / 'ci-plans/ci-validation-plan-attempt1/plan.json'
        plan.parent.mkdir(parents=True)
        plan.write_text('{' if selection == 'malformed' else json.dumps({'deployments': selection}))
    binary = tmp_path / 'bin'
    binary.mkdir()
    fake = binary / 'python'
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > "$RUNNER_TEMP/approval-args"\nexit "$APPROVAL_RESULT"\n')
    fake.chmod(0o755)
    done = subprocess.run(['bash', '-euo', 'pipefail', '-c', step['run']], capture_output=True, text=True,
        env={**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'], 'RUNNER_TEMP': str(tmp_path),
             'PR_NUMBER': '387', 'APPROVAL_RESULT': str(approval)})
    assert (done.returncode != 0 if expected is None else done.returncode == expected), done.stderr
    called = tmp_path / 'approval-args'
    assert called.exists() is (isinstance(selection, list) and bool(selection))
    if called.exists():
        assert called.read_text().strip() == '-I -S scripts/deployment_revision.py accept --pr-number 387'
